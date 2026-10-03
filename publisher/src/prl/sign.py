"""ed25519 signing for manifests. The launcher embeds the public key (base64, 32 bytes)."""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def keygen(private_key_path: Path) -> str:
    """Write a new PEM private key (refuses to overwrite). Returns the base64 public key."""
    if private_key_path.exists():
        raise FileExistsError(f"{private_key_path} already exists; refusing to overwrite a signing key")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    private_key_path.write_bytes(pem)
    return public_key_b64(key)


def load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"{path} is not an ed25519 private key")
    return key


def public_key_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def sign(key: Ed25519PrivateKey, data: bytes) -> bytes:
    """Detached signature file contents: base64 of the 64-byte signature, plus a newline."""
    return base64.b64encode(key.sign(data)) + b"\n"


def verify(public_b64: str, data: bytes, signature_file: bytes) -> bool:
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
    try:
        public.verify(base64.b64decode(signature_file.strip()), data)
        return True
    except (InvalidSignature, ValueError):
        return False
