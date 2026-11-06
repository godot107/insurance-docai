"""Load the dashboard tables (data/dashboard/*.parquet) into Azure SQL, schema `dash`, and grant read access.

Tables are dropped and recreated each run (idempotent full refresh: they're small). Column types come from
the pandas dtypes. Auth is Entra ID only (the operator's az login); the server has no SQL passwords.
--reader adds an Entra user (e.g. the Power BI work account) to db_datareader.

Usage: .venv/bin/python src/load_sql.py [--reader willie@<tenant>.onmicrosoft.com]
"""
import argparse
import os

import mssql_python
import pandas as pd

from common import ROOT, connect_sql

SRC = ROOT / "data" / "dashboard"


def sql_type(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "BIT"
    if pd.api.types.is_integer_dtype(s):
        return "BIGINT"
    if pd.api.types.is_float_dtype(s):
        return "FLOAT"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "DATETIME2"
    width = int(s.astype(str).str.len().max() or 1)
    return f"NVARCHAR({min(max(width * 2, 32), 4000)})"


def clean(v):
    if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.tz_convert(None).to_pydatetime() if v.tzinfo else v.to_pydatetime()
    return v.item() if hasattr(v, "item") else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reader", action="append", default=[], help="Entra UPN to grant db_datareader")
    args = ap.parse_args()
    conn = connect_sql()
    cur = conn.cursor()
    cur.execute("IF SCHEMA_ID('dash') IS NULL EXEC('CREATE SCHEMA dash')")
    for path in sorted(SRC.glob("*.parquet")):
        df = pd.read_parquet(path)
        for c in df.select_dtypes(include=["object", "string"]):
            df[c] = df[c].where(df[c].isna(), df[c].astype(str))   # mixed truth/pred values → text
        name = f"dash.{path.stem}"
        cols = ", ".join(f"[{c}] {sql_type(df[c])}" for c in df.columns)
        cur.execute(f"DROP TABLE IF EXISTS {name}")
        cur.execute(f"CREATE TABLE {name} ({cols})")
        if len(df):
            marks = ", ".join("?" * len(df.columns))
            cur.executemany(f"INSERT INTO {name} VALUES ({marks})",
                            [tuple(clean(v) for v in row) for row in df.itertuples(index=False)])
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        print(f"{name:22} {cur.fetchone()[0]:5} rows")
    for upn in args.reader:
        cur.execute(f"IF USER_ID(N'{upn}') IS NULL CREATE USER [{upn}] FROM EXTERNAL PROVIDER")
        cur.execute(f"ALTER ROLE db_datareader ADD MEMBER [{upn}]")
        print(f"granted db_datareader to {upn}")
    conn.commit()


if __name__ == "__main__":
    from common import load_env
    load_env()   # DOCAI_ENV=dev|qa|prod (default dev)
    main()
