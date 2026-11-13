"""data/validate_data.py - PHASE 5 validation report.

Ye module SACHCHAI check karta hai (koi number invent nahi):
  1) Data completeness - kitne rows, kaun sa din missing
  2) No-hallucination - hamara stored data vs live source (0.5% tolerance)
  3) Ephemeris accuracy - Sun longitude ~280 deg on 2024-01-01
  4) Retrograde sanity - known Mercury retrograde dates
  5) Source health - kaun sa source fail hua (logs se)

Report: logs/validation_report.json (+ console print)
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from app import config, state_manager
from data import normalize

# Known (published) Mercury retrograde windows - validation reference only.
# Ye astronomy/astrology reference dates hain, hamara data nahi.
MERCURY_RETRO_REFERENCE = [
    ("2023-12-20", True), ("2024-01-01", True), ("2024-01-15", False),
    ("2024-04-10", True), ("2024-05-10", False), ("2024-08-10", True),
    ("2024-09-10", False), ("2024-12-01", True), ("2025-01-10", False),
    ("2020-02-25", True), ("2020-04-01", False), ("2018-03-30", True),
]

KNOWN_CHECK_DATES = ["2008-01-21", "2020-03-23", "2021-02-01", "2024-01-01", "2026-06-01"]


def completeness_report() -> dict:
    """Har dataset ka row count, date range aur trading-day gaps."""
    out: dict = {"datasets": {}, "notes": []}
    for name in ("nifty50", "banknifty", "sensex", "midcap", "nifty100_index",
                 "nifty100_stocks", "arbitrage_quotes", "constituents",
                 "ephemeris_daily", "aspects_daily", "events_daily"):
        try:
            df = normalize.read_parquet(normalize.processed_path(name))
        except Exception:  # noqa: BLE001
            continue
        if df is None or len(df) == 0:
            out["datasets"][name] = {"rows": 0, "status": "MISSING"}
            continue
        dates = pd.to_datetime(df["date"], errors="coerce")
        entry = {
            "rows": int(len(df)),
            "first_date": dates.min().date().isoformat() if dates.notna().any() else None,
            "last_date": dates.max().date().isoformat() if dates.notna().any() else None,
            "distinct_dates": int(dates.dt.date.nunique()),
            "symbols": int(df["symbol"].nunique()) if "symbol" in df.columns else None,
            "sources": sorted({str(s) for s in df.get("source", pd.Series(dtype=str))
                               .dropna().unique()}),
            "status": "OK",
        }
        out["datasets"][name] = entry

    # trading-day count for the deepest series
    sensex = out["datasets"].get("sensex", {})
    nifty = out["datasets"].get("nifty50", {})
    out["trading_day_counts"] = {
        "sensex_distinct_dates": sensex.get("distinct_dates"),
        "nifty50_distinct_dates": nifty.get("distinct_dates"),
    }
    if (nifty.get("first_date") or "") > "2007-09-17":
        out["notes"].append(
            "NIFTY-50 index history before 2007-09-17 is not available from any source "
            "reachable from this network: the official NSE ind_close_all archive starts "
            "2013 and the Yahoo ^NSEI series starts 2007-09-17. This gap is reported, "
            "not filled with invented numbers. SENSEX and individual stocks do have "
            "2000-01-03 onwards.")
    return out



def no_hallucination_report(check_dates: list[str] | None = None,
                            tolerance_pct: float = 0.5) -> dict:
    """Stored data vs a LIVE Yahoo fetch - mismatch ho to saaf batao."""
    from data import fetch_yahoo_backup as yb

    check_dates = check_dates or KNOWN_CHECK_DATES
    result: dict = {"tolerance_pct": tolerance_pct, "checks": [], "live_fetch_ok": True}
    stored = normalize.read_parquet(normalize.processed_path("sensex"))
    stored_idx = normalize.read_parquet(normalize.processed_path("nifty50"))

    for name, df, ticker in (("sensex", stored, "^BSESN"),
                             ("nifty50", stored_idx, "^NSEI")):
        if df is None or len(df) == 0:
            result["checks"].append({"dataset": name, "status": "MISSING"})
            continue
        work = df.assign(d=df["date"].astype(str))
        for date_str in check_dates:
            row = work[work["d"] == date_str]
            if len(row) == 0:
                result["checks"].append({"dataset": name, "date": date_str,
                                         "status": "not_in_dataset"})
                continue
            stored_close = float(row["close"].iloc[0])
            live = yb.fetch_chart(ticker, start=date_str,
                                  end=(dt.date.fromisoformat(date_str) +
                                       dt.timedelta(days=1)).isoformat())
            if live is None or len(live) == 0:
                result["live_fetch_ok"] = False
                result["checks"].append({"dataset": name, "date": date_str,
                                         "stored_close": stored_close,
                                         "status": "live_fetch_unavailable"})
                continue
            live_close = float(live["close"].iloc[0])
            diff = abs(stored_close - live_close) / live_close * 100 if live_close else None
            result["checks"].append({
                "dataset": name, "date": date_str, "ticker": ticker,
                "stored_close": round(stored_close, 4), "live_close": round(live_close, 4),
                "diff_pct": round(diff, 4) if diff is not None else None,
                "status": ("MATCH" if diff is not None and diff <= tolerance_pct
                           else "MISMATCH"),
            })
    bad = [c for c in result["checks"] if c.get("status") == "MISMATCH"]
    result["mismatch_count"] = len(bad)
    result["verdict"] = ("all comparable values matched within tolerance"
                         if not bad else f"{len(bad)} MISMATCH(es) flagged")
    return result


def ephemeris_report() -> dict:
    """Ephemeris accuracy + retrograde sanity."""
    from data import fetch_ephemeris as fe

    out: dict = {"accuracy": fe.validate_sun_longitude(dt.date(2024, 1, 1))}
    eph = normalize.read_parquet(normalize.processed_path("ephemeris_daily"))
    if eph is None or len(eph) == 0:
        out["stored_ephemeris"] = "MISSING"
        return out
    stored = eph.assign(d=eph["date"].astype(str))
    sun = stored[(stored["d"] == "2024-01-01") & (stored["body"] == "Sun")]
    if len(sun):
        out["stored_sun_longitude_2024_01_01"] = float(sun["longitude_deg"].iloc[0])
        out["stored_matches_live_calc"] = (
            abs(out["stored_sun_longitude_2024_01_01"] -
                out["accuracy"]["sun_longitude_deg"]) < 0.1)

    merc = stored[stored["body"] == "Mercury"][["d", "is_retrograde"]]
    retro_map = dict(zip(merc["d"], merc["is_retrograde"].astype(bool)))
    checks = []
    for date_str, expected in MERCURY_RETRO_REFERENCE:
        if date_str not in retro_map:
            continue
        got = bool(retro_map[date_str])
        checks.append({"date": date_str, "expected_retrograde": expected,
                       "computed_retrograde": got, "pass": got == expected})
    out["mercury_retrograde_checks"] = checks
    out["mercury_checks_passed"] = sum(1 for c in checks if c["pass"])
    out["mercury_checks_total"] = len(checks)
    return out



def source_health_report() -> dict:
    """Kaun sa source fail hua (logs + state se) - imaandaar report."""
    from db import database

    out: dict = {"status_counts": {}, "recent_failures": []}
    try:
        conn = database.get_connection()
        try:
            for row in conn.execute(
                    "SELECT status, COUNT(*) AS c FROM source_log GROUP BY status"):
                out["status_counts"][row["status"]] = row["c"]
            out["recent_failures"] = [dict(r) for r in conn.execute(
                "SELECT logged_ist, source, status, http_status,"
                " substr(message,1,160) AS message FROM source_log"
                " WHERE status IN ('FAILED','BLOCKED','EMPTY') ORDER BY id DESC LIMIT 12"
            ).fetchall()]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    out["state_source_failures"] = state_manager.load_state().get("source_failures", [])
    return out


def gaps_report(limit: int = 25) -> dict:
    """Missing trading days (khaali din) - jo asli me MISSING hain."""
    from db import database

    out: dict = {"missing_dates_in_state":
                 state_manager.load_state().get("missing_dates", [])}
    try:
        conn = database.get_connection()
        try:
            out["gap_total"] = conn.execute(
                "SELECT COUNT(*) c FROM data_gaps").fetchone()["c"]
            out["gap_samples"] = [dict(r) for r in conn.execute(
                "SELECT dataset_name, symbol, gap_date, reason FROM data_gaps"
                " ORDER BY gap_date DESC LIMIT ?", (limit,)).fetchall()]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def build_report(quick: bool = False) -> dict:
    """Poora validation report banao aur logs/validation_report.json me likho."""
    config.bootstrap_environment()
    report = {
        "generated_ist": config.now_ist().isoformat(timespec="seconds"),
        "project_root": str(config.PROJECT_ROOT),
        "disk": config.disk_report(),
        "completeness": completeness_report(),
        "ephemeris": ephemeris_report(),
        "source_health": source_health_report(),
        "gaps": gaps_report(),
    }
    if not quick:
        report["no_hallucination"] = no_hallucination_report()
    path = config.LOGS_DIR / "validation_report.json"
    config.assert_not_c_drive(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.json_safe(report), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    report["report_file"] = str(path)
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate the data books.")
    parser.add_argument("--quick", action="store_true", help="skip the live cross-check")
    args = parser.parse_args()
    report = build_report(quick=args.quick)
    print(json.dumps(config.json_safe(report), indent=2, ensure_ascii=False)[:6000])
    print("\nFull report written to:", report.get("report_file"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
