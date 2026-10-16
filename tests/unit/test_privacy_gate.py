"""Plan P3: on-device privacy gate (mask + restore round-trip, local reroute, refuse, consent)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.device_agent.privacy import LOCAL_TARGETS, PRIVACY_MODES, OnDevicePrivacyGate
from services.gate import Approval, Gate, payload_hash
from services.gate.audit import AuditLog

SAMPLE_PII_TEXT = (
    "Email alice@example.com or bob@example.org, SSN 078-05-1120, "
    "phone 415-555-0199, key sk-or-v1-9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c."
)


def test_supported_modes_and_validation():
    assert PRIVACY_MODES == ("mask", "local", "refuse", "consent")
    assert LOCAL_TARGETS == ("local-ollama", "local-llamacpp")
    with pytest.raises(ValueError, match="invalid privacy mode"):
        OnDevicePrivacyGate(mode="leaky")
    with pytest.raises(ValueError, match="invalid local_target"):
        OnDevicePrivacyGate(mode="local", local_target="cloud-frontier")


def test_mask_mode_deterministic_standins_vault_isolation_and_roundtrip_restore(tmp_path: Path):
    audit = AuditLog(tmp_path / "audit.ndjson")
    pg = OnDevicePrivacyGate(mode="mask", audit=audit)

    text = (
        "Contact alice@example.com (backup: bob@example.org, again alice@example.com), "
        "SSN 078-05-1120, phone (415) 555-0199, token sk-or-v1-9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c."
    )
    payload = {"prompt": text, "meta": {"owner_email": "alice@example.com"}}
    dec = pg.evaluate(payload)

    assert dec.allowed and dec.action == "masked" and dec.flagged
    assert {"email", "ssn", "phone", "credential"} <= set(dec.categories)

    # Stand-in tokens are deterministic and reuse the same index for identical values
    assert "[PII:EMAIL_1]" in dec.outbound_text
    assert "[PII:EMAIL_2]" in dec.outbound_text
    assert dec.outbound_text.count("[PII:EMAIL_1]") == 2
    assert "[PII:SSN_1]" in dec.outbound_text
    assert "[PII:PHONE_1]" in dec.outbound_text
    assert "[PII:SECRET_1]" in dec.outbound_text

    # No raw PII ever appears in outbound_text or serialized outbound_payload
    serialized_outbound = json.dumps(dec.outbound_payload)
    for raw_secret in (
        "alice@example.com",
        "bob@example.org",
        "078-05-1120",
        "555-0199",
        "sk-or-v1-9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c",
    ):
        assert raw_secret not in dec.outbound_text
        assert raw_secret not in serialized_outbound

    # Nested dict fields were also masked with the same deterministic token
    assert dec.outbound_payload["meta"]["owner_email"] == "[PII:EMAIL_1]"

    # Simulate cloud response referencing stand-in tokens and verify exact restoration
    cloud_reply = (
        "Drafted reply to [PII:EMAIL_1] and cc'd [PII:EMAIL_2] regarding SSN [PII:SSN_1] "
        "and phone [PII:PHONE_1] (auth [PII:SECRET_1])."
    )
    restored = pg.restore(cloud_reply)
    assert restored == (
        "Drafted reply to alice@example.com and cc'd bob@example.org regarding SSN 078-05-1120 "
        "and phone (415) 555-0199 (auth sk-or-v1-9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c)."
    )
    # Full round-trip on the original masked text equals original text
    assert pg.restore(dec.outbound_text) == text
    assert pg.restore_payload(dec.outbound_payload) == payload


def test_clean_payload_passes_through_unmodified_in_all_modes():
    clean = {"prompt": "Refactor the retry loop to use exponential backoff with jitter."}
    for mode in PRIVACY_MODES:
        pg = OnDevicePrivacyGate(mode=mode)
        dec = pg.evaluate(clean)
        assert dec.allowed and not dec.flagged and dec.action == "passthrough"
        assert dec.outbound_payload == clean
        assert dec.target == "cloud"


def test_local_mode_reroutes_to_local_model_with_no_cloud_egress():
    pg_ollama = OnDevicePrivacyGate(mode="local", local_target="local-ollama")
    dec1 = pg_ollama.evaluate({"prompt": SAMPLE_PII_TEXT})
    assert dec1.allowed and dec1.flagged
    assert dec1.action == "reroute_local"
    assert dec1.target == "local-ollama"
    assert dec1.outbound_text == "" and dec1.outbound_payload == {}

    pg_llama = OnDevicePrivacyGate(mode="local", local_target="local-llamacpp")
    dec2 = pg_llama.evaluate(SAMPLE_PII_TEXT)
    assert dec2.allowed and dec2.action == "reroute_local" and dec2.target == "local-llamacpp"


def test_refuse_mode_blocks_step_when_pii_detected(tmp_path: Path):
    audit = AuditLog(tmp_path / "audit.ndjson")
    pg = OnDevicePrivacyGate(mode="refuse", audit=audit)
    dec = pg.evaluate({"prompt": SAMPLE_PII_TEXT})
    assert not dec.allowed and dec.flagged
    assert dec.action == "refused"
    assert dec.target == "none"
    assert dec.outbound_text == "" and dec.outbound_payload == {}
    assert "pii_refused" in dec.reason
    assert audit.rows()[-1]["action"] == "refused"


def test_consent_mode_parks_without_valid_approval_and_allows_with_bound_approval(tmp_path: Path):
    gate = Gate(tmp_path / "audit.ndjson", clock=lambda: 1000.0)
    pg = OnDevicePrivacyGate(mode="consent", gate=gate)
    payload = {"model": "frontier", "prompt": SAMPLE_PII_TEXT}
    other_payload = {"model": "frontier", "prompt": "My SSN is 123-45-6789"}

    # 1. No approval -> parks for consent
    d_no_appr = pg.evaluate(payload)
    assert not d_no_appr.allowed and d_no_appr.action == "park_consent"
    assert d_no_appr.target == "parked" and d_no_appr.reason == "consent_required"

    # 2. Forged approval (not in Gate audit log) -> parks
    forged = Approval(id="ap_forged", payload_hash=payload_hash(payload))
    d_forged = pg.evaluate(payload, approval=forged)
    assert not d_forged.allowed and d_forged.action == "park_consent"
    assert "consent_unknown_approval" in d_forged.reason

    # 3. Approval bound to a different payload hash -> parks
    appr_other = gate.grant(other_payload)
    d_mismatch = pg.evaluate(payload, approval=appr_other)
    assert not d_mismatch.allowed and d_mismatch.action == "park_consent"
    assert d_mismatch.reason == "consent_payload_mismatch"

    # 4. Valid human approval covering the exact payload hash -> allowed
    appr_valid = gate.grant(payload)
    d_ok = pg.evaluate(payload, approval=appr_valid)
    assert d_ok.allowed and d_ok.action == "consented"
    assert d_ok.target == "cloud" and d_ok.outbound_payload == payload
