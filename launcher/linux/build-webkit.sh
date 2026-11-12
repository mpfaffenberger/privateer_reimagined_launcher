#!/usr/bin/env bash
# Build WebKitGTK 4.1, and the glib, glib-networking, libsoup 3 and GTK 3.24 it and Tauri need,
# in the manylinux_2_28 container (#171), so the AppImage that bundles them needs glibc 2.28 at
# most.
#
# Tauri v2 needs webkit2gtk-4.1 (libsoup 3) and GTK 3.24. AlmaLinux 8, under manylinux_2_28,
# only packages webkit2gtk-4.0 (libsoup 2), glib 2.56 and GTK 3.22, and no distro with glibc
# <= 2.28 has 4.1. So this builds it the way RHEL 8 builds its own WebKitGTK 2.54 (clang, plus
# RHEL's patches for el8's older cairo, HarfBuzz, ICU and libxml2), with upstream's libsoup 3
# instead of RHEL's soup 2.
#
# Installs into /usr (tauri's AppImage bundler looks for WebKit's helper processes there) and
# into <stage>, which CI caches: WebKit takes hours on a hosted runner. Part of the cache key.
#
# usage (in the container, after manylinux-deps.sh): build-webkit.sh <stage dir>
set -euo pipefail

mkdir -p "$1"
stage=$(realpath "$1")
src=${SRC_DIR:-/tmp/webkit-src}
mkdir -p "$src"

# manylinux points pkg-config at its own SQLite, built for its Pythons without a SONAME, so
# libsoup and WebKit would load it by its path in this container. Use el8's.
export PKG_CONFIG_PATH=

# Python 3.12 for meson and glib's and WebKit's build scripts (el8's python3 is 3.6).
python=/opt/python/cp312-cp312/bin
export PATH=$python:$PATH
pip install -q meson==1.7.2 ninja==1.11.1.4

# RHEL 8's WebKitGTK patches, pinned to the AlmaLinux c8 commit that ships 2.54.2.
rhel=https://git.almalinux.org/rpms/webkit2gtk3/raw/commit/8a20cfc0403f4c85a906a9322aa5c72d32f5ecb0/SOURCES

fetch() {  # <url> <sha256>: download into $src once, verify, print the path
  local file
  file=$src/$(basename "$1")
  [[ -f $file ]] || curl -fsSL --retry 3 -o "$file" "$1"
  echo "$2  $file" | sha256sum -c --quiet - >&2
  echo "$file"
}

unpack() {  # <url> <sha256>: unpack a fresh tree, print its path
  local tarball dir
  tarball=$(fetch "$1" "$2")
  dir=${tarball%.tar.xz}
  rm -rf "$dir" && tar -xJf "$tarball" -C "$src"
  echo "$dir"
}

# Install into the stage, then into / so the next component builds against it.
publish() { cp -a "$stage"/. /; }

meson_build() {  # <url> <sha256> [meson options...]
  local dir
  dir=$(unpack "$1" "$2")
  shift 2
  meson setup "$dir/build" "$dir" --prefix=/usr --libdir=lib64 --buildtype=release "$@"
  meson compile -C "$dir/build"
  meson install -C "$dir/build" --destdir "$stage"
  publish
}

# el8's PCRE2 (10.32) is older than glib needs (10.34): use glib's pinned wrap, linked in.
meson_build https://download.gnome.org/sources/glib/2.84/glib-2.84.4.tar.xz \
  8a9ea10943c36fc117e253f80c91e477b673525ae45762942858aef57631bb90 \
  --force-fallback-for=pcre2 \
  -Dintrospection=disabled -Dtests=false -Dman-pages=disabled -Ddocumentation=false \
  -Dselinux=disabled -Dsysprof=disabled

meson_build https://download.gnome.org/sources/glib-networking/2.80/glib-networking-2.80.1.tar.xz \
  b80e2874157cd55071f1b6710fa0b911d5ac5de106a9ee2a4c9c7bee61782f8e \
  -Dgnutls=disabled -Dopenssl=enabled -Dlibproxy=disabled -Dgnome_proxy=disabled \
  -Denvironment_proxy=enabled
gio-querymodules /usr/lib64/gio/modules

meson_build https://download.gnome.org/sources/libsoup/3.6/libsoup-3.6.6.tar.xz \
  51ed0ae06f9d5a40f401ff459e2e5f652f9a510b7730e1359ee66d14d4872740 \
  -Dgssapi=disabled -Dntlm=disabled -Dbrotli=enabled -Dtls_check=false -Dintrospection=disabled \
  -Dvapi=disabled -Ddocs=disabled -Dtests=false -Dautobahn=disabled -Dsysprof=disabled \
  -Dpkcs11_tests=disabled

# GTK 3.24 (tao asks for it; el8 has 3.22), which needs ATK >= 2.35 (el8 has 2.28).
meson_build https://download.gnome.org/sources/atk/2.38/atk-2.38.0.tar.xz \
  ac4de2a4ef4bd5665052952fe169657e65e895c5057dffb3c2a810f6191a0c36 \
  -Dintrospection=false -Ddocs=false
meson_build https://download.gnome.org/sources/gtk/3.24/gtk-3.24.52.tar.xz \
  80931fa472a77b9a164f6740e3c0b444fac6770054632d35a7ff9d679e5e7b9f \
  -Dintrospection=false -Ddemos=false -Dexamples=false -Dtests=false -Dcolord=no \
  -Dprint_backends=file

webkit=$(unpack https://webkitgtk.org/releases/webkitgtk-2.54.2.tar.xz \
  a76345b391bef6211fb5ab6cfab2e86b285d18cda17cbb9ade595f1252b86065)
# RHEL applies cairo-1.15 after its glib-2.56.patch, which isn't needed with glib 2.84, and one
# of its hunks has that patch's line as context: hence its fuzz of 3 (patch's default is 2).
for patch in \
  "compiler-flags 75dedf71d0debbbf3bf15a535a04f41b9f1c5beff4839c0a60986a3d19fe4762 2" \
  "cairo-1.15 2f0d2380e81e7311d80ccd80a254db90d65e6c4c2b0d57811864fad21830f9dd 3" \
  "harfbuzz-1.7.5 0dbeb89462d55b87b89dadf9b9e46bbe918e09b3da18fb3f8750a52d39932df4 2" \
  "libxml2 fcf21cccb8db1cbf80b3a53b1254dff2205ea41d492091466c09b82c9f70ed92 2" \
  "icu60 22e9c194a2e68564e928954e9015a686dc7a0025808b32330aca2b419030ef44 2"; do
  read -r name sha fuzz <<<"$patch"
  patch -d "$webkit" -p1 --forward --quiet --fuzz="$fuzz" <"$(fetch "$rhel/$name.patch" "$sha")"
done

# The launcher shows its own UI and one news page: no media, WebGL, speech, spellcheck,
# introspection or docs. Fewer features, fewer bundled libraries, a shorter build. And -O2
# like the distros: CMake's Release default, -O3, only makes the build longer.
cmake -S "$webkit" -B "$webkit/build" -G Ninja -DPORT=GTK -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_FLAGS_RELEASE="-O2 -DNDEBUG" -DCMAKE_CXX_FLAGS_RELEASE="-O2 -DNDEBUG" \
  -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
  -DCMAKE_INSTALL_PREFIX=/usr -DLIB_INSTALL_DIR=/usr/lib64 -DUSE_GTK4=OFF \
  -DUSE_GSTREAMER=OFF -DENABLE_VIDEO=OFF -DENABLE_WEB_AUDIO=OFF -DENABLE_MEDIA_STREAM=OFF \
  -DENABLE_MEDIA_RECORDER=OFF -DENABLE_WEB_RTC=OFF -DENABLE_WEB_CODECS=OFF -DENABLE_WEBGL=OFF \
  -DENABLE_SPEECH_SYNTHESIS=OFF -DENABLE_SPELLCHECK=OFF -DENABLE_GAMEPAD=OFF \
  -DENABLE_DOCUMENTATION=OFF -DENABLE_INTROSPECTION=OFF -DENABLE_JOURNALD_LOG=OFF \
  -DENABLE_BUBBLEWRAP_SANDBOX=OFF -DENABLE_MINIBROWSER=OFF -DENABLE_WEBDRIVER=OFF \
  -DUSE_LIBSECRET=OFF -DUSE_LIBHYPHEN=OFF -DUSE_LIBBACKTRACE=OFF -DUSE_AVIF=OFF -DUSE_JPEGXL=OFF \
  -DUSE_SYSTEM_SYSPROF_CAPTURE=OFF
cmake --build "$webkit/build" -j "${JOBS:-$(nproc)}"
DESTDIR=$stage cmake --install "$webkit/build"
publish
