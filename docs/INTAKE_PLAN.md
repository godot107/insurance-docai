# Plan: upload documents and send them through Document Intelligence (not built)

Status: **plan only** (2026-11-11). Nothing here is implemented. The review app and the Power BI report are private
(development environment, owner only) and are not intended to be shared publicly.

## What the user does

In the review app: choose a household and a document type (renewal, quote mailer, declarations), take a photo or pick
a PDF, press **Submit**. A "My letters" list shows each document's progress:
*Received → Reading → Needs review → Done*.

## What happens behind it

| Step | Piece | Notes |
| :--- | :--- | :--- |
| 1. Upload | Power Apps screen + Power Automate flow | The flow stores the file in Blob Storage named by its SHA-256 (no personal file names, as today) and inserts a row in a new `intake.document` table (status `received`). |
| 2. Quality gate | Azure Function, triggered by Event Grid "blob created" | Rejects unreadable scans with "please rescan" before paying for extraction (RESULTS.md: a third of the harshest scans had numbers no model could read). |
| 3. Read | Same Function → Document Intelligence custom model | Managed identity (Cognitive Services User), no keys. Reuses `normalize.py`: map labels to `schema/fields.yaml`, range/ratio/required checks. |
| 4. Route | Same Function → Azure SQL | Trusted values go straight to the quote table; uncertain ones become `review.item` rows with hints; the page image goes to `review.document`, so the Power BI "Letter" link works at once. |
| 5. Review | Existing review app | No change. |
| 6. Report | Power BI | Re-push after each applied document, or on a schedule (or move the model to DirectQuery). |

Why a Function rather than doing it all in Power Automate: extraction, mapping, validation and routing already exist as
tested Python. The flow stays thin (upload + record); the logic stays in code with tests.

## Decide before building

- **Privacy.** Uploads are real personal documents: row-level access by household, AI Language redaction for anything
  copied downstream, a retention period, audit of who opened what (the app already traces views).
- **New layouts.** A document from an unfamiliar insurer starts at full review; its cutoff is set from those reviews
  (the RESULTS.md finding that cutoffs don't transfer between layouts).
- **Licensing.** Some Power Automate connectors are premium. The developer plan covers building; sharing needs licences.
- **Azure.** Needs a running subscription. The free trial ends early December 2026; build only after an upgrade to
  pay-as-you-go.

## Cost and size

- About 3 cents per page for the custom model at list price; the Function is effectively free at this volume.
- Roughly 3-4 working sessions: upload screen + flow; Function + quality gate; status list; testing on the demo houses.

## Circuit breaker: stop trusting the model when review feedback goes bad (plan only)

The review queue already measures the model for free: every decision says whether a flagged value was right
(accepted), wrong (corrected) or invented (not on document). The "Review feedback & drift" page shows the case this
should catch: when `ins-neural-v1` went live, *not on document* jumped from 0% to 59% of flagged values.

**Baseline.** Per layout (or insurer), from the burn-in period when a new layout is fully reviewed and from the
validation set: flag rate, accept / correction / not-on-document rates, and mean confidence. Stored in a small
`ops.baseline` table with the model version it belongs to; a new model version gets a new baseline.

**Trigger.** A scheduled check (hourly; SQL view + Azure Function or Logic App) compares the latest window with the
baseline, per layout:
- *rate shift*: correction or not-on-document rate above its baseline by more than 3 standard errors (a p-chart limit),
  or above a hard ceiling (e.g. 25%);
- *volume guard*: only when the window has enough decisions (e.g. ≥ 20), so a quiet day can't trip it;
- *persistence*: two consecutive windows, so one bad batch doesn't flap it;
- also: a sudden drop in mean confidence, or a new, unknown layout (no baseline yet → full review by design).

**Actions, in tiers** (state in `ops.circuit_state`, which the intake Function reads before routing anything):

| State | Meaning | What happens |
| :--- | :--- | :--- |
| closed | normal | confidence + rules route values; trusted ones go straight through |
| warn | drifting | notify the support channel (Power Automate → Teams/email) with the page link and the worst examples |
| open (layout) | tripped | **no auto-accept for that layout**: every value goes to a person; intake continues, so nothing is lost |
| open (global) | many layouts tripped, or the model endpoint failing | pause intake (uploads queue as "received", not processed) and page the on-call |

Reset is manual (a person confirms the cause) with hysteresis: the rate must return under the limit for a full
window before the state can close.

**Retrain, with a person in the decision.** Corrections and not-on-document decisions are labels: turn them into
training examples, retrain, evaluate on the held-out set and on the layout that tripped, compare to the current
model, and promote only with an approval (the deploy workflow's prod gate). Automatic retraining on reviewer output
would let a bad week of reviews train the model; the gate keeps a person accountable.

**Why per layout, not global.** One insurer changing its letter shouldn't stop all intake; the RESULTS.md finding
that cutoffs don't transfer between layouts applies to breakers too.
