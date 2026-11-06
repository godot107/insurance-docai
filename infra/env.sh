#!/usr/bin/env bash
# Write .env.<env> (gitignored) from an existing deployment's outputs, without deploying anything.
# Used by infra/up.sh after a deploy, and by CI jobs that need an environment's endpoints.
#   infra/env.sh [dev|qa|prod]
set -euo pipefail
cd "$(dirname "$0")/.."
ENV=${1:-dev}
[[ "$ENV" =~ ^(dev|qa|prod)$ ]] || { echo "usage: infra/env.sh [dev|qa|prod]" >&2; exit 2; }
set -a; source "config/$ENV.env"; set +a
OUTPUTS=$(mktemp)
az deployment group show -g "$RG" -n main --query properties.outputs -o json > "$OUTPUTS"
python3 - "$OUTPUTS" "$ENV" <<'PY'
import json, sys
o = json.load(open(sys.argv[1])); env = sys.argv[2]
keys = {"DOCINTEL_ENDPOINT": "docIntelEndpoint", "STORAGE_ACCOUNT": "storageAccount", "BLOB_ENDPOINT": "blobEndpoint",
        "OPENAI_ENDPOINT": "openaiEndpoint", "CHAT_DEPLOYMENT": "chatDeployment", "LOG_WORKSPACE": "logWorkspace",
        "LANGUAGE_ENDPOINT": "languageEndpoint", "DATABRICKS_HOST": "databricksUrl", "SQL_SERVER": "sqlServer",
        "SQL_DATABASE": "sqlDatabase", "LOG_WORKSPACE_ID": "logWorkspaceId",
        "APPINSIGHTS_CONNECTION_STRING": "appInsightsConnectionString"}
lines = [f"{k}={o[v]['value']}\n" for k, v in keys.items() if v in o and o[v]["value"]]
for path in [f".env.{env}"] + ([".env"] if env == "dev" else []):
    open(path, "w").writelines(lines)
print(f"[{env}] endpoints → .env.{env}" + (" (+ .env)" if env == "dev" else ""))
print("".join(l for l in lines if not l.startswith("APPINSIGHTS")))
PY
rm -f "$OUTPUTS"
