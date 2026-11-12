#!/usr/bin/env bash
# AlmaLinux 8 packages for building WebKitGTK and the launcher in the manylinux_2_28 container
# (#171). Their libraries are also what the AppImage bundles, so every one of them needs glibc
# 2.28 at most. build-webkit.sh builds what el8 lacks or has too old: glib, libsoup 3, GTK 3.24
# and WebKitGTK 4.1.
#
# Part of the WebKit cache key in .github/workflows/linux.yml: changing it rebuilds WebKit.
set -euo pipefail

dnf -y -q install dnf-plugins-core epel-release
dnf config-manager --set-enabled powertools

packages=(
  # Toolchain: clang like RHEL 8's own WebKitGTK build; it uses gcc-toolset's libstdc++.
  clang libatomic ruby rubygems rubygem-json 'perl(bigint)' 'perl(English)' 'perl(FindBin)'
  'perl(JSON::PP)' gperf unifdef bison flex gettext git
  # glib, glib-networking (OpenSSL backend: el8's GnuTLS is too old) and libsoup 3
  libffi-devel pcre2-devel libmount-devel zlib-devel openssl-devel
  sqlite-devel libpsl-devel libnghttp2-devel brotli-devel
  # WebKitGTK and GTK 3.24 (gtk3-devel only for its dependencies), fonts, images, graphics
  gtk3-devel at-spi2-core-devel at-spi2-atk-devel cairo-devel harfbuzz-devel libicu-devel
  freetype-devel fontconfig-devel libjpeg-turbo-devel libpng-devel libwebp-devel woff2-devel
  libxml2-devel libxslt-devel lcms2-devel libgcrypt-devel libtasn1-devel libepoxy-devel
  mesa-libEGL-devel mesa-libgbm-devel libdrm-devel libXt-devel
  wayland-devel wayland-protocols-devel
)
dnf -y -q install "${packages[@]}"

# build-webkit.sh replaces these. Drop el8's copies, or their leftover modules get bundled
# too: glib-networking 2.56 is an older TLS backend that GIO would pick over ours (GnuTLS over
# OpenSSL), and GTK 3.22's cloudprint backend loads libsoup 2, which can't share a process
# with libsoup 3. Their dependencies stay: GTK 3.24 builds against them.
for replaced in glib-networking gtk3-devel gtk3; do
  if rpm -q --quiet "$replaced"; then rpm -e --nodeps "$replaced"; fi
done
