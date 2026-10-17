# Device plane (P1–P7: registry, grants, privacy gate, wisp actuation, parked drain, security, and intake)

Status: Complete (`P1`–`P7` of `docs/plans/2026-10-16-001-feat-personal-computer-device-plane-plan.md`).
Provenance: documentation-based. The shape copies the publicly documented
Personal Computer contract (a registered machine that must be awake and
online, scoped folder access, work that runs on that machine, an on-device
privacy gate that masks, reroutes locally, refuses, or asks for consent before
any payload leaves the device, local app/browser control, remote/device-origin
starts, 2FA + kill switch, and hotkey/voice intake); nothing here describes the
vendor's internal implementation.

## Architecture Overview (P1–P7)

| Unit | Piece | Where | Contract |
| --- | --- | --- | --- |
| P1 | Registry | `services/device/registry.py` | `register` (idempotent), `heartbeat`, `eligible`, `pick`. JSON file `devices/devices.json`. Stale after 60 s without a heartbeat; asleep or offline devices are never eligible. |
| P1 | Envelopes | `services/device/protocol.py` | `seal` / `open_envelope`: HMAC-SHA256 over canonical step JSON + timestamp + nonce. 120 s window, nonce set blocks replay, per-device secret. |
| P1 | Placement | `services/placement/rules.py` rule 0 | A step with `device_root`, `needs_local_app` or `needs_local_browser` lands on `device`, before every other rule. Device is local, so I1–I3 hold by construction. With no eligible device the target is still `device` and the reason says the run parks. |
| P1 | Dispatch | `services/orchestrator/dispatch.py` | `device` steps go to mailbox `device/<id>`; with no device, to `device/parked`. The audit row carries `mailbox`. |
| P2 | Grant store | `services/device/grants.py` | `Grant` dataclass and file-backed `GrantStore` (`devices/grants.json`): `add(device_id, root, modes, ttl_s)`, `list(device_id)`, `revoke(grant_id)`, and `check(device_id, path, mode, now)` with append-only audit logging. Modes are a subset of `("read", "write", "exec")`. |
| P2 | Path confinement | `services/device_agent/grants.py` | `resolve_granted_path(grant_root, candidate_path)` resolves symlinks via `Path.resolve()` and raises `GrantViolation` if the target escapes `grant_root` (`..` traversal, absolute paths outside root, or symlinks pointing outside root). |
| P3 | On-device privacy gate | `services/device_agent/privacy.py` | `OnDevicePrivacyGate` reuses `services/gate/pii.py` `scan()` across four modes: `"mask"` (deterministic `[PII:<TYPE>_<N>]` stand-in tokens with device-held vault and `restore(text)` on return), `"local"` (reroutes step to `local-ollama` / `local-llamacpp` with zero cloud egress), `"refuse"` (blocks when PII is flagged), and `"consent"` (requires an explicit approval token bound to the payload hash or parks for consent). |
| P4 | Wisp actuator | `services/device_agent/wisp_actuator.py` + `services/device_agent/exec.py` | `WispActuator` speaks `wispd`'s Unix domain socket JSON-line IPC (`$XDG_RUNTIME_DIR/wisp/wispd.sock` or `/tmp/wisp/wispd.sock`, with an injectable transport for tests). `execute()` supports `("echo", "fs_read", "fs_write", "fs_list", "app_action", "browser_action")`, verifies the device has capability `"app"` / `"browser"`, and when `WispActuator` returns `requires_confirm=True`, enqueues a consent request into `ApprovalQueue` and parks the step. |
| P5 | Parked drain & opsview | `services/device/parked.py` + `services/opsview/devices.py` | `drain_parked(fs, registry, audit)` moves pending tasks from `device/parked` to `device/<device_id>` when an eligible device registers or heartbeats and appends a `parked_drained` audit event. `Orchestrator.submit` / `run` record `origin="device"` and `device_id`. `collect_devices(root)` reports registered devices, online/awake/eligible state, active grant counts, and `device/parked` depth. |
| P6 | TOTP pairing & kill switch | `services/device/security.py` + `deploy/oc-device.service` | Pure-stdlib RFC 6238 TOTP (`mint_totp_secret`, `generate_totp`, `verify_totp`) gating `pair_device` and `refresh_device_token`. `kill_device` revokes all active grants for `device_id`, deletes `<device_id>.secret` (and `.token.json`), marks the device offline in `DeviceRegistry`, fails any queued tasks in `device/<device_id>`, and writes a `device_killed` audit event. `deploy/oc-device.service` provides a hardened systemd user service with `Restart=on-failure`. |
| P7 | Hotkey & voice intake | `services/device_agent/intake.py` | `DeviceIntake.submit_goal(prompt, device_id, origin="device")` submits directly to `Orchestrator` when reachable or spools to `offline_goals.json` when offline, flushing in FIFO order via `flush()` / `on_reconnect()`. |

## Why these choices

- **Rule 0, not a late rule.** A local file, native app, or local browser profile cannot be driven from a cloud model or a cloud microVM. Placing device-bound steps first guarantees `needs_device(sub)` ⇒ `device`, always (invariant I6, tested over the full 1,152-case table with each device key forced on).
- **Park, don't fail, and drain on heartbeat.** Parking in `device/parked` keeps the run resumable and auditable. When the device comes online and heartbeats, `drain_parked` atomically links the task envelope into `device/<device_id>`'s inbox, unlinks it from `device/parked`, and writes a `parked_drained` audit record.
- **Explicit roots, no Full Disk Access.** Every file operation resolves symlinks on the device and checks a time-bounded, mode-scoped `Grant`. `..` segments and symlink escapes are rejected before any I/O occurs, and every check is written to the audit log.
- **Stand-in vault stays on the device.** In `"mask"` mode, `OnDevicePrivacyGate` replaces each span with `[PII:EMAIL_1]`, `[PII:SSN_1]`, `[PII:PHONE_1]`, `[PII:SECRET_1]`, etc. The mapping table lives only on the `OnDevicePrivacyGate` instance on the device (`_token_to_raw`), never in `outbound_payload` or `outbound_text`. When cloud output returns, `restore(text)` swaps the stand-ins back locally.
- **Delegate Linux actuation to `wispd`.** Instead of building a second Wayland/Hyprland/CUA driver, `WispActuator` talks to `wispd` over its JSON-line Unix socket and bridges `wispd` confirmation cards directly into `open-computer`'s `ApprovalQueue`.
- **TOTP + one-call kill switch.** Headless always-on hosts require RFC 6238 TOTP at pairing and token rotation, and `kill_device` revokes grants, wipes secrets, marks the device offline, fails queued inbox tasks, and audits the kill in one atomic operation.
