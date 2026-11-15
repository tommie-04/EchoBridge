"""FastAPI endpoints for the local TikTok fulfillment simulation workbench."""
from datetime import datetime, timezone

from fastapi import FastAPI

from fulfillment_api import router as fulfillment_router
from fulfillment_simulator import list_mock_shops


app = FastAPI(
    title="EchoBridge Fulfillment Workbench",
    description="20 家美国 TikTok 店铺的洛杉矶共享仓面单模拟",
    version="3.0.0",
)
app.include_router(fulfillment_router)


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "EchoBridge Fulfillment",
        "mode": "simulation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "registered_shops_count": len(list_mock_shops()),
    }
