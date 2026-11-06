"""Generate synthetic homeowners documents with exact ground truth.

Python plants every number (from a simple, documented rating formula); the carrier kits (kits.py) supply only
wording. Each document gets:
  samples/synthetic/pdf/S###.pdf      rendered with WeasyPrint; about half are "scanned" (rasterized at ~130 dpi,
                                      slightly rotated/blurred/JPEG-compressed, like the real mailers)
  samples/synthetic/labels/S###.json  canonical field values *as printed*, plus null for fields not shown
  samples/synthetic/index.csv         doc_id, carrier, layout, scanned, split (train/test by carrier)

Everything is fictional: invented carriers, Faker names/streets, real Texas ZIP codes only.
Augmentation (train carriers only, never test): --augment N writes A### docs + index_aug.csv with
  - field omission (teaches "not printed" → no value; v1 made up B/D/F on postcards)
  - carrier wording mixed onto other train layouts, labels stacked above values, distractor numbers
    (prior premium, "save up to $X", optional higher limits)
  - harsh scans: 72-115 dpi, ±4° skew, 180° flips, perspective warp, shadows, fax-style binarization
Usage: .venv/bin/python src/synth/generate.py [--per-carrier 12] [--seed 7]
       .venv/bin/python src/synth/generate.py --augment 150 --seed 101
"""
import argparse
import csv
import io
import json
import random
from pathlib import Path

import pypdfium2 as pdfium
from faker import Faker
from jinja2 import Environment, FileSystemLoader
from PIL import Image, ImageFilter
from weasyprint import HTML

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "samples" / "synthetic"
TEMPLATES = Path(__file__).parent / "templates"

# Which canonical fields each layout prints (others are null in the labels).
SHOWN = {
    "dec_page": ["coverage_a_dwelling", "coverage_b_other_structures", "coverage_c_personal_property",
                 "coverage_d_loss_of_use", "coverage_e_liability", "coverage_f_medical", "premium_annual_total",
                 "deductible_all_peril", "deductible_wind_hail_pct", "year_built", "square_feet"],
    "mailer_table": ["coverage_a_dwelling", "coverage_b_other_structures", "coverage_c_personal_property",
                     "coverage_d_loss_of_use", "coverage_e_liability", "coverage_f_medical",
                     "premium_annual_total", "year_built", "square_feet"],
    "mailer_prose": ["coverage_a_dwelling", "coverage_c_personal_property", "coverage_e_liability",
                     "premium_annual_total", "year_built"],
    "quote_proposal": ["coverage_a_dwelling", "coverage_b_other_structures", "coverage_c_personal_property",
                       "coverage_d_loss_of_use", "coverage_e_liability", "coverage_f_medical",
                       "premium_annual_total", "deductible_all_peril", "deductible_wind_hail_pct"],
    "postcard": ["coverage_a_dwelling", "coverage_c_personal_property", "coverage_e_liability",
                 "premium_annual_total"],
    "renewal_notice": ["coverage_a_dwelling", "coverage_c_personal_property", "coverage_e_liability",
                       "deductible_all_peril", "deductible_wind_hail_pct", "premium_annual_total"],
}
TEST_CARRIERS = {"C5", "C6"}   # postcard + renewal_notice layouts never appear in training
TX_ZIPS = {"Richmond": ["77406", "77407", "77469"], "Katy": ["77449", "77450", "77494"],
           "Sugar Land": ["77478", "77479"], "Cypress": ["77429", "77433"], "Pearland": ["77581", "77584"],
           "Round Rock": ["78664", "78665"], "Frisco": ["75033", "75034"], "San Antonio": ["78253", "78254"]}
DISCOUNTS = ["Claim Free Discount", "Multi-Policy Discount", "Paid in Full Discount", "New Home Discount",
             "Protective Device Discount", "Paperless Discount"]


def money(x, cents=False):
    return f"${x:,.2f}" if cents else f"${x:,.0f}"


def sample_truth(rng, carrier_rate):
    """One household + quote. Premium = A/1000 × carrier rate × deductible/age/construction factors − discounts + fee."""
    sqft = int(min(5200, max(1100, rng.lognormvariate(7.75, 0.28))))
    year = rng.randint(1978, 2025)
    construction = rng.choice(["Brick Veneer", "Brick Veneer", "Frame", "Stucco"])
    a = round(sqft * rng.uniform(165, 235), -2)
    b = round(a * rng.choice([0.05, 0.10, 0.10, 0.20]), -2)
    c = round(a * rng.choice([0.25, 0.40, 0.50, 0.50, 0.70, 0.75]), -2)
    d = round(a * rng.choice([0.10, 0.20, 0.20, 0.30]), -2)
    e = rng.choice([100000, 300000, 300000, 500000])
    f = rng.choice([1000, 2500, 5000, 5000])
    aop = rng.choice([1000, 1000, 2500, 5000, "1%"])
    aop_amt = round(a * 0.01) if aop == "1%" else aop
    wh = rng.choice([1, 2, 2, 5])
    ded_factor = {1000: 1.0, 2500: 0.9, 5000: 0.8}.get(aop, 0.85) * {1: 1.12, 2: 1.0, 5: 0.85}[wh]
    age_factor = 1.0 + max(0, 2026 - year - 10) * 0.01
    cons_factor = 1.08 if construction == "Frame" else 1.0
    base = round(a / 1000 * carrier_rate * ded_factor * age_factor * cons_factor, 2)
    discounts = [{"name": n, "amount": round(base * rng.uniform(0.03, 0.12), 2)}
                 for n in rng.sample(DISCOUNTS, rng.randint(0, 3))]
    fee = rng.choice([0, 25, 50, 90])
    total = round(base - sum(x["amount"] for x in discounts) + fee, 2)
    prior = round(total / (1 + rng.uniform(-0.05, 0.25)), 2)
    return {"coverage_a_dwelling": a, "coverage_b_other_structures": b, "coverage_c_personal_property": c,
            "coverage_d_loss_of_use": d, "coverage_e_liability": e, "coverage_f_medical": f,
            "deductible_all_peril": aop_amt, "deductible_all_peril_raw": aop, "deductible_wind_hail_pct": wh,
            "premium_annual_total": total, "base_premium": base, "discounts": discounts, "policy_fee": fee,
            "prior_premium": prior, "premium_change_pct": round(100 * (total / prior - 1), 1),
            "year_built": year, "square_feet": sqft, "construction": construction, "carrier_rate": carrier_rate}


def printed(t, rng):
    """Value strings as they appear on the page (formats vary per document)."""
    cents = rng.random() < 0.35
    s = {k: money(t[k], cents) for k in ["coverage_a_dwelling", "coverage_b_other_structures",
                                         "coverage_c_personal_property", "coverage_d_loss_of_use",
                                         "coverage_e_liability", "coverage_f_medical"]}
    s["premium_annual_total"] = money(t["premium_annual_total"], True)
    s["base_premium"] = money(t["base_premium"], True)
    s["deductible_all_peril"] = (f"1% ({money(t['deductible_all_peril'])})" if t["deductible_all_peril_raw"] == "1%"
                                 else money(t["deductible_all_peril"]))
    wh_amt = money(t["coverage_a_dwelling"] * t["deductible_wind_hail_pct"] / 100)
    s["deductible_wind_hail_pct"] = rng.choice([f"{t['deductible_wind_hail_pct']}%",
                                                f"{t['deductible_wind_hail_pct']}% - {wh_amt}",
                                                f"{t['deductible_wind_hail_pct']}% of Coverage A ({wh_amt})"])
    return s


def person(fake, rng):
    city = rng.choice(list(TX_ZIPS))
    first, last = fake.first_name(), fake.last_name()
    return {"first": first, "name": f"{first} {last}", "street": fake.street_address(), "city": city,
            "zip": rng.choice(TX_ZIPS[city]), "policy_no": fake.bothify("??#######").upper(),
            "agent": fake.name(), "period": "12/01/2026 - 12/01/2027"}


def scan(pdf_bytes, rng):
    """Rasterize like a cheap home scan: ~130 dpi, slight skew, blur, JPEG artifacts."""
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=rng.uniform(1.6, 1.9)).to_pil().convert("L")
    img = img.rotate(rng.uniform(-1.5, 1.5), expand=True, fillcolor=255, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.9)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=rng.randint(35, 70))
    out = io.BytesIO()
    Image.open(buf).save(out, "PDF", resolution=130)
    return out.getvalue()


def harsh_scan(pdf_bytes, rng):
    """Worse than scan(): phone photo / fax / old scanner conditions."""
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=rng.uniform(1.0, 1.6)).to_pil().convert("L")
    w, h = img.size
    if rng.random() < 0.4:   # perspective (phone photo): jitter the 4 corners by up to 3%
        j = lambda: rng.uniform(0, 0.03)
        quad = (w * j(), h * j(), w * j(), h * (1 - j()), w * (1 - j()), h * (1 - j()), w * (1 - j()), h * j())
        img = img.transform((w, h), Image.QUAD, quad, resample=Image.BICUBIC, fillcolor=255)
    img = img.rotate(rng.uniform(-4, 4) + (180 if rng.random() < 0.1 else 0), expand=True, fillcolor=255,
                     resample=Image.BICUBIC)
    if rng.random() < 0.4:   # uneven lighting: darken one side
        grad = Image.linear_gradient("L").resize(img.size).rotate(rng.choice([0, 90, 180, 270]))
        img = Image.composite(img, Image.new("L", img.size, 0), grad.point(lambda v: 160 + v * 95 // 255))
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.2)))
    if rng.random() < 0.25:  # fax-style black and white
        img = img.point(lambda v: 255 if v > rng.randint(140, 190) else 0).convert("L")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=rng.randint(25, 60))
    out = io.BytesIO()
    Image.open(buf).save(out, "PDF", resolution=100)
    return out.getvalue()


def augment(args, rng, fake, kits, env):
    train = [k for k in kits if k["carrier_id"] not in TEST_CARRIERS]
    layouts = sorted({k["layout"] for k in train})
    rates = {k["carrier_id"]: round(rng.uniform(4.5, 13.0), 2) for k in kits}
    index = []
    for n in range(1, args.augment + 1):
        k, layout = rng.choice(train), rng.choice(layouts)
        labels = {f: (v.title() if v.islower() else v).replace("Aop ", "AOP ") for f, v in k["labels"].items()}
        t, p = sample_truth(rng, rates[k["carrier_id"]]), person(fake, rng)
        shown = list(SHOWN[layout])
        omit = set()
        if layout != "mailer_prose" and rng.random() < 0.6:
            omit = set(rng.sample([f for f in shown if f != "premium_annual_total"], rng.randint(1, 4)))
        distractor = rng.choice([None, None,
                                 f"Neighbors who switched saved an average of {money(rng.uniform(250, 1500))} a year!",
                                 f"Last term you paid {money(t['prior_premium'], True)}.",
                                 f"Want more protection? Raise your limit to {money(round(t['coverage_a_dwelling'] * 1.15, -3))}."])
        html = env.get_template(f"{layout}.html.j2").render(
            k=k, L=labels, t=t, s=printed(t, rng), p=p, omit=omit, stacked=rng.random() < 0.3, distractor=distractor,
            margin=rng.choice([0.5, 0.6, 0.75]), fs=rng.choice([8.5, 9, 10, 11, 12]),
            font=rng.choice(["DejaVu Sans", "DejaVu Serif", "Liberation Sans", "Liberation Serif", "sans-serif"]))
        pdf = HTML(string=html).write_pdf()
        mode = rng.choices(["clean", "scan", "harsh"], [0.2, 0.3, 0.5])[0]
        pdf = harsh_scan(pdf, rng) if mode == "harsh" else scan(pdf, rng) if mode == "scan" else pdf
        doc_id = f"A{n:03d}"
        (SYN / "pdf" / f"{doc_id}.pdf").write_bytes(pdf)
        visible = [f for f in shown if f not in omit]
        truth = {f: (t[f] if f in visible else None) for f in SHOWN["dec_page"]}
        (SYN / "labels" / f"{doc_id}.json").write_text(json.dumps({
            "doc_id": doc_id, "carrier_id": k["carrier_id"], "layout": layout, "scanned": mode != "clean",
            "fields": truth, "labels_used": {f: labels[f] for f in visible},
            "augment": {"omitted": sorted(omit), "distractor": bool(distractor), "scan_mode": mode}}, indent=1))
        index.append({"doc_id": doc_id, "carrier_id": k["carrier_id"], "carrier_name": k["carrier_name"],
                      "layout": layout, "scanned": mode != "clean", "split": "train", "scan_mode": mode,
                      "omitted": len(omit), "distractor": bool(distractor)})
    with open(SYN / "index_aug.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=index[0].keys())
        w.writeheader()
        w.writerows(index)
    print(f"{len(index)} augmented train docs -> samples/synthetic/ (index_aug.csv)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-carrier", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--augment", type=int, default=0, help="write N augmented TRAIN docs instead of the base set")
    args = ap.parse_args()
    rng, fake = random.Random(args.seed), Faker("en_US")
    Faker.seed(args.seed)
    kits = json.load(open(SYN / "carriers.json"))
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    env.globals.update(money=lambda x: money(x, True), fmt_int=lambda x: f"{x:,}")
    (SYN / "pdf").mkdir(parents=True, exist_ok=True)
    (SYN / "labels").mkdir(parents=True, exist_ok=True)
    if args.augment:
        return augment(args, rng, fake, kits, env)
    rates = {k["carrier_id"]: round(rng.uniform(4.5, 13.0), 2) for k in kits}   # carriers differ in price level
    index, n = [], 0
    for k in kits:
        labels = {f: (v.title() if v.islower() else v).replace("Aop ", "AOP ").replace("Ale", "ALE")
                  for f, v in k["labels"].items()}
        tpl = env.get_template(f"{k['layout']}.html.j2")
        for _ in range(args.per_carrier):
            n += 1
            doc_id = f"S{n:03d}"
            t, p = sample_truth(rng, rates[k["carrier_id"]]), person(fake, rng)
            html = tpl.render(k=k, L=labels, t=t, s=printed(t, rng), p=p, omit=set(), stacked=False, distractor=None,
                              margin=rng.choice([0.5, 0.6, 0.75]), fs=rng.choice([9, 10, 10.5, 11]),
                              font=rng.choice(["DejaVu Sans", "DejaVu Serif", "Liberation Sans", "sans-serif"]))
            pdf = HTML(string=html).write_pdf()
            scanned = rng.random() < 0.5
            if scanned:
                pdf = scan(pdf, rng)
            (SYN / "pdf" / f"{doc_id}.pdf").write_bytes(pdf)
            shown = SHOWN[k["layout"]]
            truth = {f: (t[f] if f in shown else None) for f in SHOWN["dec_page"]}
            (SYN / "labels" / f"{doc_id}.json").write_text(json.dumps({
                "doc_id": doc_id, "carrier_id": k["carrier_id"], "layout": k["layout"], "scanned": scanned,
                "fields": truth, "labels_used": {f: labels[f] for f in shown},
                "hidden_truth": {"base_premium": t["base_premium"], "discounts": t["discounts"],
                                 "policy_fee": t["policy_fee"], "carrier_rate": t["carrier_rate"],
                                 "construction": t["construction"], "prior_premium": t["prior_premium"]},
            }, indent=1))
            index.append({"doc_id": doc_id, "carrier_id": k["carrier_id"], "carrier_name": k["carrier_name"],
                          "layout": k["layout"], "scanned": scanned,
                          "split": "test" if k["carrier_id"] in TEST_CARRIERS else "train"})
        print(f"{k['carrier_id']} {k['layout']:15} rate={rates[k['carrier_id']]:5} x{args.per_carrier}")
    with open(SYN / "index.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=index[0].keys())
        w.writeheader()
        w.writerows(index)
    print(f"{len(index)} docs -> samples/synthetic/")


if __name__ == "__main__":
    main()
