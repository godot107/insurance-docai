#!/usr/bin/env bash
# Power Apps as code: the review app lives in powerapps/src (unpacked canvas app) + powerapps/solution (template).
#
#   powerapps/deploy.sh pull                   # dev → powerapps/src (after editing in Studio; overwrites src!)
#   powerapps/deploy.sh push                   # powerapps/src → dev (unmanaged solution, same app, in place)
#   powerapps/deploy.sh install [--env dev]    # new environment: build the solution from git and import it
#   powerapps/deploy.sh promote --env qa       # dev → MANAGED solution → qa/prod, pointed at that env's SQL
#   ... --dry-run                              # build everything, import nothing
#
# Environments come from config/<env>.env (PAC_PROFILE, SOLUTION_PACKAGE) and .env.<env> (SQL_SERVER,
# APPINSIGHTS_CONNECTION_STRING; written by infra/up.sh <env>). Only dev exists for this project.
# One Power Platform CLI profile per environment (device code, work account):
#   pac auth create --name docai-<env> --deviceCode --environment <environment-id>
# CI uses the same commands with a GitHub OIDC profile (pac auth create --githubFederated; docs/ENVIRONMENTS.md).
#
# Close Power Apps Studio first: an open authoring session locks the app (~15 min after closing).
# push refuses to run if the app in dev changed since the last pull/push (would lose browser edits).
set -euo pipefail
cd "$(dirname "$0")"
PAC=${PAC:-$HOME/.dotnet/tools/pac}
SOLUTION=InsuranceDocAI
SOURCE_SQL_SERVER=sql-docai-i777yv-cus.database.windows.net   # server name captured inside powerapps/src
APP=idoc_insurancedocaireview_b69d0

CMD=${1:-}; shift || true
ENV=${DOCAI_ENV:-dev}; DRY=false
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV=$2; shift 2 ;;
    --dry-run) DRY=true; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[[ "$ENV" =~ ^(dev|qa|prod)$ ]] || { echo "--env must be dev, qa or prod" >&2; exit 2; }
set -a
# shellcheck source=/dev/null
source "../config/$ENV.env"
set +a
ENVFILE=../.env.$ENV; [ -f "$ENVFILE" ] || { [ "$ENV" = dev ] && ENVFILE=../.env; }
mkdir -p build

use_profile() {   # select the CLI profile for an environment, or explain how to create it
  local profile=$1
  if ! "$PAC" auth select --name "$profile" >/dev/null 2>&1; then
    echo "no Power Platform CLI profile '$profile'. Create it:" >&2
    echo "  pac auth create --name $profile --deviceCode --environment <environment-id>" >&2
    exit 1
  fi
}

setting() { grep -s "^$1=" "$ENVFILE" | cut -d= -f2- || true; }

export_unpack() {   # current profile's solution → build/solution (unmanaged)
  "$PAC" solution export --name "$SOLUTION" --path build/$SOLUTION.zip --overwrite >/dev/null
  rm -rf build/solution
  "$PAC" solution unpack --zipfile build/$SOLUTION.zip --folder build/solution --packagetype Unmanaged --allowDelete true >/dev/null
  MSAPP=$(ls build/solution/CanvasApps/*_DocumentUri.msapp)
}

pack_app() {   # src → build/app.msapp, pointed at $ENV's SQL server and telemetry
  rm -rf build/src_push && cp -r src build/src_push
  local server ai
  server=$(setting SQL_SERVER)
  if [ -z "$server" ]; then
    echo "[$ENV] no SQL_SERVER in $ENVFILE (run infra/up.sh $ENV first)" >&2
    $DRY || exit 1
    server="sql-docai-$ENV-placeholder.database.windows.net"
  fi
  grep -rl "$SOURCE_SQL_SERVER" build/src_push | xargs -r sed -i "s/$SOURCE_SQL_SERVER/$server/g"
  ai=$(setting APPINSIGHTS_CONNECTION_STRING)   # telemetry (never in git)
  sed -i "s|__APPINSIGHTS_CONNECTION_STRING__|${ai}|" build/src_push/CanvasManifest.json
  rm -f build/app.msapp
  "$PAC" canvas pack --sources build/src_push --msapp build/app.msapp > build/pack.log 2>&1 || { cat build/pack.log >&2; exit 1; }
  [ -s build/app.msapp ] || { echo "pack produced no app file" >&2; cat build/pack.log >&2; exit 1; }
  echo "[$ENV] app packed against $server"
}

import_zip() {   # $1 = zip; skipped on --dry-run
  if $DRY; then echo "[$ENV] dry run: would import $1"; return; fi
  "$PAC" solution import --path "$1" --publish-changes --force-overwrite >/dev/null
  echo "[$ENV] imported $1"
}

case "$CMD" in
  install)
    use_profile "$PAC_PROFILE"
    pack_app
    rm -rf build/sol_install && cp -r solution build/sol_install
    cp build/app.msapp "build/sol_install/CanvasApps/${APP}_DocumentUri.msapp"
    "$PAC" solution pack --zipfile build/${SOLUTION}_install.zip --folder build/sol_install --packagetype Unmanaged >/dev/null
    import_zip build/${SOLUTION}_install.zip
    echo "open the app once in make.powerapps.com to create/allow the SQL connection"
    ;;
  pull)
    [ "$ENV" = dev ] || { echo "pull is dev only (qa/prod hold managed solutions)" >&2; exit 2; }
    use_profile "$PAC_PROFILE"
    export_unpack
    rm -rf src && "$PAC" canvas unpack --msapp "$MSAPP" --sources src >/dev/null
    sha256sum src/Src/Screen1.fx.yaml | cut -d' ' -f1 > .last_sync
    echo "pulled app sources into powerapps/src (review: git diff powerapps/src)"
    ;;
  push)
    [ "$ENV" = dev ] || { echo "push is dev only; use: deploy.sh promote --env $ENV" >&2; exit 2; }
    use_profile "$PAC_PROFILE"
    export_unpack
    rm -rf build/live && "$PAC" canvas unpack --msapp "$MSAPP" --sources build/live >/dev/null
    if [ -f .last_sync ] && [ "$(sha256sum build/live/Src/Screen1.fx.yaml | cut -d' ' -f1)" != "$(cat .last_sync)" ]; then
      echo "The app changed in dev since the last pull/push (edited in Studio?). Run 'deploy.sh pull', merge, then push." >&2
      exit 1
    fi
    pack_app
    cp build/app.msapp "$MSAPP"
    "$PAC" solution pack --zipfile build/${SOLUTION}_push.zip --folder build/solution --packagetype Unmanaged >/dev/null
    import_zip build/${SOLUTION}_push.zip
    if ! $DRY; then   # record what's live now so the next push can detect browser edits
      export_unpack
      rm -rf build/live && "$PAC" canvas unpack --msapp "$MSAPP" --sources build/live >/dev/null
      sha256sum build/live/Src/Screen1.fx.yaml | cut -d' ' -f1 > .last_sync
    fi
    ;;
  promote)
    [ "$ENV" != dev ] || { echo "promote targets qa or prod (dev uses push)" >&2; exit 2; }
    # 1. from dev: export unmanaged + managed, unpack both (SolutionPackager 'Both' layout)
    use_profile "$(grep '^PAC_PROFILE=' ../config/dev.env | cut -d= -f2)"
    "$PAC" solution export --name "$SOLUTION" --path build/${SOLUTION}.zip --overwrite >/dev/null
    "$PAC" solution export --name "$SOLUTION" --path build/${SOLUTION}_managed.zip --managed --overwrite >/dev/null
    rm -rf build/promote
    "$PAC" solution unpack --zipfile build/${SOLUTION}.zip --folder build/promote --packagetype Both --allowDelete true >/dev/null
    # 2. rebuild the app against the TARGET environment's SQL server + telemetry, pack as managed
    pack_app
    for f in build/promote/CanvasApps/*_DocumentUri.msapp; do cp build/app.msapp "$f"; done
    "$PAC" solution pack --zipfile "build/${SOLUTION}_${ENV}_managed.zip" --folder build/promote --packagetype Managed >/dev/null
    echo "[$ENV] built managed solution build/${SOLUTION}_${ENV}_managed.zip"
    # 3. import into the target environment (needs its own profile; prod is approved in CI)
    $DRY || use_profile "$PAC_PROFILE"
    import_zip "build/${SOLUTION}_${ENV}_managed.zip"
    ;;
  *) sed -n '2,16p' "$0"; exit 2 ;;
esac
