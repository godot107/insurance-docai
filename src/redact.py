"""Redact personal information from extracted document text before anything leaves private/.

Three layers:
  1. Azure AI Language PII detection (the Azure counterpart of AWS Comprehend DetectPiiEntities):
     names, addresses, phone numbers, emails, ID numbers. Non-personal categories are kept because the
     analysis needs them: Organization (carriers/agencies), DateTime (policy periods), PersonType, URL.
  2. Label-anchored IDs: whatever follows "Policy Number", "Loan #", "Reference#", "Control No", "Agent Code"...
     Generic models miss insurer-specific ID formats (measured: policy + loan numbers on the dec page).
     This layer needs no per-household setup, so it scales to other users.
  3. Deny-list: known household identifiers in private/pii_terms.txt (optional, belt and braces).
Writes private/redacted/<doc_id>.txt and private/redacted/_report.json, and prints a PII-free summary:
entity counts by category, and how many deny-list terms survived layer 1 (= what the service missed).

Usage: .venv/bin/python src/redact.py [--min-score 0.5]
"""
import argparse
import csv
import json
import os
import re
from collections import Counter

from azure.ai.textanalytics import TextAnalyticsClient

from common import DI_OUT, PRIVATE, credential

KEEP = {"Organization", "DateTime", "PersonType", "URL"}
ID_LABEL = re.compile(r"(?i)\b(policy\s*(?:number|no\.?|#)|loan\s*(?:number|no\.?|#)|reference\s*#|control\s*no\.?|"
                      r"agent\s*(?:code|id)|account\s*(?:number|no\.?|#)|claim\s*(?:number|no\.?|#))[\s:#]*([A-Z0-9][A-Z0-9-]{4,})")
CHUNK = 5000   # service limit is 5,120 characters per document


def chunks(text):
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK)
        if end < len(text):   # cut at a line break so entities aren't split
            nl = text.rfind("\n", start, end)
            end = nl + 1 if nl > start else end
        out.append((start, text[start:end]))
        start = end
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=float, default=0.5)
    args = ap.parse_args()
    client = TextAnalyticsClient(os.environ["LANGUAGE_ENDPOINT"], credential())
    terms = [t.strip() for t in open(PRIVATE / "pii_terms.txt") if t.strip() and not t.startswith("#")]
    terms.sort(key=len, reverse=True)
    out_dir = PRIVATE / "redacted"
    out_dir.mkdir(exist_ok=True)
    report, totals, missed_total = {}, Counter(), 0
    for row in csv.DictReader(open(PRIVATE / "manifest.csv")):
        if row["doc_type"] == "superseded":
            continue
        path = DI_OUT / "layout" / f"{row['doc_id']}.json"
        if not path.exists():
            continue
        text = json.load(open(path))["content"]
        spans, cats = [], Counter()
        parts = chunks(text)
        for i in range(0, len(parts), 5):   # 5 documents per request on F0
            batch = parts[i:i + 5]
            res = client.recognize_pii_entities([t for _, t in batch], language="en")
            for (offset, _), r in zip(batch, res):
                if r.is_error:
                    raise SystemExit(f"{row['doc_id']}: {r.error}")
                for e in r.entities:
                    if e.confidence_score >= args.min_score and e.category not in KEEP:
                        spans.append((offset + e.offset, offset + e.offset + e.length, e.category))
                        cats[e.category] += 1
        redacted = list(text)
        for s, e, cat in spans:
            for k in range(s, e):
                redacted[k] = "\0"
        layer1 = "".join(redacted)
        missed = [t for t in terms if re.search(re.escape(t), layer1.replace("\0", " "), re.I)]
        # layer 2: label-anchored IDs
        n_ids = 0
        for m in ID_LABEL.finditer(text):
            n_ids += 1
            for k in range(m.start(2), m.end(2)):
                redacted[k] = "\0"
        after2 = "".join(redacted).replace("\0", " ")
        missed_after2 = [t for t in terms if re.search(re.escape(t), after2, re.I)]
        # layer 3: deny-list
        for t in terms:
            for m in re.finditer(re.escape(t), layer1, re.I):
                for k in range(m.start(), m.end()):
                    redacted[k] = "\0"
        final = re.sub("\0+", "[REDACTED]", "".join(redacted))
        (out_dir / f"{row['doc_id']}.txt").write_text(final)
        leftover = [t for t in terms if re.search(re.escape(t), final, re.I)]
        report[row["doc_id"]] = {"categories": dict(cats), "denylist_terms_missed_by_service": len(missed),
                                 "label_anchored_ids": n_ids, "denylist_terms_left_after_layers_1_2": len(missed_after2),
                                 "denylist_terms_left_after_both": len(leftover)}
        totals.update(cats)
        missed_total += len(missed)
        missed2_total = report.setdefault("_m2", 0) + len(missed_after2)
        report["_m2"] = missed2_total
        print(f"{row['doc_id']}: {sum(cats.values()):3} entities by service, {n_ids} label-anchored IDs; known terms "
              f"missed by service {len(missed)}, after layer 2 {len(missed_after2)}, after all {len(leftover)}")
    (out_dir / "_report.json").write_text(json.dumps(report, indent=1))
    print(f"\nredacted categories: {dict(totals.most_common())}\nknown terms missed by the service: {missed_total}; "
          f"still missed after the generic ID layer: {report.pop('_m2', 0)}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    from common import ROOT
    load_dotenv(ROOT / ".env", override=True)
    main()
