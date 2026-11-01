#!/usr/bin/env bash
set -euo pipefail
trap 'printf "runner-bridge: error at line %s (exit %s)\n" "$LINENO" "$?" >&2' ERR

GITHUB_REF="${OC_RUNNER_GITHUB_REF:?set OC_RUNNER_GITHUB_REF}"
KEY_REF="${OC_RUNNER_KEY_REF:?set OC_RUNNER_KEY_REF}"
BRIDGE="$HOME/bin/private_runner_bridge.py"

exec omaseal run \
  -e "OC_RUNNER_GITHUB_TOKEN=$GITHUB_REF" \
  -e "OC_RUNNER_PRIVATE_KEY=$KEY_REF" \
  -- /usr/bin/python3 "$BRIDGE"
