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

cluster1 and cluster2 remain the fallback pair (same x86_64/KVM/Docker shape,
~15G RAM) — cluster1 additionally hosts the P10 device agent.

**aarch64 verdict (permanent):** `omarchy-max` (Asahi/arm64) cannot run the
SPACE eval path — upstream's Kind path is x86_64-only (`uname -m` check fails
fast) and Firecracker images/tooling target x86_64. CubeVM stays the aarch64
dev backend — the two-backend model is the architecture, not a workaround.

Cold-boot measurement: pending — SPACE bring-up completed across two runs
(first failed on host limits below); create→exec≈seconds once seed was
published. Record a clean cold-boot timing if the cluster is ever recreated.

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
