//! ed25519 signature check for manifests (detached base64 `.sig`, see publisher sign.py).

use base64::{engine::general_purpose::STANDARD as B64, Engine};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};

use crate::{Error, Result};

pub fn verify_manifest(public_key_b64: &str, manifest: &[u8], sig_file: &[u8]) -> Result<()> {
    if public_key_b64.trim().is_empty() {
        return Err(Error::Untrusted("no release key configured in this launcher build".into()));
    }
    let key_bytes: [u8; 32] = B64
        .decode(public_key_b64.trim())
        .ok()
        .and_then(|v| v.try_into().ok())
        .ok_or_else(|| Error::Untrusted("malformed release public key".into()))?;
    let key = VerifyingKey::from_bytes(&key_bytes).map_err(|_| Error::Untrusted("invalid release public key".into()))?;
    let sig_text = std::str::from_utf8(sig_file).map_err(|_| Error::Untrusted("signature is not text".into()))?;
    let sig_bytes: [u8; 64] = B64
        .decode(sig_text.trim())
        .ok()
        .and_then(|v| v.try_into().ok())
        .ok_or_else(|| Error::Untrusted("malformed signature".into()))?;
    key.verify(manifest, &Signature::from_bytes(&sig_bytes))
        .map_err(|_| Error::Untrusted("manifest signature does not match the release key".into()))
}
