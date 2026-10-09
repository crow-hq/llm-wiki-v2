# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""move_note and describe_folder: links, archives, indexes and `check` after a move."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki
from llmw2.bundle.check import check
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.store import WikiStore
from llmw2.errors import InputError


def build(tmp_path: Path) -> WikiStore:
    store = WikiStore(tmp_path / "w", actor="t")
    store.init()
    (store.root / "raw" / "x.md").write_text(chr(10).join(["---", "type: Source", "title: X", "---", "x", ""]), encoding="utf-8")
    root = store.load()
    a, _ = store.create_folder(root, "a", "Folder a.")
    b, _ = store.create_folder(root, "b", "Folder b.")
    src = {"resource": "/raw/x.md"}
    n1 = store.write_note(a, title="One", summary="s1", body="See [two](two.md#x) and [raw](../raw/x.md).", tags=[], source=src)
    n2 = store.write_note(a, title="Two", summary="s2", body="Body two.", tags=[], source=src)
    store.write_note(b, title="Three", summary="s3", body="See [one](../a/one.md).", tags=[], source=src)
    store.link(n1, n2)
    return store


def test_move_rewrites_links_archive_and_indexes(tmp_path: Path) -> None:
    store = build(tmp_path)
    one = store.load().find("a").note("one")  # type: ignore[union-attr]
    assert one is not None
    store.archive_note(one)
    moved = store.move_note("a/one.md", "/b")
    assert moved.rel == "b/one.md"
    assert not (store.root / "a/one.md").exists()
    assert "(../a/two.md#x)" in moved.body and "(../a/two.md)" in moved.body and "(../raw/x.md)" in moved.body
    assert "(one.md)" in store.read("b/three.md").body and "../a/one.md" not in store.read("b/three.md").body
    assert "(../b/one.md)" in store.read("a/two.md").body
    archived = next((store.root / ".archive").rglob("*.md"))
    assert OKFDocument.parse(archived.read_text(encoding="utf-8")).frontmatter["archived_from"] == "/b/one.md"
    assert check(store.root) == []
    assert "one.md" in (store.root / "b/index.md").read_text(encoding="utf-8")
    assert "one.md" not in (store.root / "a/index.md").read_text(encoding="utf-8")


def test_move_errors_and_unique_name(tmp_path: Path) -> None:
    store = build(tmp_path)
    for rel, folder in [("a/nope.md", "/b"), ("a/index.md", "/b"), ("raw/x.md", "/b"), ("a/one.md", "/zz"), ("a/one.md", "/a")]:
        with pytest.raises(InputError):
            store.move_note(rel, folder)
    store.write_note(store.load().find("b"), title="One", summary="", body="x", tags=[], source={})  # type: ignore[arg-type]
    assert store.move_note("a/one.md", "b").rel == "b/one-2.md"
    assert store.move_note("b/one-2.md", "/").rel == "one-2.md"
    assert check(store.root) == []


def test_describe_folder(tmp_path: Path) -> None:
    store = build(tmp_path)
    assert store.describe_folder("/a", "  New   words ").description == "New words"
    assert store.load().find("a").description == "New words"  # type: ignore[union-attr]
    for rel, text in [("/", "x"), ("/zz", "x"), ("/a", "  ")]:
        with pytest.raises(InputError):
            store.describe_folder(rel, text)
    assert check(store.root) == []


def test_http_move_and_describe(client: Any, classic_wiki: Wiki) -> None:
    classic_wiki.init()
    classic_wiki.create_folder("/", "a", "Folder a.")
    classic_wiki.create_folder("/", "b", "Folder b.")
    note = classic_wiki.store.write_note(
        classic_wiki.tree().find("a"), title="One", summary="s", body="x", tags=[], source={}  # type: ignore[arg-type]
    )
    assert client.post("/note/move", json={"path": "zz.md", "folder": "/b"}).status_code == 404
    assert client.post("/note/move", json={"path": note.rel, "folder": "/zz"}).status_code == 404
    assert client.post("/note/move", json={"path": note.rel, "folder": "/a"}).status_code == 400
    r = client.post("/note/move", json={"path": note.rel, "folder": "/b"})
    assert r.status_code == 200 and r.json()["path"] == "b/one.md"
    r = client.post("/folder/describe", json={"path": "/b", "description": "New  text"})
    assert r.json() == {"path": "/b", "description": "New text"}
    assert client.post("/folder/describe", json={"path": "/zz", "description": "x"}).status_code == 404
    assert client.post("/folder/describe", json={"path": "/b", "description": " "}).status_code == 422
