"""FastAPI service exposing the lineage graph to the UI."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .builder import build_graph
from .graph import Graph

DATA_DIR = Path(os.environ.get("LINEAGE_DATA_DIR", Path(__file__).resolve().parents[2] / "sample_data"))
UI_DIST = Path(os.environ.get("LINEAGE_UI_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))

app = FastAPI(title="Teradata Lineage")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"],
                   allow_headers=["*"])
_state: dict[str, Graph] = {}


def graph() -> Graph:
    if "g" not in _state:
        _state["g"] = build_graph(DATA_DIR)
    return _state["g"]


@app.get("/api/stats")
def stats() -> dict:
    return graph().stats()


@app.post("/api/rescan")
def rescan() -> dict:
    _state["g"] = build_graph(DATA_DIR)
    return graph().stats()


@app.get("/api/search")
def search(q: str = Query(min_length=1), type: str | None = None, limit: int = 25) -> list[dict]:
    types = set(type.split(",")) if type else None
    return graph().search(q, types, min(limit, 100))


@app.get("/api/graph")
def get_graph(node: str | None = None, direction: str = Query("both", pattern="^(up|down|both)$"),
              depth: int = Query(3, ge=1, le=15), mode: str = Query("table", pattern="^(table|job)$")) -> dict:
    g = graph()
    if node is None:
        return g.overview(mode)
    if node not in g.nodes:
        raise HTTPException(404, f"Unknown node {node}")
    return g.lineage(node, direction, depth, mode)


@app.get("/api/node")
def node_detail(id: str) -> dict:
    d = graph().detail(id)
    if d is None:
        raise HTTPException(404, f"Unknown node {id}")
    return d


@app.get("/api/source")
def source_snippet(file: str, line: int = 1, context: int = 8) -> dict:
    """Return lines around `line` of a file under the data directory (for evidence)."""
    root = DATA_DIR.resolve()
    path = (root / file).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "File not found")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, line - context)
    end = min(len(lines), line + context)
    return {"file": file, "line": line, "start": start,
            "lines": [{"n": n, "text": lines[n - 1]} for n in range(start, end + 1)]}


if UI_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):  # noqa: ANN201
        f = UI_DIST / path
        return FileResponse(f if path and f.is_file() else UI_DIST / "index.html")
