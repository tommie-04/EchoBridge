"""Replay six synthetic TikTok handoffs through the project's real stdio MCP server."""
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_servers.ticket_server import PROJECT_ROOT

# Fixed incoming event and customer labels allow a replay after an interrupted run.
HANDOFFS = (
    ("tt-us-west", "Mia", "damaged", "升降桌电机停转", "客户已按 FAQ 断电复位，电机仍停转，申请人工安排换件。"),
    ("tt-us-west", "Noah", "return", "人体工学椅退货", "椅背尺寸不适合，客户确认申请退货，需要客服提供寄回标签。"),
    ("tt-us-east", "Eva", "missing", "桌架缺少安装螺丝", "客户核对装箱清单后确认少了一包螺丝，希望人工安排补寄。"),
    ("tt-us-east", "Leo", "delivery", "签收后未收到包裹", "物流显示已签收，客户已检查前台，希望人工向承运商核查。"),
    ("tt-uk", "Ava", "refund", "退件签收后退款未到账", "客户已寄回脚踏板并确认仓库签收，申请人工核查退款进度。"),
    ("tt-uk", "Oscar", "exchange", "换货取件日期冲突", "客户接受换货方案，但原取件日无人收件，请专员协调新日期。"),
)


async def replay(database: Path) -> None:
    server = StdioServerParameters(command=sys.executable, args=[
        str(PROJECT_ROOT / "mcp_servers/ticket_server.py"), "--db", str(database), "serve"])
    async with stdio_client(server) as streams, ClientSession(*streams) as client:
        await client.initialize()
        for shop, buyer, category, title, summary in HANDOFFS:
            result = await client.call_tool("create_ticket", {
                "shop_id": shop, "user_id": f"demo-{buyer.lower()}", "user_name": buyer,
                "request_id": f"handoff-v1-{buyer.lower()}", "platform": "tiktok_demo",
                "category": category, "title": title, "description": summary})
            if result.isError or not json.loads(result.content[0].text).get("success"):
                raise RuntimeError(f"Could not import demo handoff for {shop}/{buyer}")
