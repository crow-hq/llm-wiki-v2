# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`llmwiki2` command line: setup, init, ingest, sync, rebuild, purge, ask, move, describe,
proposals, rename-root, merge-roots, audit, health, check, serve.

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

from llmw2.audit import AUDIT_MIN, HEALTH_MAX_SHARE, HEALTH_MIN_FILED
from llmw2.batch import deviation_line, deviations
from llmw2.bundle.files import extract_text
from llmw2.bundle.proposals import notes_count
from llmw2.config import PROVIDERS, WikiConfig
from llmw2.errors import InputError, ModelError
from llmw2.logo import show_logo
from llmw2.models.llm import probe
from llmw2.settings import Settings, default_bundle, mask, ready, tilde
from llmw2.sources import LocalFolderSource, SyncReport, documents, estimate, sync
from llmw2.wiki import Wiki


def settings_wiki(*, bundle: Path | None = None, mode: str | None = None) -> Wiki:
    return Wiki(Settings(bundle=bundle, mode=mode).config())


# Tests replace these to inject fake models or answers.
make_wiki: Callable[..., Wiki] = settings_wiki
build_wiki: Callable[[WikiConfig], Wiki] = Wiki  # after a settings change: the server and `setup`
prompt: Callable[[str], str] = input
secret: Callable[[str], str] = getpass.getpass
# the commands that may write log.md
CHANGING = ("ingest", "sync", "rebuild", "purge", "move", "describe", "proposals", "rename-root", "merge-roots", "audit")


def login() -> str | None:
    """Who runs the command, by the system: the author its log lines carry (declared, not verified); None when it cannot be told."""
    try:
        return getpass.getuser()
    except Exception:  # no user in the environment (a container), or none with an entry: the log goes without an author
        return None


def load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=VALUE lines of a .env file into the environment, never overriding what is already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))



def _where(pairs: list[str] | None) -> dict[str, str] | None:
    """`--where KEY=VALUE` (repeatable) as the filter of `ask` and `search`; None when there is none."""
    where: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise InputError(f"--where wants KEY=VALUE, not {pair!r}")
        where[key] = value
    return where or None

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
    sync_cmd.add_argument("--max-cost", type=float, metavar="USD", help="stop once the sync has spent this much (as the provider reports)")
    sync_cmd.add_argument("--estimate", action="store_true", help="say how many documents a sync would file and the likely cost")
    sync_cmd.add_argument(
        "--purge-removed", action="store_true", help="a document removed at its source is purged (notes, raw copies, archive), not marked"
    )
    sync_cmd.add_argument("--json", action="store_true", help="print the full report as JSON")
    rebuild = sub.add_parser(
        "rebuild", help="delete every note and file the documents again on the same folders (a folder's, else the wiki's raw copies)"
    )
    rebuild.add_argument("folder", type=Path, nargs="?", help="the documents to file; none: the wiki's own raw copies")
    rebuild.add_argument("--yes", action="store_true", help="really do it: notes, raw copies and archived versions are deleted")
    rebuild.add_argument("--concurrency", type=int, help="documents prepared at once, 1 to 16 (env OKF_CONCURRENCY)")
    rebuild.add_argument("--json", action="store_true", help="print the full report as JSON")
    purge = sub.add_parser("purge", help="delete a document for good: its raw copies, archived versions and notes")
    purge.add_argument("key", help="the origin key of the document: source:account:id, as the raw copies and the log carry it")
    purge.add_argument("--yes", action="store_true", help="really do it: nothing can be undone")
    purge.add_argument("--json", action="store_true", help="print the full report as JSON")
    ask = sub.add_parser("ask", help="answer a question from the wiki")
    ask.add_argument("question")
    ask.add_argument("--where", action="append", metavar="KEY=VALUE", help="only the notes whose fields have this value (repeatable)")
    ask.add_argument("--json", action="store_true", help="print the full answer as JSON")
    search = sub.add_parser("search", help="find the notes for a query, with no model call")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=8, help="how many notes (default 8)")
    search.add_argument("--where", action="append", metavar="KEY=VALUE", help="only the notes whose fields have this value (repeatable)")
    search.add_argument("--json", action="store_true", help="print the hits as JSON")
    move = sub.add_parser("move", help="move a note into another folder; its links follow")
    move.add_argument("note", help="the note's path, e.g. finance/pricing.md")
    move.add_argument("folder", help='the folder to file it in, e.g. /sourcing ("/" is the top level)')
    describe = sub.add_parser("describe", help="change a folder's one-line description, the line notes are routed by")
    describe.add_argument("folder", help="the folder, e.g. /finance")
    describe.add_argument("description")
    proposals = sub.add_parser("proposals", help="top-level areas users propose: list them, add one, confirm or dismiss it")
    actions = proposals.add_subparsers(dest="action", help="with none: list the open proposals")
    add = actions.add_parser("add", help="propose an area: a name, a description and the notes (by id) that would fill it")
    add.add_argument("label", help="the folder's name, e.g. suppliers")
    add.add_argument("--description", required=True, help="one line: what belongs in it")
    add.add_argument("notes", nargs="+", help="note ids (the `id` in a note's frontmatter)")
    confirm = actions.add_parser("confirm", help="open the area and move the proposed notes into it")
    confirm.add_argument("id")
    confirm.add_argument("--over-limit", action="store_true", help="open it although the wiki has as many top-level folders as the limit")
    dismiss = actions.add_parser("dismiss", help="close a proposal without doing anything")
    dismiss.add_argument("id")
    rename_root = sub.add_parser("rename-root", help="rename a top-level folder; its notes and the links to them follow")
    rename_root.add_argument("folder", help="the top-level folder, e.g. /finance")
    rename_root.add_argument("name", help="its new name")
    merge_roots = sub.add_parser("merge-roots", help="move all of a top-level folder into another and delete it")
    merge_roots.add_argument("source", help="the top-level folder to empty, e.g. /legal")
    merge_roots.add_argument("target", help="the top-level folder that takes its notes, e.g. /contracts")
    audit = sub.add_parser("audit", help="CROW: list the notes the classifier would file in another folder today (no LLM call)")
    audit.add_argument("--min-confidence", type=float, default=AUDIT_MIN, help=f"least confidence to move a note (default {AUDIT_MIN})")
    audit.add_argument("--apply", action="store_true", help="move the notes (otherwise only list them)")
    health = sub.add_parser("health", help="per folder and month: new subfolders and fallbacks per note filed (from log.md)")
    health.add_argument("--min-filed", type=int, default=HEALTH_MIN_FILED, help="fewest notes filed to flag a row")
    health.add_argument("--max-share", type=float, default=HEALTH_MAX_SHARE, help="largest share of new subfolders and fallbacks")
    sub.add_parser("check", help="lint the wiki (exit 1 on problems)")
    serve = sub.add_parser("serve", help="run the HTTP API and the web page")
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
    if args.command in CHANGING and wiki.author is None:
        wiki.author = login()
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


def _report(report: SyncReport, as_json: bool, stopped: str) -> int:
    """Print what a sync or a rebuild did (JSON, or counts and the notes the limit on top-level folders deviated); its exit code."""
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        counts = (f"{name} {count}" for name, count in report.to_dict().items() if name not in ("errors", "stopped", "deviations"))
        print(", ".join(counts))
        for d in report.deviations:
            print(f"deviated: {deviation_line(d)}: the limit of top-level folders was reached")
        for key, message in report.errors:
            print(f"error: {key}: {message}", file=sys.stderr)
    if report.stopped:
        print(f"llmwiki2: {stopped}: {report.stopped}", file=sys.stderr)
        return 2
    return 1 if report.errors else 0


def _purge(wiki: Wiki, args: argparse.Namespace) -> int:
    """`purge`: say what would go (without `--yes`, exit 2), or delete it and print what went; its exit code."""
    done = wiki.purge(args.key, dry_run=not args.yes)
    if done is None:
        print(f"llmwiki2: no raw copy of {tilde(wiki.cfg.bundle)} has the origin key {args.key}", file=sys.stderr)
        return 1
    if not args.yes:
        print(
            f"purge would delete {len(done.notes)} note(s), {len(done.sources)} raw copy(ies) and {len(done.archived)} archived "
            f"version(s) of {tilde(wiki.cfg.bundle)}, and file {len(done.refiled)} other document(s) again. "
            "Nothing was done: run it again with --yes.",
            file=sys.stderr,
        )
        return 2
    if args.json:
        print(json.dumps(done.to_dict(), indent=2))
    else:
        for name, items in (("deleted note", done.notes), ("deleted raw copy", done.sources), ("deleted archived", done.archived),
                            ("unlinked", done.unlinked), ("filed again", done.refiled), ("filed", done.filed)):
            for item in items:
                print(f"{name}: {item}")
        print(f"{len(done.notes)} note(s), {len(done.sources)} raw copy(ies), {len(done.archived)} archived version(s) deleted; "
              f"{len(done.refiled)} document(s) filed again; {done.log_lines} log line(s) purged")
        for key, message in done.errors:
            print(f"error: {key}: {message}", file=sys.stderr)
    if done.stopped:
        print(f"llmwiki2: purge stopped, the documents not filed again keep their raw copies: {done.stopped}", file=sys.stderr)
        return 2
    return 1 if done.errors else 0


def _estimate(wiki: Wiki, folder: Path, as_json: bool) -> None:
    """Print what a sync of `folder` would file and cost; with no history of syncs, advise a cap."""
    found = estimate(wiki, LocalFolderSource(folder))
    if as_json:
        print(json.dumps(found, indent=2))
    elif found["est_cost"] is None:
        print(f"{found['documents']} document(s) to file; no cost estimate yet ({found['basis']}): use --max-cost to cap the first sync")
    else:
        print(f"{found['documents']} document(s) to file, about ${found['est_cost']:.2f} ({found['basis']})")


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
            for one in [result, *result.parts]:  # a spreadsheet: one note per sheet
                created = f" (new folders: {', '.join(one.created_folders)})" if one.created_folders else ""
                print(f"{one.action}: {one.note}{created}")
            for d in deviations([result]):
                print(f"deviated: {deviation_line(d)}: the limit of top-level folders was reached")
    elif args.command == "sync":
        if args.estimate:
            _estimate(wiki, args.folder, args.json)
            return 0
        done = sync(
            wiki, LocalFolderSource(args.folder), concurrency=args.concurrency, max_cost=args.max_cost, purge_removed=args.purge_removed
        )
        if code := _report(done, args.json, "sync stopped, run it again to go on"):
            return code
    elif args.command == "rebuild":
        items = documents(LocalFolderSource(args.folder)) if args.folder else wiki.raw_items()  # read before anything is deleted
        source = f"the documents of {args.folder}" if args.folder else "the wiki's own raw copies"
        if not args.yes:
            print(
                f"rebuild would delete every note, raw copy and archived version of {tilde(wiki.cfg.bundle)}, keep its folders, "
                f"and file {len(items)} document(s) again from {source}. Nothing was done: run it again with --yes.",
                file=sys.stderr,
            )
            return 2
        report = SyncReport()
        report.add(wiki.rebuild(items, concurrency=args.concurrency))
        if code := _report(report, args.json, "rebuild stopped, run it again to start over"):
            return code
    elif args.command == "purge":
        return _purge(wiki, args)
    elif args.command == "ask":
        answer = wiki.ask(args.question, _where(args.where))
        print(json.dumps(answer.to_dict(), indent=2) if args.json else answer.text)
    elif args.command == "search":
        hits = wiki.search(args.query, k=args.k, where=_where(args.where))
        if args.json:
            print(json.dumps([h.to_dict() for h in hits], indent=2))
        for h in [] if args.json else hits:
            print(f"{h.rank}. {h.path} — {h.title}" + (f": {h.summary}" if h.summary else ""))
    elif args.command == "move":
        print(f"moved: /{wiki.move_note(args.note, args.folder).rel}")
    elif args.command == "describe":
        folder = wiki.describe_folder(args.folder, args.description)
        print(f"{folder.label}: {folder.description}")
    elif args.command == "proposals":
        if args.action == "add":
            new = wiki.propose_area(args.label, args.description, args.notes)
            print(f"proposed: {new.id}  /{new.label}: {new.description} ({notes_count(len(new.notes))})")
        elif args.action == "confirm":
            opened, filed = wiki.confirm_proposal(args.id, over_limit=args.over_limit)
            print(f"confirmed: {opened.id}  /{opened.label}: {notes_count(len(filed))} moved")
        elif args.action == "dismiss":
            print(f"dismissed: {wiki.dismiss_proposal(args.id).id}")
        else:
            queue = wiki.proposals()
            for item in queue:
                print(f"{item.id}  /{item.label}: {item.description} ({notes_count(len(item.notes))})")
            print(f"{len(queue)} open proposal(s)")
    elif args.command == "rename-root":
        print(f"renamed: {wiki.rename_root(args.folder, args.name).label}")
    elif args.command == "merge-roots":
        print(f"merged into: {wiki.merge_roots(args.source, args.target).label}")
    elif args.command == "audit":
        proposals = wiki.audit_routes(args.min_confidence, apply=args.apply)
        for p in proposals:
            print(f"{p.id or '-'}  /{p.path}  {p.current} → {p.proposed}  {p.confidence:.2f}" + ("  moved" if p.applied else ""))
        moved = f", {sum(p.applied for p in proposals)} moved" if args.apply else ""
        print(f"{len(proposals)} proposal(s){moved}; classifier {wiki.usage.classifier.calls} calls")
    elif args.command == "health":
        rows = wiki.taxonomy_health(args.min_filed, args.max_share)
        if rows:
            width = max(len(r.folder) for r in rows)
            print(f"  {'month':7}  {'folder':{width}}  filed  new  fallback  share")
        for r in rows:
            cells = f"{r.month:7}  {r.folder:{width}}  {r.filed:5}  {r.new_subfolders:3}  {r.fallbacks:8}  {r.share:.2f}"
            print(f"{'!' if r.flag else ' '} {cells}")
        print(f"{sum(r.flag for r in rows)} flagged of {len(rows)} row(s)")
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

    if args.command in ("ingest", "sync", "rebuild", "purge", "ask", "audit"):
        print(wiki.usage.summary(), file=sys.stderr)
    return 0
