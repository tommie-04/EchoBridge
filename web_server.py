"""Web server launcher for EchoBridge operations dashboard and API."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from api_service import app

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
@app.get("/dashboard")
async def serve_dashboard():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return {
            "message": "EchoBridge Hub Backend is running. Static dashboard is preparing.",
            "api_docs": "/docs",
            "health": "/api/health",
        }
    return FileResponse(index_file)


def main() -> None:
    parser = argparse.ArgumentParser(description="EchoBridge Operations Web Server")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host")
    parser.add_argument("--port", type=int, default=8000, help="Listen port")
    parser.add_argument("--reload", action="store_true", help="Enable auto reload")
    args = parser.parse_args()

    print(f"==================================================")
    print(f"🚀 EchoBridge AI Operations Hub running at http://{args.host}:{args.port}")
    print(f"📖 Swagger API Documentation at http://{args.host}:{args.port}/docs")
    print(f"==================================================")
    uvicorn.run("web_server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
