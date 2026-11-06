# Data model: Azure SQL and Databricks Unity Catalog

Two stores, two jobs:

- **Azure SQL Database** (`docai`) is the **serving** layer: a small star schema for Power BI (`dash`) and the
  transactional review queue the Power Apps app writes to (`review`).
- **Databricks Unity Catalog** (`workspace` catalog on Free Edition; one catalog per environment in production) is the
  **lakehouse**: raw landing files → validated data → evaluation and model outputs, in a bronze / silver / gold
  medallion.

Column lists below were read from the live databases on 2026-11-06 (`INFORMATION_SCHEMA` in both), not written
by hand. Keys are logical unless marked enforced: the `dash` tables are a full-refresh reporting copy, so
referential integrity is checked in the pipeline and modelled as relationships in Power BI.

---

## 1. Azure SQL: `dash` schema (Power BI star schema)

Loaded by `src/load_sql.py` from `data/dashboard/*.parquet` (full refresh each run). The Power BI semantic model
(`src/publish_powerbi.py`) adds a computed `doc_key = method | doc_id` on both fact tables to relate them.

```mermaid
erDiagram
  fact_document["dash.fact_document"] {
    string method PK "rules | ins-neural-v1 | ins-neural-v2"
    string doc_id PK
    string carrier_id FK
    string carrier
    string layout
    string split "train | test (held-out layouts)"
    bit scanned
    datetime processed_at
    bigint pages
    bigint fields_flagged
    bit straight_through "no field needed review"
  }
  fact_field["dash.fact_field"] {
    string method FK
    string doc_id FK
    string field FK
    float truth "synthetic answer key"
    float pred "extracted value"
    float confidence
    string outcome "correct | wrong | spurious | missed | true_null"
    bit flagged
    string flag_reasons
    bit silent_error "wrong and not flagged"
    string carrier
    string layout
    string split
    bit scanned
  }
  dim_field["dash.dim_field"] {
    string field PK
    string type "money | percent | int"
    string group
  }
  dim_carrier["dash.dim_carrier"] {
    string carrier_id PK
    string carrier "fictional insurer"
    string layout
    string split
  }
  fact_threshold["dash.fact_threshold"] {
    string method PK
    float threshold PK
    bit rules PK "validity rules on/off"
    float review_rate
    float silent_error_rate
    float errors_caught
    bigint correct_sent_to_review
  }
  fact_quote["dash.fact_quote"] {
    string household_id PK "simulated demo household"
    string doc_id PK
    string carrier
    string doc_type "declarations | quote"
    float premium
    float coverage_a
    float coverage_c
    float coverage_e
    float wind_hail_pct
    float aop_deductible
    string apples_to_apples "yes | no | baseline"
    string notes
    bigint year_built
    bigint square_feet
    float premium_per_1k_a
    float vs_current_usd
    float vs_current_pct
    datetime renewal_date
  }
  dim_assumption["dash.dim_assumption"] {
    string name PK
    float value
    string unit
    string note
  }
  fact_review["dash.fact_review"] {
    string item_key PK "app-### or sim-###"
    string source "app | simulated"
    string method
    string doc_id
    string field
    string field_label
    string layout
    float confidence
    string reason "low confidence | validity rule"
    string decision "accepted | corrected | not_on_document | open"
    bit decided
    datetime created_at
    datetime decided_at
    datetime week
    float hours_to_decision
    float open_days "no-reply age"
    bit changed_mind "from temporal history"
    bit model_was_right
    bit reviewer_agrees "vs answer key"
  }
  fact_document ||--o{ fact_field : "method + doc_id"
  dim_field ||--o{ fact_field : field
  dim_carrier ||--o{ fact_document : carrier_id
```

| Table | Grain | Rows (2026-11-06) | Feeds |
| :--- | :--- | ---: | :--- |
| `fact_document` | method × document | 144 | Operations: straight-through rate, pages; Finance: cost, labor |
| `fact_field` | method × document × field | 1,043 | Operations: recall, precision, silent errors, flag reasons |
| `fact_threshold` | method × cutoff × rules on/off | 606 | Operations: review-load and silent-error curves |
| `fact_quote` | household × document | 9 | Homeowner page (simulated household only) |
| `fact_review` | value sent to a person | 97 | Review feedback & drift: decision, timing, no-reply age, agreement with answer key (`source` = app / simulated) |
| `dim_field` | field | 12 | Field names / groups |
| `dim_carrier` | carrier | 8 | Fictional insurers, layout, train/test split |
| `dim_assumption` | assumption | 6 | Unit prices and minutes behind the Finance measures |

DAX measures (Recall, Precision, Silent errors, Straight-through rate, Cost per document, Labor $ avoided, Current
premium, Best comparable quote, …) live in the semantic model, defined in `src/publish_powerbi.py`.

---

## 2. Azure SQL: `review` schema (Power Apps review queue)

Created by `src/review_queue.py`. This is the only part of the system a person writes to, so it carries the
controls: enforced primary keys, a status check constraint, a **system-versioned temporal table** for the audit
trail, and column-level grants.

```mermaid
erDiagram
  item["review.item (system-versioned)"] {
    int item_id PK "identity"
    string doc_id FK
    string method
    string field
    string carrier
    string layout
    float extracted_value
    float confidence
    string flag_reasons
    string status "open | accepted | corrected | not_on_document (CHECK)"
    float reviewed_value "app may update"
    string reviewer "app may update (User().Email)"
    datetime reviewed_at "app may update"
    string comment "app may update"
    datetime created_at
    string field_label "reviewer context"
    string field_help
    string reason_help
    string hint "from schema + same-letter values + calibration, never the answer key"
    string doc_context
    string other_values
    datetime valid_from "period start (hidden)"
    datetime valid_to "period end (hidden)"
  }
  item_history["review.item_history"] {
    int item_id
    string status
    float reviewed_value
    string reviewer
    datetime valid_from
    datetime valid_to
  }
  document["review.document"] {
    string doc_id PK
    string page_image "JPEG data URI, SYNTHETIC DEMO watermark"
  }
  answer_key["review.answer_key"] {
    int item_id PK
    float truth
    string outcome
  }
  document ||--o{ item : doc_id
  item ||--o{ item_history : "every prior version"
  item ||--|| answer_key : "scoring only"
```

| Table | Grain | Who can do what |
| :--- | :--- | :--- |
| `item` | one flagged value | Reviewer (work account): `SELECT`; `UPDATE` only on `status, reviewed_value, reviewer, reviewed_at, comment`. Operator: full |
| `item_history` | one superseded version of an item | Written by SQL Server itself; nobody edits it. `SELECT … FOR SYSTEM_TIME ALL` gives the full trail |
| `document` | one page image per document | Reviewer: `SELECT` |
| `answer_key` | one true value per item (synthetic) | **Operator only.** Lets `review_queue.py --score` grade reviewer decisions without showing the answer |

Why a temporal table and not a trigger: Power Apps' `Patch` sends `UPDATE … OUTPUT`, which SQL Server rejects on
tables with enabled triggers. System versioning records the same history with no trigger.

---

## 3. Databricks: Unity Catalog medallion

Built by the `docai_lakehouse` job (`databricks/`), all Delta tables, managed by Unity Catalog. Raw files land in
the volume `workspace.bronze.landing` (`src/export_landing.py` → `databricks fs cp`).

```mermaid
flowchart LR
  V[/"volume bronze.landing<br/>*.jsonl"/]
  subgraph B["bronze · as delivered + ingest metadata"]
    bp["predictions"]; bt["truth"]; bd["documents"]; bs["schema"]
  end
  subgraph S["silver · typed, deduplicated, validated"]
    sp["predictions"]; sq["predictions_quarantine<br/>+ dq_reasons"]; st["truth"]; sd["documents"]; sf["field_schema"]
  end
  subgraph G["gold · evaluation, metrics, model outputs"]
    ge["extraction_eval"]; gm["method_metrics"]; gt["threshold_curve"]; gr["review_queue"]; gx["router_runs"]
    M[["model gold.review_router<br/>(UC registry, MLflow)"]]
  end
  V --> bp & bt & bd & bs
  bp --> sp
  bp --> sq
  bt --> st
  bd --> sd
  bs --> sf
  sp & st & sd & sf --> ge
  ge --> gm & gt & gr
  ge --> M --> gx
```

Quality gate: the silver task fails the job if more than 5% of prediction rows land in quarantine.

```mermaid
erDiagram
  s_predictions["silver.predictions"] {
    string method PK
    string doc_id PK
    string field PK
    string run_id "latest run kept"
    string model_id
    string value_text "as extracted"
    double value "parsed"
    double confidence
    boolean rule_flag
    string _source_file
    timestamp _ingested_at
  }
  s_quarantine["silver.predictions_quarantine"] {
    string method
    string doc_id
    string field
    string value_text
    array dq_reasons "why the row failed validation"
  }
  s_truth["silver.truth"] {
    string doc_id PK
    string field PK
    double truth
  }
  s_documents["silver.documents"] {
    string doc_id PK
    string carrier_id
    string layout
    string split
    boolean scanned
    string scan_mode
    boolean augmented
  }
  s_field_schema["silver.field_schema"] {
    string field PK
    string type
  }
  g_eval["gold.extraction_eval"] {
    string method PK
    string doc_id PK
    string field PK
    string model_id
    double value
    double truth
    double confidence
    boolean rule_flag
    string outcome
    boolean is_error
    string carrier_id
    string layout
    string split
    boolean scanned
  }
  g_metrics["gold.method_metrics"] {
    string method PK
    string split PK
    long correct
    long printed
    long extracted
    long wrong
    long spurious
    long flagged
    long silent_errors
    double recall
    double precision
    double review_rate
    double threshold
  }
  g_curve["gold.threshold_curve"] {
    string method PK
    string split PK
    double threshold PK
    double review_rate
    double silent_error_rate
    long values
  }
  g_queue["gold.review_queue"] {
    string method
    string doc_id
    string field
    string layout
    double value
    double confidence
    string reason
    string outcome
  }
  g_router["gold.router_runs"] {
    string run_id PK "MLflow run"
    timestamp trained_at
    double auc_router
    double auc_confidence_only
    double review_budget
    double silent_errors_threshold_policy
    double silent_errors_router_policy
    double silent_errors_hybrid_policy
  }
  s_documents ||--o{ s_predictions : doc_id
  s_field_schema ||--o{ s_predictions : field
  s_truth ||--o| s_predictions : "doc_id + field"
  s_predictions ||--|| g_eval : "method + doc_id + field"
  g_eval }o--|| g_metrics : "aggregated by method + split"
  g_eval }o--|| g_curve : "swept by cutoff"
  g_eval ||--o| g_queue : "flagged rows"
```

| Layer | Table | Grain | Notes |
| :--- | :--- | :--- | :--- |
| bronze | `predictions`, `truth`, `documents`, `schema` | as delivered | Every column kept, plus `_source_file`, `_ingested_at` |
| silver | `predictions` | method × document × field | Typed `value`, latest `run_id` only, rows failing checks moved out |
| silver | `predictions_quarantine` | rejected row | `dq_reasons` array: `missing_key`, `unknown_field`, `value_not_numeric`, `negative_value`, `confidence_out_of_range` |
| silver | `truth`, `documents`, `field_schema` | key per table | Typed, deduplicated |
| gold | `extraction_eval` | method × document × field | Prediction joined to truth and document facts; `outcome`, `is_error` |
| gold | `method_metrics` | method × split | Recall, precision, silent errors at the review cutoff (matches `docs/RESULTS.md`) |
| gold | `threshold_curve` | method × split × cutoff | Review load vs silent errors |
| gold | `review_queue` | flagged value | What would go to a person |
| gold | `router_runs` | training run | Review-router cross-validation results, linked to the MLflow run |
| model | `gold.review_router` | version | Registered in Unity Catalog (v1 on 2026-11-06) |

**Lineage across stores.** Bronze rows carry `model_id` and `run_id`, so any gold metric or Power BI number traces
back to the extraction run that produced it. In the production design ([ARCHITECTURE.md](ARCHITECTURE.md)) the
review decisions in `review.item` flow back to the lake as labels for retraining, and Purview scans both Unity
Catalog and SQL for end-to-end lineage.
