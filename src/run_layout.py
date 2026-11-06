"""Run Document Intelligence prebuilt-layout on every letter in private/manifest.csv.

Full results (text, tables, key-value pairs, confidences) go to private/di/layout/<doc_id>.json.
Prints a PII-free quality summary only.
Manifest column `pages` (optional) limits analysis to a page range.
Usage: .venv/bin/python src/run_layout.py [--model prebuilt-layout]
"""
import argparse
import csv
import json
import statistics as st

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentAnalysisFeature

from common import DI_OUT, DOCINTEL_ENDPOINT, LETTERS, PRIVATE, credential


def summarize(doc_id, r):
    words = [w for p in r.pages for w in (p.words or [])]
    conf = [w.confidence for w in words]
    kv = r.key_value_pairs or []
    return {
        "doc_id": doc_id,
        "pages": len(r.pages),
        "angle": round(max(abs(p.angle or 0) for p in r.pages), 1),
        "words": len(words),
        "word_conf_mean": round(st.mean(conf), 3) if conf else None,
        "word_conf_p10": round(sorted(conf)[len(conf) // 10], 3) if conf else None,
        "pct_words_below_0.8": round(100 * sum(c < 0.8 for c in conf) / len(conf), 1) if conf else None,
        "tables": len(r.tables or []),
        "kv_pairs": len(kv),
        "kv_conf_mean": round(st.mean(k.confidence for k in kv), 3) if kv else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="prebuilt-layout")
    ap.add_argument("--force", action="store_true", help="re-analyze docs that already have output")
    args = ap.parse_args()
    client = DocumentIntelligenceClient(DOCINTEL_ENDPOINT, credential())
    out_dir = DI_OUT / args.model.replace("prebuilt-", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(PRIVATE / "manifest.csv")))
    summary = []
    for row in rows:
        out = out_dir / f"{row['doc_id']}.json"
        if out.exists() and not args.force:   # don't spend pages re-analyzing
            continue
        data = (LETTERS / row["file"]).read_bytes()
        poller = client.begin_analyze_document(
            args.model, AnalyzeDocumentRequest(bytes_source=data),
            pages=row.get("pages_range") or None,   # e.g. "19-20": analyze only the useful pages of a long packet
            features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS] if args.model == "prebuilt-layout" else None)
        result = poller.result()
        out.write_text(json.dumps(result.as_dict(), indent=1))
        s = summarize(row["doc_id"], result)
        summary.append(s)
        print(json.dumps(s))
    old = json.loads((out_dir / "_summary.json").read_text()) if (out_dir / "_summary.json").exists() else []
    merged = {s["doc_id"]: s for s in old + summary}
    (out_dir / "_summary.json").write_text(json.dumps(sorted(merged.values(), key=lambda s: s["doc_id"]), indent=1))


if __name__ == "__main__":
    main()
