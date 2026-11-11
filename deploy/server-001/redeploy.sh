#!/usr/bin/env bash
# Rebuild and swap open-computer on server-001 from the CURRENT checkout.
# Pattern: kurultai's deploy/server-001/redeploy.sh, plus backup + rollback.
#
#   deploy/server-001/redeploy.sh            deploy HEAD of this checkout
#   deploy/server-001/redeploy.sh <full-sha> require HEAD == <sha> (CI passes the merged SHA)
#   deploy/server-001/redeploy.sh --rollback roll back to the previous good image
#
# The caller (the GitHub Actions deploy job over SSH) fetches and checks out the
# commit first; this script never moves the checkout. Secrets come from the env
# file on the host (OC_ENV_FILE, default ~/.config/oc/server-001.env); see
# docs/runbooks/approved-deployment.md for the variable names.
set -euo pipefail
trap 'echo "redeploy: error at line $LINENO" >&2' ERR

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE="$ROOT/deploy/server-001/docker-compose.oc.yml"
export OC_ENV_FILE="${OC_ENV_FILE:-$HOME/.config/oc/server-001.env}"
STATE="${OC_STATE_DIR:-$HOME/.local/state/oc-deploy}"
BACKUPS="${OC_BACKUP_DIR:-$HOME/oc-backups}"
PORT="${OC_PORT:-8791}"
KEEP_BACKUPS=5

log() { printf 'redeploy: %s\n' "$*" >&2; }
dc() { docker compose -f "$COMPOSE" --env-file "$OC_ENV_FILE" "$@"; }

[[ -f "$OC_ENV_FILE" ]] || { log "missing $OC_ENV_FILE"; exit 1; }
mkdir -p "$STATE" "$BACKUPS"
exec 9>"$STATE/lock"
flock -n 9 || { log "another deploy is running"; exit 1; }

healthy() {
  # Loopback request: the server mints a local session, so this exercises the SPA
  # shell and the JSON API without Cloudflare Access.
  local tag="$1" i page sess svc
  for i in $(seq 1 45); do
    sleep 2
    page="$(curl -fsS -m 3 "http://127.0.0.1:$PORT/" 2>/dev/null)" || continue
    sess="$(curl -fsS -m 3 "http://127.0.0.1:$PORT/api/session" 2>/dev/null)" || continue
    [[ $page == *'id="root"'* && $sess == *'"csrf"'* ]] || continue
    sleep 5 # let the worker / relay fail fast if they are going to
    for svc in opsview worker; do
      [[ "$(docker inspect -f '{{.State.Status}}/{{.State.Restarting}}' "oc-$svc" 2>/dev/null)" == "running/false" ]] \
        || { log "oc-$svc is not running"; return 1; }
    done
    [[ "$(docker inspect -f '{{.Config.Image}}' oc-opsview)" == "oc-server-001:$tag" ]] \
      || { log "oc-opsview is not on image $tag"; return 1; }
    return 0
  done
  return 1
}

up() { OC_IMAGE_TAG="$1" dc up -d --remove-orphans; }

PREV=""
[[ -f "$STATE/current" ]] && PREV="$(<"$STATE/current")"

if [[ "${1:-}" == "--rollback" ]]; then
  [[ -f "$STATE/previous" ]] || { log "no previous image recorded"; exit 1; }
  TARGET="$(<"$STATE/previous")"
  log "manual rollback to $TARGET"
  up "$TARGET"
  healthy "$TARGET" || { log "rollback unhealthy"; exit 1; }
  printf '%s' "$TARGET" >"$STATE/current"
  log "rolled back to $TARGET"
  exit 0
fi

SHA="$(git -C "$ROOT" rev-parse HEAD)"
if [[ -n "${1:-}" && "$1" != "$SHA" ]]; then
  log "checkout is at $SHA, expected $1"
  exit 1
fi
[[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]] || { log "checkout has local modifications"; exit 1; }
TAG="${SHA:0:12}"

log "build oc-server-001:$TAG"
docker build -f "$ROOT/deploy/server-001/Dockerfile" --build-arg "OC_COMMIT=$SHA" -t "oc-server-001:$TAG" "$ROOT"

# ---- backup (skipped on the very first deploy: nothing exists yet) ------------
if docker volume inspect oc-data >/dev/null 2>&1; then
  B="$BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)-${PREV:-none}"
  mkdir -p "$B"
  log "backup -> $B"
  docker run --rm -v oc-data:/v:ro -v "$B":/b alpine:3.22 tar czf /b/oc-data.tgz -C /v .
  # Temporal state is a real database: dump it, don't copy live files.
  if docker inspect -f '{{.State.Running}}' oc-postgres 2>/dev/null | grep -q true; then
    docker exec oc-postgres pg_dumpall -U temporal | gzip >"$B/temporal.sql.gz"
  fi
  # shellcheck disable=SC2012
  ls -1dt "$BACKUPS"/*/ 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old; do
    case "$old" in "$BACKUPS"/*) rm -r -- "$old" ;; esac
  done
fi

# ---- swap ----------------------------------------------------------------------
log "deploying $TAG (was ${PREV:-none})"
up "$TAG"
if healthy "$TAG"; then
  [[ -n "$PREV" && "$PREV" != "$TAG" ]] && printf '%s' "$PREV" >"$STATE/previous"
  printf '%s' "$TAG" >"$STATE/current"
  log "live: $TAG $(git -C "$ROOT" log -1 --format=%s)"
  exit 0
fi

log "health check FAILED for $TAG"
dc logs --tail 20 opsview worker >&2 || true
if [[ -n "$PREV" ]] && docker image inspect "oc-server-001:$PREV" >/dev/null 2>&1; then
  log "rolling back to $PREV"
  up "$PREV"
  healthy "$PREV" && log "rolled back to $PREV (volume backups in $BACKUPS)" \
    || log "rollback ALSO unhealthy — inspect: docker compose -f $COMPOSE logs"
else
  log "no previous image to roll back to"
fi
exit 1
