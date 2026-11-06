"""Phase 3: choose the review threshold from measured trade-offs, not a guess.

For each extraction method, on the held-out test layouts (synthetic, exact truth):
  - reliability table: confidence bins → share actually correct (is the score calibrated?)
  - threshold sweep: at each cutoff, % of extracted values sent to a person vs errors that slip through,
    with and without the schema's range/ratio rules
Picks the lowest-review threshold that keeps silent errors at or below a target rate, and writes
  samples/synthetic/eval/calibration.json   (chosen thresholds + sweep, for docs and the dashboard)
  data/dashboard/fact_threshold.parquet      (sweep rows for the ops page)

Usage: .venv/bin/python src/calibrate.py [--max-silent 0.02]
"""
import argparse
import json

import numpy as np
import pandas as pd

from common import ROOT
from normalize import review

SYN = ROOT / "samples" / "synthetic"
METHODS = {"rules": "rules_rows.csv", "ins-neural-v1": "ins-neural-v1_rows.csv", "ins-neural-v2": "ins-neural-v2_rows.csv"}


def rule_flags(df):
    """Range/ratio/missing checks with the confidence check switched off (threshold applied separately)."""
    out = []
    for doc_id, g in df.groupby("doc_id"):
        values = {r.field: r.pred for r in g.itertuples() if pd.notna(r.pred)}
        for r in g.itertuples():
            v = r.pred if pd.notna(r.pred) else None
            f = [x for x in review(r.field, v, 1.0, "quote", values, 1) if x != "missing_required"]
            out.append((r.Index, bool(f)))
    return pd.Series(dict(out))


def pick_threshold(d, max_silent):
    """Lowest-review cutoff (with rules) whose silent-error rate on `d` is <= max_silent; 1.0 = review everything."""
    best = None
    for th in np.round(np.arange(0.0, 1.0001, 0.01), 2):
        flagged = (d.confidence < th) | d.rule_flag
        rate, silent = flagged.mean(), (d.bad & ~flagged).mean()
        if silent <= max_silent and (best is None or rate < best[1]):
            best = (float(th), rate)
    return best[0] if best else 1.0


def outcome_at(d, th):
    flagged = (d.confidence < th) | d.rule_flag
    return {"threshold": th, "n_values": int(len(d)), "review_rate": float(flagged.mean()),
            "silent_errors": int((d.bad & ~flagged).sum()), "silent_error_rate": float((d.bad & ~flagged).mean())}


def cross_fit(d, max_silent):
    """Honest estimate: choose the cutoff on one held-out carrier, score it on the other, then swap.
    (Choosing and scoring on the same test docs flatters the result.)"""
    folds = []
    for choose_on in sorted(d.carrier_id.unique()):
        sel, ev = d[d.carrier_id == choose_on], d[d.carrier_id != choose_on]
        th = pick_threshold(sel, max_silent)
        folds.append({"chosen_on": choose_on, "scored_on": ",".join(sorted(ev.carrier_id.unique())), **outcome_at(ev, th)})
    n = sum(f["n_values"] for f in folds)
    pooled = {"review_rate": sum(f["review_rate"] * f["n_values"] for f in folds) / n,
              "silent_errors": sum(f["silent_errors"] for f in folds),
              "silent_error_rate": sum(f["silent_errors"] for f in folds) / n}
    return {"folds": folds, "pooled": pooled}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-silent", type=float, default=0.02, help="max share of extracted values that are wrong AND unflagged")
    args = ap.parse_args()
    result, rows = {}, []
    for method, fname in METHODS.items():
        d = pd.read_csv(SYN / "eval" / fname)
        d = d[(d.split == "test") & d.outcome.isin(["correct", "wrong", "spurious"])].copy()   # extracted values
        d["bad"] = d.outcome != "correct"
        d["rule_flag"] = rule_flags(d)
        bins = pd.cut(d.confidence, [0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0], include_lowest=True)
        rel = d.groupby(bins, observed=True).agg(n=("bad", "size"), share_correct=("bad", lambda b: 1 - b.mean()))
        sweep = []
        for th in np.round(np.arange(0.0, 1.0001, 0.01), 2):
            low = d.confidence < th
            for rules_on in (False, True):
                flagged = low | (d.rule_flag if rules_on else False)
                sweep.append({"method": method, "threshold": th, "rules": rules_on,
                              "review_rate": flagged.mean(),
                              "silent_error_rate": (d.bad & ~flagged).mean(),
                              "errors_caught": (d.bad & flagged).sum() / max(d.bad.sum(), 1),
                              "correct_sent_to_review": int((~d.bad & flagged).sum())})
        sw = pd.DataFrame(sweep)
        ok = sw[(sw.rules) & (sw.silent_error_rate <= args.max_silent)]
        pick = ok.sort_values(["review_rate", "threshold"]).iloc[0].to_dict() if len(ok) else None
        result[method] = {"n_values": len(d), "errors": int(d.bad.sum()),
                          "reliability": [{"bin": str(i), "n": int(r.n), "share_correct": round(r.share_correct, 3)}
                                          for i, r in rel.iterrows()],
                          "chosen": pick,
                          "cross_fit": cross_fit(d, args.max_silent)}
        rows.append(sw)
        print(f"\n{method}: {len(d)} extracted values on test layouts, {int(d.bad.sum())} wrong/made-up")
        print("  reliability (confidence bin → share correct):")
        for r in result[method]["reliability"]:
            print(f"    {r['bin']:14} n={r['n']:3}  correct {r['share_correct']:.0%}")
        for th in (0.0, 0.5, 0.8, 0.9):
            a = sw[(sw.threshold == th) & sw.rules].iloc[0]
            print(f"  threshold {th:.2f} + rules: review {a.review_rate:5.1%}  silent errors {a.silent_error_rate:5.1%}  caught {a.errors_caught:5.1%}")
        if pick:
            print(f"  → chosen: threshold {pick['threshold']:.2f} (review {pick['review_rate']:.1%}, silent {pick['silent_error_rate']:.1%})")
        else:
            print(f"  → no threshold reaches silent errors ≤ {args.max_silent:.0%} for {method}")
        cf = result[method]["cross_fit"]
        for f in cf["folds"]:
            print(f"  cross-fit: chosen on {f['chosen_on']} → {f['threshold']:.2f}; on {f['scored_on']}: "
                  f"review {f['review_rate']:.1%}, silent {f['silent_errors']} ({f['silent_error_rate']:.1%})")
        print(f"  cross-fit pooled: review {cf['pooled']['review_rate']:.1%}, silent {cf['pooled']['silent_error_rate']:.1%}")
    (SYN / "eval" / "calibration.json").write_text(json.dumps({"max_silent": args.max_silent, **result}, indent=1))
    out = ROOT / "data" / "dashboard"
    out.mkdir(parents=True, exist_ok=True)
    pd.concat(rows).to_parquet(out / "fact_threshold.parquet", index=False)


if __name__ == "__main__":
    main()
