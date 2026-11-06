"""Turn synthetic ground truth into Document Intelligence training labels, and upload them.

For each TRAIN doc (test carriers are never uploaded), every printed field value is located in the layout
OCR (word whose number equals the truth; ties broken by distance to the field's label text), and written in
DI's labeling format, the same files Document Intelligence Studio produces when a person labels by hand:
  train/S###.pdf, train/S###.pdf.ocr.json, train/S###.pdf.labels.json, train/fields.json
Upload target: blob container `synthetic`, prefix `train/` (DI reads it with its managed identity).

Usage: .venv/bin/python src/build_training_set.py [--dry-run]
"""
import argparse
import csv
import json
import re

from azure.storage.blob import BlobServiceClient

from common import BLOB_ENDPOINT, ROOT, credential
from normalize import FIELDS

SYN = ROOT / "samples" / "synthetic"
NUM = re.compile(r"^\(?\$?(\d[\d,]*(?:\.\d+)?)(%?)\)?[.,:;]?$")
FIELDS_JSON = {
    "$schema": "https://schema.cognitiveservices.azure.com/formrecognizer/2021-03-01/fields.json",
    "fields": [{"fieldKey": f, "fieldType": "string", "fieldFormat": "not-specified"} for f in FIELDS
               if f != "policy_fee"],
    "definitions": {},
}


def word_number(text):
    m = NUM.match(text.strip())
    if not m:
        return None, None
    return float(m.group(1).replace(",", "")), bool(m.group(2))


def center(poly):
    xs, ys = poly[0::2], poly[1::2]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def locate(page, field, truth, label):
    """The OCR word holding `truth` for `field`, nearest to its label if several match."""
    is_pct = FIELDS[field]["type"] == "percent"
    hits = []
    for w in page["words"]:
        v, pct = word_number(w["content"])
        if v is not None and abs(v - truth) < 0.005 and pct == is_pct:
            hits.append(w)
    if len(hits) <= 1:
        return hits[0] if hits else None
    lab = label.lower().rstrip(":")
    lines = [ln for ln in page.get("lines", []) if lab in ln["content"].lower()]
    if not lines:
        return hits[0]
    lx, ly = center(lines[0]["polygon"])
    return min(hits, key=lambda w: abs(center(w["polygon"])[1] - ly) * 3 + abs(center(w["polygon"])[0] - lx))


def labels_for(doc_id, layout, truth, labels_used):
    page = layout["pages"][0]
    W, H = page["width"], page["height"]
    out, missing = [], []
    for field, t in truth.items():
        if t is None or field not in labels_used and field != "year_built":
            continue
        w = locate(page, field, float(t), labels_used.get(field, ""))
        if w is None:
            missing.append(field)
            continue
        p = w["polygon"]
        box = [round(v / (W if i % 2 == 0 else H), 6) for i, v in enumerate(p)]
        text = w["content"].strip("()").rstrip(".,:;")
        out.append({"label": field, "value": [{"page": 1, "text": text, "boundingBoxes": [box]}]})
    return {"$schema": "https://schema.cognitiveservices.azure.com/formrecognizer/2021-03-01/labels.json",
            "document": f"{doc_id}.pdf", "labels": out}, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prefix", default="train/", help="blob prefix; v2 uses train-v2/")
    ap.add_argument("--with-augmented", action="store_true", help="add index_aug.csv docs (A###)")
    ap.add_argument("--skip-incomplete", action="store_true",
                    help="leave out docs where a printed value can't be found in the OCR (partial labels teach "
                         "the model to ignore printed values)")
    args = ap.parse_args()
    files = ["index.csv"] + (["index_aug.csv"] if args.with_augmented else [])
    index = [r for f in files for r in csv.DictReader(open(SYN / f)) if r["split"] == "train"]
    index = [r for r in index if (SYN / "di" / "layout" / f"{r['doc_id']}.json").exists()]
    cont = None if args.dry_run else BlobServiceClient(BLOB_ENDPOINT, credential=credential()).get_container_client("synthetic")
    n_labels, n_missing, skipped = 0, 0, []
    for r in index:
        d = r["doc_id"]
        lab = json.load(open(SYN / "labels" / f"{d}.json"))
        layout = json.load(open(SYN / "di" / "layout" / f"{d}.json"))
        labels, missing = labels_for(d, layout, lab["fields"], lab["labels_used"])
        n_missing += len(missing)
        if missing:
            print(f"{d} {r['layout']}: could not locate {missing}{' -> skipped' if args.skip_incomplete else ''}")
            if args.skip_incomplete:
                skipped.append(d)
                continue
        n_labels += len(labels["labels"])
        if cont:
            cont.upload_blob(f"{args.prefix}{d}.pdf", (SYN / "pdf" / f"{d}.pdf").read_bytes(), overwrite=True)
            cont.upload_blob(f"{args.prefix}{d}.pdf.labels.json", json.dumps(labels), overwrite=True)
            cont.upload_blob(f"{args.prefix}{d}.pdf.ocr.json",
                             json.dumps({"status": "succeeded", "analyzeResult": layout}), overwrite=True)
    if cont:
        cont.upload_blob(f"{args.prefix}fields.json", json.dumps(FIELDS_JSON), overwrite=True)
    print(f"{len(index) - len(skipped)} train docs used ({len(skipped)} skipped), {n_labels} field labels located, {n_missing} not found"
          f"{' (dry run)' if args.dry_run else f' -> synthetic/{args.prefix}'}")


if __name__ == "__main__":
    main()
