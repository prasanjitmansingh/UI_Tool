"""Readers for CSV exports: scheduler (CA-7/Control-M/TWS), DBQL and DBC.TablesV."""
from __future__ import annotations

import csv
import re
from collections import OrderedDict
from pathlib import Path


def _rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for n, row in enumerate(reader, start=2):  # header is line 1
            yield n, {(k or "").strip().lower().replace(" ", "_"): (v or "").strip()
                      for k, v in row.items()}


def _first(row: dict, *keys: str) -> str:
    return next((row[k] for k in keys if row.get(k)), "")


def read_scheduler(path: Path) -> list[dict]:
    """Rows: job, predecessor(s), schedule. Predecessor cells may hold several jobs."""
    out = []
    for n, row in _rows(path):
        job = _first(row, "job", "jobname", "job_name").upper()
        if not job:
            continue
        preds = re.split(r"[,;\s]+", _first(row, "predecessor", "predecessors", "pred",
                                              "depends_on", "in_condition").upper())
        out.append({"job": job, "preds": [p for p in preds if p], "line": n,
                    "schedule": _first(row, "schedule", "calendar", "time"),
                    "description": _first(row, "description", "desc")})
    return out


def read_dbql(path: Path) -> list[dict]:
    """Collapse DBQL SQL text rows into unique (job, sql) pairs with execution counts.

    Accepts either one row per query (``sql_text``) or the native DBQLSqlTbl layout
    (``queryid`` + ``sqlrowno`` + ``sqltextinfo``) which is reassembled here.
    """
    queries: "OrderedDict[str, dict]" = OrderedDict()
    for n, row in _rows(path):
        qid = _first(row, "queryid") or f"row{n}"
        text = _first(row, "sql_text", "sqltextinfo", "querytext", "sql")
        q = queries.setdefault(qid, {"parts": [], "line": n, "row": row})
        q["parts"].append((int(row.get("sqlrowno") or 0), text))
    agg: "OrderedDict[tuple, dict]" = OrderedDict()
    for q in queries.values():
        row = q["row"]
        sql = " ".join(t for _, t in sorted(q["parts"], key=lambda p: p[0]))
        job = _first(row, "job_name", "jobname")
        if not job:
            m = re.search(r"(?i)\bJOB(?:NAME)?\s*=\s*([^;'\s]+)", row.get("queryband", ""))
            job = m.group(1) if m else ""
        job = (job or f"USER:{_first(row, 'username', 'user') or 'UNKNOWN'}").upper()
        key = (job, re.sub(r"\s+", " ", sql).strip())
        entry = agg.setdefault(key, {"job": job, "sql": sql, "line": q["line"], "count": 0,
                                     "user": _first(row, "username", "user")})
        entry["count"] += 1
    return list(agg.values())


def read_dbc_tables(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for _, row in _rows(path):
        db = _first(row, "databasename", "database")
        tb = _first(row, "tablename", "table")
        if not db or not tb:
            continue
        out[f"{db}.{tb}".upper()] = {
            "tableKind": _first(row, "tablekind"),
            "owner": _first(row, "creatorname", "creator"),
            "created": _first(row, "createtimestamp"),
            "lastAltered": _first(row, "lastaltertimestamp"),
            "comment": _first(row, "commentstring", "comment"),
        }
    return out


def read_hive_metastore(path: Path) -> dict[str, dict]:
    """Hive metastore export: db_name,tbl_name,tbl_type,location,owner,comment,view_text,..."""
    out: dict[str, dict] = {}
    for n, row in _rows(path):
        db = _first(row, "db_name", "database", "databasename")
        tb = _first(row, "tbl_name", "table", "tablename")
        if not db or not tb:
            continue
        out[f"{db}.{tb}".lower()] = {
            "tableKind": _first(row, "tbl_type", "type", "tablekind"),
            "location": _first(row, "location", "sd_location"),
            "owner": _first(row, "owner"),
            "comment": _first(row, "comment"),
            "partitionedBy": _first(row, "partitioned_by", "partition_keys"),
            "lastAltered": _first(row, "last_ddl_time", "lastaltertimestamp"),
            "viewText": _first(row, "view_text", "view_expanded_text", "view_original_text"),
            "line": n,
        }
    return out
