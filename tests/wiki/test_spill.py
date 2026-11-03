# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A batch planned apart (every window read and planned before any is filed) keeps the text of the documents read in
files of a temporary folder, not in memory, and removes the folder whatever ends the batch."""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Item, ModelError, Origin, Wiki
from llmw2.agents.data import Source
from llmw2.models.usage import UsageTracker
from llmw2.wiki import COUNT_CHARS, PLAN_OPENING, Planned, Read
from tests.wiki.test_crow_plan import filed_all
from tests.wiki.test_stable_plan import GROUPS, WindowsLLM, make

FILLER = "A line that makes the document longer than the opening a plan reads.\n" * 40


def big(n: int) -> Item:
    """As `test_stable_plan.doc`, with a body far longer than an opening."""
    group = GROUPS[min(n // 50, 2)]
    data = f"topic: {group}-{n}\nline of document {n}.\n{FILLER}".encode()
    return Item(f"doc-{n:03d}.md", data=data, origin=Origin("test", "acct", f"doc-{n:03d}"))


def scrub(text: str) -> str:
    """Less what each run makes its own: times, and the ids drawn at random."""
    text = re.sub(r"\d{4}-\d\d-\d\d[T ][\d:.]+(Z|[+-]\d\d:?\d\d)?", "T", text)
    return re.sub(r"\d{8}-\d{6}|(?<=id: )'?[0-9a-f]{12}'?", "X", text)


def digest(wiki: Wiki) -> dict[str, str]:
    """Every file of the bundle by path, with the hash of its content, both less what each run makes its own."""
    out = {}
    for path in sorted(wiki.cfg.bundle.rglob("*")):
        if path.is_file():
            text = scrub(path.read_bytes().decode("utf-8", errors="replace"))
            out[scrub(path.relative_to(wiki.cfg.bundle).as_posix())] = hashlib.sha256(text.encode()).hexdigest()
    return out


@pytest.fixture
def spill_dirs(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The temporary folders `ingest_many` makes."""
    made: list[Path] = []
    real = tempfile.mkdtemp

    def spy(*args: Any, **kwargs: Any) -> str:
        made.append(Path(real(*args, **kwargs)))
        return str(made[-1])

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    return made


def test_after_the_plans_the_reads_hold_only_an_opening_and_the_folder_holds_the_texts(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, spill_dirs: list[Path]
) -> None:
    # ARRANGE: each window as `_file_window` gets it, with the content of the files at that moment
    wiki, _ = make(bundle)
    seen: list[tuple[list[Read], dict[Path, bytes]]] = []
    real = wiki._file_window

    def spy(window: Any, planned: Planned, *args: Any) -> bool:
        reads = [r for r in planned.reads if isinstance(r, Read)]
        seen.append((reads, {r.spilled: r.spilled.read_bytes() for r in reads if r.spilled is not None}))
        return real(window, planned, *args)

    monkeypatch.setattr(wiki, "_file_window", spy)

    # ACT
    report = wiki.ingest_many([big(n) for n in range(120)])

    # ASSERT
    assert filed_all(report, 120) and len(seen) == 3 and len(spill_dirs) == 1
    for reads, held in seen:
        assert len(reads) == len(held) and reads
        for r in reads:
            assert r.source is not None and r.spilled is not None and r.spilled.parent == spill_dirs[0]
            assert len(r.source.text) == max(PLAN_OPENING, COUNT_CHARS)
            whole = held[r.spilled].decode()
            assert whole.startswith(r.source.text) and FILLER in whole


def test_the_tree_the_notes_and_the_raw_copies_are_the_same_with_and_without_the_spill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE
    with_spill, _ = make(tmp_path / "with")
    plain, _ = make(tmp_path / "plain")

    # ACT
    one = with_spill.ingest_many([big(n) for n in range(120)])
    with monkeypatch.context() as m:
        m.setattr(Wiki, "_spill", staticmethod(lambda read, folder: None))
        two = plain.ingest_many([big(n) for n in range(120)])

    # ASSERT
    assert filed_all(one, 120) and filed_all(two, 120)
    assert digest(with_spill) == digest(plain)
    assert any(path.startswith("raw/") for path in digest(with_spill))


def test_a_text_comes_back_from_its_file_whatever_characters_it_has(tmp_path: Path) -> None:
    # ARRANGE: a lone surrogate and Windows line ends
    text = "café \ud800 end\r\nnext\n" + "x" * 2000
    read = Read(None, None, Source(text))  # type: ignore[arg-type]

    # ACT
    Wiki._spill(read, tmp_path)

    # ASSERT
    assert read.spilled is not None and read.source is not None
    assert read.spilled.read_bytes().decode("utf-8", errors="surrogatepass") == text
    assert read.source.text == text[: max(PLAN_OPENING, COUNT_CHARS)]


def test_the_folder_is_gone_when_the_batch_is_done(bundle: Path, spill_dirs: list[Path]) -> None:
    wiki, _ = make(bundle)

    wiki.ingest_many([big(n) for n in range(120)])

    assert len(spill_dirs) == 1 and not spill_dirs[0].exists()


def test_the_folder_is_gone_after_a_stop(bundle: Path, spill_dirs: list[Path]) -> None:
    # ARRANGE: the model fails for good on document 60
    llm = WindowsLLM(UsageTracker())
    llm.fail_on = {"line of document 60.": ModelError("down")}
    wiki, _ = make(bundle, llm)

    # ACT
    report = wiki.ingest_many([big(n) for n in range(120)])

    # ASSERT
    assert report.stopped is not None
    assert len(spill_dirs) == 1 and not spill_dirs[0].exists()


def test_the_folder_is_gone_after_an_exception_while_planning(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, spill_dirs: list[Path]
) -> None:
    # ARRANGE
    wiki, _ = make(bundle)
    real = wiki._plan_window
    calls: list[int] = []

    def plan_window(*args: Any, **kwargs: Any) -> Planned:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(wiki, "_plan_window", plan_window)

    # ACT
    with pytest.raises(RuntimeError, match="boom"):
        wiki.ingest_many([big(n) for n in range(120)])

    # ASSERT
    assert len(spill_dirs) == 1 and not spill_dirs[0].exists()


def test_a_batch_that_is_not_planned_apart_makes_no_folder(bundle: Path, spill_dirs: list[Path]) -> None:
    wiki, _ = make(bundle)

    report = wiki.ingest_many([big(n) for n in range(30)])

    assert filed_all(report, 30) and spill_dirs == []
