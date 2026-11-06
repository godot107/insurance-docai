"""Export the synthetic benchmark as raw landing files for the Databricks lakehouse (bronze input).

Writes data/landing/ (JSON Lines, one record per line; synthetic only, safe to share):
  predictions.jsonl  method, model_id, run_id, doc_id, field, value_text, confidence, rule_flag
                     (rule_flag = schema range/ratio checks, computed here with normalize.review)
  truth.jsonl        doc_id, field, truth (null = not printed)
  documents.jsonl    doc_id, carrier_id, layout, split, scanned, scan_mode, augmented
  schema.jsonl       field, type, group (from schema/fields.yaml)
Custom-model predictions are exported for held-out TEST docs only (never their own training docs).

Usage: .venv/bin/python src/export_landing.py [--methods rules,ins-neural-v1,ins-neural-v2]
"""
import argparse
import csv
import json
from datetime import datetime, timezone

from common import ROOT
from eval_extraction import custom_predict, rules_predict
from normalize import FIELDS, review

SYN = ROOT / "samples" / "synthetic"
OUT = ROOT / "data" / "landing"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="rules,ins-neural-v1")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    docs = {r["doc_id"]: r for f in ("index.csv", "index_aug.csv") if (SYN / f).exists()
            for r in csv.DictReader(open(SYN / f))}
    base = {d: r for d, r in docs.items() if d.startswith("S")}   # benchmark docs (augmented are training-only)

    with open(OUT / "documents.jsonl", "w") as fh:
        for d, r in docs.items():
            fh.write(json.dumps({"doc_id": d, "carrier_id": r["carrier_id"], "layout": r["layout"],
                                 "split": r["split"], "scanned": r["scanned"] == "True",
                                 "scan_mode": r.get("scan_mode") or ("scan" if r["scanned"] == "True" else "clean"),
                                 "augmented": d.startswith("A")}) + "\n")
    with open(OUT / "truth.jsonl", "w") as fh:
        for d in base:
            for f, t in json.load(open(SYN / "labels" / f"{d}.json"))["fields"].items():
                fh.write(json.dumps({"doc_id": d, "field": f, "truth": t}) + "\n")
    n = 0
    with open(OUT / "predictions.jsonl", "w") as fh:
        for method in args.methods.split(","):
            sub = "layout" if method == "rules" else method
            for d, r in base.items():
                if method != "rules" and r["split"] != "test":
                    continue
                p = SYN / "di" / sub / f"{d}.json"
                if not p.exists():
                    continue
                res = json.load(open(p))
                pred = rules_predict(res) if method == "rules" else custom_predict(res)
                values = {f: v for f, (v, _) in pred.items()}
                for f, (v, c) in pred.items():
                    flags = [x for x in review(f, v, 1.0, "quote", values, 1) if x != "missing_required"]
                    fh.write(json.dumps({"method": method, "model_id": "prebuilt-layout+rules" if method == "rules" else method,
                                         "run_id": run_id, "doc_id": d, "field": f, "value_text": str(v),
                                         "confidence": c, "rule_flag": bool(flags)}) + "\n")
                    n += 1
    with open(OUT / "schema.jsonl", "w") as fh:
        for f, s in FIELDS.items():
            fh.write(json.dumps({"field": f, "type": s["type"]}) + "\n")
    print(f"landing: {len(docs)} documents, {len(base)} benchmark docs with truth, {n} predictions "
          f"({args.methods}) -> data/landing/ (run {run_id})")


if __name__ == "__main__":
    main()
