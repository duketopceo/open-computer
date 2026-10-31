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


def test_log_viewer_embeds_lines_for_windowing_and_a_noscript_tail(console):
    from services.opsview import runs as runs_mod

    hrefs = [f"/runs/{r['id']}" for r in runs_mod.list_runs()]
    # list page is the React shell now; run detail stays server-rendered
    found = False
    for href in dict.fromkeys(hrefs):
        _, _, page = console.get(href)
        if 'data-lines="12001"' in page:
            found = True
            assert 'id="log-data"' in page and "<noscript>" in page and 'role="log"' in page
            assert page.count('class="ln"') == 0  # lines are windowed client-side, not 12k DOM nodes
    assert found
