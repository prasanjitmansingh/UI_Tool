import json
from pathlib import Path

import pytest

from lineage.abinitio import build_abinitio_graph, norm_path
from lineage.sqlscan import Context, extract_sql

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data" / "abinitio"


def flow_pairs(g):
    return {(e.source, e.target) for e in g.edges.values() if e.kind == "FLOW"}


def test_norm_path_strips_scheme_partitions_and_globs():
    assert norm_path("hdfs://nn:8020/data/x/dt=2024-01-01/") == "/data/x"
    assert norm_path("/data/x/*.dat") == "/data/x"
    assert norm_path("/data/x/") == "/data/x"


def test_hive_insert_overwrite_lower_case():
    s = extract_sql("INSERT OVERWRITE TABLE Mart.R PARTITION (dt='1') SELECT * FROM dw.A a JOIN B b ON 1=1",
                    Context(default_db="default", lower=True))[0]
    assert s.target.name == "mart.r" and [r.name for r in s.sources] == ["dw.a", "default.b"]


@pytest.fixture(scope="module")
def g():
    return build_abinitio_graph(SAMPLE)


def test_component_walk_and_lookups(g):
    pairs = flow_pairs(g)
    assert ("ds:/data/prod/landing/crm/customer", "tbl:stg.customer") in pairs  # params + scheme stripped
    assert ("tbl:ref.region", "tbl:dw.customer_dim") in pairs  # lookup on a Reformat
    assert ("tbl:raw.txn", "tbl:dw.txn_enriched") in pairs  # Input Table with SQL
    assert ("tbl:dw.customer_dim", "tbl:dw.txn_enriched") in pairs  # second input into a Join


def test_hdfs_location_maps_to_hive_table(g):
    # txn_ingest writes files; the metastore says that path is external table raw.txn
    assert ("ds:/data/prod/landing/payments/txn", "tbl:raw.txn") in flow_pairs(g)
    assert "ds:/data/prod/raw/txn" not in g.nodes


def test_run_sql_view_and_scheduler(g):
    pairs = flow_pairs(g)
    assert ("tbl:dw.txn_enriched", "tbl:mart.risk_exposure") in pairs  # backtick-quoted Hive SQL
    assert ("tbl:mart.risk_exposure", "tbl:mart.v_risk_latest") in pairs  # metastore view text
    assert g.nodes["tbl:mart.v_risk_latest"].type == "view"
    sched = {(e.source, e.target) for e in g.edges.values() if e.kind == "RUNS_AFTER"}
    assert ("job:cust_dim_build", "job:txn_enrich") in sched
    assert g.stats()["warnings"] == []


def test_impact(g):
    imp = g.impact("tbl:stg.customer")
    assert [o["name"] for o in imp["objects"]][:1] == ["dw.customer_dim"]
    assert "tbl:mart.v_risk_latest" in {o["id"] for o in imp["objects"]}
    assert {j["name"] for j in imp["jobs"]} == {"cust_dim_build", "txn_enrich", "daily_risk"}
    job = g.impact("job:txn_ingest")
    assert {j["name"] for j in job["jobs"]} >= {"txn_enrich", "daily_risk"}


def test_bad_graph_and_unknown_param_are_warnings(tmp_path):
    (tmp_path / "graphs").mkdir()
    (tmp_path / "graphs" / "broken.json").write_text("{ nope")
    (tmp_path / "graphs" / "g.json").write_text(json.dumps({
        "graph": "g", "components": [
            {"id": "i", "type": "Input File", "path": "/in/$MISSING"},
            {"id": "o", "type": "Output Table", "table": "t"}],
        "flows": [["i", "o"]]}))
    g = build_abinitio_graph(tmp_path)
    w = " ".join(g.stats()["warnings"])
    assert "broken.json" in w and "$MISSING" in w
    assert ("ds:/in/$MISSING", "tbl:default.t") in flow_pairs(g)
