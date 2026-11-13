"""app/agents/historical_agent.py - AGENT 2: itihaas (history) analysis.

Ye agent do kaam karta hai:
  1) Purana performance: 1Y / 3Y / 5Y return, max drawdown, volatility.
  2) Ephemeris event study: pichhle dino me jab Mercury retrograde ya
     Saturn aspect hua, tab is stock ne aage 5/10 din me kya kiya?
     (Only REAL data from Phase 1 + Phase 2 - koi guess nahi.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app import config
from app.agents import base

MAJOR_ASPECTS = ("Conjunction", "Square", "Opposition", "Trine")


class HistoricalAgent:
    name = "historical_agent"

    def __init__(self, forward_horizons: tuple[int, ...] = (5, 10)):
        self.horizons = forward_horizons

    # ------------------------------------------------------------------
    @staticmethod
    def _returns(sym_df: pd.DataFrame) -> dict:
        close = sym_df["close"].astype(float)
        last = float(close.iloc[-1])

        def ret_over(days: int):
            n = int(252 * days)
            if len(close) > n:
                return base.pct(last, float(close.iloc[-(n + 1)]))
            return None

        return {
            "return_1y_pct": base.rnd(ret_over(1), 2),
            "return_3y_pct": base.rnd(ret_over(3), 2),
            "return_5y_pct": base.rnd(ret_over(5), 2),
            "max_drawdown_full_pct": base.rnd(base.max_drawdown(close), 2),
            "max_drawdown_5y_pct": base.rnd(base.max_drawdown(close.tail(252 * 5)), 2),
            "volatility_20d_annual_pct": base.rnd(
                base.annualised_vol(close.pct_change(), 20), 2),
            "volatility_1y_annual_pct": base.rnd(
                base.annualised_vol(close.pct_change(), 252), 2),
            "history_days": int(len(close)),
            "history_start": str(sym_df["date"].iloc[0]),
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _forward_returns(sym_df: pd.DataFrame, horizon: int) -> dict[str, float]:
        """date -> aage `horizon` trading din ka return %."""
        close = sym_df["close"].astype(float).to_numpy()
        dates = sym_df["date"].astype(str).to_numpy()
        out: dict[str, float] = {}
        for i in range(len(close) - horizon):
            base_price = close[i]
            if base_price and base_price == base_price:
                out[dates[i]] = (close[i + horizon] / base_price - 1.0) * 100.0
        return out

    def _condition_stats(self, fwd: dict[str, float], day_set: set[str],
                         label: str) -> dict:
        values = [fwd[d] for d in day_set if d in fwd]
        if not values:
            return {"condition": label, "sample_days": 0, "note": "no overlapping data"}
        arr = np.asarray(values, dtype=float)
        return {
            "condition": label,
            "sample_days": int(arr.size),
            "mean_return_pct": base.rnd(float(arr.mean()), 3),
            "median_return_pct": base.rnd(float(np.median(arr)), 3),
            "hit_rate_pct": base.rnd(float((arr > 0).mean() * 100.0), 1),
            "worst_pct": base.rnd(float(arr.min()), 2),
            "best_pct": base.rnd(float(arr.max()), 2),
            "std_pct": base.rnd(float(arr.std(ddof=0)), 3),
        }

    @staticmethod
    def _event_day_sets(eph_df: pd.DataFrame, aspects_df: pd.DataFrame) -> dict:
        """Mercury retro / multi-retro / Saturn-aspect din - EK BAAR nikaalo.

        (Pehle ye har stock ke liye dobara hota tha -> bahut dheema tha.)
        """
        sets: dict[str, set] = {"mercury_retro": set(), "multi_retro": set(),
                                "saturn_major": set()}
        if eph_df is not None and len(eph_df):
            merc = eph_df[eph_df["body"].astype(str) == "Mercury"]
            sets["mercury_retro"] = set(merc[merc["is_retrograde"].astype(bool)]
                                        ["date"].astype(str))
            retro = eph_df[eph_df["is_retrograde"].astype(bool)]
            counts = retro.groupby("date")["body"].nunique()
            sets["multi_retro"] = set(counts[counts >= 2].index.astype(str))
        if aspects_df is not None and len(aspects_df):
            a = aspects_df[aspects_df["aspect"].astype(str).isin(MAJOR_ASPECTS)]
            mask = (a["body_a"].astype(str) == "Saturn") | (a["body_b"].astype(str) == "Saturn")
            sets["saturn_major"] = set(a[mask]["date"].astype(str))
        return sets

    def ephemeris_event_study(self, sym_df: pd.DataFrame, day_sets: dict) -> dict:
        """Mercury retro / Saturn aspect wale dino me stock ka behaviour."""
        out: dict[str, dict] = {}
        if sym_df is None or len(sym_df) < 60 or not day_sets:
            return out
        labels = {
            "mercury_retro": "Mercury retrograde",
            "multi_retro": "2+ planets retrograde",
            "saturn_major": "Saturn major aspect (Con/90/180/120)",
        }
        for horizon in self.horizons:
            fwd = self._forward_returns(sym_df, horizon)
            baseline = self._condition_stats(fwd, set(fwd.keys()), "all days (baseline)")
            base_mean = baseline.get("mean_return_pct")
            stats = [baseline]
            for key, label in labels.items():
                stats.append(self._condition_stats(fwd, day_sets.get(key, set()), label))
            for st in stats:
                if isinstance(base_mean, (int, float)) and isinstance(
                        st.get("mean_return_pct"), (int, float)):
                    st["vs_baseline_pct"] = round(st["mean_return_pct"] - base_mean, 3)
            out[f"forward_{horizon}d"] = stats
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _hist_score(returns: dict, event_study: dict) -> tuple[float, list[str]]:
        """0-100 historical score (transparent rules)."""
        score = 50.0
        reasons: list[str] = []
        r1 = returns.get("return_1y_pct")
        r5 = returns.get("return_5y_pct")
        dd = returns.get("max_drawdown_5y_pct")
        vol = returns.get("volatility_20d_annual_pct")

        if isinstance(r1, (int, float)):
            adj = max(-15.0, min(15.0, r1 / 2.0))
            score += adj
            reasons.append(f"1-year return is {r1:.1f}%")
        if isinstance(r5, (int, float)):
            score += 8 if r5 > 0 else -8
            reasons.append(f"5-year return is {r5:.1f}%")
        if isinstance(dd, (int, float)):
            score += 8 if dd > -25 else (-8 if dd < -50 else 0)
            reasons.append(f"worst 5-year fall (max drawdown) is {dd:.1f}%")
        if isinstance(vol, (int, float)):
            if vol < 25:
                score += 5
                reasons.append(f"volatility is calm ({vol:.1f}% annualised)")
            elif vol > 45:
                score -= 7
                reasons.append(f"volatility is high ({vol:.1f}% annualised)")

        # Ephemeris behaviour: agar Mercury retro me stock historically kamzor hai
        fwd = (event_study or {}).get("forward_5d") or []
        for st in fwd:
            if st.get("condition") == "Mercury retrograde" and isinstance(
                    st.get("vs_baseline_pct"), (int, float)):
                if st["vs_baseline_pct"] < -0.5 and st.get("sample_days", 0) >= 30:
                    score -= 5
                    reasons.append("historically this stock dips during Mercury retrograde")
                elif st["vs_baseline_pct"] > 0.5 and st.get("sample_days", 0) >= 30:
                    score += 4
                    reasons.append("historically this stock holds up during Mercury retrograde")
        return max(0.0, min(100.0, score)), reasons

    def run(self, symbols: list[str] | None = None, book: pd.DataFrame | None = None,
            eph_df: pd.DataFrame | None = None,
            aspects_df: pd.DataFrame | None = None) -> dict:
        book = book if book is not None else base.load_stock_book()
        if book is None or len(book) == 0:
            return {"agent": self.name, "results": {}, "count": 0,
                    "error": "stock book empty - run the data build first"}
        if eph_df is None:
            eph_df = base.load_dataset("ephemeris_daily")
        if aspects_df is None:
            aspects_df = base.load_dataset("aspects_daily")

        # Ephemeris din ek baar nikaalo, phir har stock par reuse karo (fast).
        day_sets = self._event_day_sets(eph_df, aspects_df)
        groups = base.group_book(book)
        wanted = ([s.upper() for s in symbols] if symbols else None)

        results: dict[str, dict] = {}
        for sym, sub in groups.items():
            if wanted and sym not in wanted:
                continue
            if len(sub) < 60:
                continue
            returns = self._returns(sub)
            study = self.ephemeris_event_study(sub, day_sets)
            score, reasons = self._hist_score(returns, study)
            results[sym] = {
                "symbol": sym, "date": str(sub["date"].iloc[-1]),
                **returns,
                "ephemeris_event_study": study,
                "hist_score": base.rnd(score, 1),
                "reasons": reasons,
                "source": sorted(set(sub["source"].astype(str))),
            }
        return {"agent": self.name, "results": results, "count": len(results),
                "as_of": config.today_ist().isoformat()}

