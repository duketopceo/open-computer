#!/usr/bin/env bash
# Lesson: a CI step piped a failing command into `tail`, so the step passed while the check failed.
# GitHub Actions runs `run:` with `bash -e {0}` unless `shell: bash` is set, and plain `bash -e` has no
# pipefail: only the exit code of the LAST command in a pipe counts.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-workflow-pipe-masking: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

shopt -s nullglob
files=(.github/workflows/*.yml .github/workflows/*.yaml)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "check-workflow-pipe-masking: ok (no workflows)"
  exit 0
fi

bad=""
for f in "${files[@]}"; do
  # A workflow is treated as safe if it sets pipefail itself or uses `shell: bash` (which adds -o pipefail).
  if grep -qE 'pipefail|shell:[[:space:]]*bash[[:space:]]*$' "$f"; then
    continue
  fi
  # Output filters that drop the exit code of what feeds them. Pipes inside if/elif/while conditions
  # (e.g. `if cmd | grep -q x`) are tests, not masking, and comment lines are skipped.
  h=$(grep -nE '\|[[:space:]]*(tail|head|tee)\b' "$f" \
    | grep -vE '^[0-9]+:[[:space:]]*(#|(if|elif|while|until)[[:space:]])' || true)
  if [[ -n "$h" ]]; then
    while IFS= read -r line; do bad+="$f:$line"$'\n'; done <<<"$h"
  fi
done

if [[ -n "$bad" ]]; then
  echo "check-workflow-pipe-masking: FAILED"
  printf '%s' "$bad"
  echo
  echo "Fix: these steps pipe a command into a filter without pipefail, so a failure on the left is hidden."
  echo "Add at the workflow top:  defaults: { run: { shell: bash } }  (GitHub then runs bash -eo pipefail),"
  echo "or start the step with  set -o pipefail. Never judge a check by output that went through | tail."
  exit 1
fi
echo "check-workflow-pipe-masking: ok"
