"""Device agent — the process that runs on the user's machine (plan P1–P7).

Supports registration, heartbeats, signed step verification, folder-scoped
file operations (P2), on-device privacy gating with stand-in vault (P3),
Linux app/browser actuation through `wispd` (P4), and hotkey/voice goal
intake with offline spooling (P7).
"""

from .exec import SUPPORTED, execute
from .grants import GrantViolation, resolve_granted_path
from .heartbeat import heartbeat_payload, registration_payload
from .intake import DeviceIntake
from .privacy import LOCAL_TARGETS, PRIVACY_MODES, OnDevicePrivacyGate, PrivacyDecision
from .wisp_actuator import WispActuator, default_sock_path

__all__ = [
    "LOCAL_TARGETS",
    "PRIVACY_MODES",
    "SUPPORTED",
    "DeviceIntake",
    "GrantViolation",
    "OnDevicePrivacyGate",
    "PrivacyDecision",
    "WispActuator",
    "default_sock_path",
    "execute",
    "heartbeat_payload",
    "registration_payload",
    "resolve_granted_path",
]
