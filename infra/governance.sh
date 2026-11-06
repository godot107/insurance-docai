#!/usr/bin/env bash
# Deploy subscription-level tag policies (infra/governance.bicep), then start remediation tasks
# so EXISTING resources missing tags get them too. Run by the operator (changes the whole subscription).
set -euo pipefail
cd "$(dirname "$0")"
az account show --query "{sub:name,id:id}" -o table
az deployment sub create -l eastus -n governance -f governance.bicep -o none
for t in project environment owner cost-center managed-by repository; do
  az policy remediation create -n "remediate-$t" --policy-assignment "inherit-tag-$t" \
    --resource-discovery-mode ReEvaluateCompliance -o none || true
done
az policy assignment list --query "[?starts_with(name,'inherit-tag-')].{name:name,effect:'modify'}" -o table
