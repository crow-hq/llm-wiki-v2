"""A custom `summarize` step that calls the LLM several times: draft, critique, rewrite.

Inside a step anything goes, so one step may spend as many model calls as it likes. They go through
`ctx.llm` and are counted in the usage of the ingest. Everything else in the flow stays as it is.

Needs a model key: set OPENROUTER_API_KEY (or the key of the provider named by OKF_LLM_PROVIDER). It runs in
classic mode, so no classifier is involved.

    python examples/multi_pass_summarize.py [bundle-folder]

Without a folder it uses a temporary one.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from llmw2 import CLASSIC, Candidate, Source, StepContext, Wiki, WikiConfig

TEXT = """Our offices close on 24 December and reopen on 2 January. Urgent matters go to the on-call number on the
intranet. Orders placed during the closure are shipped from 3 January, in the order they arrived."""


def multi_pass(ctx: StepContext, source: Source) -> Candidate:
    system = ctx.prompts.render("librarian/system")
    text = source.text[: ctx.cfg.source_chars]
    draft = ctx.llm.complete(system, f"Write a short note in Markdown about this text:\n\n{text}", op="draft")
    critique = ctx.llm.complete(
        system, f"List what is missing or wrong in this note, given the text.\n\nText:\n{text}\n\nNote:\n{draft}", op="critique"
    )
    final = ctx.llm.complete(
        system, f"Rewrite the note to fix them. Reply with the note only.\n\nProblems:\n{critique}\n\nNote:\n{draft}", op="rewrite"
    )
    title = source.title or final.splitlines()[0].lstrip("# ").strip() or "Untitled"
    summary = " ".join(final.split())[:160]
    return Candidate(title, summary, [], final.strip())


def main(bundle: Path | None = None) -> None:
    bundle = bundle or Path(tempfile.mkdtemp(prefix="llmw2-multipass-"))
    steps = replace(CLASSIC, name="multi-pass", summarize=multi_pass)
    with Wiki(WikiConfig.from_env(bundle=bundle, mode="classic"), steps=steps) as wiki:
        wiki.init()
        result = wiki.ingest(TEXT, title="Winter closure")
        print(result.action, result.note)
        print("calls in this ingest:", result.usage.llm.calls)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
