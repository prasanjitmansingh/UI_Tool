"""Lineage for Hadoop (Hive / HDFS) tables loaded by Ab Initio graphs.

Layout of the platform folder (every folder optional)::

    graphs/     *.json  one exported graph each (components + flows, see README)
    scheduler/  *.csv   job,predecessor,schedule,description  (Control-M / Autosys)
    metastore/  *.csv   Hive metastore export (db_name,tbl_name,tbl_type,location,...)
    config.json {"default_database": "default", "substitutions": {"$ENV": "prod"}}

A graph's lineage comes from walking each sink component (Output File / Output
Table / Write ...) back through its flows to every source component (Input File /
Input Table / Read ... and lookups). HDFS paths that are the location of a Hive
table are reported as that table, so file-based loads still connect.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .graph import Graph
from .sources import read_hive_metastore, read_scheduler
from .sqlscan import Context, extract_sql

SOURCE_PREFIXES = ("input", "read")
SINK_PREFIXES = ("output", "write")
_PARAM = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")


def norm_path(p: str) -> str:
    """hdfs://nn:8020/data/x/dt=2024-01-01/* -> /data/x"""
    p = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*", "", p.strip())  # scheme + authority
    p = re.sub(r"(/[^/]*=[^/]*|/\*[^/]*)+/?$", "", p)  # partition dirs and globs
    return p.rstrip("/") or "/"


def _files(folder: Path, suffix: str):
    if folder.is_dir():
        yield from sorted(p for p in folder.rglob(f"*{suffix}") if p.is_file())


def _component_lines(text: str) -> dict[str, int]:
    """Line of each component's "id" in the export, for evidence links."""
    lines: dict[str, int] = {}
    for m in re.finditer(r'"id"\s*:\s*"([^"]+)"', text):
        lines.setdefault(m.group(1), text.count("\n", 0, m.start()) + 1)
    return lines


def build_abinitio_graph(data_dir: str | Path) -> Graph:
    root = Path(data_dir)
    g = Graph()
    cfg = {}
    if (root / "config.json").exists():
        cfg = json.loads((root / "config.json").read_text())
    default_db = cfg.get("default_database", "default")
    subs: dict[str, str] = cfg.get("substitutions", {})
    runtime = set(cfg.get("runtime_parameters", []))  # supplied at run time, e.g. RUN_DATE

    def rel(p: Path) -> str:
        return str(p.relative_to(root))

    # -- metastore first: it maps HDFS locations to tables -------------------
    meta: dict[str, dict] = {}
    meta_file: dict[str, str] = {}
    for p in _files(root / "metastore", ".csv"):
        part = read_hive_metastore(p)
        meta.update(part)
        meta_file.update({k: rel(p) for k in part})
    by_location = {norm_path(v["location"]): k for k, v in meta.items() if v["location"]}
    if meta:
        g.sources["metastore"] = len(meta)

    def table_name(raw: str) -> str:
        n = raw.replace("`", "").strip().lower()
        return n if "." in n else f"{default_db}.{n}"

    def table_node(name: str) -> str:
        nid = f"tbl:{name}"
        g.add_node(nid, "table", name)
        return nid

    def path_node(path: str) -> str:
        p = norm_path(path)
        if p in by_location:
            return table_node(by_location[p])
        nid = f"ds:{p}"
        g.add_node(nid, "dataset", p)
        return nid

    # -- graphs ---------------------------------------------------------------
    graph_files = list(_files(root / "graphs", ".json"))
    g.sources["graphs"] = len(graph_files)
    for p in graph_files:
        text = p.read_text(encoding="utf-8")
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as e:
            g.warnings.append(f"{rel(p)}: not valid JSON ({e.msg} at line {e.lineno})")
            continue
        gname = spec.get("graph") or p.stem
        params = {**{k.lstrip("$"): v for k, v in subs.items()},
                  **{k.lstrip("$"): str(v) for k, v in spec.get("parameters", {}).items()}}
        lines = _component_lines(text)
        comps = {c["id"]: c for c in spec.get("components", []) if "id" in c}
        jid = f"job:{gname}"
        g.add_node(jid, "job", gname, file=rel(p), line=1, description=spec.get("description"),
                   project=spec.get("project"),
                   steps=[{"name": c.get("name", cid), "pgm": c.get("type", "")} for cid, c in comps.items()])

        def resolve(val: str) -> str:
            def sub(m: re.Match) -> str:
                if m.group(1) in params:
                    return params[m.group(1)]
                if m.group(1) in runtime:
                    return m.group(0)
                g.warnings.append(f"{gname}: parameter ${m.group(1)} is not defined")
                return m.group(0)
            return _PARAM.sub(sub, val)

        def sql_of(c: dict) -> str:
            return resolve(c["sql"]).replace("`", "")  # Hive quotes identifiers with backticks

        def ev(cid: str) -> dict:
            c = comps.get(cid, {})
            return {"job": gname, "step": c.get("name", cid), "file": rel(p),
                    "line": lines.get(cid, 1), "origin": "abinitio", "confidence": "parsed",
                    "scriptKind": c.get("type", "Ab Initio")}

        def endpoints(c: dict, role: str) -> list[str]:
            out = []
            if c.get("table"):
                out.append(table_node(table_name(resolve(c["table"]))))
            for key in ("path", "file", "dataset"):
                if c.get(key):
                    out.append(path_node(resolve(c[key])))
            if role == "source":
                for lk in c.get("lookups", []):
                    out.append(path_node(resolve(lk)) if "/" in lk else table_node(table_name(resolve(lk))))
                if c.get("sql"):
                    for s in extract_sql(sql_of(c), Context(default_db=default_db, lower=True)):
                        if s.target is None:
                            out += [table_node(r.name) for r in s.sources]
            return out

        def role(c: dict) -> str:
            t = c.get("type", "").strip().lower()
            if t.startswith(SOURCE_PREFIXES):
                return "source"
            if t.startswith(SINK_PREFIXES):
                return "sink"
            return "transform"

        preds: dict[str, list[str]] = {}
        for f in spec.get("flows", []):
            src, dst = (f["from"], f["to"]) if isinstance(f, dict) else (f[0], f[1])
            for x in (src, dst):
                if x not in comps:
                    g.warnings.append(f"{gname}: flow references unknown component {x}")
            preds.setdefault(dst, []).append(src)

        # Run SQL style components carry their own INSERT ... SELECT lineage
        for cid, c in comps.items():
            if not c.get("sql"):
                continue
            for s in extract_sql(sql_of(c), Context(default_db=default_db, lower=True)):
                if s.target is None:
                    continue
                tgt = table_node(s.target.name)
                g.add_edge("WRITES", jid, tgt, ev(cid))
                for r in s.sources:
                    src = table_node(r.name)
                    g.add_edge("READS", src, jid, ev(cid))
                    g.add_edge("FLOW", src, tgt, ev(cid))

        sinks = [cid for cid, c in comps.items() if role(c) == "sink"]
        for sink in sinks:
            targets = endpoints(comps[sink], "sink")
            if not targets:
                g.warnings.append(f"{gname}/{comps[sink].get('name', sink)}: output has no table or path")
            seen, stack, sources = set(), [sink], []
            while stack:
                cid = stack.pop()
                if cid in seen or cid not in comps:
                    continue
                seen.add(cid)
                c = comps[cid]
                r = role(c)
                if r == "source" or (c.get("lookups") and cid != sink):
                    sources += [(e, cid) for e in endpoints(c, "source")]
                if r != "source":
                    stack += preds.get(cid, [])
            if not sources and targets:
                g.warnings.append(f"{gname}/{comps[sink].get('name', sink)}: no upstream source found")
            for tgt in targets:
                g.add_edge("WRITES", jid, tgt, ev(sink))
                for src, scid in sources:
                    g.add_edge("READS", src, jid, ev(scid))
                    if src != tgt:
                        g.add_edge("FLOW", src, tgt, ev(sink))

    # -- scheduler ---------------------------------------------------------
    names = {n.name.upper(): n.name for n in g.nodes.values() if n.type == "job"}
    for p in _files(root / "scheduler", ".csv"):
        g.sources["scheduler"] = g.sources.get("scheduler", 0) + 1
        for row in read_scheduler(p):
            job = names.get(row["job"], row["job"])
            g.add_node(f"job:{job}", "job", job, schedule=row["schedule"] or None,
                       description=row["description"] or None)
            for pred in row["preds"]:
                pj = names.get(pred, pred)
                g.add_node(f"job:{pj}", "job", pj)
                g.add_edge("RUNS_AFTER", f"job:{pj}", f"job:{job}",
                           {"job": job, "file": rel(p), "line": row["line"], "origin": "scheduler"})

    # -- metastore enrichment and view definitions ---------------------------
    for name, info in meta.items():
        if info["viewText"]:
            vid = f"tbl:{name}"
            g.add_node(vid, "view", name)
            for s in extract_sql(info["viewText"], Context(default_db=name.split(".")[0], lower=True)):
                for r in s.sources:
                    g.add_edge("FLOW", table_node(r.name), vid,
                               {"job": "view definition", "file": meta_file[name], "line": info["line"],
                                "origin": "metastore", "confidence": "parsed"})
    missing = []
    for n in g.nodes.values():
        if n.type not in ("table", "view"):
            continue
        info = meta.get(n.name)
        if info is None:
            missing.append(n.name)
            continue
        if "VIEW" in info["tableKind"].upper():
            n.type = "view"
        n.attrs.update({k: v for k, v in info.items() if v and k not in ("viewText", "line")})
    if meta and missing:
        g.warnings.append(f"{len(missing)} table(s) not found in metastore export: "
                          + ", ".join(sorted(missing)[:10]) + (" ..." if len(missing) > 10 else ""))
    return g
