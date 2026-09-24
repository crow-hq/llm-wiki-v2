# Contributing

Thanks for your interest in llm-wiki-v2.

- **The Open Knowledge Format itself** ([`SPEC.md`](SPEC.md)) is maintained
  by Google at
  [GoogleCloudPlatform/open-knowledge-format](https://github.com/GoogleCloudPlatform/open-knowledge-format).
  Propose format changes there; this repository follows the upstream spec.
- **The LLM wiki** (`src/okf_wiki`, prompts, Docker stack, docs): issues and
  pull requests are welcome here.

Before sending a pull request:

1. Keep changes small and in the style of the surrounding code (typed,
   class-based, one prompt per file under `src/okf_wiki/prompts/`).
2. Add or update tests under `tests/wiki/` for every behaviour you change;
   tests never call a real model (use `tests/wiki/fakes.py`).
3. Run the whole suite: `.venv/bin/pytest` (setup in [README.md](README.md)).
4. New files carry the Apache-2.0 header; files that came from Google's OKF
   keep theirs (see [NOTICE](NOTICE)).

This file replaces the upstream CONTRIBUTING.md, which asks for Google's
Contributor License Agreement; that agreement applies to contributions to
Google's repository, not to this one.
