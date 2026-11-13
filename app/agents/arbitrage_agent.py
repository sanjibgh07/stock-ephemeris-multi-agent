"""app/agents/arbitrage_agent.py - AGENT 5: arbitrage (Option B).

Char tarah ke arbitrage check karta hai:
  1) Same stock NSE vs BSE price difference
  2) Nifty Futures vs Spot (premium/discount)
  3) ETF market price vs its NAV (AMFI official feed)
  4) Same stock different listing (vendors: .NS vs .BO)

Cost model: brokerage + STT + exchange + GST + stamp duty (India).
Agar spread cost se kam hai -> "NOT PROFITABLE" likha jaata hai.
Kabhi bhi negative profit ko profitable nahi dikhaya jaata.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from app import config
from app.agents import base


class ArbitrageAgent:
    name = "arbitrage_agent"

    def __init__(self, limit: int = 25):
        self.limit = limit

    # ------------------------------------------------------------------
    @staticmethod
    def cost_model() -> dict:
        """Chhoti cost table (percent me, % me convert karke)."""
        bps = dict(config.ARB_COST_BPS)
        one_way_pct = sum(bps.values()) / 100.0          # basis points -> percent
        return {
            "components_bps": bps,
            "one_way_cost_pct": round(one_way_pct, 4),
            "round_trip_cost_pct": round(one_way_pct * 2, 4),
            "min_required_spread_pct": config.ARB_MIN_PROFITABLE_SPREAD_PCT,
            "note": ("Indicative discount-broker cost model. Actual cost depends on "
                     "your broker, taxes and quantities."),
        }

    @staticmethod
    def _verdict(gross_spread_pct: float, cost_pct: float, min_spread: float,
                 stale_days: int | None = None) -> dict:
        """Profitability ka saaf, honest faisla.

        `stale_days` = dono legs ki quote date ka fark. Agar legs alag-alag
        dinon ke hain to difference asli arbitrage NAHI hai (stale-price
        artefact) - usko profitable nahi dikhaya jaata.
        """
        if gross_spread_pct is None:
            return {"profitable": False, "reason": "spread could not be computed"}
        net = gross_spread_pct - cost_pct
        if stale_days is not None and abs(stale_days) >= 1:
            return {
                "profitable": False, "net_spread_pct": round(net, 4),
                "reason": (f"quote dates differ by {abs(stale_days)} day(s) - the difference "
                           f"is a stale-price artefact, NOT a valid arbitrage "
                           f"(buy side and sell side must be the same session)"),
            }
        if gross_spread_pct < min_spread:
            return {"profitable": False, "net_spread_pct": round(net, 4),
                    "reason": (f"spread {gross_spread_pct:.3f}% is below the "
                               f"{min_spread}% minimum - NOT PROFITABLE after safety margin")}
        if net <= 0:
            return {"profitable": False, "net_spread_pct": round(net, 4),
                    "reason": (f"after costs ({cost_pct:.3f}%) the net spread is "
                               f"{net:.3f}% - NOT PROFITABLE")}
        return {"profitable": True, "net_spread_pct": round(net, 4),
                "reason": f"net spread {net:.3f}% after costs - worth checking"}

    # ------------------------------------------------------------------
    def nse_vs_bse(self, stock_book=None, bse_quotes=None) -> list[dict]:
        """NSE vs BSE same stock ka price difference (asli data se)."""
        stock_book = stock_book if stock_book is not None else base.load_stock_book()
        bse_quotes = bse_quotes if bse_quotes is not None else base.load_dataset("arbitrage_quotes")
        out: list[dict] = []
        if stock_book is None or len(stock_book) == 0 or bse_quotes is None or len(bse_quotes) == 0:
            return out

        nse_last = (stock_book.sort_values("date").groupby("symbol").tail(1)
                    [["symbol", "date", "close", "source"]])
        bse = bse_quotes.copy()
        bse["plain"] = bse["symbol"].astype(str).str.replace(".BO", "", regex=False).str.upper()
        bse_last = (bse.sort_values("date").groupby("plain").tail(1)
                    [["plain", "date", "close", "source"]])
        merged = nse_last.merge(bse_last, left_on="symbol", right_on="plain",
                               suffixes=("_nse", "_bse"))
        cost = self.cost_model()
        for _, row in merged.head(self.limit).iterrows():
            nse_px = float(row["close_nse"]) if row["close_nse"] == row["close_nse"] else None
            bse_px = float(row["close_bse"]) if row["close_bse"] == row["close_bse"] else None
            if not nse_px or not bse_px:
                continue
            spread_pct = (bse_px - nse_px) / nse_px * 100.0
            gross = abs(spread_pct)
            try:
                age = (pd.Timestamp(str(row["date_nse"])) -
                       pd.Timestamp(str(row["date_bse"]))).days
            except Exception:  # noqa: BLE001
                age = None
            verdict = self._verdict(gross, cost["round_trip_cost_pct"],
                                    cost["min_required_spread_pct"], stale_days=age)
            buy_on = "NSE" if spread_pct > 0 else "BSE"
            sell_on = "BSE" if spread_pct > 0 else "NSE"
            out.append({
                "type": "nse_vs_bse",
                "symbol": str(row["symbol"]),
                "nse_close": base.rnd(nse_px), "bse_close": base.rnd(bse_px),
                "nse_date": str(row["date_nse"]), "bse_date": str(row["date_bse"]),
                "quote_date_gap_days": age,
                "spread_pct": base.rnd(spread_pct, 4),
                "gross_spread_pct": base.rnd(gross, 4),
                "estimated_cost_pct": cost["round_trip_cost_pct"],
                "buy_on": buy_on, "sell_on": sell_on,
                "expiry": "not applicable (spot equity, no expiry)",
                "stale_data_warning": (bool(age is not None and abs(age) >= 2) or
                                       "second listing may be thinly traded"),
                "sources": {"nse": str(row["source_nse"]), "bse": str(row["source_bse"])},
                **verdict,
            })
        out.sort(key=lambda r: r["gross_spread_pct"] or 0, reverse=True)
        return out

    # ------------------------------------------------------------------
    def futures_vs_spot(self, stock_book=None, lookback_days: int = 6) -> dict:
        """Nifty/stock Futures vs Spot (official NSE F&O bhavcopy se, best effort)."""
        from data import http_util, fetch_nse  # noqa: PLC0415

        session = http_util.browser_session(referer=fetch_nse.NIFTYINDICES_HOST + "/")
        today = config.today_ist()
        fo_df = None
        used_day = None
        attempts = 0
        max_attempts = 4            # network par load kam rakhne ke liye cap
        for back in range(lookback_days + 3):
            if attempts >= max_attempts:
                break
            day = today - pd.Timedelta(days=back)
            day = day.date() if hasattr(day, "date") else day
            if day.weekday() >= 5:
                continue
            attempts += 1
            fo_df = fetch_nse.fetch_fo_bhavcopy(session, day)
            if fo_df is not None and len(fo_df):
                used_day = day.isoformat()
                break
        if fo_df is None or len(fo_df) == 0:
            return {"available": False, "items": [],
                    "reason": ("official NSE F&O bhavcopy not available from this network "
                               "(logged in logs/fetch_log.jsonl and state.source_failures)"),
                    "checked_days": lookback_days + 3}

        book = stock_book if stock_book is not None else base.load_stock_book()
        spot: dict[str, float] = {}
        spot_date: dict[str, str] = {}
        if book is not None and len(book):
            last = book.sort_values("date").groupby("symbol").tail(1)
            for k, v, d in zip(last["symbol"].astype(str), last["close"],
                               last["date"].astype(str)):
                if v == v:
                    spot[str(k).upper()] = float(v)
                    spot_date[str(k).upper()] = str(d)
        # Index futures ke liye index book se spot lo (NIFTY/BANKNIFTY ...)
        idx_book = base.load_index_book()
        if idx_book is not None and len(idx_book):
            idx_last = idx_book.sort_values("date").groupby("symbol").tail(1)
            for k, v, d in zip(idx_last["symbol"].astype(str), idx_last["close"],
                               idx_last["date"].astype(str)):
                if v == v:
                    spot.setdefault(str(k).upper(), float(v))
                    spot_date.setdefault(str(k).upper(), str(d))
            for alias in ("NIFTY", "NIFTY 50", "NIFTY50"):
                if "NIFTY50" in spot:
                    spot.setdefault(alias, spot["NIFTY50"])
                    spot_date.setdefault(alias, spot_date.get("NIFTY50"))

        cost = self.cost_model()
        items: list[dict] = []
        fo = fo_df.copy()
        fo["SYMBOL"] = fo["SYMBOL"].astype(str).str.strip().str.upper()
        if "INSTRUMENT" in fo.columns:
            inst = fo["INSTRUMENT"].astype(str).str.upper().str.strip()
            # old file: FUTIDX/FUTSTK   |   new file: STF (stock fut), IDF (index fut)
            fut = fo[inst.str.contains("FUT", na=False)
                     | inst.isin({"STF", "IDF", "SF", "IF"})]
        else:
            fut = fo
        if "OPTION_TYP" in fut.columns:
            opt = fut["OPTION_TYP"].astype(str).str.strip().str.upper()
            fut = fut[opt.isin({"", "XX", "NAN", "NONE"}) | opt.isna()]
        if "EXPIRY_DT" in fut.columns:
            fut = fut.assign(_exp=pd.to_datetime(fut["EXPIRY_DT"], errors="coerce",
                                                 format="mixed", dayfirst=True))
            fut = fut.sort_values("_exp").groupby("SYMBOL").head(1)
        for _, row in fut.iterrows():
            sym = str(row["SYMBOL"])
            fut_px = row.get("CLOSE")
            spot_px = spot.get(sym)
            try:
                fut_px = float(fut_px)
                spot_px = float(spot_px)
            except (TypeError, ValueError):
                continue
            if not fut_px or not spot_px:
                continue
            premium_pct = (fut_px - spot_px) / spot_px * 100.0
            gross = abs(premium_pct)
            try:
                gap = (pd.Timestamp(str(used_day)) -
                       pd.Timestamp(str(spot_date.get(sym)))).days \
                    if spot_date.get(sym) else None
            except Exception:  # noqa: BLE001
                gap = None
            verdict = self._verdict(gross, cost["round_trip_cost_pct"],
                                    cost["min_required_spread_pct"], stale_days=gap)
            items.append({
                "type": "futures_vs_spot", "symbol": sym,
                "spot_close": base.rnd(spot_px), "future_close": base.rnd(fut_px),
                "expiry": str(row.get("EXPIRY_DT", config.MISSING)),
                "premium_pct": base.rnd(premium_pct, 4),
                "gross_spread_pct": base.rnd(gross, 4),
                "estimated_cost_pct": cost["round_trip_cost_pct"],
                "buy_on": "SPOT" if premium_pct > 0 else "FUTURES",
                "sell_on": "FUTURES" if premium_pct > 0 else "SPOT",
                "as_of": used_day, "spot_date": spot_date.get(sym),
                "quote_date_gap_days": gap,
                "sources": {"fo_bhavcopy": config.SOURCE_NSE_OFFICIAL},
                **verdict,
            })
        items.sort(key=lambda r: r["gross_spread_pct"] or 0, reverse=True)
        return {"available": True, "as_of": used_day, "items": items[:self.limit],
                "total_futures_seen": int(len(fut))}

    # ------------------------------------------------------------------
    def etf_vs_nav(self) -> dict:
        """ETF market price vs AMFI NAV (official feed)."""
        from data import fetch_amfi, fetch_yahoo_backup  # noqa: PLC0415

        amfi = fetch_amfi.fetch_amfi_nav()
        if amfi is None or len(amfi) == 0:
            return {"available": False, "items": [],
                    "reason": ("official AMFI NAV feed not reachable from this network "
                               "(logged honestly, nothing invented)")}
        cost = self.cost_model()
        items: list[dict] = []
        for ticker, label in fetch_amfi.TRACKED_ETFS.items():
            nav_info = fetch_amfi.find_etf_nav(amfi, label)
            if not nav_info.get("available"):
                continue
            quotes = fetch_yahoo_backup.fetch_spot_quotes([ticker.split(".")[0]], "NS")
            quote = quotes.get(ticker.split(".")[0])
            if not quote or not quote.get("available"):
                continue
            price = float(quote["close"])
            nav = float(nav_info["nav"])
            premium_pct = (price - nav) / nav * 100.0
            gross = abs(premium_pct)
            verdict = self._verdict(gross, cost["round_trip_cost_pct"],
                                    cost["min_required_spread_pct"])
            items.append({
                "type": "etf_vs_nav", "ticker": ticker, "etf_name": label,
                "market_price": base.rnd(price), "nav": base.rnd(nav),
                "nav_date": str(nav_info["nav_date"]), "price_date": quote.get("date"),
                "premium_discount_pct": base.rnd(premium_pct, 4),
                "gross_spread_pct": base.rnd(gross, 4),
                "estimated_cost_pct": cost["round_trip_cost_pct"],
                "buy_on": "ETF" if premium_pct < 0 else "NAV (creation unit)",
                "sell_on": "NAV (redemption)" if premium_pct < 0 else "ETF",
                "expiry": "not applicable (open-ended ETF)",
                "liquidity_note": ("ETF arbitrage needs large creation-unit quantities "
                                   "and an authorised participant; retail size may not work"),
                "sources": {"nav": config.SOURCE_AMFI_OFFICIAL, "price": quote.get("source")},
                **verdict,
            })
        items.sort(key=lambda r: r["gross_spread_pct"] or 0, reverse=True)
        return {"available": True, "items": items, "nav_date": str(amfi["nav_date"].iloc[0])
                if len(amfi) else config.MISSING}

    # ------------------------------------------------------------------
    def run(self, stock_book=None, bse_quotes=None, include_futures: bool = True,
            include_etf: bool = True, network_budget_s: float = 10.0) -> dict:
        """Option-B final output: saare arbitrage opportunities.

        `network_budget_s` = external network checks ke liye time budget, taaki
        frontend ka RUN 30 second ke andar hi rahe. Budget khatam ho to saaf
        reason likha jaata hai (data invent nahi hota).
        """
        started = time.time()
        cost = self.cost_model()
        pairs = self.nse_vs_bse(stock_book, bse_quotes) or []
        skipped = {"available": False, "items": [],
                   "reason": ("time budget exceeded - external source not checked this run; "
                              "press RUN again to retry")}
        no_futures = {"available": False, "items": [], "reason": "skipped by user"}
        no_etf = {"available": False, "items": [], "reason": "skipped by user"}

        futures = no_futures
        etf = no_etf
        within_budget = (time.time() - started) < network_budget_s
        if within_budget and (include_futures or include_etf):
            # Dono external checks SAATH me chalao (threads) - time bache.
            results: dict[str, dict] = {}
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_map = {}
                if include_futures:
                    future_map[pool.submit(self.futures_vs_spot, stock_book)] = "futures"
                if include_etf:
                    future_map[pool.submit(self.etf_vs_nav)] = "etf"
                for fut, key in future_map.items():
                    try:
                        results[key] = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        results[key] = {"available": False, "items": [],
                                        "reason": f"check failed: {type(exc).__name__}: {exc}"}
            futures = results.get("futures", no_futures)
            etf = results.get("etf", no_etf)
        elif not within_budget:
            if include_futures:
                futures = dict(skipped, reason="time budget exceeded before the F&O check")
            if include_etf:
                etf = dict(skipped, reason="time budget exceeded before the AMFI check")

        all_items = pairs + (futures.get("items") or []) + (etf.get("items") or [])
        profitable = [i for i in all_items if i.get("profitable")]
        return {
            "agent": self.name,
            "available": bool(all_items),
            "mode": "arbitrage",
            "as_of": config.today_ist().isoformat(),
            "cost_model": cost,
            "nse_vs_bse": {"count": len(pairs), "items": pairs},
            "futures_vs_spot": futures,
            "etf_vs_nav": etf,
            "profitable_count": len(profitable),
            "not_profitable_count": len(all_items) - len(profitable),
            "top_opportunities": sorted(all_items, key=lambda r: r.get("net_spread_pct") or -999,
                                        reverse=True)[:10],
            "method_note": ("Spreads are computed from real quoted prices. Anything below "
                            f"{cost['min_required_spread_pct']}% is marked NOT PROFITABLE. "
                            "Negative net spread is never shown as profitable."),
            "disclaimer": base.DISCLAIMER,
            "disclaimer_hi": base.DISCLAIMER_HI,
        }
