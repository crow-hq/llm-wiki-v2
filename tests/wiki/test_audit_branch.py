# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The routing audit proposes a move only to another branch: never deeper into the note's own branch, nor up out of it."""

from __future__ import annotations

from llmw2 import Wiki
from tests.wiki.fakes import FakeClassifier
from tests.wiki.test_id_audit_health import NONE, add_folder, add_note


def _route(classifier: FakeClassifier, *levels: str) -> None:
    """Script a confident flat Choice for the folder at the path `levels`."""
    label = "/" + "/".join(levels)
    classifier.add_choice("route", label, {label: 0.95, NONE: 0.05}, 0.95)


def test_a_route_into_a_subfolder_of_the_note_s_own_folder_is_not_proposed(crow_wiki: Wiki, classifier: FakeClassifier) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    add_folder(crow_wiki, "/alpha", "deeper", "Deeper alpha things.")
    add_note(crow_wiki, "/alpha", "Parent note")
    _route(classifier, "alpha", "deeper")

    assert crow_wiki.audit_routes(min_confidence=0.5) == []


def test_a_note_at_the_root_can_still_be_proposed_into_a_folder(crow_wiki: Wiki, classifier: FakeClassifier) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    add_note(crow_wiki, "/", "Loose note")
    _route(classifier, "alpha")

    proposals = crow_wiki.audit_routes(min_confidence=0.5)

    assert [(p.current, p.proposed) for p in proposals] == [("/", "/alpha")]
