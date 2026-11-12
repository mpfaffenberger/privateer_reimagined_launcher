#!/usr/bin/env bash
# Build the launcher's AppImage and .deb in the manylinux_2_28 container (#171), against the
# WebKit stack from build-webkit.sh, so they need glibc 2.28 at most. Run from the repo root;
# the bundles land in target/release/bundle/.
#
# usage: build-appimage.sh <webkit stage dir>
set -euo pipefail

here=$(dirname "$(realpath "$0")")
bash "$here/manylinux-deps.sh"
# Only the launcher needs these, so they stay out of the WebKit cache key: node for the tauri
# CLI, file for the AppImage bundler, librsvg2 for GTK's SVG icons, libxdo for tao's menus.
dnf -y -q module install nodejs:22/common
dnf -y -q install file librsvg2-devel libxdo-devel
cp -a "$1"/. /

curl -fsSL --retry 3 https://sh.rustup.rs | sh -s -- -y -q --profile minimal --default-toolchain stable
# shellcheck source=/dev/null
. "$HOME/.cargo/env"

cd launcher
npm ci
# APPIMAGE_EXTRACT_AND_RUN: no FUSE in the container for the AppImage tooling itself.
APPIMAGE_EXTRACT_AND_RUN=1 npx tauri build --bundles appimage,deb \
  --config '{"bundle":{"createUpdaterArtifacts":false}}'
