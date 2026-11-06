# Privacy rule

The real renewal letters are the author's own and contain personal information. They are used to build and
test the pipeline, but they are never published.

## What counts as PII here

Names, mailing/property address, policy and claim numbers, account/loan (mortgagee) numbers, agent name and
phone, email, signatures, barcodes/QR codes, and **anything that combines into an identifier**: an exact
premium + carrier + year + ZIP can identify a household even with the name removed.

## Where things live

| Material | Location | Public? |
| :--- | :--- | :--- |
| Original letters (PDF/scans) | `private/letters/` (gitignored) + private blob container `raw-private` | Never |
| Hand labels for real letters | `private/labels/` | Never |
| Document Intelligence output for real letters | `private/` or `data/private/` | Never |
| Synthetic letters + their labels | `samples/synthetic/` | Yes |
| Aggregate metrics (accuracy, calibration) incl. real-letter results | `docs/`, blog | Yes, aggregates only |
| The author's own premium finding | blog | Only as rounded/relative figures (e.g. "~20th percentile", "+14% at renewal"), no carrier + exact amount + ZIP together |

## Before anything goes public

1. `git ls-files` lists no file from `private/` and no non-synthetic document.
2. Grep the repo for the author's name, street, ZIP, policy numbers, and carrier account numbers (the
   search list lives in `private/pii_terms.txt`, which is also gitignored).
3. Run Azure AI Language PII detection over every tracked text/notebook output.
4. Clear notebook outputs that show real-letter rows.
5. **Git history:** earlier commits contain the work-account address (it embeds the personal Gmail handle)
   and account IDs. Publish from a fresh single-commit history (new public repo, or `git filter-repo`), not by
   flipping this repo's visibility. Operator identifiers now live in `private/NOTES.md`.
6. Only then switch the GitHub repo to public. (Done 2026-11-06: published from a fresh history; commits use
   the GitHub no-reply email.)

`docs/img/` is the one place images are tracked: screenshots of synthetic letters (watermarked) and the simulated
household only. Check every new image before committing it.

### Audit log
- 2026-11-06 pre-check: 0 tracked files from `private/`; 0 non-synthetic PDFs; deny-list (10 terms): only the
  author's first name matched (author/operator credit, acceptable); work-account address and IDs replaced with
  placeholders in tracked files; history still contains them (item 5). Steps 3-4 not yet run.
- 2026-11-06 pre-publication: (1) no tracked file from `private/`; (2) deny-list grep over all tracked text: only
  the author's first name (credit); account identifiers absent; (3) Azure AI Language PII scan of all 14 tracked
  Markdown files: only the author's first name, cited textbook authors, the public contact address, and
  false positives; (4) no notebooks tracked. Screenshots in `docs/img/` reviewed one by one (synthetic data,
  account names redacted). Remaining: publish from a fresh history (item 5).

