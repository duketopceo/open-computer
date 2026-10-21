"""Remote device relay (plan P8) — pull transport for device agents on other machines.

Same-host agents read fsproto mailboxes directly; a remote agent can't see the
orchestrator root, so this thin authenticated HTTP layer re-exposes exactly the
operations the drain loop needs. The filesystem stays the durable spine — the
relay translates HTTP to FSAdapter/registry calls and keeps no state of its own.

Auth: `Authorization: Bearer <device secret>` — the same secret pairing writes to
`devices/<id>.secret` (P6). Each request loads `devices/<id>.token.json`, requires
the record active and the secret matching in constant time. Kill-switch revocation
and token expiry therefore cut relay access with no extra code. Per-task
operations additionally require the task to currently sit in *that* device's
inbox — a device can never touch another's mailbox, even holding its own token.

TLS is delegated to the transport (tailscale / cloudflared); bind on loopback or a
tailscale interface, never re-implement encryption here.
"""

from services.device_relay.server import make_server

__all__ = ["make_server"]
