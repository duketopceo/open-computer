---
title: Device plane phase 2 — remote transport + opsview observability
type: feat
date: 2026-10-19
depends_on: docs/design/device-plane.md, docs/plans/2026-10-16-001-feat-personal-computer-device-plane-plan.md
---

# Device plane phase 2 — remote transport + observability

## Summary

P1–P7 built a complete device plane that works **only same-host**: the
orchestrator and device agent share one filesystem, so the signed-envelope
mailbox is a directory both sides can see. A device on another machine —
the actual "Personal Computer" scenario (always-on box, laptop, phone-
initiated runs) — cannot reach its mailbox. Separately, `collect_devices()`
(P5) exists but is wired to no HTTP route, so the console is blind to
devices.

Phase 2 adds **remote transport** (an authenticated pull relay over the
existing fsproto mailboxes — no protocol change, no new crypto) and the
**opsview device surface** (`/api/devices` + console panel), then dogfoods
the whole arc on a second machine.

Unit IDs continue the device plane's `P` series: **P8, P9, P10**. Roadmap
IDs stay `OC-*`.

## Problem Frame

Everything the device plane needs for remote operation already exists:
HMAC-signed envelopes with nonce replay protection (P1), the grant store
(P2), parked drain (P5), scoped device tokens + kill switch (P6). The only
missing piece is a wire between the mailbox files and a device that isn't
local — plus an operator who can't see any of it without shell access.

## Decisions

- **Pull, not push.** The device agent polls the host outbound-only — no
  listener on the device, so NAT/firewall/sleeping laptops need nothing
  inbound. The orchestrator host runs one reachable relay endpoint.
- **Relay is stateless I/O over the existing fsproto files.** Envelopes,
  secrets, nonces, parked queues, results — all unchanged. The relay
  translates HTTP↔mailbox; the durable spine stays the filesystem.
- **Auth = the device token that already exists.** P6 minted scoped
  device tokens; the relay verifies them per request. No new credential
  type.
- **Same-host path stays fsproto.** Local devices never need the relay —
  remote transport is additive, not a replacement.
- **TLS is the network, not the relay.** Relay binds a plain-HTTP socket;
  transport security comes from the layer that already exists (tailscale
  or the cloudflared tunnel). Documented in the runbook, not re-implemented.

## Implementation Units

### P9. Opsview device surface

- **Goal:** the console shows device reality — land this first since it's
  independent and small.
- **Work:**
  - `GET /api/devices` route in `services/opsview/server.py` returning
    `collect_devices(root)` verbatim (read-only; same session+CSRF
    posture as existing routes — GET needs session, no CSRF).
  - Console panel (devices section on an existing page or `/devices` —
    match the current views structure): device_id, os, capabilities,
    roots, online/awake/eligible state, last heartbeat age, active
    grants count, inbox + parked depth.
  - Fail-safe: registry unreadable → panel shows empty + "unavailable",
    not an error page.
- **Files:** `services/opsview/server.py`, `services/opsview/views.py`
  (or the page module the current console uses),
  `tests/opsview/test_devices_route.py` (new).
- **Test scenarios:**
  - Route returns collected rows for a seeded registry; 401/redirect
    without session (matches existing route posture).
  - Missing/corrupt `devices.json` → 200 with empty payload, no 500.
  - Panel renders online vs parked-depth correctly for a fixture device.

### P8. Remote device transport (relay + agent remote mode)

- **Goal:** a device on another machine executes signed steps with zero
  protocol changes.
- **Work:**
  - `services/device_relay/server.py` — small HTTPS-unaware stdlib HTTP
    service rooted at the fsproto tree. Endpoints (shipped under
    `/devices/<did>/...`):
    - `POST /devices/<did>/heartbeat` → registry heartbeat + parked drain
    - `GET /devices/<did>/inbox` → pending task ids
    - `GET /devices/<did>/tasks/<tid>` → task record body
    - `POST /devices/<did>/tasks/<tid>/claim` → lease (409/410)
    - `POST /devices/<did>/tasks/<tid>/result` → outbox write (409/410)
    - `GET /devices/<did>/grants` → grant store snapshot
    - `POST /devices/<did>/audit` → device-emittable event allowlist
    - Pairing and token refresh stay host-side operator actions; there is
      no remote pair/refresh endpoint in P8.
    - Bearer = the pairing-time device token secret, verified per request;
      wrong/absent → 401, fail-closed. That secret is dual-use: it is also
      the envelope HMAC key, provisioned to the device at pairing time
      (0600 file, operator-managed channel). Envelopes stay sealed
      host-side; the device opens them with its own copy. Results are
      opaque agent JSON — server-side result-envelope verification is
      deferred (see risks).
  - `services/device_agent/remote.py` — transport client: poll
    heartbeat+mailbox on an interval, POST results; offline spool reuse
    of P7's JSON spool for results when the host is unreachable.
  - `services/device_agent/main.py` — `--remote <relay-url>` mode: same
    envelope open/verify/execute path; mailbox I/O goes through the
    remote client instead of local dirs. Local mode untouched.
  - `deploy/oc-device-relay.service` + runbook documenting tailscale-
    bind (recommended) or cloudflared exposure.
- **Files:** `services/device_relay/` (new), `services/device_agent/remote.py`
  (new), `services/device_agent/main.py`, `deploy/oc-device-relay.service`,
  `docs/runbooks/device-remote.md` (new),
  `tests/unit/test_device_relay.py` + `tests/unit/test_device_remote.py` (new).
- **Test scenarios:**
  - Round-trip: dispatch → relay serves sealed envelope → remote client
    opens with the device secret → executes → POSTs signed result →
    host verifies + writes outbox → orchestrator collects.
  - Bad/absent token → 401 on every endpoint; token for device A cannot
    read device B's mailbox.
  - Forged result envelope (wrong secret/nonce replay/stale timestamp) →
    rejected at relay, never reaches the outbox.
  - Host unreachable → agent spools results FIFO and flushes on return
    (P7 spool semantics, remote variant).
  - `kill` on the host → relay denies the device's next poll AND its
    queued tasks are failed (existing kill path, remote-visible).
- **Verification:** two-process integration test on loopback (relay +
  remote-mode agent on separate fsproto roots), no same-fs shortcut.

### P10. Dogfood a real remote device

- **Goal:** the arc is real — a device on another machine runs a goal.
- **Work:**
  - Runbook: pair a second host (Hetzner box or LAN machine) via
    `--remote` over tailscale; TOTP pair → token → heartbeat → parked
    drain → fs grant → `echo`/`fs_read` step end-to-end.
  - Deploy `oc-device-agent.service` on the second host; relay on
    omarchy-max.
  - Audit review: pairing, dispatch, result, kill-switch paths all write
    rows.
- **Files:** `docs/runbooks/device-remote.md`, `deploy/oc-device-agent.service`.
- **Test scenarios:** manual smoke in the runbook (echo step through the
  full remote path); kill switch verified from the console side.
- **Verification:** a recorded goal run whose trace crosses the relay.

## Sequencing

- **P9 first** — independent, immediately useful, no dependencies.
- **P8 second** — the core unit.
- **P10 last** — needs a second machine (user's choice: Hetzner or LAN
  host) and everything before it live.

## Scope Boundaries

- No listener on the device — pull only. No mDNS, no inbound port.
- No new cryptography — existing signed envelopes + device tokens only.
- No TLS inside the relay — tailscale/cloudflared provides the secure
  channel, documented in the runbook.
- No change to the same-host fsproto path — remote is additive.
- No phone/push notification — polling is the model; intake (P7) stays
  device-local.
- No relay for the cloud sandbox plane — this is device-plane only.

## Risks

- **Relay is the new exposed surface.** Mitigation: token auth on every
  request, tailscale-bind by default (LAN exposure opt-in), per-device
  scope (token A never sees mailbox B), kill switch revokes access
  server-side.
- **Result forgery/replay.** Mitigation (partial — honest boundary):
  bearer auth scopes result writes to tasks in that device's own inbox
  (403 cross-device), results are immutable once written, and writes to
  failed/final tasks are refused. Results are NOT signed envelopes —
  a stolen token can forge results for its own device until revoked.
  Server-side result-envelope verification is deferred to a later unit.
- **Sleep/disconnect gaps look like offline.** Mitigation: heartbeat
  staleness is already modeled (eligible/awake); parked drain absorbs
  gaps and flushes on return.
- **Token theft = device impersonation.** Mitigation: scoped tokens,
  TOTP refresh, `oc device kill` revokes; audit every accepted/rejected
  request.
- **Scope creep toward a real remote-procedure system.** Mitigation:
  the relay does no dispatch logic — it moves bytes between HTTP and
  files; all decisions stay in dispatch/drain/agent.

## Success Signals

- A device agent on a *different* filesystem namespace completes a
  sealed step end-to-end (loopback integration test).
- `/api/devices` + console panel show the device's live state including
  parked depth.
- A goal run on a second physical machine passes audit review.

## Sources

- `docs/design/device-plane.md` — P1–P7 contract this extends
- `services/device/protocol.py`, `security.py`, `parked.py` — the
  wire/auth/drain pieces the relay reuses unchanged
- `services/opsview/devices.py` — the collector this wires up
