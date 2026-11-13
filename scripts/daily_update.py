"""scripts/daily_update.py - ROZ KA AUTO UPDATE (idempotent).

Ye script roz chalegi (Windows Task Scheduler se 6:30 PM IST):
  1) Kal/ aaj ka official NSE index file + bhavcopy laayegi
  2) Parquet me APPEND karegi (duplicate nahi banega - idempotent)
  3) Ephemeris ke naye din calculate karegi
  4) state/project_state.json update karegi
  5) logs/daily_run.jsonl me ek line likhegi

Do baar chalao -> koi duplicate nahi hoga. Ye important hai.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from app import config, state_manager  # noqa: E402
from data import fetch_ephemeris, fetch_nse, normalize  # noqa: E402


def _last_date(dataset: str) -> str | None:
    df = normalize.read_parquet(normalize.processed_path(dataset))
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    return str(df["date"].astype(str).max())


def _append_daily_log(record: dict) -> None:
    path = config.DAILY_RUN_LOG
    config.assert_not_c_drive(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(config.json_safe(record), ensure_ascii=False) + "\n")


def _index_rows_from_raw(raw_idx: pd.DataFrame, date_str: str) -> list[dict]:
    names = raw_idx["index_name"].astype(str).str.strip().str.lower()
    tagged = raw_idx.assign(_key=names.map(fetch_nse.INDEX_NAME_MAP))
    wanted = tagged[tagged["_key"].isin(fetch_nse.OFFICIAL_INDEX_SYMBOLS)]
    rows: list[dict] = []
    for _, row in wanted.iterrows():
        rows.append({"date": date_str, "symbol": row["_key"],
                     "open": row.get("open"), "high": row.get("high"),
                     "low": row.get("low"), "close": row.get("close"),
                     "volume": row.get("volume"),
                     "source": config.SOURCE_NSE_OFFICIAL})
    return rows


def _equity_rows_from_raw(raw_eq: pd.DataFrame, date_str: str,
                          symbols: set[str]) -> list[dict]:
    work = raw_eq.copy()
    if "SERIES" in work.columns:
        work = work[work["SERIES"].astype(str).str.upper().str.strip() == "EQ"]
    work = work.assign(sym=work["SYMBOL"].astype(str).str.upper().str.strip())
    if symbols:
        work = work[work["sym"].isin(symbols)]
    rows: list[dict] = []
    for _, row in work.iterrows():
        rows.append({"date": date_str, "symbol": row["sym"],
                     "open": row.get("OPEN"), "high": row.get("HIGH"),
                     "low": row.get("LOW"), "close": row.get("CLOSE"),
                     "volume": row.get("TOTTRDQTY"),
                     "source": config.SOURCE_NSE_OFFICIAL})
    return rows


def update_stock(days_back: int = 7, progress=print) -> dict:
    """Naye dinon ka official stock data append karo (idempotent)."""
    from db import database

    today = config.today_ist()
    last_index = _last_date("nifty50")
    last_stock = _last_date("nifty100_stocks")

    cons = normalize.read_parquet(normalize.processed_path("constituents"))
    symbols: set[str] = set()
    if cons is not None and len(cons):
        row = cons[cons["index_key"].astype(str) == "NIFTY100"]
        symbols = set(row["symbol"].astype(str).str.upper())

    session = fetch_nse.http_util.browser_session(referer=fetch_nse.NIFTYINDICES_HOST + "/")
    index_rows: list[dict] = []
    equity_rows: list[dict] = []
    open_dates: list[str] = []
    checked = 0

    for day in fetch_nse.candidate_trading_dates(today - dt.timedelta(days=days_back), today):
        date_str = day.isoformat()
        checked += 1
        already = (last_index is not None and last_stock is not None
                   and date_str <= last_index and date_str <= last_stock)
        if already:
            continue
        raw_idx = fetch_nse.fetch_index_file(session, day)
        if raw_idx is not None and len(raw_idx):
            open_dates.append(date_str)
            index_rows.extend(_index_rows_from_raw(raw_idx, date_str))
        raw_eq = fetch_nse.fetch_equity_bhavcopy(session, day)
        if raw_eq is not None and len(raw_eq):
            equity_rows.extend(_equity_rows_from_raw(raw_eq, date_str, symbols))
        progress(f"checked {date_str}: index_rows={len(index_rows)} "
                 f"equity_rows={len(equity_rows)}")

    written: dict[str, int] = {}
    if index_rows:
        idx_df = pd.DataFrame(index_rows)
        for symbol, dataset in fetch_nse.INDEX_DATASET.items():
            sub = idx_df[idx_df["symbol"] == symbol]
            if not len(sub):
                continue
            before_df = normalize.read_parquet(normalize.processed_path(dataset))
            path = normalize.write_dataset(dataset, sub)
            after_df = normalize.read_parquet(path)
            written[dataset] = int(len(after_df) - (0 if before_df is None else len(before_df)))
    if equity_rows:
        before_df = normalize.read_parquet(normalize.processed_path("nifty100_stocks"))
        path = normalize.write_dataset("nifty100_stocks", pd.DataFrame(equity_rows))
        after_df = normalize.read_parquet(path)
        written["nifty100_stocks"] = int(len(after_df) - (0 if before_df is None else len(before_df)))
    if open_dates:
        database.upsert_trading_calendar([(d, "NSE", 1, config.SOURCE_NSE_OFFICIAL)
                                          for d in open_dates])
    return {"days_checked": checked, "rows_added": written,
            "last_stock_date": _last_date("nifty100_stocks"),
            "last_index_date": _last_date("nifty50")}



def update_ephemeris(progress=print) -> dict:
    """Ephemeris ke naye din add karo (idempotent - merge on date+body)."""
    last = _last_date("ephemeris_daily")
    today = config.today_ist()
    if last is not None and last >= today.isoformat():
        progress(f"ephemeris already up to {last}")
        return {"added_days": 0, "last_ephemeris_date": last}
    start = ((dt.date.fromisoformat(last) + dt.timedelta(days=1)) if last
             else dt.date.fromisoformat(config.EPHEMERIS_START_DATE))
    built = fetch_ephemeris.build_ephemeris(start, today, progress=progress)
    fetch_ephemeris.save_ephemeris(built)
    return {"added_days": (today - start).days + 1,
            "last_ephemeris_date": _last_date("ephemeris_daily"),
            "rows": int(len(built["ephemeris"])), "aspects": int(len(built["aspects"])),
            "events": int(len(built["events"]))}


def run_daily_update(days_back: int = 7, progress=print) -> dict:
    """Poora daily update - safe to run any number of times (idempotent)."""
    config.bootstrap_environment()
    from db import database

    database.init_db()
    progress(f"daily update start (project root {config.PROJECT_ROOT})")
    before = config.disk_report()

    stock = update_stock(days_back=days_back, progress=progress)
    # Evaluate only forecasts whose future observed close now exists.
    from app.agents import base as agent_base
    from db import database
    ledger = database.evaluate_prediction_ledger(agent_base.load_stock_book())
    eph = update_ephemeris(progress=progress)

    state = state_manager.load_state()
    if stock.get("last_stock_date"):
        state["last_data_date_stock"] = stock["last_stock_date"]
    if eph.get("last_ephemeris_date"):
        state["last_data_date_ephemeris"] = eph["last_ephemeris_date"]
    state["last_run_date"] = config.today_ist().isoformat()
    state_manager.save_state(state)

    record = {"ist": config.now_ist().isoformat(timespec="seconds"),
              "stock": stock, "ephemeris": eph, "prediction_ledger": ledger,
              "disk_before": before, "disk_after": config.disk_report()}
    _append_daily_log(record)
    progress(f"daily update done: {json.dumps(config.json_safe(record), default=str)[:400]}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily incremental update (idempotent).")
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    out = run_daily_update(days_back=args.days_back,
                           progress=(lambda _m: None) if args.quiet else print)
    print(json.dumps(config.json_safe(out), indent=2, default=str)[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
