#!/usr/bin/env bash
# Renders every desktop screen headless with Rune's own snapshot mode (no display, no GPU window), dark and light.
# Usage: desktop/scripts/screens.sh OUT_DIR [path/to/open-computer]
# The chat, settings and diagnostics screens need a core: set OC_CLOUD_URL and OC_CLOUD_TOKEN (the token stays in the
# environment) or they render in their not-connected state.
set -euo pipefail
out="${1:?usage: screens.sh OUT_DIR [binary]}"
bin="${2:-$(dirname "$0")/../target/debug/open-computer}"
mkdir -p "$out"
HOME_TMP="$(mktemp -d)"
trap 'rm -rf "$HOME_TMP"' EXIT
for theme in dark light; do
  # Setup screens: a clean config directory so the app opens on onboarding.
  XDG_CONFIG_HOME="$HOME_TMP" env -u OC_CLOUD_URL -u OC_CLOUD_TOKEN "$bin" --snap "$out/onboarding-connect-$theme.png" --size 900x700 --theme "$theme" >/dev/null 2>&1
  for tab in chat settings diagnostics; do
    XDG_CONFIG_HOME="$HOME_TMP" OC_START_TAB="$tab" "$bin" --snap "$out/$tab-$theme.png" --size 1000x1300 --theme "$theme" >/dev/null 2>&1
  done
done
ls "$out"
