# Data lineage — Mainframe/Teradata and Ab Initio/Hadoop

Table-level lineage for two independent platforms, chosen with the switch at the top of the page:

* **Mainframe** — Teradata tables loaded by JCL jobs: MF dataset → job → staging table → DW table → view / extract file.
* **Ab Initio** — Hive tables loaded by Ab Initio graphs: HDFS path → graph → Hive table → view.

Each platform has its own table search (browse on focus, filter by tables / jobs or graphs / datasets or paths / all), its own lineage graph and warnings. Selecting a table or job shows its **downstream impact** — every affected table, view and dataset with hop count, plus the jobs or graphs that load them (and scheduler successors for a job) — with *Copy summary* for incident notes.

```
./run.sh            # http://localhost:8000  (sample data)
LINEAGE_DATA_DIR=/path/to/exports ./run.sh   # expects mainframe/ and abinitio/ inside
# dev: backend `uvicorn lineage.api:app --reload` in backend/, UI `npm run dev` in frontend/ (port 5173)
# tests: cd backend && python -m pytest
```

## Self-contained demo page

`demo/lineage-explorer.html` is the same UI with the sample lineage embedded, so it runs with no server (it is what the shared demo link shows). Rebuild it after changing the data or the UI:

```
cd frontend && npm run build && cd ..
python scripts/build_demo.py sample_data demo/lineage-explorer.html
```

## Mainframe inputs (`$LINEAGE_DATA_DIR/mainframe/`, every folder optional — see `sample_data/mainframe/`)

| Folder | Content | What it gives you |
|---|---|---|
| `jcl/` | JCL members | jobs, steps, DD → dataset names (GDG suffix stripped, `&SYMBOLS.` and `SET` resolved), in-stream `SYSIN`, proc expansion with `STEP.DD` overrides |
| `procs/` | PROC libraries | cataloged procedure bodies |
| `scripts/` | BTEQ / FastLoad / MultiLoad / TPT / SQL, file name = PDS member | resolved from `DSN=LIB(MEMBER)` DDs |
| `scheduler/*.csv` | `job,predecessor,schedule,description` (CA-7 / Control-M / TWS export) | job ordering (RUNS_AFTER) |
| `dbql/*.csv` | `job_name` or `queryband` (`JOB=x;`) + `sql_text` (or DBQLSqlTbl `queryid,sqlrowno,sqltextinfo`) | edges *observed* at runtime; finds jobs that JCL doesn't explain (ad hoc) |
| `dbc/*.csv` | DBC.TablesV export | table/view kind, owner, comment, last altered; warns about unknown tables |
| `config.json` | `default_database`, `substitutions` | resolves unqualified names and `${ENV}` style variables |

What is extracted: `INSERT/INS … SELECT`, `UPDATE … FROM`, `MERGE … USING`, `DELETE`, `CREATE TABLE … AS`, `CREATE/REPLACE VIEW`, volatile tables (scoped per job), CTEs, comma/ANSI joins, BTEQ `.IMPORT`/`.EXPORT`/`.RUN`, FastLoad `DEFINE FILE`/`BEGIN LOADING`, MultiLoad `.DML LABEL`/`.IMPORT … APPLY`, TPT operators + `APPLY … TO OPERATOR … FROM OPERATOR`.

Every edge keeps its evidence (job, step, file:line, origin `jcl | dbql | scheduler`); click an edge in the UI to see it with the source excerpt. Edges linked only by script position (e.g. `.IMPORT` followed by `INSERT … VALUES`) are marked *inferred*.

## Ab Initio inputs (`$LINEAGE_DATA_DIR/abinitio/` — see `sample_data/abinitio/`)

| Folder | Content | What it gives you |
|---|---|---|
| `graphs/*.json` | one export per graph (format below) | sources → sinks per graph, walking component flows; lookups count as sources |
| `scheduler/*.csv` | `job,predecessor,schedule,description` (Control-M / Autosys export; job = graph name) | graph ordering |
| `metastore/*.csv` | Hive metastore export `db_name,tbl_name,tbl_type,location,owner,comment,partitioned_by,last_ddl_time,view_text` | owner/comment/location; **HDFS paths that are a table's location become that table**; view lineage from `view_text` |
| `config.json` | `default_database`, `substitutions` (`$ENV`), `runtime_parameters` (e.g. `RUN_DATE`, left unresolved without a warning) | parameter resolution |

```json
{
  "graph": "txn_enrich", "project": "/Projects/payments", "parameters": {"ENV": "prod"},
  "components": [
    {"id": "in_raw",  "type": "Input Table",  "sql": "SELECT * FROM raw.txn"},
    {"id": "in_cust", "type": "Input Table",  "table": "dw.customer_dim"},
    {"id": "join",    "type": "Join"},
    {"id": "fx",      "type": "Reformat",     "lookups": ["/data/$ENV/ref/fx_rates"]},
    {"id": "out",     "type": "Output Table", "table": "dw.txn_enriched"}
  ],
  "flows": [["in_raw", "join"], ["in_cust", "join"], ["join", "fx"], {"from": "fx", "to": "out"}]
}
```

Component roles come from the type name: `Input …` / `Read …` are sources, `Output …` / `Write …` are sinks, everything else is a transform. Endpoints are `table` (Hive), `path`/`file`/`dataset` (HDFS; scheme, partition dirs and globs are stripped) or `sql` (Hive SQL; `INSERT OVERWRITE TABLE … SELECT` in a *Run SQL* component gives its own lineage). This JSON is the interchange format — generate it from your EME / `air` exports or `.mp` files with a small converter.

## UI

Search → pick an object → upstream / downstream / both with depth. *Data flow* shows tables, views and datasets with jobs on the edges; *Jobs* shows dataset/table → job → table plus scheduler order. Double-click a node to re-center; `‹ ›` on a node expands further. The URL hash holds the view, so links are shareable.

## Known limits (next steps)

* Table-level only; no column mapping (a real SQL grammar such as sqlglot's Teradata dialect would be the way to add it).
* Stored procedures, macros (`CALL`/`EXEC`), dynamic SQL, and `RENAME TABLE` swaps are not followed.
* Job variables that only exist at runtime (TPT `@Var`, shell-generated SQL) are reported as warnings, not resolved.
* Ab Initio: reads the JSON graph format above, not `.mp` files or the EME directly; subgraphs must be flattened into one component list.
* DBQL/DBC are read from CSV exports; a live `teradatasql` reader would replace `sources.py`'s CSV readers.
* No auth; the API serves whatever is under `LINEAGE_DATA_DIR` (source excerpts are confined to it).
