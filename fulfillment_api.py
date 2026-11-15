"""Local simulation endpoints for preparing warehouse PDF batches."""
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from fulfillment_service import BatchNotFoundError, prepare_batch, read_batch
from fulfillment_simulator import list_mock_shops, render_batch_pdf


router = APIRouter(prefix="/api/simulation", tags=["Fulfillment simulation"])


class BatchRequest(BaseModel):
    shop_ids: list[str] = Field(min_length=1, max_length=20)
    ship_date: date


@router.get("/shops")
def shops():
    return {"mode": "simulation", "shops": list_mock_shops()}


@router.post("/batches")
def create_batch(payload: BatchRequest):
    try:
        return prepare_batch(payload.shop_ids, payload.ship_date.isoformat())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str):
    try:
        return read_batch(batch_id)
    except BatchNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/batches/{batch_id}/pdf")
def download_pdf(batch_id: str, group_id: str | None = None):
    batch = get_batch(batch_id)
    try:
        pdf = render_batch_pdf(batch, group_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="demo-labels-{batch["batch_id"]}.pdf"',
        "Cache-Control": "no-store",
    })
