"""Fictional carrier "style kits" written by the Foundry model (gpt-4.1-mini), cached to samples/synthetic/carriers.json.

The LLM only supplies wording: carrier name, tagline, marketing copy, and which label text the carrier uses for
each canonical field (picked from known synonyms or newly invented, to test generalization). It never chooses
numbers, so ground truth stays exact. Re-running uses the cache unless --refresh.
Usage: .venv/bin/python src/synth/kits.py [--refresh]
"""
import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from azure.identity import AzureCliCredential, get_bearer_token_provider
from dotenv import load_dotenv
from openai import AzureOpenAI

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "samples" / "synthetic" / "carriers.json"
SCHEMA = yaml.safe_load(open(ROOT / "schema" / "fields.yaml"))["fields"]
LABELED = ["coverage_a_dwelling", "coverage_b_other_structures", "coverage_c_personal_property",
           "coverage_d_loss_of_use", "coverage_e_liability", "coverage_f_medical", "premium_annual_total",
           "deductible_all_peril", "deductible_wind_hail_pct", "year_built", "square_feet"]
LAYOUTS = ["dec_page", "mailer_table", "mailer_prose", "quote_proposal", "postcard", "renewal_notice",
           "dec_page", "mailer_table"]

PROMPT = """You are creating a FICTIONAL Texas homeowners-insurance company for a synthetic test dataset.
The name must be invented and must not resemble any real insurer or agency (no State Farm, Allstate, Progressive,
USAA, Farmers, Liberty, Nationwide, Travelers, Chubb, ASI, Germania, Texas Farm Bureau, etc.).
Avoid Texas cliches (Lone Star, Bluebonnet, Alamo, Longhorn) and these names already used: {used}.
Build the name from this unusual seed word: "{seed}".
Document style: {layout}. Tone: {tone}.

For each canonical field below, give the exact label text this company prints next to the value.
Use one of the known label variants OR invent a plausible new one (invent for roughly a third of fields).
Known variants: {synonyms}

Return JSON with keys:
  "carrier_name", "agency_name", "tagline", "brand_color" (hex, dark enough for white text),
  "headline" (mailer/notice headline, <= 10 words),
  "paragraphs" (list of 3 short marketing or notice paragraphs, no specific prices or percentages),
  "cta" (call-to-action line, no phone number),
  "disclaimer" (1-2 sentences of fine print),
  "labels" (object: canonical field -> label text)
"""
SEEDS = ["Kestrel", "Marlow", "Quarry", "Tidewell", "Corbel", "Ashgrove", "Pennant", "Wrenfield"]
TONES = ["formal insurer", "pushy direct-mail agent", "friendly local agency", "plain-spoken regional mutual",
         "slick national-brand style", "neutral renewal notice", "bureaucratic", "upbeat comparison-shopping"]


def client():
    load_dotenv(ROOT / ".env")
    tp = get_bearer_token_provider(AzureCliCredential(), "https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(azure_endpoint=os.environ["OPENAI_ENDPOINT"], azure_ad_token_provider=tp,
                       api_version="2024-10-21")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    if OUT.exists() and not args.refresh:
        print(f"cached: {OUT}")
        return
    c, kits, tokens = client(), [], 0
    syn = {f: SCHEMA[f].get("synonyms", []) for f in LABELED}
    for i, (layout, tone) in enumerate(zip(LAYOUTS, TONES)):
        r = c.chat.completions.create(
            model=os.environ["CHAT_DEPLOYMENT"], temperature=1.0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": PROMPT.format(layout=layout, tone=tone, synonyms=json.dumps(syn),
                                                               used=[k["carrier_name"] for k in kits] or "none", seed=SEEDS[i])}])
        kit = json.loads(r.choices[0].message.content)
        missing = [f for f in LABELED if f not in kit.get("labels", {})]
        if missing:
            sys.exit(f"kit {i} missing labels {missing}")
        kit.update(carrier_id=f"C{i + 1}", layout=layout, tone=tone)
        kits.append(kit)
        tokens += r.usage.total_tokens
        print(f"C{i + 1} {layout:15} {kit['carrier_name']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(kits, indent=1))
    print(f"{len(kits)} kits, {tokens} tokens -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
