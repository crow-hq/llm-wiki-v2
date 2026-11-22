# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The search index on disk (`llmw2.searchindex`): the same ranking as `lexical.Bm25`, a file that can be lost, damaged or shared by
many processes and still gives the right answer, and a note that changed costs its own rows."""

from __future__ import annotations

import os
import random
import sqlite3
import subprocess
import sys
import textwrap
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

import pytest

import llmw2
from llmw2 import Wiki, WikiConfig
from llmw2 import searchindex as si
from llmw2.agents import lexical
from llmw2.bundle.store import RACY_NS
from llmw2.models.usage import UsageTracker
from llmw2.search import NoteSearch, _text_of
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_search import make, paths

VOCAB = [f"word{i}" for i in range(40)] + ["alpha", "beta", "gamma", "delta", "tiger", "river", "market", "garden"]


def _index_file(tmp_path: Path) -> Path:
    return tmp_path / "wiki" / si.INDEX_FILE


def _age(wiki_dir: Path) -> None:
    """Make every note old enough to be trusted by its time and size."""
    old = time.time_ns() - 3 * RACY_NS
    for f in wiki_dir.rglob("*.md"):
        os.utime(f, ns=(old, old))


def _rows(path: Path) -> tuple[dict[str, tuple[int, str]], set[tuple[str, str, int]]]:
    """What a file of the index holds: {note: (length, hash)} and the postings as (word, note, count)."""
    with closing(sqlite3.connect(path)) as db:
        docs = {rel: (n, h) for rel, n, h in db.execute("SELECT rel, len, hash FROM docs WHERE bad = 0")}
        posts = set(db.execute("SELECT p.word, d.rel, p.f FROM postings p JOIN docs d ON d.id = p.doc"))
    return docs, posts


def _fresh_rows(wiki: Wiki) -> tuple[dict[str, tuple[int, str]], set[tuple[str, str, int]]]:
    """The same, from a new index in memory built from the files alone."""
    fresh = NoteSearch(None)
    fresh.index.sync(wiki.store, fresh._entry)
    assert fresh.index._mem is not None
    db = fresh.index._mem
    docs = {rel: (n, h) for rel, n, h in db.execute("SELECT rel, len, hash FROM docs WHERE bad = 0")}
    posts = set(db.execute("SELECT p.word, d.rel, p.f FROM postings p JOIN docs d ON d.id = p.doc"))
    return docs, posts


def _random_wiki(tmp_path: Path, n: int, seed: int = 1) -> Wiki:
    rng = random.Random(seed)
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()))
    wiki.init()
    for folder in ("ops", "hr"):
        wiki.store.create_folder(wiki.store.load(), folder, folder)
    for i in range(n):
        root = wiki.store.load()
        target = next(f for f in root.subfolders if f.rel == ("ops", "hr")[i % 2])
        body = " ".join(rng.choice(VOCAB) for _ in range(rng.randint(5, 40)))
        wiki.store.write_note(
            target, title=f"Note {i}", summary=" ".join(rng.sample(VOCAB, 3)), body=body, tags=[], source={"resource": "/raw/x.md"},
            fields={"team": target.rel},
        )  # fmt: skip
    _age(tmp_path / "wiki")
    return wiki


def _expected(wiki: Wiki, query: str, k: int, within: list[str] | None = None) -> list[str]:
    """The ranking of `lexical.Bm25` on the notes, in the order of the tree."""
    notes = wiki.store.load().all_notes()
    index = lexical.Bm25({n.rel: Counter(lexical.words(_text_of(n))) for n in notes})
    keys = [n.rel for n in notes] if within is None else within
    scores = index.scores(query, keys)
    return sorted((r for r in keys if scores[r] > 0), key=lambda r: -scores[r])[:k]


def test_ranking_is_the_one_of_bm25_over_the_whole_wiki_and_over_a_part(tmp_path: Path) -> None:
    wiki = _random_wiki(tmp_path, 60)
    rng = random.Random(7)
    ops = [n.rel for n in wiki.store.load().all_notes() if n.rel.startswith("ops/")]
    for _ in range(40):
        q = " ".join(rng.sample(VOCAB, rng.randint(1, 4)))
        assert paths(wiki.search(q, k=8)) == _expected(wiki, q, 8)
        got = wiki._search.find(wiki.store, q, 8, {"team": "ops"})
        assert [rel for rel, _, _ in got] == _expected(wiki, q, 8, ops)


def test_ranking_follows_edits_new_and_deleted_notes(tmp_path: Path) -> None:
    wiki = _random_wiki(tmp_path, 40)
    rng = random.Random(3)
    wiki.search("alpha")
    for step in range(15):
        notes = wiki.store.load().all_notes()
        victim = rng.choice(notes)
        action = step % 3
        if action == 0:
            extra = " ".join(rng.choices(VOCAB, k=20))
            victim.path.write_text(victim.path.read_text(encoding="utf-8") + "\n" + extra + "\n", encoding="utf-8")
        elif action == 1:
            victim.path.unlink()
        else:
            wiki.store.write_note(
                wiki.store.load().subfolders[0],
                title=f"Fresh {step}",
                summary="fresh",
                body=" ".join(rng.choices(VOCAB, k=15)),
                tags=[],
                source={"resource": "/raw/y.md"},
            )
        _age(tmp_path / "wiki")
        for _ in range(3):
            q = " ".join(rng.sample(VOCAB, 3))
            assert paths(wiki.search(q, k=8)) == _expected(wiki, q, 8)
    assert _rows(_index_file(tmp_path)) == _fresh_rows(wiki)


def test_files_changed_while_no_process_ran_are_seen_by_the_next_one(tmp_path: Path) -> None:
    """The file of the index says what each note was; a note edited, added or deleted by hand in between is found out by its time
    and size when the next process opens the wiki."""
    wiki = _random_wiki(tmp_path, 40)
    wiki.search("alpha")
    notes = wiki.store.load().all_notes()
    edited, deleted = notes[3], notes[5]
    edited.path.write_text(edited.path.read_text(encoding="utf-8") + "\nzzedited zzedited zzedited\n", encoding="utf-8")
    deleted.path.unlink()
    added = edited.path.parent / "by-hand.md"
    added.write_text("---\ntype: Note\ntitle: By hand\ndescription: d\n---\nzzadded zzadded river\n", encoding="utf-8")
    _age(tmp_path / "wiki")
    fresh = Wiki(wiki.cfg, llm=wiki.llm)  # a new process, in effect: nothing in memory

    assert paths(fresh.search("zzedited", k=3))[:1] == [edited.rel]
    assert paths(fresh.search("zzadded", k=3))[:1] == [added.relative_to(tmp_path / "wiki").as_posix()]
    for q in ("alpha river", "beta tiger market", "word3 word7"):
        assert paths(fresh.search(q, k=8)) == _expected(fresh, q, 8)
    assert _rows(_index_file(tmp_path)) == _fresh_rows(fresh)


def test_a_changed_note_writes_only_its_own_rows(tmp_path: Path) -> None:
    wiki = _random_wiki(tmp_path, 30)
    wiki.search("alpha")
    before_docs, before_posts = _rows(_index_file(tmp_path))
    target = next(n for n in wiki.store.load().all_notes() if n.rel.startswith("ops/"))
    target.path.write_text(target.path.read_text(encoding="utf-8") + "\nuniqueword uniqueword\n", encoding="utf-8")
    _age(tmp_path / "wiki")

    assert paths(wiki.search("uniqueword", k=1)) == [target.rel]

    docs, posts = _rows(_index_file(tmp_path))
    assert {r for r in docs if docs[r] != before_docs.get(r)} == {target.rel}
    changed = {row for row in before_posts ^ posts}
    assert changed and all(rel == target.rel for _, rel, _ in changed)  # no row of another note was touched
    assert len(changed) < 10  # the rows of the words whose count changed, not the note's rows again
    assert (docs, posts) == _fresh_rows(wiki)


def test_the_index_is_a_cache_and_comes_back_after_it_is_deleted(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    assert paths(wiki.search("refund within thirty days", k=1)) == ["refunds.md"]
    assert _index_file(tmp_path).exists()

    # by another process, while this one holds its memory of the file
    wiki._search.index.close()  # the connection is kept between searches; Windows lets nobody delete or rewrite a file held open
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_index_file(tmp_path)}{suffix}").unlink(missing_ok=True)
    assert paths(wiki.search("refund within thirty days", k=1)) == ["refunds.md"]  # the same object

    wiki._search.index.close()  # the connection is kept between searches; Windows lets nobody delete or rewrite a file held open
    _index_file(tmp_path).unlink()
    assert paths(Wiki(wiki.cfg, llm=wiki.llm).search("parcels road", k=1)) == ["shipping.md"]  # a new one
    assert _index_file(tmp_path).exists()


def test_a_wiki_of_an_older_version_gets_an_index_and_nothing_else_changes(tmp_path: Path) -> None:
    """A wiki with no search index (v1.0.2 never wrote one): opening it adds the file under `.llmw2/cache/` and touches no note."""
    wiki = make(tmp_path)
    root = tmp_path / "wiki"
    wiki._search.index.close()  # the connection is kept between searches; Windows lets nobody delete or rewrite a file held open
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_index_file(tmp_path)}{suffix}").unlink(missing_ok=True)
    (root / ".llmw2" / "cache").mkdir(parents=True, exist_ok=True)
    (root / ".llmw2" / "cache" / "classifier.sqlite").write_bytes(b"left alone")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    hits = paths(Wiki(wiki.cfg, llm=wiki.llm).search("passwords quarter", k=2))

    assert hits[0] == "security.md"
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert {p for p in after if p not in before} <= {p for p in after if p.name.startswith("search-v") or p.name == ".gitignore"}
    assert all(after[p] == before[p] for p in before)  # no note, no index.md, no other cache was written


@pytest.mark.parametrize("how", ["garbage", "truncated", "zeroed-middle", "wrong-schema", "wrong-totals"])
def test_a_damaged_file_is_recognized_and_built_again(tmp_path: Path, how: str) -> None:
    wiki = _random_wiki(tmp_path, 50)
    q = "alpha beta tiger"
    want = _expected(wiki, q, 8)
    assert paths(wiki.search(q, k=8)) == want
    path = _index_file(tmp_path)
    wiki._search.index.close()  # the connection is kept between searches; Windows lets nobody delete or rewrite a file held open
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    data = path.read_bytes()
    if how == "garbage":
        path.write_bytes(os.urandom(len(data)))
    elif how == "truncated":
        path.write_bytes(data[: len(data) // 3])
    elif how == "zeroed-middle":
        mid = (len(data) // si.PAGE_SIZE // 2) * si.PAGE_SIZE
        path.write_bytes(data[:mid] + bytes(si.PAGE_SIZE * 2) + data[mid + si.PAGE_SIZE * 2 :])
    elif how == "wrong-schema":
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA user_version = 99")
    else:  # a file that is a database but does not add up
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE meta SET v = v + 5 WHERE k = 'total'")
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)

    again = Wiki(wiki.cfg, llm=wiki.llm)

    assert paths(again.search(q, k=8)) == want
    assert paths(again.search(q, k=8)) == want
    assert _rows(path) == _fresh_rows(wiki)


def test_a_file_damaged_under_a_running_process_is_built_again(tmp_path: Path) -> None:
    wiki = _random_wiki(tmp_path, 30)
    q = "alpha river"
    want = _expected(wiki, q, 8)
    assert paths(wiki.search(q, k=8)) == want
    path = _index_file(tmp_path)
    wiki._search.index.close()  # the connection is kept between searches; Windows lets nobody delete or rewrite a file held open
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    wiki._search.index._hot.clear()  # the postings are asked for again from the file
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    path.write_bytes(b"not a database at all" * 1000)

    assert paths(wiki.search(q, k=8)) == want
    assert _rows(path) == _fresh_rows(wiki)


def test_damage_found_while_ranking_is_repaired_from_the_first_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The file turns out to be damaged at the last step of a search, after the sync: the state that was just thrown away must not
    answer (an empty result), the search starts again on a file made again."""
    wiki = _random_wiki(tmp_path, 30)
    q = "alpha river"
    want = _expected(wiki, q, 8)
    assert paths(wiki.search(q, k=8)) == want
    index = wiki._search.index
    real, calls = index._rank, []

    def damaged(*args):  # type: ignore[no-untyped-def]
        if not calls:
            calls.append(1)
            index._close_held()
            _index_file(tmp_path).write_bytes(b"x" * 5000)
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real(*args)

    monkeypatch.setattr(index, "_rank", damaged)

    assert paths(wiki.search(q, k=8)) == want
    assert _rows(_index_file(tmp_path)) == _fresh_rows(wiki)


def test_a_write_by_another_process_while_answering_starts_the_search_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Another process commits between this one's sync and its reading of the postings: the postings may name notes this one does not
    know, so the search starts again from the sync and answers with the new note."""
    wiki = _random_wiki(tmp_path, 30)
    other = Wiki(wiki.cfg, llm=wiki.llm)
    wiki.search("alpha")
    index = wiki._search.index
    index._hot.clear()
    index._hot_n = 0
    real, late = index._postings, []

    def racing(words):  # type: ignore[no-untyped-def]
        if not late:
            note = other.store.write_note(
                other.store.load().subfolders[0], title="Late", summary="s", body="zzlate alpha", tags=[], source={"resource": "/raw/l.md"}
            )
            late.append(note.rel)
            other.search("zzlate")  # the other process indexes it: the generation of the file moves
        return real(words)

    monkeypatch.setattr(index, "_postings", racing)

    assert paths(wiki.search("zzlate alpha", k=3))[:1] == late


def test_a_file_that_cannot_be_written_leaves_the_search_working_in_memory(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    cache = tmp_path / "wiki" / ".llmw2" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    for f in cache.glob("search-v*.sqlite*"):
        f.unlink()
    if not any(cache.iterdir()):
        cache.rmdir()
    (tmp_path / "wiki" / ".llmw2" / "cache").write_bytes(b"a file where the folder should be")

    assert paths(Wiki(wiki.cfg, llm=wiki.llm).search("refund thirty days", k=1)) == ["refunds.md"]


def test_a_bad_file_in_the_wiki_is_left_out_and_not_read_again(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    broken = tmp_path / "wiki" / "broken.md"
    broken.write_text("---\ntype: Note\n: : :\n---\nrefund refund refund refund\n", encoding="utf-8")
    _age(tmp_path / "wiki")

    assert "broken.md" not in paths(wiki.search("refund", k=8))
    assert "broken.md" not in paths(Wiki(wiki.cfg, llm=wiki.llm).search("refund", k=8))
    broken.write_text(broken.read_text(encoding="utf-8") + "more\n", encoding="utf-8")  # still not a note
    _age(tmp_path / "wiki")
    assert "broken.md" not in paths(wiki.search("refund", k=8))


def test_the_index_never_loads_a_pickle() -> None:
    source = Path(si.__file__).read_text(encoding="utf-8")
    assert "pickle" not in source.replace("no `pickle`", "") and "marshal" not in source


WORKER = textwrap.dedent(
    """
    import random, sys
    from pathlib import Path
    from llmw2 import Wiki, WikiConfig
    from llmw2.models.usage import UsageTracker
    from tests.wiki.fakes import FakeLLM
    me, root = int(sys.argv[1]), Path(sys.argv[2])
    wiki = Wiki(WikiConfig(bundle=root, mode="classic"), llm=FakeLLM(UsageTracker()))
    rng = random.Random(me)
    mine = []
    for step in range(6):
        wiki.search("alpha river market", k=8)
        token = f"zq{me}x{step}"
        with wiki.store.lock():  # writers take turns, as the CLI and the server do
            note = wiki.store.write_note(wiki.store.load(), title=f"Worker {me} {step}", summary="w", body=f"{token} alpha river " + token,
                                         tags=[], source={"resource": "/raw/w.md"})
        mine.append((note, token))
        got = [h.path for h in wiki.search(token, k=3)]
        assert got[:1] == [note.rel], (token, got)
        if step % 3 == 2:
            old, tok = mine.pop(0)
            wiki.delete_note(old.rel)  # takes the write lock itself
            assert old.rel not in [h.path for h in wiki.search(tok, k=8)]
    print("ok")
    """
)


def test_thirty_processes_on_one_wiki_leave_no_half_state(tmp_path: Path) -> None:
    wiki = _random_wiki(tmp_path, 30)
    wiki.search("alpha")
    script = tmp_path / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    parts = [str(Path(llmw2.__file__).parents[1]), str(Path(__file__).parents[2]), os.environ.get("PYTHONPATH", "")]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(parts))
    command = [sys.executable, str(script)]
    procs = [
        subprocess.Popen([*command, str(i), str(tmp_path / "wiki")], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(30)
    ]
    results = [(*p.communicate(timeout=600), p.returncode) for p in procs]
    assert [(r[1][-1500:], r[2]) for r in results if r[2] != 0] == []
    assert all(out.strip() == "ok" for out, _, _ in results)

    time.sleep(0.1)
    _age(tmp_path / "wiki")
    check = Wiki(wiki.cfg, llm=wiki.llm)
    check.search("alpha")
    path = _index_file(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        total = db.execute("SELECT SUM(len) FROM docs").fetchone()[0]
        assert int(db.execute("SELECT v FROM meta WHERE k = 'total'").fetchone()[0]) == total
        assert db.execute("SELECT COUNT(*) FROM docs d LEFT JOIN docbody b ON b.id = d.id WHERE b.id IS NULL").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM postings p LEFT JOIN docs d ON d.id = p.doc WHERE d.id IS NULL").fetchone()[0] == 0
    assert _rows(path) == _fresh_rows(check)


def test_the_schema_and_tokenizer_are_in_the_file(tmp_path: Path) -> None:
    make(tmp_path).search("refund")
    with closing(sqlite3.connect(_index_file(tmp_path))) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == si.SCHEMA
        assert db.execute("SELECT v FROM meta WHERE k = 'tokens'").fetchone()[0] == lexical._WORD.pattern
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def _owners_wiki(tmp_path: Path, embedder: object = None) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()), embedder=embedder)  # type: ignore[arg-type]
    wiki.init()
    for i in range(6):
        wiki.store.write_note(
            wiki.store.load(), title=f"Note {i}", summary="pricing", body=f"pricing body{i}", tags=[],
            source={"resource": "/raw/x.md"}, fields={"owner": "ana" if i % 2 else "bo"},
        )  # fmt: skip
    _age(tmp_path / "wiki")
    return wiki


@pytest.mark.parametrize("with_embedder", [False, True])
def test_an_old_tree_given_to_the_researchers_path_does_not_hide_a_new_note(tmp_path: Path, with_embedder: bool) -> None:
    """A tree loaded before a note was written, then two searches, then the researcher's path (`NoteSearch.search`) with that old tree:
    the new note stays in the results of `Wiki.search` (with and without `where`) and nothing raises."""
    from tests.wiki.test_search import Embedder as FakeEmbedder

    wiki = _owners_wiki(tmp_path, FakeEmbedder({"pricing": [1.0, 0, 0]}) if with_embedder else None)
    old_tree = wiki.store.load()
    note = wiki.store.write_note(
        wiki.store.load(), title="Fresh zorblax", summary="pricing", body="zorblax pricing", tags=[],
        source={"resource": "/raw/y.md"}, fields={"owner": "carla"},
    )  # fmt: skip
    _age(tmp_path / "wiki")
    assert paths(wiki.search("zorblax", 8))[:1] == [note.rel]
    assert paths(wiki.search("zorblax", 8, {"owner": "carla"})) == [note.rel]
    wiki.search("zorblax", 8)
    wiki._search.search(old_tree.all_notes(), old_tree.all_notes(), "pricing", 3)

    assert paths(wiki.search("zorblax", 8))[:1] == [note.rel]
    assert paths(wiki.search("zorblax", 8, {"owner": "carla"})) == [note.rel]


def test_an_outside_edit_of_the_fields_alone_is_seen_by_where(tmp_path: Path) -> None:
    wiki = _owners_wiki(tmp_path)
    assert paths(wiki.search("pricing", 8, {"owner": "zed"})) == []
    target = tmp_path / "wiki" / "note-0.md"
    target.write_text(target.read_text(encoding="utf-8").replace("owner: bo", "owner: zed"), encoding="utf-8")

    assert paths(wiki.search("pricing", 8, {"owner": "zed"})) == ["note-0.md"]


def _same_tick_rewrite(path: Path, old: bytes, new: bytes) -> None:
    """Rewrite a file with the same size and the same modification time (two writes in one clock tick)."""
    before = path.stat()
    path.write_bytes(path.read_bytes().replace(old, new))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size


def _target_wiki(tmp_path: Path) -> tuple[Wiki, Path]:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()))
    wiki.init()
    source = {"resource": "/raw/x.md"}
    for i in range(5):
        wiki.store.write_note(wiki.store.load(), title=f"Filler {i}", summary="filler", body="nothing here " * 5, tags=[], source=source)
    body = "the codeword is alpha today"
    note = wiki.store.write_note(wiki.store.load(), title="Target", summary="target", body=body, tags=[], source=source)
    return wiki, Path(note.path)


def test_a_note_rewritten_in_the_tick_it_was_read_is_read_again(tmp_path: Path) -> None:
    """A note read while its time is recent, then rewritten with the same size and time: once it is old the next search, of this process
    or of a new one, must serve the new text (v1.0.2 caches a note only once it is older than RACY_NS)."""
    wiki, path = _target_wiki(tmp_path)
    assert paths(wiki.search("alpha", 1)) == ["target.md"]  # read while racy
    _same_tick_rewrite(path, b"alpha", b"omega")
    time.sleep(2.3)

    assert paths(wiki.search("omega", 1)) == ["target.md"] and paths(wiki.search("alpha", 1)) == []
    assert paths(Wiki(wiki.cfg, llm=wiki.llm).search("omega", 1)) == ["target.md"]


def test_the_researchers_path_reads_a_note_rewritten_in_the_tick_again(tmp_path: Path) -> None:
    wiki, path = _target_wiki(tmp_path)
    notes = wiki.store.load().all_notes()
    assert [n.rel for n in wiki._search.search(notes, notes, "alpha", 1)] == ["target.md"]
    _same_tick_rewrite(path, b"alpha", b"omega")
    time.sleep(2.3)
    notes = wiki.store.load().all_notes()

    assert [n.rel for n in wiki._search.search(notes, notes, "omega", 1)] == ["target.md"]


def test_ask_finds_its_notes_through_the_index_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = make(tmp_path)
    monkeypatch.setattr(NoteSearch, "search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the in-memory path was used")))
    finder = wiki.researcher()._finder(wiki.store.load(), None)
    assert [n.rel for n in finder("refund within thirty days", 1)] == ["refunds.md"]
    scoped = wiki.researcher()._finder(wiki.store.load(), {"team": "hr"})
    assert [n.rel for n in scoped("engineers designers", 2)] == ["hiring.md"]


def _hash_embedder(texts: list[str]) -> list[list[float]]:
    import hashlib

    return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


def _alpha_wiki(tmp_path: Path, n: int) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()), embedder=_hash_embedder)
    wiki.init()
    for i in range(n):
        wiki.store.write_note(
            wiki.store.load(), title=f"Note {i}", summary="alpha text", body=f"alpha beta {i}", tags=[], source={"resource": "/raw/x.md"}
        )
    _age(tmp_path / "wiki")
    return wiki


def test_a_note_taken_away_between_the_sync_and_the_hashes_does_not_break_the_embedder_path(tmp_path: Path) -> None:
    wiki = _alpha_wiki(tmp_path, 6)
    assert len(wiki.search("alpha", 3)) == 3
    other = Wiki(wiki.cfg, llm=wiki.llm, embedder=_hash_embedder)
    victim = tmp_path / "wiki" / "note-3.md"
    real = wiki._search.index.hashes

    def racing(*args):  # type: ignore[no-untyped-def]
        victim.unlink()
        other.search("alpha")  # another process deletes the note and syncs the index
        return real(*args)

    wiki._search.index.hashes = racing  # type: ignore[method-assign]

    assert "note-3.md" not in paths(wiki.search("alpha", 8))


def test_a_note_unreadable_when_the_embedder_path_reads_it_is_left_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = _alpha_wiki(tmp_path, 4)
    real = NoteSearch._read

    def failing(self, rel):  # type: ignore[no-untyped-def]
        if rel == "note-1.md":
            raise FileNotFoundError(rel)
        return real(self, rel)

    monkeypatch.setattr(NoteSearch, "_read", failing)

    assert "note-1.md" not in paths(wiki.search("alpha", 8))


def test_an_error_of_the_wiki_while_listing_does_not_send_the_index_to_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A folder another process removes while the wiki is listed is a matter of the wiki: the index stays on its file."""
    import llmw2.bundle.store as store_mod

    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()))
    wiki.init()
    for area in ("ops", "hr"):
        wiki.store.create_folder(wiki.store.load(), area, area)
    for folder in wiki.store.load().subfolders:
        wiki.store.write_note(folder, title=f"N {folder.rel}", summary="s", body="alpha beta", tags=[], source={"resource": "/raw/x.md"})
    assert len(wiki.search("alpha")) == 2
    real, done = os.scandir, []

    def racing(path):  # type: ignore[no-untyped-def]
        if str(path).endswith("hr") and not done:
            done.append(1)
            raise FileNotFoundError(2, "gone", str(path))
        return real(path)

    monkeypatch.setattr(store_mod.os, "scandir", racing)
    assert paths(wiki.search("alpha")) == ["ops/n-ops.md"]
    monkeypatch.setattr(store_mod.os, "scandir", real)

    assert wiki._search.index._mem is None
    assert sorted(paths(wiki.search("alpha"))) == ["hr/n-hr.md", "ops/n-ops.md"]


def test_a_build_commits_by_lots_and_goes_on_after_an_interruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(si, "BATCH", 10)
    wiki = _random_wiki(tmp_path, 45)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_index_file(tmp_path)}{suffix}").unlink(missing_ok=True)
    fresh = Wiki(wiki.cfg, llm=wiki.llm)
    reads = []
    real = fresh._search._entry

    def stopping(rel, path, stamp):  # type: ignore[no-untyped-def]
        reads.append(rel)
        if len(reads) == 33:
            raise KeyboardInterrupt
        return real(rel, path, stamp)

    index = fresh._search.index
    with pytest.raises(KeyboardInterrupt):
        index.run(lambda: index.sync(fresh.store, stopping))
    with closing(sqlite3.connect(_index_file(tmp_path))) as db:
        committed = db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    assert committed == 30  # three whole lots; the fourth was rolled back

    again = Wiki(wiki.cfg, llm=wiki.llm)
    q = "alpha river"
    assert paths(again.search(q, 8)) == _expected(again, q, 8)
    assert _rows(_index_file(tmp_path)) == _fresh_rows(again)


def test_a_file_received_with_a_view_in_place_of_a_table_is_not_trusted(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    assert paths(wiki.search("refund", 1)) == ["refunds.md"]
    wiki._search.index.close()  # a file received: nobody holds it (Windows lets nobody move a file held open)
    index_file = _index_file(tmp_path)
    with closing(sqlite3.connect(index_file)) as db:  # the sender edits the file
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("ALTER TABLE docbody RENAME TO docbody_real")
        db.execute("CREATE VIEW docbody AS SELECT id, 'TITLE FROM THE FILE' AS title, summary, counts FROM docbody_real")
        db.execute("CREATE TRIGGER t AFTER INSERT ON docs BEGIN SELECT 1; END")
        db.commit()

    hits = Wiki(wiki.cfg, llm=wiki.llm).search("refund", 1)

    assert [(h.path, h.title) for h in hits] == [("refunds.md", "Refunds")]
    with closing(sqlite3.connect(index_file)) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('view', 'trigger')").fetchone()[0] == 0
        assert si._master(db) == si._expected_master()


START = "\n".join(
    [
        "import sys",
        "from pathlib import Path",
        "from llmw2 import Wiki, WikiConfig",
        "from llmw2.models.usage import UsageTracker",
        "from tests.wiki.fakes import FakeLLM",
        "import time",
        "w = Wiki(WikiConfig(bundle=Path(sys.argv[1]), mode='classic'), llm=FakeLLM(UsageTracker()))",
        "def ask():",
        "    hits = w.search('alpha river market', k=8)",
        "    return ('partial' if hits.partial is not None else 'whole') + ':' + ','.join(h.path for h in hits)",
        "first, end = ask(), time.monotonic() + 300",
        "last = first",
        "while last.startswith('partial') and time.monotonic() < end:  # the process that builds waits for its own build to end",
        "    time.sleep(0.05)",
        "    last = ask()",
        "print(first)",
        "print(last)",
    ]
)


def test_thirty_processes_starting_together_on_a_wiki_with_no_index_agree(tmp_path: Path) -> None:
    wiki = _random_wiki(tmp_path, 30)
    script = tmp_path / "start.py"
    script.write_text(START, encoding="utf-8")
    parts = [str(Path(llmw2.__file__).parents[1]), str(Path(__file__).parents[2]), os.environ.get("PYTHONPATH", "")]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(parts))
    command = [sys.executable, str(script), str(tmp_path / "wiki")]
    procs = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(30)]
    results = [(*p.communicate(timeout=600), p.returncode) for p in procs]
    assert [(r[1][-800:], r[2]) for r in results if r[2] != 0] == []
    answers = [r[0].split() for r in results]
    whole = "whole:" + ",".join(_expected(wiki, "alpha river market", 8))
    # while one process builds the index the others may answer partial (even empty) results, marked so; every result not marked is the
    # index whole's, the same in every process, and every process ends with it
    assert all(len(a) == 2 and a[1] == whole for a in answers), answers
    assert all(a[0] == whole or a[0].startswith("partial:") for a in answers), answers
    assert _rows(_index_file(tmp_path)) == _fresh_rows(wiki)


def test_a_file_received_with_an_oddly_named_table_is_thrown_away_not_edited(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    assert paths(wiki.search("refund", 1)) == ["refunds.md"]
    wiki._search.index.close()  # a file received: nobody holds it (Windows lets nobody move a file held open)
    index_file = _index_file(tmp_path)
    with closing(sqlite3.connect(index_file)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute('CREATE TABLE "x"" junk" (a)')
        db.commit()

    fresh = Wiki(wiki.cfg, llm=wiki.llm)

    assert paths(fresh.search("refund", 1)) == ["refunds.md"]
    assert fresh._search.index._mem is None  # the file was made again, not given up
    with closing(sqlite3.connect(index_file)) as db:
        assert si._master(db) == si._expected_master()


def test_two_notes_with_one_text_one_unreadable_do_not_break_the_embedder_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()), embedder=_hash_embedder)
    wiki.init()
    for _ in range(2):  # one text twice: the two notes share a vector
        wiki.store.write_note(wiki.store.load(), title="Same", summary="alpha", body="alpha beta", tags=[], source={"resource": "/r.md"})
    wiki.store.write_note(wiki.store.load(), title="Other", summary="gamma", body="gamma delta", tags=[], source={"resource": "/raw/x.md"})
    _age(tmp_path / "wiki")
    real = NoteSearch._read

    def failing(self, rel):  # type: ignore[no-untyped-def]
        if rel == "same.md":
            raise FileNotFoundError(rel)
        return real(self, rel)

    monkeypatch.setattr(NoteSearch, "_read", failing)

    assert "same-2.md" in paths(wiki.search("alpha", 8))


def test_the_cache_folder_ignores_itself_and_a_file_of_another_schema_is_left_alone(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    cache = _index_file(tmp_path).parent
    cache.mkdir(parents=True, exist_ok=True)
    other = cache / "search-v1.sqlite"
    other.write_bytes(b"an older version's file")

    assert paths(wiki.search("refund", 1)) == ["refunds.md"]

    assert (cache / ".gitignore").read_text(encoding="utf-8").strip() == "*"
    assert other.read_bytes() == b"an older version's file"
    assert _index_file(tmp_path).name == f"search-v{si.SCHEMA}.sqlite"


def test_the_fields_are_what_decides_a_note_changed_when_its_text_did_not(tmp_path: Path) -> None:
    wiki = _owners_wiki(tmp_path)
    assert paths(wiki.search("pricing", 8, {"owner": "zed"})) == []
    target = tmp_path / "wiki" / "note-0.md"
    target.write_text(target.read_text(encoding="utf-8").replace("owner: bo", "owner: xy"), encoding="utf-8")
    _age(tmp_path / "wiki")

    assert paths(Wiki(wiki.cfg, llm=wiki.llm).search("pricing", 8, {"owner": "xy"})) == ["note-0.md"]
    with closing(sqlite3.connect(_index_file(tmp_path))) as db:
        row = db.execute("SELECT b.fields FROM docbody b JOIN docs d ON d.id = b.id WHERE d.rel = 'note-0.md'").fetchone()
        assert row[0] == '[["owner", "xy"]]'


def test_the_tokenizer_and_the_tables_are_tied_to_the_schema() -> None:
    """Changing the tokenizer or the tables without a new SCHEMA would let two versions rebuild one file by turns: this fails until
    SCHEMA moves and the line below says what it holds."""
    import hashlib

    digest = hashlib.sha256(repr(si._DDL).encode()).hexdigest()[:16]
    assert {si.SCHEMA: (lexical._WORD.pattern, digest)} == {6: (r"[^\W_]{3,}|\d+(?:[.,]\d+)*", "5ea6f0e5bfe691f0")}


ODD = {
    "intkey": "fields:\n  1: x\n  team: ops",
    "set": "fields: !!set {ana: null, bo: null}",
    "date": "fields:\n  day: 2026-01-02",
    "list": "fields:\n  team: [ops, hr]",
    "plain": "fields:\n  team: ops",
}
ODD_WHERES: list[dict] = [  # type: ignore[type-arg]
    {"ana": "None"},
    {1: "x"},
    {"1": "x"},
    {1: "x", "team": "ops"},
    {"team": "ops"},
    {"team": "hr"},
    {"day": "2026-01-02"},
    {True: "x"},
    {1.0: "x"},
]


def test_where_on_keys_that_are_not_strings_answers_as_the_tree_filter_does(tmp_path: Path) -> None:
    from llmw2.bundle.tree import matching

    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()))
    wiki.init()
    for name, fm in ODD.items():
        (tmp_path / "wiki" / f"{name}.md").write_text(
            f"---\ntype: Note\ntitle: {name}\ndescription: d\n{fm}\n---\nalpha {name}\n", encoding="utf-8"
        )
    _age(tmp_path / "wiki")
    root = wiki.store.load()
    for where in ODD_WHERES:
        assert sorted(paths(wiki.search("alpha", 8, where))) == sorted(n.rel for n in matching(root, where).all_notes()), where
    assert paths(wiki.search("alpha", 8, {1: "x"})) == ["intkey.md"] and wiki.search("alpha", 8, {"1": "x"}) == []
