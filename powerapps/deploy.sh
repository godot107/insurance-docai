#!/usr/bin/env bash
# Power Apps as code: pull the review app from the developer environment into git, or push git → app.
#
#   powerapps/deploy.sh install  # NEW environment: build the solution from git (solution/ + src/) and import it
#   powerapps/deploy.sh pull     # export solution → unpack app sources into powerapps/src (overwrites src!)
#   powerapps/deploy.sh push     # pack powerapps/src → swap into solution → import + publish (same app, in place)
#
# Needs: Power Platform CLI (~/.dotnet/tools/pac, .NET 10) signed in as the work account:
#   pac auth create --deviceCode --environment <environment-id>
# Close Power Apps Studio first: an open authoring session locks the app (~15 min after closing).
# push refuses to run if the app in the environment changed since the last pull/push (would lose browser edits).
# powerapps/solution/ is the solution template (metadata only; the .msapp is always built from src/).
set -euo pipefail
cd "$(dirname "$0")"
PAC=${PAC:-$HOME/.dotnet/tools/pac}
SOLUTION=InsuranceDocAI
SOURCE_SQL_SERVER=sql-docai-i777yv-cus.database.windows.net   # server name inside powerapps/src
mkdir -p build

export_unpack() {
  "$PAC" solution export --name "$SOLUTION" --path build/$SOLUTION.zip --overwrite >/dev/null
  rm -rf build/solution
  "$PAC" solution unpack --zipfile build/$SOLUTION.zip --folder build/solution --packagetype Unmanaged --allowDelete true >/dev/null
  MSAPP=$(ls build/solution/CanvasApps/*_DocumentUri.msapp)
}

pack_app() {   # src → build/app.msapp, pointed at this deployment's SQL server (from ../.env)
  rm -rf build/src_push && cp -r src build/src_push
  local server
  server=$(grep -s '^SQL_SERVER=' ../.env | cut -d= -f2- || true)
  if [ -n "$server" ]; then
    grep -rl "$SOURCE_SQL_SERVER" build/src_push | xargs -r sed -i "s/$SOURCE_SQL_SERVER/$server/g"
  fi
  rm -f build/app.msapp
  "$PAC" canvas pack --sources build/src_push --msapp build/app.msapp > build/pack.log 2>&1 || { cat build/pack.log >&2; exit 1; }
  [ -s build/app.msapp ] || { echo "pack produced no app file" >&2; cat build/pack.log >&2; exit 1; }
}

case "${1:-}" in
  install)
    pack_app
    rm -rf build/sol_install && cp -r solution build/sol_install
    cp build/app.msapp build/sol_install/CanvasApps/idoc_insurancedocaireview_b69d0_DocumentUri.msapp
    "$PAC" solution pack --zipfile build/${SOLUTION}_install.zip --folder build/sol_install --packagetype Unmanaged >/dev/null
    "$PAC" solution import --path build/${SOLUTION}_install.zip --publish-changes >/dev/null
    echo "installed solution $SOLUTION; open the app once in make.powerapps.com to create/allow the SQL connection"
    ;;
  pull)
    export_unpack
    rm -rf src && "$PAC" canvas unpack --msapp "$MSAPP" --sources src >/dev/null
    sha256sum src/Src/Screen1.fx.yaml | cut -d' ' -f1 > .last_sync
    echo "pulled app sources into powerapps/src (review: git diff powerapps/src)"
    ;;
  push)
    export_unpack
    rm -rf build/live && "$PAC" canvas unpack --msapp "$MSAPP" --sources build/live >/dev/null
    if [ -f .last_sync ] && [ "$(sha256sum build/live/Src/Screen1.fx.yaml | cut -d' ' -f1)" != "$(cat .last_sync)" ]; then
      echo "The app changed in the environment since the last pull/push (edited in Studio?)." >&2
      echo "Run 'powerapps/deploy.sh pull', merge, then push." >&2
      exit 1
    fi
    pack_app
    cp build/app.msapp "$MSAPP"
    "$PAC" solution pack --zipfile build/${SOLUTION}_push.zip --folder build/solution --packagetype Unmanaged >/dev/null
    "$PAC" solution import --path build/${SOLUTION}_push.zip --publish-changes --force-overwrite >/dev/null
    # record what's live now (as the service re-serializes it) so the next push can detect browser edits
    export_unpack
    rm -rf build/live && "$PAC" canvas unpack --msapp "$MSAPP" --sources build/live >/dev/null
    sha256sum build/live/Src/Screen1.fx.yaml | cut -d' ' -f1 > .last_sync
    echo "pushed and published; players see it after 'Refresh' (or a reload)"
    ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
