"""Stage 2: the console speaks the shared log dialect. Every request gets an id (echoed in the response and on
every line it produces) and one access-log line: method, route template, status, latency, pseudonymous actor.
Never the query string, cookies, headers, bodies or an email address."""

import json
import re
import time

import pytest

from oc.obs import log as obs_log


@pytest.fixture
def lines(capfd):
    """Log rows since the last call. The server writes the access line just after the response, so a reader that
    expects N request lines polls briefly for them instead of racing the handler thread."""
    obs_log.configure(force=True)

    def read(expect: int = 0):
        rows: list[dict] = []
        deadline = time.monotonic() + 3
        while True:
            err = capfd.readouterr().err
            rows += [json.loads(ln) for ln in err.splitlines() if ln.startswith("{")]
            if len(access(rows)) >= expect or time.monotonic() > deadline:
                return rows
            time.sleep(0.02)

    return read


def access(rows):
    return [r for r in rows if r["service"] == "opsview" and r["msg"] == "request"]


def test_request_id_is_generated_echoed_and_on_the_access_line(console, lines):
    status, headers, _ = console.get("/overview")
    rid = {k.lower(): v for k, v in headers.items()}["x-request-id"]
    assert status == 200 and re.fullmatch(r"[0-9a-f]{16}", rid)
    (row,) = access(lines(1))
    assert row["request_id"] == rid and row["method"] == "GET" and row["status"] == 200
    assert row["route"] == "g_overview" and isinstance(row["ms"], float) and row["level"] == "INFO"


def test_well_formed_inbound_request_id_is_kept_malformed_is_replaced(console, lines):
    _, h1, _ = console.get("/overview", headers={"X-Request-Id": "proxy-req-0001"})
    _, h2, _ = console.get("/overview", headers={"X-Request-Id": "bad id\twith spaces"})
    got = [{k.lower(): v for k, v in h.items()}["x-request-id"] for h in (h1, h2)]
    assert got[0] == "proxy-req-0001" and re.fullmatch(r"[0-9a-f]{16}", got[1])
    assert [r["request_id"] for r in access(lines(2))] == got


def test_route_is_a_template_not_the_raw_path_and_query_never_logged(console, lines):
    console.get("/runs?secret_param=sk-or-v1-0123456789abcdef0123456789abcdef&q=private-words")
    console.get("/no/such/page/ap_12345678")
    rows = access(lines(2))
    assert [r["route"] for r in rows] == ["spa/runs", "other"]
    raw = json.dumps(rows)
    assert "private-words" not in raw and "sk-or-v1" not in raw and "ap_12345678" not in raw


def test_unauthenticated_and_csrf_and_origin_rejections_are_warnings_with_a_reason(console, lines):
    console.get("/overview", cookie=False, headers={"Host": "remote.test"})
    console.post("/runs", {"confirm": "1"}, csrf=False)
    console.post("/runs", {"confirm": "1"}, headers={"Origin": "http://evil.example"})
    rows = [r for r in access(lines(4)) if r["method"] == "POST" or r["status"] == 302]  # (csrf() does a GET)
    assert [(r["status"], r["reason"], r["level"]) for r in rows] == [
        (302, "unauthenticated", "INFO"),
        (403, "csrf", "WARNING"),
        (403, "cross_origin", "WARNING"),
    ]


def test_actor_is_a_pseudonym_never_the_email(console, lines):
    console.get("/overview")
    (row,) = access(lines(1))
    assert re.fullmatch(r"u_[0-9a-f]{8}", row["actor"]) and "luke@test" not in json.dumps(row)


def test_other_log_lines_in_the_request_carry_the_request_id(console, lines):
    console.get("/overview")
    rows = [r for r in lines(1) if r["service"] == "opsview"]
    assert rows and all("request_id" in r for r in rows)
