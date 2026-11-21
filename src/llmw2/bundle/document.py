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

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import yaml

from llmw2.errors import WikiError

DELIMITER = "---"
REQUIRED_KEYS = ("type",)
READ_ATTEMPTS = 6  # about 0.3 s in all: long enough for another process to finish replacing the file


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

if hasattr(yaml, "CSafeLoader"):  # libyaml's parser: the same events, many times faster than the pure-Python one

    class _FastLoader(yaml.CSafeLoader):
        """_Loader on libyaml."""

    _FastLoader.yaml_implicit_resolvers = _Loader.yaml_implicit_resolvers
    _FRONTMATTER_LOADER: type = _FastLoader
else:
    _FRONTMATTER_LOADER = _Loader


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _SHARED_READ = (0x80000000, 0x7, None, 3, 0x80, None)  # GENERIC_READ; share read, write and delete; OPEN_EXISTING; normal
    _INVALID_HANDLE = wintypes.HANDLE(-1).value


def open_note(path: Path) -> IO[bytes]:
    """A file of the wiki open for reading. On Windows it is shared for deleting too (FILE_SHARE_DELETE), as any open file is on POSIX:
    another process may delete or replace it while this one reads it (a purge, a filing), and is never refused because of it."""
    if sys.platform != "win32":
        return open(path, "rb")
    handle = _kernel32.CreateFileW(str(path), *_SHARED_READ)
    if handle in (None, _INVALID_HANDLE):
        error = ctypes.get_last_error()
        raise OSError(0, ctypes.FormatError(error), str(path), error)  # FileNotFoundError, PermissionError... from the Windows error
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)  # type: ignore[attr-defined,unused-ignore]
    except BaseException:
        _kernel32.CloseHandle(handle)
        raise
    try:
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_bytes(path: Path) -> bytes:
    """A file's bytes (`open_note`), waiting a moment for a file another process is replacing (Windows refuses to open it meanwhile)."""
    for attempt in range(READ_ATTEMPTS):
        try:
            with open_note(path) as f:
                return f.read()
        except PermissionError:
            if attempt == READ_ATTEMPTS - 1:
                raise
            time.sleep(0.01 * 2**attempt)
    raise AssertionError("not reached")


@dataclass
class OKFDocument:
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @classmethod
    def read(cls, path: Path) -> OKFDocument:
        """The document in a file; OKFDocumentError for one that is not UTF-8 text (an editor saved it as Latin-1)."""
        data = read_bytes(path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise OKFDocumentError(f"not UTF-8 text (byte {e.start}): save it as UTF-8") from e
        return cls.parse(text.replace("\r\n", "\n").replace("\r", "\n"))  # the line ends `read_text` turned into "\n"

    @classmethod
    def parse(cls, text: str) -> OKFDocument:
        text = text.removeprefix("\ufeff")  # an editor's byte order mark would hide the frontmatter
        lines = text.split("\n")  # not splitlines(): a form feed or U+2028 in the body is text, not a line break
        if lines[0].strip() != DELIMITER:
            return cls({}, text)
        # The closing line starts at column 0: YAML indents every continuation line, so a value holding "---" never ends the block.
        end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == DELIMITER), None)
        if end is None:
            raise OKFDocumentError("Unterminated YAML frontmatter block")
        try:
            frontmatter = yaml.load("\n".join(lines[1:end]), Loader=_FRONTMATTER_LOADER) or {}
        except yaml.YAMLError as e:
            raise OKFDocumentError(f"Invalid YAML in frontmatter: {e}") from e
        if not isinstance(frontmatter, dict):
            raise OKFDocumentError("Frontmatter must be a YAML mapping")
        body = "\n".join(lines[end + 1 :]).removesuffix("\n")  # the file's last line break, as splitlines() drops it
        return cls(frontmatter, body.removeprefix("\n"))  # the blank line after the block is layout

    def serialize(self) -> str:
        frontmatter = yaml.safe_dump(self.frontmatter, sort_keys=False, allow_unicode=True).rstrip()
        body = self.body if self.body.endswith("\n") else self.body + "\n"
        return f"{DELIMITER}\n{frontmatter}\n{DELIMITER}\n\n{body}"

    def validate(self) -> None:
        if missing := [key for key in REQUIRED_KEYS if not self.frontmatter.get(key)]:
            raise OKFDocumentError(f"Missing required frontmatter keys: {', '.join(missing)}")
