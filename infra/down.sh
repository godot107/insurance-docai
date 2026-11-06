#!/usr/bin/env bash
# Delete EVERYTHING in the project resource group. Asks before deleting.
set -euo pipefail
RG=rg-insurance-docai-dev
az resource list -g "$RG" -o table
read -r -p "Delete resource group $RG and all resources above? [y/N] " ok
[[ "$ok" == "y" ]] || { echo "aborted"; exit 1; }
az group delete -n "$RG" --yes
# Document Intelligence / Language accounts are soft-deleted; purge so names and F0 quota free up:
echo "Check soft-deleted Cognitive Services accounts: az cognitiveservices account list-deleted -o table"
