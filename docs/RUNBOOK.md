# Runbook: insurance-docai

How to operate everything in this repo. Commands run from the repo root unless noted (`cd src` for pipeline
scripts, which import `common.py`).

## 0. Identities and access

| Who | Used for | How |
| :--- | :--- | :--- |
| Operator (personal Microsoft account, guest in tenant) | Azure resources, SQL admin, Document Intelligence, Foundry, Language | `az login` (default profile) |
| Work account `willie@<tenant>.onmicrosoft.com` | Power BI (personal accounts can't use Power BI / Fabric) | `AZURE_CONFIG_DIR=~/.azure-powerbi az login --use-device-code --allow-no-subscriptions --tenant <tenant-id>` |

No API keys or SQL passwords exist: every service has local auth disabled and access is RBAC (see
`infra/main.bicep`, role assignments section). Endpoints live in `.env` (written by `infra/up.sh`, gitignored).

## 1. Deploy, update, tear down

```bash
infra/up.sh             # create/refresh RG + all resources, write .env (idempotent)
infra/governance.sh     # once per subscription: tag-inheritance policy (operator decision; changes the subscription)
infra/down.sh           # delete the resource group (asks first)
az cognitiveservices account list-deleted -o table      # then purge soft-deleted AI accounts if re-creating names
```

Before `down.sh`, export anything you want to keep: custom models live in the Document Intelligence resource
and are deleted with it; blobs and SQL tables too.

## 2. Process a household's documents (the real-world routine)

Once a year, ~60 days before renewal (Texas insurers must give 60 days' notice of a ≥10% increase):

1. Put PDFs in `private/letters/`: the full renewal packet + every quote (binding quotes preferably).
2. Run:
   ```bash
   cd src
   ../.venv/bin/python upload_letters.py          # → blob raw-private (hashed names) + manifest rows
   # edit private/manifest.csv: doc_type (declarations / rating-info / quote / quote-mailer / ad),
   #   and pages_range for long packets (e.g. 19-20) so only useful pages are analyzed
   ../.venv/bin/python run_layout.py              # prebuilt-layout (skips done docs; --force to redo)
   ../.venv/bin/python run_layout.py --model ins-neural-v1   # custom model (optional second opinion)
   ../.venv/bin/python normalize.py               # → private/out/{fields,review_queue,comparison}.csv
   ../.venv/bin/python redact.py                  # PII-redacted text → private/redacted/
   ../.venv/bin/python publish_curated.py         # → blob curated/ (Parquet + run metadata)
   ```
3. Work the review queue (`private/out/review_queue.csv`): fix values, note new label variants in
   `schema/fields.yaml` synonyms.
4. Read `comparison.csv`: only rows with `apples_to_apples = yes` are real price comparisons. For the others,
   ask the agent to re-quote with the current policy's limits and deductibles (incl. wind/hail %).
5. Switch effective on the renewal date (no gap, no mid-term fee); new insurer sends evidence of insurance to
   the mortgagee; confirm the lender received it.

## 3. Synthetic data, training, evaluation

```bash
.venv/bin/python src/synth/kits.py                       # carrier wording via Foundry (cached; --refresh)
.venv/bin/python src/synth/generate.py                   # 96 base docs (C5/C6 = test only)
.venv/bin/python src/synth/generate.py --augment 150 --seed 101   # augmented TRAIN docs (A###)
cd src
../.venv/bin/python synth_layout.py                      # layout OCR for all synthetic docs
../.venv/bin/python build_training_set.py --prefix train-v2/ --with-augmented
../.venv/bin/python train_custom.py --model-id ins-neural-v2 --prefix train-v2/   # ~25-40 min
../.venv/bin/python synth_layout.py --model ins-neural-v2   # analyzes TEST docs only
../.venv/bin/python eval_extraction.py --method ins-neural-v2
../.venv/bin/python calibrate.py                         # review threshold from the trade-off curve
```

Rules: never train on test carriers (C5, C6) or real letters; compare models only on the same test docs;
never report a trained model's score on its own training docs.

## 4. Refresh the dashboard

```bash
cd src
../.venv/bin/python build_dashboard_tables.py --methods rules,ins-neural-v1   # → data/dashboard/*.parquet
../.venv/bin/python build_review_feedback.py       # → fact_review (app decisions + simulated history)
../.venv/bin/python pull_app_telemetry.py          # → fact_app_event (Application Insights → Parquet)
# once, to populate usage before real traffic exists: ../.venv/bin/python simulate_app_telemetry.py
../.venv/bin/python load_sql.py                     # → Azure SQL schema dash (full refresh)
../.venv/bin/python publish_powerbi.py              # → Power BI push dataset, updated in place
```

`publish_powerbi.py --recreate` is needed when tables or relationships change. It creates the new dataset,
**rebinds every report** that used the old one (same names, so visuals keep working), then deletes the old one.

The report itself is code: `build_report.py` writes `powerbi/report/` (PBIR JSON: 3 pages, 31 visuals) and
uploads it with the Fabric item-definition API (works in this PPU workspace, no Desktop needed).

```bash
../.venv/bin/python build_report.py            # generate + publish (overwrites the report in the service)
../.venv/bin/python build_report.py --pull     # after editing in the browser: save the service version to git
```

Edited in the browser? Run `--pull` before the next `build_report.py`, or the browser edits are overwritten.
`powerbi/backup/` keeps earlier versions. Export to PDF/PNG via API needs dedicated capacity (Fabric/Premium),
so evidence images come from the browser: sign in as the work account, screenshot the `.displayArea` element of
each page (Playwright, viewport 2000×1200, wait ~15 s per page) → `docs/img/powerbi/*.png`, then combine them into
`insurance-docai-dashboard.pdf` with Pillow. Only the simulated `demo` household appears on the homeowner page.

## 4b. Databricks lakehouse (Unity Catalog medallion)

```bash
# one-time: install the Databricks CLI and log in to Free Edition in a browser
#   (no sudo: download databricks_cli_<v>_linux_amd64.zip from github.com/databricks/cli/releases,
#    check it against the SHA256SUMS file, unzip, install -m 755 databricks ~/.local/bin/)
databricks auth login --host https://<your-free-edition>.cloud.databricks.com --profile free   # base URL only, no /?o=…
# each refresh
.venv/bin/python src/export_landing.py --methods rules,ins-neural-v1,ins-neural-v2   # landing files (synthetic)
cd databricks
databricks bundle deploy -t free --profile free        # schemas, volume, job, wheel
databricks fs cp -r --overwrite ../data/landing dbfs:/Volumes/workspace/bronze/landing --profile free
databricks bundle run -t free docai_lakehouse --profile free
.venv/bin/python -m pytest -q tests                    # local Spark tests (no workspace needed)
```

Compute: serverless on Free Edition (nothing to stop). On Azure (`-t azure`), each run creates a single-node
**job** cluster that terminates when the run finishes; don't create all-purpose clusters (if you must, set
auto-termination to 10 minutes). Azure Databricks on a Free Trial subscription with a personal-account tenant
can't use Unity Catalog (workspace not attached to a Databricks account), so the workspace is off by default:
`deployDatabricks=false` in `infra/main.bicep`. After upgrading to pay-as-you-go: set it true, run `up.sh`,
sign in to https://accounts.azuredatabricks.net with the work account (Global Admin), create a metastore,
attach the workspace, then `bundle deploy -t azure`.

## 4c. Review queue app (Power Apps, browser)

```bash
cd src
../.venv/bin/python review_queue.py --reviewer willie@<tenant>.onmicrosoft.com   # schema review + queue + grants
../.venv/bin/python review_queue.py --score     # reviewer decisions vs the synthetic answer key
../.venv/bin/python review_queue.py --reset     # reload queue (clears decisions + history)
```

Building the app in make.powerapps.com: `docs/POWERAPPS.md`.

### Power Apps as code (Power Platform CLI)

The app lives in the Dataverse solution **InsuranceDocAI** (publisher prefix `idoc`); its sources are in
`powerapps/src/` (screen: `Src/Screen1.fx.yaml`).

```bash
# one-time: .NET 10 + CLI, then sign in as the work account (device code, private browser window)
sudo apt-get install -y dotnet-sdk-10.0 && dotnet tool install --global Microsoft.PowerApps.CLI.Tool
~/.dotnet/tools/pac auth create --deviceCode --environment <environment-id>
# each change
powerapps/deploy.sh install  # new environment: build the solution from git and import it
powerapps/deploy.sh pull     # environment → powerapps/src (after editing in Studio)
powerapps/deploy.sh push     # powerapps/src → solution import + publish (same app, in place)
```

Close Power Apps Studio before `push` (an authoring session locks the app for ~15 min). `push` stops if the app
changed in the environment since the last pull/push, so browser edits aren't overwritten. Players pick up a
new version after **Refresh**; a changed connection asks once more to **Allow** data access.

## 5. Costs and tags

```bash
.venv/bin/python tools/finops.py        # tag compliance (exit 1 on violations), budgets, month-to-date cost
az bicep build -f infra/main.bicep --outfile /tmp/main.json && python tools/check_template_tags.py /tmp/main.json
```

Budgets on the RG: $50 / $150 / $250 monthly (email). Idle cost is ~$0: SQL free offer auto-pauses,
Document Intelligence/Language/Foundry are pay-per-use, Log Analytics capped at 0.1 GB/day.
Cost data for a new subscription appears after ~24-48 h.

## 6. Known issues and fixes

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `RegionDoesNotAllowProvisioning` for SQL | Trial subscriptions are blocked from new SQL servers in many US regions | Query `Microsoft.Sql/locations/<loc>/capabilities` for `status: Available` (centralus worked) |
| SQL name "already exists in location eastus" | A failed create leaves the name reserved | Use a new name (suffix `-cus`) |
| `RequestConflict` deploying Foundry | Project and model deployment created in parallel | `dependsOn` serializes them (in main.bicep) |
| Power BI: "Fabric trial isn't available" | Tenant/account not eligible | Power BI PPU trial + push dataset; Fabric later (F2 ≈ $0.36/h) |
| `publish_powerbi.py`: table not found | Push datasets can't add tables in place | `--recreate` (rebinds reports automatically) |
| Power Apps Patch: "cannot have any enabled triggers if the statement contains an OUTPUT clause" | Power Apps' SQL connector updates with OUTPUT; SQL forbids that on tables with triggers | No triggers on app-edited tables; audit via a temporal table (`review.item_history`) |
| `dotnet tool install Microsoft.PowerApps.CLI.Tool`: "DotnetToolSettings.xml was not found" | CLI 2.x targets .NET 10 | Install `dotnet-sdk-10.0` |
| Adding the app to a solution: "locked by user" | Power Apps Studio authoring session | Close Studio, wait ~15 min |
| Canvas app not found in Dataverse (`canvasapps` empty) | App created outside a solution | Solutions → Add existing → App → Canvas app → **Outside Dataverse** |
| Power BI `ExportTo`: "not on dedicated capacity" | Export API needs Premium/Fabric capacity | Screenshot in the browser |
| Measure creation fails: "column with the same name" | Measure names clash with columns (case-insensitive) | Rename the measure |
| F0 Document Intelligence slow / 429 | Free tier rate limit | Script retries; S0 since 2026-11-06 |
| GitHub MCP server 400 "Authorization header badly formatted" | `GITHUB_PERSONAL_ACCESS_TOKEN` unset | `export GITHUB_PERSONAL_ACCESS_TOKEN="$(gh auth token)"` in `~/.bashrc`, restart Claude Code |
| Databricks API "Did not find workspace with specified org ID" | Free Trial + personal-account tenant: workspace not attached to a Databricks account | Use Databricks Free Edition (or pay-as-you-go + work-account tenant) |
| accounts.azuredatabricks.net: AADSTS500200 | Signed in with the personal (Gmail) account | Private window, work account `willie@<tenant>.onmicrosoft.com` |
| accounts.azuredatabricks.net: "unexpected error connecting to Databricks" (work account, Global Admin) | Free Trial subscription can't create a Databricks account | Free Edition now; retry after pay-as-you-go |
| Databricks `trial` SKU refused | "Trial workspace creation is not supported for Azure Free Trial" | `premium` SKU |
| PySpark `toPandas` fails on Python 3.12 (`distutils`) | removed from stdlib | `pip install setuptools` |
| CloudBurn "No findings" on Azure templates | CloudBurn rules are AWS-only | `tools/check_template_tags.py` + `tools/finops.py` |

## 7. Privacy checklist before anything goes public

See `docs/PRIVACY.md`. Short form: `git ls-files` has nothing from `private/`; grep tracked files for every
line of `private/pii_terms.txt`; run `redact.py` over anything derived from real letters; publish real-letter
results only as aggregates; dashboard household page uses the simulated `demo` household.
