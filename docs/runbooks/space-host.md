# SPACE host verdict (S0) — 2026-10-22 (revised: host changed)

**Verdict: `space-host` (<tailnet-ip>, tailnet) is the SPACE host. No Hetzner spend.**

| Requirement | the SPACE host | Notes |
|---|---|---|
| x86_64 | yes | — |
| `/dev/kvm` + `/dev/net/tun` | yes | Firecracker needs both |
| `kvm-ok` | "KVM acceleration can be used" | S0 gate: verified before any install step |
| Root access | `space-host` user, **passwordless sudo** | lab host 1 failed this — passworded, no credential path |
| RAM | **30G** (~23G free) | Kind + Rust builds + microVMs fit comfortably |
| Disk | 468G, 325G free | images + templates + btrfs |
| Docker | 29.8.2 | Kind prereq |
| btrfs | module loaded + persisted via `/etc/modules-load.d/space-btrfs.conf` | SPACE filesystem requirement |

Fallback is conditional, not active: the other lab hosts share the
x86_64/KVM/Docker shape (~15G RAM), but neither has a non-interactive root
path today (lab host 1: passworded sudo, no credential in omaseal). They become
a fallback only once root is provisioned. lab host 1 also hosts the P10 device
agent.

**aarch64 verdict (permanent):** `dev-host` (Asahi/arm64) cannot run the
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

## S1 deploy (2026-10-23) — SPACE live on the SPACE host

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

The worker reaches ECW on the SPACE host through the `oc-space-tunnel` systemd
user unit (`deploy/oc-space-tunnel.service` is the repo copy):

- `127.0.0.1:18080` → `space-host:8080` — ECW API (management)
- `127.0.0.1:18092` → `space-host:8092` — envd data plane
- SSH via the `space-host` config alias; `ExitOnForwardFailure` +
  `Restart=always`. Kind binds hostPorts to loopback, so the tunnel is
  the access path, not a workaround.

Backend selection is explicit: `OC_EXECPLANE_BACKEND=space` in
`~/bin/oc-worker` → `engines._plane()` → `execplane.adapter(root,
backend="space")`. Unset falls back to cube. `SPACE_API_KEY` is injected
by `omaseal run -e SPACE_API_KEY=space/ecw-key` — never on disk.
`e2b==2.21.1` is an optional `space` extra in `pyproject.toml` (uv.lock
regenerated, not hand-edited).

Verified 2026-10-26 on dev-host: `adapter(root, backend="space")
.sandboxes_available()` → `True` through the tunnel.
`oc-deploy` restarts the tunnel and fails the health check if an enabled
link unit is down.

## Guest egress (TPROXY datapath)

ECW sandboxes route guest traffic through a per-sandbox `space-netgtw-svc`
transparent proxy (TCP :15001, UDP :15002) inside the `sns-*` netns.
Diverted packets take `fwmark 0x1 -> table 100 -> local dev lo` to reach the
proxy's transparent socket. Two host-side details make that work:

- `net.ipv4.conf.tap0.accept_local=1` (+ `all`) — without it the FIB
  rejects the `local` route for packets arriving on tap0 and every guest
  packet is dropped silently at the route lookup (no INPUT, no forward,
  no error counter). Upstream ECW netns setup does not enable it.
- Masquerade chain: `sns-*` (`169.254.0.0/16` -> vpeer `10.12.128.x`),
  then `cni-*` pod netns (`10.12.0.0/16` -> pod IP). Worker
  `KIND-MASQ-AGENT` + host NAT cover the rest.

`oc-ecw-egress@<worker>` systemd units on the SPACE host run
`/usr/local/lib/oc-ecw-egress/watch.sh` inside each Kind worker container
every 2s to enforce all of it on new namespaces. Enabled on
space-local-worker{,2,3,4,5}. Verified 2026-10-27: `R2[space]` conformance
PASS with `SPACE_EGRESS_ROUTED=1`; deny-by-default intact.
