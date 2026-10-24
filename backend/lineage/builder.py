"""Scan a data directory and build the lineage graph.

Layout (every folder optional)::

    jcl/        JCL members (one or more jobs per file)
    procs/      cataloged / in-stream procedure libraries
    scripts/    BTEQ, FastLoad, MultiLoad, TPT and SQL members (file name = member name)
    scheduler/  *.csv  job,predecessor,schedule
    dbql/       *.csv  job_name,sql_text   (or DBQL native columns)
    dbc/        *.csv  DatabaseName,TableName,TableKind,...
    config.json {"default_database": "...", "substitutions": {"&ENV": "PROD"}}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .graph import Graph
from .jcl import DD, Step, expand_steps, load_procs, parse_jcl
from .scripts import Endpoint, ScriptLineage, parse_script
from .sqlscan import Context
from .sources import read_dbc_tables, read_dbql, read_scheduler

SCRIPT_DDS = {"SYSIN", "SYSTSIN", "BTQIN", "BTEQIN", "BTEQCMD", "FLDIN", "FASTLIN",
              "MLDIN", "MLOADIN", "TPTIN", "TPTSCRIPT", "SQLIN", "SCRIPT"}
LOADER_PGMS = {"BTEQ", "BTQMAIN", "FASTLOAD", "FASTLD", "MLOAD", "MULTLOAD", "TBUILD",
               "TPT", "TPTLOAD", "BTQ", "TDLOAD"}
SKIP_DIRS = {"__pycache__"}


def _files(folder: Path):
    if folder.is_dir():
        for p in sorted(folder.rglob("*")):
            if p.is_file() and not set(p.parts) & SKIP_DIRS:
                yield p


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _strip_gdg(dsn: str) -> tuple[str, bool]:
    m = re.match(r"^(.*)\((?:\+|-)?\d+\)$", dsn)
    return (m.group(1), True) if m else (dsn, False)


def build_graph(data_dir: str | Path) -> Graph:
    root = Path(data_dir)
    g = Graph()
    cfg = {}
    if (root / "config.json").exists():
        cfg = json.loads(_read(root / "config.json"))
    subs: dict[str, str] = cfg.get("substitutions", {})
    default_db = cfg.get("default_database")

    def prep(text: str) -> str:
        for k, v in subs.items():
            text = text.replace(k, v)
        return text

    def rel(p: Path) -> str:
        return str(p.relative_to(root))

    # -- script library: lookup by member name and by dotted DSN ------------
    scripts: dict[str, Path] = {}
    for p in _files(root / "scripts"):
        scripts.setdefault(p.stem.upper(), p)
        scripts.setdefault(p.name.upper(), p)
    g.sources["scripts"] = len({str(p) for p in scripts.values()})

    procs = load_procs([_read(p) for p in _files(root / "procs")])
    g.sources["procs"] = len(procs)

    def tbl_id(name: str) -> str:
        return f"tbl:{name}"

    def ensure_endpoint(ep: Endpoint) -> str:
        if ep.kind == "file":
            nid = f"ds:{ep.name}"
            g.add_node(nid, "dataset", ep.name)
        else:
            nid = tbl_id(ep.name)
            g.add_node(nid, ep.kind, ep.name, temporary=ep.name.startswith("VOLATILE:") or None)
        return nid

    def locate_script(dd: DD) -> Path | None:
        if not dd.dsn:
            return None
        m = re.match(r"^(.*)\((\w+)\)$", dd.dsn)
        keys = [m.group(2), dd.dsn] if m else [dd.dsn]
        return next((scripts[k.upper()] for k in keys if k.upper() in scripts), None)

    def resolve_file(name: str, step: Step, job: str) -> str:
        key = re.sub(r"(?i)^DD:", "", name).upper()
        dd = next((d for d in step.dds if d.name == key), None)
        if dd is None:
            if "." in key:
                return _strip_gdg(key)[0]
            g.warnings.append(f"{job}/{step.name}: DD {key} referenced by script is not defined in the JCL")
            return f"UNRESOLVED:{job}.{step.name}.{key}"
        if dd.dsn:
            dsn, gdg = _strip_gdg(dd.dsn)
            g.add_node(f"ds:{dsn}", "dataset", dsn, gdg=gdg or None, dd=dd.name)
            return dsn
        if dd.inline is not None:
            return f"INLINE:{job}.{step.name}.{dd.name}"
        return f"UNRESOLVED:{job}.{step.name}.{key}"

    def record(job: str, step_name: str, file: str, sl: ScriptLineage, base: int,
               step: Step | None, origin: str, extra: dict | None = None) -> None:
        jid = f"job:{job}"

        def fix(ep: Endpoint) -> Endpoint:
            if ep.kind == "file" and step is not None:
                return Endpoint("file", resolve_file(ep.name, step, job))
            return ep

        def ev(line: int, conf: str = "parsed") -> dict:
            return {"job": job, "step": step_name, "file": file, "line": base + line - 1,
                    "origin": origin, "confidence": conf, "scriptKind": sl.kind, **(extra or {})}

        for ep, line in sl.reads:
            g.add_edge("READS", ensure_endpoint(fix(ep)), jid, ev(line))
        for ep, line in sl.writes:
            g.add_edge("WRITES", jid, ensure_endpoint(fix(ep)), ev(line))
        for f in sl.flows:
            g.add_edge("FLOW", ensure_endpoint(fix(f.src)), ensure_endpoint(fix(f.dst)),
                       ev(f.line, f.confidence))
        for w in sl.warnings:
            g.warnings.append(f"{job}/{step_name}: {w}")

    def run_script(job: str, step: Step, dd: DD, depth: int = 0) -> None:
        ctx = Context(scope=job, default_db=default_db)
        path = locate_script(dd)
        if path is not None:
            text, file, base = prep(_read(path)), rel(path), 1
        elif dd.inline and (dd.name in SCRIPT_DDS or (step.pgm or "") in LOADER_PGMS
                            or step.params.get("_proc")):
            text, file, base = prep(dd.inline), dd.__dict__.get("jcl_file", ""), dd.inline_line
        else:
            return
        sl = parse_script(text, ctx)
        if not (sl.reads or sl.writes or sl.includes or sl.warnings):
            return
        record(job, step.name, file, sl, base, step, "jcl")
        if depth < 3:
            for inc in sl.includes:
                key = re.sub(r"(?i)^DD:", "", inc).upper()
                nested = next((d for d in step.dds if d.name == key), None)
                if nested is not None and nested is not dd:
                    run_script(job, step, nested, depth + 1)

    # -- JCL ---------------------------------------------------------------
    jcl_files = list(_files(root / "jcl"))
    g.sources["jcl"] = len(jcl_files)
    for p in jcl_files:
        member = parse_jcl(_read(p))
        job = member.job or p.stem.upper()
        jid = f"job:{job}"
        steps = expand_steps(member, procs)
        g.add_node(jid, "job", job, file=rel(p), line=1,
                   steps=[{"name": s.name, "pgm": s.pgm or s.params.get("_proc") or s.proc}
                          for s in steps])
        for s in steps:
            if s.proc and not s.dds and s.name in {x.name for x in member.steps}:
                if s.proc not in procs and s.proc not in member.procs:
                    g.warnings.append(f"{job}/{s.name}: procedure {s.proc} not found in procs/")
            for dd in s.dds:
                if dd.inline:
                    dd.__dict__["jcl_file"] = rel(p)
                run_script(job, s, dd)

    # -- scheduler -----------------------------------------------------------
    for p in _files(root / "scheduler"):
        if p.suffix.lower() != ".csv":
            continue
        g.sources["scheduler"] = g.sources.get("scheduler", 0) + 1
        for row in read_scheduler(p):
            jid = f"job:{row['job']}"
            g.add_node(jid, "job", row["job"], schedule=row["schedule"] or None,
                       description=row["description"] or None)
            for pred in row["preds"]:
                g.add_node(f"job:{pred}", "job", pred)
                g.add_edge("RUNS_AFTER", f"job:{pred}", jid,
                           {"job": row["job"], "file": rel(p), "line": row["line"],
                            "origin": "scheduler"})

    # -- DBQL ----------------------------------------------------------------
    for p in _files(root / "dbql"):
        if p.suffix.lower() != ".csv":
            continue
        g.sources["dbql"] = g.sources.get("dbql", 0) + 1
        for q in read_dbql(p):
            ctx = Context(scope=q["job"], default_db=default_db)
            prefix = f"tbl:VOLATILE:{q['job']}."  # volatile tables already seen in this job's scripts
            ctx.volatile = {nid[len(prefix):] for nid in g.nodes if nid.startswith(prefix)}
            sl = parse_script(prep(q["sql"]), ctx)
            if not (sl.reads or sl.writes):
                continue
            g.add_node(f"job:{q['job']}", "job", q["job"])
            record(q["job"], "DBQL", rel(p), sl, q["line"], None, "dbql",
                   {"confidence": "observed", "executions": q["count"]})

    # -- dictionary ----------------------------------------------------------
    dictionary: dict[str, dict] = {}
    for p in _files(root / "dbc"):
        if p.suffix.lower() == ".csv":
            dictionary.update(read_dbc_tables(p))
    if dictionary:
        g.sources["dbc"] = len(dictionary)
        missing = []
        for n in g.nodes.values():
            if n.type in ("table", "view") and not n.attrs.get("temporary"):
                info = dictionary.get(n.name)
                if info is None:
                    missing.append(n.name)
                    continue
                if info["tableKind"].upper() == "V":
                    n.type = "view"
                n.attrs.update({k: v for k, v in info.items() if v})
        if missing:
            shown = ", ".join(sorted(missing)[:10])
            g.warnings.append(f"{len(missing)} table(s) not found in DBC export: {shown}"
                              + (" ..." if len(missing) > 10 else ""))
    return g

