#!/usr/bin/env bash
# Lesson (stated in ci.yml's header and followed by every action pin): PR code must never run with secrets, and
# third-party actions are pinned to a commit so a moved tag cannot change what runs with our token.
#  1. no workflow triggers on pull_request_target;
#  2. every `uses:` of a remote action is pinned to a 40-hex commit SHA.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-workflow-hardening: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

shopt -s nullglob
files=(.github/workflows/*.yml .github/workflows/*.yaml)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "check-workflow-hardening: FAILED (no workflows found to scan)" >&2
  exit 2
fi

bad=""
for f in "${files[@]}"; do
  h=$(grep -nE 'pull_request_target' "$f" | grep -vE '^[0-9]+:[[:space:]]*#' || true)
  [[ -n "$h" ]] && bad+="$(sed "s|^|$f:|" <<<"$h")"$'\n'
  u=$(grep -nE '^[[:space:]]*-?[[:space:]]*uses:[[:space:]]' "$f" \
    | grep -vE 'uses:[[:space:]]*\./' \
    | grep -vE 'uses:[[:space:]]*[^[:space:]@]+@[0-9a-f]{40}([[:space:]]|$)' || true)
  [[ -n "$u" ]] && bad+="$(sed "s|^|$f:|" <<<"$u")"$'\n'
done

if [[ -n "$bad" ]]; then
  echo "check-workflow-hardening: FAILED"
  printf '%s' "$bad"
  echo
  echo "Fix: pin each action to its full commit SHA with the version in a trailing comment, e.g."
  echo "  uses: actions/checkout@<40-hex-sha> # v7.0.1"
  echo "and never trigger on pull_request_target (it runs PR code with the repository's secrets)."
  exit 1
fi
echo "check-workflow-hardening: ok"
