# Device plane (P1–P3: registry, folder grants, and on-device privacy gate)

Status: P1–P3 of `docs/plans/2026-10-16-001-feat-personal-computer-device-plane-plan.md`.
Provenance: documentation-based. The shape copies the publicly documented
Personal Computer contract (a registered machine that must be awake and
online, scoped folder access, work that can only run on that machine, and an
on-device privacy gate that masks, reroutes locally, refuses, or asks for
consent before any payload leaves the device); nothing here describes the
vendor's internal implementation.

## What exists after P1–P3

| Piece | Where | Does |
| --- | --- | --- |
| Registry (P1) | `services/device/registry.py` | `register` (idempotent), `heartbeat`, `eligible`, `pick`. JSON file `devices/devices.json`. Stale after 60 s without a heartbeat; asleep or offline devices are never eligible. |
| Envelopes (P1) | `services/device/protocol.py` | `seal` / `open_envelope`: HMAC-SHA256 over canonical step JSON + timestamp + nonce. 120 s window, nonce set blocks replay, per-device secret. |
| Placement (P1) | `services/placement/rules.py` rule 0 | A step with `device_root`, `needs_local_app` or `needs_local_browser` lands on `device`, before every other rule. Device is local, so I1–I3 hold by construction. With no eligible device the target is still `device` and the reason says the run parks. |
| Dispatch (P1) | `services/orchestrator/dispatch.py` | `device` steps go to mailbox `device/<id>`; with no device, to `device/parked`. The audit row carries `mailbox`. |
| Grant store (P2) | `services/device/grants.py` | `Grant` dataclass and file-backed `GrantStore` (`devices/grants.json`): `add(device_id, root, modes, ttl_s)`, `list(device_id)`, `revoke(grant_id)`, and `check(device_id, path, mode, now)` with append-only audit logging. Modes are a subset of `("read", "write", "exec")`. |
| Path confinement (P2) | `services/device_agent/grants.py` | `resolve_granted_path(grant_root, candidate_path)` resolves symlinks via `Path.resolve()` and raises `GrantViolation` if the target escapes `grant_root` (`..` traversal, absolute paths outside root, or symlinks pointing outside root). |
| Device executor (P1+P2) | `services/device_agent/exec.py` | Supports `("echo", "fs_read", "fs_write", "fs_list")`. File steps require a valid, non-expired, non-revoked grant matching `"read"` or `"write"` and record every check (allowed or refused) to the audit log. |
| On-device privacy gate (P3) | `services/device_agent/privacy.py` | `OnDevicePrivacyGate` reuses `services/gate/pii.py` `scan()` across four modes: `"mask"` (deterministic `[PII:<TYPE>_<N>]` stand-in tokens with device-held vault and `restore(text)` on return), `"local"` (reroutes step to `local-ollama` / `local-llamacpp` with zero cloud egress), `"refuse"` (blocks when PII is flagged), and `"consent"` (requires an explicit approval token bound to the payload hash or parks for consent). |

## Why these choices

- **Rule 0, not a late rule.** A local file cannot be read from a cloud model or a sandbox. If the device rule came after PII or spend rules, a PII-flagged device step would be sent to `local-ollama`, which cannot do the work. Placing it first keeps the placer honest: `needs_device(sub)` ⇒ `device`, always (invariant I6, tested over the full 1,152-case table with each device key forced on).
- **Park, don't fail.** The plan's L1-equivalent for devices is "awake and online or nothing happens". Parking in a named mailbox keeps the run resumable and auditable.
- **Explicit roots, no Full Disk Access.** Every file operation resolves symlinks on the device and checks a time-bounded, mode-scoped `Grant`. `..` segments and symlink escapes are rejected before any I/O occurs, and every check is written to the audit log.
- **Stand-in vault stays on the device.** In `"mask"` mode, `OnDevicePrivacyGate` replaces each span with `[PII:EMAIL_1]`, `[PII:SSN_1]`, `[PII:PHONE_1]`, `[PII:SECRET_1]`, etc. The mapping table lives only on the `OnDevicePrivacyGate` instance on the device (`_token_to_raw`), never in `outbound_payload` or `outbound_text`. When cloud output returns, `restore(text)` swaps the stand-ins back locally.

## Counterarguments recorded

- This is new attack surface on the user's machine. `fs_read`, `fs_write`, and `fs_list` are disabled unless a `GrantStore` and `device_id` are explicitly supplied to `execute()`, and every path check is confined to canonicalized grant roots.
- Regex-based PII prefiltering can miss unstructured context. `services/gate/pii.py` is the deterministic floor; `"consent"` and `"local"` modes let high-sensitivity workflows avoid cloud egress altogether.

## Not in P1–P3

App/browser actuation through wisp (P4), phone-initiated device runs and opsview device column (P5), always-on host with TOTP and kill switch (P6), hotkey/voice intake (P7).
