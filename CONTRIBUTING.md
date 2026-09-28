# Contributing

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Licence and CLA

llm-wiki-v2 is licensed under the [GNU AGPL v3.0 or later](LICENSE). Its
owners also offer it under commercial terms, so every contribution is accepted
under the [Contributor License Agreement](CLA.md): you keep the copyright in
your work and give the owners the right to publish it under the AGPL and under
other terms.

On your first pull request a bot asks you to sign by posting one comment:

> I have read the CLA Document and I hereby sign the CLA

It records the signature once; later pull requests pass on their own.

## Development

```bash
uv sync --extra dev            # the versions pinned in uv.lock
uv run ruff check src tests    # lint
uv run mypy                    # strict types
uv run pytest --cov            # tests, failing under 95% coverage
```

CI runs the same checks on Python 3.11 to 3.13. Tests never call a real model:
they use the scripted fakes in `tests/wiki/fakes.py`, and any network access in
a test fails it.

Keep changes in the style of the surrounding code (typed, class-based, one
prompt per file under `src/okf_wiki/agents/prompts/`), with a test for every
behaviour you change. New files start with:

```python
# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later
```
