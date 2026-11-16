#!/usr/bin/env bash
# Lesson: Cloudflare refused a deploy ("Your Worker failed validation because it exceeded startup limits") that had succeeded
# the time before, because the core did too much work when it loaded: @openrouter/sdk builds hundreds of schema objects the
# moment it is imported, and a static import ran that on every start. Startup now has a budget.
#
# This measures the cloud core's startup the way Wrangler does (`wrangler check startup`: the Worker bundle is built and its
# global scope runs once in local workerd under a CPU profiler) and fails above the budget. It reads the "Active" CPU time
# (the profile window also counts idle time). The best of three runs is used, so one noisy run does not decide it.
#
# The budget is local, so it cannot be compared with Cloudflare's own 1000 ms limit directly. The anchor is a measurement: with
# the SDK imported statically this reported about 156 ms active and Cloudflare refused some deploys; with the lazy import it
# reports about 26 ms. The budget is half of the failing figure.
#
# Needs the core dependencies installed (`pnpm install` in core/) and Docker (Wrangler builds the container image to analyse
# the Worker). Run it where the core's bundle check already runs.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-core-startup: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root/core/packages/cloud" || exit 2

budget="${OC_STARTUP_BUDGET_MS:-78}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

best=""
for i in 1 2 3; do
  if ! out=$(pnpm exec wrangler check startup -c wrangler.jsonc --outfile "$tmp/startup.cpuprofile" 2>&1); then
    echo "check-core-startup: FAILED to measure (wrangler check startup exited non-zero)"
    printf '%s\n' "$out" | tail -n 12
    exit 1
  fi
  active=$(printf '%s\n' "$out" | sed -n 's/.*Active: \([0-9.]*\) ms.*/\1/p' | head -n 1)
  if [[ -z "$active" ]]; then
    echo "check-core-startup: FAILED: could not read the Active time from Wrangler's output (did its format change?)"
    printf '%s\n' "$out" | tail -n 12
    exit 1
  fi
  if [[ -z "$best" ]] || awk -v a="$active" -v b="$best" 'BEGIN { exit !(a < b) }'; then best="$active"; fi
done

if awk -v a="$best" -v b="$budget" 'BEGIN { exit !(a > b) }'; then
  echo "check-core-startup: FAILED: the core's startup is ${best} ms active, over the budget of ${budget} ms."
  echo
  echo "Fix: do less work when the module loads. Import heavy libraries inside the handler that uses them (await import(...)),"
  echo "build large tables and schemas lazily, and never use top-level await. See where the time goes:"
  echo "  cd core/packages/cloud && pnpm exec wrangler check startup -c wrangler.jsonc --outfile /tmp/startup.cpuprofile"
  echo "and open the profile in Chrome DevTools (Performance, Load profile)."
  exit 1
fi
echo "check-core-startup: ok (${best} ms active, budget ${budget} ms)"
