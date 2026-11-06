"""Read-only FinOps report for the subscription: tag compliance, budgets, month-to-date cost.

  tags    every resource group + resource has the required keys with allowed values (shared/TAGGING.md)
  budgets each budget's amount vs current spend
  cost    month-to-date cost grouped by `project` tag and by service (Cost Management API;
          a new subscription has no cost data for ~24-48 h, reported as "not available yet")

Exit code 1 if any tag violation, so it can gate CI or a scheduled check.
Usage: .venv/bin/python tools/finops.py
"""
import json
import subprocess
import sys

REQUIRED = {"project": None, "environment": {"dev", "paper", "prod", "shared"}, "owner": {"williemaize"},
            "cost-center": {"personal"}, "managed-by": None}
UNTAGGABLE = {"microsoft.cognitiveservices/accounts/projects"}   # tags shown but some APIs omit them


def az(*args):
    r = subprocess.run(["az", *args, "-o", "json"], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr else "az failed")
    return json.loads(r.stdout or "null")


def check_tags():
    bad = []
    items = [("group", g["name"], g.get("tags") or {}) for g in az("group", "list")]
    items += [(r["type"], r["name"], r.get("tags") or {}) for r in az("resource", "list")
              if r["type"].lower() not in UNTAGGABLE and not r["resourceGroup"].lower().startswith(("networkwatcherrg", "managed-", "databricks-rg"))]
    for kind, name, tags in items:
        for k, allowed in REQUIRED.items():
            v = tags.get(k)
            if v is None:
                bad.append(f"{kind} {name}: missing '{k}'")
            elif allowed and v not in allowed:
                bad.append(f"{kind} {name}: '{k}={v}' not in {sorted(allowed)}")
    print(f"tags: {len(items)} checked, {len(bad)} violations")
    for b in bad:
        print("  -", b)
    return bad


def budgets():
    for g in az("group", "list"):
        for b in az("consumption", "budget", "list", "--resource-group", g["name"]) or []:
            spent = float((b.get("currentSpend") or {}).get("amount", 0))
            print(f"budget {b['name']}: ${spent:,.2f} of ${float(b["amount"]):,.0f} ({g['name']})")


def cost():
    sub = az("account", "show")["id"]
    for group in [{"type": "TagKey", "name": "project"}, {"type": "Dimension", "name": "ServiceName"}]:
        body = {"type": "ActualCost", "timeframe": "MonthToDate",
                "dataset": {"granularity": "None", "aggregation": {"cost": {"name": "Cost", "function": "Sum"}},
                            "grouping": [group]}}
        try:
            res = az("rest", "--method", "post", "--url",
                     f"https://management.azure.com/subscriptions/{sub}/providers/Microsoft.CostManagement/query"
                     "?api-version=2023-11-01", "--body", json.dumps(body))
        except RuntimeError as e:
            print(f"cost by {group['name']}: not available yet ({str(e)[:80]}...)")
            return
        print(f"cost MTD by {group['name']}:")
        for row in res["properties"]["rows"]:
            print(f"  {row[1:]!s:50} ${row[0]:,.4f}")


if __name__ == "__main__":
    violations = check_tags()
    budgets()
    cost()
    sys.exit(1 if violations else 0)
