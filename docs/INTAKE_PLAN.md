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
