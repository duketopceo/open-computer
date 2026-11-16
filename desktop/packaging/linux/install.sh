#!/bin/sh
# Installs the Open Computer desktop app for the current user. No root needed, nothing outside the prefix is touched.
#
#   sh install.sh [--prefix DIR] [--version vX.Y.Z]
#
# Installs into <prefix>/bin, <prefix>/share/applications and <prefix>/share/icons (default prefix: ~/.local).
# Run it from a folder that holds the release files. If they are not there, it downloads them from the GitHub
# release (latest, or --version) and checks them against SHA256SUMS first.
set -eu

prefix="${HOME:?HOME is not set}/.local"
version=latest
repo="${OC_RELEASE_REPO:-duketopceo/open-computer}"
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) prefix="${2:?--prefix needs a directory}"; shift 2 ;;
    --version) version="${2:?--version needs a tag}"; shift 2 ;;
    -h | --help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$(uname -m)" in
  x86_64 | amd64) arch=x86_64 ;;
  aarch64 | arm64) arch=aarch64 ;;
  *) echo "install.sh: unsupported CPU $(uname -m); releases cover x86_64 and aarch64 Linux." >&2; exit 1 ;;
esac
[ "$(uname -s)" = Linux ] || { echo "install.sh: this installer is for Linux." >&2; exit 1; }

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
bin_name="open-computer-linux-$arch"
files="$bin_name open-computer-launch open-computer.desktop open-computer.svg open-computer-48.png open-computer-128.png open-computer-256.png open-computer-512.png"

src="$here"
missing=0
for f in $files; do [ -f "$src/$f" ] || missing=1; done
if [ "$missing" = 1 ]; then
  src=$(mktemp -d)
  trap 'rm -rf "$src"' EXIT
  if [ "$version" = latest ]; then base="https://github.com/$repo/releases/latest/download"; else base="https://github.com/$repo/releases/download/$version"; fi
  command -v curl >/dev/null 2>&1 || { echo "install.sh: curl is needed to download the release." >&2; exit 1; }
  for f in $files SHA256SUMS; do
    curl -fsSL "$base/$f" -o "$src/$f" || { echo "install.sh: could not download $base/$f" >&2; exit 1; }
  done
  (cd "$src" && grep -E " ($(echo "$files" | tr ' ' '|'))\$" SHA256SUMS | sha256sum -c --quiet -) \
    || { echo "install.sh: checksum mismatch; nothing was installed." >&2; exit 1; }
fi

install -d "$prefix/bin" "$prefix/share/applications"
install -m 0755 "$src/$bin_name" "$prefix/bin/open-computer"
install -m 0755 "$src/open-computer-launch" "$prefix/bin/open-computer-launch"

# A menu starts apps with a minimal PATH that may not include <prefix>/bin, so the entry points at the launcher by
# absolute path. The launcher then sets the PATH the app needs and logs to ~/.local/state/open-computer/desktop.log.
sed "s|^Exec=.*|Exec=$prefix/bin/open-computer-launch|" "$src/open-computer.desktop" >"$prefix/share/applications/open-computer.desktop"
chmod 0644 "$prefix/share/applications/open-computer.desktop"

for s in 48 128 256 512; do
  install -d "$prefix/share/icons/hicolor/${s}x${s}/apps"
  install -m 0644 "$src/open-computer-$s.png" "$prefix/share/icons/hicolor/${s}x${s}/apps/open-computer.png"
done
install -d "$prefix/share/icons/hicolor/scalable/apps"
install -m 0644 "$src/open-computer.svg" "$prefix/share/icons/hicolor/scalable/apps/open-computer.svg"

# Best effort: refresh the caches so the menu shows the entry and icon at once.
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$prefix/share/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$prefix/share/icons/hicolor" 2>/dev/null || true

echo "Installed Open Computer to $prefix."
echo "Start it from the application menu, or run: $prefix/bin/open-computer-launch"
echo "Its log: ${XDG_STATE_HOME:-$HOME/.local/state}/open-computer/desktop.log"
