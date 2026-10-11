import re

from services.opsview import views

NOW = 1_791_700_000.0


def test_relative_times_within_a_week_then_a_date():
    assert views.rel_time(NOW - 4, NOW) == "just now"
    assert views.rel_time(NOW - 42, NOW) == "42s ago"
    assert views.rel_time(NOW - 125, NOW) == "2m ago"
    assert views.rel_time(NOW - 7200, NOW) == "2h ago"
    assert views.rel_time(NOW - 86400 * 3, NOW) == "3d ago"
    assert re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", views.rel_time(NOW - 86400 * 9, NOW))


def test_time_tag_has_absolute_time_on_hover_not_raw_epoch():
    t = views.time_tag(1791704867.1325488, 1791704867.1325488 + 3600)
    assert "1h ago" in t and 'title="2026-10-' in t and "UTC" in t and 'datetime="2026-10-' in t
    assert "1791704867" not in t


def test_durations_and_money():
    assert views.duration(252) == "4m 12s" and views.duration(7) == "7s"
    assert views.money(0) == "$0.00" and views.money(0.0187) == "$0.02"
    assert views.money(0.000004) == "$0.000004" and views.money(0.0021) == "$0.0021"


def test_status_is_glyph_plus_word_never_color_alone():
    for word in ("passed", "failed", "pending", "blocked", "sent back", "not configured"):
        s = views.status(word)
        assert "<svg" in s and f"<span>{word}</span>" in s


def test_audit_page_uses_relative_times(console):
    _, _, html = console.get("/audit")
    assert "ago</time>" in html and not re.search(r">\s*17\d{8}\.\d+", html)


def test_empty_states_say_what_will_appear(tmp_path, monkeypatch, console):
    monkeypatch.setenv("OC_APPROVALS", str(tmp_path / "x.ndjson"))
    monkeypatch.setenv("OC_RUNS_DIR", str(tmp_path / "runs"))
    _, _, html = console.get("/approvals")
    assert "Nothing waiting on you" in html and "what leaves, where it goes" in html
    _, _, html = console.get("/runs")
    assert "No runs yet" in html and "New matrix run" in html


def test_log_viewer_embeds_lines_for_windowing_and_a_noscript_tail(console):
    _, _, html = console.get("/runs")
    big = re.findall(r'href="(/runs/run_[0-9a-f]+)"', html)
    found = False
    for href in dict.fromkeys(big):
        _, _, page = console.get(href)
        if 'data-lines="12001"' in page:
            found = True
            assert 'id="log-data"' in page and "<noscript>" in page and 'role="log"' in page
            assert page.count('class="ln"') == 0  # lines are windowed client-side, not 12k DOM nodes
    assert found
