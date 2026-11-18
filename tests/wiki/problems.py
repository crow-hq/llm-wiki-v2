# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A helper of the tests that ask `check` whether a wiki is sound."""

from __future__ import annotations

from collections.abc import Iterable

from llmw2.bundle.check import ERROR, Problem


def errors(found: Iterable[Problem]) -> list[Problem]:
    """The problems of `check` that are errors. The others are warnings, marks of a wiki's history (a note typed in by hand has
    no line in the log, a See-also entry is on one side only), which a wiki built by the test's own calls on the store has."""
    return [p for p in found if p.severity == ERROR]
