---
title: Personal Computer teardown and open-computer device-plane plan
type: feat
date: 2026-10-16
depends_on: docs/research/2026-10-16-pplx-computer-teardown.md, docs/plans/2026-10-12-2318-brainstorm-apple-mobile-plan.md
---

# Personal Computer teardown → open-computer device plane

## Summary

Perplexity's Personal Computer extends its cloud Computer onto a user's
Mac: local files, native apps, a local browser, always-on hosting, remote
starts and approvals from a phone, and a privacy gate that decides what
may leave the machine. open-computer is cloud-only today: goals → route →
place → gate → sandbox → audit. This plan adds a **device plane**: a small
agent on a user-owned machine that registers capabilities, executes
scoped local steps, and runs a local privacy gate before any payload
leaves. Linux first, because that is the fleet; macOS via adapter later.

Unit IDs are `P1`–`P7`. They never renumber. Roadmap IDs stay `OC-*`.

## Provenance

- **documentation-based** only: Perplexity help center, product pages and
  blog. No client inspection. Internal implementation of Personal Computer
  is not described here and is not claimed.
  - https://www.perplexity.ai/help-center/en/articles/14659663-what-is-personal-computer
  - https://www.perplexity.ai/personal-computer-setup
  - https://www.perplexity.ai/help-center/en/articles/19800002-get-started
  - https://www.perplexity.ai/help-center/en/articles/19800008-use-the-app-on-a-mac-mini
  - https://www.perplexity.ai/help-center/en/articles/19800001-about-the-app
  - https://www.perplexity.ai/hub/blog/introducing-hybrid-compute-on-mac
  - https://www.perplexity.ai/hub/blog/personal-computer-is-available-to-all-mac-users
- **source-assisted** for the cloud half: see the SPACE teardown in
  `docs/research/2026-10-16-pplx-computer-teardown.md`.
- **third-party** press is not relied on.

## Research Findings

### A. Product surface (documentation-based, high)

- Runs inside the regular Perplexity macOS app; macOS 14 or later; a Mac
  mini needs macOS 15 for the always-on role. No separate Mac mini app.
- macOS permissions: Accessibility (click and type), Screen and System
  Audio Recording (see the screen), Full Disk Access (read files). File
  access comes from Full Disk Access, per-folder prompts, and a
  Filesystem connector. "Use a folder for related work": inside a chosen
  folder, no re-prompt per file; sensitive actions still need approval.
- App control: Mail, Finder, Slack, Messages, Notes, Calendar, and the
  Comet browser for web tools without connectors.
- Triggers: double-press both Command keys, a floating bar, voice, and
  tasks started from an iPhone. Approvals can be answered from any device.
- Device choice: the app must be installed, online, awake and signed in
  on the machine that does the work; with several machines, the user picks
  the device. Its files, apps, browser state and network decide what the
  task can use.
- Security posture: actions run in an isolated sandbox on the cloud side;
  on-device authorization for sensitive steps; 2FA for Mac mini sessions;
  actions logged, auditable and reversible; a kill switch.
- Availability: Pro and Max.

### B. Hybrid compute privacy gate (documentation-based, high)

- An on-device classifier reads each task before it leaves the Mac and
  flags names, addresses, account numbers and secrets.
- The gate can **mask** (swap for stand-ins, restore on return), **keep the
  step local**, **refuse**, or **ask for consent**.
- Division of labor: cloud does frontier reasoning, search and planning;
  the local model handles private files and on-device actions.

### C. open-computer today (in-tree, high)

| Personal Computer capability | open-computer | Gap |
|---|---|---|
| Cloud sandbox and orchestration | `services/orchestrator`, `services/execplane` (SPACE backend per teardown) | none in scope |
| Device as an execution target | none; placement is cloud-only | P1 |
| Folder-scoped file grants | none | P2 |
| Privacy gate with mask/local/refuse/consent | `services/gate/pii.py` prefilter, OC-022 corpus, OC-038 tracer, OC-036 placement | no on-device stage, no stand-in swap and restore |
| App control | none; wisp already drives Hyprland and CUA | P4 reuses wisp |
| Phone-initiated tasks and approvals | iOS brainstorm plan (approvals first) | P5 adds device-initiated runs |
| Always-on host with 2FA and kill switch | none | P6 |
| Hotkey and voice intake | none; wisp has both | P7 |

### D. Verdicts

- Personal Computer's value is the **orchestrator plus device integration**,
  not the Mac app itself (high). open-computer should add the device plane
  as a thin registered agent, not a desktop product.
- The privacy gate is the part that maps best onto what the repo already
  prioritizes: a real gate, a per-task cost cap, and placement that keeps
  PII local (high). P3 is the load-bearing unit.
- Do not build app control twice. wisp is the Linux actuator; open-computer
  calls it over a local socket (high).
- Counterargument: for one operator, Tailscale plus SSH plus the existing
  gate already give most of this. A device plane is a new attack surface
  with long-lived credentials on user machines. Mitigation: device tokens
  are scoped per folder and per capability, expire, and every call is
  audited through the same gate path as cloud steps.

## Key Technical Decisions

- **D1.** Device agent `oc-device` is a Python service reusing
  `oc/fsproto` for file transfer and a mutual-TLS or Tailscale-only
  channel to the orchestrator. It registers `{device_id, os, roots[],
  capabilities[], awake, online}` and heartbeats.
- **D2.** Placement (`services/placement/rules.py`) gains a `device`
  target. A step is placed on a device when it touches a granted root, a
  local app, or a local browser profile; everything else stays cloud.
- **D3.** Privacy gate runs **on the device** before upload:
  `services/gate/pii.py` is packaged into `oc-device`; decisions are
  mask, local, refuse, consent. Masking uses deterministic stand-ins with a
  device-held map; restoration happens on the device when results return.
- **D4.** Grants are explicit roots with modes `read`, `write`, `exec`
  and an expiry. No Full-Disk equivalent in v1.
- **D5.** App control and voice intake on Linux delegate to wisp's
  `wispd` IPC (`docs/IPC_CONTRACT.md` in wisp). macOS adapter later via
  osascript and the Accessibility API.
- **D6.** Always-on host is a systemd user service with linger; headless
  sessions require TOTP at pairing and on token refresh; a `kill` command
  cancels every run on the device and revokes its token.
- **D7.** Phone flow reuses the iOS plan's approval queue; device-initiated
  runs are ordinary goals with `origin=device`.

## Implementation Units

### P1. Device registry and agent skeleton
- **Goal:** a device can register, heartbeat, and receive a placed step.
- **Files:** `services/device/` (new: `registry.py`, `protocol.py`),
  `services/device_agent/` (new package `oc-device`: `main.py`,
  `heartbeat.py`, `exec.py`), `services/placement/rules.py` (`device`
  target), `services/orchestrator/dispatch.py`, `deploy/` unit files,
  `docs/design/device-plane.md`, `tests/unit/test_device_registry.py`.
- **Behavior:** registration is idempotent; a stale heartbeat (60 s)
  removes the device from placement; steps carry a signed envelope.
- **Test scenarios:** register, heartbeat, stale eviction; placement picks
  device only for matching steps; envelope signature rejects tampering.
- **Verification:** a step echoes `hostname` from a dev device end to end.

### P2. Folder-scoped grants
- **Goal:** the device exposes only granted roots with explicit modes.
- **Files:** `services/device_agent/grants.py`, `services/device/
  grants.py`, `services/gate/audit.py` (grant events), `oc/fsproto`
  (root-relative paths only), CLI `oc device grant add|list|revoke`,
  `tests/unit/test_grants.py`.
- **Test scenarios:** path escape (`..`, symlink out of root) refused;
  write on a read grant refused; expired grant refused; each access
  audited.
- **Verification:** conformance test added under `tests/conformance`.

### P3. On-device privacy gate
- **Goal:** mask, keep local, refuse, or ask before anything leaves the
  device. Extends OC-022 and OC-038.
- **Files:** `services/gate/pii.py` (importable without the cloud stack),
  `services/device_agent/privacy.py` (stand-in map, restore),
  `services/gate/gate.py` (decision table gains `local`), `services/
  gate/console.py` (consent prompt relay), `tests/unit/test_privacy_gate.py`,
  PII corpus additions.
- **Scanner contract:** `services/gate/pii.py` `PIIDecision` today returns only
  `flagged` and `categories`. Extend it with typed match spans (category,
  start, end) and a per-match confidence, so `privacy.py` can replace the
  exact matched text and route low-confidence matches to consent. Existing
  `scan()` callers keep working (new fields are additive).
- **Behavior:** classifier runs on the step payload; `mask` swaps spans
  for typed stand-ins and records the map locally; `local` reroutes the
  step to a local model or tool; `refuse` returns a typed error; `consent`
  parks the run in the approval queue.
- **Test scenarios:** exact-span replacement (offsets match the scanner's
  spans, adjacent and overlapping matches); consent routing for
  low-confidence matches; round-trip mask and restore; map never leaves the
  device (assert absent from upload); decision table coverage; precision
  and recall gates from OC-022 hold on the device build.
- **Verification:** R-series conformance: no raw PII span in any uploaded
  payload across the fixture corpus.

### P4. Linux app control through wisp
- **Goal:** placed steps can drive local apps without a new actuator.
- **Files:** `services/device_agent/actuators/wisp.py` (socket client),
  `services/device_agent/exec.py` (dispatch by capability),
  `docs/design/device-plane.md` (contract), `tests/unit/test_wisp_actuator.py`
  with a fake socket.
- **Test scenarios:** capability absent → step refused at placement;
  wisp confirm prompts surface in the oc approval queue; cancel propagates.
- **Verification:** "open the latest PDF in Downloads" runs on a dev box.

### P5. Phone-initiated device runs
- **Goal:** start a device task from the phone and approve from anywhere.
- **Files:** iOS plan units (intake), `services/orchestrator/runner.py`
  (`origin=device`), `services/gate/queue.py` (device consent items),
  `services/opsview` (device column).
- **Test scenarios:** run shows origin and device; consent answered on
  phone resumes the device step; device offline → run parks with reason.
- **Verification:** one run started on iPhone completes on the Linux box.

### P6. Always-on host, 2FA, kill switch
- **Goal:** a headless machine can host the device agent safely.
- **Files:** `deploy/oc-device.service`, `services/device_agent/pairing.py`
  (TOTP), `services/device/tokens.py` (expiry and revoke), CLI
  `oc device kill`, `docs/` runbook.
- **Test scenarios:** pairing without TOTP fails; token refresh requires
  TOTP after expiry; `kill` cancels active runs and revokes within 5 s.
- **Verification:** 72 h soak on the Hetzner or home box with audit review.

### P7. Hotkey and voice intake
- **Goal:** local trigger parity with double-Command and voice.
- **Files:** wisp side: a `wispd goal submit` verb that posts to
  `oc-device`; oc side: `services/device_agent/intake.py`; docs.
- **Test scenarios:** spoken goal creates a run with `origin=device`;
  offline queueing with retry.
- **Verification:** demo recording of a voice-started run.

## Scope Boundaries

- No macOS app, no Windows agent in this plan.
- No Full Disk Access equivalent; grants only.
- No local LLM hosting requirement; `local` decisions may use an existing
  local model if configured, otherwise they refuse.
- No change to the cloud sandbox backend; that is the SPACE teardown's
  roadmap.

## Risks

- Device tokens on user machines. Mitigation: short expiry, scoped, TOTP
  refresh, audited.
- Classifier false negatives leak PII. Mitigation: OC-022 corpus gates
  apply to the device build; consent default for low-confidence spans.
- Dependency on wisp for actuation couples two repos. Mitigation: the
  contract is one socket verb set, documented in both.
- Scope creep toward a desktop product. Mitigation: no UI beyond CLI and
  the existing console.

## Sources

Listed under Provenance. Cloud-side facts: `docs/research/2026-10-16-pplx-computer-teardown.md`.
