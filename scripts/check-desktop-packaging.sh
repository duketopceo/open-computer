#!/usr/bin/env bash
# Lessons: (1) a desktop app started from a menu has a minimal PATH, so it could not find wrangler, node or the
# agent CLIs that live in ~/.local/bin and the mise shims; (2) the desktop app shipped with no log, so a failed
# launch left nothing to read. Both are fixed for launches from the installed entry by the launcher script, so the
# packaging must keep doing it: the menu entry runs the launcher, and the launcher sets the PATH and logs.
#
# (The same lesson inside the app's own code, "spawn tools with the extended PATH and write a log file", needs the
# desktop fixes on fix/oc-live-failures first; see the PR description. Add that check when they land.)
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-desktop-packaging: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

dir=desktop/packaging/linux
fails=""
need() { # file, fixed string, why
  if [[ ! -f "$1" ]]; then
    fails+="$1: file is missing"$'\n'
  elif ! grep -qF -- "$2" "$1"; then
    fails+="$1: must contain '$2' ($3)"$'\n'
  fi
}

need "$dir/open-computer-launch" '$HOME/.local/bin' 'menu launches have a minimal PATH'
need "$dir/open-computer-launch" '$HOME/.local/share/mise/shims' 'node and agent CLIs live in the mise shims'
need "$dir/open-computer-launch" 'desktop.log' 'the app output must be kept in a log'
need "$dir/open-computer-launch" '.local/state}/open-computer' 'the log lives under the state directory'
need "$dir/open-computer.desktop" 'Name=Open Computer' 'menu name'
need "$dir/open-computer.desktop" 'Icon=open-computer' 'icon name matches the installed icon files'
need "$dir/open-computer.desktop" 'Exec=open-computer-launch' 'the menu must start the launcher, never the bare binary'
need "$dir/open-computer.svg" '#101316' 'the rounded tile colour of the brand icon'
need "$dir/open-computer.svg" '#FFB020' 'the amber plug of the brand mark'
need "$dir/install.sh" 'Exec=$prefix/bin/open-computer-launch' 'the installed entry must point at the launcher by absolute path'
need "$dir/install.sh" 'hicolor/scalable/apps' 'SVG icon install'
for s in 48 128 256 512; do
  need "$dir/install.sh" "open-computer-$s.png" "icon size $s"
  need "$dir/make-icons.sh" "$s" "icon size $s is rendered"
done
need .github/workflows/release.yml 'desktop/packaging/linux' 'the release must ship the packaging files'
need .github/workflows/release.yml 'open-computer-launch' 'the release must ship the launcher'

# The menu entry must not run the bare binary: any Exec line other than the launcher fails.
if [[ -f "$dir/open-computer.desktop" ]] && grep -E '^Exec=' "$dir/open-computer.desktop" | grep -qvE '^Exec=open-computer-launch( |$)'; then
  fails+="$dir/open-computer.desktop: Exec must be 'open-computer-launch'"$'\n'
fi

if [[ -n "$fails" ]]; then
  echo "check-desktop-packaging: FAILED"
  printf '%s' "$fails"
  echo
  echo "Fix: keep the launcher (PATH with ~/.local/bin and ~/.local/share/mise/shims, output appended to"
  echo "~/.local/state/open-computer/desktop.log), the menu entry that runs it, the brand icon, and the release"
  echo "workflow that ships them. Do not point the menu entry at the bare open-computer binary."
  exit 1
fi
echo "check-desktop-packaging: ok"
