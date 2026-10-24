"""Minimal JCL reader: jobs, steps, DD statements, in-stream data, procs, symbolics."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DD:
    name: str
    line: int
    dsn: str | None = None
    disp: str | None = None
    inline: str | None = None
    inline_line: int = 0
    dummy: bool = False
    sysout: bool = False


@dataclass
class Step:
    name: str
    pgm: str | None
    proc: str | None
    line: int
    params: dict[str, str] = field(default_factory=dict)
    dds: list[DD] = field(default_factory=list)
    overrides: list[tuple[str, DD]] = field(default_factory=list)  # (procstep, dd)
    proc_steps: list["Step"] = field(default_factory=list)


@dataclass
class Proc:
    name: str
    defaults: dict[str, str]
    steps: list[Step]


@dataclass
class JclMember:
    job: str | None
    steps: list[Step]
    procs: dict[str, Proc]
    symbols: dict[str, str]


_STMT = re.compile(r"^//(\S*)\s+(\S+)(?:\s+(.*))?$")


def _operand_field(rest: str) -> tuple[str, bool]:
    """Operands end at the first blank outside quotes/parens; report trailing comma."""
    depth, quote, i = 0, False, 0
    while i < len(rest):
        c = rest[i]
        if c == "'":
            quote = not quote
        elif not quote:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == " " and depth <= 0:
                break
        i += 1
    ops = rest[:i]
    return ops, ops.endswith(",")


def _split_ops(ops: str) -> list[str]:
    parts, cur, depth, quote = [], "", 0, False
    for c in ops:
        if c == "'":
            quote = not quote
        if not quote and c == "(":
            depth += 1
        if not quote and c == ")":
            depth -= 1
        if c == "," and depth == 0 and not quote:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    if cur:
        parts.append(cur)
    return parts


def _kv(ops: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in _split_ops(ops):
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().upper()] = v.strip().strip("'")
        elif p.strip():
            out.setdefault("_", p.strip())
    return out


def substitute(s: str | None, symbols: dict[str, str]) -> str | None:
    if s is None:
        return None
    return re.sub(
        r"(?<!&)&([A-Z#$@][A-Z0-9#$@]*)\.?",
        lambda m: symbols.get(m.group(1).upper(), m.group(0)), s, flags=re.I,
    )


def _dd_from(name: str, ops: str, line: int, symbols: dict[str, str]) -> DD:
    dd = DD(name=name, line=line)
    kv = _kv(ops)
    first = _split_ops(ops)[0].strip() if ops else ""
    if first in ("*", "DATA") or first.upper().startswith(("DATA", "*")) and "=" not in first:
        dd.inline = ""
    if "DUMMY" in ops.upper().split(",")[:1] or first.upper() == "DUMMY":
        dd.dummy = True
    if "SYSOUT" in kv:
        dd.sysout = True
    dsn = kv.get("DSN") or kv.get("DSNAME")
    if dsn:
        dsn = substitute(dsn, symbols) or dsn
        dd.dsn = dsn.upper()
    disp = kv.get("DISP")
    if disp:
        dd.disp = disp.strip("()").split(",")[0].upper()
    return dd


def parse_jcl(text: str) -> JclMember:
    raw = text.splitlines()
    job: str | None = None
    steps: list[Step] = []
    procs: dict[str, Proc] = {}
    symbols: dict[str, str] = {}
    cur_proc: tuple[str, dict[str, str], list[Step]] | None = None
    cur_dd: DD | None = None
    i = 0

    def target_steps() -> list[Step]:
        return cur_proc[2] if cur_proc else steps

    while i < len(raw):
        line = raw[i]
        lineno = i + 1
        if len(line) == 80:
            line = line[:72]
        i += 1
        if not line.startswith("//") or line.startswith("//*"):
            if cur_dd is not None and cur_dd.inline is not None and not line.startswith("/*"):
                if not cur_dd.inline_line:
                    cur_dd.inline_line = lineno
                cur_dd.inline += line + "\n"
            elif line.startswith("/*"):
                cur_dd = None
            continue
        if line.strip() == "//":
            cur_dd = None
            continue
        m = _STMT.match(line.rstrip())
        if not m:
            cur_dd = None
            continue
        name, op, rest = m.group(1), m.group(2).upper(), (m.group(3) or "").strip()
        ops, cont = _operand_field(rest)
        while cont and i < len(raw) and raw[i].startswith("//") and not raw[i].startswith("//*"):
            nxt = raw[i][2:].lstrip()
            if len(raw[i]) == 80:
                nxt = raw[i][:72][2:].lstrip()
            more, cont = _operand_field(nxt)
            ops += more
            i += 1
        cur_dd = None
        if op == "JOB":
            job = name.upper()
        elif op == "SET":
            for k, v in _kv(ops).items():
                symbols[k] = v
        elif op == "PROC":
            cur_proc = (name.upper(), {k: v for k, v in _kv(ops).items() if k != "_"}, [])
        elif op == "PEND":
            if cur_proc:
                procs[cur_proc[0]] = Proc(cur_proc[0], cur_proc[1], cur_proc[2])
            cur_proc = None
        elif op == "EXEC":
            kv = _kv(ops)
            pgm = kv.pop("PGM", None)
            proc = kv.pop("PROC", None)
            if not pgm and not proc and "_" in kv:
                proc = kv.pop("_")
            kv.pop("_", None)
            target_steps().append(Step(name.upper() or f"STEP{len(target_steps()) + 1}",
                                       pgm.upper() if pgm else None,
                                       proc.upper() if proc else None, lineno, kv))
        elif op == "DD":
            tgt = target_steps()
            if not tgt:
                continue
            step = tgt[-1]
            if "." in name:
                pstep, ddname = name.upper().split(".", 1)
                dd = _dd_from(ddname, ops, lineno, symbols)
                step.overrides.append((pstep, dd))
            else:
                dd = _dd_from(name.upper() or (step.dds[-1].name if step.dds else ""), ops, lineno, symbols)
                step.dds.append(dd)
            cur_dd = dd
    return JclMember(job, steps, procs, symbols)


def expand_steps(member: JclMember, proc_lib: dict[str, Proc]) -> list[Step]:
    """Return executable steps with PROC calls expanded and symbols substituted."""
    out: list[Step] = []
    for step in member.steps:
        proc = member.procs.get(step.proc or "") or proc_lib.get(step.proc or "")
        if not step.proc:
            for dd in step.dds:
                _subst_dd(dd, member.symbols)
            out.append(step)
            continue
        if not proc:
            step.proc_steps = []
            out.append(step)  # unresolved proc; kept so it can be reported
            continue
        syms = {**member.symbols, **proc.defaults, **step.params}
        for ps in proc.steps:
            copy = Step(f"{step.name}.{ps.name}", ps.pgm, None, ps.line, dict(ps.params),
                        [DD(**vars(d)) for d in ps.dds])
            copy.params["_proc"] = proc.name
            for pstep, odd in step.overrides:
                if pstep == ps.name:
                    existing = next((d for d in copy.dds if d.name == odd.name), None)
                    if existing:
                        copy.dds[copy.dds.index(existing)] = DD(**vars(odd))
                    else:
                        copy.dds.append(DD(**vars(odd)))
            for dd in copy.dds:
                _subst_dd(dd, syms)
            out.append(copy)
    return out


def _subst_dd(dd: DD, syms: dict[str, str]) -> None:
    if dd.dsn:
        dd.dsn = (substitute(dd.dsn, syms) or dd.dsn).upper()
    if dd.inline:
        dd.inline = substitute(dd.inline, syms) or dd.inline


def load_procs(texts: list[str]) -> dict[str, Proc]:
    lib: dict[str, Proc] = {}
    for t in texts:
        lib.update(parse_jcl(t).procs)
    return lib
