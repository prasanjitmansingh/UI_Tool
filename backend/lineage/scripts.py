"""Script-level lineage for BTEQ, FastLoad, MultiLoad, TPT and plain SQL.

Each parser returns a ScriptLineage describing which tables/files a script reads
and writes, plus the source->target flows. File references are returned as
written in the script (usually a DD name); the JCL layer resolves them to
datasets.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sqlscan import (
    Context, LineIndex, Ref, Stmt, blank_dot_commands, extract_sql, mask,
)


@dataclass(frozen=True)
class Endpoint:
    kind: str  # table | view | file
    name: str


@dataclass
class Flow:
    src: Endpoint
    dst: Endpoint
    line: int
    confidence: str = "parsed"  # parsed | inferred


@dataclass
class ScriptLineage:
    kind: str
    reads: list[tuple[Endpoint, int]] = field(default_factory=list)
    writes: list[tuple[Endpoint, int]] = field(default_factory=list)
    flows: list[Flow] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)  # .RUN FILE= targets
    warnings: list[str] = field(default_factory=list)

    def read(self, ep: Endpoint, line: int) -> None:
        self.reads.append((ep, line))

    def write(self, ep: Endpoint, line: int) -> None:
        self.writes.append((ep, line))

    def flow(self, src: Endpoint, dst: Endpoint, line: int, conf: str = "parsed") -> None:
        self.flows.append(Flow(src, dst, line, conf))
        self.read(src, line)
        self.write(dst, line)

    def merge(self, other: "ScriptLineage") -> None:
        self.reads += other.reads
        self.writes += other.writes
        self.flows += other.flows
        self.includes += other.includes
        self.warnings += other.warnings


def table(name: str) -> Endpoint:
    return Endpoint("table", name)


def file_ep(name: str) -> Endpoint:
    return Endpoint("file", name)


def _unquote(s: str) -> str:
    return s.strip().strip("'\"").strip()


def detect_kind(text: str) -> str:
    t = mask(text, keep_strings=True).upper()
    if re.search(r"\bDEFINE\s+(JOB|OPERATOR)\b", t) and re.search(r"\bAPPLY\b|\bTYPE\s+(LOAD|UPDATE|STREAM|EXPORT|DATACONNECTOR)\b", t):
        return "TPT"
    if re.search(r"\bBEGIN\s+LOADING\b", t):
        return "FASTLOAD"
    if re.search(r"(?m)^\s*\.BEGIN\b.*\bMLOAD\b", t):
        return "MLOAD"
    if re.search(r"(?m)^\s*\.(LOGON|LOGOFF|IMPORT|EXPORT|SET|IF|QUIT|RUN)\b", t):
        return "BTEQ"
    return "SQL"


def parse_script(text: str, ctx: Context | None = None) -> ScriptLineage:
    ctx = ctx or Context()
    kind = detect_kind(text)
    return {
        "TPT": _parse_tpt, "FASTLOAD": _parse_fastload, "MLOAD": _parse_mload,
    }.get(kind, _parse_bteq)(text, ctx, kind)


# ---------------------------------------------------------------- BTEQ / SQL

_DOT = re.compile(r"(?m)^[ \t]*\.(\w+)[ \t]*(.*)$")
_FILE_ARG = re.compile(r"""\b(?:FILE|DDNAME)\s*=?\s*(['"]?[\w.$#@()+-]+['"]?)""", re.I)


def _dot_events(nc: str) -> list[tuple[int, str, str]]:
    return [(m.start(), m.group(1).upper(), m.group(2)) for m in _DOT.finditer(nc)]


def _apply_stmt(sl: ScriptLineage, s: Stmt, imp: str | None, exp: str | None) -> None:
    if s.kind == "ddl":
        return
    if s.target:
        ep = Endpoint("view" if s.target_is_view else "table", s.target.name)
        sl.write(ep, s.target.line)
        for r in s.sources:
            sl.flow(table(r.name), ep, r.line)
        if not s.sources and imp and s.kind in ("insert", "update", "merge"):
            sl.flow(file_ep(imp), ep, s.target.line, "inferred")
    elif s.kind == "select":
        for r in s.sources:
            sl.read(table(r.name), r.line)
            if exp:
                sl.flow(table(r.name), file_ep(exp), r.line, "inferred")


def _parse_bteq(text: str, ctx: Context, kind: str) -> ScriptLineage:
    sl = ScriptLineage(kind)
    nc = mask(text, keep_strings=True)
    lines = LineIndex(nc)
    events: list[tuple[int, str, object]] = [(o, "dot", (c, a)) for o, c, a in _dot_events(nc)]
    # statements carry their own start offset; extract_sql shares the same layout
    for s in extract_sql(text, ctx):
        events.append((s.pos, "stmt", s))
    events.sort(key=lambda e: e[0])
    imp = exp = None
    for pos, typ, payload in events:
        if typ == "stmt":
            _apply_stmt(sl, payload, imp, exp)
            continue
        cmd, args = payload
        m = _FILE_ARG.search(args)
        arg = _unquote(m.group(1)) if m else None
        if cmd == "IMPORT":
            imp = None if args.strip().upper().startswith("RESET") else arg
        elif cmd == "EXPORT":
            exp = None if args.strip().upper().startswith("RESET") else arg
        elif cmd == "RUN" and arg:
            sl.includes.append(arg)
    return sl


# ------------------------------------------------------------------ FastLoad

def _parse_fastload(text: str, ctx: Context, kind: str) -> ScriptLineage:
    sl = ScriptLineage(kind)
    nc = mask(text, keep_strings=True)
    lines = LineIndex(nc)
    files: list[str] = []
    for m in re.finditer(r"(?is)\bDEFINE\b.*?\bFILE\s*=?\s*([\w.$#@()+-]+)\s*;", nc):
        files.append(m.group(1))
    targets = []
    for m in re.finditer(r"(?i)\bBEGIN\s+LOADING\s+(" + r"[\w$#&{}.\"]+" + r")", nc):
        name = ctx.normalise(m.group(1))
        if name:
            targets.append((name, lines.line(m.start(1))))
    if targets and not files:
        sl.warnings.append("FastLoad script has no DEFINE ... FILE=")
    for name, line in targets:
        sl.write(table(name), line)
        for f in files:
            sl.flow(file_ep(f), table(name), line)
    return sl


# ------------------------------------------------------------------ MultiLoad

_ML_STOP = re.compile(
    r"(?i)\b(WORKTABLES|ERRORTABLES|ERRORFILES|CHECKPOINT|SESSIONS|TENACITY|SLEEP|"
    r"NOTIFY|AMPCHECK|QUERYBAND|SQLERROR)\b|;"
)


def _parse_mload(text: str, ctx: Context, kind: str) -> ScriptLineage:
    sl = ScriptLineage(kind)
    nc = mask(text, keep_strings=True)
    lines = LineIndex(nc)
    tables: list[tuple[str, int]] = []
    for m in re.finditer(r"(?is)\.BEGIN\b[^;]*?\bMLOAD\s+TABLES\s+(.*?)(?=" + _ML_STOP.pattern[4:] + r"|\Z)", nc):
        clause_start = m.start(1)
        for n in re.finditer(r"[\w$#&{}.\"]+", m.group(1)):
            name = ctx.normalise(n.group())
            if name:
                tables.append((name, lines.line(clause_start + n.start())))
    for name, line in tables:
        sl.write(table(name), line)

    # statements per DML label
    label_targets: dict[str, list[tuple[str, int]]] = {}
    cur = None
    events = []
    for m in re.finditer(r"(?im)^[ \t]*\.DML\s+LABEL\s+(\w+)", nc):
        events.append((m.start(), "label", m.group(1).upper()))
    for m in re.finditer(r"(?is)^[ \t]*\.IMPORT\b(.*?);", nc, re.M):
        events.append((m.start(), "import", (m.group(1), lines.line(m.start()))))
    for s in extract_sql(text, ctx):
        if s.target and s.kind in ("insert", "update", "merge", "delete"):
            events.append((s.pos, "stmt", s))
    events.sort(key=lambda e: e[0])
    imports = []
    for pos, typ, p in events:
        if typ == "label":
            cur = p
        elif typ == "stmt":
            ep = table(p.target.name)
            sl.write(ep, p.target.line)
            for r in p.sources:
                sl.flow(table(r.name), ep, r.line)
            if cur:
                label_targets.setdefault(cur, []).append((p.target.name, p.target.line))
        else:
            imports.append(p)
    for args, line in imports:
        f = re.search(r"(?i)\bINFILE\s+([\w.$#@()+-]+|'[^']+')", args)
        if not f:
            continue
        infile = _unquote(f.group(1))
        applied = re.search(r"(?is)\bAPPLY\s+(.*?)(?:\bWHERE\b|$)", args)
        labels = re.findall(r"\w+", applied.group(1).upper()) if applied else []
        targets = [t for lb in labels for t in label_targets.get(lb, [])] or tables
        for name, _ in targets:
            sl.flow(file_ep(infile), table(name), line, "parsed" if labels else "inferred")
    return sl


# ------------------------------------------------------------------------ TPT

_TPT_ATTR = re.compile(
    r"(?is)\b(TargetTable|FileName|SelectStmt|TableName|DSNName)\s*=\s*"
    r"('(?:[^']|'')*'|@?[\w.$#]+)"
)


def _parse_tpt(text: str, ctx: Context, kind: str) -> ScriptLineage:
    sl = ScriptLineage(kind)
    nc = mask(text, keep_strings=True)
    lines = LineIndex(nc)
    ops: dict[str, dict] = {}
    blocks = list(re.finditer(r"(?is)\bDEFINE\s+OPERATOR\s+(\w+)", nc))
    for k, m in enumerate(blocks):
        end = len(nc)
        nxt = re.search(r"(?is)\b(DEFINE\s+(?:OPERATOR|SCHEMA|JOB)|APPLY|STEP)\b", nc[m.end():])
        if nxt:
            end = m.end() + nxt.start()
        body = nc[m.start():end]
        tm = re.search(r"(?is)\bTYPE\s+(\w+)(?:\s+(PRODUCER|CONSUMER))?", body)
        op = {"type": (tm.group(1).upper() if tm else ""), "role": (tm.group(2) or "").upper() if tm else "",
              "line": lines.line(m.start()), "attrs": {}}
        for a in _TPT_ATTR.finditer(body):
            op["attrs"][a.group(1).lower()] = a.group(2)
        ops[m.group(1).upper()] = op

    def resolve(val: str) -> str | None:
        if val.startswith("@"):
            sl.warnings.append(f"TPT job variable {val} is not resolved")
            return None
        return _unquote(val).replace("''", "'")

    def endpoints(op: dict, producer: bool) -> list[tuple[Endpoint, int]]:
        out: list[tuple[Endpoint, int]] = []
        a = op["attrs"]
        if "filename" in a:
            v = resolve(a["filename"])
            if v:
                out.append((file_ep(re.sub(r"(?i)^DD:", "", v)), op["line"]))
        for key in ("targettable", "tablename"):
            if key in a:
                v = resolve(a[key])
                n = v and ctx.normalise(v)
                if n:
                    out.append((table(n), op["line"]))
        if producer and "selectstmt" in a:
            v = resolve(a["selectstmt"])
            if v:
                for s in extract_sql(v, ctx):
                    for r in s.sources:
                        out.append((table(r.name), op["line"]))
        return out

    pairs: list[tuple[str, str]] = []
    for m in re.finditer(r"(?is)\bAPPLY\b(.*?)(?:;|\Z)", nc):
        body = m.group(1)
        c = re.search(r"(?is)\bTO\s+OPERATOR\s*\(\s*(\w+)", body)
        p = re.search(r"(?is)\bFROM\s+OPERATOR\s*\(\s*(\w+)", body)
        if c and p:
            pairs.append((p.group(1).upper(), c.group(1).upper()))
    if not pairs:
        producers = [n for n, o in ops.items() if o["role"] == "PRODUCER" or o["type"] in ("EXPORT", "SELECTOR", "ODBC")]
        consumers = [n for n, o in ops.items() if n not in producers and o["type"] != "DDL"]
        pairs = [(p, c) for p in producers for c in consumers]
    for p, c in pairs:
        if p not in ops or c not in ops:
            continue
        for src, sline in endpoints(ops[p], True):
            for dst, dline in endpoints(ops[c], False):
                if src != dst:
                    sl.flow(src, dst, dline, "parsed")
    return sl
