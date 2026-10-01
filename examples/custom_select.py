"""Ask a wiki with your own `select` and `answer` steps: no model, no classifier.

The notes carry `fields` in their frontmatter (written at ingest). A custom `select` filters on them,
so the question is answered by a rule, not by a model. No model is called, so this needs NO API key.

    python examples/custom_select.py [bundle-folder]

Without a folder it uses a temporary one.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from llmw2 import Candidate, Route, Source, StepContext, Steps, Wiki, WikiConfig
from llmw2.bundle.tree import Folder, Note

DEVICES = [
    ("Pocket thermometer", "Reads body temperature in two seconds.", {"status": "active", "year": 2022}),
    ("Desk lamp", "An LED lamp with three brightness levels.", {"status": "retired", "year": 2015}),
    ("Router", "A dual-band wireless router with four ports.", {"status": "active", "year": 2019}),
]


def summarize(ctx: StepContext, source: Source) -> Candidate:
    return Candidate(source.title or "Untitled", source.text, [], source.text, fields=dict(source.fields))


def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    return Route(root, False, [], create=[("devices", "Devices and their status.")])


def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    return None


def relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    return []


def select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
    """The notes whose `fields.status` is "active". The core does not cut this list to max_notes."""
    return [n for n in root.all_notes() if n.frontmatter.get("fields", {}).get("status") == "active"]


def answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
    """A text built from the notes. Citations are the bundle paths in square brackets."""
    lines = [f"- {n.title} ({n.frontmatter['fields']['year']}) [{n.rel}]" for n in notes]
    return "Devices in use:\n" + "\n".join(lines)


STEPS = Steps(name="select-by-field", summarize=summarize, route=route, match=match, relate=relate, select=select, answer=answer)


def main(bundle: Path | None = None) -> Wiki:
    bundle = bundle or Path(tempfile.mkdtemp(prefix="llmw2-select-"))
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), steps=STEPS)
    wiki.init()
    for title, text, fields in DEVICES:
        wiki.ingest(text, title=title, fields=fields)
    result = wiki.ask("Which devices are still in use?")
    print(result.text)
    print("cited:", result.citations)
    print("model calls:", wiki.usage.llm.calls)
    return wiki


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
