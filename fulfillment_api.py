"""Local simulation endpoints for preparing reproducible warehouse PDF batches."""
from contextlib import closing
from datetime import date
import json
import os
from pathlib import Path
import sqlite3

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from fulfillment_simulator import build_batch, list_mock_shops, render_batch_pdf


router = APIRouter(prefix="/api/simulation", tags=["Fulfillment simulation"])


class BatchRequest(BaseModel):
    shop_ids: list[str] = Field(min_length=1, max_length=20)
    ship_date: date


def batch_connection():
    database = Path(os.environ.get("FULFILLMENT_SIM_DB", str(
        Path(__file__).resolve().parent / "data" / "fulfillment-simulation.db")))
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database, timeout=10)
    conn.execute("CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)")
    return conn


def read_batch(batch_id: str) -> dict:
    with closing(batch_connection()) as conn:
        row = conn.execute("SELECT snapshot FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "找不到该打印文件批次，请重新生成。")
    return json.loads(row[0])


@router.get("/shops")
def shops():
    return {"mode": "simulation", "shops": list_mock_shops()}


@router.post("/batches")
def prepare_batch(payload: BatchRequest):
    try:
        batch = build_batch(payload.shop_ids, payload.ship_date.isoformat())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with closing(batch_connection()) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO batches VALUES (?, ?)", (
            batch["batch_id"], json.dumps(batch, ensure_ascii=False)))
    # The persisted snapshot is the source for every download, including after a restart.
    return read_batch(batch["batch_id"])


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str):
    return read_batch(batch_id)


@router.get("/batches/{batch_id}/pdf")
def download_pdf(batch_id: str, group_id: str | None = None):
    batch = read_batch(batch_id)
    try:
        pdf = render_batch_pdf(batch, group_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="demo-labels-{batch["batch_id"]}.pdf"',
        "Cache-Control": "no-store",
    })
