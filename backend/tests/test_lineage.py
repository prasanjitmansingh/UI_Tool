from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lineage.builder import build_graph
from lineage.jcl import parse_jcl
from lineage.scripts import Endpoint, parse_script
from lineage.sqlscan import Context, extract_sql

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data"


def flows(sql, **kw):
    out = []
    for s in extract_sql(sql, Context(**kw)):
        if s.target:
            out.append((s.kind, s.target.name, sorted(r.name for r in s.sources)))
    return out


def test_insert_select_joins_and_comma_joins():
    assert flows("INSERT INTO A.T SEL x FROM B.S1 a, B.S3 c JOIN B.S2 b ON a.i=b.i;") == [
        ("insert", "A.T", ["B.S1", "B.S2", "B.S3"])]


def test_ctes_and_extract_from_are_not_tables():
    sql = "INSERT INTO A.T WITH c AS (SEL i FROM B.S) SEL EXTRACT(YEAR FROM d), i FROM c;"
    assert flows(sql) == [("insert", "A.T", ["B.S"])]


def test_system_tables_and_strings_ignored():
    sql = "INS A.T SEL 'FROM FAKE.X' FROM DBC.TablesV, B.REAL; -- FROM NOPE.X"
    assert flows(sql) == [("insert", "A.T", ["B.REAL"])]


def test_update_from_alias_resolves_target():
    assert flows("UPDATE t FROM T.A t, S.B b SET x=b.x WHERE t.i=b.i;") == [
        ("update", "T.A", ["S.B"])]


def test_merge_using_subquery_and_table():
    assert flows("MERGE INTO D.T t USING (SEL * FROM S.U) s ON t.i=s.i WHEN MATCHED THEN UPDATE SET x=1;") == [
        ("merge", "D.T", ["S.U"])]
    assert flows("MERGE INTO D.T t USING S.V s ON t.i=s.i WHEN MATCHED THEN UPDATE SET x=1;") == [
        ("merge", "D.T", ["S.V"])]


def test_ctas_view_and_volatile_scope():
    sql = """CREATE VOLATILE TABLE vt AS (SEL * FROM S.A) WITH DATA;
             REPLACE VIEW D.V AS LOCKING ROW FOR ACCESS SEL * FROM vt;"""
    out = flows(sql, scope="J1")
    assert out == [("ctas", "VOLATILE:J1.VT", ["S.A"]), ("view", "D.V", ["VOLATILE:J1.VT"])]


def test_default_database_statement():
    assert flows("DATABASE PRD; INSERT INTO T SEL * FROM S;") == [("insert", "PRD.T", ["PRD.S"])]


def test_fastload_mload_tpt_bteq():
    fl = parse_script("DEFINE A (CHAR(1)) FILE=IN1;\nBEGIN LOADING D.T ERRORFILES D.E1,D.E2;")
    assert [(f.src, f.dst) for f in fl.flows] == [(Endpoint("file", "IN1"), Endpoint("table", "D.T"))]
    ml = parse_script(".BEGIN IMPORT MLOAD TABLES D.T WORKTABLES D.W ERRORTABLES D.E;\n"
                      ".DML LABEL L1;\nINSERT INTO D.T VALUES (:a);\n.IMPORT INFILE F1 LAYOUT X APPLY L1;")
    assert [(f.src.name, f.dst.name) for f in ml.flows] == [("F1", "D.T")]
    tpt = parse_script("""DEFINE JOB J (
      DEFINE OPERATOR R TYPE DATACONNECTOR PRODUCER ATTRIBUTES (VARCHAR FileName='DD:IN');
      DEFINE OPERATOR W TYPE LOAD ATTRIBUTES (VARCHAR TargetTable='D.T');
      APPLY ('x') TO OPERATOR (W[1]) SELECT * FROM OPERATOR (R[1]); );""")
    assert [(f.src.name, f.dst.name) for f in tpt.flows] == [("IN", "D.T")]
    bt = parse_script(".EXPORT DATA FILE=OUT\nSEL * FROM D.T;\n.EXPORT RESET\n")
    assert [(f.src.name, f.dst.name) for f in bt.flows] == [("D.T", "OUT")]


def test_jcl_steps_dds_and_inline():
    m = parse_jcl("""//J1 JOB (A)
//S1 EXEC PGM=BTEQ
//IN DD DSN=A.B.C(+1),DISP=SHR
//SYSIN DD *
SEL 1;
/*
""")
    assert m.job == "J1"
    s = m.steps[0]
    assert s.pgm == "BTEQ" and s.dds[0].dsn == "A.B.C(+1)"
    assert s.dds[1].inline.strip() == "SEL 1;" and s.dds[1].inline_line == 5


@pytest.fixture(scope="module")
def g():
    return build_graph(SAMPLE)


def flow_pairs(g):
    return {(e.source, e.target) for e in g.edges.values() if e.kind == "FLOW"}


def test_sample_end_to_end_flows(g):
    pairs = flow_pairs(g)
    assert ("ds:PROD.MF.CUSTOMER.EXTRACT", "tbl:PROD_STG.CUSTOMER_STG") in pairs  # fastload + GDG + symbolic
    assert ("tbl:PROD_STG.CUSTOMER_STG", "tbl:PROD_DW.CUSTOMER") in pairs  # inline BTEQ
    assert ("ds:PROD.MF.ORDERS.DAILY", "tbl:PROD_STG.ORDERS_STG") in pairs  # mload from PDS member
    assert ("tbl:VOLATILE:ORDLOAD.VT_ORDERS", "tbl:PROD_DW.ORDER_FACT") in pairs  # proc + override
    assert ("ds:PROD.MF.PRODUCT.MASTER", "tbl:PROD_STG.PRODUCT_STG") in pairs  # TPT via DD:
    assert ("tbl:PROD_STG.PRODUCT_STG", "tbl:PROD_DW.PRODUCT") in pairs  # MERGE
    assert ("tbl:PROD_DW.SALES_SUMMARY", "ds:PROD.MF.SALES.SUMMARY") in pairs  # .EXPORT


def test_sample_scheduler_dbql_and_dictionary(g):
    assert ("job:CUSTLOAD", "job:ORDLOAD") in {(e.source, e.target) for e in g.edges.values() if e.kind == "RUNS_AFTER"}
    assert ("tbl:PROD_DW.CUSTOMER", "tbl:PROD_DW.CUSTOMER_360") in flow_pairs(g)  # only seen in DBQL
    assert g.nodes["tbl:PROD_DW.V_SALES_BY_REGION"].type == "view"
    assert g.nodes["tbl:PROD_DW.CUSTOMER"].attrs["comment"] == "Customer dimension"
    confirmed = g.edges["FLOW|tbl:PROD_DW.CUSTOMER|tbl:PROD_DW.ORDER_FACT"]
    assert {ev["origin"] for ev in confirmed.evidence} == {"jcl", "dbql"}
    assert "tbl:PROD_DW.VT_ORDERS" not in g.nodes  # DBQL volatile resolved via JCL


def test_traversal_depth_and_direction(g):
    r = g.lineage("tbl:PROD_DW.ORDER_FACT", "up", 1)
    assert {n["id"] for n in r["nodes"]} >= {"tbl:PROD_DW.CUSTOMER", "tbl:PROD_DW.PRODUCT"}
    assert "tbl:PROD_DW.SALES_SUMMARY" not in {n["id"] for n in r["nodes"]}
    r = g.lineage("ds:PROD.MF.ORDERS.DAILY", "down", 10)
    assert "tbl:PROD_DW.SALES_SUMMARY" in {n["id"] for n in r["nodes"]}


def test_api():
    from lineage import api
    c = TestClient(api.app)
    assert c.get("/api/search", params={"q": "order_fact"}).json()[0]["id"] == "tbl:PROD_DW.ORDER_FACT"
    r = c.get("/api/graph", params={"node": "tbl:PROD_DW.CUSTOMER", "direction": "down", "depth": 2})
    assert r.status_code == 200 and r.json()["edges"]
    assert c.get("/api/graph", params={"node": "nope"}).status_code == 404
    snip = c.get("/api/source", params={"file": "scripts/ORDFACT.bteq", "line": 14}).json()
    assert any("ORDER_FACT" in l["text"] for l in snip["lines"])
    assert c.get("/api/source", params={"file": "../../etc/passwd"}).status_code == 404
