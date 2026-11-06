# Quickstart: run it yourself

You don't need any real insurance letters. The repo ships everything synthetic: 246 generated letters from 8 fictional
insurers (`samples/synthetic/pdf`), their exact answers (`labels/`), the Document Intelligence output for each
(`di/`), and the evaluation results (`eval/`). Bring your own letters only if you want to (step 8).

Pick a depth:

| Path | Needs | You get |
| :--- | :--- | :--- |
| **A. Laptop only** | Python 3.12, Java 17+ (for local Spark) | Unit tests, the lakehouse transforms on local Spark reproducing every number in `docs/RESULTS.md`, the dashboard tables as Parquet |
| **B. Azure** | An Azure subscription (the free trial works) | Infrastructure, Azure SQL serving layer, live Document Intelligence runs, custom model training |
| **C. Full stack** | B + a work/school account + Databricks Free Edition | Power BI report, Power Apps review app, Databricks job |

---

## Prerequisites

| Tool | Why | Install |
| :--- | :--- | :--- |
| Python 3.12 + venv | Pipeline | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |
| Azure CLI + Bicep | Infrastructure | `az bicep install` |
| Java 17+ | Local Spark tests | distro package |
| Databricks CLI | Lakehouse deploy | release zip from github.com/databricks/cli (see RUNBOOK §4b) |
| .NET 10 + Power Platform CLI | Review app deploy | `dotnet-sdk-10.0`, then `dotnet tool install --global Microsoft.PowerApps.CLI.Tool` |

Accounts:

- **Azure subscription.** A free trial is enough. On trials, several US regions refuse new SQL servers; the
  template defaults SQL to `centralus` (`sqlLocation` parameter). Keep the spending limit on.
- **Work or school (Entra ID) account** for Power BI and Power Apps; personal Microsoft accounts can't use them.
  On a trial tenant, create a user in Entra ID (e.g. `you@<tenant>.onmicrosoft.com`) and use it for both.
  Power BI: start a Pro or Premium Per User trial. Power Apps: the free **Developer Plan**.
- **Databricks Free Edition** (databricks.com/learn/free-edition). Azure Databricks on a free-trial subscription
  can't attach Unity Catalog, which is why the bundle targets Free Edition (`-t free`).

---

## A. Laptop only (no cloud, $0)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q tests                                    # normalizer, redaction, label locator
cd src && ../.venv/bin/python build_dashboard_tables.py --methods rules,ins-neural-v1,ins-neural-v2
../.venv/bin/python calibrate.py                             # threshold sweep + cross-layout check
../.venv/bin/python export_landing.py --methods rules,ins-neural-v1,ins-neural-v2
cd ../databricks && python3 -m venv .venv && .venv/bin/pip install "pyspark==3.5.*" pytest pandas pyarrow scikit-learn setuptools
.venv/bin/python -m pytest -q tests                          # bronze → silver → gold on local Spark, checks RESULTS.md numbers
```

## B. Azure

```bash
az login
infra/up.sh                       # resource group + all resources (Entra ID only, tagged); writes .env
.venv/bin/python tools/finops.py  # tags, budgets, month-to-date cost
cd src
../.venv/bin/python load_sql.py   # dashboard tables → Azure SQL (dash schema)
```

Re-run extraction yourself (optional, costs cents to a few dollars on S0):

```bash
../.venv/bin/python synth_layout.py                          # prebuilt layout on all synthetic letters
../.venv/bin/python build_training_set.py --prefix train-v2/ --with-augmented --skip-incomplete
../.venv/bin/python train_custom.py --model-id ins-neural-v2 --prefix train-v2/                   # ~25-40 min
../.venv/bin/python synth_layout.py --model ins-neural-v2
../.venv/bin/python eval_extraction.py --method ins-neural-v2
```
Full order and options: [RUNBOOK §3](RUNBOOK.md#3-synthetic-data-training-evaluation).

## C. Full stack

**Power BI** (work account, separate az profile so your Azure login is untouched):
```bash
AZURE_CONFIG_DIR=~/.azure-powerbi az login --use-device-code --allow-no-subscriptions --tenant <tenant-id>
.venv/bin/python src/load_sql.py --reader you@<tenant>.onmicrosoft.com
cd src && ../.venv/bin/python publish_powerbi.py --recreate && ../.venv/bin/python build_report.py
```

**Review app** (Power Apps):
```bash
.venv/bin/python src/review_queue.py --reviewer you@<tenant>.onmicrosoft.com    # queue, grants, page images
~/.dotnet/tools/pac auth create --deviceCode --environment <your-developer-environment-id>
powerapps/deploy.sh install        # builds the solution from git, points it at YOUR SQL server (.env), imports
```
Open the app once in make.powerapps.com to create the SQL connection (Microsoft Entra ID) and allow it.
Prefer clicking? [POWERAPPS.md](POWERAPPS.md) builds the same screen by hand.

**Databricks** (Free Edition):
```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile free
cd databricks && databricks bundle deploy -t free --profile free
databricks fs cp -r --overwrite ../data/landing dbfs:/Volumes/workspace/bronze/landing --profile free
databricks bundle run -t free docai_lakehouse --profile free
```

## 8. Your own letters (optional, stays private)

Put PDFs in `private/letters/` (gitignored), then follow [RUNBOOK §2](RUNBOOK.md#2-process-a-households-documents-the-real-world-routine):
upload → layout → normalize → review → compare. Nothing under `private/` is ever committed; read
[PRIVACY.md](PRIVACY.md) before sharing any result derived from real documents.

## Clean up

```bash
infra/down.sh                                     # deletes the resource group (asks first)
az cognitiveservices account list-deleted -o table   # purge soft-deleted AI accounts if you redeploy the same names
```
Idle cost while it exists is about $0: SQL auto-pauses, AI services are pay-per-use, logging is capped.

## Known gotchas

See the table in [RUNBOOK §6](RUNBOOK.md#6-known-issues-and-fixes): trial SQL regions, the Databricks Free Trial
limitation, Power BI push-dataset limits, the Power Apps trigger/`Patch` conflict, `pac` needing .NET 10, and more.
