# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Prompt files: `prompts/<role>/<name>.md` with `{{placeholders}}`.

`shared/*.md` fragments can be embedded anywhere as `{{shared/<name>}}`. A
directory passed as `override_dir` (env `OKF_PROMPTS_DIR`) wins file by file.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

_PLACEHOLDER = re.compile(r"\{\{\s*([\w/]+)\s*\}\}")
_PACKAGE = resources.files("okf_wiki.agents") / "prompts"


class Prompts:
    def __init__(self, override_dir: Path | None = None) -> None:
        self.override_dir = override_dir

    def load(self, name: str, /) -> str:
        if self.override_dir and (path := self.override_dir / f"{name}.md").is_file():
            return path.read_text(encoding="utf-8")
        resource = _PACKAGE.joinpath(*f"{name}.md".split("/"))
        if not resource.is_file():
            raise FileNotFoundError(f"no prompt named {name!r}")
        return resource.read_text(encoding="utf-8")

    def render(self, name: str, /, **values: object) -> str:
        """Fill `{{var}}` from `values` and `{{shared/x}}` from shared fragments; a missing value raises."""

        def fill(match: re.Match[str]) -> str:
            key = match.group(1)
            if key.startswith("shared/"):  # fragments may use the prompt's own placeholders
                return _PLACEHOLDER.sub(fill, self.load(key).strip())
            if key not in values:
                raise KeyError(f"prompt {name!r} needs {{{{{key}}}}}")
            return str(values[key])

        return _PLACEHOLDER.sub(fill, self.load(name)).strip()

    def render_sections(self, name: str, /, **values: object) -> dict[str, str]:
        """Split a rendered prompt on `## <key>` headings: {"instructions": …, "Here": …}.

        Used by classifier prompts, where one file holds a question and its options.
        """
        sections: dict[str, str] = {}
        key = ""
        for line in self.render(name, **values).splitlines():
            if line.startswith("## "):
                key = line[3:].strip()
                sections[key] = ""
            elif key:
                sections[key] = f"{sections[key]}\n{line}".strip()
        return sections

    def placeholders(self, name: str) -> set[str]:
        """Values `render` needs, including those used inside embedded shared fragments."""
        keys = set(_PLACEHOLDER.findall(self.load(name)))
        shared = {k for k in keys if k.startswith("shared/")}
        return (keys - shared).union(*(self.placeholders(k) for k in shared))

    @staticmethod
    def names() -> list[str]:
        return sorted(
            f"{role.name}/{file.name[:-3]}"
            for role in _PACKAGE.iterdir()
            if role.is_dir()
            for file in role.iterdir()
            if file.name.endswith(".md")
        )
