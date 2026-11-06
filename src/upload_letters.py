"""Upload private/letters/*.pdf to the private blob container and append new ones to private/manifest.csv.
Existing rows (matched by sha256) keep their doc_id, so IDs never shift.

Blob names are content hashes, not file names, so nothing identifying ends up in blob names.
Usage: .venv/bin/python src/upload_letters.py
"""
import csv
import hashlib

from azure.storage.blob import BlobServiceClient
from pypdf import PdfReader

from common import BLOB_ENDPOINT, LETTERS, PRIVATE, credential


def main():
    container = BlobServiceClient(BLOB_ENDPOINT, credential=credential()).get_container_client("raw-private")
    manifest = PRIVATE / "manifest.csv"
    rows = list(csv.DictReader(open(manifest))) if manifest.exists() else []
    known = {r["sha256"] for r in rows}
    for pdf in sorted(LETTERS.glob("*.pdf")):
        data = pdf.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha in known:          # already uploaded: keep its doc_id stable
            continue
        # max existing number + 1: manifest rows can be split or removed, so a row count could reuse an ID
        doc_id = f"L{max((int(r['doc_id'][1:]) for r in rows), default=0) + 1:02d}"
        blob = f"letters/{sha[:16]}.pdf"
        container.upload_blob(blob, data, overwrite=True)
        pages = len(PdfReader(pdf).pages)
        rows.append({"household_id": "h001", "doc_id": doc_id, "file": pdf.name, "sha256": sha, "blob": blob, "pages": pages,
                     "pages_range": "", "source": "scan", "year": "", "carrier_code": "", "doc_type": "", "notes": ""})
        print(f"{doc_id}  pages={pages}  -> raw-private/{blob}")
    with open(manifest, "w", newline="") as f:
        fields = list(dict.fromkeys(k for r in rows for k in r))   # union: older rows may lack new columns
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    print(f"manifest: {len(rows)} rows -> private/manifest.csv")


if __name__ == "__main__":
    main()
