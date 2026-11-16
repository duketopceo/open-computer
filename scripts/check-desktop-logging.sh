#!/usr/bin/env bash
# Lessons: the desktop app shipped with no log of its own, so a failed connect, keyring refusal or agent error left nothing to
# read; and its child programs were started without the extended PATH, so the menu launch could not find wrangler, node,
# hermes or opencode. The launcher (check-desktop-packaging.sh) covers a launch from the menu. This guards the app's own code:
# it must start its file log, write it where the launcher does, and start children through the PATH helpers.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-desktop-logging: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

fails=""
need() { # file, fixed string, why
  if [[ ! -f "$1" ]]; then
    fails+="$1: file is missing"$'\n'
  elif ! grep -qF -- "$2" "$1"; then
    fails+="$1: must contain '$2' ($3)"$'\n'
  fi
}

core=desktop/crates/shell-core/src
need desktop/src/main.rs 'applog::init()' 'the app must start its file log before anything else runs'
need "$core/applog.rs" 'open-computer' 'the log lives under the open-computer state directory'
need "$core/applog.rs" 'desktop.log' 'the log file is the one the launcher and the docs name'
need "$core/applog.rs" 'pub fn redact' 'log text is redacted before it is written'
need "$core/harness.rs" 'child_path()' 'agent programs are started with the extended PATH'
need "$core/harness.rs" 'resolve_program' 'agent programs are looked up in ~/.local/bin and the mise shims'
need "$core/signin.rs" 'child_path()' 'Wrangler is started with the extended PATH'

# The core's HTTP failures must keep their reason: a bare "could not be reached." with the error thrown away hides the cause.
if grep -nE 'map_err\(\|_\|[^)]*could not be reached' "$core"/*.rs; then
  fails+="a map_err(|_| ...could not be reached) above throws the transport error away; use http::unreachable"$'\n'
fi

if [[ -n "$fails" ]]; then
  echo "check-desktop-logging: FAILED"
  printf '%s' "$fails"
  echo
  echo "Fix: call shell_core::applog::init() first in desktop/src/main.rs, keep the log at"
  echo "~/.local/state/open-computer/desktop.log, start child programs with signin::child_path() and harness::resolve_program,"
  echo "and report transport errors through http::unreachable so the reason reaches the log and the screen."
  exit 1
fi
echo "check-desktop-logging: ok"
