"""Request dependencies for the HTTP delivery layer."""

from __future__ import annotations

import os

from quant.storage import PostgresStore


def get_store() -> PostgresStore:
    """Create the persisted store from the server-only database setting."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("research database is not configured")
    return PostgresStore(dsn)
