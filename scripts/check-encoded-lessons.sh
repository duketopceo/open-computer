#!/usr/bin/env bash
# Orchestrator for encoded lessons (luke-agents ENCODE_LESSONS.md, AGENTS.md section 5.13).
# Runs every check, reports each result, and fails if any failed. Add new checks to CHECKS.
# .sh entries run under bash, .mjs entries under node. Each check prints "check-<name>: ok" on success.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-encoded-lessons: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

CHECKS=(
  # Generic (templates from luke-agents).
  scripts/check-merge-conflict-markers.sh
  scripts/check-client-secret-mirrors.sh
  scripts/check-workflow-pipe-masking.sh
  # Already encoded earlier in this repo, registered here rather than duplicated.
  scripts/check-open-source-hygiene.sh   # tests/unit/test_obs_open_source_hygiene.py
  scripts/check-no-purple.mjs
  scripts/check-wizard-fresh.sh          # web setup wizard page regenerated after a source change
  # Repo-specific.
  scripts/check-403-not-key-invalid.sh
  scripts/check-desktop-packaging.sh
  scripts/check-workflow-hardening.sh
)

failed=0
for c in "${CHECKS[@]}"; do
  if [[ ! -f "$c" ]]; then
    echo "check-encoded-lessons: listed check missing: $c" >&2
    failed=1
    continue
  fi
  case "$c" in
    *.mjs) node "$c" ;;
    *) bash "$c" ;;
  esac || failed=1
done

if [[ $failed -ne 0 ]]; then
  echo "check-encoded-lessons: FAILED (see above). Fix the code; do not weaken a check without owner review." >&2
  exit 1
fi
echo "check-encoded-lessons: ok (${#CHECKS[@]} checks)"
