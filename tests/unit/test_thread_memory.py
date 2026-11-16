"""The thread's own memory file (OC-077): what the model wrote about the
work survives compaction whole — the writer and the summarizer are
different actors, so the file never passes through jev-compact. It is
spliced back as a user-role message immediately before the live turn."""

from __future__ import annotations

import pathlib

from services import compact as c


def test_write_then_read_round_trips(tmp_path):
    assert c.write_memory("th1", "goal: the lidar fix\nrule: never touch prod", tmp_path)
    assert c.read_memory("th1", tmp_path) == "goal: the lidar fix\nrule: never touch prod"


def test_absent_file_reads_none(tmp_path):
    assert c.read_memory("ghost", tmp_path) is None


def test_empty_write_is_refused_and_clears_nothing(tmp_path):
    assert not c.write_memory("th1", "   \n  ", tmp_path)
    c.write_memory("th1", "goal: x", tmp_path)
    assert c.read_memory("th1", tmp_path) == "goal: x"


def test_overlong_text_is_capped_not_failed(tmp_path):
    body = "x" * (c.MEMORY_MAX_CHARS + 500)
    assert c.write_memory("th1", body, tmp_path)
    assert len(c.read_memory("th1", tmp_path)) == c.MEMORY_MAX_CHARS


def test_write_replaces_whole_no_merging(tmp_path):
    c.write_memory("th1", "goal: a\nrule: b", tmp_path)
    c.write_memory("th1", "goal: c", tmp_path)
    assert c.read_memory("th1", tmp_path) == "goal: c"


def test_memory_message_splices_as_user_role(tmp_path):
    assert c.memory_message("ghost", tmp_path) is None
    c.write_memory("th1", "goal: a", tmp_path)
    msg = c.memory_message("th1", tmp_path)
    assert msg == {"role": "user",
                   "content": f"{c.MEMORY_HEADER}\ngoal: a"}


def test_memory_file_lives_outside_the_workspace_mount(tmp_path, monkeypatch):
    # the workspace the sandbox mounts is the thread's own directory; the
    # memory file sits in the compact store (`<threads>/_compact/<tid>/`)
    # beside the span store, which the sandbox never sees
    monkeypatch.setenv("OC_THREADS_DIR", str(tmp_path / "threads"))
    p = c.memory_path("th1")
    assert "_compact" in p.parts
    assert p.name == "memory.md"


def test_fit_leaves_the_memory_file_out_of_the_compacted_block(tmp_path):
    """The property that makes the mechanism work: after fit(), the
    compacted system block never contains the memory text, because the
    memory message is spliced after fit() by the caller (threads.py)."""
    from services.orchestrator import chat

    fill = "filler sentence about the regolith survey. " * 20
    msgs = [{"role": "system", "content": chat.SYSTEM}]
    for i in range(12):
        msgs.append({"role": "user", "content": f"q{i}: {fill}"})
        msgs.append({"role": "assistant", "content": f"a{i}: {fill}"})
    out = c.Compactor(c.CompactConfig(budget=300, tail=2),
                      lambda m, ms: '{"rel":1,"load":0.1}').fit("th1", msgs)
    assert out.compacted
    sent = "\n".join(str(m["content"]) for m in out.messages)
    assert "goal: the lidar fix" not in sent  # the model's memory was never an input


def test_a_failed_write_preserves_the_previous_file(tmp_path, monkeypatch):
    """A disk-full mid-write must not truncate or half-replace the
    existing memory: the write is atomic (complete temp file, replace)."""
    c.write_memory("th1", "goal: the good version", tmp_path)
    before = c.read_memory("th1", tmp_path)

    real_write_text = pathlib.Path.write_text

    def exploding(self, data, *a, **kw):
        if self.suffix == ".tmp":
            raise OSError(28, "No space left on device")
        return real_write_text(self, data, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", exploding)
    assert c.write_memory("th1", "goal: the new version", tmp_path) is False
    monkeypatch.undo()
    assert c.read_memory("th1", tmp_path) == before
    # no temp litter left behind
    assert not list((tmp_path / "_compact" / "th1").glob("*.tmp"))
