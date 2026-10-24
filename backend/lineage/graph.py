"""In-memory lineage graph with two views.

* ``table`` view: tables/views/datasets connected by FLOW edges (source -> target),
  each annotated with the jobs that perform the flow.
* ``job`` view: dataset/table -> job -> table edges (READS/WRITES) plus scheduler
  RUNS_AFTER edges between jobs.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

TABLE_TYPES = {"table", "view", "dataset"}


@dataclass
class Node:
    id: str
    type: str  # table | view | dataset | job
    name: str
    attrs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "name": self.name, "attrs": self.attrs}


@dataclass
class Edge:
    kind: str  # FLOW | READS | WRITES | RUNS_AFTER
    source: str
    target: str
    evidence: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.kind}|{self.source}|{self.target}"

    def to_dict(self) -> dict:
        jobs = sorted({e["job"] for e in self.evidence if e.get("job")})
        return {"id": self.id, "kind": self.kind, "source": self.source,
                "target": self.target, "jobs": jobs, "evidence": self.evidence}


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self.warnings: list[str] = []
        self.sources: dict[str, int] = {}

    # -- construction -------------------------------------------------------
    def add_node(self, id: str, type: str, name: str, **attrs) -> Node:
        n = self.nodes.get(id)
        if n is None:
            n = self.nodes[id] = Node(id, type, name, {})
        # a view/table classification from the data dictionary beats a guess
        if type != n.type and n.type == "table" and type == "view":
            n.type = type
        n.attrs.update({k: v for k, v in attrs.items() if v is not None})
        return n

    def add_edge(self, kind: str, source: str, target: str, evidence: dict) -> None:
        e = Edge(kind, source, target)
        e = self.edges.setdefault(e.id, e)
        if evidence not in e.evidence:
            e.evidence.append(evidence)

    # -- views --------------------------------------------------------------
    def view_edges(self, mode: str) -> list[Edge]:
        kinds = {"FLOW"} if mode == "table" else {"READS", "WRITES", "RUNS_AFTER"}
        return [e for e in self.edges.values() if e.kind in kinds]

    def lineage(self, root: str, direction: str = "both", depth: int = 3,
                mode: str = "table") -> dict:
        edges = self.view_edges(mode)
        down: dict[str, list[Edge]] = {}
        up: dict[str, list[Edge]] = {}
        for e in edges:
            down.setdefault(e.source, []).append(e)
            up.setdefault(e.target, []).append(e)

        keep_nodes: dict[str, int] = {root: 0}
        keep_edges: dict[str, Edge] = {}

        def bfs(adj: dict[str, list[Edge]], forward: bool) -> None:
            seen = {root}
            q = deque([(root, 0)])
            while q:
                nid, d = q.popleft()
                if d >= depth:
                    continue
                for e in adj.get(nid, []):
                    other = e.target if forward else e.source
                    keep_edges[e.id] = e
                    keep_nodes.setdefault(other, d + 1)
                    if other not in seen:
                        seen.add(other)
                        q.append((other, d + 1))

        if direction in ("up", "both"):
            bfs(up, False)
        if direction in ("down", "both"):
            bfs(down, True)

        nodes = []
        for nid, dist in keep_nodes.items():
            n = self.nodes[nid].to_dict()
            n["distance"] = dist
            n["hasMoreUp"] = any(e.id not in keep_edges for e in up.get(nid, []))
            n["hasMoreDown"] = any(e.id not in keep_edges for e in down.get(nid, []))
            nodes.append(n)
        return {"root": root, "mode": mode, "nodes": nodes,
                "edges": [e.to_dict() for e in keep_edges.values()]}

    def overview(self, mode: str, limit: int = 800) -> dict:
        edges = self.view_edges(mode)
        ids = {e.source for e in edges} | {e.target for e in edges}
        truncated = len(ids) > limit
        if truncated:
            deg: dict[str, int] = {}
            for e in edges:
                deg[e.source] = deg.get(e.source, 0) + 1
                deg[e.target] = deg.get(e.target, 0) + 1
            ids = set(sorted(ids, key=lambda i: -deg[i])[:limit])
            edges = [e for e in edges if e.source in ids and e.target in ids]
        return {"root": None, "mode": mode, "truncated": truncated,
                "nodes": [self.nodes[i].to_dict() | {"distance": 0, "hasMoreUp": False, "hasMoreDown": False}
                          for i in sorted(ids)],
                "edges": [e.to_dict() for e in edges]}

    def search(self, q: str, types: set[str] | None = None, limit: int = 25) -> list[dict]:
        q = q.strip().upper()
        if not q:
            return []
        scored = []
        for n in self.nodes.values():
            if types and n.type not in types:
                continue
            hay = n.name.upper()
            if q not in hay and q not in n.id.upper():
                continue
            tail = hay.rsplit(".", 1)[-1]
            rank = 0 if hay == q or tail == q else 1 if hay.startswith(q) or tail.startswith(q) else 2
            scored.append((rank, len(hay), n))
        scored.sort(key=lambda t: t[:2])
        return [n.to_dict() for _, _, n in scored[:limit]]

    def detail(self, nid: str) -> dict | None:
        n = self.nodes.get(nid)
        if not n:
            return None
        flows_in = [e for e in self.edges.values() if e.kind == "FLOW" and e.target == nid]
        flows_out = [e for e in self.edges.values() if e.kind == "FLOW" and e.source == nid]
        reads = [e for e in self.edges.values() if e.kind == "READS" and e.target == nid] if n.type == "job" else []
        writes = [e for e in self.edges.values() if e.kind == "WRITES" and e.source == nid] if n.type == "job" else []
        after = [e for e in self.edges.values() if e.kind == "RUNS_AFTER" and e.target == nid]
        before = [e for e in self.edges.values() if e.kind == "RUNS_AFTER" and e.source == nid]
        writers = sorted({ev["job"] for e in self.edges.values()
                          if e.kind == "WRITES" and e.target == nid for ev in e.evidence})
        readers = sorted({ev["job"] for e in self.edges.values()
                          if e.kind == "READS" and e.source == nid for ev in e.evidence})
        return {
            **n.to_dict(),
            "loadedBy": writers,
            "readBy": readers,
            "upstream": [e.to_dict() for e in flows_in],
            "downstream": [e.to_dict() for e in flows_out],
            "jobInputs": [e.to_dict() for e in reads],
            "jobOutputs": [e.to_dict() for e in writes],
            "predecessors": [e.source for e in after],
            "successors": [e.target for e in before],
        }

    def stats(self) -> dict:
        by_type: dict[str, int] = {}
        for n in self.nodes.values():
            by_type[n.type] = by_type.get(n.type, 0) + 1
        by_kind: dict[str, int] = {}
        for e in self.edges.values():
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        return {"nodes": by_type, "edges": by_kind, "sources": self.sources,
                "warnings": self.warnings}
