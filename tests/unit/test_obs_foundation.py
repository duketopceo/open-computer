"""oc.obs: correlation ids, redaction, structured logging."""

import json
import logging
import threading

import pytest

from oc import obs
from oc.obs import context, log as obs_log
from oc.obs.redact import MASK, redact, redact_str, sensitive_key

SECRETS = [
    "sk-or-v1-0123456789abcdef0123456789abcdef",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk",
    "AKIAABCDEFGHIJKLMNOP",
]


@pytest.fixture
def stderr_logs(capsys, monkeypatch):
    obs_log.configure(force=True)

    def read():
        err = capsys.readouterr().err
        return [json.loads(line) for line in err.splitlines() if line.strip()]

    return read


# --- context -------------------------------------------------------------------------------------
def test_trace_id_is_deterministic_32_hex():
    a = context.trace_id_for("run1")
    assert a == context.trace_id_for("run1") and a != context.trace_id_for("run2")
    assert len(a) == 32 and int(a, 16) >= 0


def test_bind_derives_trace_and_chat_and_restores():
    assert context.current() == {}
    with obs.bind(run_id="r1", subtask="s1", bogus="x", chat_id=None) as c:
        assert c["trace_id"] == context.trace_id_for("r1")
        assert c["chat_id"] == "r1"  # falls back to the run
        assert "bogus" not in c
        with obs.bind(chat_id="chatA"):
            assert context.current()["chat_id"] == "chatA" and context.current()["run_id"] == "r1"
        assert context.current()["chat_id"] == "r1"
    assert context.current() == {}


def test_context_is_per_thread():
    seen = {}

    def worker(n):
        with obs.bind(run_id=f"r{n}"):
            threading.Event().wait(0.01)
            seen[n] = context.current()["run_id"]

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert seen == {i: f"r{i}" for i in range(5)}


# --- redaction -----------------------------------------------------------------------------------
@pytest.mark.parametrize("secret", SECRETS)
def test_value_patterns_are_masked(secret):
    assert secret not in redact_str(f"calling with {secret} now")
    assert secret not in json.dumps(redact({"nested": [{"x": f"Bearer {secret}"}]}))


def test_key_names_mask_values_but_not_counters():
    out = redact({"api_key": "abc", "Authorization": "x", "OPENROUTER_API_KEY": "y", "tokens_in": 5,
                  "max_tokens": 100, "cached_tokens": 3, "cost": 0.1, "token": "t", "password": "p"})
    assert out["api_key"] == out["Authorization"] == out["OPENROUTER_API_KEY"] == out["token"] == MASK
    assert out["password"] == MASK
    assert (out["tokens_in"], out["max_tokens"], out["cached_tokens"], out["cost"]) == (5, 100, 3, 0.1)


def test_pii_and_url_credentials_masked():
    s = redact_str("mail jane.doe@example.com or call 303-555-1212; https://u:hunter2@host/x?api_key=zzz&a=1")
    for leaked in ("jane.doe@example.com", "303-555-1212", "hunter2", "zzz"):
        assert leaked not in s
    assert "a=1" in s


def test_timestamps_and_ids_are_not_mangled():
    assert redact_str("ts 1791700021.4 gen-3bhGkxlo4XFrqiabUM7N run 0123456789ab") == (
        "ts 1791700021.4 gen-3bhGkxlo4XFrqiabUM7N run 0123456789ab"
    )


def test_env_secret_values_are_scrubbed_even_without_a_pattern(monkeypatch):
    monkeypatch.setenv("MY_SERVICE_TOKEN", "plainsecretvalue123")
    assert "plainsecretvalue123" not in redact_str("failed with plainsecretvalue123 at host")
    monkeypatch.setenv("SHORT_TOKEN", "abc")  # too short to be a safe scrub target
    assert redact_str("abc stays") == "abc stays"


def test_redact_does_not_mutate_and_handles_odd_types():
    src = {"k": {"api_key": "v"}, "b": b"1234", "s": {1, 2}, "o": object()}
    out = redact(src)
    assert src["k"]["api_key"] == "v"
    assert out["b"] == "<4 bytes>" and isinstance(out["s"], list) and isinstance(out["o"], str)
    deep = cur = {}
    for _ in range(30):
        cur["n"] = {}
        cur = cur["n"]
    assert "[TRUNCATED]" in json.dumps(redact(deep))


def test_sensitive_key_table():
    assert sensitive_key("x-api-key") and sensitive_key("private_key") and sensitive_key("OC_SENTRY_DSN")
    assert not sensitive_key("tokens") and not sensitive_key("generation_id") and not sensitive_key("latency_ms")


# --- logging -------------------------------------------------------------------------------------
def test_json_shape_ids_and_levels(stderr_logs):
    log = obs.get_logger("router")
    with obs.bind(run_id="r9", subtask="s2", chat_id="c1"):
        log.info("decided", route="cloud", cost=0.001)
        log.debug("hidden at INFO")
    (row,) = stderr_logs()
    assert row["level"] == "INFO" and row["service"] == "router" and row["msg"] == "decided"
    assert row["run_id"] == "r9" and row["chat_id"] == "c1" and row["trace_id"] == context.trace_id_for("r9")
    assert row["route"] == "cloud" and row["ts"].endswith("+00:00")


def test_debug_mode_and_per_service_level(stderr_logs, monkeypatch):
    log = obs.get_logger("placement")
    log.debug("off by default")
    assert stderr_logs() == []
    monkeypatch.setenv("OC_LOG_LEVEL_PLACEMENT", "DEBUG")
    log.debug("per-service on")
    obs.get_logger("gate").debug("other service still off")
    assert [r["msg"] for r in stderr_logs()] == ["per-service on"]
    monkeypatch.delenv("OC_LOG_LEVEL_PLACEMENT")
    monkeypatch.setenv("OC_DEBUG", "1")
    obs.get_logger("gate").debug("debug everywhere")
    assert [r["msg"] for r in stderr_logs()] == ["debug everywhere"]


def test_secrets_never_reach_the_sink(stderr_logs, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-envkeyenvkeyenvkey1234")
    log = obs.get_logger("openrouter")
    for s in SECRETS:
        log.info(f"auth {s}", api_key=s, nested={"h": f"Bearer {s}"})
    log.info("env value sk-or-v1-envkeyenvkeyenvkey1234 leaked in text")
    try:
        raise RuntimeError(f"boom {SECRETS[0]}")
    except RuntimeError:
        log.exception("failed")
    raw = json.dumps(stderr_logs())
    for s in [*SECRETS, "sk-or-v1-envkeyenvkeyenvkey1234"]:
        assert s not in raw


def test_file_sink_rotates_config_and_is_ndjson(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OC_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("OC_LOG_STDERR", "0")
    obs_log.configure(force=True)
    obs.get_logger("cost").info("a", n=1)
    obs.get_logger("cost").warning("b", n=2)
    lines = (tmp_path / "logs" / "oc.log").read_text().splitlines()
    assert [json.loads(x)["msg"] for x in lines] == ["a", "b"]
    assert capsys.readouterr().err == ""
    for h in logging.getLogger("oc").handlers:
        assert not isinstance(h, logging.StreamHandler) or hasattr(h, "baseFilename")


def test_logging_failure_never_raises(monkeypatch, tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    monkeypatch.setenv("OC_LOG_DIR", str(f / "sub"))  # cannot mkdir under a file
    before = obs.failures()
    obs_log.configure(force=True)
    obs.get_logger("x").info("still fine")
    assert obs.failures() > before
    monkeypatch.delenv("OC_LOG_DIR")
    obs_log.configure(force=True)

    class Bad:
        def __str__(self):
            raise RuntimeError("no str")

    obs.get_logger("x").info("unserializable", v=Bad())  # must not raise
