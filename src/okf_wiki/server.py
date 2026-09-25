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

"""HTTP API (extra `server`) and the local web UI at GET /.

POST /ingest, POST /upload (a .txt/.md/.pdf file), POST /ask, GET /tree, GET /note,
GET /graph, GET /usage, GET /check, GET /health.
"""

from __future__ import annotations

import threading
from importlib import resources
from pathlib import PurePath
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from okf_wiki import __version__
from okf_wiki.client import ModelError
from okf_wiki.files import SUFFIXES
from okf_wiki.store import graph
from okf_wiki.wiki import Wiki


class IngestRequest(BaseModel):
    text: str
    title: str | None = None
    resource: str | None = None


class AskRequest(BaseModel):
    question: str


def create_app(wiki: Wiki) -> FastAPI:
    app = FastAPI(title="okf-wiki", version=__version__)
    write_lock = threading.Lock()  # one writer at a time: ingests change files and indexes

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return (resources.files("okf_wiki") / "web" / "index.html").read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict[str, Any]:
        classifier = wiki.classifier.model if wiki.classifier else None
        return {"status": "ok", "mode": wiki.cfg.mode, "llm": wiki.llm.model, "classifier": classifier}

    @app.post("/ingest")
    def ingest(req: IngestRequest) -> dict[str, Any]:
        with write_lock:
            try:
                return wiki.ingest(req.text, title=req.title, resource=req.resource).to_dict()
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
                return wiki.ingest_file(data, filename).to_dict()

        try:
            return await run_in_threadpool(run)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except ModelError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        try:
            return wiki.ask(req.question).to_dict()
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except ModelError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/tree")
    def tree() -> dict[str, Any]:
        return wiki.tree().to_dict()

    @app.get("/graph")
    def brain() -> dict[str, Any]:
        return graph(wiki.tree())

    @app.get("/note")
    def note(path: str) -> dict[str, Any]:
        try:
            n = wiki.store.read(path)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(404, f"no note at {path}") from e
        return {"path": n.rel, "title": n.title, "summary": n.summary, "tags": n.tags, "frontmatter": n.frontmatter, "body": n.body}

    @app.get("/usage")
    def usage() -> dict[str, Any]:
        return wiki.usage.to_dict()

    @app.get("/check")
    def check() -> list[str]:
        return [str(p) for p in wiki.check()]

    return app


def serve(wiki: Wiki, *, host: str = "127.0.0.1", port: int = 8000) -> None:
    import logging

    import uvicorn

    # One line per decision and per model call, next to uvicorn's request log.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(message)s")
    logging.getLogger("okf_wiki").setLevel(logging.INFO)
    wiki.init()
    uvicorn.run(create_app(wiki), host=host, port=port)
