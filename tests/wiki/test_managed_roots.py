# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Managed top-level areas: the queue of proposals users make, confirm and dismiss, and renaming and merging roots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig, cli
from llmw2.bundle.tree import LOG, Note
from llmw2.errors import InputError
from tests.wiki.fakes import FakeLLM

SOURCE = {"type": "text", "title": "t"}


class Built:
    """/finance holds tea.md (links to budget.md and to ops/pricing/rates.md), budget.md and pricing/list.md; /ops holds
    runbook.md (links to tea.md) and pricing/rates.md; /legal holds terms.md (links to tea.md)."""

    def __init__(self, wiki: Wiki) -> None:
        self.wiki = wiki
        store = wiki.store
        root = store.load()
        finance, _ = store.create_folder(root, "finance", "Money in and out.")
        pricing, _ = store.create_folder(finance, "pricing", "What things cost.")
        ops, _ = store.create_folder(root, "ops", "How the work is done.")
        ops_pricing, _ = store.create_folder(ops, "pricing", "What the work costs.")
        legal, _ = store.create_folder(root, "legal", "Contracts.")

        def put(folder: Any, title: str, body: str = "Text.") -> Note:
            return store.write_note(folder, title=title, summary=f"About {title}.", body=f"# Overview\n\n{body}\n", tags=[], source=SOURCE)

        self.budget = put(finance, "Budget")
        self.tea = put(finance, "Tea", "See [the budget](budget.md) and [rates](../ops/pricing/rates.md).")
        self.list = put(pricing, "List")
        self.runbook = put(ops, "Runbook", "Tea is in [tea](../finance/tea.md).")
        self.rates = put(ops_pricing, "Rates")
        self.terms = put(legal, "Terms", "See [the tea note](../finance/tea.md).")

    def ids(self, *notes: Note) -> list[str | None]:
        return [n.id for n in notes]

    def note(self, title: str) -> Note:
        return next(n for n in self.wiki.store.load().all_notes() if n.title == title)

    def roots(self) -> list[str]:
        return [f.name for f in self.wiki.store.load().subfolders]


@pytest.fixture
def built(classic_wiki: Wiki) -> Built:
    return Built(classic_wiki)


def clean(wiki: Wiki) -> None:
    assert [str(p) for p in wiki.check()] == []


def log_text(wiki: Wiki) -> str:
    return (wiki.cfg.bundle / LOG).read_text(encoding="utf-8")


# -- the queue --------------------------------------------------------------------------------


def test_the_queue_is_empty_by_default_and_a_proposal_survives_reopening_the_wiki(built: Built, llm: FakeLLM) -> None:
    # ARRANGE
    assert built.wiki.proposals() == []

    # ACT
    made = built.wiki.propose_area("Suppliers", "Who we buy from.", [str(i) for i in built.ids(built.tea, built.terms)])
    again = Wiki(WikiConfig(bundle=built.wiki.cfg.bundle, mode="classic"), llm=llm)

    # ASSERT
    (found,) = again.proposals()
    assert found == made and found.status == "open" and found.origin == "user" and found.created
    assert found.label == "Suppliers" and found.description == "Who we buy from." and found.notes == built.ids(built.tea, built.terms)
    assert built.roots() == ["finance", "legal", "ops"]  # nothing moved, nothing made


def test_a_note_can_be_proposed_by_its_path_and_is_kept_by_its_id(built: Built) -> None:
    made = built.wiki.propose_area("Suppliers", "Who we buy from.", ["finance/tea.md", str(built.terms.id)])

    assert made.notes == built.ids(built.tea, built.terms)


@pytest.mark.parametrize(
    ("label", "description", "notes", "said"),
    [
        ("", "d", ["finance/tea.md"], "needs a name"),
        ("Suppliers", " ", ["finance/tea.md"], "needs a description"),
        ("Suppliers", "d", [], "at least one note"),
        ("Suppliers", "d", ["finance/nothing.md"], "no note with id or path finance/nothing.md"),
        ("Finance", "d", ["finance/tea.md"], "is taken"),
    ],
)
def test_a_proposal_needs_a_name_a_description_notes_that_exist_and_a_free_name(
    built: Built, label: str, description: str, notes: list[str], said: str
) -> None:
    with pytest.raises(InputError, match=said):
        built.wiki.propose_area(label, description, notes)

    assert built.wiki.proposals(None) == []


def test_two_open_proposals_cannot_share_a_name(built: Built) -> None:
    built.wiki.propose_area("Suppliers", "Who we buy from.", ["finance/tea.md"])

    with pytest.raises(InputError, match="is taken"):
        built.wiki.propose_area("suppliers", "Others.", ["legal/terms.md"])


def test_dismiss_closes_a_proposal_and_touches_nothing(built: Built) -> None:
    # ARRANGE
    made = built.wiki.propose_area("Suppliers", "Who we buy from.", ["finance/tea.md"])

    # ACT
    built.wiki.dismiss_proposal(made.id)

    # ASSERT
    assert built.wiki.proposals() == [] and [p.status for p in built.wiki.proposals(None)] == ["dismissed"]
    assert built.roots() == ["finance", "legal", "ops"] and built.note("Tea").rel == "finance/tea.md"
    with pytest.raises(InputError, match="no open proposal"):
        built.wiki.dismiss_proposal(made.id)


# -- confirm ---------------------------------------------------------------------------------


def test_confirm_opens_the_root_and_moves_only_the_proposed_notes_that_are_still_there(built: Built) -> None:
    # ARRANGE
    built.tea.frontmatter["overflow"] = {"p_none": 0.4, "p_root": 0.3, "at": "x"}
    built.wiki.store._save(built.tea)
    made = built.wiki.propose_area("Suppliers", "Who we buy from.", ["finance/tea.md", "legal/terms.md", "finance/pricing/list.md"])
    built.wiki.delete_note("finance/pricing/list.md")
    others = ("Budget", "Rates")
    before = {n.rel: (dict(n.frontmatter), n.body) for n in built.wiki.store.load().all_notes() if n.title in others}

    # ACT
    proposal, moved = built.wiki.confirm_proposal(made.id)

    # ASSERT
    assert proposal.status == "confirmed" and built.wiki.proposals() == []
    assert sorted(moved) == ["suppliers/tea.md", "suppliers/terms.md"]
    tree = built.wiki.store.load()
    area = tree.find("suppliers")
    assert area is not None and area.description == "Who we buy from."
    assert built.note("Tea").frontmatter["moved_from"] == "/finance" and built.note("Terms").frontmatter["moved_from"] == "/legal"
    assert "overflow" not in built.note("Tea").frontmatter
    after = {n.rel: (dict(n.frontmatter), n.body) for n in tree.all_notes() if n.title in others}
    # (Runbook only follows the link of the moved note)
    assert after == before
    assert "[tea](../suppliers/tea.md)" in built.note("Runbook").body and "(tea.md)" in built.note("Terms").body
    clean(built.wiki)
    lines = [x for x in log_text(built.wiki).splitlines() if "Confirmed area" in x]
    assert len(lines) == 1 and "/suppliers/tea.md" in lines[0] and "Moved" not in log_text(built.wiki)
    assert log_text(built.wiki).count("Created folder") == 5  # the one made by the confirm has no line of its own


def test_confirm_is_refused_at_the_limit_unless_over_the_limit_and_the_proposal_stays_open(bundle: Path, llm: FakeLLM) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", max_top_folders=3), llm=llm)
    wiki.init()
    built = Built(wiki)
    made = wiki.propose_area("Suppliers", "Who we buy from.", ["finance/tea.md"])

    # ACT
    with pytest.raises(InputError, match="max_top_folders"):
        wiki.confirm_proposal(made.id)
    still = [p.id for p in wiki.proposals()]
    wiki.confirm_proposal(made.id, over_limit=True)

    # ASSERT
    assert still == [made.id] and built.roots() == ["finance", "legal", "ops", "suppliers"]
    clean(wiki)


def test_confirm_with_none_of_the_notes_left_or_a_name_taken_meanwhile_changes_nothing(built: Built) -> None:
    # ARRANGE
    only = built.wiki.propose_area("Suppliers", "Who we buy from.", ["legal/terms.md"])
    taken = built.wiki.propose_area("Vendors", "Others.", ["finance/tea.md"])
    built.wiki.delete_note("legal/terms.md")
    built.wiki.create_folder("/", "vendors", "Made by hand.")

    # ACT / ASSERT
    with pytest.raises(InputError, match="none of the proposed notes"):
        built.wiki.confirm_proposal(only.id)
    with pytest.raises(InputError, match="is taken"):
        built.wiki.confirm_proposal(taken.id)
    assert "suppliers" not in built.roots() and built.note("Tea").rel == "finance/tea.md" and len(built.wiki.proposals()) == 2


def test_an_unknown_or_settled_proposal_cannot_be_confirmed(built: Built) -> None:
    made = built.wiki.propose_area("Suppliers", "Who we buy from.", ["finance/tea.md"])
    built.wiki.confirm_proposal(made.id)

    for proposal_id in (made.id, "nope"):
        with pytest.raises(InputError, match="no open proposal"):
            built.wiki.confirm_proposal(proposal_id)


def test_emptying_the_wiki_clears_the_queue(built: Built) -> None:
    built.wiki.propose_area("Suppliers", "Who we buy from.", ["finance/tea.md"])

    built.wiki.clear()

    assert built.wiki.proposals(None) == []


# -- create_area, rename, merge --------------------------------------------------------------


def test_create_area_is_refused_at_the_limit_unless_over_it(bundle: Path, llm: FakeLLM) -> None:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", max_top_folders=1), llm=llm)
    wiki.init()
    wiki.create_area("one", "The first.")

    with pytest.raises(InputError, match="max_top_folders"):
        wiki.create_area("two", "The second.")
    assert wiki.create_area("two", "The second.", over_limit=True).label == "/two"


def test_rename_root_moves_the_notes_and_keeps_every_link_and_the_description(built: Built) -> None:
    # ACT
    folder = built.wiki.rename_root("/finance", "Money")

    # ASSERT
    assert folder.label == "/money" and folder.description == "Money in and out." and built.roots() == ["legal", "money", "ops"]
    money = built.wiki.store.load().find("money")
    assert money is not None and sorted(n.rel for n in money.all_notes()) == ["money/budget.md", "money/pricing/list.md", "money/tea.md"]
    assert "[tea](../money/tea.md)" in built.note("Runbook").body and "(../money/tea.md)" in built.note("Terms").body
    assert "(budget.md)" in built.note("Tea").body and "(../ops/pricing/rates.md)" in built.note("Tea").body
    assert "Renamed folder" in log_text(built.wiki)
    clean(built.wiki)


@pytest.mark.parametrize(
    ("path", "name", "said"),
    [
        ("/finance/pricing", "x", "not a top-level"),
        ("/finance", "ops", "taken"),
        ("/finance", "!!", "needs a name"),
        ("/nothing", "x", "not a top-level"),
    ],
)
def test_rename_root_refuses_what_is_not_a_free_name_for_a_root(built: Built, path: str, name: str, said: str) -> None:
    with pytest.raises(InputError, match=said):
        built.wiki.rename_root(path, name)


def test_merge_roots_moves_notes_and_subfolders_with_moved_from_and_deletes_the_source(built: Built) -> None:
    # ACT
    target = built.wiki.merge_roots("/finance", "/ops")

    # ASSERT
    assert target.label == "/ops" and built.roots() == ["legal", "ops"]
    ops = built.wiki.store.load().find("ops")
    assert ops is not None and sorted(n.rel for n in ops.all_notes()) == [  # the two "pricing" folders are one
        "ops/budget.md", "ops/pricing/list.md", "ops/pricing/rates.md", "ops/runbook.md", "ops/tea.md"]
    assert built.note("Tea").frontmatter["moved_from"] == "/finance" and built.note("List").frontmatter["moved_from"] == "/finance/pricing"
    assert "moved_from" not in built.note("Runbook").frontmatter
    assert "(../ops/tea.md)" in built.note("Terms").body and "(tea.md)" in built.note("Runbook").body
    assert not (built.wiki.cfg.bundle / "finance").exists()
    assert "Merged folders" in log_text(built.wiki)
    clean(built.wiki)


def test_merge_roots_keeps_a_subfolder_the_target_lacks_with_its_description(built: Built) -> None:
    built.wiki.merge_roots("/finance", "/legal")

    moved = built.wiki.store.load().find("legal/pricing")
    assert moved is not None and moved.description == "What things cost." and [n.rel for n in moved.notes] == ["legal/pricing/list.md"]
    clean(built.wiki)


@pytest.mark.parametrize(
    ("source", "target"), [("/finance", "/finance"), ("/finance/pricing", "/ops"), ("/finance", "/nothing"), ("/", "/ops")]
)
def test_merge_roots_refuses_what_are_not_two_different_roots(built: Built, source: str, target: str) -> None:
    with pytest.raises(InputError):
        built.wiki.merge_roots(source, target)

    assert built.roots() == ["finance", "legal", "ops"]


# -- the command line ------------------------------------------------------------------------


@pytest.fixture
def cli_wiki(monkeypatch: pytest.MonkeyPatch, classic_wiki: Wiki) -> Built:
    monkeypatch.setattr(cli, "make_wiki", lambda **_: classic_wiki)
    return Built(classic_wiki)


def test_the_cli_proposes_lists_confirms_dismisses_renames_and_merges(cli_wiki: Built, capsys: pytest.CaptureFixture[str]) -> None:
    # ARRANGE
    bundle = str(cli_wiki.wiki.cfg.bundle)

    def run(*args: str) -> str:
        assert cli.main(["--bundle", bundle, *args]) == 0
        return capsys.readouterr().out

    # ACT
    added = run("proposals", "add", "suppliers", "--description", "Who we buy from.", "finance/tea.md", "legal/terms.md")
    other = run("proposals", "add", "vendors", "--description", "Others.", str(cli_wiki.budget.id))
    listing = run("proposals")
    ids = {line.split("/")[1].split(":")[0]: line.split()[0] for line in listing.splitlines()[:2]}
    dismissed = run("proposals", "dismiss", ids["vendors"])
    confirmed = run("proposals", "confirm", ids["suppliers"])
    renamed = run("rename-root", "/suppliers", "partners")
    merged = run("merge-roots", "/partners", "/ops")

    # ASSERT
    assert added.startswith("proposed: ") and "(2 notes)" in added and "vendors" in other
    assert "/suppliers: Who we buy from. (2 notes)" in listing and "2 open proposal(s)" in listing
    assert dismissed.startswith("dismissed: ") and "2 notes moved" in confirmed
    assert "/partners" in renamed and "/ops" in merged
    assert cli_wiki.roots() == ["finance", "legal", "ops"] and cli_wiki.note("Tea").rel.startswith("ops/")
    clean(cli_wiki.wiki)


def test_the_cli_refuses_with_a_message(cli_wiki: Built, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["--bundle", str(cli_wiki.wiki.cfg.bundle), "proposals", "confirm", "nope"])

    assert code == 2 and "no open proposal nope" in capsys.readouterr().err


# -- the HTTP API ----------------------------------------------------------------------------


def test_the_http_api_proposes_lists_confirms_dismisses_renames_and_merges(classic_wiki: Wiki, client: Any) -> None:
    # ARRANGE
    built = Built(classic_wiki)

    # ACT
    made = client.post("/proposals", json={"label": "suppliers", "description": "Who we buy from.", "notes": ["finance/tea.md"]})
    other = client.post("/proposals", json={"label": "vendors", "description": "Others.", "notes": [built.terms.id]})
    listed = client.get("/proposals")
    dismissed = client.post(f"/proposals/{other.json()['id']}/dismiss")
    confirmed = client.post(f"/proposals/{made.json()['id']}/confirm", json={"over_limit": False})
    renamed = client.post("/folder/rename", json={"path": "/suppliers", "name": "partners"})
    merged = client.post("/folder/merge", json={"source": "/partners", "target": "/ops"})

    # ASSERT
    assert made.status_code == 200 and made.json()["status"] == "open" and made.json()["notes"] == [built.tea.id]
    assert [p["label"] for p in listed.json()] == ["suppliers", "vendors"]
    assert dismissed.json()["status"] == "dismissed" and confirmed.json()["moved"] == ["suppliers/tea.md"]
    assert renamed.json()["path"] == "/partners" and merged.json()["path"] == "/ops"
    assert client.get("/proposals").json() == [] and len(client.get("/proposals?status=all").json()) == 2
    assert built.note("Tea").rel == "ops/tea.md"
    clean(classic_wiki)


def test_the_http_api_creates_an_area_within_the_limit_and_over_it_only_when_asked(classic_wiki: Wiki, client: Any) -> None:
    built = Built(classic_wiki)
    classic_wiki.cfg.max_top_folders = len(built.roots()) + 1

    made = client.post("/areas", json={"name": "suppliers", "description": "Who we buy from."})
    refused = client.post("/areas", json={"name": "vendors", "description": "Others."})
    forced = client.post("/areas", json={"name": "vendors", "description": "Others.", "over_limit": True})

    assert made.status_code == 200 and made.json()["path"] == "/suppliers"
    assert refused.status_code == 422 and "max_top_folders" in refused.json()["detail"]
    assert forced.status_code == 200 and "vendors" in built.roots()
    clean(classic_wiki)


def test_the_http_api_answers_422_for_what_the_wiki_refuses(classic_wiki: Wiki, client: Any) -> None:
    Built(classic_wiki)

    bad = client.post("/proposals", json={"label": "finance", "description": "d", "notes": ["finance/tea.md"]})
    missing = client.post("/proposals/nope/confirm", json={})
    merge = client.post("/folder/merge", json={"source": "/ops", "target": "/ops"})

    assert (bad.status_code, missing.status_code, merge.status_code) == (422, 422, 422)
    assert "is taken" in bad.json()["detail"] and "no open proposal nope" in missing.json()["detail"]


# -- scale and concurrency --------------------------------------------------------------------


def test_merge_roots_writes_each_note_once_however_many_there_are(classic_wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE: 300 notes in /a, each linking to the next, and 300 in /c, each linking to one in /a
    store = classic_wiki.store
    root = store.load()
    a, _ = store.create_folder(root, "a", "The first.")
    store.create_folder(root, "b", "The second.")
    c, _ = store.create_folder(root, "c", "Others.")
    count = 300
    for i in range(count):
        link = f"[next](n{(i + 1) % count}.md)"
        store.write_note(a, title=f"N{i}", summary="s", body=f"# Overview\n\n{link}\n", tags=[], source=SOURCE)
        store.write_note(c, title=f"M{i}", summary="s", body=f"# Overview\n\n[a](../a/n{i}.md)\n", tags=[], source=SOURCE)
    saved: list[str] = []
    original = store._save
    monkeypatch.setattr(store, "_save", lambda note: (saved.append(note.rel), original(note))[1])

    # ACT
    classic_wiki.merge_roots("/a", "/b")

    # ASSERT
    assert len(saved) == len(set(saved)) == 2 * count  # the 300 moved notes and the 300 linking ones, once each
    assert sorted(set(saved)) == sorted([f"b/n{i}.md" for i in range(count)] + [f"c/m{i}.md" for i in range(count)])
    clean(classic_wiki)


def test_two_creators_at_once_cannot_pass_the_limit_of_top_level_folders(bundle: Path, llm: FakeLLM) -> None:
    import threading

    # ARRANGE: room for one more
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", max_top_folders=3), llm=llm)
    wiki.init()
    built = Built(wiki)  # three roots already: the limit is 3, so confirms and creations all race for nothing
    wiki.delete_folder("/legal")
    one = wiki.propose_area("one", "First.", ["finance/tea.md"])
    two = wiki.propose_area("two", "Second.", ["ops/runbook.md"])
    barrier = threading.Barrier(4)
    outcomes: list[str] = []

    def attempt(work: Any) -> None:
        barrier.wait()
        try:
            work()
            outcomes.append("ok")
        except InputError:
            outcomes.append("refused")

    # ACT
    threads = [
        threading.Thread(target=attempt, args=(work,))
        for work in (
            lambda: wiki.create_area("x", "X."),
            lambda: wiki.create_area("y", "Y."),
            lambda: wiki.confirm_proposal(one.id),
            lambda: wiki.confirm_proposal(two.id),
        )
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ASSERT
    assert sorted(outcomes) == ["ok", "refused", "refused", "refused"] and len(built.roots()) == 3
