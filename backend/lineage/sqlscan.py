"""Table-level extraction from Teradata SQL / BTEQ text.

This is a purpose-built scanner rather than a full SQL grammar: it finds the
target of each DML/DDL statement and every table it reads (FROM / JOIN / USING),
which is all that table-level lineage needs. Comments and string literals are
blanked (same length) so offsets still map to source line numbers.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

SYSTEM_PREFIXES = ("DBC.", "SYS_CALENDAR.", "TD_SYSFNLIB.", "SYSLIB.", "SYSUDTLIB.")
IGNORED_NAMES = {"DUAL", "TABLE", "LATERAL", "UNNEST", "SELECT", "SEL", "WITH"}
FUNC_FROM = {"EXTRACT", "TRIM", "SUBSTRING", "SUBSTR", "OVERLAY"}
ALIAS_STOP = {
    "WHERE", "GROUP", "ORDER", "HAVING", "QUALIFY", "UNION", "INTERSECT", "EXCEPT",
    "MINUS", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "ON", "USING", "SET",
    "WHEN", "SAMPLE", "WITH", "LIMIT", "NATURAL", "OUTER", "FROM", "SELECT", "SEL",
    "INSERT", "VALUES", "ALL", "NORMALIZE", "ANSI", "PRIMARY", "NO", "DATA",
}
LOCK_MODES = {"ACCESS", "READ", "WRITE", "EXCLUSIVE", "EXCL", "SHARE", "CHECKSUM"}

_NAME = r'(?:"[^"]+"|[\w$#&%@{}]+)(?:\.(?:"[^"]+"|[\w$#&%@{}]*))*'
_TOKEN = re.compile(
    rf"(?P<name>{_NAME})|(?P<str>'[^']*')|(?P<punct>[(),;])|(?P<other>\S)"
)


def mask(text: str, keep_strings: bool = False) -> str:
    """Blank comments (and, unless keep_strings, string contents) in place."""
    out = list(text)
    n = len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = text[i]
        if c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            if not keep_strings:
                blank(i + 1, j)
            i = j + 1
        elif c == '"':
            j = text.find('"', i + 1)
            i = (n if j < 0 else j) + 1
        elif text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


def blank_dot_commands(text: str) -> str:
    """Blank BTEQ-style dot-command lines so they don't pollute SQL statements."""
    return re.sub(r"(?m)^[ \t]*\.[A-Za-z].*$", lambda m: " " * len(m.group(0)), text)


class LineIndex:
    def __init__(self, text: str):
        self._starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line(self, offset: int) -> int:
        return bisect_right(self._starts, offset)


@dataclass
class Tok:
    kind: str
    text: str
    pos: int

    @property
    def up(self) -> str:
        return self.text.upper()


def tokenize(text: str) -> list[Tok]:
    return [Tok(m.lastgroup, m.group(), m.start()) for m in _TOKEN.finditer(text)]


@dataclass
class Ref:
    name: str
    line: int


@dataclass
class Stmt:
    kind: str  # insert | update | merge | delete | ctas | view | select | ddl
    target: Ref | None
    sources: list[Ref]
    line: int
    target_is_view: bool = False
    pos: int = 0


@dataclass
class Context:
    scope: str = ""  # job name, used to scope volatile tables
    default_db: str | None = None
    volatile: set[str] = field(default_factory=set)

    def normalise(self, raw: str) -> str | None:
        name = raw.replace('"', "").strip(".").upper()
        if not name or name.startswith(SYSTEM_PREFIXES) or name in IGNORED_NAMES:
            return None
        if "." not in name:
            if name in self.volatile:
                return f"VOLATILE:{self.scope}.{name}"
            if self.default_db:
                return f"{self.default_db}.{name}"
        return name


def _matching(toks: list[Tok], i: int) -> int:
    depth = 0
    for k in range(i, len(toks)):
        if toks[k].text == "(":
            depth += 1
        elif toks[k].text == ")":
            depth -= 1
            if depth == 0:
                return k
    return len(toks) - 1


def cte_names(toks: list[Tok]) -> set[str]:
    names: set[str] = set()
    for i, t in enumerate(toks):
        if t.up != "WITH":
            continue
        j = i + 1
        if j < len(toks) and toks[j].up == "RECURSIVE":
            j += 1
        while j + 1 < len(toks) and toks[j].kind == "name":
            k = j + 1
            if toks[k].text == "(" and toks[k + 1:k + 2] and toks[k].kind == "punct":
                # optional column list before AS
                close = _matching(toks, k)
                if close + 1 < len(toks) and toks[close + 1].up == "AS":
                    k = close + 1
            if toks[k].up != "AS" or k + 1 >= len(toks) or toks[k + 1].text != "(":
                break
            names.add(toks[j].up)
            end = _matching(toks, k + 1)
            j = end + 1
            if j < len(toks) and toks[j].text == ",":
                j += 1
            else:
                break
    return names


class _Walker:
    """Collects table references following FROM / JOIN (and MERGE ... USING)."""

    def __init__(self, ctx: Context, lines: LineIndex, ctes: set[str], merge: bool):
        self.ctx, self.lines, self.ctes, self.merge = ctx, lines, ctes, merge
        self.refs: list[Ref] = []
        self.aliases: dict[str, str] = {}

    def walk(self, toks: list[Tok]) -> None:
        stack: list[str] = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.kind == "punct":
                if t.text == "(":
                    stack.append(toks[i - 1].up if i else "")
                elif t.text == ")" and stack:
                    stack.pop()
                i += 1
                continue
            w = t.up
            in_func = bool(stack) and stack[-1] in FUNC_FROM
            if (w == "FROM" and not in_func) or w == "JOIN" or (w == "USING" and self.merge):
                i = self._table_list(toks, i, comma=w in ("FROM", "USING"))
                continue
            i += 1

    def _table_list(self, toks: list[Tok], i: int, comma: bool) -> int:
        j = i + 1
        while j < len(toks):
            t = toks[j]
            if t.text == "(":
                close = _matching(toks, j)
                self.walk(toks[j + 1:close])
                j = close + 1
            elif t.kind == "name" and t.up not in ("SELECT", "SEL"):
                name = self.ctx.normalise(t.text) if t.up not in self.ctes else None
                if name:
                    self.refs.append(Ref(name, self.lines.line(t.pos)))
                j += 1
                if j < len(toks) and toks[j].text == "(" and t.up == "TABLE":
                    j = _matching(toks, j) + 1
            else:
                return j
            # optional alias
            alias = None
            if j < len(toks) and toks[j].up == "AS" and j + 1 < len(toks):
                alias = toks[j + 1].text
                j += 2
            elif j < len(toks) and toks[j].kind == "name" and toks[j].up not in ALIAS_STOP:
                alias = toks[j].text
                j += 1
            if alias and t.kind == "name" and name:
                self.aliases[alias.upper()] = name
            if comma and j < len(toks) and toks[j].text == ",":
                j += 1
                continue
            return j
        return j


def _strip_locking(toks: list[Tok]) -> list[Tok]:
    i = 0
    while i < len(toks) and toks[i].up in ("LOCKING", "LOCK"):
        j = i
        while j < len(toks) and toks[j].up not in LOCK_MODES:
            j += 1
        i = j + 1
        while i < len(toks) and toks[i].up in ("MODE", "NOWAIT"):
            i += 1
    return toks[i:]


def _dedupe(refs: list[Ref], exclude: str | None) -> list[Ref]:
    seen, out = set(), []
    for r in refs:
        if r.name != exclude and r.name not in seen:
            seen.add(r.name)
            out.append(r)
    return out


def analyse(toks: list[Tok], ctx: Context, lines: LineIndex) -> Stmt | None:
    toks = _strip_locking(toks)
    if toks and toks[0].up == "USING" and len(toks) > 1 and toks[1].text == "(":
        toks = toks[_matching(toks, 1) + 1:]  # BTEQ USING (...) prefix
    toks = _strip_locking(toks)
    if not toks:
        return None
    head = toks[0].up
    ctes = cte_names(toks)
    line = lines.line(toks[0].pos)

    def finish(kind, target_tok_idx, rest_from, merge=False, view=False, alias_target=False):
        tt = toks[target_tok_idx]
        w = _Walker(ctx, lines, ctes, merge)
        w.walk(toks[rest_from:])
        raw = tt.text
        if alias_target and raw.upper() in w.aliases:
            target = w.aliases[raw.upper()]
        else:
            target = ctx.normalise(raw)
        if not target:
            return None
        return Stmt(kind, Ref(target, lines.line(tt.pos)), _dedupe(w.refs, target),
                    line, view, toks[0].pos)

    if head in ("INSERT", "INS"):
        j = 2 if len(toks) > 1 and toks[1].up == "INTO" else 1
        return finish("insert", j, j + 1) if j < len(toks) else None
    if head in ("UPDATE", "UPD") and len(toks) > 1:
        return finish("update", 1, 2, alias_target=True)
    if head == "MERGE" and len(toks) > 2:
        return finish("merge", 2, 3, merge=True)
    if head in ("DELETE", "DEL") and len(toks) > 1:
        j = 2 if toks[1].up == "FROM" else 1
        if j < len(toks):
            return finish("delete", j, j + 1, alias_target=True) if j == 1 else \
                finish("delete", j, j + 1)
        return None
    if head in ("CREATE", "REPLACE"):
        return _analyse_create(toks, ctx, lines, ctes)
    if head in ("SELECT", "SEL", "WITH"):
        w = _Walker(ctx, lines, ctes, False)
        w.walk(toks)
        return Stmt("select", None, _dedupe(w.refs, None), line, pos=toks[0].pos)
    return None


def _analyse_create(toks: list[Tok], ctx: Context, lines: LineIndex, ctes: set[str]) -> Stmt | None:
    words = [t.up for t in toks[:10]]
    kw = next((k for k, w in enumerate(words) if w in ("TABLE", "VIEW")), None)
    if kw is None or kw + 1 >= len(toks):
        return None
    is_view = words[kw] == "VIEW"
    volatile = "VOLATILE" in words[:kw] or ("GLOBAL" in words[:kw] and "TEMPORARY" in words[:kw])
    name_tok = toks[kw + 1]
    if volatile and "." not in name_tok.text:
        ctx.volatile.add(name_tok.text.replace('"', "").upper())
    target = ctx.normalise(name_tok.text)
    if not target:
        return None
    line = lines.line(toks[0].pos)
    tref = Ref(target, lines.line(name_tok.pos))
    rest = toks[kw + 2:]
    as_idx = next((k for k, t in enumerate(rest) if t.up == "AS"), None)
    if as_idx is None:
        return Stmt("ddl", tref, [], line, pos=toks[0].pos)
    after = rest[as_idx + 1:]
    if not is_view:
        # AS must directly follow the table name / options, not a column definition
        nxt = after[0] if after else None
        if nxt is None:
            return Stmt("ddl", tref, [], line, pos=toks[0].pos)
        if nxt.kind == "name" and nxt.up not in ("SELECT", "SEL", "WITH", "LOCKING", "LOCK"):
            ups = [t.up for t in after]
            no_data = any(a == "NO" and b == "DATA" for a, b in zip(ups, ups[1:]))
            src = ctx.normalise(nxt.text)
            srcs = [] if no_data or not src else [Ref(src, lines.line(nxt.pos))]
            return Stmt("ctas", tref, _dedupe(srcs, target), line, pos=toks[0].pos)
    w = _Walker(ctx, lines, ctes, False)
    w.walk(after)
    return Stmt("view" if is_view else "ctas", tref, _dedupe(w.refs, target), line,
                is_view, toks[0].pos)


def split_statements(toks: list[Tok]) -> list[list[Tok]]:
    out, cur = [], []
    for t in toks:
        if t.text == ";" and t.kind == "punct":
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def extract_sql(text: str, ctx: Context | None = None, line_offset: int = 0) -> list[Stmt]:
    """Return lineage for every statement in `text`, in order."""
    ctx = ctx or Context()
    masked = blank_dot_commands(mask(text))
    lines = LineIndex(masked)
    result: list[Stmt] = []
    for st in split_statements(tokenize(masked)):
        if st[0].up == "DATABASE" and len(st) > 1:
            ctx.default_db = ctx.normalise(st[1].text) or ctx.default_db
            continue
        s = analyse(st, ctx, lines)
        if s:
            for r in ([s.target] if s.target else []) + s.sources:
                r.line += line_offset
            s.line += line_offset
            result.append(s)
    return result
