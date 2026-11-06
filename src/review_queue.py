"""Phase 5 MVP: the review queue in Azure SQL, ready for a Power Apps canvas app (browser).

Creates schema `review`:
  review.item        one row per flagged value: what the model extracted, its confidence, why it was flagged,
                     and the reviewer's decision (status open → accepted / corrected / not_on_document)
  review.document    one page image per document (JPEG data URI, so the app's Image control shows it with
                     no blob connector; fine for an MVP of ~10 synthetic docs, production would use blob)
  review.item_history  every prior version of each row (temporal table, kept by SQL itself) = the audit
                     trail. Not a trigger: Power Apps' Patch fails on tables with enabled triggers.
  review.answer_key  the true value for synthetic docs, NOT readable by the app: scores reviewer accuracy

The app's account (the work account) gets SELECT on item/document and UPDATE on the decision columns only.

Source: data/dashboard/fact_field.parquet, flagged values of one method on the held-out test layouts.
Synthetic documents only (no real letters in the demo app).

Usage: .venv/bin/python src/review_queue.py --reviewer willie@<tenant>.onmicrosoft.com [--method ins-neural-v2] [--reset]
       .venv/bin/python src/review_queue.py --score     # reviewer accuracy against the answer key
"""
import argparse
import base64
import io
import subprocess
import tempfile

import json

import pandas as pd
import yaml
from PIL import Image, ImageDraw, ImageFont

from common import ROOT, connect_sql

PDF = ROOT / "samples" / "synthetic" / "pdf"
SYN = ROOT / "samples" / "synthetic"
WATERMARK = "SYNTHETIC · DEMO"

# Reviewer-facing context. Labels come from schema/fields.yaml (synonyms, ranges); text here is plain English.
FIELD_INFO = {
    "coverage_a_dwelling": ("Dwelling (Coverage A)", "What it costs to rebuild the house itself. Usually the largest coverage on the letter."),
    "coverage_b_other_structures": ("Other structures (Coverage B)", "Fences, detached garage, sheds. Often 10% of dwelling."),
    "coverage_c_personal_property": ("Personal property (Coverage C)", "Your belongings. Often 25-75% of dwelling."),
    "coverage_d_loss_of_use": ("Loss of use (Coverage D)", "Living costs while the house is repaired. Often 10-30% of dwelling."),
    "coverage_e_liability": ("Personal liability (Coverage E)", "If someone sues you for injury or damage. Round numbers: 100,000 / 300,000 / 500,000."),
    "coverage_f_medical": ("Medical payments (Coverage F)", "Guests' medical bills, no fault needed. Small round numbers: 1,000 / 2,500 / 5,000."),
    "premium_annual_total": ("Annual premium (total)", "The price for one year. Has cents, usually a few thousand dollars, often the biggest bold number."),
    "deductible_all_peril": ("Deductible (all other perils)", "What you pay first on a claim. Small round numbers: 500 / 1,000 / 2,500."),
    "deductible_wind_hail_pct": ("Wind/hail deductible (%)", "A percentage of dwelling coverage, e.g. 1% or 2%. Not a dollar amount."),
    "policy_fee": ("Policy fee", "A flat fee, usually under $100."),
    "year_built": ("Year built", "A four-digit year."),
    "square_feet": ("Square feet", "Living area of the house."),
}
REASONS = {
    "low_confidence": "The model wasn't sure it read this correctly.",
    "out_of_range": "The number is outside what's normal for this field.",
    "missing_required": "A required field wasn't found.",
    "conflict": "The letter seems to show two different values.",
}

DDL = [
    "IF SCHEMA_ID('review') IS NULL EXEC('CREATE SCHEMA review')",
    """IF OBJECT_ID('review.item') IS NULL CREATE TABLE review.item (
        item_id INT IDENTITY PRIMARY KEY,
        doc_id NVARCHAR(16) NOT NULL, method NVARCHAR(32) NOT NULL, field NVARCHAR(64) NOT NULL,
        carrier NVARCHAR(128), layout NVARCHAR(32),
        extracted_value FLOAT NULL, confidence FLOAT NULL, flag_reasons NVARCHAR(256),
        status NVARCHAR(20) NOT NULL DEFAULT 'open'
            CONSTRAINT ck_item_status CHECK (status IN ('open', 'accepted', 'corrected', 'not_on_document')),
        reviewed_value FLOAT NULL, reviewer NVARCHAR(256) NULL, reviewed_at DATETIME2 NULL, comment NVARCHAR(400) NULL,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME())""",
    # reviewer context columns (added after the first version; nullable so the temporal table accepts them)
    *[f"IF COL_LENGTH('review.item', '{c}') IS NULL ALTER TABLE review.item ADD {c} NVARCHAR(600) NULL"
      for c in ("field_label", "field_help", "reason_help", "hint", "doc_context", "other_values")],
    """IF OBJECT_ID('review.document') IS NULL CREATE TABLE review.document (
        doc_id NVARCHAR(16) PRIMARY KEY, page_image NVARCHAR(MAX) NOT NULL)""",
    """IF OBJECT_ID('review.answer_key') IS NULL CREATE TABLE review.answer_key (
        item_id INT PRIMARY KEY, truth FLOAT NULL, outcome NVARCHAR(16) NOT NULL)""",
    # Audit trail = system-versioned (temporal) table: SQL keeps every prior version of each row in
    # review.item_history by itself. Not a trigger: Power Apps' Patch uses an OUTPUT clause, which SQL refuses
    # on tables with enabled triggers ("cannot have any enabled triggers if the statement contains an OUTPUT
    # clause without INTO clause").
    "DROP TRIGGER IF EXISTS review.tr_item_audit",
    "DROP TABLE IF EXISTS review.audit",
    """IF COL_LENGTH('review.item', 'valid_from') IS NULL
       ALTER TABLE review.item ADD
         valid_from DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL
             CONSTRAINT df_item_from DEFAULT SYSUTCDATETIME(),
         valid_to DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL
             CONSTRAINT df_item_to DEFAULT CONVERT(DATETIME2, '9999-12-31 23:59:59.9999999'),
         PERIOD FOR SYSTEM_TIME (valid_from, valid_to)""",
    """IF OBJECTPROPERTY(OBJECT_ID('review.item'), 'TableTemporalType') = 0
       ALTER TABLE review.item SET (SYSTEM_VERSIONING = ON (HISTORY_TABLE = review.item_history))""",
]


def page_image(doc_id, width=900):
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", "1", "-singlefile",
                        str(PDF / f"{doc_id}.pdf"), f"{tmp}/page"], check=True)
        im = Image.open(f"{tmp}/page.png").convert("RGB")
    im.thumbnail((width, width * 2))
    # diagonal watermark baked into the image, so any screenshot of a document says what it is
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(im.width / 14))
    except OSError:
        font = ImageFont.load_default()
    w, h = d.textbbox((0, 0), WATERMARK, font=font)[2:]
    d.text(((im.width - w) / 2, (im.height - h) / 2), WATERMARK, font=font, fill=(200, 30, 30, 75))
    layer = layer.rotate(25, center=(im.width / 2, im.height / 2))
    im = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def money(v, field):
    if pd.isna(v):
        return "(none)"
    if field == "deductible_wind_hail_pct":
        return f"{v:g}%"
    if field in ("year_built", "square_feet"):
        return f"{v:,.0f}"
    return f"${v:,.2f}" if v % 1 else f"${v:,.0f}"


def context(r, doc_preds, schema, reliability):
    """Reviewer help built only from what the pipeline knows at review time (never the answer key)."""
    label, what = FIELD_INFO.get(r.field, (r.field, ""))
    spec = schema["fields"].get(r.field, {})
    look = ", ".join(f"'{x.title()}'" for x in spec.get("synonyms", [])[:5])
    rng = spec.get("range")
    help_ = what + (f" Look for labels like {look}." if look else "")
    if rng:
        help_ += f" Normal range: {money(rng[0], r.field)} to {money(rng[1], r.field)}."
    reasons = " ".join(REASONS.get(x, x) for x in str(r.flag_reasons).split(";") if x)
    hints = []
    v = r.pred
    if pd.notna(v):
        same = [FIELD_INFO.get(f, (f,))[0] for f, pv in doc_preds.items() if f != r.field and pd.notna(pv) and abs(pv - v) < 0.005]
        if same:
            hints.append(f"Same number the model read as {', '.join(same)} on this letter: it may have grabbed the wrong number.")
        rt = spec.get("ratio_to")
        a = doc_preds.get(rt["field"]) if rt else None
        if rt and a and pd.notna(a):
            pct = v / a
            lo, hi = rt["range"]
            hints.append(f"This is {pct:.0%} of dwelling coverage (normal {lo:.0%}-{hi:.0%}).")
        if rng and not (rng[0] <= v <= rng[1]):
            hints.append(f"{money(v, r.field)} is outside the normal range for this field.")
        for b in reliability:
            lo, hi = (float(x) for x in b["bin"].strip("(]").split(","))
            if lo < r.confidence <= hi and b["n"] >= 3:
                hints.append(f"At this confidence ({r.confidence:.2f}) the model was right on {round(b['share_correct'] * b['n'])} "
                             f"of {b['n']} test values.")
                break
    if not hints:
        hints.append("Find the label on the letter and compare the number next to it.")
    others = " · ".join(f"{FIELD_INFO.get(f, (f,))[0]}: {money(pv, f)}" for f, pv in doc_preds.items()
                        if f != r.field and pd.notna(pv))
    return label, help_, reasons, " ".join(hints), others


def load(cur, method, reset):
    f = pd.read_parquet(ROOT / "data" / "dashboard" / "fact_field.parquet")
    schema = yaml.safe_load(open(ROOT / "schema" / "fields.yaml"))
    reliability = json.loads((SYN / "eval" / "calibration.json").read_text())[method]["reliability"]
    preds = f[f.method == method]
    q = f[(f.method == method) & (f.split == "test") & f.flagged].sort_values(["doc_id", "field"])
    cur.execute("SELECT COUNT(*) FROM review.item")
    if cur.fetchone()[0] and not reset:
        print("review.item already has rows (decisions kept); use --reset to reload")
        return
    cur.execute("ALTER TABLE review.item SET (SYSTEM_VERSIONING = OFF)")
    for t in ("review.answer_key", "review.item", "review.item_history", "review.document"):
        cur.execute(f"DELETE FROM {t}")
    cur.execute("ALTER TABLE review.item SET (SYSTEM_VERSIONING = ON (HISTORY_TABLE = review.item_history))")
    cur.execute("DBCC CHECKIDENT ('review.item', RESEED, 0)")
    for r in q.itertuples():
        doc_preds = dict(preds[preds.doc_id == r.doc_id][["field", "pred"]].itertuples(index=False))
        label, help_, reasons, hint, others = context(r, doc_preds, schema, reliability)
        unseen = " (a layout the model never trained on)" if r.split == "test" else ""
        doc_ctx = f"{r.doc_id} · {r.carrier} · {r.layout.replace('_', ' ')}{unseen} · {'scanned' if r.scanned else 'digital'}"
        cur.execute("""INSERT INTO review.item (doc_id, method, field, carrier, layout, extracted_value, confidence,
                       flag_reasons, field_label, field_help, reason_help, hint, doc_context, other_values)
                       OUTPUT INSERTED.item_id VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r.doc_id, r.method, r.field, r.carrier, r.layout,
                     None if pd.isna(r.pred) else float(r.pred), float(r.confidence), r.flag_reasons,
                     label, help_, reasons, hint, doc_ctx, others))
        item_id = cur.fetchone()[0]
        cur.execute("INSERT INTO review.answer_key VALUES (?, ?, ?)",
                    (item_id, None if pd.isna(r.truth) else float(r.truth), r.outcome))
    for doc in sorted(q.doc_id.unique()):
        cur.execute("INSERT INTO review.document VALUES (?, ?)", (doc, page_image(doc)))
    print(f"loaded {len(q)} flagged values from {q.doc_id.nunique()} documents ({method}, test layouts)")


def grant(cur, upn):
    cur.execute(f"IF USER_ID(N'{upn}') IS NULL CREATE USER [{upn}] FROM EXTERNAL PROVIDER")
    cur.execute(f"GRANT SELECT ON review.item TO [{upn}]")
    cur.execute(f"GRANT SELECT ON review.document TO [{upn}]")
    cur.execute(f"GRANT UPDATE ON review.item (status, reviewed_value, reviewer, reviewed_at, comment) TO [{upn}]")
    print(f"granted {upn}: SELECT item/document, UPDATE decision columns (no access to answer_key or history)")


def score(cur):
    cur.execute("""SELECT i.item_id, i.doc_id, i.field, i.extracted_value, i.status, i.reviewed_value, k.truth, k.outcome
                   FROM review.item i JOIN review.answer_key k ON k.item_id = i.item_id""")
    rows = pd.DataFrame.from_records([tuple(r) for r in cur.fetchall()],
                                     columns=["item_id", "doc_id", "field", "extracted", "status", "reviewed", "truth", "outcome"])
    done = rows[rows.status != "open"].copy()

    def right(r):
        if r.status == "not_on_document":
            return pd.isna(r.truth)
        final = r.extracted if r.status == "accepted" else r.reviewed
        return pd.notna(r.truth) and pd.notna(final) and abs(final - r.truth) <= max(0.01, abs(r.truth) * 1e-6)

    done["right"] = done.apply(right, axis=1)
    print(f"reviewed {len(done)}/{len(rows)}; reviewer decisions correct: {done.right.sum()}/{len(done)}")
    cur.execute("""SELECT item_id, status, reviewed_value, reviewer, valid_from FROM review.item FOR SYSTEM_TIME ALL
                   WHERE item_id IN (SELECT item_id FROM review.item WHERE status <> 'open') ORDER BY item_id, valid_from""")
    hist = [tuple(r) for r in cur.fetchall()]
    print(f"audit trail: {len(hist)} row versions for decided items (open → decision, incl. changes of mind)")
    for h in hist:
        print("  ", h)
    if len(done):
        print(done[["item_id", "doc_id", "field", "status", "extracted", "reviewed", "truth", "right"]].to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="ins-neural-v2")
    ap.add_argument("--reviewer", action="append", default=[], help="Entra UPN of the app user")
    ap.add_argument("--reset", action="store_true", help="reload the queue (clears decisions and history)")
    ap.add_argument("--score", action="store_true")
    a = ap.parse_args()
    conn = connect_sql()
    cur = conn.cursor()
    if a.score:
        score(cur)
        return
    for stmt in DDL:
        cur.execute(stmt)
    load(cur, a.method, a.reset)
    for upn in a.reviewer:
        grant(cur, upn)
    conn.commit()
    cur.execute("SELECT status, COUNT(*) FROM review.item GROUP BY status")
    print("queue:", {s: n for s, n in cur.fetchall()})


if __name__ == "__main__":
    from common import load_env
    load_env()   # DOCAI_ENV=dev|qa|prod (default dev)
    main()
