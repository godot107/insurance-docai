# Dashboard design: who looks at what

One semantic model (curated Parquet → star schema), three pages for three audiences. Every KPI is computable
from tables this pipeline already produces (`fields`, `review_queue`, `comparison`, `_runs`, eval rows) plus
Azure cost data.

## Data model

| Table | Grain | Source |
| :--- | :--- | :--- |
| `fact_field` | document × field: value, confidence, method, flags, needs_review, reviewed_value | `fields` (+ review decisions later) |
| `fact_quote` | document: premium, coverages, deductibles, apples_to_apples, vs_current | `comparison` |
| `fact_run` | pipeline run: when, model, schema version, row counts, duration | `_runs/*.json` |
| `fact_eval` | synthetic doc × field: truth, prediction, outcome, confidence | `samples/synthetic/eval/*_rows.csv` |
| `fact_cost` | day × service × project tag: cost | Cost Management export |
| `dim_document`, `dim_carrier`, `dim_field`, `dim_household`, `dim_date` | | manifest, kits, schema |

## Page 1: Household ("should I switch?")

Audience: the homeowner. Question: *is my renewal fair, and which quote is a real alternative?*

- Current premium, renewal date, days left to decide
- Best apples-to-apples quote and the savings ($ and %)
- Quotes table: premium, premium per $1k of dwelling, coverage gaps vs current, deductible differences,
  "comparable?" flag with the reason when it isn't
- Premium history by year (once prior declarations are loaded)
- Open items: fields waiting for human review before the comparison can be trusted

## Page 2: Operations ("is the pipeline healthy?")

Audience: product owner / ops lead running intake at scale.

- Documents processed (by day, type, carrier), pages consumed vs free-tier/quota
- **Straight-through rate**: share of documents with zero fields needing review
- Review queue size, age, and reasons (low confidence, out of range, ratio, missing, conflict)
- Field accuracy on the synthetic benchmark by layout, split (seen vs unseen layouts), and scanned vs clean
- **Silent-error rate**: wrong values that would pass the review rules (the risk the client cares about)
- Run failures and latency (Log Analytics)

## Page 3: Review feedback & drift ("are reviewers keeping up, and is the model changing?")

Audience: ops lead and model owner. Source: `dash.fact_review` (`src/build_review_feedback.py`): one row per value
sent to a person, from the Power Apps queue (`source = app`) plus a simulated reviewer replaying the project's
history on real benchmark outcomes (`source = simulated`, clearly labelled).

- Accept / correct / not-on-document rates (feedback on the model, per field, layout, model version)
- **No reply**: values still open and how long they've waited; median hours to a decision
- **Drift**: decision mix by week; a jump in "not on document" (made-up values) or in corrections after a model or
  layout change is the retraining trigger
- Reviewer agreement with the answer key (synthetic data only) and changed decisions (from the temporal history)

## Page 4: Review app usage ("how is the review tool used?")

Audience: ops lead / product owner. Source: `dash.fact_app_event` (`src/pull_app_telemetry.py` from Application
Insights `AppTraces`). The Power Apps app sends `Trace()` events: `queue_opened`, `item_viewed`,
`review_decision` (with `seconds_on_item`); Power Apps adds sessions and screen views automatically.
`src/simulate_app_telemetry.py` sends matching simulated events (`sim=true`) through the same pipeline.

- Sessions, active reviewers (pseudonyms), items viewed vs decisions logged, **skip rate** (viewed, not decided)
- Median seconds per decision, by decision type (corrections cost the most reviewer time)
- Per-reviewer throughput; no location, IP or user identifiers are stored

## Page 5: Finance ("what does it cost and what does it return?")

Audience: CFO / budget owner.

- Month-to-date Azure cost by service and by `project` tag vs budget lines ($50 / $150 / $250)
- **Cost per document** and cost per field extracted (Document Intelligence pages + model tokens + storage)
- Review minutes avoided (straight-through docs × assumed manual minutes per doc) → labor $ avoided
- Household savings identified (sum over households of current − best comparable quote)
- Unit economics at scale: cost per 1,000 documents on F0 vs S0 vs custom model

## Built (2026-11-06)

`src/build_report.py` generates the three pages (PBIR, `powerbi/report/`) on the push dataset; screenshots and a
PDF are in `docs/img/powerbi/`. Operations defaults to ins-neural-v2 on the unseen (`test`) layouts, one method
at a time. The finance chart compares methods on the same 24 unseen-layout docs. Labor $ avoided doesn't price
silent errors yet, which is why rules and v2 look alike there; the Operations page shows that difference.

## Tooling choice

- **Power BI Desktop** (free, Windows) builds the report from the Parquet/CSV files with no capacity cost.
  This is the default.
- **Microsoft Fabric** adds a hosted Lakehouse + shared report. F2 pay-as-you-go is about $0.36/hour
  (~$263/month if left on), billed per second, and stops when paused. The 60-day trial generally needs a
  work/school (Entra) account, not a personal Microsoft account. Use a dedicated user in this tenant, or a
  paused F2 run only while building and demoing.
