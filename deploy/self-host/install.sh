#!/usr/bin/env bash
# Open Computer self-hosted backend: one install script for a Linux server with systemd.
# Runs the same TypeScript core binary (`oc-core --daemon`) as a service on loopback, then exposes it through a
# Cloudflare Tunnel. Put Cloudflare Access in front of the tunnel hostname; the access token stays as a second factor.
#
#   sudo ./install.sh --bin ./oc-core [--port 8787] [--sandbox bubblewrap|space|cube|none] [--hostname core.example.com] [--dry-run]
#
# The daemon is the full agent runtime (loop, approvals, memory, audit, spend). Its sandbox is chosen with --sandbox:
# bubblewrap (default, needs the bwrap package), space or cube (an E2B-compatible API; put SPACE_API_URL,
# SPACE_API_KEY and SPACE_TEMPLATE_ID, or the CUBE_ equivalents, in /etc/open-computer/sandbox.env), or none.
# --dry-run prints every action and writes nothing. Re-running is safe: an existing token is kept.
set -euo pipefail

BIN=""
PORT="8787"
HOSTNAME_ARG=""
SANDBOX="bubblewrap"
DRY=0
PREFIX="${OC_PREFIX:-}"   # test hook: install under a scratch root instead of /

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --bin) BIN="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --hostname) HOSTNAME_ARG="${2:-}"; shift 2 ;;
    --sandbox) SANDBOX="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$PORT" in ''|*[!0-9]*) echo "--port must be a number" >&2; exit 2 ;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || { echo "--port must be 1-65535" >&2; exit 2; }
case "$SANDBOX" in bubblewrap|space|cube|none) ;; *) echo "--sandbox must be bubblewrap, space, cube or none" >&2; exit 2 ;; esac
[ -n "$BIN" ] || { echo "--bin is required (the compiled oc-core binary; build it with: pnpm --filter @open-computer/local build)" >&2; exit 2; }

run() { if [ "$DRY" = 1 ]; then echo "would run: $*"; else "$@"; fi; }
say() { echo "==> $*"; }

[ "$DRY" = 1 ] || [ -f "$BIN" ] || { echo "binary not found: $BIN" >&2; exit 1; }
[ "$DRY" = 1 ] || [ -n "$PREFIX" ] || [ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
[ "$DRY" = 1 ] || [ -n "$PREFIX" ] || command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }

BIN_DEST="$PREFIX/usr/local/bin/oc-core"
CONF_DIR="$PREFIX/etc/open-computer"
TOKEN_FILE="$CONF_DIR/token"
UNIT="$PREFIX/etc/systemd/system/open-computer-core.service"

say "service user"
if [ -z "$PREFIX" ]; then
  id oc-core >/dev/null 2>&1 || run useradd --system --no-create-home --shell /usr/sbin/nologin oc-core
else
  echo "(scratch root: user creation skipped)"
fi

say "binary -> $BIN_DEST"
run install -D -m 0755 "$BIN" "$BIN_DEST"

say "access token (kept if it already exists)"
run install -d -m 0750 "$CONF_DIR"
if [ "$DRY" = 1 ]; then
  echo "would create $TOKEN_FILE with 32 random bytes, mode 0640, group oc-core (value is never printed)"
elif [ ! -s "$TOKEN_FILE" ]; then
  umask 077
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$TOKEN_FILE"
  [ -n "$PREFIX" ] || chgrp oc-core "$TOKEN_FILE" "$CONF_DIR"
  chmod 0640 "$TOKEN_FILE"
  echo "token written to $TOKEN_FILE (read it once with: sudo cat $TOKEN_FILE)"
else
  echo "token already present; left unchanged"
fi

say "systemd unit -> $UNIT"
UNIT_TEXT="[Unit]
Description=Open Computer core (self-hosted)
After=network-online.target
Wants=network-online.target

[Service]
User=oc-core
Group=oc-core
Environment=OC_CORE_TOKEN_FILE=/etc/open-computer/token
Environment=OC_CORE_PORT=$PORT
Environment=OC_CORE_STATE_DIR=/var/lib/open-computer
Environment=OC_SANDBOX=$SANDBOX
EnvironmentFile=-/etc/open-computer/sandbox.env
StateDirectory=open-computer
StateDirectoryMode=0700
ExecStart=/usr/local/bin/oc-core --daemon
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
MemoryMax=1G

[Install]
WantedBy=multi-user.target
"
if [ "$DRY" = 1 ]; then
  echo "would write:"; printf '%s\n' "$UNIT_TEXT" | cat
else
  install -d -m 0755 "$(dirname "$UNIT")"
  printf '%s' "$UNIT_TEXT" > "$UNIT"
fi
if [ -z "$PREFIX" ]; then
  run systemctl daemon-reload
  run systemctl enable --now open-computer-core.service
fi

say "Cloudflare Tunnel"
if command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared found. Next, as the account owner (these open a browser once):"
else
  echo "cloudflared is not installed. Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/ then run:"
fi
HOST="${HOSTNAME_ARG:-core.example.com}"
cat <<EOF
    cloudflared tunnel login
    cloudflared tunnel create open-computer-core
    cloudflared tunnel route dns open-computer-core $HOST
    # ~/.cloudflared/config.yml:
    #   tunnel: open-computer-core
    #   ingress:
    #     - hostname: $HOST
    #       service: http://127.0.0.1:$PORT
    #     - service: http_status:404
    cloudflared service install
Then add a Cloudflare Access application for $HOST (Zero Trust > Access > Applications) so only you can reach it.
EOF

say "test it"
echo "    OC_CLOUD_URL=https://$HOST OC_CLOUD_TOKEN=\$(sudo cat /etc/open-computer/token) node core/packages/diagnostics/src/cli.ts doctor --backend self-hosted"
echo
echo "Give the core its model key once it is running (from your keyring, never on a command line):"
echo "    printf '{\"secrets\":{\"model_key\":\"%s\"}}' \"\$OPENROUTER_API_KEY\" | curl -sS -X POST http://127.0.0.1:$PORT/v1/secrets/load \\"
echo "      -H \"authorization: Bearer \$(sudo cat /etc/open-computer/token)\" -H 'content-type: application/json' -d @-"
echo "The key is held in memory only; load it again after a restart (or set OC_CORE_MODEL_KEY_FILE in sandbox.env to a root-only file)."
if [ "$SANDBOX" = bubblewrap ]; then echo "Sandbox: bubblewrap needs the bwrap package (apt install bubblewrap) and unprivileged user namespaces."; fi
