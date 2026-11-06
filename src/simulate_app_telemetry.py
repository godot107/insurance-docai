"""Simulate review-app usage telemetry through the real Application Insights pipeline.

Why: real usage takes weeks to accumulate. This sends the same events the Power Apps review app emits with
Trace() (queue_opened, item_viewed, review_decision) to Application Insights, shaped exactly like the app's
(AppTraces, record fields in Properties), so the ingestion → Log Analytics → SQL → Power BI path is exercised
end to end today. Every simulated event carries sim=true and sim_time (the simulated moment; App Insights stamps
its own ingestion time), and pull_app_telemetry.py reports them as source = simulated.

The story matches dash.fact_review's simulated rows (same items, decisions and weeks: rules → v1 → v2):
three pseudonymous reviewers work the queue in daily sessions; corrections take longer than accepts; some items
are opened and skipped; the three "no reply" items are viewed but never decided.

Usage: .venv/bin/python src/simulate_app_telemetry.py [--dry-run]     (needs APPINSIGHTS_CONNECTION_STRING in .env)
"""
import argparse
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from common import ROOT

REVIEWERS = ["sim-reviewer-1", "sim-reviewer-2", "sim-reviewer-3"]
SECONDS = {"accepted": (12, 0.5), "corrected": (45, 0.5), "not_on_document": (25, 0.5)}   # median, spread (lognormal)


def envelope(ikey, message, props, session, user, sim_time):
    return {"name": "Microsoft.ApplicationInsights.Message", "time": datetime.now(timezone.utc).isoformat(),
            "iKey": ikey,
            "tags": {"ai.session.id": session, "ai.user.id": user, "ai.cloud.role": "insurance-docai-review-simulator"},
            "data": {"baseType": "MessageData", "baseData": {
                "ver": 2, "message": message, "severityLevel": 1,
                "properties": {"sim": "true", "sim_time": sim_time.isoformat(), "ms-appName": "Insurance DocAI Review",
                               **{k: str(v) for k, v in props.items()}}}}}


def build_events(ikey):
    rv = pd.read_parquet(ROOT / "data" / "dashboard" / "fact_review.parquet")
    rv = rv[rv.source == "simulated"].sort_values("created_at")
    rng = random.Random(23)
    events = []
    # a session = one reviewer, one day: open the queue, then view/decide items flagged that day
    rv["day"] = pd.to_datetime(rv.created_at).dt.date
    for day, items in rv.groupby("day"):
        for reviewer in REVIEWERS:
            mine = items[items.index % len(REVIEWERS) == REVIEWERS.index(reviewer)]
            if mine.empty:
                continue
            session = str(uuid.UUID(int=rng.getrandbits(128)))
            t = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=rng.uniform(13, 21))
            events.append(envelope(ikey, "queue_opened", {"open_items": len(items)}, session, reviewer, t))
            for r in mine.itertuples():
                base = {"item_id": r.item_key, "field": r.field, "method": r.method, "layout": r.layout,
                        "confidence": round(r.confidence, 3)}
                t += timedelta(seconds=rng.uniform(3, 20))
                events.append(envelope(ikey, "item_viewed", base, session, reviewer, t))
                if r.decision == "open" or rng.random() < 0.08:          # viewed and skipped (no decision now)
                    continue
                med, spread = SECONDS[r.decision]
                secs = max(3, int(rng.lognormvariate(0, spread) * med))
                t += timedelta(seconds=secs)
                events.append(envelope(ikey, "review_decision", {**base, "decision": r.decision, "seconds_on_item": secs},
                                       session, reviewer, t))
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cs = dict(kv.split("=", 1) for kv in os.environ["APPINSIGHTS_CONNECTION_STRING"].split(";") if "=" in kv)
    events = build_events(cs["InstrumentationKey"])
    kinds = pd.Series([e["data"]["baseData"]["message"] for e in events]).value_counts().to_dict()
    print(f"{len(events)} simulated events: {kinds}")
    if a.dry_run:
        return
    url = cs["IngestionEndpoint"].rstrip("/") + "/v2/track"
    for i in range(0, len(events), 200):
        r = requests.post(url, data=json.dumps(events[i:i + 200]), headers={"Content-Type": "application/json"}, timeout=30)
        body = r.json() if r.content else {}
        if not r.ok or body.get("itemsAccepted") != len(events[i:i + 200]):
            raise SystemExit(f"ingestion failed: {r.status_code} {str(body)[:300]}")
    print(f"sent to Application Insights ({url.split('//')[1].split('/')[0]}); appears in Log Analytics within ~5 min")


if __name__ == "__main__":
    from common import load_env
    load_env()   # DOCAI_ENV=dev|qa|prod (default dev)
    main()
