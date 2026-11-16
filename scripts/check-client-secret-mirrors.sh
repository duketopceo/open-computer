#!/usr/bin/env bash
# Lesson: server credentials were mirrored into client-exposed env vars, which bundlers inline into
# the shipped JavaScript. AGENTS.md §5.1 (never commit or ship secrets).
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "check-client-secret-mirrors: not inside a repository; refusing to report ok" >&2; exit 2; }
cd "$root" || exit 2

prefix='(VITE|NEXT_PUBLIC|EXPO_PUBLIC|PUBLIC|REACT_APP|NUXT_PUBLIC)_'
secretish='[A-Z0-9_]*(SECRET|PRIVATE|SERVICE_ROLE|PASSWORD|PASSWD|API_TOKEN|ACCESS_TOKEN|AUTH_TOKEN|WEBHOOK)[A-Z0-9_]*'
# Names that are public by design.
allow='(PUBLISHABLE|ANON_KEY|SITE_KEY|PUBLIC_KEY|DSN)'

hits=$(git grep -nIE "\b${prefix}${secretish}\b" -- . \
  ':!scripts/check-client-secret-mirrors.sh' ':!*.lock' ':!package-lock.json' ':!pnpm-lock.yaml' \
  ':!**/dist/**' ':!**/build/**' ':!**/vendor/**' ':!*.md' \
  | grep -vE "${prefix}[A-Z0-9_]*${allow}" || true)

if [[ -n "$hits" ]]; then
  echo "check-client-secret-mirrors: FAILED"
  echo "$hits"
  echo
  echo "Fix: client-prefixed env vars (VITE_, NEXT_PUBLIC_, EXPO_PUBLIC_, ...) are inlined into the shipped"
  echo "bundle, so a secret there is public. Keep the credential server-side (API route, Worker, edge function)"
  echo "and call that from the client. If the value is public by design, rename it to say so (e.g. *_PUBLISHABLE_KEY)."
  exit 1
fi
echo "check-client-secret-mirrors: ok"
