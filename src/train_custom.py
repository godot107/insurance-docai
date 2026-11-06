"""Train a custom neural extraction model on synthetic/train/ (labels from build_training_set.py).

DI reads the container with its own managed identity (Storage Blob Data Reader, granted in main.bicep),
so no SAS token or account key is involved.
Usage: .venv/bin/python src/train_custom.py --model-id ins-neural-v1 [--mode neural|template]
"""
import argparse
import time

from azure.ai.documentintelligence import DocumentIntelligenceAdministrationClient
from azure.ai.documentintelligence.models import AzureBlobContentSource, BuildDocumentModelRequest

from common import BLOB_ENDPOINT, DOCINTEL_ENDPOINT, credential


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", default="ins-neural-v1")
    ap.add_argument("--mode", default="neural", choices=["neural", "template"])
    ap.add_argument("--prefix", default="train/")
    args = ap.parse_args()
    admin = DocumentIntelligenceAdministrationClient(DOCINTEL_ENDPOINT, credential())
    t0 = time.time()
    poller = admin.begin_build_document_model(BuildDocumentModelRequest(
        model_id=args.model_id, build_mode=args.mode,
        description="Homeowners docs: coverages A-F, premium, deductibles, property facts (synthetic train carriers)",
        azure_blob_source=AzureBlobContentSource(container_url=f"{BLOB_ENDPOINT}synthetic", prefix=args.prefix)))
    model = poller.result()
    print(f"built {model.model_id} in {time.time() - t0:.0f}s; fields: {sorted(model.doc_types[args.model_id].field_schema)}")


if __name__ == "__main__":
    main()
