#!/usr/bin/env bash
# Lesson: HTTP 403 was reported as "key invalid" / "key was rejected". A 403 means forbidden: the key works but
# lacks permission (for example OpenRouter credits need a management key), or a policy refused the call. Only 401
# means the credential itself is bad. Fixed once for credits (see "says credits need a management key on a 403"
# in core/packages/{local,cloud}/test); the same shortcut kept coming back in the sibling routes.
#
# Ratchet: the sites below predate this check and are allowed at their current count. New sites fail.
# Shrink the list as each one is fixed; never grow it.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-403-not-key-invalid: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

declare -A ALLOWED=(
  [core/packages/local/src/runtime/routes.ts]=2
  [core/packages/cloud/src/diag.ts]=2
  [core/packages/cloud/src/handler.ts]=1
  [core/packages/agent-core/src/tools/web-search.ts]=1
  [core/packages/agent-core/src/provider/openrouter.ts]=1
)

# A line that tests for 403 and, on the same line, calls the result a bad, rejected or invalid key.
pattern='(===|==)[[:space:]]*403.*(key_invalid|rejected|invalid)'
files=$(git ls-files -- 'core/packages/*/src/*' 'desktop/src/*' 'desktop/crates/*/src/*' ':!*/test/*' ':!*.test.*')
if [[ -z "$files" ]]; then
  echo "check-403-not-key-invalid: FAILED (found no source files to scan)" >&2
  exit 2
fi

bad=""
declare -A seen=()
while IFS= read -r f; do
  [[ -f "$f" ]] || continue
  n=$(grep -cE "$pattern" "$f" || true)
  [[ "$n" -gt 0 ]] || continue
  seen[$f]=$n
  if [[ "$n" -gt "${ALLOWED[$f]:-0}" ]]; then
    bad+="$(grep -nE "$pattern" "$f" | sed "s|^|$f:|")"$'\n'
  fi
done <<<"$files"

stale=""
for f in "${!ALLOWED[@]}"; do
  if [[ "${seen[$f]:-0}" -lt "${ALLOWED[$f]}" ]]; then
    stale+="$f (allowed ${ALLOWED[$f]}, found ${seen[$f]:-0})"$'\n'
  fi
done

if [[ -n "$bad" ]]; then
  echo "check-403-not-key-invalid: FAILED"
  printf '%s' "$bad"
  echo
  echo "Fix: map only 401 to key_invalid. A 403 is 'forbidden': report that the key lacks permission for this call"
  echo "(for example needs_management_key), or that policy refused it. Do not tell the user a working key is bad."
  exit 1
fi
if [[ -n "$stale" ]]; then
  echo "check-403-not-key-invalid: FAILED (the allowlist is out of date: a site was fixed, so lower its count here)"
  printf '%s' "$stale"
  exit 1
fi
echo "check-403-not-key-invalid: ok"
