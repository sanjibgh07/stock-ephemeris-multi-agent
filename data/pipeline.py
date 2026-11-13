"""data/pipeline.py - poora data book banane wala main flow.

Yahi file sab kuch jodti hai:
  1) Official NSE constituent list
  2) Official NSE index daily files  -> nse_official
  3) Official NSE bhavcopy           -> nse_official (per stock)
  4) Yahoo backup (2000 se)          -> yahoo_backup (saaf label)
  5) Trading calendar (jo din asli file aayi = market open)
  6) Missing day detection + cross-check (NSE vs Yahoo, 0.5%)

Har step idempotent hai: dobara chalao to duplicate nahi hoga.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app import config, state_manager
from data import fetch_bse, fetch_nse, fetch_yahoo_backup, normalize

# our index symbol -> parquet dataset name (spec ke naam)
INDEX_DATASET = {
    "NIFTY50": "nifty50",
    "BANKNIFTY": "banknifty",
    "SENSEX": "sensex",
    "MIDCAP150": "midcap",
    "NIFTY100": "nifty100_index",
}
OFFICIAL_INDEX_SYMBOLS = ("NIFTY50", "BANKNIFTY", "NIFTY100", "MIDCAP150")
YAHOO_PRIORITY_FIRST = ("SENSEX", "MIDCAP150")   # jinka official nahi milta


def _noop(_msg: str) -> None:
    return None


def combine_sources(frames_low_to_high: list[pd.DataFrame]) -> pd.DataFrame:
    """Kam priority pehle, zyada priority baad me -> official data jeetta hai."""
    frames = [f for f in frames_low_to_high if f is not None and len(f) > 0]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    sort_cols = [c for c in ("date", "symbol") if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols)
    keys = [k for k in ("date", "symbol") if k in merged.columns] or ["date"]
    return merged.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)


def step_constituents(progress=_noop) -> dict:
    """Official constituent lists download + symbols table fill."""
    progress("step 1/7: official NSE constituent lists ...")
    lists = fetch_nse.download_constituents()
    frames = []
    for key, df in lists.items():
        if df is None or len(df) == 0:
            continue
        keep = df.copy()
        keep["index_key"] = key
        keep["date"] = config.today_ist().isoformat()   # snapshot date
        frames.append(keep)
        try:
            from db import database  # noqa: PLC0415

            for _, row in df.iterrows():
                database.upsert_symbol(
                    row.get("symbol"), "NSE", "equity", row.get("company_name"),
                    row.get("isin"), row.get("industry"), row.get("series"), None,
                    config.SOURCE_NSE_OFFICIAL)
        except Exception:  # noqa: BLE001
            pass
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        path = normalize.write_dataset("constituents", combined,
                                       keys=("index_key", "symbol"), register=True)
        progress(f"  constituents saved: {len(combined)} rows -> {path.name}")
        return {"rows": int(len(combined)), "lists": list(lists.keys()),
                "nifty100_symbols": sorted(
                    lists.get("NIFTY100", pd.DataFrame()).get("symbol", pd.Series(dtype=str))
                    .astype(str).tolist())}
    progress("  WARNING: no constituent list downloaded (logged, not invented)")
    return {"rows": 0, "lists": [], "nifty100_symbols": []}


def step_official_indices(start_year: int | None, end: dt.date, progress=_noop) -> dict:
    """Official NSE index daily files se index history."""
    start_year = start_year or config.OFFICIAL_BACKFILL_START_YEAR
    start = dt.date(start_year, 1, 1)
    progress(f"step 2/7: official NSE index files {start} -> {end} ...")
    result = fetch_nse.backfill_indices(start, end, wanted=OFFICIAL_INDEX_SYMBOLS,
                                        progress=progress)
    return result


def step_official_equities(symbols: set[str], end: dt.date, recent_days: int,
                           progress=_noop) -> dict:
    """Official NSE bhavcopy se per-stock OHLC (recent window)."""
    start = end - dt.timedelta(days=recent_days)
    progress(f"step 3/7: official NSE bhavcopy {start} -> {end} "
             f"({len(symbols)} symbols) ...")
    return fetch_nse.backfill_equities(start, end, symbols=symbols or None,
                                       progress=progress)


def step_bse_official(start: dt.date, end: dt.date, progress=_noop) -> dict:
    """Official BSE try karo. Blocked ho to honest log (Yahoo fallback aayega)."""
    progress("step 4/7: official BSE attempt (Sensex archive) ...")
    status = fetch_bse.official_status()
    frames: dict[str, pd.DataFrame] = {}
    if status.get("available"):
        df = fetch_bse.fetch_bse_index_history("SENSEX", start, end)
        if df is not None and len(df):
            frames["SENSEX"] = df
            progress(f"  official BSE SENSEX: {len(df)} rows")
    if not frames:
        # Lock file me SACCH likho (spec requirement) - data invent nahi karte.
        state_manager.record_source_failure(
            config.SOURCE_BSE_OFFICIAL, fetch_bse.BSE_INDEX_ARCH_URL,
            "BLOCKED" if not status.get("reachable") else "EMPTY",
            str(status.get("note")) + " No BSE number is ever invented.")
        progress(f"  official BSE not usable ({status.get('note')}); "
                 f"Sensex uses the labelled yahoo_backup source")
    return {"frames": frames, "status": status}


def step_yahoo_indices(start: dt.date, end: dt.date, progress=_noop) -> dict:
    progress("step 5/7: Yahoo backup index history (labelled yahoo_backup) ...")
    return fetch_yahoo_backup.fetch_index_history(start=start, end=end, progress=progress)


def step_yahoo_stocks(symbols: list[str], start: dt.date, end: dt.date,
                      progress=_noop) -> dict:
    progress(f"step 6/7: Yahoo backup stocks ({len(symbols)} symbols, .NS + .BO) ...")
    nse = fetch_yahoo_backup.fetch_stock_history(symbols, "NS", start=start, end=end,
                                                 progress=progress)
    bse = fetch_yahoo_backup.fetch_stock_history(symbols, "BO", start=start, end=end,
                                                 progress=progress)
    return {"nse": nse, "bse": bse}


def _index_frames_to_datasets(official: pd.DataFrame, yahoo_frames: dict,
                              bse_frames: dict, progress=_noop) -> dict:
    """Har index ka parquet likho: kam priority backup, zyada priority official."""
    written: dict[str, dict] = {}
    official = official if official is not None else pd.DataFrame()
    for symbol, dataset in INDEX_DATASET.items():
        parts: list[pd.DataFrame] = []
        if symbol in yahoo_frames:
            parts.append(yahoo_frames[symbol])
        if symbol in bse_frames:
            parts.append(bse_frames[symbol])
        if len(official):
            sub = official[official["symbol"] == symbol]
            if len(sub):
                parts.append(sub)
        if not parts:
            progress(f"  {symbol}: NO DATA from any source -> stays MISSING (not invented)")
            continue
        combined = combine_sources(parts)
        path = normalize.write_dataset(dataset, combined)
        written[dataset] = {
            "rows": int(len(combined)), "file": path.name,
            "first": str(combined["date"].min()), "last": str(combined["date"].max()),
            "sources": sorted(set(combined["source"].astype(str))),
        }
        progress(f"  {dataset}: {len(combined)} rows "
                 f"({combined['date'].min()}..{combined['date'].max()}) "
                 f"sources={sorted(set(combined['source'].astype(str)))}")
    return written


def build_stock_book(official_start_year: int | None = None,
                     recent_official_days: int = 400,
                     use_official_indices: bool = True,
                     progress=_noop) -> dict:
    """Poora Stock Data Book banao (2000-01-01 -> today)."""
    from db import database  # noqa: PLC0415

    config.bootstrap_environment()
    database.init_db()
    end = config.today_ist()
    start = dt.date.fromisoformat(config.HISTORY_START_DATE)

    cons = step_constituents(progress)
    symbols = [s for s in cons.get("nifty100_symbols", []) if s and s != "MISSING"]
    if not symbols:
        progress("WARNING: NIFTY100 constituent list empty - stock book limited to indices")
    symbol_set = set(symbols)

    if use_official_indices:
        official = step_official_indices(official_start_year, end, progress)
    else:
        official = {"frame": pd.DataFrame(), "market_open_dates": [],
                    "files_ok": 0, "files_missing": 0, "days_tried": 0}

    equities = step_official_equities(symbol_set, end, recent_official_days, progress)
    bse = step_bse_official(start, end, progress)
    yahoo_idx = step_yahoo_indices(start, end, progress)
    yahoo_stocks = step_yahoo_stocks(symbols, start, end, progress)

    progress("step 7/7: writing datasets ...")
    index_written = _index_frames_to_datasets(official.get("frame"), yahoo_idx["frames"],
                                             bse["frames"], progress)

    # ---- stock book (Nifty-100 universe): official + yahoo backup -------------
    stock_parts: list[pd.DataFrame] = []
    for sym, df in (yahoo_stocks["nse"]["frames"] or {}).items():
        stock_parts.append(df)
    if equities.get("frame") is not None and len(equities["frame"]):
        stock_parts.append(equities["frame"])
    if stock_parts:
        stock_combined = combine_sources(stock_parts)
        path = normalize.write_dataset("nifty100_stocks", stock_combined)
        progress(f"  nifty100_stocks: {len(stock_combined)} rows, "
                 f"{stock_combined['symbol'].nunique()} symbols -> {path.name}")

    # ---- arbitrage quotes: NSE (.NS) vs BSE (.BO) ----------------------------
    quote_parts: list[pd.DataFrame] = []
    for sym, df in (yahoo_stocks["bse"]["frames"] or {}).items():
        tag = df.copy()
        tag["symbol"] = f"{sym}.BO"
        quote_parts.append(tag)
    if quote_parts:
        arb_df = pd.concat(quote_parts, ignore_index=True)
        path = normalize.write_dataset("arbitrage_quotes", arb_df)
        progress(f"  arbitrage_quotes (BSE .BO): {len(arb_df)} rows -> {path.name}")

    # ---- trading calendar + gaps --------------------------------------------
    open_dates = sorted(set(official.get("market_open_dates", []))
                        | set(equities.get("market_open_dates", [])))
    if open_dates:
        database.upsert_trading_calendar([(d, "NSE", 1, config.SOURCE_NSE_OFFICIAL)
                                          for d in open_dates])
    gap_count = _record_gaps(open_dates, progress)

    # ---- cross-check NSE official vs Yahoo ----------------------------------
    xchecks = _cross_check_official_vs_yahoo(equities.get("frame"),
                                             yahoo_stocks["nse"]["frames"],
                                             progress)

    summary = {
        "end_date": end.isoformat(),
        "constituents": {"rows": cons.get("rows"), "lists": cons.get("lists")},
        "index_datasets": index_written,
        "official_index_files_ok": official.get("files_ok"),
        "official_index_files_missing": official.get("files_missing"),
        "official_equity_files_ok": equities.get("files_ok"),
        "bse_official": bse.get("status"),
        "yahoo_index_unavailable": yahoo_idx.get("unavailable"),
        "yahoo_stock_failures": {
            "nse": len(yahoo_stocks["nse"]["failed"]),
            "bse": len(yahoo_stocks["bse"]["failed"]),
        },
        "market_open_dates": len(open_dates),
        "missing_dates_recorded": gap_count,
        "cross_checks": xchecks,
        "disk": config.disk_report(),
        "stock_symbols": len(symbols),
    }
    _update_state_after_stock_build(summary)
    return summary


def _record_gaps(open_dates: list[str], progress=_noop) -> int:
    """Market-open tha par data nahi -> missing_dates (never filled with fake)."""
    from db import database  # noqa: PLC0415

    if not open_dates:
        return 0
    stock = normalize.read_parquet(normalize.processed_path("nifty100_stocks"))
    nifty = normalize.read_parquet(normalize.processed_path("nifty50"))
    total = 0
    for dataset, df, symbol in (("nifty50", nifty, "NIFTY50"),
                                ("nifty100_stocks", stock, None)):
        if df is None or len(df) == 0:
            continue
        if symbol:
            gaps = normalize.find_missing_trading_days(df, symbol, open_dates)
            total += database.record_gaps(dataset, gaps)
        else:
            for sym, grp in df.groupby("symbol"):
                first = str(grp["date"].min())
                gaps = normalize.find_missing_trading_days(
                    grp, str(sym), [d for d in open_dates if d >= first])
                total += database.record_gaps(dataset, gaps[:400])
    state = state_manager.load_state()
    state["missing_dates"] = (state.get("missing_dates", []) + [
        {"dataset": "nifty50/nifty100_stocks", "count": total,
         "note": "market was open but this source had no row - kept as MISSING"}])[-20:]
    state_manager.save_state(state)
    progress(f"  missing trading days recorded: {total}")
    return total


def _cross_check_official_vs_yahoo(official_df, yahoo_frames, progress=_noop) -> dict:
    """Official NSE vs Yahoo backup compare (>0.5% = MISMATCH, no auto-correct)."""
    from db import database  # noqa: PLC0415

    results: dict[str, dict] = {}
    if official_df is None or len(official_df) == 0 or not yahoo_frames:
        progress("  cross-check skipped (no official equity rows)")
        return {"skipped": "no overlapping official data in this run"}
    symbols = sorted(set(official_df["symbol"]))[:20]
    flagged_total = 0
    for sym in symbols:
        off = official_df[official_df["symbol"] == sym]
        back = yahoo_frames.get(sym)
        if back is None or len(back) == 0:
            continue
        res = normalize.cross_check(off, back, sym, tolerance_pct=0.5)
        results[sym] = {"compared": res["compared"], "max_diff_pct": res["max_diff_pct"],
                        "mismatches": len(res["mismatches"])}
        for mm in res["mismatches"]:
            database.record_mismatch(sym, mm["date"], mm["primary_close"], mm["backup_close"],
                                     mm["diff_pct"], mm["primary_source"], mm["backup_source"])
            flagged_total += 1
    state = state_manager.load_state()
    state["mismatches"] = (state.get("mismatches", []) + [
        {"checked_symbols": len(results), "flagged": flagged_total, "tolerance_pct": 0.5,
         "note": "NSE official vs yahoo_backup; flagged only, no auto-correct"}])[-20:]
    state_manager.save_state(state)
    progress(f"  cross-check: {len(results)} symbols compared, {flagged_total} flagged (>0.5%)")
    return results


def _update_state_after_stock_build(summary: dict) -> None:
    """Stock book banane ke baad state file update karo."""
    state = state_manager.load_state()
    last_dates = [v.get("last") for v in (summary.get("index_datasets") or {}).values()]
    last_dates = [d for d in last_dates if d]
    if last_dates:
        state["last_data_date_stock"] = max(last_dates)
    state["data_coverage"]["stock"] = summary.get("index_datasets")
    state["row_counts"]["stock_total"] = sum(
        int(v.get("rows", 0)) for v in (summary.get("index_datasets") or {}).values())
    state["last_run_date"] = config.today_ist().isoformat()
    state_manager.save_state(state)


def build_ephemeris_book(start: dt.date | None = None, end: dt.date | None = None,
                         progress=_noop) -> dict:
    """Poora Ephemeris Book banao aur save karo."""
    from data import fetch_ephemeris  # noqa: PLC0415
    from db import database  # noqa: PLC0415

    database.init_db()
    progress("ephemeris book: calculating ...")
    built = fetch_ephemeris.build_ephemeris(start, end, progress=progress)
    written = fetch_ephemeris.save_ephemeris(built)
    eph = built["ephemeris"]
    state = state_manager.load_state()
    state["last_data_date_ephemeris"] = str(eph["date"].max()) if len(eph) else None
    state["data_coverage"]["ephemeris"] = {
        "rows": int(len(eph)), "aspects": int(len(built["aspects"])),
        "events": int(len(built["events"])), "backend": built.get("backend"),
        "eclipse_method": built.get("eclipse_method")}
    state["row_counts"]["ephemeris_total"] = int(len(eph))
    state["last_run_date"] = config.today_ist().isoformat()
    state_manager.save_state(state)
    progress(f"ephemeris book saved: {written}")
    return {"written": written, "rows": int(len(eph)), "aspects": int(len(built["aspects"])),
            "events": int(len(built["events"])), "backend": built.get("backend")}


def build_all(official_start_year: int | None = None, recent_official_days: int = 400,
              skip_official_indices: bool = False, skip_ephemeris: bool = False,
              progress=_noop) -> dict:
    """Sab kuch ek saath: stock book + ephemeris book."""
    config.bootstrap_environment()
    out = {"resume_message": state_manager.resume_message(), "disk_before": config.disk_report()}
    out["stock_book"] = build_stock_book(
        official_start_year=official_start_year,
        recent_official_days=recent_official_days,
        use_official_indices=not skip_official_indices, progress=progress)
    if not skip_ephemeris:
        out["ephemeris_book"] = build_ephemeris_book(progress=progress)
    state_manager.mark_phase_done(
        "PHASE_1_STOCK_DATA_BOOK",
        note="stock data book built from official NSE archives + labelled Yahoo backup")
    state_manager.mark_phase_done(
        "PHASE_2_EPHEMERIS_BOOK",
        note="ephemeris book built with Swiss Ephemeris (tropical + Lahiri sidereal)")
    out["disk_after"] = config.disk_report()
    out["state"] = state_manager.load_state()
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415
    import json  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Build the Stock + Ephemeris books.")
    parser.add_argument("--official-start-year", type=int, default=None)
    parser.add_argument("--recent-official-days", type=int, default=400)
    parser.add_argument("--skip-official-indices", action="store_true")
    parser.add_argument("--skip-ephemeris", action="store_true")
    args = parser.parse_args(argv)

    def _p(msg: str) -> None:
        print(f"[{config.now_ist().strftime('%H:%M:%S')}] {msg}", flush=True)

    result = build_all(official_start_year=args.official_start_year,
                       recent_official_days=args.recent_official_days,
                       skip_official_indices=args.skip_official_indices,
                       skip_ephemeris=args.skip_ephemeris, progress=_p)
    print("\n==== BUILD SUMMARY ====")
    print(json.dumps(result, indent=2, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
