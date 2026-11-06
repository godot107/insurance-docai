# Code and architecture review (2026-11-06)

Grounded in Willie's textbook library (citations: book, page). Status: **Fixed** = changed in this commit;
**Planned** = next phase; **Accepted** = deliberate for a dev/demo build, revisit for production.

## Defects found and fixed

| # | Where | Problem | Fix |
| :--- | :--- | :--- | :--- |
| D1 | `upload_letters.py` | New doc IDs were `row count + 1`; after splitting/removing manifest rows an ID could be reused | max existing ID + 1 |
| D2 | `publish_powerbi.py` | All tables' rows were deleted before any were re-pushed; a mid-run failure (happened once) emptied the dashboard | clear each table immediately before re-pushing it |
| D3 | `common.py` | Credential worked only where `az login` exists | managed identity when running in Azure, CLI otherwise |
| D4 | `build_training_set.py` | Docs with printed-but-unlocatable values were uploaded with partial labels (teaches the model to ignore printed values) | `--skip-incomplete`; reported counts now exclude skipped docs |
| D5 | tests | Redaction ID rule and the training-label locator had no tests | 2 tests added (8 total) |

## Process: data quality

1. **The range/ratio checks measure validity, not accuracy. Name them that way.** "Just because a data value is
   valid … does not mean it is correct … while validity is not accuracy, invalid values cannot be correct"
   (Sebastian-Coleman, *Measuring Data Quality*, p.105). That is exactly what the results show: 8 rules-method
   errors passed every range/ratio check. **Planned:** label review reasons in the dashboard as *validity*
   (range, ratio, type) vs *consistency* vs *confidence*, and report accuracy only against ground truth.
2. **Add cross-document consistency checks.** Two records of one real-world fact that disagree mean at least
   one is wrong (same source, p.104). This project already found mailers with the wrong square footage by
   comparing against the insurer's rating page. **Planned:** generalize: for one household, year built,
   square footage, and coverage A should agree across documents within tolerance; disagreement → review.
3. **Add an input-quality gate before extraction.** In the augmentation run, 34% of harsh scans had values
   OCR could not read at all. No downstream model can fix that. **Planned:** reject + "please rescan" when
   mean word confidence or effective dpi is below a threshold, measured separately from field review.
4. Track the DMBOK quality dimensions explicitly (DAMA-DMBOK, p.464): completeness (required fields
   present), validity (ranges), consistency (cross-document), timeliness (days to renewal), uniqueness
   (sha256 dedupe, already in place).

## Process: ML and human review

5. **Close the loop from review to training.** The textbook pattern routes low-confidence predictions to people,
   then uses the reviewed labels "to build a continuous pipeline for training and improving our … model …
   The more accurate our model becomes, the less reviews" (Fregly & Barth, *Data Science on AWS*, pp.425-426).
   The Azure equivalent here: Power Apps review queue → corrections in SQL → `build_training_set.py` adds
   reviewed real documents → retrain. **Planned (phase 5).**
6. **Register models and record lineage per row.** Each extraction row should carry the model ID/version and
   schema version, not just the run file (Sinha, *Practical ML on Databricks*, pp.115-118 on MLflow tracking
   and the Model Registry; Korner & Waaijer, *Mastering Azure ML*, p.345 on reproducible pipelines that
   overwrite the same output for the same inputs). **Partly built:** `train_router` logs an MLflow run and registers `gold.review_router` in UC;
   `model_id`/`run_id` carried through bronze → gold. Still planned: `schema_version` column in `fields`.
7. **Monitor drift on what we can observe without labels:** confidence distribution, review rate, share of
   new label variants, per-carrier volume. "Both the input and the output distribution of data can change"
   (Ameisen, *Building ML Powered Applications*, p.240); input-drift techniques in Treveil et al.,
   *Introducing MLOps*, pp.92-95. **Planned:** daily metrics table + Log Analytics alert on review rate.
8. **Keep the test set clean.** Threshold selection is now cross-fitted across the two test carriers (RESULTS
   Phase 3b): the in-sample cutoff didn't transfer, which led to the new-layout burn-in policy.
   Original note: Already enforced (test carriers never trained on, trained models never scored on
   their own training docs). Huyen (*AI Engineering*, pp.392-393) shows how contaminated benchmarks inflate
   results; same principle.

## Cloud implementation

9. **Orchestration is manual scripts.** Fine for a lab; production needs an event-driven, idempotent flow:
   blob upload → queue → extract → validate → load, with retries and a dead-letter path (event-driven
   decoupling, Brisals, *Serverless Development on AWS*, p.340; Azure equivalents: Event Grid + Functions,
   or a Databricks Workflow, or Power Automate for the low-code path). **Planned (phase 5).**
10. **Lakehouse layering.** Raw (`raw-private`) and curated (`curated` Parquet) exist; a medallion layout on
    Delta tables gives versioned, MERGE-able bronze/silver/gold (Lee, *Delta Lake*, ch. 9, p.311).
    **Built** (`databricks/`): UC bronze/silver/gold with quarantine + quality gate; deployed and run on Databricks Free Edition 2026-11-06.
11. **SQL firewall allows all Azure IPs** (needed for Power BI service). Auth is still Entra-only, so the
    risk is reachability, not access. **Accepted for dev;** production: private endpoints + VNet data gateway
    (zero-trust "verify each request", Andersson, *Learning Microsoft Azure*, p.273).
12. **No resource locks or blob soft delete.** A `CanNotDelete` lock prevents accidental deletion while still
    allowing changes (Andersson, p.375). **Planned:** lock on SQL + storage, blob soft delete 7 days
    (pennies). Not applied yet because `down.sh` teardown must remove the lock first.
13. **Public network access on AI services.** Same as #11: Entra-only auth, dev build. Production: private
    endpoints.
14. **Push dataset limits.** Can't add tables in place, no scheduled refresh from SQL. Production: Import or
    DirectQuery model from Azure SQL (needs Desktop or Fabric) with scheduled refresh. **Accepted** until
    Fabric/Desktop is available.

## What is already good (keep)

- No keys or passwords anywhere (Entra ID + RBAC + managed identity); infra-as-code with tag checks in CI.
- Ground-truth synthetic benchmark with held-out layouts; silent-error metric; threshold chosen from data.
- PII handled in layers with measured misses; real data never leaves `private/` except as aggregates.
- Idle cost ≈ $0 (free tiers, auto-pause, pay-per-use).
