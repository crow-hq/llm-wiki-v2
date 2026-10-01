"""Load a labelled dataset into a wiki with no model: the labels become the folders.

Shows custom `summarize` and `route` steps that read `Source.fields`, and `Route.create`,
which names the whole folder path so the core opens it level by level. No model is called,
so this needs NO API key.

    python examples/labeled_dataset.py [bundle-folder]

Without a folder it uses a temporary one.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from llmw2 import Candidate, Route, Source, StepContext, Steps, Wiki, WikiConfig
from llmw2.bundle.tree import Folder, Note

DATASET = Path(__file__).with_name("labeled_dataset.jsonl")


def summarize(ctx: StepContext, source: Source) -> Candidate:
    """The row is already the note: no model writes it. The label travels as a field."""
    summary = source.text.split(". ")[0].rstrip(".") + "."
    return Candidate(source.title or "Untitled", summary, [], source.text, fields=dict(source.fields))


def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    """File the note under its category path, e.g. Animals / Mammals / Dogs, creating what is missing."""
    path: list[str] = ctx.source.fields["category"] if ctx.source else []
    levels = [(name, f"Notes labelled {' / '.join(path[: i + 1])}.") for i, name in enumerate(path)]
    return Route(root, False, [], create=levels)


def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    """Every row is its own note: never merge."""
    return None


def relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    return []


STEPS = Steps(name="labeled-dataset", summarize=summarize, route=route, match=match, relate=relate)


def main(bundle: Path | None = None) -> Wiki:
    bundle = bundle or Path(tempfile.mkdtemp(prefix="llmw2-labeled-"))
    # Classic mode needs no classifier, and no key as long as no step calls a model.
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), steps=STEPS)
    wiki.init()
    for line in DATASET.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        result = wiki.ingest(row["text"], title=row["title"], fields={"category": row["category"]})
        print(f"{result.action:8} {result.note}")
    for folder in wiki.tree().walk():
        if not folder.is_root:
            print("  " * (folder.depth - 1) + folder.name + "/", f"({len(folder.notes)} notes)")
    print("model calls:", wiki.usage.llm.calls)
    return wiki


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
