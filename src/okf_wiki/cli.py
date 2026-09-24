# Copyright 2026 Federico Cesarini
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

"""`okf-wiki` command line: init, ingest, ask, check, visualize, serve, bench.

Configuration comes from `OKF_*` environment variables (and a `.env` file in the
current folder, the one Docker uses); `--bundle` and `--mode` override them.
A one-line token summary goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from okf_wiki import bench
from okf_wiki.client import ModelError
from okf_wiki.config import WikiConfig
from okf_wiki.files import extract_text
from okf_wiki.wiki import Wiki

# Tests replace these to inject fake models.
make_wiki: Callable[..., Wiki] = Wiki.from_env
make_bench_wiki: Callable[[WikiConfig], Wiki] = Wiki


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
    parser = argparse.ArgumentParser(prog="okf-wiki", description="LLM wiki on the Open Knowledge Format")
    parser.add_argument("--bundle", type=Path, help="wiki folder (env OKF_BUNDLE)")
    parser.add_argument("--mode", choices=["classic", "crow"], help="decider (env OKF_MODE)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create an empty wiki")
    ingest = sub.add_parser("ingest", help="file a source: a .txt/.md/.pdf path, or - for stdin")
    ingest.add_argument("source")
    ingest.add_argument("--title")
    ingest.add_argument("--resource", help="URL or path of the original")
    ingest.add_argument("--json", action="store_true", help="print the full result as JSON")
    ask = sub.add_parser("ask", help="answer a question from the wiki")
    ask.add_argument("question")
    ask.add_argument("--json", action="store_true", help="print the full answer as JSON")
    sub.add_parser("check", help="lint the wiki (exit 1 on problems)")
    viz = sub.add_parser("visualize", help="write an HTML graph of the wiki")
    viz.add_argument("--out", type=Path)
    serve = sub.add_parser("serve", help="run the HTTP API (needs the [server] extra)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    b = sub.add_parser("bench", help="catalogue the same .md files into a classic and a CROW wiki and compare")
    b.add_argument("--data", default="bench-data", help="folder searched recursively for .md files (default bench-data)")
    b.add_argument("--limit", type=int, help="catalogue at most N files (default: all)")
    b.add_argument("--out", help="output folder (default bench-runs/<timestamp>)")
    b.add_argument("--modes", default="classic,crow", help="comma-separated modes to compare")
    b.add_argument("--seed", type=int, help="shuffle seed for the file order (default: a random one, saved in run.json)")
    b.add_argument("--no-pdf", action="store_true", help="skip report.pdf (written by default when the pdf extra is installed)")
    b.add_argument("--llm-price", help="LLM USD per million tokens 'IN,OUT' (default: OpenRouter list price)")
    b.add_argument("--classifier-price", help="classifier USD per million tokens 'IN,OUT' (default: Jev 0.042,0)")
    again = b.add_mutually_exclusive_group()
    again.add_argument("--resume", metavar="RUN", help="continue a stopped run folder (its saved settings are reused)")
    again.add_argument("--report", metavar="RUN", help="rebuild the report of a run folder from its saved data, no model calls")

    args = parser.parse_args(argv)
    if args.command == "bench":
        try:
            cfg = WikiConfig.from_env(bundle=Path(args.out or "bench-runs"))
        except Exception as e:
            print(f"okf-wiki: {e}", file=sys.stderr)
            return 2
        return bench.main(args, cfg, make_wiki=make_bench_wiki)
    try:
        wiki = make_wiki(bundle=args.bundle, mode=args.mode)
    except Exception as e:  # e.g. no bundle configured
        print(f"okf-wiki: {e}", file=sys.stderr)
        return 2
    try:
        return _run(wiki, args)
    except (ModelError, ValueError, OSError) as e:  # model down, empty input, missing file
        print(f"okf-wiki: {e}", file=sys.stderr)
        return 2


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
            print(f"{result.action}: {result.note}" + (f" (new folders: {', '.join(result.created_folders)})" if result.created_folders else ""))
    elif args.command == "ask":
        answer = wiki.ask(args.question)
        print(json.dumps(answer.to_dict(), indent=2) if args.json else answer.text)
    elif args.command == "check":
        problems = wiki.check()
        for problem in problems:
            print(problem)
        print(f"{len(problems)} problem(s)", file=sys.stderr)
        return 1 if problems else 0
    elif args.command == "visualize":
        print(wiki.visualize(args.out))
    elif args.command == "serve":
        from okf_wiki.server import serve

        serve(wiki, host=args.host, port=args.port)

    if args.command in ("ingest", "ask"):
        print(wiki.usage.summary(), file=sys.stderr)
    return 0
