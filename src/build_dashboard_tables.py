"""Build the dashboard star schema (docs/DASHBOARD.md) as Parquet in data/dashboard/.

Everything here is synthetic, so the report can be shared publicly:
  - Ops/accuracy pages: the 96 synthetic documents as actually processed by Document Intelligence
    (timestamps = when each DI result was saved), scored against exact ground truth, with the review rules applied.
  - Household page: a simulated demo household (one house, a high current premium, quotes from the
    8 fictional carriers priced with the generator's rating formula). Real household data stays in private/.
  - Finance page: unit-cost assumptions are an explicit table (dim_assumption) so they're visible and editable.

Usage: .venv/bin/python src/build_dashboard_tables.py [--methods rules,ins-neural-v1]
"""
import argparse
import csv
import json
import random
from pathlib import Path

import pandas as pd

from common import ROOT
from eval_extraction import custom_predict, rules_predict, same
from normalize import FIELDS, review

SYN = ROOT / "samples" / "synthetic"
OUT = ROOT / "data" / "dashboard"

ASSUMPTIONS = [  # name, value, unit, note
    ("price_layout_per_page", 0.010, "USD/page", "Document Intelligence S0 prebuilt layout (~$10 per 1,000 pages); F0 = free up to 500 pages/mo"),
    ("price_custom_per_page", 0.030, "USD/page", "Document Intelligence S0 custom extraction (~$30 per 1,000 pages)"),
    ("price_llm_per_doc", 0.002, "USD/doc", "gpt-4.1-mini, ~2k tokens per document if an LLM arm is used"),
    ("manual_minutes_per_doc", 6.0, "minutes", "analyst keys ~10 fields from one letter by hand"),
    ("review_minutes_per_field", 0.5, "minutes", "confirming one flagged field in the review app"),
    ("analyst_cost_per_hour", 35.0, "USD/hour", "loaded hourly cost of an operations analyst"),
]


DEMO_HOUSEHOLD = "Demo house (simulated) · 100 Example Lane"


# Fictional households for the "Should I switch?" picker: obviously made-up addresses, each a different situation.
DEMO_HOUSEHOLDS = [
    # id, picker label, seed, dwelling (A), current premium, share of quotes on the same terms
    ("demo", DEMO_HOUSEHOLD, 11, 512000.0, 6480.00, 0.6),
    ("demo2", "Demo: already a good deal (simulated) · 42 Sample Street", 23, 386000.0, 1240.00, 0.6),
    ("demo3", "Demo: nothing comparable (simulated) · 7 Placeholder Court", 37, 455000.0, 4920.00, 0.0),
    ("demo4", "Demo: big house, big gap (simulated) · 1 Fictional Ridge", 51, 845000.0, 11800.00, 0.7),
]


def demo_households():
    return pd.concat([demo_household(seed, hid, a, prem, same) for hid, _, seed, a, prem, same in DEMO_HOUSEHOLDS],
                     ignore_index=True)


def demo_household(seed=11, hid="demo", a=512000.0, current=6480.00, p_same=0.6):
    """One simulated house + current policy + one quote per fictional carrier (same coverage, formula prices)."""
    rng = random.Random(seed)
    kits = json.load(open(SYN / "carriers.json"))
    sqft, year = int(a / 206), 2019 - seed % 17
    cov = {"coverage_a": a, "coverage_c": round(a * 0.5, -2), "coverage_e": 300000.0}
    rows = [{"household_id": hid, "doc_id": f"{hid}-D00", "carrier": "Current insurer (simulated)", "doc_type": "declarations",
             "premium": current, **cov, "wind_hail_pct": 2.0, "aop_deductible": 1000.0, "apples_to_apples": "baseline",
             "notes": "", "year_built": year, "square_feet": sqft}]
    for i, k in enumerate(kits, 1):
        rate = rng.uniform(3.2, 9.5)
        same_terms = rng.random() < p_same
        wh = 2.0 if same_terms else rng.choice([1.0, 5.0])
        prem = round(a / 1000 * rate * {1.0: 1.12, 2.0: 1.0, 5.0: 0.85}[wh], 2)
        quote_a = a if same_terms else round(a * rng.uniform(0.85, 1.05), -2)
        notes = [] if same_terms else ["dwelling differs >5%" if abs(quote_a / a - 1) > 0.05 else "", f"wind/hail {wh:g}% vs 2%"]
        rows.append({"household_id": hid, "doc_id": f"{hid}-D{i:02d}", "carrier": k["carrier_name"],
                     "doc_type": "quote", "premium": prem, "coverage_a": quote_a, "coverage_c": cov["coverage_c"],
                     "coverage_e": cov["coverage_e"], "wind_hail_pct": wh, "aop_deductible": 1000.0,
                     "apples_to_apples": "yes" if same_terms else "no", "notes": "; ".join(n for n in notes if n),
                     "year_built": year, "square_feet": sqft})
    df = pd.DataFrame(rows)
    base = df.loc[df.doc_type == "declarations", "premium"].iloc[0]
    df["premium_per_1k_a"] = (df.premium / df.coverage_a * 1000).round(2)
    df["vs_current_usd"] = (df.premium - base).where(df.doc_type != "declarations").round(2)
    df["vs_current_pct"] = (100 * (df.premium / base - 1)).where(df.doc_type != "declarations").round(1)
    df["renewal_date"] = pd.Timestamp("2026-12-27")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="rules")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    index = {r["doc_id"]: r for r in csv.DictReader(open(SYN / "index.csv"))}
    kits = {k["carrier_id"]: k for k in json.load(open(SYN / "carriers.json"))}

    docs, fields = [], []
    for method in args.methods.split(","):
        sub = "layout" if method == "rules" else method
        for doc_id, meta in index.items():
            p = SYN / "di" / sub / f"{doc_id}.json"
            if not p.exists():
                continue
            if method != "rules" and meta["split"] == "train":
                continue   # a trained model scored on its own training docs is meaningless: test layouts only
            res = json.load(open(p))
            pred = rules_predict(res) if method == "rules" else custom_predict(res)
            truth = json.load(open(SYN / "labels" / f"{doc_id}.json"))["fields"]
            values = {f: v for f, (v, c) in pred.items()}
            doc_type = "declarations" if meta["layout"] == "dec_page" else "quote"
            n_flag = 0
            for f, t in truth.items():
                v, c = pred.get(f, (None, None))
                flags = review(f, v, c, doc_type, values, 1) if (v is not None or t is not None) else []
                outcome = ("true_null" if v is None else "spurious") if t is None else \
                          ("missed" if v is None else ("correct" if same(f, v, t) else "wrong"))
                if outcome == "true_null":
                    continue
                n_flag += bool(flags)
                fields.append({"method": method, "doc_id": doc_id, "field": f, "truth": t, "pred": v,
                               "confidence": c, "outcome": outcome, "flagged": bool(flags),
                               "flag_reasons": ";".join(flags),
                               "silent_error": outcome in ("wrong", "spurious") and not flags})
            docs.append({"method": method, "doc_id": doc_id, "carrier_id": meta["carrier_id"],
                         "carrier": kits[meta["carrier_id"]]["carrier_name"], "layout": meta["layout"],
                         "split": meta["split"], "scanned": meta["scanned"] == "True",
                         "processed_at": pd.Timestamp.fromtimestamp(p.stat().st_mtime, tz="UTC"),  # when DI returned
                         "pages": len(res.get("pages", [])), "fields_flagged": n_flag,
                         "straight_through": n_flag == 0})

    fact_doc, fact_field = pd.DataFrame(docs), pd.DataFrame(fields)
    fact_field = fact_field.merge(fact_doc[["method", "doc_id", "carrier", "layout", "split", "scanned"]],
                                  on=["method", "doc_id"])
    dim_field = pd.DataFrame([{"field": f, "type": s["type"],
                               "group": "coverage" if f.startswith("coverage") else
                                        "premium" if f.startswith("premium") else
                                        "deductible" if f.startswith("deductible") else "property"}
                              for f, s in FIELDS.items()])
    dim_household = pd.DataFrame([{"household_id": h, "household": lab, "simulated": True} for h, lab, *_ in DEMO_HOUSEHOLDS])
    tables = {"fact_document": fact_doc, "fact_field": fact_field, "fact_quote": demo_households(),
              "dim_household": dim_household,
              "dim_field": dim_field,
              "dim_carrier": pd.DataFrame([{"carrier_id": c, "carrier": k["carrier_name"], "layout": k["layout"],
                                            "split": "test" if c in ("C5", "C6") else "train"} for c, k in kits.items()]),
              "dim_assumption": pd.DataFrame(ASSUMPTIONS, columns=["name", "value", "unit", "note"])}
    for name, df in tables.items():
        df.to_parquet(OUT / f"{name}.parquet", index=False)
        print(f"{name:15} {len(df):5} rows")
    s = fact_field[fact_field.method == "rules"]
    print(f"rules: accuracy {(s.outcome == 'correct').mean():.1%}, flagged {s.flagged.mean():.1%}, "
          f"silent errors {s.silent_error.sum()} of {(s.outcome.isin(['wrong', 'spurious'])).sum()} errors; "
          f"straight-through docs {fact_doc[fact_doc.method == 'rules'].straight_through.mean():.1%}")


if __name__ == "__main__":
    main()
