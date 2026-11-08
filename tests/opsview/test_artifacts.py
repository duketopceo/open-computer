"""Run artifacts: derived from literal run results; safe names, caps, export. No model, no sandbox."""

import io
import json
import zipfile

import pytest

from services.opsview import artifacts, goalruns


def run_with(*results, state="DONE"):
    return {"run": "goal_00000001", "subtasks": [{"subtask": f"t{i}", "state": state, "result": r} for i, r in enumerate(results, 1)]}


def test_step_results_and_named_blocks_become_files():
    r = run_with("plain answer", "intro\n<<<ARTIFACT name=report.md\n# Title\nbody\nARTIFACT>>>\noutro")
    names = [a["name"] for a in artifacts.listing(r)]
    assert names == ["step-t1.md", "report.md", "step-t2.md"]
    assert artifacts.get(r, "report.md")["text"] == "# Title\nbody"
    assert "[file: report.md]" in artifacts.get(r, "step-t2.md")["text"]  # the step file points at it, no duplicate body
    assert all("text" not in a for a in artifacts.listing(r))


@pytest.mark.parametrize("bad", ["../etc/passwd", "/abs/x.md", ".hidden", "a b.md", "x" * 90, "..", "a\\..\\b.md"])
def test_unsafe_names_are_never_written_as_given(bad):
    got = artifacts.safe_name(bad)
    assert got is None or ("/" not in got and not got.startswith(".") and ".." not in got)
    r = run_with(f"<<<ARTIFACT name={bad}\nx\nARTIFACT>>>")
    assert all("/" not in a["name"] and not a["name"].startswith(".") for a in artifacts.listing(r))


def test_large_file_is_listed_as_refused_with_a_reason():
    big = "x" * (artifacts.MAX_BYTES + 1)
    r = run_with(f"<<<ARTIFACT name=big.csv\n{big}\nARTIFACT>>>")
    entry = next(a for a in artifacts.listing(r) if a["name"] == "big.csv")
    assert "refused" in entry and "too large" in entry["refused"]
    assert artifacts.get(r, "big.csv") is None


def test_unfinished_and_empty_steps_have_no_files():
    assert artifacts.listing(run_with("text", state="RUNNING")) == []
    assert artifacts.listing(run_with("   ")) == []


def test_repeated_names_keep_both_and_count_is_capped():
    r = run_with("<<<ARTIFACT name=a.md\none\nARTIFACT>>>", "<<<ARTIFACT name=a.md\ntwo\nARTIFACT>>>")
    assert {"a.md", "a-1.md"} <= {a["name"] for a in artifacts.listing(r)}
    many = run_with(*[f"r{i}" for i in range(80)])
    assert len(artifacts.listing(many)) == artifacts.MAX_FILES


def test_html_is_served_as_text_never_html():
    assert artifacts.mime_of("page.html") == "text/plain" and artifacts.mime_of("d.csv") == "text/csv"


def test_export_contains_exactly_the_listed_files():
    big = "x" * (artifacts.MAX_BYTES + 1)
    r = run_with("one", f"<<<ARTIFACT name=big.md\n{big}\nARTIFACT>>>")
    z = zipfile.ZipFile(io.BytesIO(artifacts.export_zip(r)))
    listed = {a["name"] for a in artifacts.listing(r) if "refused" not in a}
    assert set(z.namelist()) == listed == {"step-t1.md", "step-t2.md"}
    assert z.read("step-t1.md") == b"one"


def test_artifacts_survive_a_restart_and_serve_over_http(console, tmp_path, monkeypatch):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path / "goals"))
    monkeypatch.setattr(goalruns, "_start_drive", lambda orch, rid, wfn: goalruns._drive(orch, rid, wfn, sleep=lambda s: None))
    plan = json.dumps([{"id": "a", "title": "write", "prompt": "p"}])
    rid = goalruns.submit("g", lambda p: plan, lambda t: "hi\n<<<ARTIFACT name=notes.txt\nhello\nARTIFACT>>>")
    status, _, body = console.get(f"/api/runs/{rid}/artifacts")
    assert status == 200 and [a["name"] for a in json.loads(body)["artifacts"]] == ["notes.txt", "step-a.md"]
    status, headers, body = console.get(f"/api/runs/{rid}/artifacts/notes.txt?download=1")
    assert status == 200 and body == "hello"
    assert headers["Content-Disposition"] == 'attachment; filename="notes.txt"' and headers["X-Content-Type-Options"] == "nosniff"
    assert console.get(f"/api/runs/{rid}/artifacts/nope.txt")[0] == 404
    assert console.get("/api/runs/goal_ffffffff/artifacts")[0] == 404
    assert console.get(f"/api/runs/{rid}/artifacts.zip")[0] == 200
