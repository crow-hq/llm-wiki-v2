# Copyright 2026 Federico Cesarini, Marco Sassarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The `okf-wiki` command line, with `make_wiki` pointed at a Wiki built on the fakes."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from okf_wiki import Wiki, WikiConfig, cli
from okf_wiki.client import ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_files import make_pdf

NOTE = "revenue-recognition.md"


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
    """A classic ingest into the root of an empty wiki: summarize, route here."""
    llm.add("librarian/summarize", {"title": "Revenue recognition", "summary": "When revenue is booked.", "body": "On delivery."})
    llm.add("librarian/route", {"action": "here"})


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
    classifier.add_choice("route", "Here", {"Here": 0.9, "New subfolder": 0.1}, 0.9)

    assert cli.main(["--bundle", str(bundle), "--mode", "crow", "ingest", "-"]) == 0

    assert made == [{"bundle": bundle, "mode": "crow"}]
    assert classifier.ops == ["route"]


def test_ingest_file_prints_the_created_note(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "memo.txt"
    source.write_text("Revenue is booked on delivery.", encoding="utf-8")
    script_ingest(llm)

    assert run(bundle, "ingest", str(source)) == 0

    assert capsys.readouterr().out == f"created: {NOTE}\n"
    assert "Revenue is booked on delivery." in llm.calls[0][1][-1]["content"]


def test_ingest_file_records_its_path_as_the_resource(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, tmp_path: Path
) -> None:
    source = tmp_path / "memo.txt"
    source.write_text("Revenue is booked on delivery.", encoding="utf-8")
    script_ingest(llm)

    run(bundle, "ingest", str(source))

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
    assert (result["action"], result["note"], result["folder"]) == ("created", NOTE, "/")
    assert result["usage"]["llm"]["calls"] == 2
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
    assert err == "llm 2 calls 200 in / 20 out | classifier 0 calls 0 in / 0 out\n"
    assert "calls" not in out


def ingest_one(bundle: Path, llm: FakeLLM) -> None:
    """Put one note at the root of the wiki, outside the CLI."""
    script_ingest(llm)
    Wiki(WikiConfig(bundle=bundle), llm=llm).ingest("Revenue is booked on delivery.")


def test_ask_prints_the_answer(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest_one(bundle, llm)
    llm.add("researcher/navigate", {"select": [NOTE], "done": True})
    llm.add("researcher/answer", f"On delivery [{NOTE}].")

    assert run(bundle, "ask", "When is revenue booked?") == 0

    assert capsys.readouterr().out == f"On delivery [{NOTE}].\n"


def test_ask_with_json_prints_the_answer_with_citations(
    made: list[dict[str, Any]], bundle: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    ingest_one(bundle, llm)
    llm.add("researcher/navigate", {"select": [NOTE], "done": True})
    llm.add("researcher/answer", f"On delivery [{NOTE}].")

    assert run(bundle, "ask", "When is revenue booked?", "--json") == 0

    answer = json.loads(capsys.readouterr().out)
    assert (answer["citations"], answer["notes"]) == ([NOTE], [NOTE])
    assert answer["usage"]["llm"]["calls"] == 2


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

    assert capsys.readouterr() == ("", "okf-wiki: no bundle configured\n")


def test_without_a_bundle_the_wiki_lives_in_the_home_folder(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["init"]) == 0  # make_wiki is the real one here: settings file, environment, flags

    assert (home / "llm-wiki" / "index.md").is_file()
    assert capsys.readouterr().out == f"wiki ready at {home / 'llm-wiki'}\n"


def test_a_broken_settings_file_exits_2_with_its_path(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = home / ".config" / "llm-wiki" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not json", encoding="utf-8")

    assert cli.main(["init"]) == 2
    assert capsys.readouterr().err.startswith(f"okf-wiki: {config} is not valid JSON")


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

    assert capsys.readouterr().err.endswith("(no API key yet: run okf-wiki setup)\n")


def test_no_command_serves_and_opens_the_browser(
    made: list[dict[str, Any]], bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from okf_wiki import server

    seen: dict[str, Any] = {}
    monkeypatch.setattr(server, "serve", lambda wiki, **kw: seen.update(kw, bundle=wiki.cfg.bundle))

    assert cli.main(["--bundle", str(bundle)]) == 0

    assert (seen["bundle"], seen["open_browser"], seen["host"], seen["port"]) == (bundle, True, "127.0.0.1", 8000)
    assert seen["settings"].flags == {"bundle": bundle}


def answers(monkeypatch: pytest.MonkeyPatch, *replies: str, key: str = "") -> list[str]:
    """Script `setup`: the replies to its questions in order, and the key typed at the hidden prompt."""
    queue, asked = list(replies), []

    def prompt(question: str) -> str:
        asked.append(question)
        return queue.pop(0)

    monkeypatch.setattr(cli, "prompt", prompt)
    monkeypatch.setattr(cli, "secret", lambda question: asked.append(question) or key)
    return asked


def saved_settings(home: Path) -> dict[str, Any]:
    return json.loads((home / ".config" / "llm-wiki" / "config.json").read_text(encoding="utf-8"))


def test_setup_tests_the_model_then_saves(
    monkeypatch: pytest.MonkeyPatch, home: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    llm.add("settings/probe", "OK")
    built: list[WikiConfig] = []
    monkeypatch.setattr(cli, "build_wiki", lambda cfg: built.append(cfg) or Wiki(cfg, llm=llm))
    asked = answers(monkeypatch, "gemini", "", key="g-key-0123456789")

    assert cli.main(["setup"]) == 0

    assert asked[0] == "Provider (openrouter, openai, gemini, ollama, custom) [openrouter]: "
    assert asked[2] == "Model (e.g. gemini-3.8-flash, gemini-3.5-flash, gemini-3.5-flash-lite) [gemini-3.8-flash]: "
    assert (built[0].llm.provider, built[0].llm.api_key) == ("gemini", "g-key-0123456789")
    assert saved_settings(home) == {"llm": {"provider": "gemini", "api_key": "g-key-0123456789", "model": "gemini-3.8-flash"}}
    assert "OK\nSaved (API key …6789)" in capsys.readouterr().out


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

    assert capsys.readouterr().out == f"created: {NOTE}\n"
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
