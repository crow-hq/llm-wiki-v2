# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`llmwiki2` command line: setup, init, ingest, sync, ask, check, serve.

`llmwiki2` alone serves the wiki and opens it in the browser, where the settings
page asks for what is missing; `llmwiki2 setup` asks the same in the terminal.
Configuration: the settings file (see `settings.py`), under `OKF_*` environment
variables (and a `.env` file in the current folder), under `--bundle` and `--mode`.
A one-line token summary goes to stderr.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import shutil
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from llmw2.bundle.files import extract_text
from llmw2.config import PROVIDERS, WikiConfig
from llmw2.errors import ModelError
from llmw2.logo import show_logo
from llmw2.models.llm import probe
from llmw2.settings import Settings, default_bundle, mask, ready, tilde
from llmw2.sources import LocalFolderSource, sync
from llmw2.wiki import Wiki


def settings_wiki(*, bundle: Path | None = None, mode: str | None = None) -> Wiki:
    return Wiki(Settings(bundle=bundle, mode=mode).config())


# Tests replace these to inject fake models or answers.
make_wiki: Callable[..., Wiki] = settings_wiki
build_wiki: Callable[[WikiConfig], Wiki] = Wiki  # after a settings change: the server and `setup`
prompt: Callable[[str], str] = input
secret: Callable[[str], str] = getpass.getpass


def load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=VALUE lines of a .env file into the environment, never overriding what is already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main(argv: list[str] | None = None) -> int:
    if argv is None:  # a real command line: read ./.env like docker compose does
        load_dotenv()
    parser = argparse.ArgumentParser(prog="llmwiki2", description="LLM wiki on the Open Knowledge Format")
    parser.add_argument("--bundle", type=Path, help="wiki folder (env OKF_BUNDLE)")
    parser.add_argument("--mode", choices=["classic", "crow"], help="decider (env OKF_MODE)")
    sub = parser.add_subparsers(dest="command", help="with none: serve and open the browser")

    sub.add_parser("setup", help="choose the model provider, key and model, and test them")
    sub.add_parser("init", help="create an empty wiki")
    ingest = sub.add_parser("ingest", help="file a source: a .txt/.md/.pdf path, or - for stdin")
    ingest.add_argument("source")
    ingest.add_argument("--title")
    ingest.add_argument("--resource", help="URL or path of the original")
    ingest.add_argument("--json", action="store_true", help="print the full result as JSON")
    sync_cmd = sub.add_parser("sync", help="file a folder's documents and keep the wiki in step with it")
    sync_cmd.add_argument("folder", type=Path)
    sync_cmd.add_argument("--concurrency", type=int, help="documents prepared at once, 1 to 16 (env OKF_CONCURRENCY)")
    sync_cmd.add_argument("--json", action="store_true", help="print the full report as JSON")
    ask = sub.add_parser("ask", help="answer a question from the wiki")
    ask.add_argument("question")
    ask.add_argument("--json", action="store_true", help="print the full answer as JSON")
    sub.add_parser("check", help="lint the wiki (exit 1 on problems)")
    serve = sub.add_parser("serve", help="run the HTTP API (needs the [server] extra)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--open", action="store_true", help="open the wiki in the browser")
    demo = sub.add_parser("demo", help="open an example wiki (a copy in ~/llm-wiki-demo)")
    demo.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args([*(sys.argv[1:] if argv is None else argv), "serve", "--open"])
    if args.command == "setup":
        try:
            return setup(Settings(bundle=args.bundle, mode=args.mode))
        except (ValueError, EOFError, KeyboardInterrupt) as e:
            print(f"\nllmwiki2: {str(e) or 'setup cancelled'}", file=sys.stderr)
            return 2
    if args.command == "demo":
        args.bundle = copy_demo(args.bundle or default_bundle().with_name("llm-wiki-demo"))
        args.command, args.host, args.open = "serve", "127.0.0.1", True
    try:
        wiki = make_wiki(bundle=args.bundle, mode=args.mode)
    except Exception as e:  # the CLI's outer boundary: a one-line message, never a traceback, for a beginner
        print(f"llmwiki2: {e}", file=sys.stderr)
        return 2
    try:
        return _run(wiki, args)
    except (ModelError, ValueError, OSError) as e:  # model down, empty input, missing file
        hint = "" if not isinstance(e, ModelError) or ready(wiki.cfg) else " (no API key yet: run llmwiki2 setup)"
        print(f"llmwiki2: {e}{hint}", file=sys.stderr)
        return 2
    finally:
        wiki.close()


def copy_demo(to: Path) -> Path:
    """The example wiki, copied to `to` the first time; later runs reopen that copy with your changes."""
    if to.exists():
        print(f"Demo wiki: {tilde(to)} (delete the folder to start it over)")
    else:
        with resources.as_file(resources.files("llmw2") / "demo") as source:
            shutil.copytree(source, to)
        print(f"Demo wiki copied to {tilde(to)}: a fictional coffee roastery to browse and ask about")
    return to


def setup(settings: Settings) -> int:
    """Ask for provider, key and model, test them with one request, save them."""
    view = settings.view()
    now, managed = view["values"], set(view["managed"])
    show_logo()
    print(f"Settings file: {tilde(settings.path)}")
    changes: dict[str, str] = {}

    def ask(field: str, question: str, default: str) -> str:
        if field in managed:
            print(f"{question}: {default} (set by the environment or a flag)")
            return default
        return prompt(f"{question} [{default}]: ").strip() or default

    provider = ask("provider", f"Provider ({', '.join(PROVIDERS)})", now["provider"])
    while provider not in PROVIDERS:
        provider = prompt(f"Provider: choose one of {', '.join(PROVIDERS)}: ").strip()
    changes["provider"] = provider
    preset = PROVIDERS[provider]
    if provider == "custom":
        changes["base_url"] = ask("base_url", "Base URL (OpenAI-compatible, ending in /v1)", now["base_url"] or "http://localhost:8000/v1")
    else:
        changes["base_url"] = ""
    if (preset.key_env or provider == "custom") and "api_key" not in managed:
        kept = f"Enter keeps {now['api_key']}" if now["api_key"] else "Enter for none"
        changes["api_key"] = secret(f"API key ({kept}): ").strip()
    same = provider == now["provider"]
    changes["model"] = ask("model", "Model" + (f" (e.g. {', '.join(preset.models)})" if preset.models else ""),
                           now["model"] if same else next(iter(preset.models), ""))

    _, cfg = settings.apply(changes)
    if cfg.mode == "crow" and not cfg.classifier.ready and "classifier_api_key" not in managed:
        # CROW asks its classifier at its own provider (OpenRouter unless set otherwise): only OpenRouter's LLM key serves it too.
        key = secret(f"CROW classifier key ({cfg.classifier.provider}; Enter to use classic mode instead): ").strip()
        if key:
            changes["classifier_api_key"] = key
        elif "mode" not in managed:
            changes["mode"] = "classic"
            print("Classic mode: the model decides where notes go.")
        _, cfg = settings.apply(changes)
    print(f"Testing {cfg.llm.model} at {cfg.llm.base_url} … ", end="", flush=True)
    try:
        with build_wiki(cfg) as trial:
            probe(trial.llm)
        print("OK")
    except ModelError as e:
        print(f"failed: {e}")
        if prompt("Save anyway? [y/N]: ").strip().lower() != "y":
            return 1
    settings.save(changes)
    print(f"Saved ({cfg.mode} mode, API key {mask(cfg.llm.api_key) or 'none'}). Start the wiki with: llmwiki2")
    return 0


def _run(wiki: Wiki, args: argparse.Namespace) -> int:
    if args.command == "init":
        wiki.init()
        print(f"wiki ready at {wiki.cfg.bundle}")
    elif args.command == "ingest":
        if args.source == "-":
            result = wiki.ingest(sys.stdin.read(), title=args.title, resource=args.resource)
        elif args.title:
            path = Path(args.source)
            text = extract_text(path.read_bytes(), path.name)
            result = wiki.ingest(text, title=args.title, resource=args.resource or args.source)
        else:
            path = Path(args.source)
            result = wiki.ingest_file(path.read_bytes(), path.name, resource=args.resource or args.source)
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            created = f" (new folders: {', '.join(result.created_folders)})" if result.created_folders else ""
            print(f"{result.action}: {result.note}{created}")
    elif args.command == "sync":
        report = sync(wiki, LocalFolderSource(args.folder), concurrency=args.concurrency)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            counts = (f"{name} {count}" for name, count in report.to_dict().items() if name not in ("errors", "stopped"))
            print(", ".join(counts))
            for key, message in report.errors:
                print(f"error: {key}: {message}", file=sys.stderr)
        if report.stopped:
            print(f"llmwiki2: sync stopped, run it again to go on: {report.stopped}", file=sys.stderr)
        if report.stopped:
            return 2
        if report.errors:
            return 1
    elif args.command == "ask":
        answer = wiki.ask(args.question)
        print(json.dumps(answer.to_dict(), indent=2) if args.json else answer.text)
    elif args.command == "check":
        problems = wiki.check()
        for problem in problems:
            print(problem)
        print(f"{len(problems)} problem(s)", file=sys.stderr)
        return 1 if problems else 0
    elif args.command == "serve":
        from llmw2.server import serve

        # One line per decision and per model call, next to uvicorn's request log.
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(message)s")
        logging.getLogger("llmw2").setLevel(logging.INFO)
        serve(wiki, host=args.host, port=args.port, settings=Settings(bundle=args.bundle, mode=args.mode),
              make_wiki=build_wiki, open_browser=args.open)

    if args.command in ("ingest", "sync", "ask"):
        print(wiki.usage.summary(), file=sys.stderr)
    return 0
