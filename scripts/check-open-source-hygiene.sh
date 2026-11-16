#!/usr/bin/env bash
# Lesson: this repo is public, so nothing may name one operator's machines, domains or Cloudflare ids, and a
# 32-hex test fixture looks exactly like a Cloudflare account id. The rule lives in
# tests/unit/test_obs_open_source_hygiene.py; this wrapper registers it with the other lesson guards.
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-open-source-hygiene: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

if command -v uv >/dev/null 2>&1; then
  run=(uv run --locked pytest)
else
  run=(python3 -m pytest)
fi
if "${run[@]}" tests/unit/test_obs_open_source_hygiene.py -q -p no:cacheprovider; then
  echo "check-open-source-hygiene: ok"
else
  echo "check-open-source-hygiene: FAILED"
  echo "Fix: see the hits above. Operator names become OC_* variables or placeholders (dev-host, example.com)."
  echo "A 32-hex fixture (a Cloudflare account id lookalike) under core/ or desktop/ must not be a literal: build it"
  echo "at run time, e.g. 'a'.repeat(31) + 'b' in TypeScript, or use a UUID-shaped value."
  exit 1
fi
