"""Dev trace API: GET /api/runs/<id>/events, /events.jsonl, GET/POST /api/dev (U6).

Streams are written straight to disk (no model, no worker); the console only reads them."""

import json

import pytest

RID = "goal_0123abcd"
TOKEN_HDR = "X-CSRF-Token"


def write_stream(console, rows: int, *, torn: bool = False, run_status: str | None = "done") -> object:
    """A run directory with a raw events.ndjson (no lock file, like a finished run left on disk)."""
    d = console.base / "goals" / RID
    d.mkdir(parents=True)
    lines = []
    for i in range(1, rows + 1):
        last = i == rows and run_status
        typ, data = ("run", {"status": run_status}) if last else ("note", {"id": f"note_{i}", "role": "agent", "text": "x"})
        lines.append(json.dumps({"id": f"{RID}:{i}", "v": 1, "goal": RID, "seq": i, "ts": 100.0 + i, "type": typ, "data": data}))
    (d / "events.ndjson").write_text("\n".join(lines) + ("\n" if lines else "") + ('{"id":"' if torn else ""))
    return d


def api(console, method, path, body=None, csrf=True):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if csrf:
        headers[TOKEN_HDR] = console.csrf()
    return console.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)


def test_events_returns_a_page_after_the_cursor_and_the_last_seq(console):
    write_stream(console, 12)
    status, headers, body = console.get(f"/api/runs/{RID}/events")
    d = json.loads(body)
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert headers["Cache-Control"] == "no-store"
    assert [e["seq"] for e in d["events"]] == list(range(1, 13)) and d["last_seq"] == 12 and d["finished"] is True
    d = json.loads(console.get(f"/api/runs/{RID}/events?after=5")[2])
    assert [e["seq"] for e in d["events"]] == list(range(6, 13)) and d["last_seq"] == 12
    d = json.loads(console.get(f"/api/runs/{RID}/events?after=12")[2])
    assert d == {"events": [], "last_seq": 12, "finished": True}


def test_a_running_run_is_not_finished(console):
    write_stream(console, 4, run_status=None)
    d = json.loads(console.get(f"/api/runs/{RID}/events")[2])
    assert d["last_seq"] == 4 and d["finished"] is False


def test_more_than_500_events_page_correctly(console):
    write_stream(console, 1203)
    seen, after, pages, finished = [], 0, 0, []
    while True:
        d = json.loads(console.get(f"/api/runs/{RID}/events?after={after}")[2])
        pages += 1
        seen += [e["seq"] for e in d["events"]]
        finished.append(d["finished"])
        if not d["events"]:
            break
        assert len(d["events"]) <= 500
        after = d["last_seq"]
    assert seen == list(range(1, 1204)) and pages == 4
    assert finished == [False, False, True, True]  # finished only once the terminal event has been delivered


@pytest.mark.parametrize("after,code,first", [("-3", 200, 1), ("0", 200, 1), ("", 200, 1), ("99999999999999999999", 200, None),
                                              ("abc", 400, None), ("1.5", 400, None)])
def test_a_bad_cursor_is_clamped_or_refused_never_a_500(console, after, code, first):
    write_stream(console, 6)
    status, _, body = console.get(f"/api/runs/{RID}/events?after={after}")
    assert status == code
    d = json.loads(body)
    if code == 200:
        assert (d["events"][0]["seq"] if d["events"] else None) == first and d["last_seq"] <= 6
    else:
        assert "error" in d


def test_reading_a_stream_creates_no_file(console):
    write_stream(console, 8)
    before = console.snapshot()
    for path in (f"/api/runs/{RID}/events", f"/api/runs/{RID}/events?after=3", f"/api/runs/{RID}/events.jsonl", "/api/dev"):
        assert console.get(path)[0] == 200
    assert console.snapshot() == before
    assert not list((console.base / "goals").rglob("*.lock"))


def test_a_run_without_a_stream_or_a_missing_run_is_404(console):
    (console.base / "goals" / RID).mkdir(parents=True)
    for tail in ("events", "events.jsonl"):
        status, _, body = console.get(f"/api/runs/{RID}/{tail}")
        assert status == 404 and json.loads(body) == {"error": "dev trace off for this run"}
        status, _, body = console.get(f"/api/runs/goal_ffffffff/{tail}")
        assert status == 404 and json.loads(body) == {"error": "no such run"}
    assert not (console.base / "goals" / RID / "events.ndjson").exists()


def test_the_jsonl_export_is_every_complete_line_as_a_download(console):
    write_stream(console, 30, torn=True)
    status, headers, body = console.get(f"/api/runs/{RID}/events.jsonl")
    assert status == 200 and headers["Content-Type"] == "application/x-ndjson"
    assert headers["Content-Disposition"] == f'attachment; filename="{RID}-events.jsonl"'
    assert headers["Cache-Control"] == "no-store"
    lines = body.splitlines()
    assert [json.loads(ln)["seq"] for ln in lines] == list(range(1, 31))  # the torn trailing fragment is not exported
    assert body.endswith("\n")


def test_dev_setting_get_has_no_side_effects_and_reads_off_by_default(console, monkeypatch):
    monkeypatch.delenv("OC_DEV_TRACE", raising=False)
    before = console.snapshot()
    status, headers, body = console.get("/api/dev")
    assert status == 200 and json.loads(body) == {"trace": False, "env": False, "active": False}
    assert headers["Cache-Control"] == "no-store"
    assert console.snapshot() == before and not (console.base / "dev.json").exists()


def test_dev_setting_post_needs_csrf_then_flips_the_file(console, monkeypatch):
    monkeypatch.delenv("OC_DEV_TRACE", raising=False)
    status, _, body = api(console, "POST", "/api/dev", {"trace": True}, csrf=False)
    assert status == 403 and "CSRF" in body and not (console.base / "dev.json").exists()
    status, _, body = api(console, "POST", "/api/dev", {"trace": True})
    assert status == 200 and json.loads(body) == {"trace": True, "env": False, "active": True}
    f = console.base / "dev.json"
    assert json.loads(f.read_text()) == {"trace": True} and (f.stat().st_mode & 0o777) == 0o600
    assert json.loads(console.get("/api/dev")[2])["trace"] is True
    assert api(console, "POST", "/api/dev", {"trace": False})[0] == 200
    assert json.loads(f.read_text()) == {"trace": False}


@pytest.mark.parametrize("bad", ["yes", "true", 1, 0, None, [], {}])
def test_dev_setting_post_takes_only_a_strict_boolean(console, bad):
    f = console.base / "dev.json"
    f.write_text('{"trace": true}')
    status, _, body = api(console, "POST", "/api/dev", {"trace": bad})
    assert status == 400 and "error" in json.loads(body)
    assert f.read_text() == '{"trace": true}'  # unchanged
    assert api(console, "POST", "/api/dev", {})[0] == 400


def test_a_corrupt_dev_json_reads_as_off(console, monkeypatch):
    monkeypatch.delenv("OC_DEV_TRACE", raising=False)
    (console.base / "dev.json").write_text("{oops")
    assert json.loads(console.get("/api/dev")[2]) == {"trace": False, "env": False, "active": False}


def test_the_env_gate_shows_in_the_setting(console, monkeypatch):
    monkeypatch.setenv("OC_DEV_TRACE", "1")
    assert json.loads(console.get("/api/dev")[2]) == {"trace": False, "env": True, "active": True}
