"""Point-in-time walk-forward validation helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.agents import base
from app.agents.technical_agent import TechnicalAgent

TECHNICAL_CONFIRMATION_MIN = 60.0
THRESHOLD_CANDIDATES = (50.0, 55.0, 60.0, 65.0, 70.0, 75.0)


def _regime(prefix: pd.DataFrame) -> str:
    """Classify state using only rows available at the historical cutoff."""
    if prefix.empty or "close" not in prefix:
        return "UNKNOWN"
    close = float(prefix.iloc[-1]["close"])
    sma200 = float(prefix["close"].rolling(200, min_periods=200).mean().iloc[-1])
    if not np.isfinite(close) or not np.isfinite(sma200) or sma200 <= 0:
        return "UNKNOWN"
    ratio = close / sma200
    return "BULL" if ratio >= 1.05 else "BEAR" if ratio <= 0.95 else "SIDEWAYS"


def _stats(rows: list[dict], threshold: float) -> dict:
    selected = [x for x in rows if float(x["tech_score"]) >= threshold]
    vals = np.asarray([x["forward_return_pct"] for x in selected], dtype=float)
    all_vals = np.asarray([x["forward_return_pct"] for x in rows], dtype=float)
    result = {
        "signals": len(selected), "observations": len(rows),
        "positive_rate_pct": None, "mean_return_pct": None,
        "median_return_pct": None, "worst_return_pct": None,
        "best_return_pct": None,
        "baseline_positive_rate_pct": round(float((all_vals > 0).mean() * 100.0), 2) if len(all_vals) else None,
        "baseline_mean_return_pct": round(float(all_vals.mean()), 4) if len(all_vals) else None,
    }
    if len(vals):
        result.update({
            "positive_rate_pct": round(float((vals > 0).mean() * 100.0), 2),
            "mean_return_pct": round(float(vals.mean()), 4),
            "median_return_pct": round(float(np.median(vals)), 4),
            "worst_return_pct": round(float(vals.min()), 4),
            "best_return_pct": round(float(vals.max()), 4),
        })
    return result


def _quality(stats: dict) -> str:
    return ("PASS" if stats.get("signals", 0) >= 20
            and (stats.get("positive_rate_pct") or 0) >= 50.0
            and stats.get("mean_return_pct") is not None
            and stats["mean_return_pct"] >= (stats.get("baseline_mean_return_pct") or 0)
            else "WEAK")


def _regime_breakdown(rows: list[dict], threshold: float) -> dict:
    return {regime: _stats([x for x in rows if x.get("regime") == regime], threshold)
            for regime in sorted({x.get("regime", "UNKNOWN") for x in rows})}


def _run_calibrated(book: pd.DataFrame, universe: str, horizon_days: int,
                    folds: int, threshold: float | None = None,
                    max_symbols: int = 20) -> dict:
    """Calibrate on early folds and score only the last two folds as holdout."""
    if book is None or len(book) == 0:
        return {"available": False, "reason": "stock book is empty"}
    horizon_days = max(1, int(horizon_days))
    folds = max(3, min(int(folds), 10))
    # Keep validation aligned with the requested dated official universe; this
    # also prevents an NIFTY50 report from silently scanning all 100 stocks.
    if str(universe).upper() not in {"ALL", "NIFTY100"}:
        book, _ = base.filter_stock_universe(book, universe)
    if book is None or len(book) == 0:
        return {"available": False, "reason": "requested stock universe is empty",
                "universe": universe, "horizon_days": horizon_days, "folds": folds}
    groups = base.group_book(book)
    # Bound the expensive point-in-time recalculation deterministically. This
    # gives six folds across 20 evenly-spaced symbols (120 observations) while
    # keeping the on-demand report responsive on the local machine.
    sampled_symbols = sorted(groups)
    max_symbols = max(1, int(max_symbols))
    if len(sampled_symbols) > max_symbols:
        indexes = np.linspace(0, len(sampled_symbols) - 1, max_symbols, dtype=int)
        sampled_symbols = [sampled_symbols[i] for i in indexes]
        groups = {symbol: groups[symbol] for symbol in sampled_symbols}
    sampled_symbol_count = len(groups)
    agent = TechnicalAgent()
    observations: list[dict] = []
    skipped = 0
    for symbol, sub in groups.items():
        if len(sub) < 260 + horizon_days * folds:
            skipped += 1
            continue
        cutoff_positions = [len(sub) - horizon_days * (folds - i) for i in range(folds)]
        for fold, pos in enumerate(cutoff_positions):
            if pos <= 0 or pos + horizon_days >= len(sub):
                continue
            prefix = sub.iloc[:pos + 1].copy()
            tech = agent.analyze_frame(prefix)
            if not tech or not isinstance(tech.get("tech_score"), (int, float)):
                continue
            start_close = float(sub.iloc[pos]["close"])
            end_close = float(sub.iloc[pos + horizon_days]["close"])
            if start_close <= 0:
                continue
            observations.append({
                "symbol": symbol, "cutoff": str(sub.iloc[pos]["date"]),
                "fold": fold, "regime": _regime(prefix),
                "tech_score": round(float(tech["tech_score"]), 4),
                "forward_return_pct": round((end_close / start_close - 1.0) * 100.0, 4),
            })
    if not observations:
        return {"available": False, "reason": "no historical observations in folds",
                "universe": universe, "horizon_days": horizon_days,
                "folds": folds, "observations": 0, "skipped_symbols": skipped}
    holdout_count = min(2, folds - 1)
    calibration_cutoff = folds - holdout_count
    calibration_rows = [x for x in observations if x["fold"] < calibration_cutoff]
    holdout_rows = [x for x in observations if x["fold"] >= calibration_cutoff]
    candidates = (float(threshold),) if threshold is not None else THRESHOLD_CANDIDATES
    candidate_stats = {str(t): _stats(calibration_rows, t) for t in candidates}
    viable = [(t, s) for t, s in candidate_stats.items()
              if s["signals"] >= 20 and s["mean_return_pct"] is not None]
    selected_threshold = float(threshold if threshold is not None else TECHNICAL_CONFIRMATION_MIN)
    if viable:
        selected_threshold = float(max(
            viable, key=lambda item: (item[1]["mean_return_pct"],
                                       item[1]["positive_rate_pct"] or 0,
                                       -float(item[0])))[0])
    calibration = _stats(calibration_rows, selected_threshold)
    holdout = _stats(holdout_rows, selected_threshold)
    all_stats = _stats(observations, selected_threshold)
    quality = _quality(holdout)
    signals = [x for x in observations if float(x["tech_score"]) >= selected_threshold]
    return {
        "available": True, "universe": universe, "horizon_days": horizon_days,
        "folds": folds, "observations": len(observations),
        "signals": all_stats["signals"], "skipped_symbols": skipped,
        "symbols_validated": sampled_symbol_count,
        "sampling_note": f"deterministic evenly-spaced {sampled_symbol_count}-symbol sample",
        "signal_positive_rate_pct": all_stats["positive_rate_pct"],
        "signal_mean_return_pct": all_stats["mean_return_pct"],
        "signal_median_return_pct": all_stats["median_return_pct"],
        "signal_worst_return_pct": all_stats["worst_return_pct"],
        "signal_best_return_pct": all_stats["best_return_pct"],
        "baseline_positive_rate_pct": all_stats["baseline_positive_rate_pct"],
        "baseline_mean_return_pct": all_stats["baseline_mean_return_pct"],
        "selected_threshold": selected_threshold,
        "calibration": {"folds": list(range(calibration_cutoff)), **calibration,
                        "candidate_thresholds": candidate_stats},
        "holdout": {"folds": list(range(calibration_cutoff, folds)), **holdout,
                      "quality": quality},
        "regime_breakdown": _regime_breakdown(holdout_rows, selected_threshold),
        "quality": quality,
        "method": "point-in-time technical threshold calibrated on early folds; last two folds held out",
        "note": "Historical validation only; not a forecast or investment advice. Confirmed picks require PASS on unseen holdout.",
        "sample": signals[:100],
    }


def run_walk_forward(book: pd.DataFrame, universe: str = "NIFTY50",
                     horizon_days: int = 20, folds: int = 6,
                     threshold: float | None = None,
                     max_symbols: int = 20) -> dict:
    """Validate the technical confirmation rule without look-ahead bias.

    At each historical cutoff, indicators see only rows up to that cutoff.  A
    signal is then scored against the next ``horizon_days`` observed closes.
    This is a validation report, never a guarantee of future performance.
    """
    return _run_calibrated(book, universe, horizon_days, folds, threshold, max_symbols)
    if book is None or len(book) == 0:
        return {"available": False, "reason": "stock book is empty"}
    horizon_days = max(1, int(horizon_days))
    folds = max(1, min(int(folds), 10))
    groups = base.group_book(book)
    agent = TechnicalAgent()
    observations: list[dict] = []
    skipped = 0
    for symbol, sub in groups.items():
        if len(sub) < 260 + horizon_days * folds:
            skipped += 1
            continue
        cutoff_positions = [len(sub) - horizon_days * (folds - i)
                            for i in range(folds)]
        for pos in cutoff_positions:
            if pos <= 0 or pos + horizon_days >= len(sub):
                continue
            prefix = sub.iloc[:pos + 1].copy()
            tech = agent.analyze_frame(prefix)
            if not tech or not isinstance(tech.get("tech_score"), (int, float)):
                continue
            start_close = float(sub.iloc[pos]["close"])
            end_close = float(sub.iloc[pos + horizon_days]["close"])
            if start_close <= 0:
                continue
            forward_return = (end_close / start_close - 1.0) * 100.0
            observations.append({
                "symbol": symbol, "cutoff": str(sub.iloc[pos]["date"]),
                "signal": bool(tech["tech_score"] >= TECHNICAL_CONFIRMATION_MIN),
                "tech_score": tech["tech_score"],
                "forward_return_pct": round(forward_return, 4),
            })
    signals = [x for x in observations if x["signal"]]
    vals = np.asarray([x["forward_return_pct"] for x in signals], dtype=float)
    baseline = np.asarray([x["forward_return_pct"] for x in observations], dtype=float)
    if not len(signals):
        return {"available": False, "reason": "no historical technical signals in folds",
                "universe": universe, "horizon_days": horizon_days,
                "folds": folds, "observations": len(observations), "skipped_symbols": skipped}
    signal_positive_rate = float((vals > 0).mean() * 100.0)
    signal_mean = float(vals.mean())
    baseline_mean = float(baseline.mean())
    quality = ("PASS" if len(signals) >= 20 and signal_positive_rate >= 50.0
               and signal_mean >= baseline_mean else "WEAK")
    return {
        "available": True, "universe": universe, "horizon_days": horizon_days,
        "folds": folds, "observations": len(observations),
        "signals": len(signals), "skipped_symbols": skipped,
        "signal_positive_rate_pct": round(signal_positive_rate, 2),
        "signal_mean_return_pct": round(signal_mean, 4),
        "signal_median_return_pct": round(float(np.median(vals)), 4),
        "signal_worst_return_pct": round(float(vals.min()), 4),
        "signal_best_return_pct": round(float(vals.max()), 4),
        "baseline_positive_rate_pct": round(float((baseline > 0).mean() * 100.0), 2),
        "baseline_mean_return_pct": round(baseline_mean, 4),
        "quality": quality,
        "method": "point-in-time technical score >= 60; future close evaluated after the cutoff",
        "note": "Historical validation only; not a forecast or investment advice.",
        "sample": signals[:100],
    }

