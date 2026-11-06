#!/usr/bin/env bash
# Create/refresh one environment: resource group + infra/main.bicep with infra/params/<env>.bicepparam,
# then write that environment's endpoints to .env.<env> (gitignored; dev also refreshes the legacy .env).
#
#   infra/up.sh                 # dev (the only environment deployed for this project)
#   infra/up.sh qa --what-if    # show what qa would create/change, deploy nothing
#   infra/up.sh prod            # would deploy prod (defined, not built; CI requires an approval for prod)
#
# Identity-specific values come from the shell or CI, never from git:
#   OPERATOR_OBJECT_ID  Entra object ID that gets data-plane roles (default: the signed-in az user)
#   SQL_ADMIN_LOGIN     Entra login shown as SQL admin (default: the signed-in az user)
#   SQL_CLIENT_IPS      JSON array of IPs allowed through the SQL firewall (default: this machine's public IP)
set -euo pipefail
cd "$(dirname "$0")/.."
ENV=${1:-dev}
[[ "$ENV" =~ ^(dev|qa|prod)$ ]] || { echo "usage: infra/up.sh [dev|qa|prod] [--what-if]" >&2; exit 2; }
shift || true
WHATIF=false
[[ "${1:-}" == "--what-if" ]] && { WHATIF=true; shift; }

set -a; source "config/$ENV.env"; set +a          # RG, LOCATION, ... for this environment
TAGS=(project=insurance-docai environment="$ENV" owner=williemaize cost-center=personal
      managed-by=bicep repository=github.com/godot107/insurance-docai)

az account show --query "{sub:name,id:id}" -o table
export OPERATOR_OBJECT_ID=${OPERATOR_OBJECT_ID:-$(az ad signed-in-user show --query id -o tsv)}
export SQL_ADMIN_LOGIN=${SQL_ADMIN_LOGIN:-$(az account show --query user.name -o tsv)}
export SQL_CLIENT_IPS=${SQL_CLIENT_IPS:-"[\"$(curl -s https://api.ipify.org)\"]"}

if $WHATIF; then
  if [ "$(az group exists -n "$RG")" = "false" ]; then
    echo "[$ENV] resource group $RG does not exist: a deploy would create it and every resource in main.bicep"
    exit 0
  fi
  az deployment group what-if -g "$RG" -n main -p "infra/params/$ENV.bicepparam" "$@"
  exit 0
fi

az group create -n "$RG" -l "$LOCATION" --tags "${TAGS[@]}" -o none
az deployment group create -g "$RG" -n main -p "infra/params/$ENV.bicepparam" "$@" -o none
infra/env.sh "$ENV"
