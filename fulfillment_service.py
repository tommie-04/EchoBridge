"""Shared batch persistence for the Web API and administrator MCP tools."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from fulfillment_simulator import build_batch


class BatchNotFoundError(LookupError):
    """The requested simulation batch has not been prepared."""


def batch_connection() -> sqlite3.Connection:
    database = Path(os.environ.get("FULFILLMENT_SIM_DB", str(
        Path(__file__).resolve().parent / "data" / "fulfillment-simulation.db")))
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=10)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)"
    )
    return connection


def read_batch(batch_id: str) -> dict:
    """Load the immutable snapshot used by every download and administrator query."""
    with closing(batch_connection()) as connection:
        row = connection.execute(
            "SELECT snapshot FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
    if row is None:
        raise BatchNotFoundError("找不到该面单批次，请先生成批次。")
    return json.loads(row[0])


def prepare_batch(shop_ids: list[str], ship_date: str) -> dict:
    """Prepare a validated batch and atomically reuse the persisted snapshot on retries."""
    batch = build_batch(shop_ids, ship_date)
    with closing(batch_connection()) as connection, connection:
        connection.execute(
            "INSERT OR IGNORE INTO batches (batch_id, snapshot) VALUES (?, ?)",
            (batch["batch_id"], json.dumps(batch, ensure_ascii=False)),
        )
    return read_batch(batch["batch_id"])
