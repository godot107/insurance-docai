#!/usr/bin/env bash
# Create/refresh the resource group, deploy infra/main.bicep, write endpoints to .env (gitignored).
set -euo pipefail
cd "$(dirname "$0")/.."
RG=rg-insurance-docai-dev
LOC=eastus
TAGS=(project=insurance-docai environment=dev owner=williemaize cost-center=personal
      managed-by=bicep repository=github.com/godot107/insurance-docai)

az account show --query "{sub:name,id:id}" -o table
OPERATOR=$(az ad signed-in-user show --query id -o tsv)
az group create -n "$RG" -l "$LOC" --tags "${TAGS[@]}" -o none
az deployment group create -g "$RG" -n main -f infra/main.bicep \
  -p operatorObjectId="$OPERATOR" sqlAdminLogin="$(az account show --query user.name -o tsv)" \
     sqlClientIps="[\"$(curl -s https://api.ipify.org)\"]" "$@" -o none
az deployment group show -g "$RG" -n main --query properties.outputs -o json > /tmp/docai_outputs.$$
python3 - "$@" <<PY
import json
o = json.load(open("/tmp/docai_outputs.$$"))
with open(".env", "w") as f:
    f.write(f"DOCINTEL_ENDPOINT={o['docIntelEndpoint']['value']}\n")
    f.write(f"STORAGE_ACCOUNT={o['storageAccount']['value']}\n")
    f.write(f"BLOB_ENDPOINT={o['blobEndpoint']['value']}\n")
    f.write(f"OPENAI_ENDPOINT={o['openaiEndpoint']['value']}\n")
    f.write(f"CHAT_DEPLOYMENT={o['chatDeployment']['value']}\n")
    f.write(f"LOG_WORKSPACE={o['logWorkspace']['value']}\n")
    f.write(f"LANGUAGE_ENDPOINT={o['languageEndpoint']['value']}\n")
    if o['databricksUrl']['value']:
        f.write(f"DATABRICKS_HOST={o['databricksUrl']['value']}\n")
    f.write(f"SQL_SERVER={o['sqlServer']['value']}\n")
    f.write(f"SQL_DATABASE={o['sqlDatabase']['value']}\n")
    f.write(f"LOG_WORKSPACE_ID={o['logWorkspaceId']['value']}\n")
    f.write(f"APPINSIGHTS_CONNECTION_STRING={o['appInsightsConnectionString']['value']}\n")
print(open(".env").read())
PY
rm -f /tmp/docai_outputs.$$
