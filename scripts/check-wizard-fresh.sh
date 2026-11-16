#!/usr/bin/env bash
# Lesson: the web setup wizard page (generated from core/packages/diagnostics/src) was not regenerated after a
# source change, so the shipped page lagged the code. CI already ran this as one step of the core job; it is
# registered here so a local `make lint-lessons` catches it too.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-wizard-fresh: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root/core" || exit 2

if [[ ! -d packages/diagnostics/node_modules ]]; then
  echo "check-wizard-fresh: FAILED"
  echo "Fix: dependencies are not installed, so the page cannot be checked (a missing input is a failure, not a pass)."
  echo "Run: cd core && corepack enable && pnpm install --frozen-lockfile"
  exit 1
fi
if pnpm --filter @open-computer/diagnostics check:wizard; then
  echo "check-wizard-fresh: ok"
else
  echo "check-wizard-fresh: FAILED"
  echo "Fix: the committed wizard page is stale. Run: cd core && pnpm --filter @open-computer/diagnostics build:wizard"
  echo "and commit the regenerated file together with the source change."
  exit 1
fi
