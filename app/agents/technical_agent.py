"""app/agents/technical_agent.py - AGENT 1: chart/price analysis.

Ye agent sirf ASLI price data dekhta hai (yahoo_backup / nse_official).
Kuch bhi guess nahi karta.

Indicators: RSI(14), MACD(12,26,9), SMA 20/50/200, Volume spike
(>1.5x 20-day avg), 52-week breakout, Support/Resistance, ATR(14).
"""
from __future__ import annotations

import pandas as pd

from app import config
from app.agents import base


class TechnicalAgent:
    """Chart-based analysis. Output: score 0-100 + saare numbers."""

    name = "technical_agent"

    def __init__(self, lookback: int = 260):
        self.lookback = lookback

    def analyze_frame(self, sym_df: pd.DataFrame) -> dict | None:
        if sym_df is None or len(sym_df) < 30:
            return None
        df = sym_df.tail(max(self.lookback, 260)).reset_index(drop=True)
        close = df["close"].astype(float)
        volume = df["volume"].astype(float) if "volume" in df else pd.Series(dtype=float)
        last = float(close.iloc[-1])

        rsi14 = float(base.rsi(close, 14).iloc[-1])
        macd_df = base.macd(close)
        macd_val = float(macd_df["macd"].iloc[-1])
        macd_sig = float(macd_df["signal"].iloc[-1])
        macd_hist = float(macd_df["hist"].iloc[-1])
        sma20 = float(base.sma(close, 20).iloc[-1])
        sma50 = float(base.sma(close, 50).iloc[-1])
        sma200 = float(base.sma(close, 200).iloc[-1]) if len(close) >= 200 else float("nan")
        atr14 = float(base.atr(df, 14).iloc[-1])

        vol_avg20 = float(volume.tail(20).mean()) if len(volume) else float("nan")
        vol_last = float(volume.iloc[-1]) if len(volume) else float("nan")
        volume_spike = bool(vol_avg20 and vol_avg20 > 0 and vol_last > 1.5 * vol_avg20)

        window_52w = df.tail(252)
        high_52w = float(window_52w["high"].max())
        low_52w = float(window_52w["low"].min())
        breakout = bool(high_52w and last > high_52w * 0.98)

        support = float(min(df["low"].tail(20).min(),
                            sma50 if sma50 == sma50 else last))
        resistance = float(max(df["high"].tail(20).max(), high_52w))

        ret_20d = base.pct(last, float(close.iloc[-21])) if len(close) > 21 else None
        vol_20d = base.annualised_vol(close.pct_change(), 20)

        score, reasons = self._score(last, rsi14, macd_val, macd_sig, macd_hist,
                                     sma20, sma50, sma200, volume_spike, breakout)

        return {
            "symbol": str(sym_df["symbol"].iloc[-1]),
            "date": str(df["date"].iloc[-1]),
            "close": base.rnd(last),
            "rsi14": base.rnd(rsi14, 1),
            "macd": base.rnd(macd_val, 3),
            "macd_signal": base.rnd(macd_sig, 3),
            "macd_hist": base.rnd(macd_hist, 3),
            "sma20": base.rnd(sma20), "sma50": base.rnd(sma50), "sma200": base.rnd(sma200),
            "atr14": base.rnd(atr14, 3),
            "volume_last": base.rnd(vol_last, 0),
            "volume_avg20": base.rnd(vol_avg20, 0),
            "volume_spike": volume_spike,
            "high_52w": base.rnd(high_52w), "low_52w": base.rnd(low_52w),
            "breakout_near_52w_high": breakout,
            "support": base.rnd(support), "resistance": base.rnd(resistance),
            "return_20d_pct": base.rnd(ret_20d, 2),
            "volatility_20d_annual_pct": base.rnd(vol_20d, 2),
            "tech_score": base.rnd(score, 1),
            "reasons": reasons,
            "source": sorted(set(df["source"].astype(str))) if "source" in df else [],
        }


    @staticmethod
    def _score(last, rsi14, macd_val, macd_sig, macd_hist, sma20, sma50, sma200,
               volume_spike, breakout) -> tuple[float, list[str]]:
        """Simple, transparent 0-100 score (har point ka reason likha hai)."""
        score = 50.0
        reasons: list[str] = []
        if rsi14 >= 70:
            score -= 10
            reasons.append(f"RSI {rsi14:.0f} is overbought (thoda thanda ho sakta hai)")
        elif rsi14 >= 55:
            score += 12
            reasons.append(f"RSI {rsi14:.0f} shows healthy momentum")
        elif rsi14 >= 45:
            score += 6
            reasons.append(f"RSI {rsi14:.0f} is neutral")
        elif rsi14 >= 30:
            score -= 2
            reasons.append(f"RSI {rsi14:.0f} is weak")
        else:
            score += 4
            reasons.append(f"RSI {rsi14:.0f} is oversold (bounce chance)")

        if macd_hist > 0 and macd_val > macd_sig:
            score += 12
            reasons.append("MACD is above its signal line (bullish)")
        elif macd_hist > 0:
            score += 6
            reasons.append("MACD histogram turned positive")
        else:
            score -= 8
            reasons.append("MACD is below its signal line (bearish)")

        score += 6 if last > sma20 else -4
        reasons.append("Price is above 20-day average" if last > sma20
                       else "Price is below 20-day average")
        score += 8 if last > sma50 else -6
        reasons.append("Price is above 50-day average" if last > sma50
                       else "Price is below 50-day average")
        if sma200 == sma200:
            score += 8 if last > sma200 else -8
            reasons.append("Price is above 200-day average (long-term uptrend)"
                           if last > sma200 else
                           "Price is below 200-day average (long-term downtrend)")
        if volume_spike:
            score += 6
            reasons.append("Volume is 1.5x above its 20-day average (high activity)")
        if breakout:
            score += 10
            reasons.append("Price is near/above its 52-week high (breakout zone)")
        return max(0.0, min(100.0, score)), reasons

    def run(self, symbols: list[str] | None = None,
            book: pd.DataFrame | None = None) -> dict:
        """Saare symbols ka technical view."""
        book = book if book is not None else base.load_stock_book()
        if book is None or len(book) == 0:
            return {"agent": self.name, "results": {}, "count": 0,
                    "error": "stock book empty - run the data build first"}
        groups = base.group_book(book)          # ek baar group -> fast
        wanted = ([s.upper() for s in symbols] if symbols else None)
        results: dict[str, dict] = {}
        for sym, sub in groups.items():
            if wanted and sym not in wanted:
                continue
            out = self.analyze_frame(sub)
            if out:
                results[sym] = out
        return {"agent": self.name, "results": results, "count": len(results),
                "as_of": config.today_ist().isoformat()}
