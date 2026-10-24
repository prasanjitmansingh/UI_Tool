# Teradata lineage

Shows table-level lineage for Teradata tables loaded by mainframe jobs: mainframe dataset → job → staging table → DW table → view / extract file.

```
./run.sh            # http://localhost:8000  (sample data)
LINEAGE_DATA_DIR=/path/to/your/exports ./run.sh
# dev: backend `uvicorn lineage.api:app --reload` in backend/, UI `npm run dev` in frontend/ (port 5173)
# tests: cd backend && python -m pytest
```

## Inputs (`LINEAGE_DATA_DIR`, every folder optional — see `sample_data/`)

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

## UI

Search → pick an object → upstream / downstream / both with depth. *Data flow* shows tables, views and datasets with jobs on the edges; *Jobs* shows dataset/table → job → table plus scheduler order. Double-click a node to re-center; `‹ ›` on a node expands further. The URL hash holds the view, so links are shareable.

## Known limits (next steps)

* Table-level only; no column mapping (a real SQL grammar such as sqlglot's Teradata dialect would be the way to add it).
* Stored procedures, macros (`CALL`/`EXEC`), dynamic SQL, and `RENAME TABLE` swaps are not followed.
* Job variables that only exist at runtime (TPT `@Var`, shell-generated SQL) are reported as warnings, not resolved.
* DBQL/DBC are read from CSV exports; a live `teradatasql` reader would replace `sources.py`'s CSV readers.
* No auth; the API serves whatever is under `LINEAGE_DATA_DIR` (source excerpts are confined to it).
