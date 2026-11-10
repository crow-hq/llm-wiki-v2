# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A classic wiki whose steps never call a model: the document names its folder, and every note it can see is related.

A document starts with "@/red " (the folder it goes to) and "[Title] " (the title of its note); its summary is the first line,
its body the text.
`relate` returns every candidate, and the candidates of `route` are all the notes of the tree, so a link between areas is
there to be kept or cut by the library.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from llmw2 import Wiki, WikiConfig
from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.agents.steps import CLASSIC, StepContext
from llmw2.bundle.tree import Folder, Note, see_also_targets
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM


def summarize(ctx: StepContext, source: Source) -> Candidate:
    text = re.sub(r"^@\S+\s+", "", source.text)
    title = re.match(r"\[([^\]]*)\]\s+", text)  # "[Title] text": the title of the note, apart from the document's own
    text = text[title.end() :] if title else text
    return Candidate(title[1] if title else source.title or "Memo", text.splitlines()[0][:80] if text else "A memo.", [], text)


def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    assert ctx.source is not None
    target = re.match(r"@(\S+)", ctx.source.text)
    assert target is not None, ctx.source.text
    folder = root.find(target[1])
    assert folder is not None, target[1]
    return Route(folder, False, [f for f in root.walk() if not f.is_root])


def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    return None


def relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    return list(others)


def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
    return NoteDraft(title=note.title, summary=note.summary, body=source.text)


STEPS = replace(CLASSIC, name="areas", summarize=summarize, route=route, match=match, relate=relate, merge=merge)


def make(bundle: Path, folders: tuple[str, ...] = ("red", "blue"), **cfg: Any) -> Wiki:
    """A wiki with the given top-level folders; `cfg` is laid over the config."""
    config = WikiConfig(bundle=bundle, mode="classic", max_links=8, **cfg)
    wiki = Wiki(config, llm=FakeLLM(UsageTracker()), steps=STEPS)
    wiki.init()
    for name in folders:
        wiki.create_folder("/", name, f"{name.capitalize()} things.")
    return wiki


def file(wiki: Wiki, folder: str, title: str, text: str = "Something to say.", **kwargs: Any) -> str:
    """File a document in `folder` ("/red"); the path of its note."""
    return wiki.ingest(f"@{folder} [{title}] {text}", title="document", **kwargs).note


def linked(wiki: Wiki, rel: str) -> list[str]:
    """The notes a note links to in its See-also list, as bundle paths."""
    return sorted(see_also_targets(wiki.store.read(rel).body, rel))
