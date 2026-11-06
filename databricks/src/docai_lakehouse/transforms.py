"""Medallion transforms as pure DataFrame functions (unit-tested on local Spark).

bronze  raw landing records + ingest metadata, nothing dropped
silver  typed, deduplicated (latest run per method/doc/field), validated; failures go to quarantine
gold    evaluation outcomes, method metrics, threshold curve, review queue
"""
from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql.window import Window

OUTCOMES = ("correct", "wrong", "missed", "spurious")


# ---------- bronze ----------
def with_ingest_metadata(df: DataFrame, source: str) -> DataFrame:
    return df.withColumn("_source_file", F.lit(source)).withColumn("_ingested_at", F.current_timestamp())


# ---------- silver ----------
def silver_predictions(bronze: DataFrame, known_fields: list[str]) -> tuple[DataFrame, DataFrame]:
    """Type + validate predictions. Returns (valid, quarantine)."""
    typed = (bronze
             .withColumn("value", F.col("value_text").cast("double"))
             .withColumn("confidence", F.col("confidence").cast("double"))
             .withColumn("rule_flag", F.col("rule_flag").cast("boolean")))
    checks = {   # DQ validity/completeness rules; a row failing any goes to quarantine with the reasons
        "missing_key": F.col("method").isNull() | F.col("doc_id").isNull() | F.col("field").isNull(),
        "unknown_field": ~F.col("field").isin(known_fields),
        "value_not_numeric": F.col("value").isNull(),
        "negative_value": F.col("value") < 0,
        "confidence_out_of_range": F.col("confidence").isNotNull() & ((F.col("confidence") < 0) | (F.col("confidence") > 1)),
    }
    reasons = F.array_compact(F.array(*[F.when(cond, F.lit(name)) for name, cond in checks.items()]))
    flagged = typed.withColumn("dq_reasons", reasons)
    quarantine = flagged.where(F.size("dq_reasons") > 0)
    valid = flagged.where(F.size("dq_reasons") == 0).drop("dq_reasons")
    latest = Window.partitionBy("method", "doc_id", "field").orderBy(F.col("run_id").desc(), F.col("_ingested_at").desc())
    valid = valid.withColumn("_rn", F.row_number().over(latest)).where("_rn = 1").drop("_rn")
    return valid, quarantine


def silver_truth(bronze: DataFrame) -> DataFrame:
    return (bronze.withColumn("truth", F.col("truth").cast("double"))
            .dropDuplicates(["doc_id", "field"]))


# ---------- gold ----------
def _same(value: Column, truth: Column, ftype: Column) -> Column:
    """Money within $0.51 (cents formatting), everything else exact."""
    return F.when(ftype == "money", F.abs(value - truth) <= 0.51).otherwise(value == truth)


def gold_eval(pred: DataFrame, truth: DataFrame, docs: DataFrame, schema: DataFrame) -> DataFrame:
    """One row per (method, doc, field) with an outcome. A method is only scored on docs it predicted on."""
    scored_docs = pred.select("method", "model_id", "doc_id").distinct()
    grid = scored_docs.join(truth, "doc_id")                                   # every truth cell per scored doc
    j = (grid.join(pred.select("method", "doc_id", "field", "value", "confidence", "rule_flag"),
                   ["method", "doc_id", "field"], "full_outer")
         .join(schema.select("field", F.col("type").alias("ftype")), "field", "left")
         .join(docs.select("doc_id", "carrier_id", "layout", "split", "scanned", "scan_mode"), "doc_id", "left"))
    outcome = (F.when(F.col("truth").isNull() & F.col("value").isNull(), F.lit("true_null"))
               .when(F.col("truth").isNull(), F.lit("spurious"))
               .when(F.col("value").isNull(), F.lit("missed"))
               .when(_same(F.col("value"), F.col("truth"), F.col("ftype")), F.lit("correct"))
               .otherwise(F.lit("wrong")))
    return (j.withColumn("outcome", outcome).where("outcome != 'true_null'")
            .withColumn("is_error", F.col("outcome").isin("wrong", "spurious", "missed"))
            .withColumn("rule_flag", F.coalesce("rule_flag", F.lit(False))))


def gold_method_metrics(ev: DataFrame, threshold: float) -> DataFrame:
    """Recall, precision, and review-policy metrics per method × split at a confidence threshold."""
    flagged = F.col("rule_flag") | (F.col("confidence") < threshold)
    extracted = F.col("outcome").isin("correct", "wrong", "spurious")
    c = lambda cond: F.sum(F.when(cond, 1).otherwise(0))
    return (ev.groupBy("method", "split")
            .agg(c(F.col("outcome") == "correct").alias("correct"),
                 c(F.col("outcome").isin("correct", "wrong", "missed")).alias("printed"),
                 c(extracted).alias("extracted"),
                 c(F.col("outcome") == "wrong").alias("wrong"),
                 c(F.col("outcome") == "spurious").alias("spurious"),
                 c(extracted & flagged).alias("flagged"),
                 c(F.col("outcome").isin("wrong", "spurious") & ~flagged).alias("silent_errors"))
            .withColumn("recall", F.col("correct") / F.col("printed"))
            .withColumn("precision", F.col("correct") / F.col("extracted"))
            .withColumn("review_rate", F.col("flagged") / F.col("extracted"))
            .withColumn("threshold", F.lit(threshold)))


def gold_threshold_curve(ev: DataFrame, step: float = 0.05) -> DataFrame:
    """Review workload vs silent errors for every threshold (extracted values only)."""
    n = int(round(1 / step))
    ths = ev.sparkSession.range(0, n + 1).select((F.col("id") * step).cast("double").alias("threshold"))
    x = ev.where(F.col("outcome").isin("correct", "wrong", "spurious")).crossJoin(ths)
    flagged = F.col("rule_flag") | (F.col("confidence") < F.col("threshold"))
    return (x.groupBy("method", "split", "threshold")
            .agg(F.avg(flagged.cast("int")).alias("review_rate"),
                 F.avg((F.col("outcome").isin("wrong", "spurious") & ~flagged).cast("int")).alias("silent_error_rate"),
                 F.count("*").alias("values")))


def gold_review_queue(ev: DataFrame, threshold: float) -> DataFrame:
    """What a reviewer would see: extracted values that the policy sends to a person, with the reason."""
    reason = (F.when(F.col("rule_flag"), F.lit("validity rule"))
              .when(F.col("confidence") < threshold, F.lit("low confidence")))
    return (ev.where(F.col("outcome").isin("correct", "wrong", "spurious"))
            .withColumn("reason", reason).where(F.col("reason").isNotNull())
            .select("method", "doc_id", "layout", "field", "value", "confidence", "reason", "outcome"))
