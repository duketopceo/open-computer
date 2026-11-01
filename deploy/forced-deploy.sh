#!/usr/bin/env bash
set -euo pipefail
trap 'printf "forced-deploy: error at line %s (exit %s)\n" "$LINENO" "$?" >&2' ERR

# This fixed request must match REMOTE_COMMAND in private-deploy-ssh.sh.
EXPECTED_COMMAND="/usr/bin/false"
SOURCE="$HOME/Documents/github/personal/open-computer"
PIN="$HOME/.config/oc-approved-deploy/controller.sha"
RUNTIME_DIR="/run/user/$(id -u)"

[[ "${SSH_ORIGINAL_COMMAND:-}" == "$EXPECTED_COMMAND" && -r "$PIN" ]] || {
  echo "forced-deploy: unauthorized request or missing controller pin" >&2
  exit 1
}
CONTROLLER_SHA="$(cat "$PIN")"
[[ "$CONTROLLER_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "forced-deploy: invalid controller pin" >&2
  exit 1
}

/usr/bin/env -i HOME="$HOME" PATH="/usr/bin:/bin" \
  GIT_NO_REPLACE_OBJECTS=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  /usr/bin/git -C "$SOURCE" cat-file blob "$CONTROLLER_SHA:deploy/approved_deploy.py" |
  /usr/bin/env -i HOME="$HOME" USER="$(id -un)" \
  PATH="$HOME/.local/bin:$HOME/bin:/usr/local/bin:/usr/bin:/bin" \
  XDG_RUNTIME_DIR="$RUNTIME_DIR" DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME_DIR/bus" \
  /usr/bin/python3 -I -
