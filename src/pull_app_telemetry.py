"""Review-app usage telemetry: Application Insights (Log Analytics) → data/dashboard/fact_app_event.parquet.

Reads the Trace() events the Power Apps review app sends (queue_opened, item_viewed, review_decision) from the
workspace's AppTraces table. Real app events are source = app; events sent by simulate_app_telemetry.py carry
sim=true and become source = simulated, timed by their sim_time.

Privacy: no city, IP, tenant or user identifiers leave Log Analytics. Real users are reduced to a stable
pseudonym (reviewer-<6 hex of a salted hash>) so "decisions per reviewer" works without naming anyone.

Usage: .venv/bin/python src/pull_app_telemetry.py [--days 60]     (then load_sql.py, publish_powerbi.py)
"""
import argparse
import hashlib
import json
import os

import pandas as pd
import requests

from common import ROOT, credential

OUT = ROOT / "data" / "dashboard"
KQL = """
AppTraces
| where TimeGenerated > ago({days}d)
| where Message in ('queue_opened', 'item_viewed', 'review_decision')
| where tostring(Properties['ms-appName']) == 'Insurance DocAI Review'
| project TimeGenerated, event = Message, Properties, SessionId, UserId
"""


def pseudonym(user_id: str) -> str:
    salt = os.environ.get("TELEMETRY_SALT", "insurance-docai")
    return "reviewer-" + hashlib.sha256((salt + user_id).encode()).hexdigest()[:6]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()
    token = credential().get_token("https://api.loganalytics.io/.default").token
    r = requests.post(f"https://api.loganalytics.io/v1/workspaces/{os.environ['LOG_WORKSPACE_ID']}/query",
                      json={"query": KQL.format(days=a.days)}, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    t = r.json()["tables"][0]
    raw = pd.DataFrame(t["rows"], columns=[c["name"] for c in t["columns"]])
    rows = []
    for x in raw.itertuples():
        p = json.loads(x.Properties) if isinstance(x.Properties, str) else (x.Properties or {})
        sim = p.get("sim") == "true"
        user = x.UserId or ""
        rows.append({
            "source": "simulated" if sim else "app",
            "event_time": pd.Timestamp(p["sim_time"] if sim else x.TimeGenerated).tz_convert(None),
            "event": x.event,
            "session_id": x.SessionId,
            "reviewer": user if sim else pseudonym(user),
            "item_id": p.get("item_id"),
            "field": p.get("field"),
            "method": p.get("method"),
            "layout": p.get("layout"),
            "confidence": float(p["confidence"]) if p.get("confidence") else None,
            "decision": p.get("decision"),
            "seconds_on_item": float(p["seconds_on_item"]) if p.get("seconds_on_item") else None,
        })
    d = pd.DataFrame(rows)
    if d.empty:
        raise SystemExit("no review-app telemetry found yet (ingestion takes a few minutes)")
    d["event_date"] = d.event_time.dt.normalize()
    d["week"] = d.event_time.dt.to_period("W-SUN").dt.start_time
    OUT.mkdir(parents=True, exist_ok=True)
    d.to_parquet(OUT / "fact_app_event.parquet", index=False)
    print(d.groupby(["source", "event"]).size().unstack(fill_value=0).to_string())
    print(f"→ {OUT / 'fact_app_event.parquet'}: {len(d)} events")


if __name__ == "__main__":
    from common import load_env
    load_env()   # DOCAI_ENV=dev|qa|prod (default dev)
    main()
