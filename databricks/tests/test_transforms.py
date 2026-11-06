from pathlib import Path

import pytest
from pyspark.sql import functions as F

from docai_lakehouse import transforms as T

LANDING = Path(__file__).resolve().parents[2] / "data" / "landing"
SCHEMA = [("coverage_a_dwelling", "money"), ("deductible_wind_hail_pct", "percent"), ("year_built", "int")]


def test_silver_quarantines_bad_rows_and_keeps_latest_run(spark):
    rows = [  # method, run_id, doc, field, value_text, confidence, rule_flag
        ("m", "r1", "D1", "coverage_a_dwelling", "100000", 0.9, False),
        ("m", "r2", "D1", "coverage_a_dwelling", "200000", 0.95, False),   # newer run wins
        ("m", "r2", "D1", "not_a_field", "5", 0.9, False),                 # unknown field
        ("m", "r2", "D1", "year_built", "abc", 0.9, False),                # not numeric
        ("m", "r2", "D2", "year_built", "2001", 1.7, False),               # confidence out of range
    ]
    df = spark.createDataFrame(rows, "method string, run_id string, doc_id string, field string, "
                                     "value_text string, confidence double, rule_flag boolean")
    valid, quarantine = T.silver_predictions(T.with_ingest_metadata(df, "x"), [f for f, _ in SCHEMA])
    assert [r.value for r in valid.collect()] == [200000.0]
    reasons = {r.field: r.dq_reasons for r in quarantine.collect()}
    assert reasons == {"not_a_field": ["unknown_field"], "year_built": reasons["year_built"]}
    assert sorted(sum((r.dq_reasons for r in quarantine.collect() if r.field == "year_built"), [])) == \
        ["confidence_out_of_range", "value_not_numeric"]


def test_gold_eval_outcomes(spark):
    schema = spark.createDataFrame(SCHEMA, "field string, type string")
    truth = spark.createDataFrame([("D1", "coverage_a_dwelling", 100000.0), ("D1", "deductible_wind_hail_pct", None),
                                   ("D1", "year_built", 1999.0)], "doc_id string, field string, truth double")
    pred = spark.createDataFrame([("m", "mid", "D1", "coverage_a_dwelling", 100000.4, 0.9, False),   # correct (cents)
                                  ("m", "mid", "D1", "deductible_wind_hail_pct", 2.0, 0.4, False)],  # spurious
                                 "method string, model_id string, doc_id string, field string, value double, "
                                 "confidence double, rule_flag boolean")
    docs = spark.createDataFrame([("D1", "C1", "dec_page", "test", True, "scan")],
                                 "doc_id string, carrier_id string, layout string, split string, scanned boolean, scan_mode string")
    ev = {r.field: r.outcome for r in T.gold_eval(pred, truth, docs, schema).collect()}
    assert ev == {"coverage_a_dwelling": "correct", "deductible_wind_hail_pct": "spurious", "year_built": "missed"}


@pytest.mark.skipif(not (LANDING / "predictions.jsonl").exists(), reason="run src/export_landing.py first")
def test_end_to_end_on_landing_matches_local_eval(spark):
    """The lakehouse must reproduce the numbers in docs/RESULTS.md from the raw landing files."""
    r = lambda f: T.with_ingest_metadata(spark.read.json(str(LANDING / f)), f)
    schema = spark.read.json(str(LANDING / "schema.jsonl"))
    valid, quarantine = T.silver_predictions(r("predictions.jsonl"), [x.field for x in schema.collect()])
    assert quarantine.count() == 0
    ev = T.gold_eval(valid, T.silver_truth(r("truth.jsonl")), spark.read.json(str(LANDING / "documents.jsonl")), schema)
    m = {(x.method, x.split): x for x in T.gold_method_metrics(ev, 0.78).collect()}
    assert round(m[("rules", "test")].recall, 3) == 0.775
    assert round(m[("ins-neural-v1", "test")].recall, 3) == 0.933
    assert m[("ins-neural-v1", "test")].spurious == 30
    curve = T.gold_threshold_curve(ev).where("method = 'rules' AND split = 'test'")
    assert curve.select(F.min("silent_error_rate")).first()[0] == 0.0     # at threshold 1.0 everything is reviewed
