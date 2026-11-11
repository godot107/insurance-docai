"""Publish the dashboard semantic model to Power BI from Linux (no Power BI Desktop): Azure SQL → push dataset.

Reads the `dash` tables from Azure SQL (the serving layer), defines a Power BI semantic model through the REST
API (tables, relationships, DAX measures), and pushes the rows. Reports are then built in the browser on
this model. Push datasets don't refresh from SQL on their own; re-run this after each pipeline run.

Auth: a Power BI token for the *work* account, taken from a separate az CLI profile so the operator's
Azure login is untouched:
    AZURE_CONFIG_DIR=~/.azure-powerbi az login --use-device-code --allow-no-subscriptions --tenant <tenant>

Usage: .venv/bin/python src/publish_powerbi.py [--workspace insurance-docai] [--dataset "Insurance DocAI"]
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

import mssql_python
import pandas as pd
import requests

from common import ROOT, connect_sql

API = "https://api.powerbi.com/v1.0/myorg"
PBI_PROFILE = Path(os.environ.get("PBI_AZURE_CONFIG_DIR", Path.home() / ".azure-powerbi"))   # CI: the job's own az login

# Model: which SQL tables, extra key columns, relationships, and DAX measures.
TABLES = ["fact_document", "fact_field", "fact_quote", "fact_threshold", "fact_review", "fact_app_event", "dim_field", "dim_carrier", "dim_assumption", "dim_household"]
RELATIONSHIPS = [  # (from table, from column, to table, to column)  many → one
    ("fact_field", "doc_key", "fact_document", "doc_key"),
    ("fact_field", "field", "dim_field", "field"),
    ("fact_document", "carrier_id", "dim_carrier", "carrier_id"),
    ("fact_quote", "household_id", "dim_household", "household_id"),
]


def a(name):   # assumption lookup used inside measures
    return f"CALCULATE(MAX(dim_assumption[value]), dim_assumption[name] = \"{name}\")"


MEASURES = {
    "fact_field": {
        "Fields": "COUNTROWS(fact_field)",
        "Correct": "CALCULATE(COUNTROWS(fact_field), fact_field[outcome] = \"correct\")",
        "Fields printed": "CALCULATE(COUNTROWS(fact_field), fact_field[outcome] IN {\"correct\", \"wrong\", \"missed\"})",
        "Values extracted": "CALCULATE(COUNTROWS(fact_field), fact_field[outcome] IN {\"correct\", \"wrong\", \"spurious\"})",
        "Recall": "DIVIDE([Correct], [Fields printed])",
        "Precision": "DIVIDE([Correct], [Values extracted])",
        "Accuracy": "[Recall]",
        "Made-up values": "CALCULATE(COUNTROWS(fact_field), fact_field[outcome] = \"spurious\")",
        "Errors": "CALCULATE(COUNTROWS(fact_field), fact_field[outcome] IN {\"wrong\", \"spurious\", \"missed\"})",
        "Wrong values": "CALCULATE(COUNTROWS(fact_field), fact_field[outcome] = \"wrong\")",
        "Flagged fields": "CALCULATE(COUNTROWS(fact_field), fact_field[flagged] = TRUE())",
        "Flag rate": "DIVIDE([Flagged fields], [Fields])",
        "Silent errors": "CALCULATE(COUNTROWS(fact_field), fact_field[silent_error] = TRUE())",
        "Silent error rate": "DIVIDE([Silent errors], [Fields])",
        "Errors caught by review": "DIVIDE([Errors] - [Silent errors], [Errors])",
        "Avg confidence": "AVERAGE(fact_field[confidence])",
    },
    "fact_document": {
        "Documents": "COUNTROWS(fact_document)",
        "Total pages": "SUM(fact_document[pages])",
        "Straight-through docs": "CALCULATE(COUNTROWS(fact_document), fact_document[straight_through] = TRUE())",
        "Straight-through rate": "DIVIDE([Straight-through docs] + 0, [Documents])",
        # custom models are billed at the custom-extraction page price, prebuilt layout (rules) at the layout price
        "Extraction cost": f"SUMX(fact_document, fact_document[pages] * IF(fact_document[method] = \"rules\", "
                           f"{a('price_layout_per_page')}, {a('price_custom_per_page')}))",
        "Cost per document": "DIVIDE([Extraction cost], [Documents])",
        "Manual hours (all by hand)": f"[Documents] * {a('manual_minutes_per_doc')} / 60",
        "Review hours (pipeline)": f"SUM(fact_document[fields_flagged]) * {a('review_minutes_per_field')} / 60",
        "Labor $ avoided": f"([Manual hours (all by hand)] - [Review hours (pipeline)]) * {a('analyst_cost_per_hour')} - [Extraction cost]",
    },
    "fact_quote": {
        "Current premium": "CALCULATE(MAX(fact_quote[premium]), fact_quote[doc_type] = \"declarations\")",
        "Best comparable quote": "CALCULATE(MIN(fact_quote[premium]), fact_quote[apples_to_apples] = \"yes\")",
        "Annual savings": "IF(ISBLANK([Best comparable quote]), BLANK(), [Current premium] - [Best comparable quote])",
        "Savings %": "DIVIDE([Annual savings], [Current premium])",
        "Comparable quotes": "CALCULATE(COUNTROWS(fact_quote), fact_quote[apples_to_apples] = \"yes\") + 0",
        "Days to renewal": "DATEDIFF(TODAY(), MAX(fact_quote[renewal_date]), DAY)",
    },
    "fact_review": {   # review feedback & drift (src/build_review_feedback.py)
        "Values flagged": "COUNTROWS(fact_review)",
        "Decisions": "CALCULATE(COUNTROWS(fact_review), fact_review[decided] = TRUE()) + 0",
        "Accept rate": "DIVIDE(CALCULATE(COUNTROWS(fact_review), fact_review[decision] = \"accepted\") + 0, [Decisions])",
        "Correction rate": "DIVIDE(CALCULATE(COUNTROWS(fact_review), fact_review[decision] = \"corrected\") + 0, [Decisions])",
        "Not-on-document rate": "DIVIDE(CALCULATE(COUNTROWS(fact_review), fact_review[decision] = \"not_on_document\") + 0, [Decisions])",
        "No reply": "CALCULATE(COUNTROWS(fact_review), fact_review[decision] = \"open\") + 0",
        "No-reply rate": "DIVIDE([No reply], [Values flagged])",
        "Oldest open (days)": "CALCULATE(MAX(fact_review[open_days]), fact_review[decision] = \"open\")",
        "Median hours": "MEDIAN(fact_review[hours_to_decision])",
        "Reviewer agreement": "DIVIDE(CALCULATE(COUNTROWS(fact_review), fact_review[reviewer_agrees] = TRUE()), [Decisions])",
        "Flagged but right": "DIVIDE(CALCULATE(COUNTROWS(fact_review), fact_review[model_was_right] = TRUE()), [Values flagged])",
        "Changed decisions": "CALCULATE(COUNTROWS(fact_review), fact_review[changed_mind] = TRUE()) + 0",
    },
    "fact_app_event": {   # review-app usage telemetry (Application Insights → src/pull_app_telemetry.py)
        "Sessions": "DISTINCTCOUNT(fact_app_event[session_id])",
        "Active reviewers": "DISTINCTCOUNT(fact_app_event[reviewer])",
        "Items viewed": "CALCULATE(COUNTROWS(fact_app_event), fact_app_event[event] = \"item_viewed\") + 0",
        "Decisions logged": "CALCULATE(COUNTROWS(fact_app_event), fact_app_event[event] = \"review_decision\") + 0",
        "Skip rate": "1 - DIVIDE([Decisions logged], [Items viewed])",
        "Seconds per decision": "CALCULATE(MEDIAN(fact_app_event[seconds_on_item]), fact_app_event[event] = \"review_decision\")",
        "Decisions per session": "DIVIDE([Decisions logged], [Sessions])",
        "Review hours logged": "DIVIDE(SUM(fact_app_event[seconds_on_item]), 3600)",
    },
}
FORMATS = {"Accuracy": "0.0%", "Recall": "0.0%", "Precision": "0.0%", "Flag rate": "0.0%", "Silent error rate": "0.0%", "Errors caught by review": "0.0%",
           "Straight-through rate": "0.0%", "Savings %": "0.0%", "Avg confidence": "0.000",
           "Extraction cost": "$#,0.00", "Cost per document": "$#,0.0000", "Labor $ avoided": "$#,0",
           "Current premium": "$#,0", "Best comparable quote": "$#,0", "Annual savings": "$#,0",
           "Accept rate": "0%", "Correction rate": "0%", "Not-on-document rate": "0%", "No-reply rate": "0%",
           "Reviewer agreement": "0%", "Flagged but right": "0%", "Oldest open (days)": "0.0",
           "Median hours": "0.0", "Skip rate": "0%", "Seconds per decision": "0",
           "Decisions per session": "0.0", "Review hours logged": "0.00"}
COLUMN_FORMATS = {("fact_quote", c): "$#,0" for c in ("premium", "coverage_a", "coverage_c", "coverage_e",
                                                      "aop_deductible", "vs_current_usd")}
COLUMN_FORMATS |= {("fact_quote", "premium_per_1k_a"): "$#,0.00", ("fact_quote", "wind_hail_pct"): '0.0"%"',
                   ("fact_quote", "vs_current_pct"): '0.0"%"', ("dim_assumption", "value"): "#,0.00",
                   ("fact_threshold", "review_rate"): "0.0%", ("fact_threshold", "silent_error_rate"): "0.0%"}


def token():
    env = dict(os.environ, AZURE_CONFIG_DIR=str(PBI_PROFILE))
    out = subprocess.run(["az", "account", "get-access-token", "--resource", "https://analysis.windows.net/powerbi/api",
                          "--query", "accessToken", "-o", "tsv"], capture_output=True, text=True, env=env)
    if out.returncode:
        raise SystemExit("No Power BI login. Run:\n  AZURE_CONFIG_DIR=~/.azure-powerbi az login --use-device-code "
                         "--allow-no-subscriptions --tenant <tenant-id>\n" + out.stderr[-300:])
    return out.stdout.strip()


def pbi_type(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "Boolean"
    if pd.api.types.is_integer_dtype(s):
        return "Int64"
    if pd.api.types.is_float_dtype(s):
        return "Double"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "DateTime"
    return "String"


def read_sql():
    conn = connect_sql()
    cur, frames = conn.cursor(), {}
    for t in TABLES:
        cur.execute(f"SELECT * FROM dash.{t}")
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame.from_records([tuple(r) for r in cur.fetchall()], columns=cols)
        for c in df.columns:   # SQL BIT comes back as bool/int; keep booleans boolean
            if set(df[c].dropna().unique()) <= {True, False} and df[c].dtype == object:
                df[c] = df[c].astype("boolean")
        frames[t] = df
    for t in ("fact_document", "fact_field"):
        frames[t]["doc_key"] = frames[t]["method"] + "|" + frames[t]["doc_id"]
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=os.environ.get("PBI_WORKSPACE", "insurance-docai"))
    ap.add_argument("--dataset", default="Insurance DocAI")
    ap.add_argument("--recreate", action="store_true",
                    help="drop and recreate the dataset (needed for new tables/relationships); reports on it are rebound")
    args = ap.parse_args()
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token()}"

    groups = s.get(f"{API}/groups", params={"$filter": f"name eq '{args.workspace}'"}).json()["value"]
    gid = groups[0]["id"] if groups else s.post(f"{API}/groups?workspaceV2=True", json={"name": args.workspace}).json()["id"]
    base = f"{API}/groups/{gid}"

    frames = read_sql()
    tables = []
    for t, df in frames.items():
        tables.append({"name": t,
                       "columns": [{"name": c, "dataType": pbi_type(df[c]),
                                    **({"formatString": COLUMN_FORMATS[(t, c)]} if (t, c) in COLUMN_FORMATS else {})}
                                   for c in df.columns],
                       "measures": [{"name": m, "expression": e, **({"formatString": FORMATS[m]} if m in FORMATS else {})}
                                    for m, e in MEASURES.get(t, {}).items()]})
    existing = [d for d in s.get(f"{base}/datasets").json()["value"] if d["name"] == args.dataset]
    if existing and not args.recreate:
        # Update in place so reports bound to this dataset keep working: new columns/measures via PUT,
        # then clear and re-push rows. (Relationship changes need --recreate.)
        did = existing[0]["id"]
        for tdef in list(tables):
            r = s.put(f"{base}/datasets/{did}/tables/{tdef['name']}", json=tdef)
            if r.status_code == 404:   # push datasets can't gain tables in place
                print(f"skip {tdef['name']}: not in the existing dataset (add it with --recreate)")
                frames.pop(tdef["name"])
                continue
            if not r.ok:
                raise SystemExit(f"update table {tdef['name']} failed: {r.status_code} {r.text[:400]}")
    else:
        # --recreate. Deleting a dataset can delete the reports on it, so: create the new dataset first,
        # rebind its reports (same table/column/measure names, visuals keep working), then delete the old one.
        old_ids = {d["id"] for d in existing}
        bound = [r for r in s.get(f"{base}/reports").json()["value"] if r.get("datasetId") in old_ids]
        body = {"name": args.dataset, "defaultMode": "Push", "tables": tables,
                "relationships": [{"name": f"{f}_{fc}_{t}", "fromTable": f, "fromColumn": fc, "toTable": t,
                                   "toColumn": tc, "crossFilteringBehavior": "OneDirection"}
                                  for f, fc, t, tc in RELATIONSHIPS]}
        r = s.post(f"{base}/datasets", json=body)
        if not r.ok:
            raise SystemExit(f"create dataset failed: {r.status_code} {r.text[:500]}")
        did = r.json()["id"]
        for rep in bound:
            r = s.post(f"{base}/reports/{rep['id']}/Rebind", json={"datasetId": did})
            if not r.ok:
                raise SystemExit(f"rebind report '{rep['name']}' failed ({r.status_code} {r.text[:300]}); "
                                 f"old dataset(s) {sorted(old_ids)} kept, new dataset {did} left in place")
            print(f"rebound report '{rep['name']}' → {did}")
        for d in existing:
            s.delete(f"{base}/datasets/{d['id']}").raise_for_status()
    for t, df in frames.items():
        rows = json.loads(df.to_json(orient="records", date_format="iso"))
        # clear right before re-pushing each table (not all tables up front), so a failure leaves at most
        # one table empty instead of the whole dashboard
        s.delete(f"{base}/datasets/{did}/tables/{t}/rows").raise_for_status()
        for i in range(0, len(rows), 5000):
            s.post(f"{base}/datasets/{did}/tables/{t}/rows", json={"rows": rows[i:i + 5000]}).raise_for_status()
        print(f"{t:15} {len(rows):5} rows")
    print(f"dataset '{args.dataset}' in workspace '{args.workspace}': https://app.powerbi.com/groups/{gid}/datasets/{did}")


if __name__ == "__main__":
    from common import load_env
    load_env()   # DOCAI_ENV=dev|qa|prod (default dev)
    main()
