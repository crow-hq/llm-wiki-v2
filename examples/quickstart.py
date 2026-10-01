"""Ingest a text and ask a question: the basic use of the library.

Needs a model key: set OPENROUTER_API_KEY (or the key of the provider named by OKF_LLM_PROVIDER). In the
default CROW mode the classifier uses the same OpenRouter key; set OKF_MODE=classic to let the LLM decide.

    python examples/quickstart.py [bundle-folder]

Without a folder it uses a temporary one.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from llmw2 import Wiki


def main(bundle: Path | None = None) -> None:
    bundle = bundle or Path(tempfile.mkdtemp(prefix="llmw2-quickstart-"))
    with Wiki.from_env(bundle=bundle) as wiki:
        wiki.init()
        result = wiki.ingest("Acme refunds orders within 30 days of delivery.", title="Refund policy")
        print(result.action, result.note, result.created_folders, result.related)

        answer = wiki.ask("How long do customers have to ask for a refund?")
        print(answer.text)
        print("cited:", answer.citations)
        print("tokens:", wiki.usage.to_dict())


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
