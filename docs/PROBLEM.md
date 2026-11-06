# The problem this solves

## For a client

A public agency, say a state insurance regulator or a consumer-protection office, receives documents from
many companies, each in its own format. The facts it needs (what changed, by how much, for whom) are
buried in letters, notices and forms. Today someone reads them by hand, which is slow, inconsistent, and
hard to audit.

What the agency needs is a pipeline that:

1. **Extracts** the fields from any layout (Azure AI Document Intelligence).
2. **Knows when it's unsure.** Each field comes with a confidence score, and a measured threshold decides
   what goes to a person.
3. **Puts a person in the loop** for exactly those fields (Power Apps review queue), and logs every
   correction so the model improves.
4. **Lands clean, auditable data** (Azure SQL / Databricks) that analysts can trust and report on (Power BI).
5. **Protects personal information** throughout.

Insurance renewal notices are a concrete, realistic case: they report the premium change a household
actually experiences, which is exactly what a regulator watching rate increases wants to see.

## For one household (the blog hook)

"My renewal letter says my premium went up. Is it high for what it covers, and is it worth shopping around?"
The extracted coverage profile is compared with public Texas Department of Insurance sample premiums. This
is framed as decision support, not a quote or advice.

## What gets measured

- Field-level extraction accuracy on letter layouts the model **never saw in training**
- Whether Document Intelligence's confidence scores match its actual accuracy (calibration)
- The trade-off: % of fields sent to a person vs errors that slip through, at each threshold
- Benchmark model error and interval coverage on held-out public data
