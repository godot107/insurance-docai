"""Job entry points (python_wheel_task). Each task reads the previous layer and overwrites its own tables,
so reruns are idempotent. Tables live in Unity Catalog: <catalog>.bronze|silver|gold.<table>.

Args (all tasks): --catalog <name> [--threshold 0.78]
"""
import argparse
import json

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from docai_lakehouse import transforms as T

LANDING_FILES = {"predictions": "predictions.jsonl", "truth": "truth.jsonl",
                 "documents": "documents.jsonl", "schema": "schema.jsonl"}


def _args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--threshold", type=float, default=0.78)
    return ap.parse_args()


def _save(df, name, comment):
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(name)
    df.sparkSession.sql(f"COMMENT ON TABLE {name} IS '{comment}'")
    print(f"{name}: {df.sparkSession.table(name).count()} rows")


def bronze():
    a, spark = _args(), SparkSession.builder.getOrCreate()
    volume = f"/Volumes/{a.catalog}/bronze/landing"
    for table, file in LANDING_FILES.items():
        df = T.with_ingest_metadata(spark.read.json(f"{volume}/{file}"), file)
        _save(df, f"{a.catalog}.bronze.{table}", f"Raw landing records from {file}, as delivered")


def silver():
    a, spark = _args(), SparkSession.builder.getOrCreate()
    b = lambda t: spark.table(f"{a.catalog}.bronze.{t}")
    known = [r.field for r in b("schema").select("field").collect()]
    valid, quarantine = T.silver_predictions(b("predictions"), known)
    _save(valid, f"{a.catalog}.silver.predictions", "Typed, validated, latest run per method/doc/field")
    _save(quarantine, f"{a.catalog}.silver.predictions_quarantine", "Prediction rows that failed DQ checks, with reasons")
    _save(T.silver_truth(b("truth")), f"{a.catalog}.silver.truth", "Ground truth per doc/field (null = not printed)")
    _save(b("documents").drop("_source_file", "_ingested_at"), f"{a.catalog}.silver.documents", "Document metadata")
    _save(b("schema").drop("_source_file", "_ingested_at"), f"{a.catalog}.silver.field_schema", "Canonical field types")
    bad = quarantine.count()
    total = bad + valid.count()
    if total and bad / total > 0.05:   # quality gate: don't publish gold from bad input
        raise SystemExit(f"silver quality gate failed: {bad}/{total} prediction rows quarantined")


def gold():
    a, spark = _args(), SparkSession.builder.getOrCreate()
    s = lambda t: spark.table(f"{a.catalog}.silver.{t}")
    ev = T.gold_eval(s("predictions"), s("truth"), s("documents"), s("field_schema"))
    _save(ev, f"{a.catalog}.gold.extraction_eval", "One row per method/doc/field with outcome vs ground truth")
    _save(T.gold_method_metrics(ev, a.threshold), f"{a.catalog}.gold.method_metrics",
          f"Recall/precision/review metrics per method and split at threshold {a.threshold}")
    _save(T.gold_threshold_curve(ev), f"{a.catalog}.gold.threshold_curve", "Review rate vs silent errors per threshold")
    _save(T.gold_review_queue(ev, a.threshold), f"{a.catalog}.gold.review_queue", "Values the review policy sends to a person")


def train_router():
    import mlflow
    from mlflow.models import infer_signature

    from docai_lakehouse import router as R

    a, spark = _args(), SparkSession.builder.getOrCreate()
    ev = spark.table(f"{a.catalog}.gold.extraction_eval").toPandas()
    x = R.features(ev)
    mlflow.set_registry_uri("databricks-uc")
    user = spark.sql("SELECT current_user()").first()[0]
    mlflow.set_experiment(f"/Users/{user}/insurance-docai-review-router")
    with mlflow.start_run(run_name="review-router") as run:
        cv = R.cross_validate(x, a.threshold)
        mlflow.log_params({"threshold": a.threshold, "validation": "leave-one-carrier-out",
                           "features": ",".join(R.FEATURES_CAT + R.FEATURES_NUM), "methods": ",".join(sorted(x.method.unique()))})
        mlflow.log_metrics({k: v for k, v in cv.items() if isinstance(v, (int, float))})
        model = R.fit_final(x)
        cols = R.FEATURES_CAT + R.FEATURES_NUM
        mlflow.sklearn.log_model(model, "model", signature=infer_signature(x[cols], model.predict_proba(x[cols])[:, 1]),
                                 registered_model_name=f"{a.catalog}.gold.review_router")
        print(json.dumps(cv, indent=1))
        spark.createDataFrame([{"run_id": run.info.run_id, **{k: float(v) for k, v in cv.items()}}]) \
            .withColumn("trained_at", F.current_timestamp()) \
            .write.mode("append").option("mergeSchema", "true").saveAsTable(f"{a.catalog}.gold.router_runs")
