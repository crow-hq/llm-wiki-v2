# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from spec-id-audit-health-2026-10-10.md: the stable note id, the routing audit, the taxonomy health."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig, cli
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.tree import LOG, Note
from llmw2.errors import ConfigError, InputError, ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
SUMMARIZE, ROUTE_FALLBACK, NAME_FOLDER = "librarian/summarize", "librarian/route_fallback", "librarian/name_folder"
HEX12 = re.compile(r"[0-9a-f]{12}")
SOURCE = {"resource": "/raw/seed.md", "title": "Seed"}


# -- helpers ----------------------------------------------------------------------


@pytest.fixture
def crow_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    """The shared CROW wiki with strict_new_folders off: these tests pin 0.3.0's routing arithmetic."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", strict_new_folders=False), llm=llm, classifier=classifier)
    wiki.init()
    return wiki


def add_folder(wiki: Wiki, parent: str, name: str, description: str) -> None:
    wiki.store.create_folder(wiki.store.load().find(parent), name, description)


def add_note(wiki: Wiki, folder: str, title: str, body: str = "# Overview\n\nSeeded.") -> Note:
    return wiki.store.write_note(wiki.store.load().find(folder), title=title, summary=f"About {title}.", body=body, tags=[], source=SOURCE)


def doc(wiki: Wiki, rel: str) -> OKFDocument:
    return OKFDocument.parse((wiki.cfg.bundle / rel.lstrip("/")).read_text(encoding="utf-8"))


def note_id(wiki: Wiki, rel: str) -> Any:
    return doc(wiki, rel).frontmatter.get("id")


def update(wiki: Wiki, note: Note, body: str = "# Overview\n\nChanged.") -> Note:
    return wiki.store.update_note(note, title=note.title, summary="Changed.", body=body, tags=[], source=SOURCE)


def legacy_note(wiki: Wiki, folder: str, title: str) -> Note:
    """A note of an older wiki: written, then its id key dropped from the file."""
    note = add_note(wiki, folder, title)
    path = wiki.cfg.bundle / note.rel
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"(?m)^id:.*\n", "", text), encoding="utf-8")
    assert "id" not in doc(wiki, note.rel).frontmatter
    return wiki.store.load().find(f"/{folder.strip('/')}").note(note.slug)  # type: ignore[union-attr, return-value]


# -- (a) the stable id ---------------------------------------------------------------


def test_a_new_note_gets_a_12_hex_id(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")

    note = add_note(classic_wiki, "/alpha", "First")

    on_disk = note_id(classic_wiki, note.rel)
    assert isinstance(on_disk, str) and HEX12.fullmatch(on_disk)
    assert note.frontmatter["id"] == on_disk


def test_every_new_note_gets_its_own_id(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    ids = {note_id(classic_wiki, add_note(classic_wiki, "/alpha", f"Note {n}").rel) for n in range(20)}

    assert len(ids) == 20


def test_a_note_filed_by_the_librarian_has_an_id(classic_wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(SUMMARIZE, {"title": "Revenue", "summary": "When.", "body": "On delivery."})
    llm.add("librarian/route", {"action": "new"}).add(NAME_FOLDER, {"name": "finance", "description": "Money."})

    result = classic_wiki.ingest("Revenue is booked on delivery.")

    assert HEX12.fullmatch(str(note_id(classic_wiki, result.note)))


def test_update_keeps_the_id(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    note = add_note(classic_wiki, "/alpha", "First")
    before = note_id(classic_wiki, note.rel)

    update(classic_wiki, note)

    assert note_id(classic_wiki, note.rel) == before
    assert doc(classic_wiki, note.rel).frontmatter["description"] == "Changed."


def test_move_keeps_the_id(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    add_folder(classic_wiki, "/", "beta", "Beta things.")
    note = add_note(classic_wiki, "/alpha", "First")
    before = note_id(classic_wiki, note.rel)

    moved = classic_wiki.move_note(note.rel, "/beta")

    assert moved.rel == "beta/first.md"
    assert note_id(classic_wiki, moved.rel) == before


def test_the_archived_copy_keeps_the_id(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    note = add_note(classic_wiki, "/alpha", "First")
    before = note_id(classic_wiki, note.rel)

    archived = classic_wiki.store.archive_note(note)
    update(classic_wiki, note)

    assert note_id(classic_wiki, archived) == before
    assert note_id(classic_wiki, note.rel) == before


def test_a_note_without_an_id_stays_valid_and_gets_none(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    add_folder(classic_wiki, "/", "beta", "Beta things.")
    note = legacy_note(classic_wiki, "/alpha", "Old")

    assert classic_wiki.check() == []
    update(classic_wiki, note)
    assert "id" not in doc(classic_wiki, note.rel).frontmatter
    moved = classic_wiki.move_note(note.rel, "/beta")
    assert "id" not in doc(classic_wiki, moved.rel).frontmatter
    assert classic_wiki.check() == []


def test_find_note_returns_the_note_with_that_id(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    add_folder(classic_wiki, "/", "beta", "Beta things.")
    first, second = add_note(classic_wiki, "/alpha", "First"), add_note(classic_wiki, "/beta", "Second")

    found = classic_wiki.find_note(note_id(classic_wiki, second.rel))

    assert found is not None and (found.rel, found.title) == (second.rel, "Second")
    assert classic_wiki.find_note(note_id(classic_wiki, first.rel)).rel == first.rel  # type: ignore[union-attr]


def test_find_note_follows_the_note_when_it_moves(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    add_folder(classic_wiki, "/", "beta", "Beta things.")
    note = add_note(classic_wiki, "/alpha", "First")
    note_id_ = note_id(classic_wiki, note.rel)

    classic_wiki.move_note(note.rel, "/beta")

    found = classic_wiki.find_note(note_id_)
    assert found is not None and found.rel == "beta/first.md"


def test_find_note_of_an_unknown_id_is_none(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    add_note(classic_wiki, "/alpha", "First")

    assert classic_wiki.find_note("000000000000") is None


def test_find_note_does_not_look_in_the_archive(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    note = add_note(classic_wiki, "/alpha", "First")
    kept = note_id(classic_wiki, note.rel)
    classic_wiki.store.archive_note(note)
    classic_wiki.delete_note(note.rel)

    assert classic_wiki.find_note(kept) is None


def test_check_is_clean_when_every_id_is_different_or_missing(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    add_note(classic_wiki, "/alpha", "First")
    add_note(classic_wiki, "/alpha", "Second")
    legacy_note(classic_wiki, "/alpha", "Third")
    legacy_note(classic_wiki, "/alpha", "Fourth")

    assert classic_wiki.check() == []


def test_check_reports_two_notes_sharing_an_id_and_nothing_else(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    add_folder(classic_wiki, "/", "beta", "Beta things.")
    first, second = add_note(classic_wiki, "/alpha", "First"), add_note(classic_wiki, "/beta", "Second")
    shared = note_id(classic_wiki, first.rel)
    path = classic_wiki.cfg.bundle / second.rel
    path.write_text(path.read_text(encoding="utf-8").replace(note_id(classic_wiki, second.rel), shared), encoding="utf-8")

    problems = classic_wiki.check()

    assert problems and all("duplicate id" in p.message.lower() for p in problems)


def test_the_archived_copy_is_not_a_duplicate(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    note = add_note(classic_wiki, "/alpha", "First")
    classic_wiki.store.archive_note(note)

    assert classic_wiki.check() == []


def test_the_id_is_in_no_link_index_or_see_also_entry(classic_wiki: Wiki) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    first, second = add_note(classic_wiki, "/alpha", "First"), add_note(classic_wiki, "/alpha", "Second")
    classic_wiki.store.link(first, second)
    ids = [note_id(classic_wiki, first.rel), note_id(classic_wiki, second.rel)]

    for rel in ("index.md", "alpha/index.md"):
        text = (classic_wiki.cfg.bundle / rel).read_text(encoding="utf-8")
        assert not any(i in text for i in ids)
    for note in (first, second):
        assert not any(i in doc(classic_wiki, note.rel).body for i in ids)  # body = text and See also


# -- (b) the routing audit ------------------------------------------------------------

# alpha holds the note and beta is empty: the route of the note is scripted as beta (Here) or something else.


@pytest.fixture
def filed(crow_wiki: Wiki) -> Wiki:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    add_folder(crow_wiki, "/", "beta", "Beta things.")
    add_note(crow_wiki, "/alpha", "Misfiled")
    return crow_wiki


def route_to_beta(classifier: FakeClassifier, p: float = 0.9) -> None:
    """The flat Choice of the audit: /beta with probability `p`."""
    classifier.add_choice("route", "/beta", {"/beta": p, "/alpha": 1 - p}, 0.9)


def test_a_note_routed_to_another_folder_above_min_confidence_gets_a_proposal(
    filed: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    route_to_beta(classifier)

    proposals = filed.audit_routes()

    assert len(proposals) == 1
    p = proposals[0]
    assert (p.path, p.title, p.current, p.proposed, p.applied) == ("alpha/misfiled.md", "Misfiled", "/alpha", "/beta", False)
    assert p.id == note_id(filed, "alpha/misfiled.md")
    assert p.confidence == pytest.approx(0.9)


def test_the_audit_makes_no_llm_call_and_changes_nothing(filed: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    route_to_beta(classifier)
    before = (filed.cfg.bundle / "alpha" / "misfiled.md").read_text(encoding="utf-8")
    log = (filed.cfg.bundle / LOG).read_text(encoding="utf-8")

    filed.audit_routes()

    assert llm.calls == []
    assert classifier.ops == ["route"]  # one flat Choice per note
    assert (filed.cfg.bundle / "alpha" / "misfiled.md").read_text(encoding="utf-8") == before
    assert (filed.cfg.bundle / LOG).read_text(encoding="utf-8") == log


def test_a_route_below_min_confidence_gives_no_proposal(filed: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    route_to_beta(classifier, p=0.6)  # below AUDIT_MIN

    assert filed.audit_routes() == []
    assert llm.calls == []


def test_min_confidence_is_a_parameter(filed: Wiki, classifier: FakeClassifier) -> None:
    route_to_beta(classifier, p=0.6)

    proposals = filed.audit_routes(min_confidence=0.55)

    assert [p.proposed for p in proposals] == ["/beta"]
    assert proposals[0].confidence == pytest.approx(0.6)


def test_the_default_min_confidence_is_0_7(filed: Wiki, classifier: FakeClassifier) -> None:
    route_to_beta(classifier, p=0.75)  # 0.75 passes the default

    assert len(filed.audit_routes()) == 1


def test_a_route_to_the_notes_own_folder_gives_no_proposal(filed: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    classifier.add_choice("route", "/alpha", {"/alpha": 0.95, "/beta": 0.05}, 0.95)

    assert filed.audit_routes() == []
    assert llm.calls == []


def test_a_classifier_error_gives_no_proposal_and_no_llm_call(filed: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # the audit never asks the LLM, nor routes by levels, where ingest would: the note gets no proposal
    def down(*args: Any, **kwargs: Any) -> Any:
        raise ModelError("classifier unreachable")

    classifier.ask = down  # type: ignore[method-assign]

    assert filed.audit_routes(min_confidence=0.0) == []
    assert llm.calls == []


def test_a_route_to_an_ancestor_of_the_notes_folder_gives_no_proposal(filed: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # the flat Choice has no "New subfolder": the other move the audit never proposes is up, out of a subfolder a plan or a split made
    add_folder(filed, "/alpha", "sub", "Sub things.")
    add_note(filed, "/alpha/sub", "Deep note")
    classifier.add_choice("route", "/alpha", {"/alpha": 0.95, "/beta": 0.05}, 0.95)  # Misfiled: its own folder
    classifier.add_choice("route", "/alpha", {"/alpha": 0.95, "/beta": 0.05}, 0.95)  # Deep note: the parent of its folder

    assert filed.audit_routes(min_confidence=0.0) == []
    assert llm.calls == []


def test_a_route_ending_in_none_of_these_gives_no_proposal(filed: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    classifier.add_choice("route", NONE, {NONE: 0.95, "/beta": 0.05}, 0.95)

    assert filed.audit_routes(min_confidence=0.0) == []
    assert llm.calls == []


def test_a_wiki_without_notes_has_nothing_to_audit(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")

    assert crow_wiki.audit_routes() == []
    assert (llm.calls, classifier.calls) == ([], [])


def test_the_archive_is_not_audited(filed: Wiki, classifier: FakeClassifier) -> None:
    note = filed.store.load().find("/alpha").note("misfiled")  # type: ignore[union-attr]
    filed.store.archive_note(note)  # type: ignore[arg-type]
    classifier.add_choice("route", "/alpha", {"/alpha": 0.95, "/beta": 0.05}, 0.95)

    filed.audit_routes()

    assert classifier.ops == ["route"]  # the one note of the tree, not its archived copy


def test_apply_moves_the_note_and_the_wiki_stays_clean(filed: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    route_to_beta(classifier)
    before = note_id(filed, "alpha/misfiled.md")

    proposals = filed.audit_routes(apply=True)

    assert [(p.path, p.proposed, p.applied) for p in proposals] == [("alpha/misfiled.md", "/beta", True)]
    assert not (filed.cfg.bundle / "alpha" / "misfiled.md").exists()
    assert note_id(filed, "beta/misfiled.md") == before
    assert filed.check() == []
    assert "Moved" in (filed.cfg.bundle / LOG).read_text(encoding="utf-8")
    assert llm.calls == []
    assert "classifier 1 calls" in filed.usage.summary()


def test_without_apply_nothing_moves(filed: Wiki, classifier: FakeClassifier) -> None:
    route_to_beta(classifier)

    filed.audit_routes(apply=False)

    assert (filed.cfg.bundle / "alpha" / "misfiled.md").is_file()
    assert not (filed.cfg.bundle / "beta" / "misfiled.md").exists()


def test_apply_moves_only_the_proposals(filed: Wiki, classifier: FakeClassifier) -> None:
    route_to_beta(classifier, p=0.6)

    assert filed.audit_routes(apply=True) == []
    assert (filed.cfg.bundle / "alpha" / "misfiled.md").is_file()


def test_the_audit_in_classic_mode_is_an_error(classic_wiki: Wiki, llm: FakeLLM) -> None:
    add_folder(classic_wiki, "/", "alpha", "Alpha things.")
    add_note(classic_wiki, "/alpha", "Misfiled")

    with pytest.raises((ConfigError, InputError)):
        classic_wiki.audit_routes()
    assert llm.calls == []


def test_the_audit_without_a_classifier_is_an_error(bundle: Path, llm: FakeLLM) -> None:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm)  # the classifier is built from the config: no key
    wiki.init()

    with pytest.raises((ConfigError, InputError)):
        wiki.audit_routes()
    assert llm.calls == []


def cli_with(monkeypatch: pytest.MonkeyPatch, wiki: Wiki) -> None:
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)


def test_cli_audit_prints_one_line_per_proposal_and_a_count(
    filed: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_with(monkeypatch, filed)
    route_to_beta(classifier)

    code = cli.main(["--bundle", str(filed.cfg.bundle), "audit"])

    out, err = capsys.readouterr()
    assert code == 0
    line = next(line for line in out.splitlines() if "alpha/misfiled.md" in line)
    assert str(note_id(filed, "alpha/misfiled.md")) in line
    assert line.index("/alpha") < line.index("/beta") and "0.9" in line
    assert re.search(r"\b1\b", out.replace(line, "") + err)  # the final count
    assert "classifier" in out + err  # and the classifier calls
    assert (filed.cfg.bundle / "alpha" / "misfiled.md").is_file()  # a report only


def test_cli_audit_min_confidence_and_apply(
    filed: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_with(monkeypatch, filed)
    route_to_beta(classifier, p=0.6)

    code = cli.main(["--bundle", str(filed.cfg.bundle), "audit", "--min-confidence", "0.5", "--apply"])

    assert code == 0
    assert (filed.cfg.bundle / "beta" / "misfiled.md").is_file()
    assert filed.check() == []


def test_cli_audit_in_classic_mode_exits_non_zero(
    classic_wiki: Wiki, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_with(monkeypatch, classic_wiki)

    assert cli.main(["--bundle", str(classic_wiki.cfg.bundle), "audit"]) != 0
    assert capsys.readouterr().err != ""


def test_http_audit_returns_the_proposals(filed: Wiki, classifier: FakeClassifier) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from llmw2.server import create_app

    route_to_beta(classifier)

    response = TestClient(create_app(filed)).post("/audit", json={})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0] == {
        "path": "alpha/misfiled.md",
        "id": note_id(filed, "alpha/misfiled.md"),
        "title": "Misfiled",
        "current": "/alpha",
        "proposed": "/beta",
        "confidence": pytest.approx(0.9),
        "applied": False,
    }
    assert (filed.cfg.bundle / "alpha" / "misfiled.md").is_file()


def test_http_audit_with_min_confidence_and_apply(filed: Wiki, classifier: FakeClassifier) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from llmw2.server import create_app

    route_to_beta(classifier, p=0.6)

    response = TestClient(create_app(filed)).post("/audit", json={"min_confidence": 0.5, "apply": True})

    assert response.status_code == 200
    assert [(p["proposed"], p["applied"]) for p in response.json()] == [("/beta", True)]
    assert (filed.cfg.bundle / "beta" / "misfiled.md").is_file()
    assert filed.check() == []


def test_http_audit_in_classic_mode_is_an_error(client: Any) -> None:
    response = client.post("/audit", json={})

    assert response.status_code >= 400  # ConfigError maps to 503, InputError to 4xx


# -- (c) the log line and the taxonomy health -------------------------------------------


def log_line(wiki: Wiki, note_rel: str) -> str:
    text = (wiki.cfg.bundle / LOG).read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if f"({note_rel})" in line and "**Created**" in line)


def test_the_log_line_of_a_note_routed_by_fallback_says_so(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, {"title": "Q3 revenue", "summary": "S.", "tags": [], "body": "Facts."})
    llm.add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    classifier.add_choice("route", NONE, {NONE: 1.0}, 1.0)  # the flat Choice: none covers it, so the per-level routing follows
    classifier.add_choice("route", "alpha", {"alpha": 0.45, NEW: 0.35}, 0.3)
    classifier.add_choice("route", HERE, {HERE: 0.4, NEW: 0.3, NONE: 0.3}, 0.9)

    result = crow_wiki.ingest("Revenue grew.")

    assert ", routed by fallback" in log_line(crow_wiki, result.note)


def test_the_log_line_of_a_note_routed_by_the_classifier_does_not(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, {"title": "Q3 revenue", "summary": "S.", "tags": [], "body": "Facts."})
    classifier.add_choice("route", NONE, {NONE: 1.0}, 1.0)  # the flat Choice: none covers it, so the per-level routing follows
    classifier.add_choice("route", "alpha", {"alpha": 0.9, NEW: 0.1}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)

    result = crow_wiki.ingest("Revenue grew.")

    assert "routed by fallback" not in log_line(crow_wiki, result.note)


def test_the_log_line_of_a_classic_ingest_does_not(classic_wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(SUMMARIZE, {"title": "Revenue", "summary": "When.", "body": "On delivery."})
    llm.add("librarian/route", {"action": "new"}).add(NAME_FOLDER, {"name": "finance", "description": "Money."})

    result = classic_wiki.ingest("Revenue is booked on delivery.")

    assert "routed by fallback" not in log_line(classic_wiki, result.note)


FALLBACK = ", routed by fallback"


def created(rel: str, suffix: str = "") -> str:
    return f"* **Created**: [{rel}]({rel}) - from /raw/x.md (steps abc, fake/llm){suffix}"


def merged(rel: str) -> str:
    return f"* **Merged**: [{rel}]({rel}) - from /raw/x.md (steps abc, fake/llm)"


def revised(rel: str) -> str:
    return f"* **Revised**: [{rel}]({rel}) - from /raw/x.md (steps abc, fake/llm), previous version archived at /.archive/y.md"


def new_folder(rel: str) -> str:
    return f"* **Created folder**: [/{rel}]({rel}/index.md) - A folder."


def write_log(wiki: Wiki, days: dict[str, list[str]]) -> None:
    """`days`: date → lines; newest first as the real log is."""
    parts = ["# Wiki log", ""]
    for day, lines in days.items():
        parts += [f"## {day}", "", *lines, ""]
    (wiki.cfg.bundle / LOG).write_text("\n".join(parts), encoding="utf-8")


def health_rows(wiki: Wiki, **kw: Any) -> dict[tuple[str, str], Any]:
    return {(r.folder.strip("/"), r.month): r for r in wiki.taxonomy_health(**kw)}


@pytest.fixture
def logged(classic_wiki: Wiki) -> Wiki:
    write_log(
        classic_wiki,
        {
            "2026-10-05": [
                created("ops/a.md"),
                created("ops/b.md"),
                created("ops/c.md"),
                created("ops/d.md"),
                created("ops/e.md", FALLBACK),  # ops, October: 5 filed, 1 fallback: 0.2
                created("sourcing/a.md", FALLBACK),
                created("sourcing/b.md", FALLBACK),
                created("sourcing/c.md", FALLBACK),
                created("sourcing/d.md", FALLBACK),  # sourcing, October: 4 filed, all by fallback, under min_filed
            ],
            "2026-09-28": [
                created("finance/a.md"),
                created("finance/b.md", FALLBACK),
                merged("finance/c.md"),
                revised("finance/d.md"),
                created("finance/f.md"),
                created("finance/pricing/e.md"),  # finance/pricing is another folder
                new_folder("finance/pricing"),  # a new subfolder of /finance
                new_folder("finance/tax"),  # and a second one
                "* **Moved**: [x](ops/x.md) - from /a.md to /ops/x.md",
                "* **Described folder**: [/ops](ops/index.md) - New text",
            ],
            "2026-09-01": [
                "* **created**: [Demo](ops/demo.md) - from /raw/demo.md (demo)",
                "* **created**: [Demo 2](ops/demo2.md) - from /raw/demo2.md (demo)",
            ],
        },
    )
    return classic_wiki


def test_health_counts_filed_new_subfolders_and_fallbacks_per_folder_and_month(logged: Wiki) -> None:
    rows = health_rows(logged)

    ops = rows[("ops", "2026-10")]
    assert (ops.filed, ops.new_subfolders, ops.fallbacks) == (5, 0, 1)
    assert ops.share == pytest.approx(0.2)
    finance = rows[("finance", "2026-09")]
    assert (finance.filed, finance.new_subfolders, finance.fallbacks) == (5, 2, 1)
    assert finance.share == pytest.approx(3 / 5)
    pricing = rows[("finance/pricing", "2026-09")]
    assert (pricing.filed, pricing.new_subfolders, pricing.fallbacks) == (1, 0, 0)


def test_health_counts_created_merged_and_revised_as_filed(logged: Wiki) -> None:
    assert health_rows(logged)[("finance", "2026-09")].filed == 5


def test_health_ignores_the_demo_lowercase_lines(logged: Wiki) -> None:
    rows = health_rows(logged)

    assert ("ops", "2026-09") not in rows or rows[("ops", "2026-09")].filed == 0
    assert rows[("ops", "2026-10")].filed == 5


def test_health_flags_a_busy_folder_with_a_high_share(logged: Wiki) -> None:
    rows = health_rows(logged)

    assert rows[("finance", "2026-09")].flag is True  # 5 filed >= 5, share 0.6 > 0.3


def test_health_does_not_flag_a_folder_with_few_filings(logged: Wiki) -> None:
    assert health_rows(logged)[("sourcing", "2026-10")].flag is False  # share 1.0 but 4 filed < 5


def test_health_does_not_flag_a_folder_under_the_max_share(logged: Wiki) -> None:
    assert health_rows(logged)[("ops", "2026-10")].flag is False  # 5 filed, share 0.2 <= 0.3


def test_health_thresholds_are_parameters(logged: Wiki) -> None:
    low_min = health_rows(logged, min_filed=4)
    assert low_min[("sourcing", "2026-10")].flag is True
    assert low_min[("ops", "2026-10")].flag is False
    assert health_rows(logged, min_filed=6)[("finance", "2026-09")].flag is False

    strict = health_rows(logged, max_share=0.1)
    assert strict[("ops", "2026-10")].flag is True
    assert strict[("sourcing", "2026-10")].flag is False  # still under min_filed


def test_health_a_share_equal_to_max_share_is_not_flagged(classic_wiki: Wiki) -> None:
    lines = [created(f"legal/{n}.md", FALLBACK if n < 3 else "") for n in range(10)]  # 3 of 10
    write_log(classic_wiki, {"2026-10-01": lines})

    row = health_rows(classic_wiki)[("legal", "2026-10")]

    assert (row.filed, row.fallbacks) == (10, 3)
    assert row.flag is False


def test_health_a_share_over_max_share_with_enough_filings_is_flagged(classic_wiki: Wiki) -> None:
    lines = [created(f"legal/{n}.md", FALLBACK if n < 3 else "") for n in range(5)] + [new_folder("legal/sub")]
    write_log(classic_wiki, {"2026-10-01": lines})

    row = health_rows(classic_wiki)[("legal", "2026-10")]

    assert (row.filed, row.new_subfolders, row.fallbacks) == (5, 1, 3)
    assert row.share == pytest.approx(0.8)
    assert row.flag is True


def test_health_splits_one_folder_by_month(classic_wiki: Wiki) -> None:
    write_log(
        classic_wiki,
        {"2026-10-01": [created("ops/a.md"), created("ops/b.md")], "2026-09-30": [created("ops/c.md")]},
    )

    rows = health_rows(classic_wiki)

    assert (rows[("ops", "2026-10")].filed, rows[("ops", "2026-09")].filed) == (2, 1)


def test_health_of_an_empty_log_is_empty(classic_wiki: Wiki) -> None:
    assert classic_wiki.taxonomy_health() == []


def test_health_makes_no_model_call(logged: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    logged.taxonomy_health()

    assert (llm.calls, classifier.calls) == ([], [])


def test_health_follows_a_real_ingest(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, {"title": "Q3 revenue", "summary": "S.", "tags": [], "body": "Facts."})
    llm.add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    classifier.add_choice("route", NONE, {NONE: 1.0}, 1.0)  # the flat Choice: none covers it, so the per-level routing follows
    classifier.add_choice("route", "alpha", {"alpha": 0.45, NEW: 0.35}, 0.3)
    classifier.add_choice("route", HERE, {HERE: 0.4, NEW: 0.3, NONE: 0.3}, 0.9)
    crow_wiki.ingest("Revenue grew.")

    rows = [r for r in crow_wiki.taxonomy_health() if r.folder.strip("/") == "alpha"]

    assert [(r.filed, r.fallbacks) for r in rows] == [(1, 1)]


def test_cli_health_prints_a_table_with_the_flagged_rows_marked(
    logged: Wiki, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli_with(monkeypatch, logged)

    code = cli.main(["--bundle", str(logged.cfg.bundle), "health", "--min-filed", "4"])

    out = capsys.readouterr().out
    assert code == 0
    lines = out.splitlines()
    assert any("2026-10" in line and "ops" in line for line in lines)
    sourcing = next(line for line in lines if "sourcing" in line)
    ops = next(line for line in lines if "ops" in line and "2026-10" in line)
    mark = re.compile(r"[*!]|flag|FLAG|⚠", re.IGNORECASE)
    assert mark.search(sourcing) and not mark.search(ops)  # flagged with --min-filed 4; ops is not


def test_cli_health_options_change_the_flags(logged: Wiki, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    cli_with(monkeypatch, logged)

    assert cli.main(["--bundle", str(logged.cfg.bundle), "health", "--max-share", "0.1"]) == 0

    ops = next(line for line in capsys.readouterr().out.splitlines() if "ops" in line and "2026-10" in line)
    assert re.search(r"[*!]|flag", ops, re.IGNORECASE)


def test_http_taxonomy_returns_the_rows(logged: Wiki) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from llmw2.server import create_app

    response = TestClient(create_app(logged)).get("/taxonomy")

    assert response.status_code == 200
    rows = {(r["folder"].strip("/"), r["month"]): r for r in response.json()}
    ops = rows[("ops", "2026-10")]
    assert (ops["filed"], ops["new_subfolders"], ops["fallbacks"], ops["flag"]) == (5, 0, 1, False)
    assert ops["share"] == pytest.approx(0.2)
    assert rows[("sourcing", "2026-10")]["flag"] is False
