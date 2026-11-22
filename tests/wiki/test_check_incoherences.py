# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`check` sees what an interrupted or hand-made write leaves: a raw copy no note cites, a See-also entry on one side, a file left
by a cut write, a note no log line names; each with a severity (the command fails for errors only: see test_cli)."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

from pathlib import Path

import pytest

from llmw2.bundle.check import ERROR, WARNING, Problem, check
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder, Note


@pytest.fixture
def store(tmp_path: Path) -> WikiStore:
    s = WikiStore(tmp_path / "w", actor="test")
    s.init()
    return s


def filed(store: WikiStore, folder: Folder, title: str, text: str = "Some text.") -> Note:
    """A note the way a filing makes it: its raw copy, the note citing it, the log line."""
    raw = store.save_raw(text, title=title, resource=None)
    note = store.write_note(
        folder, title=title, summary=f"About {title}.", body="# Overview\n\nFacts.", tags=[], source={"resource": raw}
    )
    store.append_log("Created", "from the raw copy", note.rel, note.title)
    return note


def found(store: WikiStore) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {ERROR: [], WARNING: []}
    for p in check(store.root):
        out[p.severity].append(str(p))
    return out


def test_a_wiki_filed_by_the_code_has_no_problem(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    a, b = filed(store, folder, "Alpha"), filed(store, folder, "Bravo")
    store.link(a, b)
    assert check(store.root) == []


def test_a_problem_has_a_severity_that_is_an_error_unless_said(store: WikiStore) -> None:
    assert Problem("a.md", "x").severity == ERROR and str(Problem("a.md", "x", WARNING)) == "a.md: x"


def test_a_raw_copy_no_note_cites_is_an_error(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    filed(store, folder, "Alpha")
    orphan = store.save_raw("Saved, then the process died.", title="Orphan", resource=None)
    assert found(store)[ERROR] == [f"{orphan.lstrip('/')}: no note cites this raw copy"]


def test_a_raw_copy_only_an_archived_version_cites_is_not_an_orphan(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    note = filed(store, folder, "Alpha")
    old = store.save_raw("An older text.", title="Old", resource=None)
    fm = dict(note.frontmatter)
    fm["sources"] = [{"id": "s1", "resource": old}]
    archived = Note(note.path, note.rel, fm, note.body, note.folder)
    store.archive_note(archived)
    assert found(store)[ERROR] == []


def test_a_see_also_entry_on_one_side_only_is_a_warning(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    a, b = filed(store, folder, "Alpha"), filed(store, folder, "Bravo")
    store.link(a, b)
    both = a.path.read_text(encoding="utf-8")
    # take the entry out of Bravo only
    b.path.write_text(b.path.read_text(encoding="utf-8").split("# See also")[0].rstrip() + "\n", encoding="utf-8")
    out = found(store)
    assert out[WARNING] == ["sales/alpha.md: See also lists sales/bravo.md, which does not list it back"] and out[ERROR] == []
    assert "# See also" in both


def test_many_one_sided_see_also_entries_are_said_up_to_a_few_then_counted(store: WikiStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wiki written by an older version, whose See-also entries go one way: a few of them one by one, then one warning with the count
    (an upgraded wiki of 50,000 notes would otherwise give tens of thousands)."""
    from llmw2.bundle import check as C

    monkeypatch.setattr(C, "SEE_ALSO_SHOWN", 2)
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    hub = filed(store, folder, "Hub")
    others = [filed(store, folder, name) for name in ("Alpha", "Bravo", "Charlie", "Delta", "Echo")]
    for other in others:
        store.link(hub, other)
        other.path.write_text(other.path.read_text(encoding="utf-8").split("# See also")[0].rstrip() + "\n", encoding="utf-8")
    warnings = found(store)[WARNING]
    assert len([w for w in warnings if "which does not list it back" in w]) == 2
    assert "(wiki): 3 more See-also entries are on one side only" in " ".join(warnings)


def test_a_file_left_by_a_cut_write_is_an_error_wherever_it_is(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    filed(store, folder, "Alpha")
    (folder.path / ".alpha.md.4242.7.tmp").write_text("partial", encoding="utf-8")
    (store.root / ".llmw2").mkdir(exist_ok=True)
    (store.root / ".llmw2" / ".lock.1.2.tmp").write_text("x", encoding="utf-8")
    left = "left by an interrupted write"
    assert found(store)[ERROR] == [f".llmw2/.lock.1.2.tmp: {left}", f"sales/.alpha.md.4242.7.tmp: {left}"]


def test_a_note_no_log_line_names_is_a_warning(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    store.write_note(folder, title="By hand", summary="Typed in.", body="# Overview\n\nFacts.", tags=[], source={"resource": "/raw/x.md"})
    filed(store, folder, "Alpha")
    out = found(store)
    assert out[WARNING] == ["sales/by-hand.md: no log line names it"] and out[ERROR] == []


def test_a_moved_note_is_still_named_by_the_log(store: WikiStore) -> None:
    root = store.load()
    sales, _ = store.create_folder(root, "sales", "Sales.")
    ops, _ = store.create_folder(root, "ops", "Operations.")
    note = filed(store, sales, "Alpha")
    store.move_note(note.rel, ops.rel)  # the line of the move names the new path; the first line the old one: the file name is the same
    assert check(store.root) == []


def test_a_see_also_section_under_another_heading_level_counts(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    a, b = filed(store, folder, "Alpha"), filed(store, folder, "Bravo")
    a.path.write_text(a.path.read_text(encoding="utf-8").rstrip("\n") + f"\n\n## See also\n\n* [Bravo]({b.path.name})\n", encoding="utf-8")
    assert found(store)[WARNING] == ["sales/alpha.md: See also lists sales/bravo.md, which does not list it back"]
