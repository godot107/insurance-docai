"""Calendar table for the date slider on the review pages, plus the day-level key on the two dated facts.

dim_date: one row per day from the first to the last event (date, year, month, month name, week start).
fact_review.date = the day the value entered the review queue (created_at), so open items stay in range;
fact_app_event.date = the event's day. Both are midnight timestamps so the relationships match.
Run after build_review_feedback.py / pull_app_telemetry.py.

Usage: .venv/bin/python src/build_dim_date.py
"""
import pandas as pd

from common import ROOT

OUT = ROOT / "data" / "dashboard"


def main():
    rev = pd.read_parquet(OUT / "fact_review.parquet")
    ev = pd.read_parquet(OUT / "fact_app_event.parquet")
    rev["date"] = pd.to_datetime(rev.created_at).dt.normalize().astype("datetime64[us]")
    ev["date"] = pd.to_datetime(ev.event_date).dt.normalize().astype("datetime64[us]")
    days = pd.date_range(min(rev.date.min(), ev.date.min()), max(rev.date.max(), ev.date.max()), freq="D")
    dim = pd.DataFrame({"date": days.astype("datetime64[us]")})
    dim["year"] = dim.date.dt.year.astype(str)
    dim["month"] = dim.date.dt.strftime("%Y-%m")
    dim["month_name"] = dim.date.dt.strftime("%b %Y")
    dim["week"] = (dim.date - pd.to_timedelta(dim.date.dt.day_of_week, unit="D")).astype("datetime64[us]")
    rev.to_parquet(OUT / "fact_review.parquet", index=False)
    ev.to_parquet(OUT / "fact_app_event.parquet", index=False)
    dim.to_parquet(OUT / "dim_date.parquet", index=False)
    print(f"dim_date: {len(dim)} days {dim.date.min():%Y-%m-%d} → {dim.date.max():%Y-%m-%d}")


if __name__ == "__main__":
    main()
