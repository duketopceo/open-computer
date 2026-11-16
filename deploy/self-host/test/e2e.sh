#!/usr/bin/env bash
# End to end check of install.sh in a systemd container: installs the real binary under the real hardened unit, starts the
# service, and calls the diagnostics routes (health, sandbox, memory, audit; the model routes too when OPENROUTER_API_KEY is set,
# passed on stdin only). Needs docker and a built binary: pnpm --filter @open-computer/local build.
#   deploy/self-host/test/e2e.sh [path/to/oc-core]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bin="${1:-$here/../../../core/packages/local/out/oc-core}"
[ -f "$bin" ] || { echo "binary not found: $bin" >&2; exit 1; }
name="oc-selfhost-e2e-$$"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker build -q -t oc-systemd-test "$here" >/dev/null
docker run -d --name "$name" --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /tmp oc-systemd-test >/dev/null
for _ in $(seq 30); do docker exec "$name" systemctl is-system-running >/dev/null 2>&1 && break; sleep 1; done
docker cp "$bin" "$name:/root/oc-core"
docker cp "$here/../install.sh" "$name:/root/install.sh"
docker exec "$name" bash /root/install.sh --bin /root/oc-core --port 8787 >/dev/null
for _ in $(seq 20); do [ "$(docker exec "$name" systemctl is-active open-computer-core)" = active ] && break; sleep 1; done

call() { docker exec "$name" sh -c 'T=$(cat /etc/open-computer/token); curl -sS -X "$0" "http://127.0.0.1:8787$1" -H "authorization: Bearer $T"' "$1" "$2"; }
expect() { # expect <label> <needle> <output>
  case "$3" in *"$2"*) echo "ok    $1" ;; *) echo "FAIL  $1: $3" >&2; exit 1 ;; esac
}

expect "health" '"ok":true' "$(call GET /v1/health)"
expect "rejects no token" 'unauthorized' "$(docker exec "$name" curl -sS http://127.0.0.1:8787/v1/health)"
expect "bindings report the bubblewrap sandbox" '"sandbox_kind":"bubblewrap"' "$(call GET /v1/diag/bindings)"
expect "sandbox runs, no key inside, egress closed" '"ok":true,"code":"ok"' "$(call POST /v1/diag/sandbox)"
expect "memory write, recall, delete" '"citation":"task:diag"' "$(call POST /v1/diag/memory)"
expect "audit chain verifies" '"ok":true,"code":"ok"' "$(call POST /v1/diag/audit)"
if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  printf '{"secrets":{"model_key":"%s"}}' "$OPENROUTER_API_KEY" | docker exec -i "$name" sh -c 'T=$(cat /etc/open-computer/token); curl -sS -X POST http://127.0.0.1:8787/v1/secrets/load -H "authorization: Bearer $T" -H "content-type: application/json" -d @-' >/dev/null
  expect "model key valid" '"ok":true' "$(call GET /v1/diag/openrouter/auth)"
  expect "streamed completion with a tool call" '"tool_calls":1' "$(call POST /v1/diag/openrouter/completion)"
fi
docker exec "$name" systemctl restart open-computer-core
sleep 2
expect "survives a restart" '"ok":true' "$(call GET /v1/health)"
echo "self-hosted install: all checks passed"
