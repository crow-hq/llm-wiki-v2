# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What a library of thousands of documents needs: the classifier's answer cache, and a folder source that pages."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from llmw2 import LocalFolderSource, ModelError, Wiki, WikiConfig, sync
from llmw2.bundle.store import CACHE_FILE
from llmw2.config import ClassifierConfig
from llmw2.models.cache import SqliteCache, cache_key
from llmw2.models.classifier import Classifier
from llmw2.models.usage import UsageTracker
from llmw2.sources import local
from tests.wiki.fakes import ContentLLM
from tests.wiki.test_ingest_many import make_wiki

# -- the answer cache -----------------------------------------------------------------

QUESTIONS = {"q": {"type": "noul", "instructions": "The text is about the weather."}}
STATE = "SECRET-STATE the sky is blue"


class Server:
    """A System One server that counts its requests and can be told to fail."""

    def __init__(self) -> None:
        self.requests = 0
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.fail:
            return httpx.Response(400, json={"error": "bad"})
        body = json.loads(request.content)
        answers = {k: {"type": "noul", "noul": 0.75} for k in body["questions"]}
        return httpx.Response(200, json={"answers": answers, "usage": {"input_tokens": 9, "output_tokens": 0}})


def classifier(server: Server, cache: Any, tracker: UsageTracker) -> Classifier:
    client = httpx.Client(transport=httpx.MockTransport(server))
    return Classifier(ClassifierConfig(model="jev", api_key="k", base_url="http://clf.test"), tracker, client=client, cache=cache)


def test_a_repeated_request_is_served_from_the_cache_and_counted(tmp_path: Path) -> None:
    server, tracker = Server(), UsageTracker()
    clf = classifier(server, SqliteCache(tmp_path / "c" / "classifier.sqlite"), tracker)

    first = clf.nouls(STATE, {"q": "The text is about the weather."}, op="relate")
    second = clf.nouls(STATE, {"q": "The text is about the weather."}, op="relate")

    assert first == second == {"q": 0.75}
    assert server.requests == 1
    report = tracker.snapshot()
    assert (report.classifier.calls, report.classifier.served, report.classifier.cost_usd) == (1, 1, 0.0)
    assert report.to_dict()["by_op"]["classifier:relate"]["served"] == 1


def test_the_cache_keeps_hashes_and_answers_and_no_text(tmp_path: Path) -> None:
    path = tmp_path / "classifier.sqlite"
    classifier(Server(), SqliteCache(path), UsageTracker()).nouls(STATE, {"q": "The text is about the weather."})

    raw = b"".join(p.read_bytes() for p in tmp_path.glob("classifier.sqlite*"))

    assert b"SECRET-STATE" not in raw and b"weather" not in raw
    assert SqliteCache(path).get(cache_key("http://clf.test", "jev", STATE, QUESTIONS)) == {"q": {"type": "noul", "noul": 0.75}}


def test_what_the_request_depends_on_is_in_the_key(tmp_path: Path) -> None:
    server = Server()
    clf = classifier(server, SqliteCache(tmp_path / "c.sqlite"), UsageTracker())

    clf.nouls(STATE, {"q": "A."})
    clf.nouls(STATE + "!", {"q": "A."})  # another state
    clf.nouls(STATE, {"q": "B."})  # another question

    assert server.requests == 3
    assert cache_key("http://a", "m", "s", QUESTIONS) != cache_key("http://a", "n", "s", QUESTIONS)
    assert cache_key("http://a", "m", "s", QUESTIONS) != cache_key("http://b", "m", "s", QUESTIONS)


def test_an_error_is_not_cached(tmp_path: Path) -> None:
    server = Server()
    clf = classifier(server, SqliteCache(tmp_path / "c.sqlite"), UsageTracker())
    server.fail = True
    with pytest.raises(ModelError):
        clf.nouls(STATE, {"q": "A."})
    server.fail = False

    assert clf.nouls(STATE, {"q": "A."}) == {"q": 0.75}
    assert server.requests == 2


def test_without_a_cache_every_request_goes_out() -> None:
    server, tracker = Server(), UsageTracker()
    clf = classifier(server, None, tracker)

    clf.nouls(STATE, {"q": "A."})
    clf.nouls(STATE, {"q": "A."})

    assert server.requests == 2 and tracker.snapshot().classifier.served == 0
    assert "served" not in tracker.snapshot().to_dict()["classifier"]


def test_any_two_method_backend_can_stand_in() -> None:
    class Memory:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}

        def get(self, key: str) -> Any:
            return self.data.get(key)

        def put(self, key: str, answers: Any) -> None:
            self.data[key] = answers

    server, memory = Server(), Memory()
    classifier(server, memory, UsageTracker()).nouls(STATE, {"q": "A."})
    classifier(server, memory, UsageTracker()).nouls(STATE, {"q": "A."})

    assert server.requests == 1 and len(memory.data) == 1


def test_the_cache_is_off_by_default_and_the_wiki_builds_it_from_the_setting(tmp_path: Path) -> None:
    off = Wiki(WikiConfig(bundle=tmp_path / "a", mode="crow", classifier=ClassifierConfig(api_key="k")), llm=ContentLLM(UsageTracker()))
    on_cfg = WikiConfig(bundle=tmp_path / "b", mode="crow", classifier=ClassifierConfig(api_key="k", cache=True))
    on = Wiki(on_cfg, llm=ContentLLM(UsageTracker()))

    assert off.classifier is not None and off.classifier.cache is None
    assert on.classifier is not None and isinstance(on.classifier.cache, SqliteCache)
    assert not (tmp_path / "b" / CACHE_FILE).exists()  # made by the first answer, not by building the wiki


def test_clearing_the_wiki_removes_the_cache_and_a_rebuild_keeps_it(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path / "w")
    cache = SqliteCache(wiki.store.root / CACHE_FILE)
    cache.put("k", {"q": {"type": "noul", "noul": 0.5}})

    wiki.store.clear(keep_folders=True)
    assert cache.get("k") is not None

    wiki.clear()
    assert not (wiki.store.root / CACHE_FILE).parent.exists()


# -- paging ----------------------------------------------------------------------------


@pytest.fixture
def small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "PAGE", 3)


def write(folder: Path, n: int) -> None:
    (folder / f"d{n:02d}.md").write_text(f"topic: red-{n}\nline {n}.", encoding="utf-8")


def pages(source: LocalFolderSource, cursor: str | None = None) -> list[list[str]]:
    out = []
    while True:
        batch = source.changes(cursor)
        out.append([f"{c.kind}:{c.origin.id}" for c in batch.changes])
        cursor = batch.cursor
        if not batch.more:
            return out


def test_the_source_hands_changes_over_a_page_at_a_time(tmp_path: Path, small_pages: None) -> None:
    for n in range(8):
        write(tmp_path, n)

    result = pages(LocalFolderSource(tmp_path))

    assert [len(p) for p in result] == [3, 3, 2]
    assert sorted(c for p in result for c in p) == [f"upsert:d{n:02d}.md" for n in range(8)]


def test_each_page_cursor_carries_only_what_was_handed_over(tmp_path: Path, small_pages: None) -> None:
    for n in range(5):
        write(tmp_path, n)
    source = LocalFolderSource(tmp_path)

    first = source.changes(None)

    assert first.more and len(json.loads(first.cursor)) == 3
    rest = source.changes(first.cursor)
    assert not rest.more and len(rest.changes) == 2
    assert len(json.loads(rest.cursor)) == 5
    assert source.changes(rest.cursor).changes == []


def test_removals_are_paged_too(tmp_path: Path, small_pages: None) -> None:
    for n in range(4):
        write(tmp_path, n)
    source = LocalFolderSource(tmp_path)
    cursor = None
    while True:
        batch = source.changes(cursor)
        cursor = batch.cursor
        if not batch.more:
            break
    for n in range(4):
        (tmp_path / f"d{n:02d}.md").unlink()
    write(tmp_path, 9)

    result = pages(source, cursor)

    assert [len(p) for p in result] == [3, 2]
    assert sorted(c for p in result for c in p) == ["remove:d00.md", "remove:d01.md", "remove:d02.md", "remove:d03.md", "upsert:d09.md"]


def test_a_cursor_in_the_old_format_still_reads(tmp_path: Path) -> None:
    write(tmp_path, 0)
    source = LocalFolderSource(tmp_path)
    old = json.dumps(source._listing())  # a full listing, as earlier runs saved it

    assert source.changes(old).changes == []


def test_a_sync_saves_its_cursor_per_page_and_a_stop_redoes_one_page_at_most(tmp_path: Path, small_pages: None) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    for n in range(9):
        write(docs, n)
    llm = ContentLLM(UsageTracker(), fail_on={"red-4\n": ModelError("llm 429", status=429)})
    wiki = make_wiki(tmp_path / "wiki", llm=llm)
    source = LocalFolderSource(docs)

    stopped = sync(wiki, source)

    assert stopped.stopped and stopped.created == 4  # d00..d02 (page 1) and d03; d04 failed
    saved = next((wiki.store.root / ".llmw2" / "sync").glob("*.json"))
    assert sorted(json.loads(json.loads(saved.read_text(encoding="utf-8"))["cursor"])) == ["d00.md", "d01.md", "d02.md"]
    llm.fail_on.clear()
    before = len([1 for op, _ in llm.calls if op == "librarian/summarize"])

    resumed = sync(wiki, source)

    redone = len([1 for op, _ in llm.calls if op == "librarian/summarize"]) - before
    assert (resumed.stopped, resumed.created, resumed.skipped) == (None, 5, 1)  # d03 is filed already: same version, not read again
    assert redone == 5  # nothing of page 1 was prepared again, and d03 only fetched
    assert wiki.check() == [] and len(wiki.tree().all_notes()) == 9


def test_threads_and_processes_can_start_on_a_new_cache_file_together(tmp_path: Path) -> None:
    path = tmp_path / "c" / "classifier.sqlite"
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            cache = SqliteCache(path)  # one object per thread, as a second process would have
            for k in range(20):
                cache.put(f"{i}-{k}", {"q": {"type": "noul", "noul": 0.5}})
                assert cache.get(f"{i}-{k}") is not None
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert SqliteCache(path).get("15-19") is not None


def test_the_summary_names_the_answers_the_cache_served() -> None:
    tracker = UsageTracker()
    tracker.record("classifier", "jev", 50, 0)
    tracker.serve("classifier", "jev")

    assert tracker.snapshot().summary() == "llm 0 calls 0 in / 0 out | classifier 1 calls + 1 from the cache 50 in / 0 out"
