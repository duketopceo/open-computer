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

Cold-boot measurement: pending S1 completion — record against SPACE's own
bring-up timing and a create-to-exec measurement once the API is live.
