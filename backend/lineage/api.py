"""FastAPI service exposing one lineage graph per platform to the UI."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .graph import Graph
from .platforms import PLATFORMS

DATA_DIR = Path(os.environ.get("LINEAGE_DATA_DIR", Path(__file__).resolve().parents[2] / "sample_data"))
UI_DIST = Path(os.environ.get("LINEAGE_UI_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))

app = FastAPI(title="Lineage")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"],
                   allow_headers=["*"])
_graphs: dict[str, Graph] = {}

PlatformParam = Query(pattern="^(" + "|".join(PLATFORMS) + ")$")


def platform_dir(platform: str) -> Path:
    return DATA_DIR / platform


def graph(platform: str) -> Graph:
    if platform not in PLATFORMS:
        raise HTTPException(404, f"Unknown platform {platform}")
    if platform not in _graphs:
        _graphs[platform] = PLATFORMS[platform].build(platform_dir(platform))
    return _graphs[platform]


@app.get("/api/platforms")
def platforms() -> list[dict]:
    return [{"id": p.id, "label": p.label, "target": p.target, "loader": p.loader,
             "configured": platform_dir(p.id).is_dir()} for p in PLATFORMS.values()]


@app.get("/api/stats")
def stats(platform: str = PlatformParam) -> dict:
    return graph(platform).stats()


@app.post("/api/rescan")
def rescan(platform: str = PlatformParam) -> dict:
    _graphs.pop(platform, None)
    return graph(platform).stats()


@app.get("/api/search")
def search(platform: str = PlatformParam, q: str = "", type: str | None = None, limit: int = 25) -> list[dict]:
    types = set(type.split(",")) if type else None
    return graph(platform).search(q, types, min(limit, 100))


@app.get("/api/graph")
def get_graph(platform: str = PlatformParam, node: str | None = None,
              direction: str = Query("both", pattern="^(up|down|both)$"),
              depth: int = Query(3, ge=1, le=15), mode: str = Query("table", pattern="^(table|job)$")) -> dict:
    g = graph(platform)
    if node is None:
        return g.overview(mode)
    if node not in g.nodes:
        raise HTTPException(404, f"Unknown node {node}")
    return g.lineage(node, direction, depth, mode)


@app.get("/api/node")
def node_detail(id: str, platform: str = PlatformParam) -> dict:
    g = graph(platform)
    d = g.detail(id)
    if d is None:
        raise HTTPException(404, f"Unknown node {id}")
    return d | {"impact": g.impact(id)}


@app.get("/api/source")
def source_snippet(file: str, platform: str = PlatformParam, line: int = 1, context: int = 8) -> dict:
    """Return lines around `line` of a file under the platform's data folder (for evidence)."""
    root = platform_dir(platform).resolve()
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
