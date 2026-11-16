#!/bin/sh
# Renders open-computer.svg (the brand mark on a #101316 rounded tile) to PNG at 48, 128, 256 and 512.
#   sh make-icons.sh OUT_DIR
set -eu
out="${1:?usage: make-icons.sh OUT_DIR}"
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
command -v rsvg-convert >/dev/null 2>&1 || { echo "make-icons.sh: rsvg-convert is needed (apt install librsvg2-bin)" >&2; exit 1; }
mkdir -p "$out"
for s in 48 128 256 512; do
  rsvg-convert -w "$s" -h "$s" "$here/open-computer.svg" -o "$out/open-computer-$s.png"
done
cp "$here/open-computer.svg" "$out/open-computer.svg"
