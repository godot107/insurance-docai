# Results (synthetic benchmark, 2026-11-06)

96 synthetic one-page documents (8 fictional carriers, 6 layouts, ~half "scanned"), exact ground truth.
**Test = 2 carriers / 2 layouts (postcard, renewal notice) never seen in training: 24 docs, 120 printed fields.**
The custom model is scored only on test docs (its own training docs would score 100% by memorization).

| Held-out layouts | Rules on prebuilt-layout | Custom neural model (`ins-neural-v1`) |
| :--- | :--- | :--- |
| Recall (printed fields extracted correctly) | 77.5% | **93.3%** |
| Precision (extracted values that are correct) | 77.5% | 75.7% |
| Wrong values | 27 | 6 |
| Made-up values (field not on the page) | 0 | 30 |
| Confidence separates right from wrong? | **No**: wrong 0.994 vs right 0.990 | **Yes**: right 0.91, wrong 0.83, made-up 0.58 |
| Fields flagged for human review | 15.8% | 29.3% |
| Errors caught by review (range/ratio/confidence rules) | 70% | **95%** |
| Silent errors (wrong + unflagged) | 8 | **2** |
| Documents with nothing to review | 42% | 0% |

What it means:
- Prebuilt-layout confidence measures OCR, not whether a value belongs to the field. A "send low confidence
  to a human" policy would pass every one of the rules' errors. Range and ratio checks are what catch them.
- The custom model generalizes to unseen layouts much better (93% vs 78% recall) and its confidence is
  usable for routing (at 0.80: 106 fields kept with 4 bad; 10 correct ones sent to review).
- Its failure mode is different: it fills fields the document doesn't print (B, D, F on postcards and
  renewal notices) at low confidence. The trade-off is fewer silent errors for more review work.
- Rules on in-distribution layouts: 95.7% recall (they were written from real mailers, not fitted to these).

Caveats: synthetic documents are cleaner and more regular than real mail; 24 test docs is small; real-letter
accuracy needs hand labels (phase 3). Training cost: 1 neural build (26 min) on F0.

# Phase 3: review threshold, real letters, PII (2026-11-06)

## Choosing the review threshold (synthetic test layouts; target: ≤2% of values wrong and unflagged)

| Confidence bin | Rules: share correct | Custom: share correct |
| :--- | :--- | :--- |
| ≤ 0.5 | (none) | 8% |
| 0.5–0.8 | (none) | 28–33% |
| 0.8–0.95 | 100% (n=1) | 89–91% |
| 0.95–1.0 | 76–86% | 100% |

- Rules + prebuilt-layout: confidence carries no signal (most errors sit at ≥0.99). Reaching ≤2% silent errors
  means reviewing **everything**; the range/ratio rules alone leave 6.7% silent.
- Custom model: **cutoff 0.78 + schema rules → 29.7% of values reviewed, 1.4% silent errors, 94% of errors caught.**
  (`src/calibrate.py`, `samples/synthetic/eval/calibration.json`)

## Real letters (11 household docs, 79 printed fields, provisional)

| | Rules | Custom (trained on synthetic only) |
| :--- | :--- | :--- |
| Printed fields correct | 98.7% | 97.5% |
| Silent errors | 0 | 0 |
| Fields flagged for review | 2 | 23 |

Caveats: the rules were written from these same letters (in-sample), and draft labels come from method
agreement plus source-text checks of the 6 disagreements; Willie's confirmation makes them final.
The synthetic-trained custom model transferring at 97.5% to real scans is the notable result; its cost is
a heavier review load on real mail.

## PII redaction (real letters)

Three layers (`src/redact.py`): Azure AI Language PII (≈ AWS Comprehend DetectPiiEntities), then
label-anchored IDs ("Policy Number", "Loan #", "Reference#", ...), then an optional household deny-list.
- Service alone missed 4 known identifiers, all insurer-specific numbers (policy, mortgage loan).
- The generic ID layer (no per-household setup) brought misses to 1 (a policy number inside a payment-coupon
  scan line with no label); the deny-list to 0.
- Non-personal categories (Organization, DateTime) are kept: carriers and policy dates are analysis data.

# Phase 4a: Databricks lakehouse + review router (2026-11-06; deployed on Databricks Free Edition)

- Unity Catalog medallion (`databricks/`): bronze (raw landing JSONL + ingest metadata) → silver (typed,
  latest run per method/doc/field, DQ quarantine with reasons, 5% quality gate) → gold (extraction_eval,
  method_metrics, threshold_curve, review_queue). On local Spark it reproduces this file's numbers exactly
  from the raw landing files (tested).
- Review router (gradient boosting on confidence, validity-rule hit, scan, layout, field, value size;
  leave-one-carrier-out), compared at the SAME review budget as threshold 0.78 + validity rules:

| | Rules extraction | Custom model |
| :--- | :--- | :--- |
| Ranks errors (AUC): router / confidence alone | **0.91** / 0.53 | 0.94 / 0.94 |
| Silent errors: threshold + rules policy | 13 | **2** |
| Silent errors: router policy | 19 | 5 |
| Silent errors: hybrid (rules always + router for the rest) | 12 | 3 |

  Negative result, reported as such: the router ranks far better than raw OCR confidence, but with 8 carriers
  it doesn't beat a calibrated threshold + validity rules at small review budgets on unseen carriers. For the
  custom model, its own confidence is already as informative as the router. Keep the router as a ranking aid
  and re-test once real review decisions accumulate.
- **Deployed run (Databricks Free Edition, serverless, 2026-11-06):** job `insurance-docai-lakehouse` built
  `workspace.{bronze,silver,gold}` (14 Delta tables incl. `silver.predictions_quarantine`); `gold.method_metrics`
  matches this file exactly (rules test recall 77.5%, v1 93.3%/75.7%, v2 95.0%/91.9%). Router logged to MLflow
  and registered as UC model `workspace.gold.review_router` v1. That run pools rules + v1 + v2 (1,028 values,
  89 errors) at threshold 0.78: AUC router 0.95 vs confidence 0.70; at the same 8.9% review budget, silent
  errors 16 (threshold + rules) vs 22 (router) vs 22 (hybrid). Same conclusion as above.
- Azure Databricks premium workspace deployed but unreachable ("Did not find workspace with specified org
  ID"): Free Trial + personal-account tenant workspaces aren't attached to a Databricks account, so no Unity
  Catalog. Signing in to the Databricks account console with a Global Admin work account in the same tenant
  also failed. Workspace deleted; Bicep keeps it behind `deployDatabricks=false` for a pay-as-you-go
  subscription. Target is Databricks Free Edition (serverless; nothing left running).

# Phase 2b: augmentation → ins-neural-v2 (2026-11-06)

150 augmented TRAIN letters (train carriers only): 1-4 fields omitted (70), distractor numbers (89), labels
stacked above values, carrier wording on other layouts, harsh scans (72-115 dpi, ±4° skew, 180° flips,
perspective, shadows, fax binarization). 29 letters dropped from training because OCR couldn't read a printed
value (26 of them harsh scans: 34% of harsh scans had at least one unreadable value). v2 trained on 193 letters.

| Held-out test layouts (same 24 letters) | v1 | **v2** |
| :--- | :--- | :--- |
| Recall | 93.3% | **95.0%** |
| Precision | 75.7% | **91.9%** |
| Made-up values | 30 | **5** |
| Wrong values | 6 | 5 |
| Cutoff for ≤2% silent errors | 0.78 | 0.44 |
| **Values sent to review at that cutoff** | 29.7% | **6.5%** |
| Silent errors at that cutoff | 1.4% | 1.6% |

| Real letters (79 printed fields, provisional labels) | rules | v1 | v2 |
| :--- | :--- | :--- | :--- |
| Correct | 98.7% | 97.5% | 97.5% |
| Made-up values | 0 | 3 | **0** |
| Fields flagged | 2 | 23 | 20 |
| Silent errors | 0 | 0 | 0 |

Caveat: the cutoffs were chosen on the same test letters they're reported on (optimistic). See Phase 3b.

# Phase 3b: honest threshold check (2026-11-06)

Choose the cutoff on one unseen carrier, score it on the other, then swap (`calibrate.py`, `cross_fit` in
`calibration.json`; target ≤2% silent errors, schema rules on):

| Method | Chosen on C5 (postcard) → scored on C6 | Chosen on C6 (renewal notice) → scored on C5 | Pooled |
| :--- | :--- | :--- | :--- |
| rules | cutoff 0.00: review 11%, **silent 11%** | 1.00: review 100%, silent 0% | review 47%, silent 6.7% |
| v1 | 0.70: review 18%, **silent 6.0%** | 0.94: review 45%, silent 0% | review 30%, silent 3.4% |
| v2 | 0.00: review 3%, **silent 9.2%** | 0.50: review 4%, silent 0% | review 3%, silent 5.6% |

- **The in-sample "6.5% review at ≤2% silent" for v2 doesn't survive.** A cutoff learned on one layout doesn't
  carry over to another: the postcard is easy (v2 made 1 error in 48 values, so "review nothing" looks fine),
  the renewal notice is not (9 errors in 76).
- What the harder layout actually needs (v2, C6 alone): cutoff 0.50 → review 10.5%, 1 silent error; 0.95 →
  review 61%, 0 silent. v1 needs 0.94 on C6.
- Two layouts × 12 docs is a small sample, so these numbers are indicative, not precise.
- **Policy consequence (new-layout burn-in):** when documents arrive in a layout the model hasn't seen, review
  every value of the first N documents, set that layout's cutoff from those reviews, then switch to
  threshold routing. That is the review-to-retraining loop (REVIEW.md #5) applied to thresholds. Until then a
  conservative global cutoff (0.95 → ~50% review for v2, 0 silent errors on both test layouts) is the safe default.

# Phase 5 MVP: Power Apps review queue (2026-11-06)

- Canvas app (browser) on Azure SQL `review.item` (17 v2-flagged values, 11 synthetic test letters), Entra ID
  connection, least-privilege grants, temporal-table audit (`review.item_history`), hidden answer key.
- Button test through the published app: Accept (S053 dwelling 580,500), Save correction (S053 premium
  580,500 → 4,097.25), Not on document (S063 medical 2,500, a made-up value), empty correction → warning, no
  write. `review_queue.py --score`: 3/3 decisions correct; history shows open → decision per item with the
  signed-in work account. Queue reset afterwards for the demo.
- Screenshot: `docs/img/powerapps/review-app.png`.

