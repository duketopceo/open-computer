"""Listing an archive's entries is bounded, so a crafted archive cannot make the console allocate without limit.

Nothing in this tree extracts an entry; the exposure is the central directory. `zipfile.ZipFile.__init__` reads
the whole directory into `self.filelist` at open, so a per-entry check applied to `infolist()` runs after the
allocation it is meant to prevent. The entry count is therefore read from the end-of-central-directory record
before the archive is opened. These tests fix each bound at its worst case, not on average.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from services.workspace import caps


def make_zip(path: Path, n=1, name_len=8, payload=b"x") -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for i in range(n):
            z.writestr(f"{(f'e{i}').ljust(name_len, 'a')}", payload)
    return path


def test_a_legitimate_archive_lists_normally(tmp_path):
    p = make_zip(tmp_path / "a.zip", n=3)
    rows = caps.entries(p)
    assert len(rows) == 3
    assert all(set(r) == {"path", "size", "dir"} for r in rows)
    assert rows[0]["dir"] is False


def test_an_archive_over_the_input_cap_is_refused_before_it_is_opened(tmp_path):
    p = tmp_path / "big.zip"
    p.write_bytes(b"\0" * (caps.MAX_INPUT + 1))
    # Not a valid zip at all: reaching zipfile would raise BadZipFile, so a bound error proves we refused first.
    with pytest.raises(caps.ArchiveTooLarge) as exc:
        caps.entries(p)
    assert "over the" in str(exc.value) and str(caps.MAX_INPUT) in str(exc.value)


def test_an_archive_with_too_many_entries_is_refused_naming_the_cap(tmp_path):
    p = make_zip(tmp_path / "many.zip", n=caps.MAX_ENTRIES + 1, name_len=2)
    with pytest.raises(caps.ArchiveTooLarge) as exc:
        caps.entries(p)
    assert str(caps.MAX_ENTRIES) in str(exc.value)


def test_the_entry_count_is_read_from_the_header_before_zipfile_opens(tmp_path, monkeypatch):
    """The point of the header read: refuse a huge directory without paying for `ZipFile()` to build it.

    Patching `zipfile.ZipFile` to fail makes this a proof about ordering rather than timing. Without the header
    read the walk would still reach `ZipFile` and raise this sentinel instead of a bound error.
    """
    p = make_zip(tmp_path / "many.zip", n=caps.MAX_ENTRIES + 1, name_len=2)
    assert caps.count_entries(p) == caps.MAX_ENTRIES + 1

    def explode(*a, **k):
        raise AssertionError("ZipFile was opened for an archive the header count already refused")

    monkeypatch.setattr(caps.zipfile, "ZipFile", explode)
    with pytest.raises(caps.ArchiveTooLarge) as exc:
        caps.entries(p)
    assert str(caps.MAX_ENTRIES) in str(exc.value)


def test_a_small_archive_under_a_huge_number_of_entries_is_refused_and_allocates_nothing(tmp_path):
    """The measured attack: >100,000 empty entries fit in 10 MB, and `ZipFile()` allocates ~60 MB to open them."""
    p = tmp_path / "creep.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_STORED) as z:
        for i in range(110_000):
            z.writestr(f"f{i}", b"")
    assert p.stat().st_size <= caps.MAX_INPUT, "the fixture must be under the size bound, or the wrong bound fires"
    with pytest.raises(caps.ArchiveTooLarge) as exc:
        caps.entries(p)
    assert f"{caps.MAX_ENTRIES}-entry limit" in str(exc.value)


def test_an_oversized_listing_is_refused_mid_iteration_not_after_building_it(tmp_path):
    """The names alone blow the listing cap while the archive stays small, so the count bound does not save us."""
    per_name = 2000
    n = (caps.MAX_LISTING // per_name) + 8
    assert n < caps.MAX_ENTRIES, "this case must fail on the listing bound, not the entry-count bound"
    p = make_zip(tmp_path / "names.zip", n=n, name_len=per_name)
    assert p.stat().st_size <= caps.MAX_INPUT, "keep the input small so only the listing bound can trigger"
    with pytest.raises(caps.ArchiveTooLarge) as exc:
        caps.entries(p)
    assert str(caps.MAX_LISTING) in str(exc.value)


def test_exactly_at_the_entry_cap_still_lists(tmp_path):
    p = make_zip(tmp_path / "edge.zip", n=caps.MAX_ENTRIES, name_len=2)
    assert len(caps.entries(p)) == caps.MAX_ENTRIES


def test_a_non_zip_file_is_a_named_error_not_a_traceback(tmp_path):
    p = tmp_path / "not.zip"
    p.write_bytes(b"this is not a zip file at all")
    with pytest.raises(caps.ArchiveTooLarge) as exc:
        caps.entries(p)
    # The route prefixes "This archive can't be listed", so the bound's own text must not repeat that sentence.
    assert "readable zip" in str(exc.value)
    assert "can't be listed" not in str(exc.value)


def test_a_truncated_zip_is_a_named_error(tmp_path):
    good = make_zip(tmp_path / "good.zip", n=2)
    truncated = tmp_path / "trunc.zip"
    truncated.write_bytes(good.read_bytes()[:40])
    with pytest.raises(caps.ArchiveTooLarge):
        caps.entries(truncated)


def test_the_route_reports_the_bound_rather_than_a_500(tmp_path, monkeypatch):
    """The console handler turns the bound into a 422 naming the limit, not the generic bad-zip sentence.

    A crafted archive that is also a valid zip would otherwise list; this one is oversized and valid, so the only
    reason it can fail is the bound. Asserting the limit appears distinguishes the cap from the bad-zip branch.
    """
    from services.workspace import routes

    big = tmp_path / "big.zip"
    with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED) as z:
        for i in range(3):
            z.writestr(f"f{i}", b"\0" * (caps.MAX_INPUT // 3))
    assert big.stat().st_size > caps.MAX_INPUT, "the fixture must exceed the bound"
    monkeypatch.setattr(routes, "_artifact", lambda *a, **k: ({"kind": "archive", "name": "big.zip"}, big))
    monkeypatch.setattr(routes, "ws", lambda: object())
    sent = {}

    class H:
        def _json(self, code, payload):
            sent["code"], sent["payload"] = code, payload

    routes.g_listing(H(), {}, "goal_00000000", "big.zip")
    assert sent["code"] == 422
    assert str(caps.MAX_INPUT) in sent["payload"]["error"]
    assert "Download it instead" in sent["payload"]["error"]
    assert sent["payload"]["error"].count("can't be listed") == 1, "the sentence is prefixed once, not twice"


def test_no_extractor_is_present_in_the_module():
    """A decompression bound would guard a code path that does not exist. This asserts the module stays that way."""
    src = Path(caps.__file__).read_text()
    assert "extractall" not in src and ".extract(" not in src


def test_the_module_says_it_bounds_the_listing_only():
    assert "listing" in (caps.__doc__ or "").lower()
    assert "not a decompression guard" in (caps.__doc__ or "")
