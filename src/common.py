"""Shared config: endpoints from .env, Entra ID credential (no keys), private paths."""
from pathlib import Path
import os

from azure.identity import AzureCliCredential, ChainedTokenCredential, ManagedIdentityCredential
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "private"
LETTERS = PRIVATE / "letters"
DI_OUT = PRIVATE / "di"

load_dotenv(ROOT / ".env")
DOCINTEL_ENDPOINT = os.environ.get("DOCINTEL_ENDPOINT", "")
BLOB_ENDPOINT = os.environ.get("BLOB_ENDPOINT", "")


def credential():
    """Managed identity when running in Azure (Functions, Databricks, VM), else the operator's `az login`.
    Roles for both are granted in infra/main.bicep; no keys anywhere."""
    if os.environ.get("IDENTITY_ENDPOINT") or os.environ.get("MSI_ENDPOINT"):
        return ChainedTokenCredential(ManagedIdentityCredential(), AzureCliCredential())
    return AzureCliCredential()


def connect_sql(retries: int = 6, wait: int = 20):
    """Azure SQL connection (Entra auth). The free-offer database auto-pauses after 60 min idle; the first
    connection wakes it and can time out, so retry for ~2 minutes."""
    import time

    import mssql_python
    conn_str = (f"Server=tcp:{os.environ['SQL_SERVER']},1433;Database={os.environ['SQL_DATABASE']};"
                "Encrypt=yes;TrustServerCertificate=no;Authentication=ActiveDirectoryDefault")
    for attempt in range(retries):
        try:
            return mssql_python.connect(conn_str)
        except mssql_python.exceptions.OperationalError as e:
            if attempt == retries - 1:
                raise
            print(f"SQL not reachable yet (auto-paused database resuming?): retry {attempt + 1}/{retries - 1}")
            time.sleep(wait)
