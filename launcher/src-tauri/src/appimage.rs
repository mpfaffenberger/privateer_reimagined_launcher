//! The Linux AppImage's TLS trust (#171). The AppImage is built in the manylinux_2_28 container
//! and bundles AlmaLinux 8's OpenSSL, which WebKit uses (through glib-networking) for the news
//! panel. That OpenSSL looks for CA certificates where RHEL keeps them, under /etc/pki/tls. On
//! other distros, SteamOS included, it finds none, every certificate is "unacceptable" and the
//! panel never loads. So, in an AppImage, point it at this system's CA bundle.

use std::path::Path;

/// Where distros keep their CA bundle, most common first.
const CA_BUNDLES: &[&str] = &[
    "/etc/ssl/certs/ca-certificates.crt", // Debian, Ubuntu, Arch, SteamOS, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",   // Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",             // openSUSE
    "/etc/ssl/cert.pem",                  // Alpine, Void
];

/// Sets `SSL_CERT_FILE` when running as an AppImage, unless the player set it. Call it first
/// thing: WebKit's processes inherit the environment when they start.
pub fn use_system_ca_bundle() {
    let in_appimage = std::env::var_os("APPIMAGE").is_some();
    let already_set = std::env::var_os("SSL_CERT_FILE").is_some();
    if let Some(bundle) = ca_bundle(in_appimage, already_set, |p| Path::new(p).is_file()) {
        std::env::set_var("SSL_CERT_FILE", bundle);
    }
}

/// The bundle to use, if any: the first of [`CA_BUNDLES`] this system has.
fn ca_bundle(in_appimage: bool, already_set: bool, exists: impl Fn(&str) -> bool) -> Option<&'static str> {
    if !in_appimage || already_set {
        return None;
    }
    CA_BUNDLES.iter().copied().find(|path| exists(path))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn picks_the_first_bundle_the_system_has() {
        let fedora = |p: &str| p == "/etc/pki/tls/certs/ca-bundle.crt" || p == "/etc/ssl/cert.pem";
        assert_eq!(ca_bundle(true, false, fedora), Some("/etc/pki/tls/certs/ca-bundle.crt"));
        let steamos = |p: &str| p == "/etc/ssl/certs/ca-certificates.crt" || p == "/etc/ssl/cert.pem";
        assert_eq!(ca_bundle(true, false, steamos), Some("/etc/ssl/certs/ca-certificates.crt"));
    }

    #[test]
    fn leaves_the_environment_alone_outside_an_appimage_or_when_set() {
        let any = |_: &str| true;
        assert_eq!(ca_bundle(false, false, any), None);   // the .deb uses the system's own TLS
        assert_eq!(ca_bundle(true, true, any), None);     // the player's choice wins
    }

    #[test]
    fn no_bundle_found_sets_nothing() {
        assert_eq!(ca_bundle(true, false, |_| false), None);
    }
}
