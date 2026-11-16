#!/usr/bin/env bash
# Lesson: merge conflict markers were committed. Fails on any tracked file containing them.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-merge-conflict-markers: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

# Only the unambiguous <<<<<<< / >>>>>>> markers: a bare ======= line is also Markdown/RST heading syntax.
hits=$(git grep -nE '^(<{7}|>{7})( |$)' -- . \
  ':!*.lock' ':!package-lock.json' ':!pnpm-lock.yaml' ':!*.min.*' \
  ':!scripts/check-merge-conflict-markers.sh' ':!**/vendor/**' ':!**/dist/**' || true)

if [[ -n "$hits" ]]; then
  echo "check-merge-conflict-markers: FAILED"
  echo "$hits"
  echo
  echo "Fix: these files contain unresolved merge conflict markers. Open each file, keep the intended side"
  echo "of every <<<<<<< ... >>>>>>> block, delete the markers, and re-run the tests before committing."
  exit 1
fi
echo "check-merge-conflict-markers: ok"
