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

"""HTTP API (extra `server`) and the local web UI at GET /.

POST /ingest, POST /upload (a .txt/.md/.pdf file), POST /ask, GET /tree, GET /note,
GET /graph, GET /usage, GET /check, GET /health; GET /settings, POST /settings,
POST /settings/test and POST /settings/models when the app is given the settings file.

Two guards for a wiki on your machine, where any web page you visit can send requests:
- every request that changes something must be same-origin when a browser sends it
  (another site's page cannot file, ask or upload through your browser);
- a server bound to localhost (the default) answers only requests naming a loopback
  Host, so a hostile domain re-pointed at 127.0.0.1 (DNS rebinding) cannot read it.
Settings can be changed only from this machine: from a loopback address, to a loopback Host.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from importlib import resources
from pathlib import PurePath
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from okf_wiki import __version__
from okf_wiki.client import ModelError
from okf_wiki.config import WikiConfig
from okf_wiki.files import SUFFIXES
from okf_wiki.settings import Settings, live_models, probe
from okf_wiki.store import graph
from okf_wiki.wiki import Wiki

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class IngestRequest(BaseModel):
    text: str
    title: str | None = None
    resource: str | None = None


class AskRequest(BaseModel):
    question: str


def hostname(request: Request) -> str:
    """The name the request was sent to, without the port."""
    return urlsplit(f"//{request.headers.get('host', '')}").hostname or ""


def same_origin(request: Request) -> bool:
    """True unless a browser says the request comes from another site (curl and scripts send no Origin)."""
    origin = request.headers.get("origin")
    return origin is None or urlsplit(origin).netloc == request.headers.get("host", "")


def is_local(request: Request) -> bool:
    """Sent from this machine, to a loopback name, and same-origin."""
    return (
        request.client is not None
        and request.client.host in LOOPBACK
        and hostname(request) in LOOPBACK
        and same_origin(request)
    )


def create_app(
    wiki: Wiki,
    *,
    settings: Settings | None = None,
    make_wiki: Callable[[WikiConfig], Wiki] = Wiki,
    local_only: bool = False,
) -> FastAPI:
    """The API over `wiki`; with `settings`, the page can also change them (the wiki is rebuilt from `make_wiki`).

    `local_only` (a server bound to localhost) refuses requests naming any other Host.
    """
    app = FastAPI(title="okf-wiki", version=__version__)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Any:
        if local_only and hostname(request) not in LOOPBACK:
            return JSONResponse({"detail": "this wiki answers only at http://localhost"}, status_code=403)
        if request.method not in SAFE_METHODS and not same_origin(request):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        return await call_next(request)

    app.state.wiki = wiki
    write_lock = threading.Lock()  # one writer at a time: ingests change files and indexes
    web = resources.files("okf_wiki") / "web"
    app.mount("/web", StaticFiles(directory=str(web)), name="web")

    def current() -> Wiki:
        return app.state.wiki

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (web / "index.html").read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict[str, Any]:
        wiki = current()
        classifier = wiki.classifier.model if wiki.classifier else None
        return {"status": "ok", "mode": wiki.cfg.mode, "llm": wiki.llm.model, "classifier": classifier}

    @app.post("/ingest")
    def ingest(req: IngestRequest) -> dict[str, Any]:
        with write_lock:
            try:
                return current().ingest(req.text, title=req.title, resource=req.resource).to_dict()
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            except ModelError as e:
                raise HTTPException(502, str(e)) from e

    @app.post("/upload")
    async def upload(request: Request, filename: str) -> dict[str, Any]:
        """Catalogue one file sent as the raw request body (?filename=notes.pdf)."""
        if PurePath(filename).suffix.lower() not in SUFFIXES:
            raise HTTPException(415, f"{filename}: use .txt, .md or .pdf")
        data = await request.body()

        def run() -> dict[str, Any]:
            with write_lock:
                return current().ingest_file(data, filename).to_dict()

        try:
            return await run_in_threadpool(run)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except ModelError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        try:
            return current().ask(req.question).to_dict()
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except ModelError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/tree")
    def tree() -> dict[str, Any]:
        return current().tree().to_dict()

    @app.get("/graph")
    def brain() -> dict[str, Any]:
        return graph(current().tree())

    @app.get("/note")
    def note(path: str) -> dict[str, Any]:
        try:
            n = current().store.read(path)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(404, f"no note at {path}") from e
        return {"path": n.rel, "title": n.title, "summary": n.summary, "tags": n.tags, "frontmatter": n.frontmatter, "body": n.body}

    @app.get("/usage")
    def usage() -> dict[str, Any]:
        return current().usage.to_dict()

    @app.get("/check")
    def check() -> list[str]:
        return [str(p) for p in current().check()]

    def editable(request: Request) -> Settings:
        if settings is None:
            raise HTTPException(404, "settings are managed by whoever built this app")
        if not is_local(request):
            raise HTTPException(403, "settings can be changed only from this machine (http://localhost)")
        return settings

    @app.get("/settings")
    def read_settings(request: Request) -> dict[str, Any]:
        if settings is None:
            raise HTTPException(404, "settings are managed by whoever built this app")
        try:
            return {**settings.view(), "editable": is_local(request)}
        except ValueError as e:
            raise HTTPException(500, str(e)) from e

    @app.post("/settings")
    def save_settings(request: Request, changes: dict[str, Any]) -> dict[str, Any]:
        store = editable(request)
        with write_lock:
            try:
                cfg = store.save(changes)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            app.state.wiki = make_wiki(cfg)
            app.state.wiki.init()
        return {**store.view(), "editable": True}

    @app.post("/settings/test")
    def test_settings(request: Request, changes: dict[str, Any]) -> dict[str, Any]:
        """Ask the model for one word with `changes` applied, without saving them."""
        try:
            _, cfg = editable(request).apply(changes)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        try:
            return {"ok": True, "model": cfg.llm.model, "reply": probe(make_wiki(cfg).llm)}
        except ModelError as e:
            return {"ok": False, "model": cfg.llm.model, "error": str(e)}

    @app.post("/settings/models")
    def list_models(request: Request, changes: dict[str, Any]) -> dict[str, Any]:
        """The models the provider offers right now, with `changes` applied (the key may be the saved one)."""
        try:
            _, cfg = editable(request).apply(changes)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"models": live_models(cfg.llm)}

    return app


def serve(
    wiki: Wiki,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    settings: Settings | None = None,
    make_wiki: Callable[[WikiConfig], Wiki] = Wiki,
    open_browser: bool = False,
) -> None:
    import logging
    import time
    import webbrowser

    import uvicorn

    # One line per decision and per model call, next to uvicorn's request log.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(message)s")
    logging.getLogger("okf_wiki").setLevel(logging.INFO)
    wiki.init()
    app = create_app(wiki, settings=settings, make_wiki=make_wiki, local_only=host in LOOPBACK)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))
    if open_browser:

        def open_when_up() -> None:
            while not server.started:
                time.sleep(0.1)
            webbrowser.open(f"http://localhost:{port}/")

        threading.Thread(target=open_when_up, daemon=True).start()
    server.run()
