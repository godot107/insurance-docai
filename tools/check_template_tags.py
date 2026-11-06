"""Offline IaC check (CI): every taggable resource in the compiled Bicep templates carries the required tags.

The Azure counterpart of the CloudBurn static scan used in the AWS projects (CloudBurn has no Azure rules yet).
Usage: az bicep build -f infra/main.bicep --outfile /tmp/main.json && python tools/check_template_tags.py /tmp/main.json
"""
import json
import sys

REQUIRED = ["project", "environment", "owner", "cost-center", "managed-by"]
NOT_TAGGABLE = ("Microsoft.Authorization/", "Microsoft.Insights/diagnosticSettings",
                "/blobServices", "/deployments", "/firewallRules")


def resources(tpl):
    res = tpl.get("resources", [])
    return res.values() if isinstance(res, dict) else res   # languageVersion 2.0 uses a dict


def main(paths):
    bad = 0
    for p in paths:
        tpl = json.load(open(p))
        tag_vars = tpl.get("parameters", {}).get("tags", {}).get("defaultValue", {})
        missing_defaults = [k for k in REQUIRED if k not in tag_vars]
        if missing_defaults:
            print(f"{p}: tags parameter default lacks {missing_defaults}")
            bad += 1
        for r in resources(tpl):
            t = r["type"]
            if any(s in t for s in NOT_TAGGABLE):
                continue
            if "tags" not in r:
                print(f"{p}: {t} '{r.get('name')}' has no tags")
                bad += 1
    print(f"template tag check: {'FAIL' if bad else 'ok'} ({bad} issues)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
