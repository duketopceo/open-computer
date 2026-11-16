#!/usr/bin/env bash
# Real-binary harness checks: the actual opencode, hermes-agent and wrangler, run through Open Computer's adapter.
# No key, no spend, no account: each agent talks only to a local fake OpenAI-compatible server, and wrangler runs
# with an empty home. Nothing is installed here; the caller provides the programs.
#
#   OC_OPENCODE_BIN   path to the opencode program
#   OC_HERMES_BIN     path to the hermes program
#   OC_WRANGLER_CMD   the wrangler command, for example /abs/core/packages/cloud/node_modules/.bin/wrangler
#                     (not `pnpm exec`: the check runs wrangler with an empty home, where corepack cannot find its pnpm)
#
# Usage: desktop/scripts/real_harness_check.sh   (any of the three may be left unset; its tests are then skipped)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
pids=()
cleanup() {
  for p in "${pids[@]:-}"; do kill "$p" 2>/dev/null || true; done
  rm -rf "$work"
}
trap cleanup EXIT

start_fake() { # name [--slow]
  local name="$1"; shift
  mkdir -p "$work/$name"
  python3 "$here/fake_openai.py" "$work/$name/port" "$@" >/dev/null 2>&1 &
  pids+=("$!")
  for _ in $(seq 1 50); do [ -s "$work/$name/port" ] && break; sleep 0.1; done
  [ -s "$work/$name/port" ] || { echo "fake server $name did not start" >&2; exit 1; }
}

start_fake normal
start_fake slow --slow
nport="$(cat "$work/normal/port")"
sport="$(cat "$work/slow/port")"

# Keep every real key out of the child programs' environment.
clean='unset OPENROUTER_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID'

if [ -n "${OC_OPENCODE_BIN:-}" ]; then
  mkdir -p "$work/oc-work" "$work/oc-home"
  for v in normal:"$nport" slow:"$sport" dead:1; do
    name="${v%%:*}"; port="${v##*:}"
    cat > "$work/opencode-$name.json" <<EOF
{"\$schema":"https://opencode.ai/config.json","autoupdate":false,"model":"fake/fake-model","provider":{"fake":{"npm":"@ai-sdk/openai-compatible","name":"Fake","options":{"baseURL":"http://127.0.0.1:$port/v1","apiKey":"not-a-key"},"models":{"fake-model":{"name":"Fake"}}}}}
EOF
    cat > "$work/opencode-$name.sh" <<EOF
#!/bin/sh
$clean
H="$work/oc-home"
export HOME="\$H" XDG_CONFIG_HOME="\$H/cfg" XDG_DATA_HOME="\$H/data" XDG_CACHE_HOME="\$H/cache" XDG_STATE_HOME="\$H/state"
export OPENCODE_CONFIG="$work/opencode-$name.json"
cd "$work/oc-work"
exec "$OC_OPENCODE_BIN" "\$@"
EOF
    chmod +x "$work/opencode-$name.sh"
  done
  export OC_REAL_OPENCODE="$work/opencode-normal.sh" OC_REAL_OPENCODE_SLOW="$work/opencode-slow.sh" OC_REAL_OPENCODE_DEAD="$work/opencode-dead.sh"
fi

if [ -n "${OC_HERMES_BIN:-}" ]; then
  mkdir -p "$work/hermes-work"
  for v in normal:"$nport" slow:"$sport"; do
    name="${v%%:*}"; port="${v##*:}"
    mkdir -p "$work/hermes-home-$name"
    printf 'model:\n  provider: custom\n  base_url: http://127.0.0.1:%s/v1\n  default: fake-model\n  api_key: not-a-key\n' "$port" > "$work/hermes-home-$name/config.yaml"
    cat > "$work/hermes-$name.sh" <<EOF
#!/bin/sh
$clean
export HOME="$work/hermes-home-$name" HERMES_HOME="$work/hermes-home-$name" XDG_CONFIG_HOME="$work/hermes-home-$name/.cfg" XDG_CACHE_HOME="$work/hermes-home-$name/.cache"
cd "$work/hermes-work"
exec "$OC_HERMES_BIN" "\$@"
EOF
    chmod +x "$work/hermes-$name.sh"
  done
  export OC_REAL_HERMES="$work/hermes-normal.sh" OC_REAL_HERMES_SLOW="$work/hermes-slow.sh"
fi

if [ -n "${OC_WRANGLER_CMD:-}" ]; then
  mkdir -p "$work/wr-home"
  cat > "$work/wrangler.sh" <<EOF
#!/bin/sh
$clean
export HOME="$work/wr-home" XDG_CONFIG_HOME="$work/wr-home/.cfg" WRANGLER_SEND_METRICS=false CI=true
exec $OC_WRANGLER_CMD "\$@"
EOF
  chmod +x "$work/wrangler.sh"
  export OC_REAL_WRANGLER="$work/wrangler.sh"
fi

cd "$here/.."
cargo test -p shell-core --test real_harness -- --ignored --test-threads=1 --nocapture

# Proof the agents really used the fake server: it logged chat completions from them.
if [ -n "${OC_OPENCODE_BIN:-}${OC_HERMES_BIN:-}" ]; then
  grep -q 'POST .*/chat/completions' "$work/normal/requests.log" || { echo "no agent reached the fake server" >&2; exit 1; }
fi
echo "real-binary harness checks passed"
