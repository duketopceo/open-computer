"""Device agent — the process that runs on the user's machine (plan P1 skeleton).

P1 scope: build the registration payload, build heartbeats, verify a
signed step envelope and answer `echo`. Grants, the file capability and
the app/browser capabilities are P2+; anything else is refused here.
"""

from .exec import SUPPORTED, execute
from .grants import GrantViolation, resolve_granted_path
from .heartbeat import heartbeat_payload, registration_payload
from .privacy import LOCAL_TARGETS, PRIVACY_MODES, OnDevicePrivacyGate, PrivacyDecision

__all__ = [
    "LOCAL_TARGETS",
    "PRIVACY_MODES",
    "SUPPORTED",
    "GrantViolation",
    "OnDevicePrivacyGate",
    "PrivacyDecision",
    "execute",
    "heartbeat_payload",
    "registration_payload",
    "resolve_granted_path",
]
