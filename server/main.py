"""
PollyPilot FastAPI Backend
REST API for controlling the trading engine and accessing data.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server.database import Database
from server.engine import TradingEngine


# ─── Pydantic Models ──────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    """Request model for updating settings."""
    mode: Optional[str] = Field(None, pattern="^(grind|balanced|moonshot)$")
    max_days: Optional[int] = Field(None, ge=1, le=365)
    allow_shorting: Optional[bool] = None
    risk_multiplier: Optional[float] = Field(None, ge=0.1, le=3.0)


class StatusResponse(BaseModel):
    """Response model for /status endpoint."""
    running: bool
    uptime: str
    mode: str
    stats: dict


class SettingsResponse(BaseModel):
    """Response model for /settings endpoint."""
    mode: str
    max_days: int
    allow_shorting: bool
    risk_multiplier: float


# ─── Module-Level Instances ───────────────────────────────────────────────

db: Database = None
engine: TradingEngine = None


# ─── Lifespan Handler ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup resources."""
    global db, engine

    # Startup
    db = Database()
    engine = TradingEngine(db=db)
    db.add_log("INFO", "FastAPI server started")

    yield

    # Shutdown
    if engine and engine.running:
        engine.stop()
    if db:
        db.add_log("INFO", "FastAPI server stopped")
        db.close()


# ─── FastAPI App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="PollyPilot API",
    description="Polymarket AI Paper Trading Bot",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS for Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Endpoints ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "PollyPilot API"}


@app.get("/status", response_model=StatusResponse)
async def get_status():
    """Get current bot status including running state, uptime, mode, and stats."""
    status = engine.get_status()
    return StatusResponse(
        running=status["running"],
        uptime=status["uptime"],
        mode=status["mode"] or "balanced",
        stats=status["stats"],
    )


@app.post("/start")
async def start_engine():
    """Start the trading engine."""
    if engine.running:
        return {"message": "Engine already running", "running": True}

    engine.start()
    return {"message": "Engine started", "running": True}


@app.post("/stop")
async def stop_engine():
    """Stop the trading engine."""
    if not engine.running:
        return {"message": "Engine not running", "running": False}

    engine.stop()
    return {"message": "Engine stopped", "running": False}


@app.get("/history")
async def get_history(limit: int = 50):
    """Get trade history."""
    trades = db.get_trade_history(limit=limit)
    return {"trades": trades, "count": len(trades)}


@app.get("/portfolio")
async def get_portfolio():
    """Get current portfolio state."""
    portfolio = db.get_portfolio()
    stats = db.get_stats()
    equity_curve = db.get_equity_curve()

    return {
        "portfolio": portfolio,
        "stats": stats,
        "equity_curve": equity_curve,
    }


@app.get("/logs")
async def get_logs(limit: int = 50):
    """Get system logs."""
    logs = db.get_logs(limit=limit)
    return {"logs": logs, "count": len(logs)}


@app.get("/settings", response_model=SettingsResponse)
async def get_settings():
    """Get all current settings."""
    settings = db.get_all_settings()
    return SettingsResponse(
        mode=settings.get("mode", "balanced"),
        max_days=int(settings.get("max_days", "30")),
        allow_shorting=settings.get("allow_shorting", "false") == "true",
        risk_multiplier=float(settings.get("risk_multiplier", "1.0")),
    )


@app.post("/settings")
async def update_settings(updates: SettingsUpdate):
    """Update bot settings."""
    updated = {}

    if updates.mode is not None:
        db.set_setting("mode", updates.mode)
        updated["mode"] = updates.mode

    if updates.max_days is not None:
        db.set_setting("max_days", str(updates.max_days))
        updated["max_days"] = updates.max_days

    if updates.allow_shorting is not None:
        db.set_setting("allow_shorting", "true" if updates.allow_shorting else "false")
        updated["allow_shorting"] = updates.allow_shorting

    if updates.risk_multiplier is not None:
        db.set_setting("risk_multiplier", str(updates.risk_multiplier))
        updated["risk_multiplier"] = updates.risk_multiplier

    if updated:
        db.add_log("INFO", f"Settings updated: {updated}")

    return {"message": "Settings updated", "updated": updated}


@app.get("/open-trades")
async def get_open_trades():
    """Get all currently open trades."""
    trades = db.get_open_trades()
    return {"trades": trades, "count": len(trades)}


@app.get("/markets")
async def get_markets(limit: int = 20):
    """Get active Polymarket markets (useful for debugging)."""
    try:
        markets = engine.polymarket.get_active_markets(limit=limit)
        return {
            "markets": [
                {
                    "id": m.get("id"),
                    "question": m.get("question"),
                    "yes_price": m.get("_parsed_prices", [0.5])[0] if m.get("_parsed_prices") else 0.5,
                    "volume": m.get("volume"),
                    "endDate": m.get("endDateIso") or m.get("endDate"),
                }
                for m in markets
            ],
            "count": len(markets),
        }
    except Exception as e:
        return {"markets": [], "count": 0, "error": str(e)}


# ─── Error Handlers ──────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Catch-all error handler to prevent crashes."""
    error_msg = str(exc)
    if db:
        db.add_log("ERROR", f"API error: {error_msg}")
    return JSONResponse(
        status_code=500,
        content={"detail": error_msg, "type": type(exc).__name__}
    )
