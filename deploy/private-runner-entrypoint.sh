#!/usr/bin/env bash
set -euo pipefail
trap 'printf "private-runner: error at line %s (exit %s)\n" "$LINENO" "$?" >&2' ERR

RUNTIME="/run/oc/runner"
SOURCE="/home/runner"
[[ "$(stat -f -c %T /run/oc)" == "tmpfs" ]] || {
  echo "private-runner: runtime must use tmpfs" >&2
  exit 1
}
[[ "$(cat /sys/fs/cgroup/memory.swap.max)" == "0" ]] || {
  echo "private-runner: container swap must be disabled" >&2
  exit 1
}

mkdir -m 700 "$RUNTIME"
cp -R "$SOURCE/." "$RUNTIME/"
export HOME="$RUNTIME"
cd "$RUNTIME"

python3 -c '
import json, os, pathlib, re, sys
data = json.load(sys.stdin)
required = ("url", "registration_token", "private_key", "public_key", "known_hosts")
if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k] for k in required):
    raise ValueError("invalid runner input")
if not re.fullmatch(r"https://github\.com/[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", data["url"]):
    raise ValueError("invalid repository URL")
for key in required:
    path = pathlib.Path(key.replace("_", "-"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(data[key])
'

eval "$(ssh-agent -a "$RUNTIME/agent.sock" -s)" >/dev/null
ssh-add "$RUNTIME/private-key" >/dev/null 2>&1
rm "$RUNTIME/private-key"

export ACTIONS_RUNNER_INPUT_TOKEN="$(cat registration-token)"
RUNNER_URL="$(cat url)"
RUNNER_NAME="oc-private-$(cat /proc/sys/kernel/random/uuid)"
./config.sh --unattended --url "$RUNNER_URL" --name "$RUNNER_NAME" \
  --ephemeral --labels oc-production --work _work
unset ACTIONS_RUNNER_INPUT_TOKEN
rm registration-token
exec ./run.sh
