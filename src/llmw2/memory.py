# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The wiki as an MCP memory: Claude Code, Codex or any MCP client recalls from it and files into it, through CROW.

    claude mcp add --transport http --scope user llm-wiki http://127.0.0.1:8000/mcp
    codex mcp add llm-wiki --url http://127.0.0.1:8000/mcp

Its tools are served over Streamable HTTP, at /mcp of the wiki's own server:
- `recall`: a question, answered with the notes the answer cites;
- `remember`: a text, filed like any note;
- `read_note`: one note in full;
- `reply`: the client's replies, when its models do the memory's work.

Whose models do that work is the setting `mcp_models`:
- "wiki": this wiki's own models (OpenRouter, a local model, Jev or Laya), and the client only calls the memory;
- "client": the client's models, on its own plan, with no key. `recall` and `remember` start the work, and its model
  calls come back to the client as requests (see models.relay). Claude Code answers a decision with a Haiku subagent
  and writes with its own model; `reply` hands the answers over until the result comes.

Its tools work while the settings turn the memory on (`mcp`: OKF_MCP, or Start under Memory on the page). Off, a client
still connects and finds them, and they answer that the memory is off and how to start it: a client added before Start
works from its next call after it, with no reconnecting. The memory is stateless and replies with plain JSON: no session
to keep and no stream left open, whatever protocol version the client speaks. The server's guard is its only Host and
Origin check (see `llmw2.server`), so the SDK's is off.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from llmw2 import __version__
from llmw2.agents.librarian import IngestResult
from llmw2.agents.researcher import Answer
from llmw2.errors import WikiError
from llmw2.models.relay import Relay, RelayClassifier, RelayLLM, relayed
from llmw2.models.usage import UsageTracker
from llmw2.wiki import Wiki

T = TypeVar("T")

# What the client's model reads about this memory when it connects.
INSTRUCTIONS = """\
Your long-term memory: a wiki of Markdown notes on this computer, which already holds the user's documents. \
A typed classifier decides where each note goes and a model writes it (CROW).
- Call `recall` before you answer about the user's work, projects, documents, decisions or preferences, and whenever \
they mention something from before. Cite the notes it returns as [path.md].
- Call `remember` when something durable is settled: a decision and why, a fix that worked, a preference, a fact about \
a project or a person. One topic per call, written to make sense without this conversation.
- Call `read_note` to read a cited note in full.
- When `recall` or `remember` returns requests instead of a result, the memory works with your models: follow its `how`.
Never store secrets: keys, passwords, tokens."""
# How the client does the memory's model work, sent with the requests.
HOW = (
    "The memory works with your models. Answer every request, then call reply with this job and "
    '{"<request id>": "<your reply>"}; it returns the next requests, or the result. '
    "When its status is waiting, the memory is still at work: call reply with the job and {} until it is done. "
    "A decide request is a typed decision for a light, fast model: in Claude Code, run each with a subagent on model "
    "haiku (the Agent tool), all of them at once, giving it the request's system text then its prompt, and reply with "
    "what it returns, verbatim; elsewhere, answer it the same way yourself. "
    "A write request is for you: answer its prompt, following its system text, with exactly what it asks for "
    "(often one JSON object) and nothing else. Requests given together can be answered at the same time."
)
WAIT = 50.0  # seconds a tool waits for the work's next requests: under the 60 s that a client such as Codex gives a tool
KEEP = 600.0  # seconds a job's result waits after its end for a client to come back for it
READ_ONLY = ToolAnnotations(read_only_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False)  # files and merges notes, never deletes
OFF = "the memory is off: press Start under Memory in the wiki's page, or set OKF_MCP=1 where it runs"
NO_LIFESPAN = "the memory needs the server's lifespan: run the wiki's app itself, not mounted inside another"


class Memory:
    """The MCP server over the wiki that `wiki()` returns at each call, so it follows the server's settings."""

    def __init__(self, wiki: Callable[[], Wiki]) -> None:
        self.wiki = wiki
        self.app: Starlette | None = None  # the SDK's app, made by each lifespan: its session manager runs only once
        self._jobs: dict[str, Relay] = {}  # the work going on with the client's models, by job id
        self._jobs_lock = threading.Lock()
        # WARNING: the SDK sets the level of the whole process's logging, and INFO would log every request.
        self.server: MCPServer = MCPServer("llm-wiki", instructions=INSTRUCTIONS, version=__version__, log_level="WARNING")
        server = self.server

        # Plain functions: the SDK runs them in a thread, as the wiki's calls block.
        @server.tool(annotations=READ_ONLY)
        def recall(question: Annotated[str, Field(description="What to look up, as a question")]) -> dict[str, Any]:
            """Answer a question from the long-term memory, with the notes the answer cites."""
            return self.run(lambda wiki: answered(wiki.ask(question)))

        @server.tool(annotations=WRITES)
        def remember(
            text: Annotated[str, Field(description="What to keep, self-contained; Markdown is fine")],
            title: Annotated[str | None, Field(description="A short title; empty: the memory names it")] = None,
        ) -> dict[str, Any]:
            """Keep something in the long-term memory.

            It is filed in the right folder, or merged into the note on the same subject, and linked to related notes.
            """
            return self.run(lambda wiki: filed(wiki.ingest(text, title=title or None)))

        @server.tool(annotations=WRITES)
        def reply(
            job: Annotated[str, Field(description="The job that recall or remember returned")],
            replies: Annotated[dict[str, str], Field(description='Your reply to each request, by its id: {"r1": "…"}')],
        ) -> dict[str, Any]:
            """Your replies to the memory's requests, when it works with your models: returns its next requests, or the result."""
            with self._jobs_lock:
                relay = self._jobs.get(job)
            if relay is None:
                raise ToolError(f"no job {job!r}: it was never started, or it ended over {KEEP / 60:.0f} minutes ago")
            return self.progress(job, relay, relay.answer(replies))

        @server.tool(annotations=READ_ONLY)
        def read_note(path: Annotated[str, Field(description="The note's path as recall cites it, e.g. finance/pricing.md")]) -> str:
            """The full text of one note of the long-term memory."""
            try:
                return guarded(lambda: self.live().store.read(path)).full_text()
            except FileNotFoundError as e:
                raise ToolError(f"no note at {path}") from e

    def run(self, work: Callable[[Wiki], dict[str, Any]]) -> dict[str, Any]:
        """The work's result with this wiki's own models; with the client's, its first requests (see models.relay)."""
        wiki = self.live()
        if wiki.cfg.mcp_models != "client":
            return guarded(lambda: work(wiki))
        relay, tracker = Relay(), UsageTracker()  # the client's plan pays: nothing to count here
        client_wiki = Wiki(relayed(wiki.cfg), llm=RelayLLM(relay, tracker), classifier=RelayClassifier(relay, tracker))
        job = uuid.uuid4().hex[:8]
        with self._jobs_lock:
            now = time.monotonic()  # the jobs that ended long ago are forgotten, their result with them
            self._jobs = {j: r for j, r in self._jobs.items() if r.ended is None or now - r.ended < KEEP}
            self._jobs[job] = relay
        relay.run(lambda: work(client_wiki))
        return self.progress(job, relay)

    def live(self) -> Wiki:
        """The wiki, while the settings turn the memory on. Off, the tool answers how to start it (a work already started goes on)."""
        wiki = self.wiki()
        if not wiki.cfg.mcp:
            raise ToolError(OFF)
        return wiki

    def progress(self, job: str, relay: Relay, unknown: Iterable[str] = ()) -> dict[str, Any]:
        """Where a job is: the requests it needs replies to, or that it is still at work, or its result once done."""
        fresh = relay.next(WAIT)
        if relay.done:  # the job stays for KEEP seconds (see `run`): a client that lost this answer asks again
            if relay.error is not None:
                raise ToolError(str(relay.error)) from relay.error
            return {"status": "done", **relay.result}
        # The new requests, else those handed out before and still unanswered: again, in case the client lost them.
        due = fresh or relay.awaited()
        out: dict[str, Any] = {"job": job, "status": "needs replies" if due else "waiting", "how": HOW}
        if due:
            out["requests"] = [asdict(r) for r in due]
        if unknown := list(unknown):
            out["unknown"] = unknown  # ids no request waits for: answered already, or never given
        return out

    @asynccontextmanager
    async def lifespan(self, _app: object) -> AsyncGenerator[None, None]:
        """The host app's lifespan: a fresh SDK app and session manager each time, as one runs only once."""
        self.app = self.server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        try:
            async with self.server.session_manager.run():
                yield
        finally:
            self.app = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI: the MCP endpoint, on or off (see `live`), once the server's lifespan has made it."""
        if self.app is None:
            await JSONResponse({"detail": NO_LIFESPAN}, status_code=503)(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def answered(answer: Answer) -> dict[str, Any]:
    return {"answer": answer.text, "notes": answer.citations}


def filed(result: IngestResult) -> dict[str, Any]:
    return {"action": result.action, "note": result.note, "folder": result.folder, "related": result.related}


def guarded(call: Callable[[], T]) -> T:
    """The call's result. A wiki error becomes a ToolError, so the client's model reads what went wrong and can act on it."""
    try:
        return call()
    except WikiError as e:
        raise ToolError(str(e)) from e
