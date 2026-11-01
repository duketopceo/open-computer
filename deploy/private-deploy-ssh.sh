#!/usr/bin/env bash
set -euo pipefail
trap 'printf "private-deploy-ssh: error at line %s (exit %s)\n" "$LINENO" "$?" >&2' ERR

TARGET="${OC_DEPLOY_SSH_TARGET:?set OC_DEPLOY_SSH_TARGET}"
KNOWN_HOSTS="${OC_DEPLOY_KNOWN_HOSTS:?set OC_DEPLOY_KNOWN_HOSTS}"
PUBLIC_KEY="${OC_DEPLOY_PUBLIC_KEY:?set OC_DEPLOY_PUBLIC_KEY}"
AGENT_SOCKET="${SSH_AUTH_SOCK:?use a dedicated deployment-only SSH agent}"
# This fixed request must match EXPECTED_COMMAND in forced-deploy.sh.
REMOTE_COMMAND="/usr/bin/false"

[[ "$TARGET" =~ ^[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*$ ]] || {
  echo "private-deploy-ssh: invalid target" >&2
  exit 1
}
[[ -r "$KNOWN_HOSTS" && -r "$PUBLIC_KEY" && -S "$AGENT_SOCKET" ]] || {
  echo "private-deploy-ssh: missing host key, public key, or agent socket" >&2
  exit 1
}

exec ssh -F /dev/null \
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o GlobalKnownHostsFile=/dev/null \
  -o "UserKnownHostsFile=$KNOWN_HOSTS" -o IdentitiesOnly=yes -o "IdentityAgent=$AGENT_SOCKET" \
  -o ClearAllForwardings=yes -o ForwardAgent=no -o RequestTTY=no -o ConnectTimeout=10 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  -i "$PUBLIC_KEY" -- "$TARGET" "$REMOTE_COMMAND"
