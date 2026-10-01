# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""HTTP API (extra `server`) and the local web UI at GET /.

POST /ingest, POST /upload (a .txt/.md/.pdf file), POST /ask, POST /folder (one made by hand), GET /tree, GET /note,
GET /graph, GET /usage, GET /check, GET /health; DELETE /note, DELETE /folder and
DELETE /wiki (empty it); GET /settings, POST /settings,
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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from llmw2 import __version__
from llmw2.bundle.files import SUFFIXES
from llmw2.bundle.origin import Origin
from llmw2.bundle.tree import graph
from llmw2.config import WikiConfig
from llmw2.errors import ConfigError, InputError, ModelError
from llmw2.models.classifier import probe as probe_classifier
from llmw2.models.llm import live_models, probe
from llmw2.settings import Settings
from llmw2.wiki import Wiki

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class OriginModel(BaseModel):
    """Where the text comes from at its source (see `llmw2.Origin`)."""

    source: str
    account: str = ""
    id: str
    link: str | None = None
    version: str | None = None
    modified: str | None = None


class IngestRequest(BaseModel):
    text: str
    title: str | None = None
    resource: str | None = None
    origin: OriginModel | None = None


class AskRequest(BaseModel):
    question: str


class FolderRequest(BaseModel):
    parent: str = "/"
    name: str
    description: str


class SettingsChanges(BaseModel):
    """The fields of `settings.FIELDS` the page may send; unset ones are left alone, "" resets one (keeps a secret)."""

    model_config = ConfigDict(extra="ignore")  # an older page sending a field we dropped still saves the rest

    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    mode: str | None = None
    reasoning: str | None = None  # "true" turns it on, "" back to the default (off)
    classifier_provider: str | None = None
    classifier_base_url: str | None = None
    classifier_model: str | None = None
    classifier_api_key: str | None = None
    bundle: str | None = None
    # CROW thresholds (settings.FIELDS "crow_*"): a number as text, "" for the classifier's preset value
    crow_tau_route: str | None = None
    crow_tau_path: str | None = None
    crow_tau_ing: str | None = None
    crow_tau_cons: str | None = None
    crow_tau_fold: str | None = None
    crow_tau_ret: str | None = None
    crow_tau_link: str | None = None
    crow_beam: str | None = None
    crow_retrieval_beam: str | None = None
    crow_k: str | None = None
    # How each model is called (a number as text, "" for the default)
    temperature: str | None = None
    timeout: str | None = None
    classifier_timeout: str | None = None
    classifier_attempts: str | None = None
    classifier_state_chars: str | None = None
    classifier_request_chars: str | None = None


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
    # No /docs, /redoc or /openapi.json: a local service, and the schema would map it for any page that can reach it.
    app = FastAPI(title="llmwiki2", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Any:
        if local_only and hostname(request) not in LOOPBACK:
            return JSONResponse({"detail": "this wiki answers only at http://localhost"}, status_code=403)
        if request.method not in SAFE_METHODS and not same_origin(request):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        size = request.headers.get("content-length", "")
        if size.isdigit() and int(size) > max_bytes():
            return JSONResponse({"detail": f"too large: the limit is {current().cfg.upload_mb} MB"}, status_code=413)
        return await call_next(request)

    # The two failures every model-backed route shares: bad input is the caller's (422), a failing model is upstream (502).
    @app.exception_handler(InputError)
    async def input_error(request: Request, e: InputError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(ModelError)
    async def model_error(request: Request, e: ModelError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=502)

    # A missing or refused key: the wiki is not set up for the request, and the message says where to fix it.
    @app.exception_handler(ConfigError)
    async def config_error(request: Request, e: ConfigError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=503)

    # FastAPI's own 422 lists pydantic errors; the page shows `detail` as text, so say it in one line.
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, e: RequestValidationError) -> JSONResponse:
        def where(loc: tuple[int | str, ...]) -> str:  # ("body", "question") -> question; ("body", 12) -> body
            return ".".join(p for p in loc[1:] if isinstance(p, str)) or str(loc[0])

        problems = "; ".join(f"{where(err['loc'])}: {err['msg']}" for err in e.errors())
        return JSONResponse({"detail": problems}, status_code=422)

    app.state.wiki = wiki
    write_lock = threading.Lock()  # one writer at a time: ingests change files and indexes
    web = resources.files("llmw2") / "web"
    app.mount("/web", StaticFiles(directory=str(web)), name="web")

    def current() -> Wiki:
        wiki: Wiki = app.state.wiki
        return wiki

    def max_bytes() -> int:
        return current().cfg.upload_mb * 1_000_000

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
            origin = Origin(**req.origin.model_dump()) if req.origin else None
            return current().ingest(req.text, title=req.title, resource=req.resource, origin=origin).to_dict()

    @app.post("/upload")
    async def upload(request: Request, filename: str) -> dict[str, Any]:
        """Catalogue one file sent as the raw request body (?filename=notes.pdf)."""
        if PurePath(filename).suffix.lower() not in SUFFIXES:
            raise HTTPException(415, f"{filename}: use .txt, .md or .pdf")
        data = bytearray()
        async for chunk in request.stream():  # a body sent without Content-Length is capped while it arrives
            data += chunk
            if len(data) > max_bytes():
                raise HTTPException(413, f"too large: the limit is {current().cfg.upload_mb} MB")

        def run() -> dict[str, Any]:
            with write_lock:
                return current().ingest_file(bytes(data), filename).to_dict()

        return await run_in_threadpool(run)

    @app.post("/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        return current().ask(req.question).to_dict()

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

    @app.delete("/note")
    def delete_note(path: str) -> dict[str, Any]:
        with write_lock:
            try:
                return current().delete_note(path).to_dict()
            except FileNotFoundError as e:
                raise HTTPException(404, f"no note at {path}") from e

    @app.post("/folder")
    def create_folder(req: FolderRequest) -> dict[str, Any]:
        with write_lock:
            try:
                return current().create_folder(req.parent, req.name, req.description).to_dict()
            except FileNotFoundError as e:
                raise HTTPException(404, f"no folder at {req.parent}") from e

    @app.delete("/folder")
    def delete_folder(path: str) -> dict[str, Any]:
        with write_lock:
            try:
                return current().delete_folder(path).to_dict()
            except FileNotFoundError as e:
                raise HTTPException(404, f"no folder at {path}") from e

    @app.delete("/wiki")
    def clear() -> dict[str, Any]:
        with write_lock:
            return current().clear().to_dict()

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
    def save_settings(request: Request, changes: SettingsChanges) -> dict[str, Any]:
        store = editable(request)
        with write_lock:
            try:
                cfg = store.save(changes.model_dump(exclude_unset=True))
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            # solo: the old Wiki is not closed, a question may still be using it; one pool per save, reclaimed by the GC.
            app.state.wiki = make_wiki(cfg)
            app.state.wiki.init()
        return {**store.view(), "editable": True}

    @app.post("/settings/test")
    def test_settings(request: Request, changes: SettingsChanges) -> dict[str, Any]:
        """Ask the model for one word, and in CROW mode the classifier for one answer, with `changes` applied, without saving them."""
        try:
            _, cfg = editable(request).apply(changes.model_dump(exclude_unset=True))
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        with make_wiki(cfg) as trial:
            try:
                result: dict[str, Any] = {"ok": True, "model": cfg.llm.model, "reply": probe(trial.llm)}
            except ModelError as e:
                result = {"ok": False, "model": cfg.llm.model, "error": str(e)}
            if trial.classifier is not None:
                try:
                    probe_classifier(trial.classifier)
                    result["classifier"] = {"ok": True, "model": cfg.classifier.model}
                except (ModelError, ConfigError) as e:
                    result["classifier"] = {"ok": False, "model": cfg.classifier.model, "error": str(e)}
        return result

    @app.post("/settings/models")
    def list_models(request: Request, changes: SettingsChanges) -> dict[str, Any]:
        """The models the provider offers right now, with `changes` applied (the key may be the saved one)."""
        try:
            _, cfg = editable(request).apply(changes.model_dump(exclude_unset=True))
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
    import time
    import webbrowser

    import uvicorn

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
