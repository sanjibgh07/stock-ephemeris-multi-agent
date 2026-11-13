"""app/agents/market_agent.py - AGENT 4: final ranking (Option A).

Ye agent teen agents ko jodta hai:
    Technical (45%) + Historical (35%) + Ephemeris (20%)
Aur top 4-5 stocks deta hai - entry zone, stop-loss zone, target zone,
aur simple bhasha me reason.

NOTE: ye RESEARCH hai, BUY recommendation NAHI.
"""
from __future__ import annotations

from app import config
from app.agents import base

WEIGHTS = {"technical": 0.45, "historical": 0.35, "ephemeris": 0.20}


class MarketAgent:
    name = "market_agent"

    def __init__(self, top_n: int = 5, min_results: int = 4):
        self.top_n = top_n
        self.min_results = min_results

    # A stock is allowed into the final shortlist only when both independent
    # confirmations are positive.  Missing planetary evidence is never treated
    # as neutral evidence.
    TECHNICAL_CONFIRMATION_MIN = 60.0
    PLANETARY_CONFIRMATION_MIN = 50.0

    # ------------------------------------------------------------------
    @staticmethod
    def _levels(tech: dict) -> dict:
        """Entry / Stop / Target zones - ATR + support/resistance se."""
        close = tech["close"]
        atr = tech["atr14"]
        support = tech["support"]
        resistance = tech["resistance"]
        if not all(isinstance(v, (int, float)) for v in (close, atr)):
            return {"entry_zone": config.MISSING, "stop_loss_zone": config.MISSING,
                    "target_zone": config.MISSING, "risk_reward": config.MISSING}

        entry_low = round(close - 0.5 * atr, 2)
        entry_high = round(close + 0.5 * atr, 2)
        stop = close - 2.0 * atr
        if isinstance(support, (int, float)):
            stop = min(stop, support * 0.99)
        target = close + 3.0 * atr
        if isinstance(resistance, (int, float)) and resistance > close:
            target = max(target, resistance * 1.005)

        stop = max(0.01, round(stop, 2))
        target = round(target, 2)
        risk = close - stop
        reward = target - close
        rr = round(reward / risk, 2) if risk > 0 else config.MISSING

        # Guarantee the ordering: stop_high < entry_low < entry_high < target_low
        stop_high = min(round(stop * 1.005, 2), round(entry_low - 0.01, 2))
        stop_low = round(min(stop * 0.995, stop_high - 0.01), 2)
        target_low = max(round(target * 0.995, 2), round(entry_high + 0.01, 2))
        target_high = round(max(target * 1.005, target_low + 0.01), 2)
        return {
            "entry_zone": [entry_low, entry_high],
            "stop_loss_zone": [stop_low, stop_high],
            "target_zone": [target_low, target_high],
            "risk_reward": rr,
            "atr14": atr,
        }

    @staticmethod
    def _risk_note(tech: dict, hist: dict, eph: dict, conditions: dict) -> str:
        notes: list[str] = []
        vol = tech.get("volatility_20d_annual_pct")
        if isinstance(vol, (int, float)) and vol > 40:
            notes.append(f"high volatility ({vol}% annualised) - position size small rakhein")
        rsi = tech.get("rsi14")
        if isinstance(rsi, (int, float)) and rsi > 70:
            notes.append("RSI overbought - entry ko confirm hone dein")
        dd = hist.get("max_drawdown_5y_pct")
        if isinstance(dd, (int, float)) and dd < -45:
            notes.append(f"stock has fallen {dd}% from peak in the last 5 years")
        retro = conditions.get("retrograde") or []
        if conditions.get("heavy_retro"):
            notes.append("3+ planets retrograde now - historically choppy period")
        elif "Mercury" in retro:
            notes.append("Mercury retrograde now - news/communications noise possible")
        if not notes:
            notes.append("no major red flag found in the data")
        if eph.get("available") is False:
            notes.append("ephemeris regime study had too few matching days for this stock")
        return "; ".join(notes)

    # ------------------------------------------------------------------
    def rank(self, technical: dict, historical: dict, ephemeris: dict,
             news: dict | None = None, validation: dict | None = None) -> list[dict]:
        tech_res = (technical or {}).get("results", {})
        hist_res = (historical or {}).get("results", {})
        eph_res = (ephemeris or {}).get("per_symbol", {})
        conditions = (ephemeris or {}).get("conditions", {})
        news_res = news or {}
        news_by_symbol = news_res.get("per_symbol", {})
        validation_res = validation or {}
        validation_pass = validation_res.get("quality") == "PASS"

        rows: list[dict] = []
        for sym, t in tech_res.items():
            h = hist_res.get(sym, {})
            e = eph_res.get(sym, {})
            tech_score = t.get("tech_score") if isinstance(t.get("tech_score"), (int, float)) else None
            hist_score = h.get("hist_score") if isinstance(h.get("hist_score"), (int, float)) else None
            eph_score = (e.get("ephemeris_score")
                         if isinstance(e.get("ephemeris_score"), (int, float)) else None)
            tech_positive = bool(tech_score is not None and
                                 tech_score >= self.TECHNICAL_CONFIRMATION_MIN)
            matched_days = e.get("matched_days") if isinstance(e, dict) else None
            mean_return = e.get("mean_return_pct") if isinstance(e, dict) else None
            hit_rate = e.get("hit_rate_pct") if isinstance(e, dict) else None
            planetary_positive = bool(
                e.get("available") is True and eph_score is not None and
                eph_score > self.PLANETARY_CONFIRMATION_MIN and
                isinstance(matched_days, (int, float)) and matched_days >= 20 and
                isinstance(mean_return, (int, float)) and mean_return > 0 and
                isinstance(hit_rate, (int, float)) and hit_rate > 50
            )
            news_item = news_by_symbol.get(sym, {})
            news_risk_flag = news_item.get("status") == "RISK_FLAG"
            if tech_positive and planetary_positive and not news_risk_flag and validation_pass:
                signal_status = "confirmed"
            elif tech_positive or planetary_positive:
                signal_status = "watchlist"
            else:
                signal_status = "rejected"
            decision_reasons = []
            decision_reasons.append("technical confirmation positive"
                                    if tech_positive else
                                    "technical confirmation not positive")
            decision_reasons.append("planetary historical confirmation positive"
                                    if planetary_positive else
                                    "planetary historical confirmation unavailable or not positive")
            decision_reasons.append("fresh NSE disclosure risk flag present"
                                    if news_risk_flag else
                                    ("fresh NSE disclosure check clear" if news_res.get("available")
                                     else "fresh NSE disclosure data unavailable; no positive claim"))
            if not validation_pass:
                decision_reasons.append("walk-forward validation is WEAK; confirmation blocked")
            combined = (WEIGHTS["technical"] * (tech_score if tech_score is not None else 0.0) +
                        WEIGHTS["historical"] * (hist_score if hist_score is not None else 0.0) +
                        WEIGHTS["ephemeris"] * (eph_score if eph_score is not None else 0.0))
            levels = self._levels(t)
            rows.append({
                "symbol": sym,
                "as_of": t.get("date"),
                "close": t.get("close"),
                "combined_score": base.rnd(combined, 1),
                "signal_status": signal_status,
                "technical_confirmation": tech_positive,
                "planetary_confirmation": planetary_positive,
                "decision_reasons": decision_reasons,
                "planetary_4w_evidence": {
                    "available": e.get("available", False),
                    "matched_days": matched_days,
                    "mean_return_pct": mean_return,
                    "baseline_mean_return_pct": e.get("baseline_mean_return_pct"),
                    "hit_rate_pct": hit_rate,
                    "horizon_days": e.get("horizon_days", 20),
                },
                "fresh_news": news_item or {
                    "status": "NOT_AVAILABLE" if not news_res.get("available") else "CLEAR",
                    "item_count": 0,
                },
                "validation_quality": validation_res.get("quality", "NOT_AVAILABLE"),
                "score_breakdown": {"technical": t.get("tech_score"),
                                    "historical": h.get("hist_score"),
                                    "ephemeris": e.get("ephemeris_score")},
                "source": t.get("source", []),
                "reasons": (t.get("reasons", [])[:4] + h.get("reasons", [])[:3]),
                **levels,
                "risk_note": self._risk_note(t, h, e, conditions),
                "key_numbers": {
                    "rsi14": t.get("rsi14"), "macd_hist": t.get("macd_hist"),
                    "sma20": t.get("sma20"), "sma50": t.get("sma50"), "sma200": t.get("sma200"),
                    "high_52w": t.get("high_52w"), "low_52w": t.get("low_52w"),
                    "volume_spike": t.get("volume_spike"),
                    "breakout_near_52w_high": t.get("breakout_near_52w_high"),
                    "return_20d_pct": t.get("return_20d_pct"),
                    "return_1y_pct": h.get("return_1y_pct"),
                    "max_drawdown_5y_pct": h.get("max_drawdown_5y_pct"),
                    "volatility_20d_annual_pct": t.get("volatility_20d_annual_pct"),
                    "ephemeris_regime": e.get("conditions_matched"),
                },
            })
        rows.sort(key=lambda r: r["combined_score"], reverse=True)
        return rows


    # ------------------------------------------------------------------
    def run(self, technical: dict, historical: dict, ephemeris: dict,
            news: dict | None = None, validation: dict | None = None,
            top_n: int | None = None) -> dict:
        """Final Option-A output: 4-5 stocks with reason, entry, stop, target."""
        top_n = top_n or self.top_n
        ranked = self.rank(technical, historical, ephemeris, news=news,
                           validation=validation)
        if not ranked:
            return {"agent": self.name, "available": False, "stocks": [],
                    "error": ("no stock analysis available - the stock book is empty. "
                              "Run the data build first (run.py does it automatically)."),
                    "disclaimer": base.DISCLAIMER, "disclaimer_hi": base.DISCLAIMER_HI}

        regime = (ephemeris or {}).get("conditions", {})
        model = (ephemeris or {}).get("risk_model", {})
        upcoming = (ephemeris or {}).get("upcoming") or {}
        confirmed = [r for r in ranked if r["signal_status"] == "confirmed"]
        watchlist = [r for r in ranked if r["signal_status"] == "watchlist"]
        rejected = [r for r in ranked if r["signal_status"] == "rejected"]
        picks = confirmed[:top_n]
        for pick in picks:
            pick["plain_reason"] = self._plain_reason(pick, regime)
        for item in watchlist + rejected:
            item["plain_reason"] = self._plain_reason(item, regime)

        return {
            "agent": self.name,
            "available": True,
            "mode": "stocks_3_to_4_weeks",
            "horizon": "4 weeks (approximately 20 trading days)",
            "as_of": config.today_ist().isoformat(),
            "picks": picks,
            "pick_count": len(picks),
            "confirmed_count": len(confirmed),
            "watchlist": watchlist[:max(top_n, 5)],
            "watchlist_count": len(watchlist),
            "rejected_count": len(rejected),
            "confirmation_policy": {
                "technical_min_score": self.TECHNICAL_CONFIRMATION_MIN,
                "planetary_min_score": self.PLANETARY_CONFIRMATION_MIN,
                "minimum_planetary_matching_days": 20,
                "requires_positive_mean_return": True,
                "requires_hit_rate_above_pct": 50,
                "rule": "Only stocks positive on both technical and planetary historical evidence are confirmed.",
            },
            "candidates_considered": len(ranked),
            "weights": WEIGHTS,
            "market_context": {
                "ephemeris_conditions": regime,
                "ephemeris_risk_model": model,
                "upcoming_3_to_4_weeks": {
                    "events": (upcoming.get("events") or [])[:25],
                    "tight_aspects": (upcoming.get("tight_aspects") or [])[:25],
                    "note": upcoming.get("note"),
                } if upcoming else None,
                "fresh_news": {
                    "status": (news or {}).get("status", "NOT_AVAILABLE"),
                    "available": bool((news or {}).get("available")),
                    "as_of": (news or {}).get("as_of"),
                    "source": (news or {}).get("source"),
                    "reason": (news or {}).get("reason"),
                    "items": ((news or {}).get("items") or [])[:20],
                },
                "walk_forward_validation": validation or {
                    "available": False, "quality": "NOT_AVAILABLE",
                },
            },
            "method_note": ("Final shortlist requires technical and planetary evidence, "
                            "a clear fresh-disclosure check, and a PASS walk-forward validation. "
                            "Technical score >= 60 and positive planetary historical evidence "
                            "must have adequate sample size, positive mean return and hit rate above 50%. "
                            "Scores are evidence summaries, not guarantees. "
                            "If no stock passes both gates, no confirmed signal is reported."),
            "disclaimer": base.DISCLAIMER,
            "disclaimer_hi": base.DISCLAIMER_HI,
        }

    @staticmethod
    def _plain_reason(pick: dict, regime: dict) -> str:
        """Simple English/Hindi-mix reason a non-coder can read."""
        t = pick["key_numbers"]
        parts = [f"Trend: price {pick['close']} vs 50-day avg {t.get('sma50')}",
                 f"RSI {t.get('rsi14')}"]
        if t.get("breakout_near_52w_high"):
            parts.append("sitting near its 52-week high")
        if t.get("volume_spike"):
            parts.append("volume above its 20-day average")
        r1 = t.get("return_1y_pct")
        if isinstance(r1, (int, float)):
            parts.append(f"1-year return {r1}%")
        dd = t.get("max_drawdown_5y_pct")
        if isinstance(dd, (int, float)):
            parts.append(f"worst 5-year drawdown {dd}%")
        retro = regime.get("retrograde") or []
        if retro:
            parts.append("sky now: " + ", ".join(retro) + " retrograde")
        return ". ".join(parts) + "."
