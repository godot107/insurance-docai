"""Review feedback & drift table for the Power BI operations dashboard: dash.fact_review (via data/dashboard).

One row per value sent to a person, with what the person decided and how long it took. Two sources:

  app        real decisions from the Power Apps review queue (review.item + its temporal history + answer key)
  simulated  a reviewer working every value the three models flagged on the held-out test layouts, replayed as
             the project's own history: week 1 rules, week 2 custom model v1, week 3 v2 (augmented).
             Outcomes are the benchmark's real outcomes; only the timing and the reviewer's slips are simulated
             (seeded). Labelled source = simulated everywhere it appears.

What the page answers:
  - Feedback: what share of flagged values do reviewers accept, correct, or mark "not on the document"?
  - No reply: how many flagged values are still open, and for how long?
  - Drift / model health: does the correction or not-on-document rate move when the model or layout mix
    changes? (v1's made-up values show up as a spike in "not on document"; v2 brings it down.)
  - Reviewer quality: how often does the reviewer's decision match the answer key (synthetic data only)?

Usage: .venv/bin/python src/build_review_feedback.py [--no-app]     (then load_sql.py, publish_powerbi.py)
"""
import argparse
import random
from datetime import datetime, timedelta, timezone

import pandas as pd

from common import ROOT
from review_queue import FIELD_INFO

OUT = ROOT / "data" / "dashboard"
WEEKS = {"rules": datetime(2026, 10, 12, 8, tzinfo=timezone.utc),       # simulated go-live per model
         "ins-neural-v1": datetime(2026, 10, 19, 8, tzinfo=timezone.utc),
         "ins-neural-v2": datetime(2026, 10, 26, 8, tzinfo=timezone.utc)}
REVIEWER_SLIP = 0.05      # share of decisions where the simulated reviewer gets it wrong
NO_REPLY_LATEST = 3   # the most recently flagged v2 values are still waiting for a reviewer ("no reply")


def primary_reason(flags: str) -> str:
    """'validity rule' if any range/ratio/required check fired, else 'low confidence'."""
    flags = [f for f in str(flags).split(";") if f]
    return "validity rule" if any(f != "low_confidence" for f in flags) else "low confidence"


def agrees(decision, outcome):
    """Does the reviewer's decision match the answer key? (accept a right value, fix a wrong one, reject a made-up one)"""
    if decision == "open":
        return None
    return {"accepted": outcome == "correct", "corrected": outcome == "wrong",
            "not_on_document": outcome == "spurious"}[decision]


def simulated(as_of):
    f = pd.read_parquet(OUT / "fact_field.parquet")
    q = f[(f.split == "test") & f.flagged].sort_values(["method", "doc_id", "field"]).reset_index(drop=True)
    rng = random.Random(7)
    rows = []
    for i, r in q.iterrows():
        start = WEEKS[r.method]
        created = start + timedelta(hours=rng.uniform(0, 5 * 24))            # flagged during the working week
        right = {"correct": "accepted", "wrong": "corrected", "spurious": "not_on_document"}[r.outcome]
        decision = right
        if rng.random() < REVIEWER_SLIP:                                     # reviewer slip
            decision = rng.choice([d for d in ("accepted", "corrected", "not_on_document") if d != right])
        decided = None if decision == "open" else created + timedelta(hours=rng.lognormvariate(1.4, 0.9))
        rows.append({"source": "simulated", "item_key": f"sim-{i + 1:03d}", "method": r.method,
                     "doc_id": r.doc_id, "field": r.field, "field_label": FIELD_INFO.get(r.field, (r.field,))[0],
                     "carrier": r.carrier, "layout": r.layout, "confidence": float(r.confidence),
                     "reason": primary_reason(r.flag_reasons), "decision": decision,
                     "created_at": created, "decided_at": decided,
                     "changed_mind": decision != "open" and rng.random() < 0.04,
                     "model_was_right": r.outcome == "correct", "reviewer_agrees": agrees(decision, r.outcome)})
    latest = sorted((x for x in rows if x["method"] == "ins-neural-v2"), key=lambda x: x["created_at"])[-NO_REPLY_LATEST:]
    for x in latest:
        x.update(decision="open", decided_at=None, changed_mind=False, reviewer_agrees=None)
    return rows


def from_app():
    """Real decisions from the Power Apps queue (Azure SQL)."""
    from common import connect_sql
    conn = connect_sql()
    cur = conn.cursor()
    cur.execute("""SELECT i.item_id, i.method, i.doc_id, i.field, i.field_label, i.carrier, i.layout, i.confidence,
                          i.flag_reasons, i.status, i.created_at, i.reviewed_at, k.outcome,
                          (SELECT COUNT(DISTINCT h.status) FROM review.item FOR SYSTEM_TIME ALL AS h
                            WHERE h.item_id = i.item_id AND h.status <> 'open') AS decisions_made
                   FROM review.item i JOIN review.answer_key k ON k.item_id = i.item_id""")
    rows = []
    for (item_id, method, doc_id, field, label, carrier, layout, conf, flags, status, created, reviewed,
         outcome, n_decisions) in cur.fetchall():
        decision = "open" if status == "open" else status
        rows.append({"source": "app", "item_key": f"app-{item_id:03d}", "method": method, "doc_id": doc_id,
                     "field": field, "field_label": label or field, "carrier": carrier, "layout": layout,
                     "confidence": conf, "reason": primary_reason(flags), "decision": decision,
                     "created_at": created.replace(tzinfo=timezone.utc) if created else None,
                     "decided_at": reviewed.replace(tzinfo=timezone.utc) if reviewed else None,
                     "changed_mind": (n_decisions or 0) > 1,
                     "model_was_right": outcome == "correct", "reviewer_agrees": agrees(decision, outcome)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-app", action="store_true", help="simulated rows only (no Azure SQL needed)")
    a = ap.parse_args()
    as_of = datetime.now(timezone.utc)
    rows = simulated(as_of)
    if not a.no_app:
        rows += from_app()
    d = pd.DataFrame(rows)
    d["decided"] = d.decision != "open"
    d["week"] = pd.to_datetime(d.created_at).dt.tz_convert(None).dt.to_period("W-SUN").dt.start_time
    d["hours_to_decision"] = (pd.to_datetime(d.decided_at) - pd.to_datetime(d.created_at)).dt.total_seconds() / 3600
    d["open_days"] = ((as_of - pd.to_datetime(d.created_at)).dt.total_seconds() / 86400).where(~d.decided)
    d["unseen_layout"] = True   # every queued value comes from the held-out test layouts
    for c in ("created_at", "decided_at"):
        d[c] = pd.to_datetime(d[c]).dt.tz_convert(None)
    d["reviewer_agrees"] = d.reviewer_agrees.astype("boolean")
    OUT.mkdir(parents=True, exist_ok=True)
    d.to_parquet(OUT / "fact_review.parquet", index=False)
    s = d.groupby(["source", "method"]).decision.value_counts().unstack(fill_value=0)
    print(s.to_string())
    print(f"→ {OUT / 'fact_review.parquet'}: {len(d)} rows")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    main()
