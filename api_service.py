"""FastAPI backend service for EchoBridge multi-store operations hub and Agent observability."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from mcp_servers.ticket_server import TicketStore, PROJECT_ROOT
from shop_registry import get_shop_by_id, list_shops, get_default_shop_id

app = FastAPI(
    title="EchoBridge Multi-Shop AI Agent & Operations Hub",
    description="Cross-border e-commerce customer support中台与 Agent 链路观测系统",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

class AgentChatRequest(BaseModel):
    message: str = Field(..., description="客户提问内容")
    shop_id: str = Field("tt-us-west", description="目标 TikTok 店铺 ID")
    sender_id: str = Field("customer_demo", description="客户 ID")
    session_id: Optional[str] = Field(None, description="会话 ID")


# ---------------------------------------------------------
# System & Storefront APIs
# ---------------------------------------------------------

@app.get("/api/health")
async def health_check():
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
async def get_shops():
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
async def get_shop_detail(shop_id: str):
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
async def list_tickets(
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
async def get_ticket(ticket_id: str):
    store = get_store()
    result = store.admin_get_ticket(ticket_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return result

@app.post("/api/tickets/{ticket_id}/update")
async def update_ticket(ticket_id: str, payload: UpdateTicketRequest):
    """管理员审批、流转状态并添加客户可见备注"""
    store = get_store()
    try:
        result = store.update_ticket(
            ticket_id=ticket_id,
            status=payload.status,
            comment=payload.comment or "",
            operator_id=payload.operator_id or "web-admin",
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.post("/api/tickets/create")
async def create_ticket(payload: CreateTicketRequest):
    """创建新工单"""
    store = get_store()
    req_id = f"web-req-{uuid4().hex[:12]}"
    try:
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
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------
# Knowledge Base APIs
# ---------------------------------------------------------

@app.get("/api/knowledge/{shop_id}")
async def get_shop_knowledge(shop_id: str):
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


# ---------------------------------------------------------
# Agent Live Chat & Execution Trace Simulation Engine
# ---------------------------------------------------------

def execute_agent_reasoning_and_tools(
    user_message: str, shop_id: str, sender_id: str, session_id: str
) -> Dict[str, Any]:
    """
    执行 Agent 的 ReAct 意图识别、双文件确定性政策读取与工具调用轨迹记录。
    在 Web 调试界面中清晰展现每一阶段的思考、工具调用（入参/返回值/耗时）及最终回复。
    """
    shop = get_shop_by_id(shop_id) or get_shop_by_id(get_default_shop_id())
    traces = []
    start_time = time.time()

    # Step 1: Tool Call -> read_file for Brand Common Policy
    t1_start = time.time()
    workspace = PROJECT_ROOT / "workspace"
    common_path = workspace / "knowledge/brand/common.md"
    common_content = common_path.read_text(encoding="utf-8") if common_path.exists() else ""
    traces.append({
        "step": 1,
        "tool_name": "read_file",
        "arguments": {"path": "knowledge/brand/common.md"},
        "result_preview": f"读取品牌通用知识库成功 (共 {len(common_content)} 字符，含商品质保与缺陷判定规则)",
        "duration_ms": round((time.time() - t1_start) * 1000, 2),
    })

    # Step 2: Tool Call -> read_file for Specific Shop Overlay
    t2_start = time.time()
    shop_path = workspace / f"knowledge/shops/{shop['shop_id']}.md"
    shop_content = shop_path.read_text(encoding="utf-8") if shop_path.exists() else ""
    traces.append({
        "step": 2,
        "tool_name": "read_file",
        "arguments": {"path": f"knowledge/shops/{shop['shop_id']}.md"},
        "result_preview": f"读取当前店铺专属覆盖政策 ({shop['name']}) 成功: 仓库位于 {shop.get('warehouse')}, 退货窗口 {shop.get('return_window_days')} 天",
        "duration_ms": round((time.time() - t2_start) * 1000, 2),
    })

    # Step 3: Intent Analysis & Decision
    msg_lower = user_message.lower()
    needs_ticket = any(k in user_message for k in ["人工", "建单", "投诉", "退货", "赔偿", "转人工", "ticket", "refund", "return"])
    is_damaged = any(k in user_message for k in ["破损", "坏了", "漏气", "卡死", "不工作", "broken", "damaged", "e08", "defect"])

    # Step 4: Conditional Tool Call -> Ticket creation if explicitly agreed
    created_ticket = None
    if needs_ticket and ("帮我建单" in user_message or "确认建单" in user_message or "申请人工" in user_message):
        t3_start = time.time()
        store = get_store()
        req_id = f"chat-auto-{uuid4().hex[:10]}"
        cat = "damaged" if is_damaged else "return"
        created_ticket = store.create_ticket(
            title=f"[{shop['name']}] 客户申请售后支持",
            description=user_message,
            user_id=sender_id,
            platform="web_chat",
            shop_id=shop["shop_id"],
            request_id=req_id,
            category=cat,
            priority="high" if is_damaged else "medium",
            product_name="Lumen 智能人体工学设备",
        )
        traces.append({
            "step": 3,
            "tool_name": "mcp_ticket_server_create_ticket",
            "arguments": {
                "title": f"[{shop['name']}] 客户申请售后支持",
                "category": cat,
                "shop_id": shop["shop_id"],
                "user_id": sender_id,
            },
            "result_preview": f"工单生成成功: 单号 {created_ticket['ticket']['ticket_id']} (状态: {created_ticket['ticket']['status']})",
            "duration_ms": round((time.time() - t3_start) * 1000, 2),
        })

    # Construct synthesized answer
    if created_ticket:
        tk_id = created_ticket["ticket"]["ticket_id"]
        answer = (
            f"已为您在 **{shop['name']}** 成功创建售后支持工单！\n\n"
            f"- **工单编号**：`{tk_id}`\n"
            f"- **所属店铺**：{shop['name']} ({shop.get('region')})\n"
            f"- **当前状态**：待人工客服审核\n\n"
            f"我们的属地售后专员将在 24 小时内完成审核并联系您出具预付退货面单（RMA）。"
            f"退货包裹将统一送达本店专属质检仓（{shop.get('warehouse')}）。您可随时在此查询进展！"
        )
    elif is_damaged:
        answer = (
            f"您好！对于您遇到的商品问题我们深感抱歉。依据 **{shop['name']}** 的售后与质保政策：\n\n"
            f"1. **质量问题处理保障**：硬件故障与运输破损属于品牌原厂质保范围，我们将为您免费补发配件或安排免费换新，来回运费全额由品牌承担；\n"
            f"2. **所需凭据**：请您协助提供 TikTok 订单号、包装箱面单及破损部位特写照片；\n"
            f"3. **退货质检仓**：本店指定退货仓位于 **{shop.get('warehouse')}**。\n\n"
            f"如需安排专员跟进或开具退换货面单，请回复 **“确认建单”** 或 **“转人工”**，我将立即为您提交售后工单！"
        )
    elif "退货" in user_message or "运费" in user_message:
        answer = (
            f"您好，很高兴为您解答 **{shop['name']}** 的退换与物流政策：\n\n"
            f"- **退货周期**：支持签收后 **{shop.get('return_window_days')} 天内** 无理由退换（需保持全新未组装状态）；\n"
            f"- **运费标准**：单笔订单满 **{shop.get('currency')} 基础门槛** 享免运保障；主观原因退货需由买家承担寄回运费；\n"
            f"- **属地履约仓**：本店商品由 **{shop.get('warehouse')}** 发货及验收。\n\n"
            f"如有订单退换需求，请告诉我您的订单编号，或说明是否需要为您创建人工客服支持工单。"
        )
    else:
        answer = (
            f"您好！我是 **Lumen Home** 旗下 **{shop['name']}** 的官方 AI 客服专员。\n\n"
            f"我们主营人体工学升降桌、Ergo Chair Pro 人体工学椅及智能桌面护眼灯，全系商品均享有原厂正品有限质保。\n"
            f"请问有什么可以帮您？（您可以咨询商品规格、物流配送时效、退换货政策，或申请售后转人工）"
        )

    return {
        "reply": answer,
        "shop_id": shop["shop_id"],
        "shop_name": shop["name"],
        "session_id": session_id,
        "traces": traces,
        "total_latency_ms": round((time.time() - start_time) * 1000, 2),
    }


@app.post("/api/agent/chat")
async def agent_chat(payload: AgentChatRequest):
    """同步调用 Agent 问答与工具链路追踪"""
    sess_id = payload.session_id or f"sess-{uuid4().hex[:8]}"
    result = execute_agent_reasoning_and_tools(
        user_message=payload.message,
        shop_id=payload.shop_id,
        sender_id=payload.sender_id,
        session_id=sess_id,
    )
    return {"success": True, **result}


@app.get("/api/agent/chat/stream")
async def agent_chat_stream(
    message: str = Query(..., description="用户输入内容"),
    shop_id: str = Query("tt-us-west", description="店铺 ID"),
    sender_id: str = Query("customer_demo", description="客户 ID"),
    session_id: Optional[str] = Query(None, description="会话 ID"),
):
    """
    SSE 流式推流接口 (Server-Sent Events)
    实时推流 Agent 思考步骤、Tool 调用轨迹及打字机回答，供可视化控制台展示。
    """
    sess_id = session_id or f"sess-{uuid4().hex[:8]}"
    
    async def event_generator() -> AsyncGenerator[str, None]:
        # 1. 发送接收事件
        yield f"event: session\ndata: {json.dumps({'session_id': sess_id, 'shop_id': shop_id})}\n\n"
        await asyncio.sleep(0.05)

        # 2. 推流思考过程
        yield f"event: thought\ndata: {json.dumps({'text': f'正在检索店铺 {shop_id} 的专属政策与品牌基础知识库...'})}\n\n"
        await asyncio.sleep(0.08)

        # 3. 执行推理与工具调用
        res = execute_agent_reasoning_and_tools(message, shop_id, sender_id, sess_id)

        # 4. 推流每个 Tool Call 详情
        for trace in res["traces"]:
            yield f"event: tool_call\ndata: {json.dumps(trace, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.05)

        # 5. 打字机逐段推流回答文本
        full_text = res["reply"]
        chunk_size = 12
        for i in range(0, len(full_text), chunk_size):
            chunk = full_text[i:i + chunk_size]
            yield f"event: token\ndata: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.03)

        # 6. 发送完成事件
        yield f"event: done\ndata: {json.dumps({'total_latency_ms': res['total_latency_ms']})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
