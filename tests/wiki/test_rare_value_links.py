# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""See-also candidates by rare shared values: `linking.rare_scores`, and CROW's `relate` reading them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llmw2 import Wiki, WikiConfig
from llmw2.agents.linking import RARE_TOP, note_values, rare_scores
from llmw2.agents.steps_classic import incoming_values
from llmw2.bundle.tree import Folder, Note
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE = "Here"
SUMMARIZE, ROUTE = "librarian/summarize", "librarian/route"
CODE = "ZK-4471"


# -- an in-memory wiki ----------------------------------------------------------------------


def make_root(bodies: dict[str, str]) -> Folder:
    """A tree whose notes are `rel -> body` (title and summary have no values); folders follow the rels."""
    base = Path("/wiki")
    root = Folder(path=base, rel="")
    folders = {"": root}
    for rel in sorted(bodies):
        parent_rel = rel.rpartition("/")[0]
        parts = parent_rel.split("/") if parent_rel else []
        for i in range(len(parts)):
            sub = "/".join(parts[: i + 1])
            if sub not in folders:
                up = folders["/".join(parts[:i])]
                folders[sub] = Folder(path=base / sub, rel=sub, parent=up)
                up.subfolders.append(folders[sub])
        folder = folders[parent_rel]
        note = Note(path=base / rel, rel=rel, frontmatter={"title": "Plain", "description": "Plain."}, body=bodies[rel], folder=folder)
        folder.notes.append(note)
    return root


def find(root: Folder, rel: str) -> Note:
    return next(n for n in root.all_notes() if n.rel == rel)


def wiki_of(special: dict[str, str], fillers: int = 20) -> Folder:
    """The special notes plus `fillers` notes with no value at all, so that N is known and the cap is 3."""
    bodies = {f"misc/filler-{i}.md": "Nothing here." for i in range(fillers)}
    return make_root({**bodies, **special})


def see_also(count: int) -> str:
    return "\n\n# See also\n\n" + "\n".join(f"* [T{i}](t{i}.md) - About it." for i in range(count))


def rels(notes: list[Note]) -> list[str]:
    return [n.rel for n in notes]


def rare_candidates(root: Folder, note: Note, exclude: set[str]) -> list[Note]:
    """The notes `rare_scores` proposes, best first."""
    return [n for n, _ in rare_scores(root, note, exclude)]


# -- note_values -----------------------------------------------------------------------------


def test_note_values_are_the_incoming_values_of_the_full_text() -> None:
    note = find(make_root({"a/x.md": f"The code {CODE} and 1,500 dollars."}), "a/x.md")

    assert note_values(note) == incoming_values(note.full_text())
    assert CODE.lower() in note_values(note)


def test_note_values_cached_call_returns_the_same_values() -> None:
    note = find(make_root({"a/x.md": f"The code {CODE}."}), "a/x.md")

    assert note_values(note) == note_values(note)


def test_note_values_change_when_the_body_changes() -> None:
    note = find(make_root({"a/x.md": f"The code {CODE}."}), "a/x.md")
    before = note_values(note)

    note.body = "The code QW-9912."

    after = note_values(note)
    assert "qw-9912" in after and "qw-9912" not in before
    assert CODE.lower() not in after


def test_note_values_ignore_the_see_also_section() -> None:
    note = find(make_root({"a/x.md": f"The code {CODE}."}), "a/x.md")
    before = note_values(note)

    note.body += "\n\n# See also\n\n* [Other 99999](other.md) - Code QW-9912."

    assert note_values(note) == before


# -- rare_candidates --------------------------------------------------------------------------


def test_a_rare_code_shared_across_distant_folders_links() -> None:
    root = wiki_of({"alpha/one.md": f"Invoice {CODE}.", "beta/deep/deeper/two.md": f"Paid {CODE} late."})

    assert rels(rare_candidates(root, find(root, "alpha/one.md"), set())) == ["beta/deep/deeper/two.md"]


def test_a_value_held_by_more_notes_than_the_cap_does_not_link() -> None:
    # N = 25 so the cap is max(3, 1.25) = 3; the code is in 5 notes
    special = {f"g{i}/n.md": f"Invoice {CODE}." for i in range(5)}
    root = wiki_of(special, fillers=20)

    assert rare_candidates(root, find(root, "g0/n.md"), set()) == []


def test_a_value_at_the_minimum_cap_still_links() -> None:
    # df == 3 == RARE_MIN_CAP on a small wiki
    root = wiki_of({f"g{i}/n.md": f"Invoice {CODE}." for i in range(3)}, fillers=10)

    assert rels(rare_candidates(root, find(root, "g0/n.md"), set())) == ["g1/n.md", "g2/n.md"]


def test_the_cap_grows_with_the_wiki() -> None:
    # N = 200 so the cap is 10: a value in 8 notes links, in 11 it does not
    fillers = 200 - 11
    linked = wiki_of({f"g{i}/n.md": f"Invoice {CODE}." for i in range(8)}, fillers=fillers + 3)
    not_linked = wiki_of({f"g{i}/n.md": f"Invoice {CODE}." for i in range(11)}, fillers=fillers)

    assert len(rare_candidates(linked, find(linked, "g0/n.md"), set())) == 7
    assert rare_candidates(not_linked, find(not_linked, "g0/n.md"), set()) == []


def test_bare_years_are_ignored() -> None:
    root = wiki_of({"alpha/one.md": "Founded in 1987, renewed in 2024.", "beta/two.md": "Since 1987 and again 2024."})

    assert rare_candidates(root, find(root, "alpha/one.md"), set()) == []


def test_a_year_does_not_count_next_to_a_real_shared_value() -> None:
    root = wiki_of(
        {
            "alpha/one.md": f"Founded in 2024. Code {CODE}.",
            "beta/two.md": f"Code {CODE}.",
            "gamma/three.md": "Founded in 2024.",
        }
    )

    assert rels(rare_candidates(root, find(root, "alpha/one.md"), set())) == ["beta/two.md"]


def test_two_shared_rare_values_beat_one() -> None:
    root = wiki_of(
        {
            "a/me.md": "Codes ZK-4471 and QW-9912 and RT-1234.",
            "a/one.md": "Only ZK-4471 here.",
            "b/two.md": "Both ZK-4471 and QW-9912.",
            "c/three.md": "All of ZK-4471, QW-9912 and RT-1234.",
        },
        fillers=100,  # N = 104: the cap is 5, so ZK-4471 (in four notes) is still rare
    )

    assert rels(rare_candidates(root, find(root, "a/me.md"), set())) == ["c/three.md", "b/two.md", "a/one.md"]


def test_equal_scores_are_ordered_by_rel() -> None:
    root = wiki_of(
        {"a/me.md": f"Code {CODE}.", "z/last.md": f"Code {CODE}.", "m/mid.md": f"Code {CODE}.", "b/first.md": f"Code {CODE}."}, fillers=100
    )

    assert rels(rare_candidates(root, find(root, "a/me.md"), set())) == ["b/first.md", "m/mid.md", "z/last.md"]


def test_the_note_itself_is_never_a_candidate() -> None:
    root = wiki_of({"a/me.md": f"Code {CODE}.", "b/other.md": f"Code {CODE}."})

    assert "a/me.md" not in rels(rare_candidates(root, find(root, "a/me.md"), set()))


def test_excluded_rels_are_skipped() -> None:
    root = wiki_of({"a/me.md": f"Code {CODE}.", "b/other.md": f"Code {CODE}.", "c/more.md": f"Code {CODE}."})

    assert rels(rare_candidates(root, find(root, "a/me.md"), {"b/other.md"})) == ["c/more.md"]


def test_a_note_with_twelve_see_also_entries_is_skipped() -> None:
    root = wiki_of(
        {
            "a/me.md": f"Code {CODE}.",
            "b/full.md": f"Code {CODE}.{see_also(12)}",
            "c/almost.md": f"Code {CODE}.{see_also(11)}",
        }
    )

    assert rels(rare_candidates(root, find(root, "a/me.md"), set())) == ["c/almost.md"]


def test_at_most_rare_top_candidates() -> None:
    codes = [f"AB-{1000 + i}" for i in range(RARE_TOP + 5)]
    special = {"a/me.md": "Codes " + " ".join(codes) + "."}
    special |= {f"b/n{i:02d}.md": f"Only {code}." for i, code in enumerate(codes)}
    root = wiki_of(special, fillers=10)

    found = rels(rare_candidates(root, find(root, "a/me.md"), set()))

    assert found == [f"b/n{i:02d}.md" for i in range(RARE_TOP)]  # equal scores: the first by rel


def test_nothing_shared_gives_nothing() -> None:
    root = wiki_of({"a/me.md": f"Code {CODE}.", "b/other.md": "Code QW-9912.", "c/third.md": "No values."})

    assert rare_candidates(root, find(root, "a/me.md"), set()) == []


# -- the Librarian ---------------------------------------------------------------------------


def draft(title: str, body: str) -> dict[str, Any]:
    return {"title": title, "summary": f"What {title} says.", "tags": [], "body": body}


def seed(wiki: Wiki) -> None:
    """alpha holds one note; beta, a distant folder, holds a ledger that shares CODE with the incoming note."""
    for name in ("alpha", "beta"):
        wiki.store.create_folder(wiki.store.load().find("/"), name, f"{name} things.")
    for folder, title, body in (
        ("/alpha", "Alpha note", "# Overview\n\nSeeded."),
        ("/beta", "Beta ledger", f"# Overview\n\nThe ledger lists {CODE}."),
    ):
        wiki.store.write_note(
            wiki.store.load().find(folder),
            title=title,
            summary=f"About {title}.",
            body=body,
            tags=[],
            source={"resource": "/raw/seed.md", "title": "Seed"},
        )


def relate_questions(classifier: FakeClassifier) -> list[str]:
    return [q["instructions"] for op, _, qs in classifier.calls if op == "relate" for q in qs.values()]


def crow_ingest(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, body: str) -> Any:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier)
    wiki.init()
    seed(wiki)
    llm.add(SUMMARIZE, draft("Q3 revenue", body))
    classifier.add_choice("route", "alpha", {"alpha": 0.9, "beta": 0.05, "New subfolder": 0.05}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.9, "New subfolder": 0.05, "None of these": 0.05}, 0.9)
    classifier.noul_scores["relate"] = {'"Beta ledger"': 0.9}
    return wiki.ingest("Revenue grew."), wiki


def test_crow_relate_is_offered_a_distant_note_sharing_a_rare_code_and_can_link_it(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    result, wiki = crow_ingest(bundle, llm, classifier, f"# Overview\n\nInvoice {CODE} paid.")

    assert any('"Beta ledger"' in q for q in relate_questions(classifier))
    assert "beta/beta-ledger.md" in result.related
    assert "[Q3 revenue]" in wiki.store.load().find("/beta").note("beta-ledger").body  # the back-link


def test_crow_records_a_relate_candidates_decision_with_the_scores(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    result, _ = crow_ingest(bundle, llm, classifier, f"# Overview\n\nInvoice {CODE} paid.")

    decision = next(d for d in result.decisions if d.step == "relate_candidates")
    assert decision.decider == "code"
    assert decision.choice == "1 by rare values"
    assert list(decision.scores) == ["beta/beta-ledger.md"]
    assert decision.scores["beta/beta-ledger.md"] > 0


def test_crow_without_a_shared_rare_value_offers_nothing_extra(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    result, _ = crow_ingest(bundle, llm, classifier, "# Overview\n\nNo values at all.")

    assert not any('"Beta ledger"' in q for q in relate_questions(classifier))
    assert "beta/beta-ledger.md" not in result.related


def test_classic_mode_adds_no_rare_candidates(bundle: Path, llm: FakeLLM) -> None:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)
    wiki.init()
    seed(wiki)
    llm.add(SUMMARIZE, draft("Q3 revenue", f"# Overview\n\nInvoice {CODE} paid."))
    llm.add(ROUTE, {"action": "descend", "subfolder": "alpha"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": "alpha/alpha-note.md"}).add("librarian/consolidate", {"decision": "new"})
    llm.add("librarian/relate", {"related": ["beta/beta-ledger.md"]})

    result = wiki.ingest("Revenue grew.")

    relate_prompt = next(m for op, msgs in llm.calls if op == "librarian/relate" for m in msgs[-1:])["content"]
    assert "beta/beta-ledger.md" not in relate_prompt
    assert result.related == []  # the LLM named a note it was never shown
    assert all(d.step != "relate_candidates" for d in result.decisions)
