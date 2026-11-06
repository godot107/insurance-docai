"""Run prebuilt-layout (or a custom model, --model) on the synthetic set → samples/synthetic/di/<layout|model>/S###.json.

Skips docs already analyzed. F0 is rate-limited, so calls are serial with retry/backoff.
Usage: .venv/bin/python src/synth_layout.py [--limit N]
"""
import argparse
import csv
import json
import time

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentAnalysisFeature
from azure.core.exceptions import HttpResponseError

from common import DOCINTEL_ENDPOINT, ROOT, credential

SYN = ROOT / "samples" / "synthetic"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model", default="prebuilt-layout", help="prebuilt-layout or a custom model id")
    args = ap.parse_args()
    client = DocumentIntelligenceClient(DOCINTEL_ENDPOINT, credential())
    out = SYN / "di" / ("layout" if args.model == "prebuilt-layout" else args.model)
    out.mkdir(parents=True, exist_ok=True)
    rows = [r for f in ("index.csv", "index_aug.csv") if (SYN / f).exists() for r in csv.DictReader(open(SYN / f))]
    if args.model != "prebuilt-layout":
        rows = [r for r in rows if r["split"] == "test"]   # custom models are only scored on held-out docs
    todo = [r["doc_id"] for r in rows if not (out / f"{r['doc_id']}.json").exists()]
    if args.limit:
        todo = todo[: args.limit]
    for i, doc_id in enumerate(todo, 1):
        for attempt in range(6):
            try:
                poller = client.begin_analyze_document(
                    args.model, AnalyzeDocumentRequest(bytes_source=(SYN / "pdf" / f"{doc_id}.pdf").read_bytes()),
                    features=[DocumentAnalysisFeature.KEY_VALUE_PAIRS] if args.model == "prebuilt-layout" else None)
                (out / f"{doc_id}.json").write_text(json.dumps(poller.result().as_dict()))
                break
            except HttpResponseError as e:
                if e.status_code != 429:
                    raise
                time.sleep(10 * (attempt + 1))
        if i % 10 == 0 or i == len(todo):
            print(f"{i}/{len(todo)}")


if __name__ == "__main__":
    main()
