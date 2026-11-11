# Production deployment (server-001)

Open Computer (`oc.example.com`) runs in Docker on **server-001**, deployed from
git. It does not run on the owner's laptop and no deploy runner lives there.
Pattern: kurultai's `deploy-server-001.yml` + `deploy/server-001/redeploy.sh`.

## Flow

1. PR merges to `main`. CI runs `python`, `console-e2e`.
2. `production-approval` waits on the `production` environment (required reviewer
   `duketopceo`; deployments restricted to `main`). This is the human gate.
3. `deploy` (GitHub-hosted `ubuntu-latest`, push-to-main only, never on
   `pull_request`) SSHes to server-001, fetches the exact merged SHA, checks it
   out in `~/open-computer`, and runs `deploy/server-001/redeploy.sh <sha>`.
   If `main` has already advanced past that SHA the job skips the stale deploy.
4. `redeploy.sh`: build `oc-server-001:<sha12>`, back up volumes, `compose up -d`,
   health check (SPA shell, `/api/session`, worker and console on the new image),
   and on failure roll back to the previous image.

## Containers (`deploy/server-001/docker-compose.oc.yml`)

| Service | Role | Exposure |
|---|---|---|
| `opsview` | console + API (`services.opsview.server`) | `127.0.0.1:8791` on the host |
| `worker` | Temporal worker (goal runs, automations) | none |
| `temporal`, `postgres` | durable workflow backend | internal network only |
| `device-relay` (profile `relay`) | remote-device pull transport, port 8792 | host network, bound to `OC_RELAY_BIND` (tailnet IP) |
| `space-tunnel` (profile `space`) | SSH forwards to the SPACE ECW host | internal network only |

Named volumes: `oc-data` (mounted at `~/.local/share/oc` in the containers: passkeys,
session and CSRF keys, threads, goals, runs, approvals, accounts, obs, execplane
policy, audit ledger) and `oc-pgdata` (Temporal Postgres).

## One-time server-001 setup

Run as the deploy user (`khan` unless you set `OC_DEPLOY_USER`).

```bash
# 1. Checkout (public repo, read-only clone is enough)
git clone https://github.com/duketopceo/open-computer.git ~/open-computer

# 2. Env file (secrets stay on the host; mode 600)
mkdir -p ~/.config/oc && install -m 600 /dev/null ~/.config/oc/server-001.env
$EDITOR ~/.config/oc/server-001.env     # variable NAMES below
```

Env file variables (names only; values are the ones the laptop's
`~/.config/oc/opsview.env` and omaseal hold today):

- Required: `OPENROUTER_API_KEY`, `TEMPORAL_PG_PASSWORD` (new, any strong value).
- Console/runtime: `OC_AUDIT`, `OC_RP_PARENT`, `OC_TASK_CAP`, `OC_RUN_CAP`, `OC_DAILY_CAP`,
  `OC_DURABLE` (compose defaults it to `1`), `OC_ACCESS_IDENTITY_URL` (optional).
- Sandboxes: `CUBE_API_URL`, `CUBE_API_KEY`, `CUBE_TEMPLATE_ID` (a Cube API reachable from
  server-001; see limitations), `OC_EXECPLANE_BACKEND`, `SPACE_API_KEY`, `SPACE_API_URL`,
  `SPACE_DATA_PLANE_URL`, `SPACE_DOMAIN`, `SPACE_TEMPLATE_ID`.
- Optional integrations: `GROQ_API_KEY` (voice), `OC_SENTRY_DSN`, `OC_TALLYLINE_KEY`, `OC_TALLYLINE_URL`, `OC_TALLYLINE_WORKSPACE`,
  `OPENROUTER_API_KEY_CHAT` / `_SUBAGENTS` / `_BACKGROUND`, `OC_COMPOSIO_TOOLKITS`.
- Compose switches: `COMPOSE_PROFILES` (`relay`, `space`, comma separated), `OC_RELAY_BIND`,
  `OC_SPACE_SSH_DIR`, `OC_PORT` (default 8791).

The omaseal keyring does not exist in the container; secrets that the laptop wrappers
resolved with `omaseal get/run` must be put in this file instead.

```bash
# 3. Migrate state from the laptop (stop the laptop services first so nothing is mid-write)
#    on the laptop:
systemctl --user stop oc-opsview oc-worker oc-device-relay
tar czf - -C ~/.local/share/oc --exclude=./deploy --exclude=./run . | ssh khan@server-001 'cat > /tmp/oc-data.tgz'
docker compose -f ~/.local/share/oc/deploy/deploy/temporal.compose.yml --env-file ~/.local/share/oc/deploy/deploy/temporal.env \
  exec -T postgres pg_dumpall -U temporal | gzip | ssh khan@server-001 'cat > /tmp/temporal.sql.gz'
#    on server-001 (fill the volume before the first deploy):
docker volume create oc-data
docker run --rm -v oc-data:/v -v /tmp:/t alpine:3.22 sh -c 'tar xzf /t/oc-data.tgz -C /v && chown -R 1000:1000 /v'
```

(Adjust the laptop compose/env paths if `temporal.env` lives elsewhere.) Passkeys
(`credentials.json`) are bound to the relying-party id: keep `OC_RP_PARENT` and the
public hostname identical and existing passkeys keep working.

Temporal history is optional state. To keep in-flight workflows, restore after the
first deploy has brought up an (empty) `oc-postgres`:

```bash
C="docker compose -f ~/open-computer/deploy/server-001/docker-compose.oc.yml --env-file ~/.config/oc/server-001.env"
$C stop temporal worker opsview
gunzip -c /tmp/temporal.sql.gz | docker exec -i oc-postgres psql -U temporal -d postgres
$C up -d
```

```bash
# 4. First deploy by hand (proves the build before CI relies on it)
cd ~/open-computer && git checkout --detach origin/main && deploy/server-001/redeploy.sh
curl -fsS http://127.0.0.1:8791/api/session
```

5. **Cloudflare tunnel**: add a public hostname route `oc.example.com` (your console hostname) to
   `http://127.0.0.1:8791` in the existing server-001 tunnel (dashboard, or the tunnel's
   `config.yml` ingress then restart `cloudflared`), and move the DNS record off the
   laptop tunnel. Keep the Cloudflare Access application on `oc.example.com`.
6. **GitHub**: in `duketopceo/open-computer`, add these secrets (repo level or on the
   `production` environment). Same server and user as kurultai, so values can match
   `KURULTAI_DEPLOY_*`, though a dedicated key is better:
   - `OC_DEPLOY_HOST`: server-001 address
   - `OC_DEPLOY_SSH_KEY`: private key whose public half is in the deploy user's `authorized_keys`
   - `OC_DEPLOY_USER`: optional, defaults to `khan`
   - `OC_DEPLOY_HOST_FINGERPRINT`: optional host key fingerprint (SHA256 form from `ssh-keygen -lf`)

   Confirm the `production` environment requires review by `duketopceo` and is limited to `main`.
7. Cut the laptop over once server-001 serves:
   `systemctl --user disable --now oc-opsview oc-worker oc-device-relay oc-space-tunnel`.
   Keep `oc-device` on the laptop (see limitations).

## Rollback

- Automatic: a failed health check puts the previous image back (`redeploy.sh` logs it).
- Manual image rollback: `ssh server-001 '~/open-computer/deploy/server-001/redeploy.sh --rollback'`.
- Data: each deploy writes `~/oc-backups/<utc>-<prev>/` with `oc-data.tgz` and
  `temporal.sql.gz` (last 5 kept). Restore `oc-data` by stopping the stack and
  extracting the tarball into the volume as in step 3. Data changes are forward-only;
  an image rollback does not undo them.
- Wedged deploy: `docker compose -f deploy/server-001/docker-compose.oc.yml --env-file ~/.config/oc/server-001.env logs --tail 100`.

## Limitations on server-001

- **CubeSandbox microVMs** need KVM and the Cube stack; they are not in these containers.
  Point `CUBE_API_URL` at a Cube API reachable from server-001, or use the SPACE backend.
- **`oc-device`** (the personal desktop agent, Wisp actuator, local file grants) needs the
  owner's desktop session and stays on the laptop, connecting to the relay over tailscale.
- **Agent jobs using the `ori`/`opencode` CLIs**: not installed in the image.
- The `space` profile needs server-001 to reach the SPACE host over SSH.
- The conformance "live" suite (real microVMs) remains manual.

## Retired mechanism

The earlier laptop design (`deploy/private-runner.yml`, `approved_deploy.py`,
`forced-deploy.sh`, `private_runner_bridge.py`, `~/bin/oc-deploy`, the ephemeral runner)
is superseded by the job above and must not be enabled.
