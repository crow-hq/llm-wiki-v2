# Copyright 2026 Federico Cesarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CROW and classic behave the same: given the same decisions, they write the same wiki.

Each scenario scripts the LLM deciders of the classic Librarian and the classifier
deciders of the CROW Librarian to reach the same decisions, with identical writing
replies (summarize, name_folder, merge), and ingests into two separate bundles.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pytest

from okf_wiki import Wiki, WikiConfig
from okf_wiki.librarian import IngestResult
from okf_wiki.store import Folder, WikiStore
from okf_wiki.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM

Mode = Literal["classic", "crow"]
ChoiceReply = tuple[str, dict[str, float], float]

LLM_DECISIONS = {
    "librarian/route",
    "librarian/route_fallback",
    "librarian/find_match",
    "librarian/consolidate",
    "librarian/relate",
}
SOURCE = {
    "text": "Finance handbook, chapter on revenue.",
    "title": "Finance handbook",
    "resource": "https://example.com/handbook",
}
REVENUE = "finance/revenue-recognition.md"

# -- normaliser: mask what legitimately differs between two runs -----------------------------

_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")  # generated.at
_RAW_STAMP = re.compile(r"\b\d{8}-\d{6}-")  # raw/<YYYYMMDD-HHMMSS>-<slug>.md
_LOG_DATE = re.compile(r"^## \d{4}-\d{2}-\d{2}$", re.MULTILINE)
_MODE = re.compile(r" \((?:classic|crow)\)$", re.MULTILINE)  # the ingest log line names the mode


def normalise(text: str) -> str:
    text = _DATETIME.sub("<datetime>", text)
    text = _RAW_STAMP.sub("<stamp>-", text)
    text = _LOG_DATE.sub("## <date>", text)
    return _MODE.sub(" (<mode>)", text)


def snapshot(bundle: Path) -> dict[str, str]:
    """Every file of the bundle, path and content normalised."""
    return {
        normalise(p.relative_to(bundle).as_posix()): normalise(p.read_text(encoding="utf-8"))
        for p in sorted(bundle.rglob("*"))
        if p.is_file()
    }


def test_normaliser_masks_only_run_specific_values() -> None:
    line = "* **Created**: [Deferred revenue](deferred-revenue.md) - from /raw/20260924-101530-handbook.md (crow)"
    masked = "* **Created**: [Deferred revenue](deferred-revenue.md) - from /raw/<stamp>-handbook.md (<mode>)"
    assert normalise(line) == masked
    assert normalise("## 2026-09-24\n  at: '2026-09-24T10:15:30+00:00'") == "## <date>\n  at: '<datetime>'"


# -- scenarios --------------------------------------------------------------------------------


def seed_folders(store: WikiStore) -> Folder:
    root = store.load()
    store.create_folder(root, "people", "Staff, hiring and teams.")
    finance, _ = store.create_folder(root, "finance", "Money matters: revenue, pricing and tax.")
    return finance


def seed_pricing(store: WikiStore) -> None:
    source = {"resource": "https://example.com/pricing", "title": "Pricing page"}
    store.write_note(
        seed_folders(store),
        title="Pricing tiers",
        summary="The three price plans.",
        body="Basic, Pro, Enterprise.",
        tags=["pricing"],
        source=source,
    )


def seed_revenue(store: WikiStore) -> None:
    source = {"resource": "https://example.com/policy", "title": "Revenue policy"}
    store.write_note(
        seed_folders(store),
        title="Revenue recognition",
        summary="When and how revenue is booked.",
        body="Revenue is recognised when the service is delivered.",
        tags=["finance"],
        source=source,
    )


def draft(title: str, summary: str, body: str, *tags: str) -> dict[str, Any]:
    return {"title": title, "summary": summary, "tags": list(tags), "body": body}


REVENUE_DRAFT = draft(
    "Revenue recognition", "When and how revenue is booked.", "Revenue is recognised on delivery.", "Finance"
)

# The same routing decision, taken by each decider.
CLASSIC_TO_FINANCE = [{"action": "descend", "subfolder": "finance"}, {"action": "here"}]
CROW_TO_FINANCE: list[ChoiceReply] = [
    ("finance", {"finance": 0.9, "people": 0.05, "Here": 0.03, "New subfolder": 0.02}, 0.9),
    ("Here", {"Here": 0.9, "New subfolder": 0.05, "None of these": 0.05}, 0.9),
]
SAME_SUBJECT = {'titled "Revenue recognition"': 0.9}  # Noul needle for the seeded note


@dataclass(frozen=True)
class Scenario:
    id: str
    seed: Callable[[WikiStore], None]
    writing: dict[str, list[Any]]  # summarize / name_folder / merge replies, identical in both modes
    classic: dict[str, list[Any]]  # the LLM decisions of the classic Librarian
    choices: dict[str, list[ChoiceReply]]  # the classifier decisions of the CROW Librarian ...
    nouls: dict[str, dict[str, float]]  # ... Choices and Nouls (unlisted Nouls score 0.0)
    expected: tuple[str, str, str, list[str], list[str]]  # action, note, folder, created_folders, related


SCENARIOS = [
    Scenario(
        "new-folder-in-empty-wiki",
        seed=lambda store: None,
        writing={
            "librarian/summarize": [REVENUE_DRAFT],
            "librarian/name_folder": [{"name": "finance", "description": "Money matters: revenue, pricing and tax."}],
        },
        classic={"librarian/route": [{"action": "new"}]},
        choices={"route": [("New subfolder", {"New subfolder": 0.9, "Here": 0.1}, 0.9)]},
        nouls={},
        expected=("created", REVENUE, "/finance", ["/finance"], []),
    ),
    Scenario(
        "note-in-existing-folder",
        seed=seed_pricing,
        writing={"librarian/summarize": [REVENUE_DRAFT]},
        classic={
            "librarian/route": CLASSIC_TO_FINANCE,
            "librarian/find_match": [{"match": None}],
            "librarian/relate": [{"related": []}],
        },
        choices={"route": CROW_TO_FINANCE},
        nouls={},
        expected=("created", REVENUE, "/finance", [], []),
    ),
    Scenario(
        "update-merged-into-note",
        seed=seed_revenue,
        writing={
            "librarian/summarize": [
                draft(
                    "Subscription revenue",
                    "Subscriptions are booked monthly.",
                    "Subscriptions are recognised monthly.",
                    "finance",
                )
            ],
            "librarian/merge": [
                draft(
                    "Revenue recognition",
                    "When and how revenue is booked, subscriptions included.",
                    "Revenue is recognised when the service is delivered.\n\nSubscriptions are recognised monthly.",
                    "finance",
                    "subscriptions",
                )
            ],
        },
        classic={
            "librarian/route": CLASSIC_TO_FINANCE,
            "librarian/find_match": [{"match": REVENUE}],
            "librarian/consolidate": [{"decision": "modify"}],
        },
        choices={"route": CROW_TO_FINANCE, "consolidate": [("Modify", {"Modify": 0.9, "New note": 0.1}, 0.9)]},
        nouls={"match": SAME_SUBJECT},
        expected=("merged", REVENUE, "/finance", [], []),
    ),
    Scenario(
        "related-note-linked",
        seed=seed_revenue,
        writing={
            "librarian/summarize": [
                draft(
                    "Deferred revenue",
                    "Cash received before it is earned.",
                    "Deferred revenue is a liability until delivery.",
                    "finance",
                )
            ]
        },
        classic={
            "librarian/route": CLASSIC_TO_FINANCE,
            "librarian/find_match": [{"match": REVENUE}],
            "librarian/consolidate": [{"decision": "new"}],
            "librarian/relate": [{"related": [REVENUE]}],
        },
        choices={"route": CROW_TO_FINANCE, "consolidate": [("New note", {"Modify": 0.1, "New note": 0.9}, 0.9)]},
        nouls={"match": SAME_SUBJECT, "relate": SAME_SUBJECT},
        expected=("created", "finance/deferred-revenue.md", "/finance", [], [REVENUE]),
    ),
]


@dataclass
class Run:
    result: IngestResult
    files: dict[str, str]
    llm: FakeLLM
    classifier: FakeClassifier


def run(tmp_path: Path, mode: Mode, scenario: Scenario) -> Run:
    """Seed a fresh bundle, ingest the source in `mode`, and check every scripted reply was used."""
    tracker = UsageTracker()
    decisions = scenario.classic if mode == "classic" else {}
    llm = FakeLLM(tracker, {**scenario.writing, **decisions})
    classifier = FakeClassifier(tracker, choices=scenario.choices, nouls=scenario.nouls)
    bundle = tmp_path / mode
    wiki = Wiki(WikiConfig(bundle=bundle, mode=mode), llm=llm, classifier=classifier)
    wiki.init()
    scenario.seed(wiki.store)

    result = wiki.ingest(SOURCE["text"], title=SOURCE["title"], resource=SOURCE["resource"])

    assert not any(llm.replies.values()), f"{mode}: unused LLM replies {llm.replies}"
    if mode == "crow":
        assert not any(classifier.choices.values()), f"crow: unused choices {classifier.choices}"
    return Run(result, snapshot(bundle), llm, classifier)


def outcome(result: IngestResult) -> tuple[str, str, str, list[str], list[str]]:
    return result.action, result.note, result.folder, result.created_folders, result.related


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_both_modes_report_the_same_ingest(tmp_path: Path, scenario: Scenario) -> None:
    classic, crow = run(tmp_path, "classic", scenario), run(tmp_path, "crow", scenario)

    assert outcome(classic.result) == outcome(crow.result) == scenario.expected


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_both_modes_write_the_same_files(tmp_path: Path, scenario: Scenario) -> None:
    classic, crow = run(tmp_path, "classic", scenario), run(tmp_path, "crow", scenario)

    assert set(classic.files) == set(crow.files)
    for path, text in classic.files.items():
        assert crow.files[path] == text, path


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_classic_mode_never_calls_the_classifier(tmp_path: Path, scenario: Scenario) -> None:
    classic = run(tmp_path, "classic", scenario)

    assert classic.classifier.calls == []
    assert classic.result.usage.classifier.calls == 0


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_crow_mode_takes_no_decision_with_the_llm(tmp_path: Path, scenario: Scenario) -> None:
    classic, crow = run(tmp_path, "classic", scenario), run(tmp_path, "crow", scenario)

    assert not LLM_DECISIONS & set(crow.llm.ops)
    assert crow.llm.ops == [op for op in classic.llm.ops if op not in LLM_DECISIONS]  # the same writing calls
    assert not any(d.fallback or d.decider == "llm" for d in crow.result.decisions)
