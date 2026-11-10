# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.search`: the notes that hold what the query quotes (a code in backticks, an identifier) come first."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2 import search as search_module
from llmw2.models.usage import UsageTracker
from llmw2.search import exact_expressions
from tests.wiki.fakes import FakeLLM

PROCESS = "annual review of the invoice process"
FILLER = [(f"Report {n}", f"The report covers the {PROCESS} and the review of every ticket.") for n in range(1, 6)]


def make(tmp_path: Path, notes: list[tuple[str, str]]) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()))
    wiki.init()
    for title, body in notes:
        wiki.store.write_note(
            wiki.store.load(), title=title, summary=f"About {title}", body=body, tags=[], source={"resource": "/raw/x.md"}
        )
    return wiki


def paths(wiki: Wiki, query: str, k: int = 3) -> list[str]:
    return [h.path for h in wiki.search(query, k=k)]


def test_a_code_in_backticks_puts_the_note_that_holds_it_first(tmp_path: Path) -> None:
    wiki = make(tmp_path, [*FILLER, ("Release", f"Run the command sync_all before the {PROCESS}.")])

    assert paths(wiki, f"What does the {PROCESS} say about `sync_all`?", k=2)[0] == "release.md"
    assert paths(wiki, f"What does the {PROCESS} say?", k=2)[0] != "release.md"


def test_a_token_with_a_digit_and_a_hyphen_puts_its_note_first(tmp_path: Path) -> None:
    wiki = make(tmp_path, [*FILLER, ("Ticket", f"Ticket AB-1001 was closed after the {PROCESS}.")])

    assert paths(wiki, f"{PROCESS} for AB-1001", k=2)[0] == "ticket.md"


def test_an_expression_found_in_more_than_three_candidates_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    common = [(f"Memo {n}", f"The {PROCESS} follows the standard v2.0 of the review of every ticket.") for n in range(5)]
    wiki = make(tmp_path, [*common, ("Other", f"The {PROCESS} has no version v3.")])
    query = f"{PROCESS} v2.0"
    boosted = paths(wiki, query, k=4)
    monkeypatch.setattr(search_module, "exact_expressions", lambda q: [])

    assert boosted == paths(wiki, query, k=4)


def test_a_query_with_no_expression_keeps_the_order(tmp_path: Path) -> None:
    wiki = make(tmp_path, FILLER)
    query = "annual review invoice process ticket"

    assert exact_expressions(query) == []
    assert paths(wiki, query, k=4) == paths(wiki, query, k=4) and len(paths(wiki, query, k=4)) == 4


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('is "late fee" charged?', ["late fee"]),
        ("che cosa dice «Tabella oneri»?", ["Tabella oneri"]),
        ("see “net 30” and `a_b`", ["net 30", "a_b"]),
        ("order AB-1001 and getUser at v2.1 or a/b", ["AB-1001", "getUser", "v2.1"]),
        ("plain words only", []),
        ('"ab" is short', []),
        ('the "AB-1001" order, AB-1001', ["AB-1001"]),
    ],
)
def test_exact_expressions(query: str, expected: list[str]) -> None:
    assert exact_expressions(query) == expected
