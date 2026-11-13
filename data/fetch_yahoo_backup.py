"""data/fetch_yahoo_backup.py - BACKUP + CROSS-CHECK source (Yahoo Finance).

Zaroori baat (important):
  * Yahoo ka koi official public API nahi hai (2017 ke baad band ho gaya).
    Ye unofficial hai aur kabhi bhi toot sakta hai.
  * Isliye hum ise PRIMARY nahi maante. Har row me source="yahoo_backup"
    likha jaata hai, taaki user ko pata rahe.
  * Official NSE data se compare karte hain. 0.5% se zyada fark = MISMATCH flag.
    Data AUTO-CORRECT nahi hota.
"""
from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from app import config
from data import http_util
from data.http_util import log_event

YAHOO_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
CHART_PATH = "/v8/finance/chart/{ticker}"

# our symbol -> Yahoo ticker
INDEX_TICKERS: dict[str, str] = {
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "NIFTY100": "^CNX100",
    "MIDCAP50": "^NSEMDCP50",
}
# Yahoo par in indices ka bharosemand data nahi milta -> MISSING maana jaayega
UNAVAILABLE_TICKERS: dict[str, str] = {
    "MIDCAP150": "no reliable Yahoo ticker for NIFTY MIDCAP 150",
    "BSE100": "Yahoo ^BSE100 discontinued / unreliable",
    "BSEMIDCAP": "Yahoo ^BSEMID empty response",
}


def yahoo_session():
    return http_util.browser_session()


def _epoch(date_like) -> int:
    ts = pd.Timestamp(date_like)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def fetch_chart(ticker: str, start=None, end=None, interval: str = "1d",
                session=None) -> pd.DataFrame:
    """Yahoo chart API se ek ticker ka OHLC. Fail = khaali frame + log."""
    start = start or config.HISTORY_START_DATE
    end = end or (config.today_ist() + dt.timedelta(days=1))
    session = session or yahoo_session()
    params = {"period1": _epoch(start), "period2": _epoch(end), "interval": interval,
              "events": "div,split", "includeAdjustedClose": "true"}

    last_error = ""
    for host in YAHOO_HOSTS:
        url = host + CHART_PATH.format(ticker=ticker)
        try:
            resp = session.get(url, params=params, timeout=config.HTTP_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            continue
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            last_error = f"bad json: {type(exc).__name__}"
            continue
        result = ((payload.get("chart") or {}).get("result") or [None])[0]
        if not result:
            last_error = "empty chart.result"
            continue
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not stamps or not quote:
            last_error = "no timestamps/quotes"
            continue
        df = pd.DataFrame({
            "date": [dt.datetime.utcfromtimestamp(t).date().isoformat() for t in stamps],
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        })
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        if len(df) == 0:
            last_error = "all closes null"
            continue
        df["source"] = config.SOURCE_YAHOO_BACKUP
        log_event(config.SOURCE_YAHOO_BACKUP, url, "OK", 200,
                  f"{ticker}: rows={len(df)} {df['date'].min()}..{df['date'].max()}")
        return df.reset_index(drop=True)

    log_event(config.SOURCE_YAHOO_BACKUP, CHART_PATH.format(ticker=ticker), "FAILED",
              None, f"{ticker}: {last_error}")
    return pd.DataFrame()


def fetch_index_history(start=None, end=None, workers: int | None = None,
                        progress=None) -> dict:
    """Bade indices ka long history (NIFTY50, BANKNIFTY, SENSEX, NIFTY100)."""
    workers = workers or max(2, min(config.HTTP_WORKERS, 6))
    start = start or config.HISTORY_START_DATE
    out: dict[str, pd.DataFrame] = {}
    unavailable: dict[str, str] = dict(UNAVAILABLE_TICKERS)

    def _one(item):
        symbol, ticker = item
        return symbol, fetch_chart(ticker, start=start, end=end)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, it) for it in INDEX_TICKERS.items()]
        for fut in as_completed(futures):
            symbol, df = fut.result()
            if df is None or len(df) == 0:
                unavailable[symbol] = f"yahoo ticker {INDEX_TICKERS[symbol]} returned nothing"
                continue
            df = df.copy()
            df["symbol"] = symbol
            out[symbol] = df
            if progress:
                progress(f"yahoo index {symbol}: {len(df)} rows "
                         f"({df['date'].min()}..{df['date'].max()})")
    return {"frames": out, "unavailable": unavailable}


def fetch_stock_history(symbols, exchange: str = "NS", start=None, end=None,
                        workers: int | None = None, progress=None) -> dict:
    """Per-stock OHLC from Yahoo. exchange='NS' (NSE) ya 'BO' (BSE)."""
    workers = workers or config.HTTP_WORKERS
    start = start or config.HISTORY_START_DATE
    frames: dict[str, pd.DataFrame] = {}
    failed: dict[str, str] = {}
    symbols = list(symbols)

    def _one(sym):
        ticker = f"{sym}.{exchange}"
        return sym, fetch_chart(ticker, start=start, end=end)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, s) for s in symbols]
        done = 0
        for fut in as_completed(futures):
            sym, df = fut.result()
            done += 1
            if df is None or len(df) == 0:
                failed[sym] = f"no yahoo data for {sym}.{exchange}"
            else:
                df = df.copy()
                df["symbol"] = sym
                frames[sym] = df
            if progress and done % 25 == 0:
                progress(f"yahoo stocks .{exchange}: {done}/{len(symbols)} "
                         f"(ok={len(frames)}, failed={len(failed)})")
    return {"frames": frames, "failed": failed}


def fetch_spot_quotes(symbols, exchange: str = "NS") -> dict:
    """Aaj ke (latest) quotes - arbitrage ke liye. Range chhota, fast."""
    session = yahoo_session()
    quotes: dict[str, dict] = {}
    for sym in symbols:
        df = fetch_chart(f"{sym}.{exchange}", start=config.today_ist() - dt.timedelta(days=12),
                         end=config.today_ist() + dt.timedelta(days=1), session=session)
        if df is None or len(df) == 0:
            quotes[sym] = {"available": False, "source": config.SOURCE_YAHOO_BACKUP,
                           "reason": "no quote returned"}
            continue
        last = df.iloc[-1]
        quotes[sym] = {
            "available": True, "exchange": exchange, "date": str(last["date"]),
            "close": float(last["close"]), "open": config.json_safe(float(last["open"])),
            "high": config.json_safe(float(last["high"])),
            "low": config.json_safe(float(last["low"])),
            "source": config.SOURCE_YAHOO_BACKUP,
        }
        time.sleep(0.05)
    return quotes


def yfinance_backup_download(ticker: str, start=None, end=None) -> pd.DataFrame:
    """Second-level backup: yfinance library (agar Yahoo chart API fail ho).

    Cache project folder me hi jaata hai (.cache/yfinance), C: par nahi.
    """
    try:
        import yfinance as yf  # noqa: PLC0415

        try:
            yf.set_tz_cache_location(str(config.CACHE_DIR / "yfinance"))
        except Exception:  # noqa: BLE001
            pass
        raw = yf.download(ticker, start=start or config.HISTORY_START_DATE,
                          end=end, progress=False, auto_adjust=False)
        if raw is None or len(raw) == 0:
            return pd.DataFrame()
        raw = raw.reset_index()
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = [c[0] for c in raw.columns]
        df = raw.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                 "Low": "low", "Close": "close", "Volume": "volume"})
        keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep]
        df["source"] = config.SOURCE_YAHOO_BACKUP
        log_event(config.SOURCE_YAHOO_BACKUP, f"yfinance:{ticker}", "OK", 200,
                  f"rows={len(df)}")
        return df
    except Exception as exc:  # noqa: BLE001
        log_event(config.SOURCE_YAHOO_BACKUP, f"yfinance:{ticker}", "FAILED", None,
                  f"{type(exc).__name__}: {str(exc)[:150]}")
        return pd.DataFrame()
