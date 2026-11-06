"""Map Document Intelligence layout output onto the canonical schema (schema/fields.yaml).

For each document and schema field:
  1. look for a key-value pair whose key matches a synonym (method "kv", DI's pair confidence)
  2. otherwise search the page text for "<synonym> ... <value>" (method "text", confidence = lowest
     word confidence inside the value)
  3. apply review rules: low confidence, out-of-range, implausible ratio, missing required, conflicts

Outputs (private, per household):
  private/out/fields.csv       one row per (doc, field): value, confidence, method, source label, flags
  private/out/review_queue.csv the rows a person must confirm before they're trusted
  private/out/comparison.csv   one row per doc, compared with the household's current declarations page

Usage: .venv/bin/python src/normalize.py [--household h001]
"""
import argparse
import csv
import json
import re

import yaml

from common import DI_OUT, PRIVATE, ROOT

SCHEMA = yaml.safe_load(open(ROOT / "schema" / "fields.yaml"))
FIELDS = SCHEMA["fields"]
THRESHOLD = SCHEMA["review"]["confidence_threshold"]
OUT = PRIVATE / "out"

MONEY = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d\d))?")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
INT = re.compile(r"(?<![\d,$.])(\d{1,3}(?:,\d{3})+|\d{3,5})(?![\d,])")  # "2,529" → 2529; skip "$1,878" pieces


def norm_label(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    s = re.sub(r"^(section [iv]+ )?([a-f] )(?=[a-z]{3})", "", s)  # "A. Dwelling" → "dwelling"
    return s


def parse(ftype: str, text: str):
    """Return (value, (start, end) of the match within text) or (None, None)."""
    rx = {"money": MONEY, "percent": PERCENT, "int": INT}.get(ftype)
    if rx is None:
        return (text.strip() or None), (0, len(text))
    for m in rx.finditer(text):
        if ftype == "money":
            if text[m.end():m.end() + 1] == "%":   # "1% ($2,687)": the 1 is a percent, not dollars
                continue
            v = float(m.group(1).replace(",", "") + "." + (m.group(2) or "0"))
            if v == 0 and "$" not in m.group(0):
                continue
        else:
            v = float(m.group(1)) if ftype == "percent" else int(m.group(1).replace(",", ""))
        return v, m.span()
    return None, None


def word_conf(doc, start, end):
    """Lowest confidence among OCR words overlapping content[start:end]."""
    confs = [w["confidence"] for p in doc["pages"] for w in p.get("words", [])
             if w["span"]["offset"] < end and w["span"]["offset"] + w["span"]["length"] > start]
    return min(confs) if confs else None


def match_field(label: str):
    """Field whose synonym matches the label; the longest synonym wins (so 'liability' ≠ 'personal liability ...')."""
    best, best_len = None, 0
    for name, spec in FIELDS.items():
        for syn in spec.get("synonyms", []):
            s = norm_label(syn)
            if (label == s or label.startswith(s + " ")) and len(s) > best_len:
                best, best_len = name, len(s)
    return best


def candidates(doc):
    """All (field, value, confidence, method, label) candidates in a document."""
    out = []
    for kv in doc.get("keyValuePairs") or []:
        field = match_field(norm_label(kv["key"]["content"]))
        if not field or not kv.get("value"):
            continue
        val, _ = parse(FIELDS[field]["type"], kv["value"]["content"])
        if val is not None:
            out.append((field, val, kv["confidence"], "kv", kv["key"]["content"].strip()))
    found = {c[0] for c in out}
    text = doc["content"]
    for field, spec in FIELDS.items():
        if field in found:
            continue
        pats = [spec["pattern"]] if "pattern" in spec else [
            r"\b" + r"[\W_]+".join(map(re.escape, norm_label(s).split())) + r"\b[^$\d%\n]{0,40}"
            for s in sorted(spec.get("synonyms", []), key=len, reverse=True)]
        for pat in pats:
            m = re.search(pat, text, re.I)
            if not m:
                continue
            tail = text[m.end(): m.end() + 30] if "pattern" not in spec else m.group(1)
            val, span = parse(spec["type"], tail)
            if val is None:
                continue
            base = m.end() if "pattern" not in spec else m.start(1)
            conf = word_conf(doc, base + span[0], base + span[1])
            out.append((field, val, conf, "text", m.group(0).strip()[:40]))
            break
    return out


def review(field, value, conf, doc_type, values, n_distinct):
    spec, flags = FIELDS[field], []
    if value is None:
        if doc_type in spec.get("required", []):
            flags.append("missing_required")
        return flags
    if conf is None or conf < THRESHOLD:
        flags.append("low_confidence")
    lo_hi = spec.get("range")
    if lo_hi and not (lo_hi[0] <= value <= lo_hi[1]):
        flags.append("out_of_range")
    r = spec.get("ratio_to")
    if r and values.get(r["field"]):
        ratio = value / values[r["field"]]
        if not (r["range"][0] <= ratio <= r["range"][1]):
            flags.append(f"ratio_vs_{r['field']}")
    if n_distinct > 1:
        flags.append("conflicting_values")
    return flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--household", default="h001")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = [r for r in csv.DictReader(open(PRIVATE / "manifest.csv"))
                if r.get("household_id", "h001") == args.household]

    field_rows, docs = [], {}
    for row in manifest:
        path = DI_OUT / "layout" / f"{row['doc_id']}.json"
        if not path.exists():
            continue
        cands = candidates(json.load(open(path)))
        best, distinct = {}, {}
        for f, v, c, m, lab in cands:
            distinct.setdefault(f, set()).add(v)
            if f not in best or (c or 0) > (best[f][1] or 0):
                best[f] = (v, c, m, lab)
        values = {f: b[0] for f, b in best.items()}
        for field in FIELDS:
            v, c, m, lab = best.get(field, (None, None, None, None))
            flags = review(field, v, c, row["doc_type"], values, len(distinct.get(field, ())))
            field_rows.append({"household_id": args.household, "doc_id": row["doc_id"], "doc_type": row["doc_type"],
                               "field": field, "value": v, "confidence": round(c, 3) if c is not None else None,
                               "method": m, "source_label": lab, "flags": ";".join(flags),
                               "needs_review": bool(flags)})
        docs[row["doc_id"]] = {"doc_type": row["doc_type"], **values}

    def write(name, rows):
        with open(OUT / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)

    write("fields.csv", field_rows)
    SKIP = {"ad", "superseded", "premium-notice"}
    queue = [r for r in field_rows if r["needs_review"] and r["doc_type"] not in SKIP | {"rating-info"}]
    write("review_queue.csv", queue or [{k: "" for k in field_rows[0]}])

    # ---- comparison vs the household's current policy (latest declarations page) ----
    decs = [d for d, v in docs.items() if v["doc_type"] == "declarations"]
    base = docs[decs[-1]] if decs else {}
    facts = next((v for v in docs.values() if v["doc_type"] == "rating-info"), {})  # insurer's own property facts
    comp = []
    for d, v in docs.items():
        if v["doc_type"] in SKIP:
            continue
        prem, a = v.get("premium_annual_total"), v.get("coverage_a_dwelling")
        notes = []
        if base and d not in decs and v["doc_type"] != "rating-info":
            if a and base.get("coverage_a_dwelling") and abs(a / base["coverage_a_dwelling"] - 1) > 0.05:
                notes.append("dwelling differs >5%")
            for k in ("year_built", "square_feet"):
                if v.get(k) and facts.get(k) and abs(v[k] / facts[k] - 1) > 0.02:
                    notes.append(f"{k} {v[k]:g} vs actual {facts[k]:g}")
            if v.get("deductible_wind_hail_pct") is None:
                notes.append("wind/hail deductible unknown")
            if v.get("deductible_all_peril") is None:
                notes.append("AOP deductible unknown")
        comp.append({
            "doc_id": d, "doc_type": v["doc_type"], "premium": prem,
            "coverage_a": a, "coverage_c": v.get("coverage_c_personal_property"),
            "coverage_e": v.get("coverage_e_liability"),
            "wind_hail_pct": v.get("deductible_wind_hail_pct"), "aop_deductible": v.get("deductible_all_peril"),
            "year_built": v.get("year_built"), "square_feet": v.get("square_feet"),
            "premium_per_1k_a": round(prem / a * 1000, 2) if prem and a else None,
            "vs_current_$": round(prem - base["premium_annual_total"], 2) if prem and base.get("premium_annual_total") and d not in decs else None,
            "vs_current_%": round(100 * (prem / base["premium_annual_total"] - 1), 1) if prem and base.get("premium_annual_total") and d not in decs else None,
            "apples_to_apples": "baseline" if d in decs else "reference" if v["doc_type"] == "rating-info" else ("yes" if not notes else "no"),
            "notes": "; ".join(notes),
        })
    comp.sort(key=lambda r: ({"declarations": 0, "rating-info": 1}.get(r["doc_type"], 2), r["premium"] or 1e9))
    write("comparison.csv", comp)
    n_docs = len(docs)
    print(f"{n_docs} docs, {len(field_rows)} field rows, {len(queue)} need review "
          f"(threshold {THRESHOLD}) -> private/out/")


if __name__ == "__main__":
    main()
