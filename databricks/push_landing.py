"""Upload ../data/landing/*.jsonl to /Volumes/<catalog>/bronze/landing (Databricks Files API).

Auth: Databricks unified auth (DATABRICKS_HOST + `databricks auth login`, or a profile in ~/.databrickscfg).
Usage: python push_landing.py --catalog workspace [--profile free]
"""
import argparse
from pathlib import Path

from databricks.sdk import WorkspaceClient

LANDING = Path(__file__).resolve().parents[1] / "data" / "landing"

ap = argparse.ArgumentParser()
ap.add_argument("--catalog", required=True)
ap.add_argument("--profile")
a = ap.parse_args()
w = WorkspaceClient(profile=a.profile) if a.profile else WorkspaceClient()
for f in sorted(LANDING.glob("*.jsonl")):
    with open(f, "rb") as fh:
        w.files.upload(f"/Volumes/{a.catalog}/bronze/landing/{f.name}", fh, overwrite=True)
    print(f"uploaded {f.name} ({f.stat().st_size:,} bytes)")
