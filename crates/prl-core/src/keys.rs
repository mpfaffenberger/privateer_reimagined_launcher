//! The release signing public key compiled into the launcher.
//!
//! Production key generated 2026-10-03 (#11) with `prl keygen`. The private half lives
//! only on the publishing machine (`%USERPROFILE%\.prl\signing_key.pem`, backed up
//! offline) and is never committed. Rotating it means shipping a new launcher.
//! An empty key makes the launcher refuse every release (fail safe).

pub const RELEASE_PUBLIC_KEY: &str = "15WL60OyhGgKaORuHiDyb2JX5FAhUdJuOSlRVZBk6ag=";

#[cfg(test)]
mod tests {
    use base64::{engine::general_purpose::STANDARD, Engine};

    #[test]
    fn release_key_is_a_valid_ed25519_public_key() {
        let bytes: [u8; 32] = STANDARD.decode(super::RELEASE_PUBLIC_KEY).unwrap().try_into().unwrap();
        ed25519_dalek::VerifyingKey::from_bytes(&bytes).unwrap();
    }
}
