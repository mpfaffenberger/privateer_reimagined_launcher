#!/usr/bin/env bash
# Fail if any ELF in an AppImage needs a newer glibc than the oldest we support (#171), or
# needs a library by a path only the build machine has.
#
# An AppImage brings its own libraries but always uses the system's glibc, so every ELF inside
# it (the launcher binary, WebKit's helper processes and every bundled library) must load on
# the oldest glibc we ship for: 2.28, the manylinux_2_28 floor, which SteamOS clears. And none
# may need a library by its path on the build machine (a NEEDED entry with a /), which only
# that machine has.
#
# usage: check-glibc-floor.sh <file.AppImage> [newest allowed glibc, default 2.28]
# Needs objdump (binutils). Extracts with the AppImage's own runtime, so run it on the
# AppImage's arch; set APPIMAGE_EXTRACT_DIR to check an already-extracted squashfs-root.
set -euo pipefail

max=${2:-2.28}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if [[ -n "${APPIMAGE_EXTRACT_DIR:-}" ]]; then
  root=$APPIMAGE_EXTRACT_DIR
else
  appimage=$(realpath "$1")
  (cd "$work" && "$appimage" --appimage-extract >/dev/null)
  root=$work/squashfs-root
fi

# Newer than $max? (sort -V knows 2.9 < 2.28)
newer_than_max() { [[ "$1" != "$max" && $(printf '%s\n' "$max" "$1" | sort -V | tail -n1) == "$1" ]]; }

elves=0 bins=0 libs=0 bad=0 newest=0
while IFS= read -r -d '' file; do
  [[ $(head -c4 "$file") == $'\x7fELF' ]] || continue
  rel=${file#"$root"/}
  elves=$((elves + 1))
  case $rel in usr/bin/*) bins=$((bins + 1)) ;; usr/lib*) libs=$((libs + 1)) ;; esac

  # Only what the file *requires* (Version References), not what a bundled libc defines.
  # GLIBC_ABI_DT_RELR (packed relocations) has no number but arrived in glibc 2.36.
  dynamic=$(objdump -p "$file") || { echo "::error::cannot inspect ELF $rel"; exit 1; }
  needed=$(printf '%s\n' "$dynamic" | awk '/^Version References:/ { refs = 1 } refs' \
    | grep -oE 'GLIBC_([0-9]+(\.[0-9]+)+|ABI_DT_RELR)' \
    | sed -e 's/^GLIBC_ABI_DT_RELR$/2.36/' -e 's/^GLIBC_//' | sort -uV || true)
  # e.g. /opt/_internal/sqlite3/lib/libsqlite3.so, a library without a SONAME.
  for path in $(printf '%s\n' "$dynamic" | awk '$1 == "NEEDED" && $2 ~ /\// { print $2 }'); do
    bad=$((bad + 1))
    echo "::error::$rel needs $path by its path on the build machine"
  done
  for version in $needed; do
    newer_than_max "$version" || continue
    bad=$((bad + 1))
    echo "::error::$rel needs GLIBC_$version (newest allowed: $max):"
    # The symbols that need it, e.g. pidfd_spawnp (diagnostics only).
    { objdump -T "$file" | grep -F "(GLIBC_$version)" || true; } | awk '{ print "    " $NF }' | sort -u
  done
  top=$(printf '%s\n' $needed | tail -n1)
  [[ -n "$top" ]] && newest=$(printf '%s\n' "$newest" "$top" | sort -V | tail -n1)
done < <(find "$root" -type f -print0)

echo "checked $elves ELF files ($bins in usr/bin, $libs in usr/lib*); newest GLIBC required: $newest"
# An empty or half-extracted AppImage must not pass by having nothing to check.
if ((bins == 0 || libs == 0)); then
  echo "::error::expected the launcher binary and its bundled libraries; is $root an AppImage?"
  exit 1
fi
((bad == 0)) || { echo "::error::$bad problem(s): see above"; exit 1; }
