"""Publish pipeline outputs to Azure as Parquet + run metadata (the "curated" layer).

  curated/household=<id>/<table>/run=<run_id>.parquet   fields, review_queue, comparison, manifest
  curated/_runs/<run_id>.json                            who/when/what: schema version, model, row counts

Parquet in Blob is what Databricks (phase 4) and Power BI read directly; Azure SQL (phase 5) gets the
serving tables. The container is private because these rows hold real-household numbers.
Usage: .venv/bin/python src/publish_curated.py [--household h001]
"""
import argparse
import getpass
import io
import json
from datetime import datetime, timezone

import pandas as pd
import yaml
from azure.storage.blob import BlobServiceClient

from common import BLOB_ENDPOINT, PRIVATE, ROOT, credential

TABLES = {"fields": PRIVATE / "out" / "fields.csv", "review_queue": PRIVATE / "out" / "review_queue.csv",
          "comparison": PRIVATE / "out" / "comparison.csv", "manifest": PRIVATE / "manifest.csv"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--household", default="h001")
    args = ap.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cont = BlobServiceClient(BLOB_ENDPOINT, credential=credential()).get_container_client("curated")
    counts = {}
    for name, path in TABLES.items():
        df = pd.read_csv(path, dtype=str)   # strings in, typed later in Databricks/SQL against the schema
        if "household_id" in df:
            df = df[df["household_id"] == args.household]
        if name == "manifest":
            df = df.drop(columns=["file"], errors="ignore")   # original file names may be personal
        df["run_id"] = run_id
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        cont.upload_blob(f"household={args.household}/{name}/run={run_id}.parquet", buf.getvalue(), overwrite=True)
        counts[name] = len(df)
    meta = {"run_id": run_id, "household_id": args.household, "operator": getpass.getuser(),
            "schema_version": yaml.safe_load(open(ROOT / "schema" / "fields.yaml"))["version"],
            "extraction_model": "prebuilt-layout", "row_counts": counts}
    cont.upload_blob(f"_runs/{run_id}.json", json.dumps(meta, indent=1), overwrite=True)
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
