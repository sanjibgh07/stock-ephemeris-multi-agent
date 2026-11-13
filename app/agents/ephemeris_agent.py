"""app/agents/ephemeris_agent.py - AGENT 3: aakash (planetary) analysis.

IMPORTANT (zaroori baat):
  * Ye agent PRICE PREDICT nahi karta. Ye sirf "correlation" dikhata hai -
    matlab pichhle dino me jab aisa aakash tha, tab market kaisa chala.
  * Aage ke 3-4 hafte ke aspects / retrograde / ingress list karta hai.
  * Har din ka 0-100 "volatility risk score" deta hai (history se,
    koi random guess nahi).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from app import config
from app.agents import base
from app.agents.historical_agent import HistoricalAgent

STRESS_ASPECTS = ("Conjunction", "Square", "Opposition")
MAJOR_ASPECTS = ("Conjunction", "Square", "Opposition", "Trine")


class EphemerisAgent:
    name = "ephemeris_agent"

    def __init__(self, index_symbol: str = "SENSEX"):
        self.index_symbol = index_symbol

    # ------------------------------------------------------------------
    @staticmethod
    def _market_returns() -> pd.DataFrame:
        """Market daily returns (deepest available index: Sensex 2000+)."""
        idx = base.load_index_book()
        if idx is None or len(idx) == 0:
            return pd.DataFrame()
        for symbol in ("SENSEX", "NIFTY50", "NIFTY100"):
            sub = base.for_symbol(idx, symbol)
            if len(sub) > 2000:
                sub = sub.assign(ret=sub["close"].astype(float).pct_change() * 100.0,
                                 abs_ret=lambda d: d["ret"].abs())
                sub["index_symbol"] = symbol
                return sub[["date", "ret", "abs_ret", "index_symbol"]]
        return pd.DataFrame()

    @staticmethod
    def _daily_features(eph_df: pd.DataFrame, aspects_df: pd.DataFrame,
                        events_df: pd.DataFrame) -> pd.DataFrame:
        """Har din ke 'aakash features' (asli ephemeris data se)."""
        if eph_df is None or len(eph_df) == 0:
            return pd.DataFrame()
        feat = pd.DataFrame({"date": sorted(eph_df["date"].astype(str).unique())})

        retro = eph_df[eph_df["is_retrograde"].astype(bool)]
        retro_cnt = retro.groupby("date")["body"].nunique().rename("retro_count")
        feat = feat.merge(retro_cnt, left_on="date", right_index=True, how="left")

        eph = eph_df
        moon = eph[eph["body"].astype(str) == "Moon"][["date", "longitude_deg"]]
        sun = eph[eph["body"].astype(str) == "Sun"][["date", "longitude_deg"]]
        phase = moon.merge(sun, on="date", suffixes=("_moon", "_sun"))
        phase["elong"] = (phase["longitude_deg_moon"] - phase["longitude_deg_sun"]) % 360.0
        feat = feat.merge(phase[["date", "elong"]], on="date", how="left")

        if aspects_df is not None and len(aspects_df):
            a = aspects_df.copy()
            cnt = a.groupby("date")["aspect"].count().rename("aspect_count")
            feat = feat.merge(cnt, left_on="date", right_index=True, how="left")
            stress = a[a["aspect"].astype(str).isin(STRESS_ASPECTS)]
            moon_stress = stress[(stress["body_a"].astype(str) == "Moon") |
                                 (stress["body_b"].astype(str) == "Moon")]
            ms = moon_stress.groupby("date")["aspect"].count().rename("moon_stress")
            feat = feat.merge(ms, left_on="date", right_index=True, how="left")
            hard = stress[~stress["aspect"].astype(str).isin(["Trine", "Sextile"])]
            hc = hard.groupby("date")["aspect"].count().rename("hard_aspects")
            feat = feat.merge(hc, left_on="date", right_index=True, how="left")

        if events_df is not None and len(events_df):
            ecl = events_df[events_df["event_type"].astype(str).str.startswith("eclipse")]
            eclipse_days: set[str] = set()
            for d in ecl["date"].astype(str):
                try:
                    base_day = dt.date.fromisoformat(d)
                except ValueError:
                    continue
                for off in range(-3, 4):
                    eclipse_days.add((base_day + dt.timedelta(days=off)).isoformat())
            feat["eclipse_window"] = feat["date"].isin(eclipse_days)

        for col in ("retro_count", "aspect_count", "moon_stress", "hard_aspects"):
            if col in feat.columns:
                feat[col] = pd.to_numeric(feat[col], errors="coerce").fillna(0)
        feat["heavy_retro"] = feat.get("retro_count", 0) >= 3
        if "elong" in feat.columns:
            e = feat["elong"]
            feat["moon_phase_extreme"] = ((e < 8) | (e > 352) | e.between(172, 188))
        else:
            feat["moon_phase_extreme"] = False
        return feat


    # ------------------------------------------------------------------
    def risk_model(self) -> dict:
        """History se dekho: kis feature wale din me market zyada hila tha?"""
        rets = self._market_returns()
        eph_df = base.load_dataset("ephemeris_daily")
        aspects_df = base.load_dataset("aspects_daily")
        events_df = base.load_dataset("events_daily")
        if len(rets) == 0 or len(eph_df) == 0:
            return {"available": False,
                    "reason": "need index history + ephemeris book (run the data build first)"}
        feat = self._daily_features(eph_df, aspects_df, events_df)
        merged = feat.merge(rets, on="date", how="inner")
        if len(merged) < 200:
            return {"available": False, "reason": "not enough overlapping days"}

        baseline = float(merged["abs_ret"].mean())
        if "aspect_count" in merged.columns and merged["aspect_count"].notna().any():
            thresh = float(merged["aspect_count"].quantile(0.75))
        else:
            thresh = float("inf")
        merged["many_aspects"] = merged.get("aspect_count", 0) >= thresh

        features = {
            "many_aspects": "many aspects active (busy sky)",
            "moon_stress": "Moon in conjunction/square/opposition",
            "hard_aspects": "hard aspects present (0/90/180)",
            "heavy_retro": "3 or more planets retrograde",
            "eclipse_window": "within 3 days of an eclipse",
            "moon_phase_extreme": "near New Moon or Full Moon",
        }
        stats: dict[str, dict] = {}
        for col, label in features.items():
            if col not in merged.columns:
                continue
            mask = merged[col].astype(bool)
            n = int(mask.sum())
            if n < 30:
                stats[col] = {"label": label, "sample_days": n,
                              "note": "too few days for a reliable correlation"}
                continue
            cond_mean = float(merged.loc[mask, "abs_ret"].mean())
            stats[col] = {
                "label": label, "sample_days": n,
                "baseline_avg_abs_move_pct": round(baseline, 4),
                "condition_avg_abs_move_pct": round(cond_mean, 4),
                "ratio": round(cond_mean / baseline, 4) if baseline else None,
                "interpretation": ("market moved MORE than usual" if cond_mean > baseline
                                   else "market moved LESS than usual"),
            }
        return {
            "available": True,
            "index_used": str(rets["index_symbol"].iloc[0]),
            "baseline_avg_abs_move_pct": round(baseline, 4),
            "sample_days": int(len(merged)),
            "features": stats,
            "note": ("This is HISTORICAL CORRELATION only, not a price prediction. "
                     "Correlation is not causation."),
        }

    # ------------------------------------------------------------------
    def upcoming(self, days: int = 28) -> dict:
        """Aage ke dinon ke events + exact aspects (Swiss Ephemeris se, abhi)."""
        try:
            from data import fetch_ephemeris as fe  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": f"pyswisseph issue: {exc}"}
        if fe.swe is None:
            return {"available": False, "reason": "pyswisseph not installed"}
        fe.init_engine()
        today = config.today_ist()
        rows: list[dict] = []
        aspects: list[dict] = []
        prev: dict[str, dict] = {}
        for offset in range(1, days + 1):
            day = today + dt.timedelta(days=offset)
            body_rows, lons = fe.compute_day(day)
            cur = {r["body"]: r for r in body_rows}
            for name, r in cur.items():
                p = prev.get(name)
                if p is not None:
                    if bool(r["is_retrograde"]) != bool(p["is_retrograde"]):
                        rows.append({
                            "date": r["date"],
                            "event_type": ("retrograde_start" if r["is_retrograde"]
                                           else "retrograde_end"),
                            "body": name,
                            "detail": f"{name} turns "
                                      f"{'retrograde' if r['is_retrograde'] else 'direct'} "
                                      f"in {r['sign']}"})
                    if str(r["sign"]) != str(p["sign"]):
                        rows.append({"date": r["date"], "event_type": "ingress", "body": name,
                                     "detail": f"{name} enters {r['sign']}"})
            prev = cur
            speeds = {k: v["speed_deg_per_day"] for k, v in cur.items()}
            aspects.extend(fe.find_aspects(day.isoformat(), lons, speeds, orb=2.0))
        ecl, method = fe._try_eclipses(today, today + dt.timedelta(days=days))
        rows.extend(ecl)
        return {"available": True, "window_days": days,
                "events": sorted(rows, key=lambda r: (r["date"], r["event_type"])),
                "tight_aspects": sorted(aspects, key=lambda r: (r["date"], r["orb_deg"]))[:200],
                "eclipse_check": method,
                "note": "Calculated live with Swiss Ephemeris for the next window."}

    # ------------------------------------------------------------------
    def current_conditions(self) -> dict:
        """Aaj ka aakash: kaun retrograde hai, kaun se aspects active hain."""
        eph_df = base.load_dataset("ephemeris_daily")
        aspects_df = base.load_dataset("aspects_daily")
        out = {"date": config.today_ist().isoformat(), "retrograde": [],
               "moon_sign": config.MISSING, "active_hard_aspects": []}
        if eph_df is None or len(eph_df) == 0:
            out["note"] = "ephemeris book empty"
            return out
        last_date = str(eph_df["date"].max())
        today = eph_df[eph_df["date"].astype(str) == last_date]
        out["ephemeris_date"] = last_date
        out["retrograde"] = sorted(today[today["is_retrograde"].astype(bool)]["body"]
                                   .astype(str).tolist())
        moon = today[today["body"].astype(str) == "Moon"]
        if len(moon):
            out["moon_sign"] = str(moon["sign"].iloc[0])
        if aspects_df is not None and len(aspects_df):
            a = aspects_df[aspects_df["date"].astype(str) == last_date]
            a = a[a["aspect"].astype(str).isin(MAJOR_ASPECTS)].sort_values("orb_deg").head(12)
            out["active_hard_aspects"] = [f"{r['body_a']}-{r['body_b']} {r['aspect']}"
                                          for _, r in a.iterrows()]
        out["heavy_retro"] = len(out["retrograde"]) >= 3
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def regime_conditions_days(conditions: dict, eph_df: pd.DataFrame) -> dict:
        """Aaj ke retrograde conditions se match karne wale purane din - EK BAAR."""
        out = {"days": set(), "labels": []}
        if eph_df is None or len(eph_df) == 0:
            return out
        retro_now = set(conditions.get("retrograde") or [])
        if retro_now:
            sub = eph_df[eph_df["body"].astype(str).isin(retro_now)
                         & eph_df["is_retrograde"].astype(bool)]
            counts = sub.groupby("date")["body"].nunique()
            out["days"] |= set(counts[counts >= max(1, len(retro_now))].index.astype(str))
            out["labels"].append("same retrograde planets as today")
        if conditions.get("heavy_retro"):
            sub2 = eph_df[eph_df["is_retrograde"].astype(bool)]
            c2 = sub2.groupby("date")["body"].nunique()
            out["days"] |= set(c2[c2 >= 3].index.astype(str))
            out["labels"].append("3+ planets retrograde")
        return out

    def symbol_regime_study(self, sym_df: pd.DataFrame, cond_days: set,
                            labels: list[str], horizon: int = 5) -> dict:
        """Aaj ke aakash jaisa aakash jab pehle tha, tab ye stock kaisa chala?"""
        if sym_df is None or len(sym_df) < 60 or not cond_days:
            return {"available": False, "reason": "no matching historical days"}

        fwd = HistoricalAgent._forward_returns(sym_df, horizon)
        if not fwd:
            return {"available": False, "reason": "no forward returns available"}
        cond_vals = [fwd[d] for d in cond_days if d in fwd]
        if len(cond_vals) < 20:
            return {"available": False, "reason": "too few matching historical days",
                    "matched_days": len(cond_vals)}
        base_vals = list(fwd.values())
        cond_mean = float(np.mean(cond_vals))
        base_mean = float(np.mean(base_vals))
        score = 50.0 + max(-25.0, min(25.0, (cond_mean - base_mean) * 8.0))
        return {
            "available": True, "horizon_days": horizon,
            "conditions_matched": labels, "matched_days": len(cond_vals),
            "mean_return_pct": round(cond_mean, 3),
            "baseline_mean_return_pct": round(base_mean, 3),
            "hit_rate_pct": round(float(np.mean(np.asarray(cond_vals) > 0) * 100.0), 1),
            "ephemeris_score": base.rnd(max(0.0, min(100.0, score)), 1),
        }

    # ------------------------------------------------------------------
    def run(self, symbols: list[str] | None = None, book: pd.DataFrame | None = None,
            upcoming_days: int = 28, include_upcoming: bool = True) -> dict:
        book = book if book is not None else base.load_stock_book()
        eph_df = base.load_dataset("ephemeris_daily")
        conditions = self.current_conditions()
        result = {
            "agent": self.name,
            "conditions": conditions,
            "risk_model": self.risk_model(),
            "upcoming": self.upcoming(upcoming_days) if include_upcoming else None,
            "per_symbol": {},
        }
        if book is not None and len(book) and len(eph_df):
            regime = self.regime_conditions_days(conditions, eph_df)  # ek baar
            groups = base.group_book(book)
            wanted = ([s.upper() for s in symbols] if symbols else None)
            for sym, sub in groups.items():
                if wanted and sym not in wanted:
                    continue
                # The user's horizon is an investment decision made today for
                # approximately four weeks, not an intraday prediction.
                study = self.symbol_regime_study(
                    sub, regime["days"], regime["labels"], horizon=20)
                if study.get("available"):
                    result["per_symbol"][sym] = study
        return result

