# insurance-docai

AI document processing on Azure: home-insurance **renewal letters** (every carrier formats them differently)
→ Azure AI Document Intelligence → confidence-gated human review → structured premium/coverage data →
a benchmark model (TDI public data) that says whether a premium looks high for its coverage.

Built as a portfolio piece for Deloitte GPS "Project Analytics & Insights Engineer III"
(req 373221, https://apply.deloitte.com/en_US/careers/JobDetail/Data-Analytics-Insights-Specialist/373221;
JD text in `../resume-builder/data/jd_373221.txt`). Tools named in the JD: Document Intelligence,
Power Platform, Python, Databricks (+ Azure SQL preferred). Client framing: `docs/PROBLEM.md`.

## Run

```bash
infra/up.sh      # create/refresh RG + deploy infra/main.bicep, writes .env
infra/down.sh    # delete the RG (asks first); then purge soft-deleted Cognitive Services accounts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd src && ../.venv/bin/python upload_letters.py   # private/letters → blob raw-private + private/manifest.csv
../.venv/bin/python run_layout.py                  # prebuilt-layout → private/di/layout/*.json (skips done docs; --force)
../.venv/bin/python normalize.py                   # → private/out/{fields,review_queue,comparison}.csv
../.venv/bin/python publish_curated.py             # → blob curated/household=<id>/<table>/run=<ts>.parquet + _runs/<ts>.json
cd .. && .venv/bin/pytest -q tests
.venv/bin/python src/synth/kits.py                 # fictional carrier wording via Foundry gpt-4.1-mini (cached; --refresh)
.venv/bin/python src/synth/generate.py             # 96 synthetic docs + exact labels → samples/synthetic/
```

## Azure

- Subscription "Azure subscription 1" (free trial, **spending limit On**). Willie is OK going
  over $200 for this project, but upgrading to pay-as-you-go is **Willie's decision**. Never upgrade or remove the limit.
- RG `rg-insurance-docai-dev`, `eastus`, tags per `../../shared/TAGGING.md` (`managed-by=bicep`).
- RG-scoped monthly budgets `insurance-docai-{50,150,250}` → contact@willieman.com (actual + forecast at 100%).
- Providers registered 2026-11-05: Storage, CognitiveServices, Sql, Databricks, Insights, MachineLearningServices.
- Deployed 2026-11-05 (`infra/main.bicep`): storage `stdocaii777yv` (containers `raw-private`, `synthetic`;
  no public access, **no shared keys**), Document Intelligence F0 `di-docai-i777yv` (**no API keys**,
  `disableLocalAuth`). Auth = `az login` user via RBAC (Blob Data Contributor, Cognitive Services User);
  DI's managed identity reads blobs. Endpoints in `.env` (written by up.sh).
- Added 2026-11-06: Foundry resource `ai-docai-i777yv` (kind AIServices, Entra-only) + project
  `insurance-docai` + deployment `gpt-4.1-mini` (GlobalStandard, 20K TPM, pay per token; trial quota 200K).
  Log Analytics `log-docai-i777yv` (30-day retention, 0.1 GB/day cap) with diagnostic settings on DI + Foundry.
  Container `curated` = pipeline tables as Parquet + `_runs/` metadata. Container `synthetic` mirrors samples/synthetic.
- Document Intelligence F0 (free, 500 pages/mo, analyzes only the first 2 pages per doc) is available in eastus. Use F0 until it limits us.

## Decisions / constraints

- **PII rule** (`docs/PRIVACY.md`): real letters live only in `private/` (gitignored) and in a private blob
  container. Nothing public is derived from them except aggregate metrics and redacted/synthetic stand-ins.
  PUBLIC since 2026-11-06 as github.com/godot107/insurance-docai (fresh single-commit history; the earlier
  history is the private repo godot107/insurance-docai-private + private/insurance-docai-full-history.bundle).
  Commit author here = GitHub noreply email (repo-local git config). Re-run the PRIVACY.md checklist before
  adding anything derived from real letters or new screenshots.
- Ground truth: synthetic letters with planted values, so extraction accuracy is exact. Report the ~10
  real letters separately (they're harder than synthetic).
- Evaluate on **held-out layouts/carriers**, never a random split (same layout in train and test = leakage).
- Benchmark output is decision support ("worth shopping around?"), not a quote or insurance advice.
- No Deloitte/TDI branding or implied endorsement in public material.

## Pipeline design (built to scale to other households)

- `schema/fields.yaml` is the single source of truth: canonical fields (coverages A–F, premium, deductibles,
  property facts), label synonyms, types, plausible ranges/ratios, required-by-doc-type. New label variants
  go in the YAML, not code. The same names will be the custom-model labels, synthetic answer keys, SQL columns.
- `private/manifest.csv`: one row per document *segment* (`household_id`, `doc_id`, `doc_type`, optional
  `pages_range` so long packets only spend DI pages on the useful pages). doc_ids are stable (keyed by sha256).
  doc_types: declarations (baseline), rating-info (insurer's property facts), quote, quote-mailer,
  premium-notice, ad, superseded.
- `normalize.py`: KV pair match → text fallback; review flags = low confidence (<0.80 placeholder), range,
  ratio, missing-required, conflicting values. Comparison = every quote vs the household's latest
  declarations + property facts; "apples_to_apples" only when coverage and deductibles are known and match.

## Storage model

| Layer | Where | What |
| :--- | :--- | :--- |
| Raw | blob `raw-private/letters/<sha16>.pdf` | originals, named by hash (no personal file names) |
| Extraction | `private/di/layout/<doc_id>.json` | full DI output (text, tables, KV pairs, confidences) |
| Curated | blob `curated/household=<id>/<table>/run=<ts>.parquet` | fields, review_queue, comparison, manifest |
| Run metadata | blob `curated/_runs/<ts>.json` | run id, operator, schema version, model, row counts |
| Serving (phase 5) | Azure SQL Database | same tables, typed, for Power Apps / Power BI |

## Synthetic data (phase 1)

- 8 fictional carriers (`samples/synthetic/carriers.json`, wording from gpt-4.1-mini, names seeded to avoid
  real insurers), 6 layouts, 12 docs each = 96 one-page docs, ~half "scanned" (raster ~130 dpi, skew, blur,
  JPEG). Python plants every number via a documented rating formula (carrier rate × deductible/age/
  construction factors − discounts + fee), so labels are exact; `hidden_truth` keeps the formula inputs.
- Split by carrier: C5 (postcard) + C6 (renewal_notice) are **test only**: layouts never seen in training.
- Faker names/streets + real TX ZIPs only. Safe to publish.

## Phase 2 results + serving (2026-11-06)

- Results table: `docs/RESULTS.md`. Custom neural `ins-neural-v1` (trained on 72 train-carrier docs via
  `build_training_set.py` → `train_custom.py`): test recall 93.3% vs rules 77.5%, silent errors 2 vs 8.
- Azure SQL free offer `sql-docai-i777yv-cus` / db `docai` in **centralus** (eastus/eastus2/southcentralus/
  westus2/northcentralus restrict new SQL servers for this trial sub). Entra-only; operator = admin;
  `willie@<tenant>.onmicrosoft.com` = db_datareader. Schema `dash` = dashboard star tables.
- Power BI: work account `willie@<tenant>.onmicrosoft.com` (Fabric trial refused; PPU trial OK).
  No Desktop on Linux → `src/publish_powerbi.py` builds a **push dataset** "Insurance DocAI" in workspace
  `insurance-docai` from SQL (tables, relationships, DAX measures) and updates it in place (report bindings
  survive). Token from `AZURE_CONFIG_DIR=~/.azure-powerbi` az profile. Refresh chain:
  `build_dashboard_tables.py --methods rules,ins-neural-v1` → `load_sql.py` → `publish_powerbi.py`.
- Fabric later (trial refused for this tenant; F2 ≈ $0.36/h if needed).
- CloudBurn has no Azure rules → `tools/finops.py` (tags, budgets, MTD cost) + `tools/check_template_tags.py`
  (CI). Subscription tag-inheritance policy `infra/governance.sh` written, NOT deployed (operator runs it).

## Phase 3 (2026-11-06)

- `calibrate.py`: review cutoff from measured trade-off → custom 0.78 (+ schema rules); rules' confidence is useless.
- AI Language F0 `lang-docai-i777yv` (Entra-only) + `redact.py` (3 layers) → `private/redacted/`.
- Custom model run on real letters → `private/di/ins-neural-v1/`. Draft labels `private/labels/L*.json`
  (status DRAFT until Willie confirms); `private/labels_draft_compare.csv` = rules vs custom per field.
- Power BI push datasets can't gain tables in place → `publish_powerbi.py --recreate` (creates new, rebinds
  reports, then deletes old; done 2026-11-06, dataset id in private/NOTES.md). Report is code: `src/build_report.py`
  → `powerbi/report/` (PBIR) → Fabric updateDefinition (works on PPU). `--pull` saves browser edits.
  Willie's hand-built page (1 method slicer) backed up in `powerbi/backup/2026-11-06-hand-built/`.

## Databricks (phase 4a, 2026-11-06)

- `databricks/` = bundle: UC schemas bronze/silver/gold + volume bronze.landing, job `docai_lakehouse`
  (bronze → silver → gold → train_router), wheel `docai_lakehouse`. Targets: `free` (Free Edition, serverless,
  catalog `workspace`) and `azure` (single-node JOB cluster that terminates with the run; Willie: no idle clusters).
- Input: `src/export_landing.py` → `data/landing/*.jsonl` (synthetic only) → `databricks/push_landing.py`.
- Tests on local Spark: `cd databricks && .venv/bin/python -m pytest -q tests` (Java 21; setuptools needed for
  toPandas on py3.12). End-to-end test asserts gold reproduces docs/RESULTS.md.
- Azure Databricks `dbw-docai-i777yv` (premium; trial SKU refused on Free Trial subs; enableNoPublicIp=false to
  avoid a ~$32/mo NAT gateway) was unreachable: Free Trial → no Databricks account/UC.
  2026-11-06 tried accounts.azuredatabricks.net as the work account (same tenant, Global Admin): personal
  account refused (AADSTS500200), work account → "unexpected error" (ref a370ed2d-…). **Workspace DELETED
  2026-11-06**; `deployDatabricks` param (default false) in main.bicep. Return to Azure Databricks only
  after Willie decides to upgrade to pay-as-you-go.
- Databricks CLI v1.24.0 at ~/.local/bin. **Free Edition DEPLOYED + run OK 2026-11-06**: profile `free`
  (workspace URL in private/NOTES.md). Catalog `workspace`, schemas bronze/silver/
  gold, UC model `workspace.gold.review_router` v1. Free target has no `mode: development` (it renamed schemas
  to dev_<user>_*). Serverless env ships older sklearn → OneHotEncoder(sparse_output=False); bump the wheel
  version in pyproject.toml on code changes (serverless caches same-version wheels). Upload landing with
  `databricks fs cp -r --overwrite ../data/landing dbfs:/Volumes/workspace/bronze/landing --profile free`
  (push_landing.py needs databricks-sdk, not installed). Steps: RUNBOOK §4b.

## Data findings

- 2026-11-05: the 10 "letters" are **unsolicited competitor quote mailers** for the same house (L01–L08, L10)
  + 1 bundling ad (L09), not renewals from Willie's own insurer. They're image-only scans at ~130 dpi;
  prebuilt-layout word confidence averages 0.94–0.99. Mailers prefill year built / sq ft (likely from
  appraisal-district records). Current policy (full 24-page renewal packet) added 2026-11-05 as segments L12 (dec p19-20), L13 (rating
  p21), L14 (premium notice p15); L11 (page-1-only scan) superseded. Packet lists rating factors but **no
  per-item premium amounts**. Mailers' prefilled square footage is wrong on 2 of 2 that state it; none state
  deductibles, so no mailer is apples-to-apples yet. Household numbers live in `private/NOTES.md`.

## Roadmap

0. Foundation: RG, providers, budgets, repo (done 2026-11-05)
1. Synthetic letter corpus + ground-truth labels; hand-label real letters
2. Extraction: prebuilt-layout baseline → custom neural model; LLM comparison arm; field P/R on held-out layouts
3. Confidence calibration → review threshold (fields sent to a human vs remaining error); PII redaction (AI Language)
4. Benchmark premium model in Databricks + MLflow on TDI/NAIC/OpenFEMA/ACS data
5. Azure SQL + Power Apps review queue + Power Automate ingest flow + Power BI report
6. Notebook walkthrough, blog/report, PII audit, go public, demo, teardown

## Phase 5 MVP: Power Apps review queue (2026-11-06)

- Willie: keep it a simple MVP, browser (Tablet layout), not mobile.
- `src/review_queue.py` → SQL schema `review`: item (17 v2-flagged values, 11 synthetic test docs), document
  (page JPEG as data URI, no blob connector), item is a temporal table (history = audit; a trigger broke
  Power Apps Patch: OUTPUT clause), answer_key (truth, not granted
  to the app). Work account: SELECT item/document + column-level UPDATE on decision columns.
- App is built by Willie in the browser from `docs/POWERAPPS.md` (Developer Plan, SQL connector w/ Entra ID).
  Next: Power Automate intake flow.
- 2026-11-06: app is code. Solution `InsuranceDocAI` (publisher `insurancedocai`, prefix `idoc`) in the developer
  env; sources `powerapps/src` (pac canvas unpack); `powerapps/deploy.sh pull|push` (export → swap msapp → import;
  push refuses if live app changed since last sync). pac 2.14 needs .NET 10 (`dotnet-sdk-10.0`), auth profile =
  work account (device code). Fixes shipped: gallery Items filtered to open + Default = first open item,
  Refresh after Patch, friendly labels, layout. Close Studio before push (lock).
- Docs for req 373221: README.md maps JD → repo; docs/ARCHITECTURE.md = closed-network Azure reference
  architecture (private endpoints, Azure Databricks Premium + UC, Fabric/PBI + VNet gateway, PP managed env).
- 2026-11-06: Power BI page 3 "Review feedback & drift" (`src/build_review_feedback.py` → dash.fact_review;
  source app = live queue, simulated = seeded reviewer replaying rules→v1→v2 weeks on real outcomes). Measures in
  publish_powerbi.py (Accept/Correction/Not-on-document rate, No reply, Oldest open, Median hours, Reviewer
  agreement, Flagged but right, Changed decisions). Report now 4 pages; screenshots + PDF in docs/img/powerbi.
  Optional next: Application Insights + Trace() in the app for usage telemetry.

