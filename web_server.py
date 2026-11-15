"""Web server launcher for EchoBridge operations dashboard and API."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

# Auto-fallback to nanobot python environment if running under default system python without FastAPI
try:
    import fastapi
    import uvicorn
except ImportError:
    candidate = Path.home() / ".local/share/uv/tools/nanobot-ai/bin/python"
    if candidate.exists() and sys.executable != str(candidate):
        os.execv(str(candidate), [str(candidate), *sys.argv])
    raise

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api_service import app

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/dashboard", methods=["GET", "HEAD"])
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
    parser.add_argument("--host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--port", type=int, default=8000, help="Listen port")
    parser.add_argument("--reload", action="store_true", help="Enable auto reload")
    parser.add_argument("--demo", action="store_true", help="Replay example handoffs into a dedicated database")
    args = parser.parse_args()
    if args.demo:
        import asyncio
        from demo_handoffs import replay
        database = PROJECT_ROOT / "data" / "demo-handoffs.db"
        os.environ["TICKET_DB_PATH"] = str(database)
        asyncio.run(replay(database))

    print(f"==================================================")
    print(f"🚀 EchoBridge AI Operations Hub running at http://{args.host}:{args.port}")
    print(f"📖 Swagger API Documentation at http://{args.host}:{args.port}/docs")
    print(f"==================================================")
    uvicorn.run("web_server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
