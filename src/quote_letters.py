"""Page images for the "Should I switch?" drill-through: one letter per row of fact_quote, stored in
review.document (doc_id → JPEG data URI) so the review app can show it (Power BI links open the app with ?doc=).

Demo households: a synthetic letter is rendered for every quote with exactly the numbers in the row (the current
policy as a declarations page, quotes as quote proposals, so deductible and wind/hail are on the page).
Real households (--include-private): page 1 of the real letter from private/letters, or the hand-redacted page
when one exists. These images go only to Azure SQL, never to the repo.

Usage: .venv/bin/python src/quote_letters.py [--include-private]
"""
import argparse
import base64
import csv
import io
import json
import random

import pandas as pd
import pypdfium2 as pdfium
from faker import Faker
from jinja2 import Environment, FileSystemLoader
from PIL import Image, ImageDraw, ImageFont
from weasyprint import HTML

from common import ROOT, connect_sql
from synth.generate import TEMPLATES, money, printed

SYN = ROOT / "samples" / "synthetic"
PRIVATE = ROOT / "private"


def to_data_uri(im, banner, colour, width=900):
    im = im.convert("RGB")
    im.thumbnail((width, width * 2))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    d.rectangle((0, im.height - 30, im.width, im.height), fill=colour)
    d.text((10, im.height - 26), banner, font=font, fill="white")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pdf_page(pdf_bytes, page=0, scale=1.6):
    return pdfium.PdfDocument(pdf_bytes)[page].render(scale=scale).to_pil()


def demo_letters():
    q = pd.read_parquet(ROOT / "data" / "dashboard" / "fact_quote.parquet")
    homes = dict(pd.read_parquet(ROOT / "data" / "dashboard" / "dim_household.parquet")[["household_id", "household"]]
                 .itertuples(index=False))
    kits = {k["carrier_name"]: k for k in json.load(open(SYN / "carriers.json"))}
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    env.globals.update(money=lambda x: money(x, True), fmt_int=lambda x: f"{x:,}")
    rng = random.Random(5)
    fake = Faker("en_US")
    Faker.seed(5)
    out = {}
    for r in q.itertuples():
        current = r.doc_type == "declarations"
        kit = dict(kits.get(r.carrier) or next(iter(kits.values())))
        if current:   # the current insurer is fictional too: borrow a style, label it as the current policy
            kit["carrier_name"] = "Current insurer (simulated)"
            kit["tagline"] = "Your current homeowners policy"
            kit["agency_name"] = "Current insurer agency (simulated)"
        layout = "dec_page" if current else "quote_proposal"
        a = float(r.coverage_a)
        t = {"coverage_a_dwelling": a, "coverage_b_other_structures": round(a * 0.1, -2),
             "coverage_c_personal_property": float(r.coverage_c), "coverage_d_loss_of_use": round(a * 0.2, -2),
             "coverage_e_liability": float(r.coverage_e), "coverage_f_medical": 5000,
             "deductible_all_peril": float(r.aop_deductible), "deductible_all_peril_raw": int(r.aop_deductible),
             "deductible_wind_hail_pct": int(r.wind_hail_pct) if float(r.wind_hail_pct).is_integer() else r.wind_hail_pct,
             "premium_annual_total": float(r.premium), "base_premium": float(r.premium), "discounts": [],
             "policy_fee": 0, "prior_premium": float(r.premium), "premium_change_pct": 0.0,
             "year_built": int(r.year_built), "square_feet": int(r.square_feet), "construction": "Brick Veneer",
             "carrier_rate": 0}
        street = homes.get(r.household_id, "").split("·")[-1].strip() or fake.street_address()
        p = {"first": "Demo", "name": "Demo Homeowner", "street": street, "city": "Austin", "zip": "78701",
             "policy_no": fake.bothify("DEMO#####").upper(), "agent": "Demo Agent", "period": "12/27/2026 - 12/27/2027"}
        labels = {f: (v.title() if v.islower() else v).replace("Aop ", "AOP ").replace("Ale", "ALE")
                  for f, v in kit["labels"].items()}
        s = printed(t, rng)
        s["deductible_all_peril"] = money(t["deductible_all_peril"])
        s["deductible_wind_hail_pct"] = f"{t['deductible_wind_hail_pct']}%"
        html = env.get_template(f"{layout}.html.j2").render(
            k=kit, L=labels, t=t, s=s, p=p, omit=set(), stacked=False, distractor=None, margin=0.6, fs=10.5,
            font="DejaVu Sans")
        out[r.doc_id] = to_data_uri(pdf_page(HTML(string=html).write_pdf()), "SYNTHETIC · DEMO LETTER",
                                    (40, 90, 170))
    return out


def real_letters():
    manifest = {r["doc_id"]: r for r in csv.DictReader(open(PRIVATE / "manifest.csv"))}
    q = pd.read_parquet(PRIVATE / "dashboard" / "fact_quote.parquet")
    out = {}
    for doc_id in q.doc_id:
        red = PRIVATE / "redacted" / f"{doc_id}_page_redacted_amounts.png"
        if red.exists():
            im = Image.open(red)
        else:
            m = manifest[doc_id]
            first = int((m.get("pages_range") or "1").split("-")[0]) - 1
            im = pdf_page((PRIVATE / "letters" / m["file"]).read_bytes(), first)
        out[doc_id] = to_data_uri(im, "REAL LETTER · PRIVATE", (160, 30, 30))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-private", action="store_true")
    a = ap.parse_args()
    images = demo_letters()
    if a.include_private:
        images |= real_letters()
    conn = connect_sql()
    cur = conn.cursor()
    for doc_id, uri in images.items():
        cur.execute("DELETE FROM review.document WHERE doc_id = ?", doc_id)
        cur.execute("INSERT INTO review.document (doc_id, page_image) VALUES (?, ?)", (doc_id, uri))
    conn.commit()
    print(f"review.document: {len(images)} quote letters stored "
          f"({sum(k.startswith('demo') for k in images)} demo, {sum(not k.startswith('demo') for k in images)} real)")


if __name__ == "__main__":
    from common import load_env
    load_env()
    main()
