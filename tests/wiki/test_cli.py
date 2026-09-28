# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The `llmwiki2` command line, with `make_wiki` pointed at a Wiki built on the fakes."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig, cli
from llmw2.errors import ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_files import make_pdf

NOTE = "finance/revenue-recognition.md"  # the root holds no notes: the first one opens a folder
FOLDER = {"name": "finance", "description": "Money in and out."}


@pytest.fixture
def made(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM, classifier: FakeClassifier) -> list[dict[str, Any]]:
    """Replace `cli.make_wiki`; returns the keyword arguments of every call."""
    seen: list[dict[str, Any]] = []

    def make_wiki(*, bundle: Path | None = None, mode: str | None = None) -> Wiki:
        seen.append({"bundle": bundle, "mode": mode})
        return Wiki(WikiConfig(bundle=bundle, mode=mode or "classic"), llm=llm, classifier=classifier)

    monkeypatch.setattr(cli, "make_wiki", make_wiki)
    return seen


def run(bundle: Path, *args: str) -> int:
    return cli.main(["--bundle", str(bundle), *args])


def script_ingest(llm: FakeLLM) -> None:
    """A classic ingest into an empty wiki: summarize, route to a new folder (the root holds no notes), name it "finance"."""
    llm.add("librarian/summarize", {"title": "Revenue recognition", "summary": "When revenue is booked.", "body": "On delivery."})
    llm.add("librarian/route", {"action": "new"}).add("librarian/name_folder", FOLDER)


def test_init_creates_the_bundle(made: list[dict[str, Any]], bundle: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(bundle, "init") == 0

    assert capsys.readouterr().out == f"wiki ready at {bundle}\n"
    assert (bundle / "index.md").is_file()
    assert made == [{"bundle": bundle, "mode": None}]


def test_mode_flag_makes_a_crow_wiki(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("Revenue is booked on delivery."))
    llm.add("librarian/summarize", {"title": "Revenue recognition", "summary": "When revenue is booked.", "body": "On delivery."})
    llm.add("librarian/name_folder", FOLDER)

    assert cli.main(["--bundle", str(bundle), "--mode", "crow", "ingest", "-"]) == 0

    assert made == [{"bundle": bundle, "mode": "crow"}]
    # CROW routed it: in an empty wiki a new folder is its one way, where the classic librarian would ask the LLM
    assert llm.ops == ["librarian/summarize", "librarian/name_folder"]


def test_ingest_when_a_file_is_given_then_prints_the_note_and_records_the_path_as_resource(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    source = tmp_path / "memo.txt"
    source.write_text("Revenue is booked on delivery.", encoding="utf-8")
    script_ingest(llm)

    # ACT
    code = run(bundle, "ingest", str(source))

    # ASSERT
    assert code == 0
    assert capsys.readouterr().out == f"created: {NOTE} (new folders: /finance)\n"
    assert "Revenue is booked on delivery." in llm.calls[0][1][-1]["content"]
    [raw] = (bundle / "raw").glob("*.md")
    assert f"resource: {source}" in raw.read_text(encoding="utf-8")


def test_ingest_stdin_with_json_prints_the_result_with_usage(
    made: list[dict[str, Any]],
    bundle: Path,
    llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("Revenue is booked on delivery."))
    script_ingest(llm)

    assert run(bundle, "ingest", "-", "--title", "Revenue memo", "--json") == 0

    result = json.loads(capsys.readouterr().out)
    assert (result["action"], result["note"], result["folder"]) == ("created", NOTE, "/finance")
    assert result["usage"]["llm"]["calls"] == 3
    assert "Revenue is booked on delivery." in llm.calls[0][1][-1]["content"]


def test_usage_summary_goes_to_stderr(
    made: list[dict[str, Any]],
    bundle: Path,
    llm: FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("Revenue is booked on delivery."))
    script_ingest(llm)

    run(bundle, "ingest", "-")

    out, err = capsys.readouterr()
    assert err == "llm 3 calls 300 in / 30 out | classifier 0 calls 0 in / 0 out\n"
    assert "calls" not in out


def ingest_one(bundle: Path, llm: FakeLLM) -> None:
    """Put one note in the wiki (in /finance), outside the CLI."""
    script_ingest(llm)
    Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm).ingest("Revenue is booked on delivery.")


def test_ask_prints_the_answer(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest_one(bundle, llm)
    llm.add("researcher/navigate", {"open": ["finance"]}, {"select": [NOTE], "done": True})
    llm.add("researcher/answer", f"On delivery [{NOTE}].")

    assert run(bundle, "ask", "When is revenue booked?") == 0

    assert capsys.readouterr().out == f"On delivery [{NOTE}].\n"


def test_ask_with_json_prints_the_answer_with_citations(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest_one(bundle, llm)
    llm.add("researcher/navigate", {"open": ["finance"]}, {"select": [NOTE], "done": True})
    llm.add("researcher/answer", f"On delivery [{NOTE}].")

    assert run(bundle, "ask", "When is revenue booked?", "--json") == 0

    answer = json.loads(capsys.readouterr().out)
    assert (answer["citations"], answer["notes"]) == ([NOTE], [NOTE])
    assert answer["usage"]["llm"]["calls"] == 3


def test_check_exits_0_on_a_clean_wiki(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest_one(bundle, llm)

    assert run(bundle, "check") == 0

    assert capsys.readouterr() == ("", "0 problem(s)\n")


def test_check_exits_1_after_corrupting_a_note(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest_one(bundle, llm)
    (bundle / NOTE).write_text("---\ntitle: Revenue recognition\n---\n\nNo type.\n", encoding="utf-8")

    assert run(bundle, "check") == 1

    out, err = capsys.readouterr()
    assert out == f"{NOTE}: Missing required frontmatter keys: type\n"
    assert err == "1 problem(s)\n"


def test_make_wiki_failure_exits_2_with_the_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def make_wiki(**_: Any) -> Wiki:
        raise RuntimeError("no bundle configured")

    monkeypatch.setattr(cli, "make_wiki", make_wiki)

    assert cli.main(["init"]) == 2

    assert capsys.readouterr() == ("", "llmwiki2: no bundle configured\n")


def test_without_a_bundle_the_wiki_lives_in_the_home_folder(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init"]) == 0  # make_wiki is the real one here: settings file, environment, flags

    assert (home / "llm-wiki" / "index.md").is_file()
    assert capsys.readouterr().out == f"wiki ready at {home / 'llm-wiki'}\n"


def test_a_broken_settings_file_exits_2_with_its_path(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = home / ".config" / "llm-wiki" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not json", encoding="utf-8")

    assert cli.main(["init"]) == 2
    assert capsys.readouterr().err.startswith(f"llmwiki2: {config} is not valid JSON")


def unreachable(llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    def chat(*_: Any, **__: Any) -> str:
        raise ModelError("llm 401 from https://openrouter.ai/api/v1/chat/completions")

    monkeypatch.setattr(llm, "chat", chat)


def test_a_model_error_without_a_key_points_to_setup(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest_one(bundle, llm)
    unreachable(llm, monkeypatch)
    capsys.readouterr()

    assert run(bundle, "ask", "anything?") == 2

    assert capsys.readouterr().err.endswith("(no API key yet: run llmwiki2 setup)\n")


def test_no_command_serves_and_opens_the_browser(
    made: list[dict[str, Any]], bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llmw2 import server

    seen: dict[str, Any] = {}
    monkeypatch.setattr(server, "serve", lambda wiki, **kw: seen.update(kw, bundle=wiki.cfg.bundle))

    assert cli.main(["--bundle", str(bundle)]) == 0

    assert (seen["bundle"], seen["open_browser"], seen["host"], seen["port"]) == (bundle, True, "127.0.0.1", 8000)
    assert seen["settings"].flags == {"bundle": bundle}


def answers(monkeypatch: pytest.MonkeyPatch, *replies: str, keys: tuple[str, ...] = ()) -> list[str]:
    """Script `setup`: the replies to its questions in order, and the keys typed at the hidden prompts."""
    queue, hidden, asked = list(replies), list(keys), []

    def prompt(question: str) -> str:
        asked.append(question)
        return queue.pop(0)

    monkeypatch.setattr(cli, "prompt", prompt)
    monkeypatch.setattr(cli, "secret", lambda question: asked.append(question) or (hidden.pop(0) if hidden else ""))
    return asked


def saved_settings(home: Path) -> dict[str, Any]:
    return json.loads((home / ".config" / "llm-wiki" / "config.json").read_text(encoding="utf-8"))


def test_setup_tests_the_model_then_saves(
    monkeypatch: pytest.MonkeyPatch, home: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    llm.add("settings/probe", "OK")
    built: list[WikiConfig] = []
    monkeypatch.setattr(cli, "build_wiki", lambda cfg: built.append(cfg) or Wiki(cfg, llm=llm))
    asked = answers(monkeypatch, "gemini", "", keys=("g-key-0123456789", "or-key-0123456789"))

    assert cli.main(["setup"]) == 0

    assert asked[0] == "Provider (openrouter, openai, gemini, ollama, custom) [openrouter]: "
    assert asked[2] == "Model (e.g. gemini-3.8-flash, gemini-3.5-flash, gemini-3.5-flash-lite) [gemini-3.8-flash]: "
    assert asked[3].startswith("CROW classifier key")
    assert (built[0].llm.provider, built[0].llm.api_key, built[0].mode) == ("gemini", "g-key-0123456789", "crow")
    assert saved_settings(home) == {
        "llm": {"provider": "gemini", "api_key": "g-key-0123456789", "model": "gemini-3.8-flash"},
        "classifier": {"api_key": "or-key-0123456789"},
    }
    assert "OK\nSaved (crow mode, API key …6789)" in capsys.readouterr().out


def test_setup_without_a_classifier_key_chooses_classic_mode(
    monkeypatch: pytest.MonkeyPatch, home: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    llm.add("settings/probe", "OK")
    monkeypatch.setattr(cli, "build_wiki", lambda cfg: Wiki(cfg, llm=llm))
    answers(monkeypatch, "ollama", "")

    assert cli.main(["setup"]) == 0

    assert saved_settings(home) == {"llm": {"provider": "ollama", "model": "gemma4"}, "mode": "classic"}
    assert "Saved (classic mode, API key none)" in capsys.readouterr().out


def test_with_openrouter_setup_keeps_crow_with_one_key(
    monkeypatch: pytest.MonkeyPatch, home: Path, llm: FakeLLM
) -> None:
    llm.add("settings/probe", "OK")
    monkeypatch.setattr(cli, "build_wiki", lambda cfg: Wiki(cfg, llm=llm))
    asked = answers(monkeypatch, "", "", keys=("or-key-0123456789",))

    assert cli.main(["setup"]) == 0

    assert not any(q.startswith("CROW classifier key") for q in asked)
    assert saved_settings(home) == {"llm": {"provider": "openrouter", "api_key": "or-key-0123456789", "model": "google/gemini-3.8-flash"}}


def test_setup_asks_before_saving_settings_that_failed_the_test(
    monkeypatch: pytest.MonkeyPatch, home: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    unreachable(llm, monkeypatch)
    monkeypatch.setattr(cli, "build_wiki", lambda cfg: Wiki(cfg, llm=llm))
    answers(monkeypatch, "ollama", "", "n")

    assert cli.main(["setup"]) == 1

    assert not (home / ".config" / "llm-wiki" / "config.json").exists()
    assert "failed: " in capsys.readouterr().out


def test_ingest_of_empty_stdin_exits_non_zero_with_a_message(
    made: list[dict[str, Any]],
    bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("   \n"))

    code = run(bundle, "ingest", "-")

    assert code not in (0, 1)  # 1 means "check found problems"
    assert "nothing to ingest" in capsys.readouterr().err


def test_ingest_of_a_pdf_reads_its_text_layer(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "board-minutes.pdf"
    source.write_bytes(make_pdf("Revenue is booked on delivery."))
    script_ingest(llm)

    assert run(bundle, "ingest", str(source)) == 0

    assert capsys.readouterr().out == f"created: {NOTE} (new folders: /finance)\n"
    prompt = llm.calls[0][1][-1]["content"]
    assert "Revenue is booked on delivery." in prompt and "Source title: board minutes" in prompt


def test_ingest_of_an_unsupported_file_exits_2(
    made: list[dict[str, Any]], bundle: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "photo.png"
    source.write_bytes(b"\x89PNG")

    assert run(bundle, "ingest", str(source)) == 2
    assert "unsupported file type" in capsys.readouterr().err


def test_load_dotenv_reads_values_without_overriding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nOKF_T_ONE=one\nOKF_T_TWO="two"\nOKF_T_JSON={"a": [1]}\nOKF_T_SET=file\n', encoding="utf-8")
    monkeypatch.setenv("OKF_T_SET", "shell")
    for key in ("OKF_T_ONE", "OKF_T_TWO", "OKF_T_JSON"):
        monkeypatch.delenv(key, raising=False)

    cli.load_dotenv(env_file)

    import os

    assert (os.environ["OKF_T_ONE"], os.environ["OKF_T_TWO"], os.environ["OKF_T_JSON"]) == ("one", "two", '{"a": [1]}')
    assert os.environ["OKF_T_SET"] == "shell"
    for key in ("OKF_T_ONE", "OKF_T_TWO", "OKF_T_JSON"):
        monkeypatch.delenv(key)


def test_the_packaged_demo_is_a_valid_wiki() -> None:
    from importlib import resources

    from llmw2.bundle.check import check
    from llmw2.bundle.store import WikiStore

    with resources.as_file(resources.files("llmw2") / "demo") as demo:
        root = WikiStore(demo, actor="test").load()
        assert check(demo) == []
    assert len(root.all_notes()) > 20 and len(root.subfolders) >= 5


def test_demo_copies_the_example_once_and_opens_it(
    made: list[dict[str, Any]], home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from llmw2 import server

    seen: dict[str, Any] = {}
    monkeypatch.setattr(server, "serve", lambda wiki, **kw: seen.update(kw, bundle=wiki.cfg.bundle))
    demo = home / "llm-wiki-demo"

    assert cli.main(["demo", "--port", "8123"]) == 0

    assert (seen["bundle"], seen["open_browser"], seen["port"]) == (demo, True, 8123)
    assert (demo / "products" / "aurora-espresso-blend.md").is_file()
    assert "copied to ~/llm-wiki-demo" in capsys.readouterr().out

    (demo / "mine.md").write_text("kept", encoding="utf-8")
    assert cli.main(["demo"]) == 0
    assert (demo / "mine.md").read_text(encoding="utf-8") == "kept"  # a second run reopens the copy


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
def test_setup_when_the_user_cancels_then_exits_2_and_saves_nothing(
    monkeypatch: pytest.MonkeyPatch, home: Path, capsys: pytest.CaptureFixture[str], interrupt: type[BaseException]
) -> None:
    # ARRANGE
    def cancel(question: str) -> str:
        raise interrupt

    monkeypatch.setattr(cli, "prompt", cancel)

    # ACT
    code = cli.main(["setup"])

    # ASSERT
    assert code == 2
    assert capsys.readouterr().err.endswith("llmwiki2: setup cancelled\n")
    assert not (home / ".config" / "llm-wiki" / "config.json").exists()


def test_ingest_when_a_file_has_a_title_then_the_title_names_the_source(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, tmp_path: Path
) -> None:
    # ARRANGE
    source = tmp_path / "board-minutes.pdf"
    source.write_bytes(make_pdf("Revenue is booked on delivery."))
    script_ingest(llm)

    # ACT
    code = run(bundle, "ingest", str(source), "--title", "Q3 board")

    # ASSERT
    assert code == 0
    prompt = llm.calls[0][1][-1]["content"]
    assert "Revenue is booked on delivery." in prompt and "Source title: Q3 board" in prompt
    [raw] = (bundle / "raw").glob("*.md")
    assert f"resource: {source}" in raw.read_text(encoding="utf-8")
