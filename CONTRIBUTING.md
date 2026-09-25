# Contributing

Thanks for your interest in llm-wiki-v2.

- **The wiki format** is derived from Google's
  [Open Knowledge Format v0.2](https://github.com/GoogleCloudPlatform/open-knowledge-format)
  and documented in [README.md](README.md#the-wiki-on-disk). Changes to it
  are proposed here; keep every wiki a conformant OKF v0.2 bundle.
- **The LLM wiki** (`src/okf_wiki`, prompts, Docker stack, docs): issues and
  pull requests are welcome here.

Before sending a pull request:

1. Keep changes small and in the style of the surrounding code (typed,
   class-based, one prompt per file under `src/okf_wiki/prompts/`).
2. Add or update tests under `tests/wiki/` for every behaviour you change;
   tests never call a real model (use `tests/wiki/fakes.py`).
3. Run the whole suite: `.venv/bin/pytest` (setup in [README.md](README.md)).
4. New files carry the Apache-2.0 header; the few files that came from
   Google's OKF keep theirs (see [NOTICE](NOTICE)).

This file replaces the upstream CONTRIBUTING.md, which asks for Google's
Contributor License Agreement; that agreement applies to contributions to
Google's repository, not to this one.
