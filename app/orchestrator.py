"""app/orchestrator.py - ORCHESTRATOR AGENT (sab ka boss).

Frontend se ek tick aata hai (stocks / arbitrage / both) aur ye:
  1) sahi agents ko bulata hai,
  2) unke results jodta hai,
  3) state/last_result.json me save karta hai,
  4) frontend ko wapas bhejta hai.

Agar data book khaali hai to saaf message deta hai (khud kuch invent nahi karta).
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import config, state_manager
from app.agents import base
from app.agents.arbitrage_agent import ArbitrageAgent
from app.agents.ephemeris_agent import EphemerisAgent
from app.agents.historical_agent import HistoricalAgent
from app.agents.market_agent import MarketAgent
from app.agents.news_agent import NewsAgent
from app.validation import run_walk_forward
from app.agents.technical_agent import TechnicalAgent

SUPPORTED_MODES = ("stocks", "arbitrage", "both")


class Orchestrator:
    def __init__(self, top_n: int = 5, upcoming_days: int = 28):
        self.top_n = top_n
        self.upcoming_days = upcoming_days
        self.technical = TechnicalAgent()
        self.historical = HistoricalAgent()
        self.ephemeris = EphemerisAgent()
        self.market = MarketAgent(top_n=top_n)
        self.news = NewsAgent()
        self.arbitrage = ArbitrageAgent()

    @staticmethod
    def data_ready(stock: pd.DataFrame | None = None,
                   eph: pd.DataFrame | None = None) -> dict:
        """Data book taiyaar hai ya nahi - saaf report.

        Frames pass karo to dobara parquet read nahi hoga (fast).
        """
        stock = stock if stock is not None else base.load_stock_book()
        eph = eph if eph is not None else base.load_dataset("ephemeris_daily")
        return {
            "stock_rows": int(len(stock)) if stock is not None else 0,
            "stock_symbols": (int(stock["symbol"].nunique())
                              if stock is not None and len(stock) else 0),
            "ephemeris_rows": int(len(eph)) if eph is not None else 0,
            "ready": bool((stock is not None and len(stock))
                          and (eph is not None and len(eph))),
        }

    @staticmethod
    def read_last_result() -> dict:
        p = Path(config.LAST_RESULT_PATH)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _save_result(result: dict) -> Path:
        p = Path(config.LAST_RESULT_PATH)
        config.assert_not_c_drive(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(config.json_safe(result), indent=2, ensure_ascii=False),
                     encoding="utf-8")
        return p

    # ------------------------------------------------------------------
    def run(self, mode: str = "stocks", use_cache: bool = False,
            cache_max_age_minutes: int = 30, progress=None,
            universe: str = "NIFTY50") -> dict:
        """Main entry: mode = stocks | arbitrage | both."""
        started = time.time()
        mode = (mode or "stocks").strip().lower()
        if mode not in SUPPORTED_MODES:
            return {"ok": False, "error": f"mode must be one of {SUPPORTED_MODES}",
                    "disclaimer": base.DISCLAIMER}

        if use_cache:
            cached = self.read_last_result()
            if cached.get("mode") == mode and cached.get("run_ist"):
                try:
                    run_dt = dt.datetime.fromisoformat(str(cached["run_ist"]))
                    age = (config.now_ist().replace(tzinfo=None) - run_dt).total_seconds()
                    if age <= cache_max_age_minutes * 60:
                        cached["used_from_cache"] = True
                        return cached
                except Exception:  # noqa: BLE001
                    pass

        # ---- load the books ONCE and share them with every agent (fast) -----
        raw_book = base.load_stock_book()
        book, universe_status = base.filter_stock_universe(raw_book, universe)
        eph_df = base.load_dataset("ephemeris_daily")
        aspects_df = base.load_dataset("aspects_daily")

        ready = self.data_ready(stock=book, eph=eph_df)
        if not ready["ready"]:
            return {
                "ok": False, "mode": mode, "used_from_cache": False,
                "error": ("The data books are empty. Run `python run.py` (or "
                          "`python -m data.pipeline`) once to build them from the "
                          "official sources. Nothing is ever invented."),
                "data_status": ready,
                "disclaimer": base.DISCLAIMER, "disclaimer_hi": base.DISCLAIMER_HI,
            }

        run_id = uuid.uuid4().hex[:12]
        result: dict = {
            "ok": True, "run_id": run_id, "mode": mode,
            "run_ist": config.now_ist().isoformat(timespec="seconds"),
            "resume_message": state_manager.resume_message(),
            "data_status": ready, "used_from_cache": False,
            "universe": universe_status,
            "disclaimer": base.DISCLAIMER, "disclaimer_hi": base.DISCLAIMER_HI,
        }
        arb_executor = None
        arb_future = None
        if mode in ("arbitrage", "both"):
            # Arbitrage reads the same immutable book and is independent of
            # stock ranking, so overlap it with the research agents.
            bse_quotes = base.load_dataset("arbitrage_quotes")
            if bse_quotes is not None and len(bse_quotes) and len(book):
                allowed = set(book["symbol"].astype(str).str.upper())
                bse_quotes = bse_quotes[
                    bse_quotes["symbol"].astype(str).str.replace(".BO", "", regex=False)
                    .str.upper().isin(allowed)
                ]
            arb_executor = ThreadPoolExecutor(max_workers=1,
                                               thread_name_prefix="arbitrage")
            arb_future = arb_executor.submit(self.arbitrage.run, stock_book=book,
                                              bse_quotes=bse_quotes)

        if mode in ("stocks", "both"):
            if progress:
                progress("running technical, historical, ephemeris and NSE disclosure checks ...")
            symbols = list(base.group_book(book).keys())
            # These agents read the same immutable snapshot and do not depend on
            # one another.  Running them together removes the avoidable network
            # and CPU wait while preserving deterministic inputs.
            with ThreadPoolExecutor(max_workers=5, thread_name_prefix="research") as pool:
                futures = {
                    "technical": pool.submit(self.technical.run, book=book),
                    "historical": pool.submit(self.historical.run, book=book,
                                               eph_df=eph_df, aspects_df=aspects_df),
                    "ephemeris": pool.submit(self.ephemeris.run, book=book,
                                              upcoming_days=self.upcoming_days),
                    "news": pool.submit(self.news.run, symbols),
                }
                tech = futures["technical"].result()
                hist = futures["historical"].result()
                eph_result = futures["ephemeris"].result()
                news_result = futures["news"].result()
            # Validation is deliberately run after the other agents release
            # their temporary indicator frames; this keeps peak memory bounded
            # while retaining the same point-in-time gate.
            validation_result = run_walk_forward(book, universe, 20, 3, None, 5)
            if progress:
                progress("running market agent (ranking) ...")
            result["stocks"] = self.market.run(
                tech, hist, eph_result, news=news_result,
                validation=validation_result, top_n=self.top_n)

        if mode in ("arbitrage", "both"):
            if progress:
                progress("running arbitrage agent ...")
            result["arbitrage"] = arb_future.result() if arb_future is not None else {}
            if arb_executor is not None:
                arb_executor.shutdown(wait=True)

        result["duration_seconds"] = round(time.time() - started, 2)
        self._save_result(result)
        try:
            from db import database  # noqa: PLC0415

            stocks = result.get("stocks", {}) or {}
            arb = result.get("arbitrage", {}) or {}
            database.log_research_run(run_id, mode, "OK",
                                      len(stocks.get("picks", []) or []),
                                      int(arb.get("profitable_count", 0) or 0),
                                      result["duration_seconds"],
                                      Path(config.LAST_RESULT_PATH).name, False)
            if result.get("stocks"):
                database.log_predictions(run_id, result["stocks"], horizon_days=20)
        except Exception:  # noqa: BLE001
            pass
        state_manager.update_state(last_result_mode=mode)
        return result


_orchestrator: Orchestrator | None = None


def get_orchestrator(top_n: int = 5) -> Orchestrator:
    """Ek hi orchestrator instance reuse karo (fast)."""
    global _orchestrator  # noqa: PLW0603
    if _orchestrator is None:
        _orchestrator = Orchestrator(top_n=top_n)
    return _orchestrator
