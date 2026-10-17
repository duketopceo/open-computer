"""On-device privacy gate — P3 of the Personal Computer device plane.

Runs on the device before any step payload or local file excerpt leaves for the
cloud. Reuses `services/gate/pii.py` `scan()` as the deterministic prefilter and
enforces four privacy modes:

  - "mask": replaces every detected PII span with a deterministic typed stand-in
    token (e.g. `[PII:EMAIL_1]`, `[PII:SSN_1]`, `[PII:PHONE_1]`, `[PII:SECRET_1]`),
    stores the mapping locally on the gate instance (never in the outbound
    payload), and provides `restore(text)` to swap stand-ins back when cloud
    results return.
  - "local": returns a decision instructing the caller to reroute the step to a
    local model (`local-ollama` / `local-llamacpp`) with no cloud egress.
  - "refuse": blocks the step when PII is detected.
  - "consent": requires an explicit approval token covering the payload hash,
    otherwise parks for consent.

Provenance: documentation-based (Perplexity Hybrid Compute / Personal Computer
privacy gate description).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from services.gate.gate import Approval, Gate, payload_hash
from services.gate.pii import _PATTERNS, _luhn_ok, scan

PRIVACY_MODES = ("mask", "local", "refuse", "consent")
LOCAL_TARGETS = ("local-ollama", "local-llamacpp")

_TOKEN_LABELS: dict[str, str] = {
    "email": "EMAIL",
    "ssn": "SSN",
    "phone": "PHONE",
    "credential": "SECRET",
    "card": "CARD",
    "address": "ADDRESS",
}


@dataclass(frozen=True)
class PIISpan:
    category: str
    label: str
    start: int
    end: int
    text: str


@dataclass
class PrivacyDecision:
    mode: str
    allowed: bool
    action: str  # "passthrough" | "masked" | "reroute_local" | "refused" | "park_consent" | "consented"
    flagged: bool
    categories: list[str] = field(default_factory=list)
    outbound_text: str = ""
    outbound_payload: dict[str, Any] = field(default_factory=dict)
    target: str = "cloud"
    reason: str = ""
    payload_hash: str = ""


def _find_spans(text: str) -> list[PIISpan]:
    """Find all non-overlapping PII spans in deterministic order (by start, then longest match)."""
    candidates: list[PIISpan] = []
    for cat, pat in _PATTERNS.items():
        label = _TOKEN_LABELS.get(cat, cat.upper())
        for m in pat.finditer(text):
            raw = m.group()
            if cat == "card" and not _luhn_ok(re.sub(r"\D", "", raw)):
                continue
            candidates.append(PIISpan(category=cat, label=label, start=m.start(), end=m.end(), text=raw))

    candidates.sort(key=lambda s: (s.start, -(s.end - s.start), s.category))
    chosen: list[PIISpan] = []
    cursor = 0
    for sp in candidates:
        if sp.start < cursor:
            continue
        chosen.append(sp)
        cursor = sp.end
    return chosen


def _canonical_payload_hash(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, dict):
        return payload_hash(payload)
    blob = json.dumps({"text": str(payload)}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


class OnDevicePrivacyGate:
    """Device-side privacy gate with deterministic stand-in masking and local map storage."""

    def __init__(
        self,
        mode: str = "mask",
        *,
        local_target: str = "local-ollama",
        gate: Gate | None = None,
        audit: Any | None = None,
    ):
        if mode not in PRIVACY_MODES:
            raise ValueError(f"invalid privacy mode {mode!r}; expected one of {PRIVACY_MODES}")
        if local_target not in LOCAL_TARGETS:
            raise ValueError(f"invalid local_target {local_target!r}; expected one of {LOCAL_TARGETS}")
        self.mode = mode
        self.local_target = local_target
        self.gate = gate
        self.audit = audit
        # Device-local stand-in tables; never serialized into outbound payloads.
        self._token_to_raw: dict[str, str] = {}
        self._raw_to_token: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    @property
    def vault_size(self) -> int:
        return len(self._token_to_raw)

    def _mint_or_get_token(self, label: str, raw: str) -> str:
        key = (label, raw)
        existing = self._raw_to_token.get(key)
        if existing is not None:
            return existing
        idx = self._counters.get(label, 0) + 1
        self._counters[label] = idx
        token = f"[PII:{label}_{idx}]"
        self._raw_to_token[key] = token
        self._token_to_raw[token] = raw
        return token

    def mask_text(self, text: str) -> tuple[str, list[str]]:
        """Replace detected PII spans with deterministic stand-in tokens."""
        pii_dec = scan(text)
        spans = _find_spans(text)
        if not spans:
            return text, list(pii_dec.categories)

        out_parts: list[str] = []
        cursor = 0
        seen_cats: list[str] = []
        for sp in spans:
            out_parts.append(text[cursor : sp.start])
            token = self._mint_or_get_token(sp.label, sp.text)
            out_parts.append(token)
            cursor = sp.end
            if sp.category not in seen_cats:
                seen_cats.append(sp.category)
        out_parts.append(text[cursor:])
        for c in pii_dec.categories:
            if c not in seen_cats:
                seen_cats.append(c)
        return "".join(out_parts), seen_cats

    def restore(self, text: str) -> str:
        """Swap stand-in tokens in `text` back to their original on-device values."""
        result = text
        # Replace in reverse length order so [PII:EMAIL_10] is replaced before [PII:EMAIL_1]
        for token in sorted(self._token_to_raw, key=len, reverse=True):
            result = result.replace(token, self._token_to_raw[token])
        return result

    def restore_payload(self, value: Any) -> Any:
        """Recursively restore stand-in tokens in strings, dicts, and lists."""
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, list):
            return [self.restore_payload(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.restore_payload(v) for v in value)
        if isinstance(value, dict):
            return {self.restore_payload(k): self.restore_payload(v) for k, v in value.items()}
        return value

    def _mask_structure(self, value: Any) -> Any:
        if isinstance(value, str):
            masked, _ = self.mask_text(value)
            return masked
        if isinstance(value, dict):
            # Keys are serialized into the scanned JSON too — a PII-bearing key leaks
            # the same way a value would.
            return {self._mask_structure(k): self._mask_structure(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self._mask_structure(v) for v in value]
        if value is None or isinstance(value, bool):
            return value
        # Non-str scalars: the scanner saw their serialized form — mask str(value)
        # when it trips a pattern, otherwise keep the typed value.
        masked, _ = self.mask_text(str(value))
        return masked if masked != str(value) else value

    def _record_audit(self, row: dict[str, Any]) -> None:
        if self.audit is not None and hasattr(self.audit, "append"):
            self.audit.append(row)
        elif self.gate is not None and hasattr(self.gate, "audit"):
            self.gate.audit.append(row)

    def evaluate(
        self,
        payload: dict[str, Any] | str,
        *,
        mode: str | None = None,
        approval: Approval | dict[str, str] | None = None,
    ) -> PrivacyDecision:
        """Run the on-device privacy gate on `payload` (str or dict) under `mode`."""
        active_mode = mode or self.mode
        if active_mode not in PRIVACY_MODES:
            raise ValueError(f"invalid privacy mode {active_mode!r}; expected one of {PRIVACY_MODES}")

        if isinstance(payload, str):
            raw_text = payload
            payload_dict: dict[str, Any] = {"text": payload}
        else:
            payload_dict = dict(payload)
            raw_text = (
                str(payload_dict.get("text") or payload_dict.get("prompt") or payload_dict.get("payload") or "")
                + "\n"
                + json.dumps(payload_dict, sort_keys=True, default=str)
            )

        p_hash = _canonical_payload_hash(payload)
        pii_dec = scan(raw_text)
        spans = _find_spans(raw_text)
        flagged = pii_dec.flagged or bool(spans)
        categories = list(pii_dec.categories)
        for sp in spans:
            if sp.category not in categories:
                categories.append(sp.category)

        if not flagged:
            out_text = (
                payload
                if isinstance(payload, str)
                else str(payload_dict.get("text") or payload_dict.get("prompt") or payload_dict.get("payload") or "")
            )
            dec = PrivacyDecision(
                mode=active_mode,
                allowed=True,
                action="passthrough",
                flagged=False,
                categories=[],
                outbound_text=out_text,
                outbound_payload=dict(payload_dict),
                target="cloud",
                reason="clean",
                payload_hash=p_hash,
            )
            self._record_audit(
                {
                    "event": "device_privacy_gate",
                    "mode": active_mode,
                    "action": dec.action,
                    "allowed": True,
                    "flagged": False,
                    "categories": [],
                    "payload_hash": p_hash,
                }
            )
            return dec

        if active_mode == "mask":
            if isinstance(payload, str):
                masked_text, _ = self.mask_text(payload)
                masked_payload = {"text": masked_text}
            else:
                masked_payload = self._mask_structure(payload_dict)
                primary_key = next(
                    (k for k in ("text", "prompt", "payload") if isinstance(masked_payload.get(k), str)),
                    None,
                )
                masked_text = masked_payload[primary_key] if primary_key else json.dumps(masked_payload, sort_keys=True)
            dec = PrivacyDecision(
                mode="mask",
                allowed=True,
                action="masked",
                flagged=True,
                categories=categories,
                outbound_text=masked_text,
                outbound_payload=masked_payload,
                target="cloud",
                reason="pii_masked_on_device",
                payload_hash=p_hash,
            )
            self._record_audit(
                {
                    "event": "device_privacy_gate",
                    "mode": "mask",
                    "action": "masked",
                    "allowed": True,
                    "flagged": True,
                    "categories": categories,
                    "payload_hash": p_hash,
                }
            )
            return dec

        if active_mode == "local":
            out_text = (
                payload
                if isinstance(payload, str)
                else str(payload_dict.get("text") or payload_dict.get("prompt") or payload_dict.get("payload") or "")
            )
            dec = PrivacyDecision(
                mode="local",
                allowed=True,
                action="reroute_local",
                flagged=True,
                categories=categories,
                outbound_text="",
                outbound_payload={},
                target=self.local_target,
                reason=f"pii_rerouted_to_{self.local_target}",
                payload_hash=p_hash,
            )
            self._record_audit(
                {
                    "event": "device_privacy_gate",
                    "mode": "local",
                    "action": "reroute_local",
                    "allowed": True,
                    "flagged": True,
                    "categories": categories,
                    "target": self.local_target,
                    "payload_hash": p_hash,
                }
            )
            return dec

        if active_mode == "refuse":
            dec = PrivacyDecision(
                mode="refuse",
                allowed=False,
                action="refused",
                flagged=True,
                categories=categories,
                outbound_text="",
                outbound_payload={},
                target="none",
                reason=f"pii_refused:{','.join(categories)}",
                payload_hash=p_hash,
            )
            self._record_audit(
                {
                    "event": "device_privacy_gate",
                    "mode": "refuse",
                    "action": "refused",
                    "allowed": False,
                    "flagged": True,
                    "categories": categories,
                    "payload_hash": p_hash,
                }
            )
            return dec

        # active_mode == "consent"
        tok_id: str | None = None
        tok_hash: str | None = None
        if isinstance(approval, Approval):
            tok_id = approval.id
            tok_hash = approval.payload_hash
        elif isinstance(approval, dict):
            tok_id = approval.get("id")
            tok_hash = approval.get("payload_hash")

        verified = False
        reason = "consent_required"
        if tok_id and tok_hash == p_hash:
            if self.gate is None:
                # Hash equality alone is forgeable — anyone holding the payload can compute it.
                # Without a Gate there is no authoritative approval record, so this parks.
                reason = "consent_no_gate"
            else:
                refusal = self.gate._refusal(payload_dict, Approval(id=tok_id, payload_hash=tok_hash))
                if refusal is None:
                    verified = True
                    reason = "consent_verified"
                else:
                    reason = f"consent_{refusal}"
        elif tok_id and tok_hash != p_hash:
            reason = "consent_payload_mismatch"

        if verified:
            out_text = (
                payload
                if isinstance(payload, str)
                else str(payload_dict.get("text") or payload_dict.get("prompt") or payload_dict.get("payload") or "")
            )
            dec = PrivacyDecision(
                mode="consent",
                allowed=True,
                action="consented",
                flagged=True,
                categories=categories,
                outbound_text=out_text,
                outbound_payload=dict(payload_dict),
                target="cloud",
                reason=reason,
                payload_hash=p_hash,
            )
        else:
            dec = PrivacyDecision(
                mode="consent",
                allowed=False,
                action="park_consent",
                flagged=True,
                categories=categories,
                outbound_text="",
                outbound_payload={},
                target="parked",
                reason=reason,
                payload_hash=p_hash,
            )

        self._record_audit(
            {
                "event": "device_privacy_gate",
                "mode": "consent",
                "action": dec.action,
                "allowed": dec.allowed,
                "flagged": True,
                "categories": categories,
                "reason": reason,
                "payload_hash": p_hash,
            }
        )
        return dec
