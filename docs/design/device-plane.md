# Device plane (P1: registry, signed steps, `device` placement target)

Status: P1 of `docs/plans/2026-10-16-001-feat-personal-computer-device-plane-plan.md`.
Provenance: documentation-based. The shape copies the publicly documented
Personal Computer contract (a registered machine that must be awake and
online, scoped folder access, work that can only run on that machine);
nothing here describes the vendor's implementation.

## What exists after P1

| Piece | Where | Does |
| --- | --- | --- |
| Registry | `services/device/registry.py` | `register` (idempotent), `heartbeat`, `eligible`, `pick`. JSON file `devices/devices.json`. Stale after 60 s without a heartbeat; asleep or offline devices are never eligible. |
| Envelopes | `services/device/protocol.py` | `seal` / `open_envelope`: HMAC-SHA256 over canonical step JSON + timestamp + nonce. 120 s window, nonce set blocks replay, per-device secret. |
| Placement | `services/placement/rules.py` rule 0 | A step with `device_root`, `needs_local_app` or `needs_local_browser` lands on `device`, before every other rule. Device is local, so I1–I3 hold by construction. With no eligible device the target is still `device` and the reason says the run parks. |
| Dispatch | `services/orchestrator/dispatch.py` | `device` steps go to mailbox `device/<id>`; with no device, to `device/parked`. The audit row carries `mailbox`. |
| Agent skeleton | `services/device_agent/` | Builds registration and heartbeat payloads, drains its mailbox, verifies the envelope, answers `echo`. Everything else is refused and the refusal is the result. |

## Why these choices

- **Rule 0, not a late rule.** A local file cannot be read from a cloud model or a sandbox. If the device rule came after PII or spend rules, a PII-flagged device step would be sent to `local-ollama`, which cannot do the work. Placing it first keeps the placer honest: `needs_device(sub)` ⇒ `device`, always (invariant I6, tested over the full 1,152-case table with each device key forced on).
- **Park, don't fail.** The plan's L1-equivalent for devices is "awake and online or nothing happens". Parking in a named mailbox keeps the run resumable and auditable; P3 drains `device/parked` on the next heartbeat.
- **fsproto as the transport in P1.** Same host, same mailbox semantics as workers. The network hop (device outside the orchestrator host) is P3; swapping transports does not change the envelope or the registry.
- **Secrets never reach the audit log.** The registry stores no secret. The agent writes `devices/<id>.secret` with mode 0600; the orchestrator side of secret exchange is P2 (grants) because grants and secrets share the same approval moment.

## Counterarguments recorded

- This is new attack surface on the user's machine. P1 ships no capability beyond `echo` for that reason; `fs` arrives in P2 with path-scoped, expiring, single-approval grants.
- A registry file is not a fleet manager. Fine: one user, a handful of machines. If that changes, the registry interface (`register`/`heartbeat`/`eligible`/`pick`) is the seam.

## Not in P1

Grants and `fs` capability (P2), network transport and parked-queue drain (P3), app/browser capabilities (P4), opsview device panel (P5), Hybrid-Compute-style local-model preference (P6), install story (P7).
