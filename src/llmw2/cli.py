# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`llmwiki2` command line: setup, init, ingest, sync, rebuild, purge, ask, move, describe,
proposals, rename-root, merge-roots, audit, health, check, serve, mcp.

`llmwiki2` alone serves the wiki and opens it in the browser, where the settings
page asks for what is missing; `llmwiki2 setup` asks the same in the terminal, and
sets the wiki up as the memory of an agentic CLI (Claude Code, Codex) with no page:
`llmwiki2 mcp` is that memory over stdio, started by the client itself.
Configuration: the settings file (see `settings.py`), under `OKF_*` environment
variables (and a `.env` file in the current folder), under `--bundle` and `--mode`.
A one-line token summary goes to stderr.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import argparse
import getpass
import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from llmw2.audit import AUDIT_MIN, HEALTH_MAX_SHARE, HEALTH_MIN_FILED
from llmw2.batch import deviation_line, deviations
from llmw2.bundle.files import extract_text
from llmw2.bundle.proposals import notes_count
from llmw2.config import CLASSIFIER_PROVIDERS, NAMES, PROVIDERS, WikiConfig
from llmw2.errors import InputError, ModelError
from llmw2.logo import show_logo
from llmw2.models.classifier import Classifier, probe_config
from llmw2.models.classifier import probe as probe_classifier
from llmw2.models.llm import probe
from llmw2.models.usage import UsageTracker
from llmw2.settings import Settings, default_bundle, mask, ready, tilde
from llmw2.sources import LocalFolderSource, SyncReport, documents, estimate, sync
from llmw2.wiki import Wiki


def settings_wiki(*, bundle: Path | None = None, mode: str | None = None) -> Wiki:
    return Wiki(Settings(bundle=bundle, mode=mode).config())


# Tests replace these to inject fake models or answers.
make_wiki: Callable[..., Wiki] = settings_wiki
build_wiki: Callable[[WikiConfig], Wiki] = Wiki  # after a settings change: the server and `setup`
build_classifier: Callable[[WikiConfig], Classifier] = lambda cfg: Classifier(cfg.classifier, UsageTracker())  # noqa: E731
prompt: Callable[[str], str] = input
secret: Callable[[str], str] = getpass.getpass
run_command: Callable[[list[str], dict[str, str]], subprocess.CompletedProcess[str]] = lambda cmd, env: subprocess.run(  # noqa: E731
    cmd, capture_output=True, text=True, check=False, env=env
)
which: Callable[[str], str | None] = shutil.which
home: Callable[[], Path] = Path.home
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
    sub.add_parser("mcp", help="the wiki as an MCP memory over stdio, for a client that starts it (claude mcp add … -- llmwiki2 mcp)")
    demo = sub.add_parser("demo", help="open an example wiki (a copy in ~/llm-wiki-demo)")
    demo.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args([*(sys.argv[1:] if argv is None else argv), "serve", "--open"])
    if args.command == "setup":
        try:
            return setup(Settings(bundle=args.bundle, mode=args.mode), bundle=args.bundle)
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


USES = (
    ("page", "With its web page and this command line: a model provider files your documents and answers"),
    ("memory", "As the memory of an agentic CLI (Claude Code, Codex), from the terminal only, with no page"),
)
WORKERS = (
    ("client", "The CLI's own models, on its plan, with no key. Slower: a memory takes about a minute"),
    ("hybrid", "Hybrid: the CLI writes, and a small classifier (Jev through OpenRouter, or a local Laya or Ollama) decides "
               "where things go. Quicker, and on the CLI's plan but for the classifier"),
    ("wiki", "A model provider for everything (OpenRouter, Ollama, …): the CLI only calls the memory"),
)
# Each agentic CLI: its command, its name, the variable that picks its configuration folder, the default folder in the
# home (others sit next to it: ~/.claude-work), the file of instructions it reads there, and what tells such a folder.
CLIENTS = (
    ("claude", "Claude Code", "CLAUDE_CONFIG_DIR", ".claude", "CLAUDE.md", (".credentials.json", "settings.json", "projects")),
    ("codex", "Codex", "CODEX_HOME", ".codex", "AGENTS.md", ("auth.json", "config.toml", "sessions")),
)
# The lines an agentic CLI reads at every session, so it calls the memory when the user speaks in plain words.
HINT_START, HINT_END = "<!-- llm-wiki memory -->", "<!-- /llm-wiki memory -->"
HINT = f"""{HINT_START}
## Long-term memory (llm-wiki)

The MCP server `llm-wiki` is my long-term memory. I talk to you in plain words, in any language, and never name its tools:
- when I ask you to remember, note or keep something ("ricorda che…", "remember that…"), call its `remember`;
- when I ask about my work, my documents, people or anything from before ("ti ricordi…", "what did we decide…"), call its
  `recall` first and cite the notes;
- when I ask you to add or import a file or a folder, call `remember_file` or `sync_folder` with its path.
{HINT_END}
"""


def choose(question: str, options: tuple[tuple[str, str], ...], default: int = 0) -> str:
    """One of `options` (key, text), picked by its number; Enter takes `default`."""
    print(question)
    for i, (_, text) in enumerate(options, 1):
        print(f"  {i}. {text}")
    while True:
        got = prompt(f"Choice [{default + 1}]: ").strip() or str(default + 1)
        if got.isdigit() and 1 <= int(got) <= len(options):
            return options[int(got) - 1][0]
        print(f"Type a number from 1 to {len(options)}.")


def setup(settings: Settings, bundle: Path | None = None) -> int:
    """Ask how the wiki will be used. With its page: provider, key and model, tested with one request, then saved. As the
    memory of an agentic CLI: whose models do the work, then the memory is turned on and the CLIs found are connected."""
    view = settings.view()
    now, managed = view["values"], set(view["managed"])
    show_logo()
    print(f"Settings file: {tilde(settings.path)}")
    if choose("How will you use the wiki?", USES, default=1 if now["mcp"] else 0) == "page":
        changes: dict[str, str] = {}
        if not _models(settings, now, managed, changes):
            return 1
        cfg = settings.save(changes)
        print(f"Saved ({cfg.mode} mode, API key {mask(cfg.llm.api_key) or 'none'}). Start the wiki with: llmwiki2")
        return 0
    keys = [key for key, _ in WORKERS]
    # The memory set up before keeps its choice; a first one starts from the CLI's own models, which need no key.
    worker = choose("Which models do the memory's work?", WORKERS, default=keys.index(now["mcp_models"] if now["mcp"] else "client"))
    changes = {"mcp": "true", "mcp_models": "" if worker == "wiki" else worker}
    if worker == "wiki" and not _models(settings, now, managed, changes):
        return 1
    if worker == "hybrid" and not _classifier(settings, now, managed, changes):
        return 1
    settings.save(changes, require_keys=worker == "wiki")
    print("Saved: the memory is on.")
    connect(bundle)
    return 0


def _models(settings: Settings, now: dict[str, str], managed: set[str], changes: dict[str, str]) -> bool:
    """Ask for provider, key and model into `changes`, and test them with one request; False when the user gives up."""

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
        return prompt("Save anyway? [y/N]: ").strip().lower() == "y"
    return True


def _classifier(settings: Settings, now: dict[str, str], managed: set[str], changes: dict[str, str]) -> bool:
    """Ask for the CROW classifier into `changes`, and test it with one request; False when the user gives up."""
    local = "custom"
    options = (
        ("openrouter", "Jev through OpenRouter (an OpenRouter key)"),
        ("typesafe", "Jev through TypeSafe (a TypeSafe key)"),
        (local, "A System One server on this computer: Ollama 0.35 or later, or Laya"),
    )
    keys = [key for key, _ in options]
    if "classifier_provider" in managed:
        provider = now["classifier_provider"]
        print(f"Classifier: {provider} (set by the environment or a flag)")
    else:
        provider = choose("Which classifier decides?", options, default=keys.index(now["classifier_provider"] or "openrouter"))
    changes["classifier_provider"] = provider
    if provider == local:
        if "classifier_base_url" not in managed:
            url = now["classifier_base_url"] or "http://localhost:11434/v1"
            question = f"Its URL, under which it serves /systemone (Laya: http://127.0.0.1:8001/v1) [{url}]: "
            changes["classifier_base_url"] = prompt(question).strip() or url
        if "classifier_model" not in managed:
            model = now["classifier_model"] if now["classifier_provider"] == local else CLASSIFIER_PROVIDERS[local].models[0]
            changes["classifier_model"] = prompt(f"Its model [{model}]: ").strip() or model
    elif "classifier_api_key" not in managed:
        kept = f"Enter keeps {now['classifier_api_key']}" if now["classifier_api_key"] else "Enter for none"
        changes["classifier_api_key"] = secret(f"{NAMES[provider]} API key ({kept}): ").strip()
    _, cfg = settings.apply(changes)
    slow = " (a model on this computer may take a minute to load)" if provider == local else ""
    print(f"Testing {cfg.classifier.model} at {cfg.classifier.base_url}{slow} … ", end="", flush=True)
    try:
        classifier = build_classifier(probe_config(cfg))
        try:
            probe_classifier(classifier)
        finally:
            classifier.close()
        print("OK")
    except ModelError as e:
        print(f"failed: {e}")
        return prompt("Save anyway? [y/N]: ").strip().lower() == "y"
    return True


def memory_command(bundle: Path | None) -> list[str]:
    """How a client starts this memory over stdio: this very Python, which runs `setup` and so is known to work.

    Not `llmwiki2` on the PATH: it may be another install, or a launcher the system refuses to run (Windows application
    control blocks new executables such as the one `uv tool install` writes)."""
    command = [sys.executable, "-m", "llmwiki2"]
    return [*command, "--bundle", str(bundle.expanduser().resolve()), "mcp"] if bundle else [*command, "mcp"]


def profiles(variable: str, default: str, markers: tuple[str, ...]) -> list[Path]:
    """A CLI's configuration folders: the default one in the home, its siblings (~/.claude-work) that hold a configuration,
    and the one `variable` names; the default first."""
    base = home()
    found = [base / default]
    found += [d for d in sorted(base.glob(f"{default}-*")) if d.is_dir() and any((d / m).exists() for m in markers)]
    named = os.environ.get(variable)
    if named and Path(named).expanduser().resolve() not in {d.resolve() for d in found}:
        found.append(Path(named).expanduser())
    return found


def pick(name: str, folders: list[Path]) -> list[Path]:
    """The configuration folders the user wants connected: a yes for one, numbers (Enter: all, 0: none) for several."""
    if len(folders) == 1:
        answer = prompt(f"Connect {name} ({tilde(folders[0])}) to the memory now? [Y/n]: ").strip().lower()
        return folders if answer in ("", "y", "yes") else []
    print(f"{name} has {len(folders)} configurations:")
    for i, folder in enumerate(folders, 1):
        print(f"  {i}. {tilde(folder)}")
    while True:
        answer = prompt("Which to connect to the memory? Numbers separated by commas, Enter for all, 0 for none: ").strip()
        if not answer:
            return folders
        if answer == "0":
            return []
        numbers = [part.strip() for part in answer.split(",")]
        if all(n.isdigit() and 1 <= int(n) <= len(folders) for n in numbers):
            return [folders[int(n) - 1] for n in dict.fromkeys(numbers)]
        print(f"Type numbers from 1 to {len(folders)}, such as 1,2.")


def connect(bundle: Path | None) -> None:
    """Register the memory with each agentic CLI found on the PATH, in the configurations the user picks, and add the lines
    that make it call the memory when the user speaks in plain words, when the user says yes; else print what to type."""
    command = memory_command(bundle)
    line = " ".join(f'"{part}"' if " " in part else part for part in command)
    found = False
    for cli_name, name, variable, default, hint_name, markers in CLIENTS:
        exe = which(cli_name)
        if exe is None:
            continue
        found = True
        chosen = pick(name, profiles(variable, default, markers))
        connected = []
        for folder in chosen:
            env = {k: v for k, v in os.environ.items() if k != variable}  # the default folder: no variable at all
            if folder != home() / default:
                env[variable] = str(folder)
            scope = ["--scope", "user"] if cli_name == "claude" else []
            run_command([exe, "mcp", "remove", *scope, "llm-wiki"], env)  # an older one, by HTTP or stdio: none is fine
            added = run_command([exe, "mcp", "add", *scope, "llm-wiki", "--", *command], env)
            if added.returncode != 0:
                print(f"{name} ({tilde(folder)}) did not take it: {(added.stderr or added.stdout).strip()}")
                continue
            print(f"{name} ({tilde(folder)}): connected. It starts the memory by itself: no server to keep running.")
            connected.append(folder)
        if not connected:
            continue
        files = [folder / hint_name for folder in connected]
        where = ", ".join(tilde(f) for f in files)
        if prompt(f"Add to {where} the lines that make {name} use the memory when you talk in plain words? [Y/n]: ").strip().lower() in (
            "", "y", "yes"
        ):
            for file in files:
                write_hint(file)
            print(f"Written in {where}.")
    if not found:
        print("No Claude Code or Codex found on the PATH. Connect yours with:")
    else:
        print("Any other MCP client:")
    print(f"  claude mcp add --scope user llm-wiki -- {line}")
    print(f"  codex mcp add llm-wiki -- {line}")
    print('Then talk to it in plain words, in any language: "remember that…", "what did we decide about…".')
    print("To see the brain or browse the notes, whenever you like: llmwiki2")


def write_hint(path: Path) -> None:
    """`HINT` in the file at `path`, in place of an older one, else at its end; the rest of the file as it was."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    start, end = text.find(HINT_START), text.find(HINT_END)
    if start != -1 and end > start:
        text = text[:start] + HINT + text[end + len(HINT_END):].lstrip("\n")
    else:
        text = text + ("\n" if text and not text.endswith("\n") else "") + ("\n" if text else "") + HINT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def memory_stdio(wiki: Wiki) -> int:
    """`llmwiki2 mcp`: the wiki as an MCP memory over stdio, on whatever the settings say, until the client closes it.

    Nothing goes to stdout but the protocol: messages go to stderr. The tools that read this computer's files are on, as
    the memory runs as the user who started the client.
    """
    try:
        from llmw2.memory import Memory
    except ImportError:
        print("llmwiki2: the memory needs the mcp extra: pip install 'llmwiki2[mcp]' (or uv tool install 'llmwiki2[mcp]')",
              file=sys.stderr)
        return 2
    wiki.cfg.mcp = True  # started for this: on, whatever the settings say
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s", stream=sys.stderr)
    Memory(lambda: wiki, local_files=True).server.run("stdio")
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
    elif args.command == "mcp":
        return memory_stdio(wiki)
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
