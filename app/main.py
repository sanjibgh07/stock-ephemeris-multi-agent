"""app/main.py - FastAPI backend (frontend ko data deta hai).

Endpoints (spec ke hisaab se):
  GET  /health          -> server zinda hai?
  POST /run-research    -> {mode: "stocks" | "arbitrage" | "both"} chalao
  GET  /status          -> state/project_state.json padho
  GET  /last-result     -> pichhla result

Extra (user ke liye aasaani):
  GET  /data-status     -> data kitna hai
  POST /rebuild-data    -> data book dobara banao
  GET  /                -> frontend page

IMPORTANT: har result me disclaimer jaata hai.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config, state_manager
from app.agents import base
from app.orchestrator import Orchestrator, get_orchestrator

config.bootstrap_environment()
try:
    from db import database

    database.init_db()
except Exception:  # noqa: BLE001
    pass

app = FastAPI(
    title="Indian Stock + Ephemeris Multi-Agent Research System",
    version="1.0.0",
    description=("Research-only system. Combines official NSE/BSE market data with "
                 "Swiss Ephemeris planetary data. NOT investment advice."),
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


class ResearchRequest(BaseModel):
    mode: str = Field(default="stocks", description="stocks | arbitrage | both")
    top_n: int = Field(default=5, ge=1, le=20)
    use_cache: bool = Field(default=True)
    include_futures: bool = Field(default=True)
    include_etf: bool = Field(default=True)
    universe: str = Field(default="NIFTY50", description="NIFTY50 | NIFTY100 | ALL")


class WalkForwardRequest(BaseModel):
    universe: str = Field(default="NIFTY50")
    horizon_days: int = Field(default=20, ge=5, le=60)
    folds: int = Field(default=6, ge=3, le=10)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "project": "Indian Stock + Ephemeris Multi-Agent Research System",
        "project_root": str(config.PROJECT_ROOT),
        "drive": config.drive_letter(config.PROJECT_ROOT),
        "no_c_drive_guard": config.NO_C_DRIVE,
        "disk": config.disk_report(),
        "data_status": Orchestrator.data_ready(),
        "disclaimer": base.DISCLAIMER,
    }


@app.get("/status")
def status() -> dict:
    """project_state.json + todo progress + dataset catalogue."""
    state = state_manager.load_state()
    datasets = []
    try:
        from db import database  # noqa: PLC0415

        conn = database.get_connection()
        try:
            rows = conn.execute(
                "SELECT dataset_name, row_count, first_date, last_date, symbols,"
                " sources, quality, updated_ist FROM datasets ORDER BY dataset_name"
            ).fetchall()
            datasets = [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    todo_done = todo_total = 0
    if Path(config.TODO_PATH).exists():
        text = Path(config.TODO_PATH).read_text(encoding="utf-8")
        todo_done = text.count("[x]")
        todo_total = todo_done + text.count("[ ]")
    return {
        "state": state,
        "datasets": datasets,
        "todo_progress": {"done": todo_done, "total": todo_total},
        "resume_message": state_manager.resume_message(),
        "data_status": Orchestrator.data_ready(),
        "disk": config.disk_report(),
    }


@app.get("/last-result")
def last_result() -> dict:
    result = Orchestrator.read_last_result()
    if not result:
        return {"ok": False, "error": "no result yet - click RUN in the frontend",
                "disclaimer": base.DISCLAIMER}
    return result


@app.get("/prediction-ledger")
def prediction_ledger(limit: int = 50) -> dict:
    """Past forecasts retained for later actual-result evaluation."""
    try:
        from db import database
        return {"ok": True,
                "items": database.recent_predictions(limit=max(1, min(limit, 200))),
                "summary": database.prediction_summary()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "items": [], "error": str(exc)}


@app.post("/walk-forward")
def walk_forward(req: WalkForwardRequest) -> dict:
    """Run point-in-time validation on demand; no future rows enter features."""
    try:
        from app.validation import run_walk_forward
        raw = base.load_stock_book()
        book, universe = base.filter_stock_universe(raw, req.universe)
        if not universe.get("available"):
            return {"ok": False, "validation": universe}
        return {"ok": True, "validation": run_walk_forward(
            book, universe=req.universe, horizon_days=req.horizon_days, folds=req.folds)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@app.get("/data-status")
def data_status() -> dict:
    ready = Orchestrator.data_ready()
    datasets = []
    try:
        from db import database  # noqa: PLC0415

        conn = database.get_connection()
        try:
            datasets = [dict(r) for r in conn.execute(
                "SELECT dataset_name, row_count, first_date, last_date, quality, sources"
                " FROM datasets ORDER BY dataset_name").fetchall()]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    return {"ready": ready, "datasets": datasets, "disk": config.disk_report()}


@app.post("/run-research")
def run_research(req: ResearchRequest) -> dict:
    """Research chalao. Ye research hai - investment advice NAHI."""
    orch = get_orchestrator(top_n=req.top_n)
    try:
        result = orch.run(mode=req.mode, use_cache=req.use_cache, universe=req.universe)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"research failed: {exc}") from exc
    if not result.get("ok"):
        return JSONResponse(status_code=200, content=result)
    if isinstance(result.get("arbitrage"), dict):
        if not req.include_futures:
            result["arbitrage"]["futures_vs_spot"] = {"available": False, "items": [],
                                                      "reason": "skipped by user"}
        if not req.include_etf:
            result["arbitrage"]["etf_vs_nav"] = {"available": False, "items": [],
                                                 "reason": "skipped by user"}
    return result


@app.post("/rebuild-data")
def rebuild_data(skip_official_indices: bool = False) -> dict:
    """Data book dobara banao (idempotent). Non-coder friendly button."""
    from data import pipeline  # noqa: PLC0415

    logs: list[str] = []
    summary = pipeline.build_all(skip_official_indices=skip_official_indices,
                                 progress=logs.append)
    return {"ok": True, "summary": config.json_safe(summary), "log_tail": logs[-25:]}


if Path(config.FRONTEND_DIR).exists():
    app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")


@app.get("/")
def index():
    page = Path(config.FRONTEND_DIR) / "index.html"
    if not page.exists():
        return JSONResponse(status_code=404, content={"error": "frontend/index.html missing"})
    return FileResponse(str(page))
