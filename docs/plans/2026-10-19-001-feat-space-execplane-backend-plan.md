---
title: SPACE execplane backend + credential boundary v2
type: feat
date: 2026-10-19
depends_on: docs/research/2026-10-16-pplx-computer-teardown.md, ROADMAP.md
---

# SPACE execplane backend → open-computer

## Summary

Perplexity open-sourced SPACE (Apache-2.0) — the Firecracker microVM
sandbox layer that runs Computer in production: millions of sandbox
creations/day, pause/resume/fork, deny-by-default egress, and
`network_credential_rules` + gateway header injection for secrets. The
teardown verdict (`docs/research/2026-10-16-pplx-computer-teardown.md`)
is settled: open-computer becomes a SPACE **application**, not a SPACE
alternative. This plan adds a second execplane backend — `space` beside
`cube` — and moves the credential boundary to v2 (secrets resolved at
the SPACE egress gateway, never materialized in the guest).

Unit IDs are `S0`–`S6`. They never renumber. Roadmap IDs stay `OC-*`;
this plan lands OC-009 (backend), OC-030 (lifecycle), OC-031 (host
verdict), OC-033 (credential v2), OC-034 (policy binding), and the
OC-023 cassette delta for the new backend.

## Provenance

- **source-assisted**: `docs/research/2026-10-16-pplx-computer-teardown.md`
  sections 3 (component map), 5 (roadmap deltas) — adopted as-is.
- **Settled decisions carried in** (from that teardown, user-merged #52):
  - Make open-computer a SPACE application; `execplane` stays the
    interface with two backends.
  - Adopt OC-033 v2 directly as `network_credential_rules` emission;
    the v1 placeholder scheme (`substitute()`, `_guest_env`) becomes
    unnecessary once a worker uses v2.
  - SPACE runs on x86_64 + KVM only — Hetzner host, never this M1 Max
    (aarch64, no KVM path). CubeVM stays for the aarch64 dev loop.
  - First client: ECW + the E2B Python SDK (the tested client). Native
    HTTP/gRPC API later if ECW falls short.
  - OpenShell: drop or defer — two policy engines on one boundary is
    more surface, not more safety.

## Problem Frame

The execplane has one real backend (CubeSandbox: `make_sandbox` + `exec`
only) and three deferred investments that SPACE already delivers:
pause/resume/fork lifecycle (OC-030), an egress-credential boundary that
actually resolves secrets outside the guest (OC-033), and a
production-scale substrate we no longer need to build. Continuing to
grow the Cube adapter surface toward what SPACE already is spends
effort re-deriving commodity infrastructure; the moat is the gate,
placement, cost cap, and audit above it.

## Implementation Units

### S0. Host verdict and footprint (OC-031)

- **Goal:** decide where SPACE runs before any adapter work depends on
  it, and record the architecture verdict permanently.
- **Work:**
  - Inventory the fleet for an x86_64 host with KVM/nested
    virtualization (`hcloud server list`; `kvm-ok`, `/dev/kvm`,
    `virt-type` on candidates). If none qualifies, provision the
    smallest KVM-capable Hetzner instance (CCX or dedicated AX —
    cloud vCPU nested virt is unreliable; verify, don't assume).
  - Record the verdict: SPACE = x86_64+KVM on the chosen host;
    aarch64 dev loop stays on CubeVM. Update ROADMAP OC-031.
  - Baseline: SPACE sandbox cold-boot time measured on the host —
    compare against Cube's recorded number; publish in the runbook.
- **Files:** `docs/runbooks/space-host.md` (new), `ROADMAP.md` (OC-031
  row).
- **Test scenarios:**
  - `kvm-ok` and `/dev/kvm` verified on the target host before any
    SPACE install step proceeds — fail fast otherwise.
  - Cold-boot measurement recorded with host specs; aarch64 verdict
    written down, not tribal.
- **Blocks:** S1 live verification. S2+ can proceed in parallel
  against synthetic cassettes.

### S1. SPACE deployment

- **Goal:** a reachable SPACE API on the S0 host, reproducible.
- **Work:**
  - Deploy SPACE per its upstream Kind path first (evaluation), then a
    minimal persistent control plane (PostgreSQL + S3-compatible store
    — MinIO already runs on this fleet for cube-sandbox).
  - Smoke: create sandbox → exec → destroy via the API before touching
    the adapter.
  - Write the deploy recipe down as a runbook; no hand state.
- **Files:** `deploy/space/` (new — manifests or compose as the upstream
  shape dictates), `docs/runbooks/space-deploy.md` (new).
- **Test scenarios:**
  - API health endpoint answers; create/exec/delete round-trip works
    from an allowed network path.
  - Deploy recipe re-runs cleanly from the runbook (idempotent or
    documented teardown + re-apply).
- **Verification:** live smoke recorded in the runbook.

### S2. `SpaceAdapter` — execplane second backend (OC-009 delta)

- **Goal:** `services/execplane/` exposes two interchangeable backends.
- **Work:**
  - New `services/execplane/space.py` implementing the adapter
    contract (`make_sandbox`, `exec`, `close`, `sandboxes_available`)
    via ECW + `e2b` Python SDK first.
  - Backend selection: `ExecplaneAdapter(..., backend=)` or a
    `backend_for(task)` seam placement can call — `space` default for
    cloud tasks, `cube` for the aarch64 dev loop. Explicit, not
    ambient.
  - Config env: `SPACE_API_URL`, `SPACE_API_KEY`, `SPACE_TEMPLATE_ID`
    mirroring the `CUBE_*` convention. `sandboxes_available()` reports
    truth → conformance rows PENDING, never fake-pass.
  - Failure honesty: unreachable/misconfigured SPACE raises
    `SandboxUnavailable` / yields `_FailedSandbox`, same shape as the
    Cube path.
- **Files:** `services/execplane/space.py` (new),
  `services/execplane/__init__.py` (backend export),
  `tests/unit/test_space_adapter.py` (new).
- **Test scenarios:**
  - No SDK/no reachable API → `sandboxes_available()` False, rows
    PENDING, no stub success.
  - Request shape vs synthetic cassette: create payload fields match
    exactly (strict cassette semantics — extra field fails).
  - API error paths surface as `SandboxUnavailable`, not raw SDK
    exceptions, to callers.
  - Backend selection: `backend=space` never silently falls back to
    cube; explicit only.

### S3. Lifecycle mapping (OC-030)

- **Goal:** the adapter surfaces SPACE's lifecycle instead of designing
  our own.
- **Work:**
  - Map SPACE pause/suspend/wake/fork (+ rollback per egress docs)
    onto an explicit adapter lifecycle surface: which ops callers get
    (`pause()`/`resume()`/`fork()` minimum; rollback if the API
    confirms it), exec timeout, teardown-on-close semantics.
  - Decide and document: lifecycle state is SPACE's (its PostgreSQL
    records); open-computer's durability scope stays the plan/run
    level (OC-044 re-scoped, per teardown).
- **Files:** `services/execplane/space.py`,
  `tests/unit/test_space_adapter.py` (lifecycle cases).
- **Test scenarios:**
  - pause → exec while paused fails closed → resume → exec works.
  - fork produces an independent sandbox; mutations don't cross.
  - exec timeout enforced; `close()` tears down (no leaked sandbox on
    the API side).
- **Verification:** live on S1; synthetic cassettes in CI meanwhile.

### S4. Credential boundary v2 (OC-033)

- **Goal:** secrets resolve at the SPACE egress gateway; nothing secret
  enters the guest — replacing the v1 placeholder scheme where a real
  worker consumes it.
- **Work:**
  - Emit `network_credential_rules` on sandbox creation from the
    placement/policy decision: reference-only secret ids bound to
    exact domains; the gateway injects headers; resolved values never
    appear in policy objects or guest env.
  - Delete the v1 path (`_CubeSandbox.substitute()`, `_guest_env`
    placeholder env) **only after** a real worker path uses v2 and the
    v2 conformance check is green — no gap window.
  - R3 becomes non-vacuous against the real mechanism.
- **Files:** `services/execplane/space.py`, `services/execplane/client.py`
  (v1 removal), `tests/unit/test_space_adapter.py`,
  conformance checks (see S6).
- **Test scenarios:**
  - Create payload contains no secret material in env/args — cassette-
    verified.
  - `network_credential_rules` emitted reference ids + exact domains
    only; no wildcards.
  - A worker step calling an allowed endpoint through the gateway
    receives injected credentials; a non-listed domain gets nothing
    (deny).
  - v1 removal PR has zero live callers of `substitute()` — grep-proven
    in CI or by test.
- **Verification:** v2 conformance green before v1 deletion merges.

### S5. Egress policy binding (OC-034)

- **Goal:** the approved policy binds to SPACE sandbox creation and is
  audited.
- **Work:**
  - Translate the gate/placement-approved egress decision into SPACE
    policy objects: `egress_default_action = DENY`, exact-domain allow
    list, no wildcards in profiles (teardown's parity rules).
  - Every apply audited — same durable audit spine the device plane
    uses.
  - Fail closed: policy engine unavailable → sandbox created with full
    deny or not created — decide and document which, then conform it.
- **Files:** `services/execplane/policy.py` (SPACE emission path),
  `services/execplane/space.py`, audit wiring.
- **Test scenarios:**
  - Approved policy → exact SPACE policy object; deny-by-default
    preserved.
  - Wildcard/empty-domain input rejected before emission.
  - Every apply writes an audit row; missing audit = test failure.
- **Verification:** conformance R2 against SPACE policy objects.

### S6. Conformance + cassettes for the second backend (OC-023 delta)

- **Goal:** the honesty contract extends to SPACE — same PENDING-when-
  absent discipline, strict replay, provenance labels.
- **Work:**
  - Parameterize R1–R3 (and lifecycle cases) over backend; SPACE rows
    run when reachable, PENDING otherwise.
  - `scripts/record_space.py` mirroring `record_cube.py` — synthetic
    cassettes now, live recording later, provenance marked.
  - CI: green with neither backend reachable; never lies.
- **Files:** `tests/conformance/checks/` (backend-parameterized),
  `scripts/record_space.py` (new), `tests/unit/fake_space.py` (new, if
  needed).
- **Test scenarios:**
  - Backend matrix: same conformance cases pass for both adapters or
    honestly PEND.
  - Synthetic cassettes labeled; a drifted adapter request fails the
    suite.
- **Verification:** `pytest` green on this aarch64 host with only
  Cube-or-nothing reachable.

## Sequencing

- **S0 and S2 run in parallel** — adapter dev against synthetic
  cassettes doesn't wait on the host.
- **S1 gates live verification** of S3–S5, not their development.
- **S4's v1 deletion gates on v2 green** — the only hard ordering
  constraint inside the adapter.
- **S6 lands last** within the effort but is written alongside S2.
- Suggested PR split: (1) S2 skeleton + S6 cassette plumbing;
  (2) S3 lifecycle; (3) S4 v2 + v1 removal; (4) S0/S1 deploy + runbooks
  + ROADMAP updates; (5) S5 policy binding. Independent enough to
  reorder around infra reality.

### Status note (2026-10-23, PR #61)

S2 (skeleton) shipped in #56. S3 shipped in #61 against the live
cluster-4 ECW deployment:

- create/exec/kill verified live (Firecracker microVM; `seed` template).
- pause/resume verified live — a `/tmp/marker` file survives
  suspend→restore; `resume()` rebuilds the routed data-plane client
  because restore rotates envd credentials.
- Egress policy is payload-verified only: the eval cluster is hermetic
  (sandboxes have no upstream route), so R2 packet-level rows wait for
  the net-gateway path (S4/S5). `allow_public_traffic=false` is sent
  explicitly — the SDK defaults it true and ECW 400s the true form.
- `fork()` is deferred: e2b 2.21.1 (ECW's tested baseline) exposes no
  fork call. `is_running()` is not surfaced — envd health does not
  reflect kill/pause on this deployment, so no lifecycle-truth method
  ships rather than one that lies.

## Scope Boundaries

- No SPACE on aarch64/macOS — x86_64+KVM only, per the OC-031 verdict.
  CubeVM remains the local dev backend; not deprecated.
- No device-plane sandboxing — device steps run on the user's machine
  under grants; `backend=space` applies to cloud sandbox tasks only.
- No planner/orchestrator/Temporal changes beyond the backend-
  selection seam in S2.
- OpenShell stays deferred — the SPACE gateway owns that boundary.
- No native-API client work unless ECW+E2B SDK proves insufficient —
  noted fallback, not a unit.

## Risks

- **Heavy dependency for one operator.** SPACE is K8s+PostgreSQL+S3+KVM,
  ~1M lines of Rust. Mitigation: S0/S1 verdict gates real investment;
  ECW-first keeps our surface tiny; if eval fails the verdict flips
  back to CubeVM and only S2's thin adapter is lost.
- **No KVM-capable host in the fleet.** S0 fails fast; provisioning a
  Hetzner box is a cost decision for the user, not an assumption.
- **ECW/E2B SDK undocumented.** The teardown flags the direct adapter as
  undocumented. Mitigation: the adapter contract isolates the client;
  swapping to native HTTP/gRPC is confined to `space.py`.
- **Credential regression window.** v1 must not die before v2 is green.
  Hard sequence in S4; conformance-enforced.
- **Two-backend drift.** One shared conformance suite parameterized
  over backends (S6) is the control.
- **aarch64 dev loop rots** if everything drifts SPACE-only. Mitigation:
  CubeVM stays a first-class backend; conformance covers both.

## Success Signals

- `backend=space` creates and execs in a real SPACE sandbox on the S0
  host; deny-by-default egress enforced via SPACE policy objects.
- No secret material in any guest env/args on the v2 path — proven by
  conformance, not review.
- SPACE cold boot measured on the host and recorded (OC-031 closed).
- CI green and honest: SPACE rows PENDING without a reachable API —
  no fake-pass, same contract as Cube today.

## Sources

- `docs/research/2026-10-16-pplx-computer-teardown.md` — component map,
  roadmap deltas, verdict (merged #52).
- `github.com/perplexityai/SPACE` — Apache-2.0, Rust + Firecracker,
  HTTP+SSE, gRPC protos, Kind/EKS deploy paths.
- `ROADMAP.md` — OC-009/030/031/033/034/023 row deltas this plan lands.
