# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The format of the wiki's folder (`.llmw2/format`): written when the journal is born, a folder that says a newer format than the
version knows is not opened (nothing changed), one with no file is a 1.x folder and opens; and `status` tells, while entries wait in the
journal, what going back to a 1.x on the same wiki would do to them."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

from pathlib import Path

import pytest

from llmw2.bundle import journal as J
from llmw2.errors import BundleFormatError
from tests.wiki.test_remember_queue import make


def listing(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_journal_says_the_folders_format_when_it_is_born_and_the_wiki_opens_again(bundle: Path) -> None:
    w = make(bundle)
    try:
        assert not (bundle / J.FORMAT_FILE).exists()  # a wiki that never used the journal is still a 1.x folder
        w._stop_filer()
        w.remember("topic: a-1\none")
    finally:
        w.close()
    assert (bundle / J.FORMAT_FILE).read_text(encoding="utf-8") == f"{J.BUNDLE_FORMAT}\n"
    assert J.BUNDLE_FORMAT == 2 and J.bundle_format(bundle) == 2
    again = make(bundle)  # its own format opens
    again.close()


def test_a_folder_with_no_format_file_is_a_1x_folder_and_opens(bundle: Path) -> None:
    make(bundle).close()
    assert J.bundle_format(bundle) == 1
    make(bundle).close()


@pytest.mark.parametrize("said", [f"{J.BUNDLE_FORMAT + 1}\n", "two\n"])
def test_a_folder_in_a_newer_format_or_one_that_cannot_be_read_is_not_opened_and_nothing_changes(bundle: Path, said: str) -> None:
    w = make(bundle)
    w._stop_filer()
    w.remember("topic: a-1\none")
    w.close()
    (bundle / J.FORMAT_FILE).write_text(said, encoding="utf-8")
    before = listing(bundle)

    with pytest.raises(BundleFormatError) as e:
        make(bundle)

    assert "format" in str(e.value)
    assert listing(bundle) == before


def test_a_format_file_another_program_holds_for_a_moment_opens_and_one_held_all_along_is_a_system_error(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An antivirus or an indexer that holds `.llmw2/format` (Windows: the read fails with a permission error) is no reason to refuse
    the wiki: the read is tried again for a moment; held all along, the system's error is raised as it is, never `BundleFormatError`."""
    w = make(bundle)
    w._stop_filer()
    w.remember("topic: a-1\none")
    w.close()
    real = Path.read_bytes
    held = {"left": 2}

    def read_bytes(self: Path) -> bytes:
        if self.name == "format" and held["left"] > 0:
            held["left"] -= 1
            raise PermissionError(13, "the file is held by another program")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    assert J.bundle_format(bundle) == J.BUNDLE_FORMAT  # held for two tries, then read
    make(bundle).close()

    held["left"] = 10**6
    with pytest.raises(PermissionError):
        J.bundle_format(bundle)


def test_what_a_write_of_the_format_cut_by_a_crash_left_is_taken_away_and_check_does_not_list_it(bundle: Path) -> None:
    from llmw2.bundle.check import check

    make(bundle).close()
    left = bundle / ".llmw2" / "format.4242.tmp"  # a process died between its write and putting it in place: no format, no journal
    left.parent.mkdir(parents=True, exist_ok=True)
    left.write_text("2\n", encoding="utf-8")
    assert not [p for p in check(bundle) if "format." in p.path]
    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\none")  # the journal is born now
    finally:
        w.close()
    assert not left.exists() and J.bundle_format(bundle) == J.BUNDLE_FORMAT


def test_status_says_what_a_1x_would_do_to_the_entries_that_wait_and_nothing_once_they_are_filed(bundle: Path) -> None:
    w = make(bundle)
    try:
        w._stop_filer()
        w.remember("topic: a-1\none")
        said = " ".join(w.status()["warnings"])
        assert "1 entries wait in the journal" in said and "llmw2 1.x" in said and "queue empty" in said
        w._filer_stopped = False
        w.drain(30)
        assert not any("1.x" in x for x in w.status()["warnings"])
    finally:
        w.close()
