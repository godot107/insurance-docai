"""Score extraction on the synthetic set against exact ground truth.

For every (doc, field): truth value (or null = not printed) vs predicted value + confidence.
  correct      prediction equals truth (money within $0.51, percent/int exact)
  wrong        prediction present but different (the dangerous case: confident-looking bad data)
  missed       truth present, nothing predicted
  spurious     nothing printed, but something predicted
Reports per split (train carriers vs held-out test layouts), per field, scanned vs clean, and writes
samples/synthetic/eval/<method>_rows.csv for calibration work (phase 3).

Usage: .venv/bin/python src/eval_extraction.py [--method rules]
"""
import argparse
import csv
import json
from collections import defaultdict

from common import ROOT
from normalize import FIELDS, candidates, parse

SYN = ROOT / "samples" / "synthetic"


def same(field, pred, truth):
    if FIELDS[field]["type"] == "money":
        return abs(float(pred) - float(truth)) <= 0.51
    return float(pred) == float(truth)


def rules_predict(doc):
    best = {}
    for f, v, c, m, lab in candidates(doc):
        if f not in best or (c or 0) > (best[f][1] or 0):
            best[f] = (v, c)
    return best


def custom_predict(result):
    """Fields from a custom DI model: the model already says which value belongs to which field."""
    best = {}
    for name, f in ((result.get("documents") or [{}])[0].get("fields") or {}).items():
        if name in FIELDS and f.get("content"):
            v, _ = parse(FIELDS[name]["type"], f["content"])
            if v is not None:
                best[name] = (v, f.get("confidence"))
    return best


def score(rows, key=lambda r: "all"):
    agg = defaultdict(lambda: defaultdict(int))
    for r in rows:
        agg[key(r)][r["outcome"]] += 1
    out = {}
    for k, c in agg.items():
        present = c["correct"] + c["wrong"] + c["missed"]
        predicted = c["correct"] + c["wrong"] + c["spurious"]
        out[k] = {"recall": c["correct"] / present if present else None,
                  "precision": c["correct"] / predicted if predicted else None,
                  "wrong": c["wrong"], "missed": c["missed"], "spurious": c["spurious"], "n_truth": present}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="rules", help="rules, or a custom model id (e.g. ins-neural-v1)")
    args = ap.parse_args()
    index = {r["doc_id"]: r for r in csv.DictReader(open(SYN / "index.csv"))}
    rows = []
    for doc_id, meta in index.items():
        path = SYN / "di" / ("layout" if args.method == "rules" else args.method) / f"{doc_id}.json"
        if not path.exists():
            continue
        res = json.load(open(path))
        pred = rules_predict(res) if args.method == "rules" else custom_predict(res)
        truth = json.load(open(SYN / "labels" / f"{doc_id}.json"))["fields"]
        for field, t in truth.items():
            p, conf = pred.get(field, (None, None))
            if t is None:
                outcome = "spurious" if p is not None else "true_null"
            elif p is None:
                outcome = "missed"
            else:
                outcome = "correct" if same(field, p, t) else "wrong"
            rows.append({"doc_id": doc_id, "carrier_id": meta["carrier_id"], "layout": meta["layout"],
                         "split": meta["split"], "scanned": meta["scanned"], "field": field,
                         "truth": t, "pred": p, "confidence": conf, "outcome": outcome})
    (SYN / "eval").mkdir(exist_ok=True)
    with open(SYN / "eval" / f"{args.method}_rows.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    def show(title, res):
        print(f"\n{title}")
        for k, v in sorted(res.items()):
            fmt = lambda x: "  -  " if x is None else f"{x:5.1%}"
            print(f"  {k:30} recall {fmt(v['recall'])}  precision {fmt(v['precision'])}  "
                  f"wrong {v['wrong']:3}  missed {v['missed']:3}  spurious {v['spurious']:3}  (n={v['n_truth']})")

    docs = len({r['doc_id'] for r in rows})
    print(f"method={args.method}  docs={docs}  field-rows={len(rows)}")
    show("by split (test = layouts never seen when the rules/synonyms were written)", score(rows, lambda r: r["split"]))
    show("by scan", score(rows, lambda r: "scanned" if r["scanned"] == "True" else "clean"))
    show("by layout", score(rows, lambda r: r["layout"]))
    show("by field", score(rows, lambda r: r["field"]))
    summary = {"overall": score(rows)["all"], "by_split": score(rows, lambda r: r["split"])}
    (SYN / "eval" / f"{args.method}_summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
