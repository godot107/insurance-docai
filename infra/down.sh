#!/usr/bin/env bash
# Delete EVERYTHING in one environment's resource group. Asks before deleting.
#   infra/down.sh [dev|qa|prod]
set -euo pipefail
cd "$(dirname "$0")/.."
ENV=${1:-dev}
[[ "$ENV" =~ ^(dev|qa|prod)$ ]] || { echo "usage: infra/down.sh [dev|qa|prod]" >&2; exit 2; }
set -a
# shellcheck source=/dev/null
source "config/$ENV.env"
set +a
az resource list -g "$RG" -o table
read -r -p "Delete [$ENV] resource group $RG and all resources above? Type the environment name to confirm: " ok
[[ "$ok" == "$ENV" ]] || { echo "aborted"; exit 1; }
az group delete -n "$RG" --yes
# Document Intelligence / Language accounts are soft-deleted; purge so names and quota free up:
echo "Check soft-deleted Cognitive Services accounts: az cognitiveservices account list-deleted -o table"
