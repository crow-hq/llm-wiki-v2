# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""server.json lists the memory in the official MCP Registry: the release publishes it after PyPI, and the registry
refuses it unless its version is on PyPI and the README there names the server."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import asyncio
import json
import tomllib
from pathlib import Path

import pytest

import llmw2
from llmw2 import Wiki
from llmw2.cli import memory_stdio
from llmw2.config import WikiConfig
from llmw2.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
PACKAGE = SERVER["packages"][0]


def test_server_json_names_the_version_being_released():
    assert SERVER["version"] == PACKAGE["version"] == llmw2.__version__


def test_readme_proves_the_package_is_ours():
    # PyPI shows the README; the registry looks there for this line, ending at a boundary (here the comment's close).
    assert f"<!-- mcp-name: {SERVER['name']} -->" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_registry_limits_hold():
    assert len(SERVER["description"]) <= 100
    assert PACKAGE["registryType"] == "pypi" and PACKAGE["transport"] == {"type": "stdio"}


def test_the_command_installs_the_mcp_extra_and_starts_the_memory():
    # uvx --with "mcp…" llmwiki2 mcp: the memory needs the mcp extra, which `uvx llmwiki2` alone does not install.
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    (extra,) = pyproject["project"]["optional-dependencies"]["mcp"]
    assert PACKAGE["identifier"] == pyproject["project"]["name"]
    assert [(a["name"], a["value"]) for a in PACKAGE["runtimeArguments"]] == [("--with", extra)]
    assert [a["value"] for a in PACKAGE["packageArguments"]] == ["mcp"]


def test_models_choices_are_the_settings_ones():
    (models,) = (v for v in PACKAGE["environmentVariables"] if v["name"] == "OKF_MCP_MODELS")
    allowed = WikiConfig.model_fields["mcp_models"].annotation.__args__
    assert sorted(models["choices"]) == sorted(allowed) and "default" not in models  # a default would override setup


class _Server:
    def run(self, transport):
        assert transport == "stdio"


def _memory():
    return type("M", (), {"server": _Server(), "close": lambda self: None})()


@pytest.fixture
def started(monkeypatch, tmp_path):
    """Start `llmwiki2 mcp` with no client: the wiki's config as the memory would see it."""
    import llmw2.memory

    monkeypatch.setattr(llmw2.memory, "Memory", lambda make, local_files: _memory())

    def start(saved: dict | None = None, **env: str):
        path = tmp_path / f"settings-{len(list(tmp_path.glob('settings-*')))}.json"  # a new one each time
        if saved is not None:
            path.write_text(json.dumps(saved), encoding="utf-8")
        settings = Settings({"OKF_CONFIG": str(path), "OKF_BUNDLE": str(tmp_path / "wiki"), **env})
        wiki = Wiki(settings.config())
        assert memory_stdio(wiki, settings) == 0
        return wiki.cfg

    return start


def test_a_memory_never_set_up_runs_on_the_clients_models(started):
    assert started().mcp_models == "client"


def test_the_choice_of_setup_or_of_the_environment_stays(started):
    assert started({"mcp": True, "mcp_models": "hybrid"}).mcp_models == "hybrid"
    assert started({"mcp": True}).mcp_models == "wiki"  # setup saves the wiki's own models as no value
    assert started({"llm": {"provider": "openrouter"}}).mcp_models == "wiki"  # set up for the page: its own models
    assert started(OKF_MCP_MODELS="wiki").mcp_models == "wiki"


def test_a_memory_never_used_answers_from_an_empty_wiki(monkeypatch, tmp_path):
    # The client's first call is often recall: on a folder that was never made, it failed with "wiki not found".
    import llmw2.memory

    pytest.importorskip("mcp")
    from mcp import Client

    real, made = llmw2.memory.Memory, []
    monkeypatch.setattr(llmw2.memory, "Memory", lambda make, local_files: made.append(make) or _memory())
    settings = Settings({"OKF_CONFIG": str(tmp_path / "never.json"), "OKF_BUNDLE": str(tmp_path / "wiki")})
    assert memory_stdio(Wiki(settings.config()), settings) == 0
    assert (tmp_path / "wiki").is_dir()

    async def go():
        async with Client(real(made[0], local_files=True).server) as client:
            return [await client.call_tool(tool, args) for tool, args in (("recall", {"question": "When does Rossi pay?"}),
                                                                          ("search", {"query": "Rossi"}))]

    recalled, searched = asyncio.run(go())
    assert not recalled.is_error and recalled.structured_content["status"] == "done"
    assert recalled.structured_content["notes"] == []
    assert not searched.is_error


def test_a_wiki_folder_that_cannot_be_made_is_said_on_stderr(started, tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder", encoding="utf-8")
    settings = Settings({"OKF_CONFIG": str(tmp_path / "never.json"), "OKF_BUNDLE": str(blocker / "wiki")})
    assert memory_stdio(Wiki(settings.config()), settings) == 2
    assert "cannot make the wiki" in capsys.readouterr().err
