"""
dashboard/app.py — LIVE DASHBOARD web application.

This is a presentation layer only. It reads from the existing trading
system (Alpaca via execution/alpaca_trader.py's client, the existing
SQLite trade log, and the existing risk modules) and never places,
modifies, or cancels an order. See dashboard/data.py for the read-only
data-access functions this app calls.

Run with:  python dashboard.py
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from dashboard import data

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Trading Agent — Live Dashboard")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.middleware("http")
async def no_store_and_no_leak(request: Request, call_next):
    """
    Belt-and-braces response hygiene:
      - never let API responses get cached (they contain account data)
      - never let an unhandled exception leak internals (env values,
        stack traces, credentials) back to the browser
    """
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error serving %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal error. See server logs for details."},
        )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ── Pages ───────────────────────────────────────────────────────────────

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ── REST endpoints (one per dashboard section) ───────────────────────────

@app.get("/api/health")
async def api_health():
    return data.get_health()


@app.get("/api/portfolio")
async def api_portfolio():
    return data.get_portfolio()


@app.get("/api/pnl")
async def api_pnl():
    return data.get_pnl()


@app.get("/api/positions")
async def api_positions():
    return data.get_positions()


@app.get("/api/trades")
async def api_trades():
    return data.get_trades()


# Alias — the spec lists both /api/trades and /api/orders; they're the same
# underlying order feed, so this avoids a second, divergent implementation.
@app.get("/api/orders")
async def api_orders():
    return data.get_trades()


@app.get("/api/decisions")
async def api_decisions():
    return data.get_decisions()


@app.get("/api/risk")
async def api_risk():
    return data.get_risk_status()


@app.get("/api/agent/status")
async def api_agent_status():
    return data.get_agent_status()


# ── Live updates (Server-Sent Events) ────────────────────────────────────
#
# Chosen over WebSockets/polling-from-scratch because the dashboard is
# read-only and one-directional (server -> browser). SSE needs no extra
# dependency beyond what FastAPI/Starlette already provide, works over
# plain HTTP, and reconnects automatically in the browser — the simplest
# option that still feels "live".

async def _event_stream():
    while True:
        payload = {
            "portfolio": data.get_portfolio(),
            "pnl": data.get_pnl(),
            "positions": data.get_positions(),
            "trades": data.get_trades(),
            "decisions": data.get_decisions(),
            "risk": data.get_risk_status(),
            "agent": data.get_agent_status(),
        }
        yield f"data: {json.dumps(payload, default=str)}\n\n"
        await asyncio.sleep(4)


@app.get("/api/stream")
async def api_stream():
    return StreamingResponse(_event_stream(), media_type="text/event-stream")
