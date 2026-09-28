# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""One wiki document: a YAML frontmatter block between `---` lines, then a Markdown body.

    ---
    type: Note
    title: Refund policy
    ---

    Body text.

A document without a leading `---` line has no frontmatter and is all body.
`type` is the only key every document must carry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from llmw2.errors import WikiError

DELIMITER = "---"
REQUIRED_KEYS = ("type",)


class OKFDocumentError(WikiError, ValueError):
    """The frontmatter is unterminated, not valid YAML, not a mapping, or misses a required key."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps timestamps as the text written (`2026-06-30T14:00:00Z` stays a string).

    PyYAML follows YAML 1.1 and would turn them into `datetime`s, which dump back
    in another form: a parse/serialize round trip would rewrite the frontmatter.
    """


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


@dataclass
class OKFDocument:
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @classmethod
    def parse(cls, text: str) -> OKFDocument:
        lines = text.splitlines()
        if not lines or lines[0].strip() != DELIMITER:
            return cls({}, text)
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == DELIMITER), None)
        if end is None:
            raise OKFDocumentError("Unterminated YAML frontmatter block")
        try:
            frontmatter = yaml.load("\n".join(lines[1:end]), Loader=_Loader) or {}
        except yaml.YAMLError as e:
            raise OKFDocumentError(f"Invalid YAML in frontmatter: {e}") from e
        if not isinstance(frontmatter, dict):
            raise OKFDocumentError("Frontmatter must be a YAML mapping")
        body = "\n".join(lines[end + 1 :])
        return cls(frontmatter, body.removeprefix("\n"))  # the blank line after the block is layout

    def serialize(self) -> str:
        frontmatter = yaml.safe_dump(self.frontmatter, sort_keys=False, allow_unicode=True).rstrip()
        body = self.body if self.body.endswith("\n") else self.body + "\n"
        return f"{DELIMITER}\n{frontmatter}\n{DELIMITER}\n\n{body}"

    def validate(self) -> None:
        if missing := [key for key in REQUIRED_KEYS if not self.frontmatter.get(key)]:
            raise OKFDocumentError(f"Missing required frontmatter keys: {', '.join(missing)}")
