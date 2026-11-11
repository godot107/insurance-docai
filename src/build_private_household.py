"""Real households for the "Should I switch?" page, kept out of git.

Reads private/household.yaml (address, optional insurer names) and private/out/comparison.csv (from normalize.py)
and writes private/dashboard/{dim_household,fact_quote}.parquet in the same shape as the demo rows in
data/dashboard/. load_sql.py adds them to Azure SQL only with --include-private, so a plain reload removes them.

private/household.yaml:
    h001:
      label: My house
      address: 123 Example St, Austin, TX 78701     # what you type into the page's search box
      renewal_date: 2026-12-27
      insurers: {L01: Insurer name, L03: ...}      # optional; default "Mailer L01"

Usage: .venv/bin/python src/build_private_household.py
"""
from pathlib import Path

import pandas as pd
import yaml

from build_dashboard_tables import letter_url
from common import ROOT, load_env

PRIVATE = ROOT / "private"
OUT = PRIVATE / "dashboard"
KEEP = {"declarations": "declarations", "quote-mailer": "quote", "quote": "quote"}


def main():
    cfg_path = PRIVATE / "household.yaml"
    if not cfg_path.exists():
        raise SystemExit(f"missing {cfg_path} (see the docstring for its format)")
    cfg = yaml.safe_load(cfg_path.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    homes, quotes = [], []
    for hid, h in cfg.items():
        comp = pd.read_csv(PRIVATE / "out" / "comparison.csv")      # one household today (h001)
        comp = comp[comp.doc_type.isin(KEEP)].copy()
        names = h.get("insurers") or {}
        comp["household_id"] = hid
        comp["carrier"] = [("Current insurer" if t == "declarations" else names.get(d, f"Mailer {d}"))
                           for d, t in zip(comp.doc_id, comp.doc_type)]
        comp["doc_type"] = comp.doc_type.map(KEEP)
        comp = comp.rename(columns={"vs_current_$": "vs_current_usd", "vs_current_%": "vs_current_pct"})
        comp["notes"] = comp["notes"].fillna("")
        comp["renewal_date"] = pd.Timestamp(str(h.get("renewal_date", "2026-12-27")))
        comp["letter_url"] = comp.doc_id.map(letter_url)
        quotes.append(comp)
        homes.append({"household_id": hid, "household": f"{h.get('label', hid)} · {h.get('address', '')}".strip(" ·"),
                      "simulated": False})
    pd.DataFrame(homes).to_parquet(OUT / "dim_household.parquet", index=False)
    pd.concat(quotes).to_parquet(OUT / "fact_quote.parquet", index=False)
    print(f"wrote {len(homes)} household(s), {sum(len(q) for q in quotes)} quote rows → {OUT} (gitignored)")


if __name__ == "__main__":
    load_env()
    main()
