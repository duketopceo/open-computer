"""OC-023: the execplane rows R1-R3 replayed from cassettes against the REAL adapter, in CI, deterministically.

Provenance matters: today's cassettes are `synthetic` (hand-written from SDK semantics). The tests prove the adapter
sends exactly the requests the cassette says and that the checks read the responses correctly; a `recorded`
cassette from `scripts/record_cube.py` (live, by hand) replaces them and the same tests then bind to real behaviour.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from checks import execplane_checks as ec
from harness import CheckFailed
from services.execplane.cassette import CassetteError, CassetteMiss, RecordingSDK, ReplaySDK, load
from services.execplane.client import ExecplaneAdapter

CASSETTES = Path(__file__).resolve().parents[1] / "fixtures" / "cube"
MAIN = CASSETTES / "r1_r3_synthetic.json"


def adapter(tmp_path, cassette) -> tuple[ExecplaneAdapter, ReplaySDK]:
    a = ExecplaneAdapter(tmp_path)
    sdk = ReplaySDK(cassette)
    a._sdk = sdk
    return a, sdk


CHECKS = [
    ec.fail_closed_on_boot_failure,
    ec.empty_allowlist_blocks_egress,
    ec.allowlist_is_exact,
    ec.no_credential_material_in_guest,
]


@pytest.mark.parametrize("fn", CHECKS, ids=lambda f: f.__name__)
def test_rows_pass_replayed_from_the_cassette(tmp_path, fn):
    a, _ = adapter(tmp_path, MAIN)
    fn(a.make_sandbox)


def test_every_recorded_interaction_is_exercised(tmp_path):
    a, sdk = adapter(tmp_path, MAIN)
    for fn in CHECKS:
        fn(a.make_sandbox)
    assert sdk.unused() == []  # a cassette entry no check uses is dead weight or a missing check


def test_cube_503_on_create_is_fail_closed(tmp_path):
    a, _ = adapter(tmp_path, CASSETTES / "r1_create_503_synthetic.json")
    sb = a.make_sandbox()
    with pytest.raises(Exception, match="503"):
        sb.exec("echo should-not-run")


def test_adapter_drift_is_a_cassette_miss_not_a_silent_pass(tmp_path):
    a, sdk = adapter(tmp_path, MAIN)
    real_create = sdk.Sandbox.create
    sdk.Sandbox.create = staticmethod(lambda **p: real_create(**{**p, "allow_internet_access": True}))
    sb = a.make_sandbox(egress_allowlist=[])
    # create failed to match -> adapter fails closed; the check then reports the control failure
    with pytest.raises(Exception):
        sb.exec("true")
    with pytest.raises(CassetteMiss):
        real_create(**{"template": "code-interpreter", "timeout": 300, "env_vars": None,
                       "allow_internet_access": True})


def _mutate(path, fn):
    d = copy.deepcopy(json.loads(path.read_text()))
    fn(d)
    return d


def test_a_cassette_where_egress_was_open_fails_the_row(tmp_path):
    def open_it(d):
        for i in d["interactions"]:
            if i["op"] == "run" and i["sandbox"] == "sb-deny":
                i["result"] = {"exit_code": 0}

    a, _ = adapter(tmp_path, _mutate(MAIN, open_it))
    with pytest.raises(CheckFailed, match="allowed with empty allowlist"):
        ec.empty_allowlist_blocks_egress(a.make_sandbox)


def test_a_cassette_with_a_broken_network_fails_the_positive_control(tmp_path):
    def break_net(d):
        for i in d["interactions"]:
            if i["op"] == "run" and "example.com" in i["cmd"]:
                i["result"] = {"exit_code": 6}

    a, _ = adapter(tmp_path, _mutate(MAIN, break_net))
    with pytest.raises(CheckFailed, match="positive control"):
        ec.empty_allowlist_blocks_egress(a.make_sandbox)


def test_a_cassette_with_the_real_secret_in_the_guest_fails_r3(tmp_path):
    def leak(d):
        for i in d["interactions"]:
            if i["op"] == "run" and i["cmd"] == "env":
                i["result"]["stdout"] += "OPENROUTER_API_KEY=oc-sentinel-7f3a9c\n"

    a, _ = adapter(tmp_path, _mutate(MAIN, leak))
    with pytest.raises(CheckFailed, match="real secret present"):
        ec.no_credential_material_in_guest(a.make_sandbox)


def test_schema_version_and_provenance_are_enforced(tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text(json.dumps({"schema": 99, "provenance": "recorded", "interactions": []}))
    with pytest.raises(CassetteError, match="schema"):
        load(bad)
    bad.write_text(json.dumps({"schema": 1, "provenance": "made-up", "interactions": []}))
    with pytest.raises(CassetteError, match="provenance"):
        load(bad)


def test_committed_cassettes_hold_no_secrets():
    sentinels = ["sk-or-v1-", "oc-sentinel", "Bearer ", "CUBE_API_KEY"]
    for p in CASSETTES.glob("*.json"):
        text = p.read_text()
        for s in sentinels:
            assert s not in text, f"{p.name} contains {s!r}"
        assert load(p)["provenance"] in ("recorded", "synthetic")


def test_recording_wrapper_produces_a_replayable_scrubbed_cassette(tmp_path):
    """The recorder is exercised against the fake SDK (never a real Cube): record, scrub, replay."""
    from fake_cube import FakeCubeSDK

    out = tmp_path / "rec.json"
    rec = RecordingSDK(FakeCubeSDK(), out, scrub=["secret-value-123"])
    a = ExecplaneAdapter(tmp_path)
    a._sdk = rec
    ec.empty_allowlist_blocks_egress(a.make_sandbox)
    ec.no_credential_material_in_guest(a.make_sandbox)
    rec.save()
    assert "secret-value-123" not in out.read_text() and load(out)["provenance"] == "recorded"
    a2 = ExecplaneAdapter(tmp_path)
    a2._sdk = ReplaySDK(out)
    ec.empty_allowlist_blocks_egress(a2.make_sandbox)
    ec.no_credential_material_in_guest(a2.make_sandbox)
