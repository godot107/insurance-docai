"""Build the Power BI report as code (PBIR JSON) and publish it to the workspace, from Linux (no Desktop).

Five pages from docs/DASHBOARD.md, on the push dataset that publish_powerbi.py maintains:
  1. Should I Switch?   homeowner: current premium vs comparable quotes (simulated `demo` household)
  2. Operations         product owner / ops: recall, precision, silent errors, review load, threshold curve
  3. Review feedback    ops: accept / correct / not-on-document rates, no-reply aging, drift by week
  4. Review app usage   ops: sessions, views vs decisions, skip rate, seconds per decision (App Insights)
  5. Finance            CFO: cost per document, labor avoided, the assumptions behind them

The definition is written to powerbi/report/ (versioned like the Bicep) and uploaded with the Fabric
item-definition API, which works in a Power BI PPU workspace. Edit visuals in the browser afterwards if you
like; then `--pull` saves the browser version back into powerbi/report/ so it isn't lost.

Usage: .venv/bin/python src/build_report.py [--pull] [--dry-run]
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

from common import ROOT

PBI = "https://api.powerbi.com/v1.0/myorg"
FABRIC = "https://api.fabric.microsoft.com/v1"
OUT = ROOT / "powerbi" / "report"
BASE = ROOT / "powerbi" / "backup" / "2026-11-06-hand-built"   # theme + report.json shell from the service
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
W, H = 1280, 720


def token(resource):
    env = dict(os.environ, AZURE_CONFIG_DIR=os.environ.get("PBI_AZURE_CONFIG_DIR", str(Path.home() / ".azure-powerbi")))
    out = subprocess.run(["az", "account", "get-access-token", "--resource", resource, "--query", "accessToken",
                          "-o", "tsv"], capture_output=True, text=True, env=env, check=True)
    return out.stdout.strip()


def vid(*parts):   # stable 20-hex ids so re-running produces the same files (clean git diffs)
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


# ---------- query helpers ----------
def col(t, c):
    return {"Column": {"Expression": {"SourceRef": {"Entity": t}}, "Property": c}}


def meas(t, m):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": t}}, "Property": m}}


def avg(t, c):
    return {"Aggregation": {"Expression": col(t, c), "Function": 1}}


def proj(field, label=None):
    if "Measure" in field:
        t, p = field["Measure"]["Expression"]["SourceRef"]["Entity"], field["Measure"]["Property"]
        ref, native = f"{t}.{p}", p
    elif "Aggregation" in field:
        c = field["Aggregation"]["Expression"]["Column"]
        t, p = c["Expression"]["SourceRef"]["Entity"], c["Property"]
        ref, native = f"Avg({t}.{p})", f"Average of {p}"
    else:
        t, p = field["Column"]["Expression"]["SourceRef"]["Entity"], field["Column"]["Property"]
        ref, native = f"{t}.{p}", p
    out = {"field": field, "queryRef": ref, "nativeQueryRef": native}
    if label:
        out["displayName"] = label
    return out


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def filt(name, t, c, values):
    """Visual-level 'column IN (values)'; values are DAX literals like "'test'" or "true"."""
    return {"name": name, "field": col(t, c), "type": "Categorical", "filter": where_in(t, c, values)}


def where_in(t, c, values):
    return {"Version": 2, "From": [{"Name": "x", "Entity": t, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "x"}}, "Property": c}}],
                "Values": [[{"Literal": {"Value": v}}] for v in values]}}}]}


def visual(page, key, vtype, x, y, w, h, buckets=None, title=None, filters=None, objects=None, z=0, sort=None):
    v = {"visualType": vtype}
    if buckets:
        v["query"] = {"queryState": {b: {"projections": [proj(*f) if isinstance(f, tuple) else proj(f) for f in fs]}
                                     for b, fs in buckets.items()}}
        if sort:   # (field, "Descending"|"Ascending")
            v["query"]["sortDefinition"] = {"sort": [{"field": sort[0], "direction": sort[1]}], "isDefaultSort": False}
    if objects:
        v["objects"] = objects
    if title:
        v["visualContainerObjects"] = {"title": [{"properties": {"show": lit("true"), "text": lit(f"'{title}'")}}]}
    v["drillFilterOtherVisuals"] = True
    out = {"$schema": f"{SCHEMA}/visualContainer/2.14.0/schema.json", "name": vid(page, key),
           "position": {"x": x, "y": y, "z": z, "width": w, "height": h, "tabOrder": z}, "visual": v}
    if filters:
        out["filterConfig"] = {"filters": filters}
    return key, out


def text(page, key, x, y, w, h, runs):
    """runs: list of paragraphs; each paragraph a list of (text, style dict)."""
    paras = [{"textRuns": [{"value": t, **({"textStyle": s} if s else {})} for t, s in p]} for p in runs]
    return visual(page, key, "textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": paras}}]})


def card(page, key, x, y, m, w=200, h=110):
    return visual(page, key, "card", x, y, w, h, {"Values": [m]},
                  objects={"categoryLabels": [{"properties": {"fontSize": lit("10D")}}]})


def slicer(page, key, x, y, w, h, c, title, values, default=None, mode="Basic"):
    """Slicer limited to `values` (hides the blank member push datasets add on the one side). With `default`:
    single-select and preselected, so cards never mix methods or train/test. Without: multi-select, all shown."""
    t, name = c["Column"]["Expression"]["SourceRef"]["Entity"], c["Column"]["Property"]
    objects = {"data": [{"properties": {"mode": lit(f"'{mode}'")}}]}
    if default is not None:
        objects["selection"] = [{"properties": {"singleSelect": lit("true")}}]
        objects["general"] = [{"properties": {"filter": {"filter": where_in(t, name, [f"'{default}'"])}}}]
    return visual(page, key, "slicer", x, y, w, h, {"Values": [c]}, title=title,
                  filters=[filt(f"{key}_values", t, name, [f"'{v}'" for v in values])], objects=objects)


H1 = {"fontWeight": "bold", "fontSize": "20pt"}
NOTE = {"fontSize": "10pt", "color": "#605E5C"}


# ---------- pages ----------
def page_switch():
    p = "switch"
    q = "fact_quote"
    vs = [
        text(p, "title", 20, 10, 1240, 70, [[("Should I switch?", H1)],
             [("Simulated demo household. Mailers are estimates, not offers: only quotes with the same dwelling "
               "coverage, deductible and wind/hail % are real comparisons (comparable = yes).", NOTE)]]),
        card(p, "current", 20, 90, meas(q, "Current premium")),
        card(p, "best", 230, 90, meas(q, "Best comparable quote")),
        card(p, "savings", 440, 90, meas(q, "Annual savings")),
        card(p, "savings_pct", 650, 90, meas(q, "Savings %")),
        card(p, "n_comparable", 860, 90, meas(q, "Comparable quotes")),
        card(p, "days", 1070, 90, meas(q, "Days to renewal")),
        visual(p, "by_carrier", "clusteredBarChart", 20, 215, 390, 490,
               {"Category": [col(q, "carrier")], "Y": [(avg(q, "premium"), "Annual premium")],
                "Series": [(col(q, "apples_to_apples"), "Comparable?")]},
               title="Annual premium by insurer", sort=(avg(q, "premium"), "Descending")),
        visual(p, "quotes", "tableEx", 420, 215, 840, 490,
               {"Values": [(col(q, "carrier"), "Insurer"), (col(q, "premium"), "Premium"), (col(q, "premium_per_1k_a"), "Per $1k A"),
                           (col(q, "coverage_a"), "Dwelling (A)"), (col(q, "aop_deductible"), "Deductible"),
                           (col(q, "wind_hail_pct"), "Wind/hail"), (col(q, "apples_to_apples"), "Comparable?"),
                           (col(q, "vs_current_usd"), "vs current"), (col(q, "notes"), "Why not comparable")]},
               title="Quotes vs current policy"),
    ]
    return p, "Should I Switch?", vs


def page_ops():
    p = "ops"
    f, d, t = "fact_field", "fact_document", "fact_threshold"
    vs = [
        text(p, "title", 20, 10, 900, 70, [[("Operations: is extraction trustworthy?", H1)],
             [("Synthetic benchmark with exact answers. 'test' = the two layouts no model saw in training. "
               "Silent errors = wrong values that no review rule flagged.", NOTE)]]),
        # the method slicer from the hand-built page, moved here: it filters fact_document → fact_field
        slicer(p, "method", 930, 5, 170, 200, col(d, "method"), "Extraction method",
               ["rules", "ins-neural-v1", "ins-neural-v2"], "ins-neural-v2"),
        slicer(p, "split", 1110, 5, 150, 200, col(d, "split"), "Layouts", ["test", "train"], "test"),
        card(p, "recall", 20, 90, meas(f, "Recall"), w=145),
        card(p, "precision", 175, 90, meas(f, "Precision"), w=145),
        card(p, "madeup", 330, 90, meas(f, "Made-up values"), w=145),
        card(p, "silent", 485, 90, meas(f, "Silent errors"), w=145),
        card(p, "flagrate", 640, 90, meas(f, "Flag rate"), w=135),
        card(p, "stp", 785, 90, meas(d, "Straight-through rate"), w=135),
        visual(p, "by_layout", "clusteredBarChart", 20, 215, 410, 490,
               {"Category": [col(f, "layout")], "Y": [meas(f, "Recall")]},
               title="Recall by layout", sort=(meas(f, "Recall"), "Descending")),
        visual(p, "reasons", "clusteredBarChart", 440, 215, 380, 490,
               {"Category": [(col(f, "flag_reasons"), "Reason")], "Y": [meas(f, "Flagged fields")]},
               title="Why values were sent to review", sort=(meas(f, "Flagged fields"), "Descending"),
               filters=[filt("flagged_only", f, "flagged", ["true"])]),
        visual(p, "review_curve", "lineChart", 830, 215, 430, 240,
               {"Category": [col(t, "threshold")], "Y": [(avg(t, "review_rate"), "Share sent to review")],
                "Series": [col(t, "method")]},
               title="Review load vs cutoff (all methods, + rules)",
               filters=[filt("rules_on", t, "rules", ["true"])]),
        visual(p, "silent_curve", "lineChart", 830, 465, 430, 240,
               {"Category": [col(t, "threshold")], "Y": [(avg(t, "silent_error_rate"), "Silent error rate")],
                "Series": [col(t, "method")]},
               title="Silent errors vs cutoff (all methods, + rules)",
               filters=[filt("rules_on", t, "rules", ["true"])]),
    ]
    return p, "Operations", vs


def page_finance():
    p = "finance"
    d = "fact_document"
    vs = [
        text(p, "title", 20, 10, 1240, 70, [[("Finance: what does it cost, what does it save?", H1)],
             [("Cards: whole benchmark run (all methods). Labor $ avoided = (hours to key every document by hand − "
               "hours reviewing flagged fields) × analyst rate − extraction cost. Assumptions below.", NOTE)]]),
        card(p, "docs", 20, 90, meas(d, "Documents")),
        card(p, "cost", 230, 90, meas(d, "Extraction cost")),
        card(p, "cpd", 440, 90, meas(d, "Cost per document")),
        card(p, "manual", 650, 90, meas(d, "Manual hours (all by hand)")),
        card(p, "review", 860, 90, meas(d, "Review hours (pipeline)")),
        card(p, "labor", 1070, 90, meas(d, "Labor $ avoided")),
        visual(p, "labor_by_method", "clusteredBarChart", 20, 215, 500, 490,
               {"Category": [(col(d, "method"), "Method")], "Y": [meas(d, "Labor $ avoided")]},
               title="Labor $ avoided per method (same 24 unseen-layout docs)",
               sort=(meas(d, "Labor $ avoided"), "Descending"), filters=[filt("test_only", d, "split", ["'test'"])]),
        visual(p, "assumptions", "tableEx", 535, 215, 725, 490,
               {"Values": [(col("dim_assumption", "name"), "Assumption"), (col("dim_assumption", "value"), "Value"),
                           (col("dim_assumption", "unit"), "Unit"), (col("dim_assumption", "note"), "Source / note")]},
               title="Assumptions (edit in build_dashboard_tables.py)"),
    ]
    return p, "Finance", vs


def page_feedback():
    """Operations, part 2: what reviewers do with flagged values, and whether that mix drifts."""
    p = "feedback"
    r = "fact_review"
    vs = [
        text(p, "title", 20, 10, 860, 110, [[("Operations: review feedback & drift", H1)],
             [("Simulated reviewer, real benchmark outcomes, replaying the project's history: rules (week of Oct 12) → "
               "custom model v1 (Oct 19) → v2 augmented (Oct 26). Watch 'not on document' spike with v1 (made-up "
               "values) and fall with v2. Source 'app' = live decisions from the Power Apps queue.", NOTE)]]),
        slicer(p, "source", 900, 10, 170, 90, col(r, "source"), "Source", ["simulated", "app"], "simulated",
               mode="Dropdown"),
        slicer(p, "method", 1090, 10, 170, 90, col(r, "method"), "Model",
               ["rules", "ins-neural-v1", "ins-neural-v2"], mode="Dropdown"),
        card(p, "flagged", 20, 135, meas(r, "Values flagged"), w=170, h=100),
        card(p, "accept", 200, 135, meas(r, "Accept rate"), w=170, h=100),
        card(p, "correct", 380, 135, meas(r, "Correction rate"), w=170, h=100),
        card(p, "notondoc", 560, 135, meas(r, "Not-on-document rate"), w=170, h=100),
        card(p, "noreply", 740, 135, meas(r, "No reply"), w=170, h=100),
        card(p, "hours", 920, 135, meas(r, "Median hours"), w=170, h=100),
        card(p, "agree", 1100, 135, meas(r, "Reviewer agreement"), w=160, h=100),
        visual(p, "by_week", "columnChart", 20, 250, 420, 455,
               {"Category": [(col(r, "week"), "Week")], "Y": [(meas(r, "Values flagged"), "Values")],
                "Series": [(col(r, "decision"), "Decision")]},
               title="Decisions by week"),
        visual(p, "drift", "lineChart", 450, 250, 420, 455,
               {"Category": [(col(r, "week"), "Week")],
                "Y": [meas(r, "Correction rate"), meas(r, "Not-on-document rate"), meas(r, "Accept rate")]},
               title="Decision mix by week (drift)",
               objects={"lineStyles": [{"properties": {"lineChartType": lit("'straight'"), "showMarker": lit("true")}}]}),
        visual(p, "open", "tableEx", 880, 250, 380, 455,
               {"Values": [(col(r, "doc_id"), "Letter"),
                           (col(r, "field_label"), "Field"), (meas(r, "Oldest open (days)"), "Open (days)")]},
               title="No reply: values still waiting", sort=(meas(r, "Oldest open (days)"), "Descending"),
               filters=[filt("open_only", r, "decision", ["'open'"])]),
    ]
    return p, "Review feedback & drift", vs


def page_usage():
    """Operations, part 3: how the review app is used (Application Insights telemetry from Trace() events)."""
    p = "usage"
    e = "fact_app_event"
    vs = [
        text(p, "title", 20, 10, 860, 110, [[("Operations: review app usage", H1)],
             [("Telemetry from the Power Apps review app (Application Insights). 'simulated' = events sent through the "
               "same pipeline by a simulator matching the feedback page's weeks; 'app' = real sessions. Reviewers are "
               "pseudonyms; no location or user identifiers are kept.", NOTE)]]),
        slicer(p, "source", 900, 10, 170, 90, col(e, "source"), "Source", ["simulated", "app"], "simulated",
               mode="Dropdown"),
        slicer(p, "method", 1090, 10, 170, 90, col(e, "method"), "Model",
               ["rules", "ins-neural-v1", "ins-neural-v2"], mode="Dropdown"),
        card(p, "sessions", 20, 135, meas(e, "Sessions"), w=170, h=100),
        card(p, "reviewers", 200, 135, meas(e, "Active reviewers"), w=170, h=100),
        card(p, "viewed", 380, 135, meas(e, "Items viewed"), w=170, h=100),
        card(p, "decisions", 560, 135, meas(e, "Decisions logged"), w=170, h=100),
        card(p, "skip", 740, 135, meas(e, "Skip rate"), w=170, h=100),
        card(p, "secs", 920, 135, meas(e, "Seconds per decision"), w=170, h=100),
        card(p, "per_session", 1100, 135, meas(e, "Decisions per session"), w=160, h=100),
        visual(p, "by_week", "clusteredColumnChart", 20, 250, 420, 455,
               {"Category": [(col(e, "week"), "Week")],
                "Y": [meas(e, "Items viewed"), meas(e, "Decisions logged")]},
               title="Views vs decisions by week"),
        visual(p, "secs_by_decision", "clusteredBarChart", 450, 250, 420, 455,
               {"Category": [(col(e, "decision"), "Decision")], "Y": [(meas(e, "Seconds per decision"), "Median seconds")]},
               title="Median seconds per decision type", sort=(meas(e, "Seconds per decision"), "Descending"),
               filters=[filt("decisions_only", e, "event", ["'review_decision'"])]),
        visual(p, "by_reviewer", "tableEx", 880, 250, 380, 455,
               {"Values": [(col(e, "reviewer"), "Reviewer"), (meas(e, "Decisions logged"), "Decisions"),
                           (meas(e, "Seconds per decision"), "Sec"), (meas(e, "Skip rate"), "Skip")]},
               title="By reviewer (pseudonyms)", sort=(meas(e, "Decisions logged"), "Descending")),
    ]
    return p, "Review app usage", vs


# ---------- write + publish ----------
def write_definition(dataset_id, dataset_name, workspace):
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(BASE / "StaticResources", OUT / "StaticResources")
    (OUT / "definition").mkdir(parents=True)
    for f in ("report.json", "version.json"):
        shutil.copy(BASE / "definition" / f, OUT / "definition" / f)
    shutil.copy(BASE / ".platform", OUT / ".platform")
    (OUT / "definition.pbir").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byConnection": {"connectionString":
            f"Data Source=powerbi://api.powerbi.com/v1.0/myorg/{workspace};initial catalog=\"{dataset_name}\";"
            f"integrated security=ClaimsToken;semanticmodelid={dataset_id}"}}}, indent=2))
    pages = [page_switch(), page_ops(), page_feedback(), page_usage(), page_finance()]
    pdir = OUT / "definition" / "pages"
    order = []
    for key, name, visuals in pages:
        pid = vid("page", key)
        order.append(pid)
        (pdir / pid / "visuals").mkdir(parents=True)
        (pdir / pid / "page.json").write_text(json.dumps({
            "$schema": f"{SCHEMA}/page/2.1.0/schema.json", "name": pid, "displayName": name,
            "displayOption": "FitToPage", "height": H, "width": W}, indent=2))
        for _, v in visuals:
            (pdir / pid / "visuals" / v["name"]).mkdir()
            (pdir / pid / "visuals" / v["name"] / "visual.json").write_text(json.dumps(v, indent=2))
    (pdir / "pages.json").write_text(json.dumps({
        "$schema": f"{SCHEMA}/pagesMetadata/1.1.0/schema.json", "pageOrder": order, "activePageName": order[0]},
        indent=2))
    return len(pages), sum(len(v) for _, _, v in pages)


def wait(s, resp):
    """Fabric long-running operation: poll until done, return the result (or None)."""
    if resp.status_code == 200:
        return resp.json() if resp.content else None
    if resp.status_code != 202:
        raise SystemExit(f"Fabric API {resp.status_code}: {resp.text[:500]}")
    op = resp.headers["x-ms-operation-id"]
    for _ in range(60):
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        st = s.get(f"{FABRIC}/operations/{op}").json()
        if st["status"] == "Succeeded":
            r = s.get(f"{FABRIC}/operations/{op}/result")
            return r.json() if r.ok and r.content else None
        if st["status"] == "Failed":
            raise SystemExit(f"Fabric operation failed: {json.dumps(st)[:800]}")
    raise SystemExit("Fabric operation timed out")


def parts_from(folder):
    return [{"path": p.relative_to(folder).as_posix(), "payloadType": "InlineBase64",
             "payload": base64.b64encode(p.read_bytes()).decode()}
            for p in sorted(folder.rglob("*")) if p.is_file()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=os.environ.get("PBI_WORKSPACE", "insurance-docai"))
    ap.add_argument("--dataset", default="Insurance DocAI")
    ap.add_argument("--report", default="Insurance DocAI")
    ap.add_argument("--pull", action="store_true", help="save the report as it is in the service into powerbi/report/")
    ap.add_argument("--dry-run", action="store_true", help="write powerbi/report/ only, don't upload")
    a = ap.parse_args()
    pbi = requests.Session()
    pbi.headers["Authorization"] = f"Bearer {token('https://analysis.windows.net/powerbi/api')}"
    gid = pbi.get(f"{PBI}/groups", params={"$filter": f"name eq '{a.workspace}'"}).json()["value"][0]["id"]
    did = next(d["id"] for d in pbi.get(f"{PBI}/groups/{gid}/datasets").json()["value"] if d["name"] == a.dataset)
    rep = next((r for r in pbi.get(f"{PBI}/groups/{gid}/reports").json()["value"] if r["name"] == a.report), None)
    fab = requests.Session()
    fab.headers["Authorization"] = f"Bearer {token('https://api.fabric.microsoft.com')}"

    if a.pull:
        res = wait(fab, fab.post(f"{FABRIC}/workspaces/{gid}/reports/{rep['id']}/getDefinition"))
        if OUT.exists():
            shutil.rmtree(OUT)
        for part in res["definition"]["parts"]:
            p = OUT / part["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(base64.b64decode(part["payload"]))
        print(f"pulled '{a.report}' → {OUT.relative_to(ROOT)}")
        return

    n_pages, n = write_definition(did, a.dataset, a.workspace)
    print(f"wrote {OUT.relative_to(ROOT)}: {n_pages} pages, {n} visuals, bound to dataset {did}")
    if a.dry_run:
        return
    body = {"definition": {"parts": parts_from(OUT)}}
    if rep:
        wait(fab, fab.post(f"{FABRIC}/workspaces/{gid}/reports/{rep['id']}/updateDefinition", json=body))
        rid = rep["id"]
    else:
        res = wait(fab, fab.post(f"{FABRIC}/workspaces/{gid}/reports", json={"displayName": a.report, **body}))
        rid = res["id"] if res else "?"
    print(f"published: https://app.powerbi.com/groups/{gid}/reports/{rid}")


if __name__ == "__main__":
    from common import load_env
    load_env()   # DOCAI_ENV=dev|qa|prod (default dev)
    main()
