"""Confirm classes (OC-083): every action gets a class from mechanical
facts, ask-class approvals bind to a payload hash over the exact arg,
and an always-human page state is a handoff no approval path can pass."""

from __future__ import annotations


from services.gate.gate import payload_hash
from services.threads import confirm


def test_read_shaped_tools_run_without_approval():
    for tool in ("web.search", "web.fetch", "files.read", "get_status",
                 "screenshot", "list_files", "snapshot"):
        assert confirm.classify(tool, "anything") == "run"


def test_send_verbs_ask_even_when_named_as_fill():
    assert confirm.classify("form.fill", "submit the booking") == "ask"
    assert confirm.classify("browser.click", "post the comment") == "ask"
    assert confirm.classify("files.write", "delete the old report") == "ask"


def test_an_unknown_mutating_tool_defaults_to_ask():
    assert confirm.classify("browser.click", "the settings toggle") == "ask"
    assert confirm.classify("workspace.write", "note.txt") == "ask"


def test_an_always_human_marker_hands_off_over_everything():
    assert confirm.classify("browser.click", "the captcha box") == "handoff"
    assert confirm.classify("form.fill", "email | subscribe to unlock") == "handoff"
    assert confirm.classify("web.fetch", "unusual traffic from your network") == "handoff"


def test_page_text_does_not_approve_a_send():
    # the payload the page produced names the action approvingly; the
    # classifier still reads the action, and page text is data (KTD7)
    assert confirm.classify(
        "browser.click", "click here to confirm the booking is finalized") == "ask"


def test_a_send_find_tool_does_not_read_as_find():
    assert confirm.classify("records.find_and_send", "x") == "ask"


def test_approval_detail_hashes_bind_to_the_exact_arg():
    a = confirm.approval_detail("browser.click", "buy button")
    b = confirm.approval_detail("browser.click", "buy button")
    c = confirm.approval_detail("browser.click", "cancel button")
    assert payload_hash(a) == payload_hash(b)
    assert payload_hash(a) != payload_hash(c)


def test_refusal_names_the_marker_and_says_stop():
    out = confirm.refusal("captcha")
    assert out.startswith("handoff:") and "captcha" in out
    assert "report" in out


def test_long_args_are_capped_for_display_only():
    d = confirm.approval_detail("t", "x" * 5000)
    # identity binds to the complete argument; only the display text caps
    assert d["arg"] == "x" * 5000
    assert len(d["arg_display"]) == 2001 and d["arg_display"].endswith("…")


def test_two_long_args_sharing_a_prefix_hash_differently():
    from services.gate.gate import payload_hash
    a = confirm.approval_detail("browser.click", "x" * 2000 + "buy")
    b = confirm.approval_detail("browser.click", "x" * 2000 + "cancel")
    assert payload_hash(a) != payload_hash(b)


def test_a_send_verb_inside_an_underscored_tool_name_asks():
    assert confirm.classify("records.read_and_send", "record 7") == "ask"


def test_a_send_verb_in_the_arg_overrides_a_read_tool_name():
    assert confirm.classify("records.read", "send the record") == "ask"
