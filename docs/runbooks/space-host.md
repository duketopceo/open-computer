# SPACE host verdict (S0) — 2026-10-22 (revised: host changed)

**Verdict: `cluster-4` (100.70.75.96, tailnet) is the SPACE host. No Hetzner spend.**

| Requirement | cluster-4 | Notes |
|---|---|---|
| x86_64 | yes | — |
| `/dev/kvm` + `/dev/net/tun` | yes | Firecracker needs both |
| `kvm-ok` | "KVM acceleration can be used" | S0 gate: verified before any install step |
| Root access | `cluster-4` user, **passwordless sudo** | cluster1 failed this — passworded, no credential path |
| RAM | **30G** (~23G free) | Kind + Rust builds + microVMs fit comfortably |
| Disk | 468G, 325G free | images + templates + btrfs |
| Docker | 29.8.2 | Kind prereq |
| btrfs | module loaded + persisted via `/etc/modules-load.d/space-btrfs.conf` | SPACE filesystem requirement |

Fallback is conditional, not active: cluster1/cluster2 share the
x86_64/KVM/Docker shape (~15G RAM), but neither has a non-interactive root
path today (cluster1: passworded sudo, no credential in omaseal). They become
a fallback only once root is provisioned. cluster1 also hosts the P10 device
agent.

**aarch64 verdict (permanent):** `omarchy-max` (Asahi/arm64) cannot run the
SPACE eval path — upstream's Kind path is x86_64-only (`uname -m` check fails
fast) and Firecracker images/tooling target x86_64. CubeVM stays the aarch64
dev backend — the two-backend model is the architecture, not a workaround.

Cold-boot baseline (2026-10-24, measured through `SpaceAdapter` over the
tailnet SSH tunnel, `seed` template, Firecracker, 3 runs):

| Run | create (API → running) | first exec | create→exec |
|---|---|---|---|
| 1 | 1.97s | 0.31s | 2.28s |
| 2 | 1.63s | 0.12s | 1.75s |
| 3 | 2.46s | 0.15s | 2.61s |

That is sandbox cold boot on a warm cluster. Cluster bring-up time (`up.sh`
from nothing) has no clean number: the first run failed on the host limits
below and the second reused the cluster. Time it if the cluster is ever
recreated.

## S1 deploy (2026-10-23) — SPACE live on cluster-4

SPACE (`perplexityai/space` @ `4791af3`) deployed via `infra/space/kind/scripts/up.sh`
→ 6-node Kind cluster `space-local` + Tilt. API `http://localhost:8080` (200
on `/healthz`), data plane `http://localhost:8092`, console auth `root@local`
(default password path, POST `/auth/v1/sessions`).

Lifecycle smoke: create `seed:latest`/`fc` → running/on_node → exec printed
`Hello from SPACE` → `DELETE` (requires `Idempotency-Key` header) → destroyed.
`GET /` on the data plane 404s — expected; it's not a browsable root.

Host fixes required (now prerequisites):

| Fix | Why |
|---|---|
| `fs.inotify.max_user_instances=8192`, `max_user_watches=1048576` (persisted `/etc/sysctl.d/`) | 128-instance default exhausts → kube-proxy CrashLoop → all NodePort routing dead |
| Host data-plane port `8090`→`8092` | `kind-cluster.yaml.tmpl` hostPort + `up.sh` `SPACE_DATA_PLANE_URL`; 8090 held by an existing docker-proxy service. In-cluster svc stays :8090 |
| `modprobe btrfs` + persist | SPACE filesystem requirement |
| Restart stale `space-db-migrate` after cluster outages | first run raced a DNS/warmup outage; `tilt trigger space-db-migrate` retried it |

Bring-up notes: `SPACE_VENDOR_ROOT=$HOME/.space-devbox/space/vendor` (firecracker
bundle installed there by `install-prereqs.sh`); Tilt UI `localhost:10350`;
`up.sh` is idempotent — re-runnable, reuses the live cluster.

## Production link (S7, 2026-10-26)

The worker reaches ECW on cluster-4 through the `oc-space-tunnel` systemd
user unit (`deploy/oc-space-tunnel.service` is the repo copy):

- `127.0.0.1:18080` → `cluster4:8080` — ECW API (management)
- `127.0.0.1:18092` → `cluster4:8092` — envd data plane
- SSH via the `cluster4` config alias; `ExitOnForwardFailure` +
  `Restart=always`. Kind binds hostPorts to loopback, so the tunnel is
  the access path, not a workaround.

Backend selection is explicit: `OC_EXECPLANE_BACKEND=space` in
`~/bin/oc-worker` → `engines._plane()` → `execplane.adapter(root,
backend="space")`. Unset falls back to cube. `SPACE_API_KEY` is injected
by `omaseal run -e SPACE_API_KEY=space/ecw-cluster4` — never on disk.
`e2b==2.21.1` is an optional `space` extra in `pyproject.toml` (uv.lock
regenerated, not hand-edited).

Verified 2026-10-26 on omarchy-max: `adapter(root, backend="space")
.sandboxes_available()` → `True` through the tunnel.
`oc-deploy` restarts the tunnel and fails the health check if an enabled
link unit is down.
