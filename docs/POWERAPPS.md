# Power Apps review queue (MVP, browser only)

> **Now managed as code.** The app is in the Dataverse solution `InsuranceDocAI`; its source is
> `powerapps/src/Src/Screen1.fx.yaml` and `powerapps/deploy.sh pull|push` syncs it (RUNBOOK §4c). The click-by-click
> steps below are how the first version was built in Studio, kept for anyone building it from scratch.

One-screen canvas app: a reviewer works through the values the pipeline flagged, looks at the document, and
accepts, corrects, or marks the value "not on the document". Decisions land in Azure SQL (`review.item`), a
temporal table: SQL keeps every earlier version of each row in `review.item_history`, which is the audit trail.
(No trigger: Power Apps' Patch fails on tables with triggers.) Synthetic documents only.

```
┌─────────────────────┬──────────────────────────────┬──────────────────────────┐
│ Queue (17 open)     │                              │ premium_annual_total     │
│ ▸ S053 premium …    │      document page image     │ Extracted: 580,500       │
│   S053 coverage_a … │                              │ Confidence: 0.51         │
│   S054 coverage_a … │                              │ Why: low_confidence;     │
│   …                 │                              │      out_of_range        │
│                     │                              │ Correct value: [______]  │
│                     │                              │ [Accept] [Correct]       │
│                     │                              │ [Not on document]        │
└─────────────────────┴──────────────────────────────┴──────────────────────────┘
```

## 0. One-time setup

1. **Power Apps Developer Plan** (free): https://www.microsoft.com/en-us/power-platform/products/power-apps/free,
   sign in with the work account `willie@<tenant>.onmicrosoft.com`. It creates a developer environment.
2. **Queue data** (operator, already done 2026-11-06):
   ```bash
   cd src && ../.venv/bin/python review_queue.py --reviewer willie@<tenant>.onmicrosoft.com
   ```
   The work account can read `review.item` + `review.document` and update only the decision columns. It can't
   read `review.answer_key`.

## 1. Create the app

1. https://make.powerapps.com → check the environment (top right) is your developer environment.
2. **Create → Blank app → Blank canvas app**, name `Insurance DocAI Review`, format **Tablet** (landscape; for the browser).
3. **Data** (left rail, cylinder icon) → **Add data** → search **SQL Server** → **Add a connection** →
   Authentication type **Microsoft Entra ID Integrated** → Connect (signs in as the work account).
   - Server: `sql-docai-i777yv-cus.database.windows.net`, database `docai`.
   - Tick tables **review.item** and **review.document** → Connect.
   They show up as `'review.item'` and `'review.document'` (if the names differ, use what the Data pane shows).

## 2. Controls (Insert menu) and their properties

Paste each formula into the property named on the left (select the control, pick the property in the
formula bar dropdown).

**First time in Power Apps Studio:**
- **Insert:** the **+** icon on the left rail. Use **Classic** controls for the text input and buttons (the
  formulas use classic property names like `.Text`).
- **Formula bar** (top): the dropdown on the left picks the property; paste the formula after `fx`.
- **Rename:** Tree view (layers icon) → double-click the control name. Use the names below exactly.
- **Position/size:** the Properties pane on the right (X, Y, Width, Height). Save often with Ctrl+S.

**Layout** (Tablet, 1366 × 768):

| Control | X | Y | Width | Height |
| :--- | :--- | :--- | :--- | :--- |
| `lblTitle` (font size 20) | 20 | 10 | 1326 | 50 |
| `galQueue` (layout: Title and subtitle) | 20 | 70 | 360 | 680 |
| `imgDoc` | 395 | 70 | 560 | 680 |
| `lblField` / `lblValue` / `lblConf` / `lblWhy` | 975 | 70 / 120 / 160 / 200 | 370 | 40 (lblWhy 60) |
| `txtValue` | 975 | 280 | 370 | 45 |
| `btnAccept` / `btnCorrect` / `btnNotOnDoc` | 975 | 340 / 400 / 460 | 370 | 45 |

When you insert the gallery, pick **review.item** as its data source. Then expand it in the Tree view and set
the Title and Subtitle labels inside it.

**Header label** `lblTitle` (top, full width)
- Text: `"Review queue: " & CountRows(Filter('review.item', status = "open")) & " open"`

**Vertical gallery** `galQueue` (left third). Insert → Vertical gallery, layout "Title and subtitle".
- Items: `SortByColumns(Filter('review.item', status = "open"), "item_id")`
- Title label Text: `ThisItem.doc_id & "  " & ThisItem.field`
- Subtitle label Text: `Text(ThisItem.extracted_value, "#,##0.##") & "  ·  conf " & Text(ThisItem.confidence, "0.00")`

**Image** `imgDoc` (middle, tall)
- Image: `LookUp('review.document', doc_id = galQueue.Selected.doc_id).page_image`
- ImagePosition: `ImagePosition.Fit`

**Labels** on the right, stacked:
- `lblField` Text: `galQueue.Selected.field`
- `lblValue` Text: `"Extracted: " & Text(galQueue.Selected.extracted_value, "#,##0.##")`
- `lblConf` Text: `"Confidence: " & Text(galQueue.Selected.confidence, "0.00")`
- `lblWhy` Text: `"Why flagged: " & galQueue.Selected.flag_reasons`

**Text input** `txtValue` (right)
- Default: `Text(galQueue.Selected.extracted_value)`
- Format: `TextFormat.Number`

**Buttons** (right). OnSelect formulas:

- `btnAccept` Text `"Accept"`:
  ```
  Patch('review.item', galQueue.Selected,
        {status: "accepted", reviewed_value: galQueue.Selected.extracted_value,
         reviewer: User().Email, reviewed_at: Now()})
  ```
- `btnCorrect` Text `"Save correction"`:
  ```
  If(IsBlank(txtValue.Text) || IsError(Value(txtValue.Text)),
     Notify("Enter the value printed on the document", NotificationType.Warning),
     Patch('review.item', galQueue.Selected,
           {status: "corrected", reviewed_value: Value(txtValue.Text),
            reviewer: User().Email, reviewed_at: Now()}))
  ```
- `btnNotOnDoc` Text `"Not on document"`:
  ```
  Patch('review.item', galQueue.Selected,
        {status: "not_on_document", reviewed_value: Blank(),
         reviewer: User().Email, reviewed_at: Now()})
  ```

After a decision the item leaves the gallery (it's no longer `open`) and the next one is selected.

## 2b. Context, hints and demo labelling (v2 of the screen)

`review_queue.py` now fills six context columns per item (built from the schema, the calibration results and the
other values on the same letter, never from the answer key) and bakes a diagonal **SYNTHETIC · DEMO** watermark
into every page image. In Studio: **Data → ⋯ next to review.item → Refresh** first, so the new columns appear.

Move/resize the existing controls, then add the new ones. All formulas go in the **formula bar**.

| Control | X | Y | W | H | Property: formula |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `lblTitle` (existing) | 20 | 10 | 1326 | 50 | Text: `"Review queue: " & CountRows(Filter('review.item', status = "open")) & " open · " & CountRows(Filter('review.item', status <> "open")) & " done"` |
| `lblContext` **new** label, size 11 | 395 | 70 | 560 | 30 | Text: `galQueue.Selected.doc_context` |
| `imgDoc` (existing) | 395 | 105 | 560 | 520 | (unchanged) |
| `lblOther` **new** label, size 11 | 395 | 630 | 560 | 100 | Text: `"Other values the model read on this letter: " & galQueue.Selected.other_values` |
| `lblField` (existing), bold 16 | 975 | 70 | 370 | 40 | Text: `galQueue.Selected.field_label` |
| `lblValue` (existing) | 975 | 115 | 370 | 35 | (unchanged) |
| `lblConf` (existing) | 975 | 150 | 370 | 35 | (unchanged) |
| `lblWhy` (existing) | 975 | 190 | 370 | 65 | Text: `"Why flagged: " & galQueue.Selected.reason_help` |
| `lblHint` **new** label | 975 | 265 | 370 | 95 | Text: `"Hint: " & galQueue.Selected.hint` · Fill: `RGBA(255, 244, 206, 1)` |
| `txtValue` (existing) | 975 | 370 | 370 | 42 | (unchanged) |
| `btnAccept` / `btnCorrect` / `btnNotOnDoc` | 975 | 420 / 470 / 520 | 370 | 42 | (unchanged) |
| `lblHelp` **new** label, size 11 | 975 | 575 | 370 | 155 | Text: `"About this field: " & galQueue.Selected.field_help` |
| `lblBanner` **new** label, size 11, white text, centered | 0 | 740 | 1366 | 28 | Text: `"DEMO · synthetic letters from fictional insurers · development environment · not insurance advice"` · Fill: `RGBA(164, 38, 44, 1)` · Color: `White` |

For the labels: Properties pane → **Wrap** on (default), and set **Font size** there. For `lblHint`, a
yellow background makes it read as advice rather than data.

## 3. Test, save, share

- **Preview** (▶ or F5): review a few items. Use the image to decide: is the extracted number the one
  printed next to that field's label?
- **File → Save**, then **Publish**. The app runs in the browser at the URL under **Details** (Apps list → … → Details).
- Score the decisions against the answer key (operator):
  ```bash
  cd src && ../.venv/bin/python review_queue.py --score
  ```
- Start over: `review_queue.py --reset` (clears decisions and history).

## Notes

- Why SQL and not Dataverse: one serving layer for Power BI, Power Apps and the pipeline, and the JD prefers
  Azure SQL. Entra ID sign-in end to end; no SQL passwords.
- Images as data URIs in SQL keep the MVP to one connector. Production: page images in blob storage,
  read through the Azure Blob Storage connector with Entra ID.
- Corrections are the labels for the review-to-retraining loop (REVIEW.md #5) and the per-layout cutoff
  burn-in (RESULTS Phase 3b). Next: a Power Automate intake flow.
