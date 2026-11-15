"""FastAPI backend service for EchoBridge multi-store operations hub and human handoffs."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from mcp_servers.ticket_server import TicketStore, TicketError, PROJECT_ROOT
from shop_registry import get_shop_by_id, list_shops
from fulfillment_api import router as fulfillment_router

app = FastAPI(
    title="EchoBridge Multi-Shop AI Agent & Operations Hub",
    description="跨店铺人工售后工单",
    version="2.0.0",
)
app.include_router(fulfillment_router)

@app.exception_handler(TicketError)
async def ticket_error_response(request: Request, exc: TicketError):
    status = {"NOT_FOUND": 404, "FORBIDDEN": 403, "IDEMPOTENCY_CONFLICT": 409}.get(exc.code, 400)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


def get_store() -> TicketStore:
    db_path = os.environ.get("TICKET_DB_PATH", str(PROJECT_ROOT / "data/tickets.db"))
    return TicketStore(db_path)


# ---------------------------------------------------------
# Pydantic Request & Response Models
# ---------------------------------------------------------

class UpdateTicketRequest(BaseModel):
    status: Optional[str] = Field(None, description="open, in_progress, resolved, closed")
    comment: Optional[str] = Field("", description="管理员批复说明")
    operator_id: str = Field("web-admin", description="处理专员 ID")

class CreateTicketRequest(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=10000)
    shop_id: str = Field("tt-us-west")
    user_id: str = Field("web-customer")
    platform: str = Field("web")
    category: str = Field("other")
    priority: str = Field("medium")
    order_id: str = Field("")
    product_name: str = Field("")

# ---------------------------------------------------------
# System & Storefront APIs
# ---------------------------------------------------------

@app.get("/api/health")
def health_check():
    store = get_store()
    shops = list_shops()
    tickets_summary = store.list_tickets(limit=1)
    return {
        "status": "healthy",
        "service": "EchoBridge Hub",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "registered_shops_count": len(shops),
        "total_tickets": tickets_summary.get("total", 0),
    }

@app.get("/api/shops")
def get_shops():
    """获取所有已注册的 TikTok 店铺信息及其统计"""
    shops = list_shops()
    store = get_store()
    results = []
    for shop in shops:
        sid = shop["shop_id"]
        total = store.list_tickets(shop_id=sid, limit=1).get("total", 0)
        open_count = store.list_tickets(shop_id=sid, status="open", limit=1).get("total", 0)
        results.append({
            **shop,
            "stats": {
                "total_tickets": total,
                "open_tickets": open_count,
            }
        })
    return {"success": True, "shops": results}

@app.get("/api/shops/{shop_id}")
def get_shop_detail(shop_id: str):
    shop = get_shop_by_id(shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Storefront not found")
    store = get_store()
    open_tickets = store.list_tickets(shop_id=shop_id, status="open", limit=5)
    return {
        "success": True,
        "shop": shop,
        "recent_open_tickets": open_tickets.get("tickets", []),
    }


# ---------------------------------------------------------
# Ticket Management APIs
# ---------------------------------------------------------

@app.get("/api/tickets")
def list_tickets(
    shop_id: Optional[str] = Query(None, description="按店铺筛选"),
    status: Optional[str] = Query(None, description="按工单状态筛选"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """跨店铺查询工单列表，支持多租户切片筛选"""
    store = get_store()
    result = store.list_tickets(status=status, shop_id=shop_id, limit=limit, offset=offset)
    return result

@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    store = get_store()
    result = store.admin_get_ticket(ticket_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return result

@app.post("/api/tickets/{ticket_id}/update")
def update_ticket(ticket_id: str, payload: UpdateTicketRequest):
    """管理员审批、流转状态并添加客户可见备注"""
    store = get_store()
    result = store.update_ticket(
        ticket_id=ticket_id,
        status=payload.status,
        comment=payload.comment or "",
        operator_id=payload.operator_id or "web-admin",
    )
    return result

@app.post("/api/tickets/create")
def create_ticket(payload: CreateTicketRequest):
    """创建新工单"""
    store = get_store()
    req_id = f"web-req-{uuid4().hex[:12]}"
    result = store.create_ticket(
        title=payload.title,
        description=payload.description,
        user_id=payload.user_id,
        platform=payload.platform,
        shop_id=payload.shop_id,
        request_id=req_id,
        category=payload.category,
        priority=payload.priority,
        order_id=payload.order_id,
        product_name=payload.product_name,
    )
    return result


# ---------------------------------------------------------
# Knowledge Base APIs
# ---------------------------------------------------------

@app.get("/api/knowledge/{shop_id}")
def get_shop_knowledge(shop_id: str):
    """获取指定店铺对应的双文件确定性政策文本"""
    shop = get_shop_by_id(shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Storefront not found")
    
    workspace = PROJECT_ROOT / "workspace"
    common_file = workspace / "knowledge/brand/common.md"
    shop_file = workspace / f"knowledge/shops/{shop_id}.md"

    common_text = common_file.read_text(encoding="utf-8") if common_file.exists() else ""
    shop_text = shop_file.read_text(encoding="utf-8") if shop_file.exists() else ""

    return {
        "success": True,
        "shop_id": shop_id,
        "shop_name": shop.get("name", ""),
        "common_policy": common_text,
        "shop_policy": shop_text,
    }
