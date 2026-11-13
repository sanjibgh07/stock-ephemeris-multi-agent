"""app/agents/base.py - common helpers for all agents.

Simple English: saare agents yahi se data padhte hain aur yahi se
indicators (RSI, MACD, SMA, ATR) nikaalte hain. Ek hi jagah rakha hai
taaki galti na ho.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app import config
from data import normalize

DISCLAIMER = ("This is research only, not investment advice. "
              "Consult a SEBI registered advisor.")

DISCLAIMER_HI = ("Ye sirf research hai, investment advice NAHI. "
                 "SEBI registered advisor se salaah lena.")

_DATASET_CACHE: dict[str, tuple[tuple[int, int], pd.DataFrame]] = {}


def load_dataset(dataset_name: str) -> pd.DataFrame:
    """parquet se dataset padho. Na mile to khaali frame."""
    try:
        path = normalize.processed_path(dataset_name)
    except KeyError:
        return pd.DataFrame()
    path_obj = Path(path)
    if not path_obj.exists():
        return pd.DataFrame()
    stat = path_obj.stat()
    signature = (int(stat.st_mtime_ns), int(stat.st_size))
    cached = _DATASET_CACHE.get(dataset_name)
    if cached is not None and cached[0] == signature:
        return cached[1]
    df = normalize.read_parquet(path)
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if "date" in df.columns:
        df = df.assign(date=df["date"].astype(str))
    _DATASET_CACHE[dataset_name] = (signature, df)
    return df


def load_stock_book() -> pd.DataFrame:
    """Nifty-100 stock book (yahoo_backup + nse_official)."""
    return load_dataset("nifty100_stocks")


def filter_stock_universe(book: pd.DataFrame, universe: str = "NIFTY100") -> tuple[pd.DataFrame, dict]:
    """Restrict the stock book to a dated official NSE constituent snapshot."""
    key = str(universe or "NIFTY100").upper().strip()
    if key in {"ALL", "NIFTY100"}:
        return book, {"universe": key, "available": True, "symbols": int(book["symbol"].nunique())}
    cons = load_dataset("constituents")
    if cons is None or len(cons) == 0 or "index_key" not in cons.columns:
        return pd.DataFrame(), {"universe": key, "available": False,
                                "reason": "official constituent snapshot is unavailable"}
    symbols = set(cons.loc[cons["index_key"].astype(str).str.upper() == key, "symbol"].astype(str).str.upper())
    filtered = book[book["symbol"].astype(str).str.upper().isin(symbols)].copy()
    return filtered, {"universe": key, "available": bool(len(filtered)),
                      "symbols": int(filtered["symbol"].nunique()) if len(filtered) else 0,
                      "constituent_symbols": len(symbols)}


def load_index_book() -> pd.DataFrame:
    """Saare index datasets ek frame me (nifty50, sensex, ...)."""
    frames = []
    for dataset in ("nifty50", "banknifty", "sensex", "midcap", "nifty100_index"):
        df = load_dataset(dataset)
        if df is not None and len(df):
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def for_symbol(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Ek symbol ka data, date se sorted, numeric columns clean.

    NOTE: for 100+ symbols use `group_book()` once instead - it is far faster.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()
    sub = df[df["symbol"].astype(str).str.upper() == str(symbol).upper()].copy()
    if len(sub) == 0:
        return pd.DataFrame()
    for col in ("open", "high", "low", "close", "volume"):
        if col in sub.columns:
            sub[col] = pd.to_numeric(sub[col], errors="coerce")
    return sub.sort_values("date").reset_index(drop=True)


def group_book(book: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Symbol -> DataFrame, EK BAAR group karke.

    Ye bahut zaroori speed fix hai: pehle har symbol ke liye poori 5 lakh row
    table filter hoti thi (44 second!). Ab sirf ek baar.
    """
    if book is None or len(book) == 0:
        return {}
    work = book.copy()
    work["symbol"] = work["symbol"].astype(str).str.upper().str.strip()
    for col in ("open", "high", "low", "close", "volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.sort_values("date")
    return {str(key): grp.reset_index(drop=True)
            for key, grp in work.groupby("symbol", sort=False)}


def rnd(value, nd: int = 2):
    """Number ko round karo. NaN ho to 'MISSING' (0 nahi)."""
    if value is None:
        return config.MISSING
    try:
        val = float(value)
    except (TypeError, ValueError):
        return config.MISSING
    if np.isnan(val) or np.isinf(val):
        return config.MISSING
    return round(val, nd)


def pct(a, b) -> float | None:
    """(a-b)/b * 100 - safe division."""
    try:
        a, b = float(a), float(b)
        if b == 0 or np.isnan(a) or np.isnan(b):
            return None
        return (a - b) / b * 100.0
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Indicators (pandas only - no extra library)
# ---------------------------------------------------------------------------
def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=max(2, window // 2)).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI (0-100). avg_loss = 0 ho to RSI = 100."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.mask(avg_loss.eq(0) & avg_gain.gt(0), 100.0)
    out = out.mask(avg_loss.eq(0) & avg_gain.eq(0), 50.0)
    return out


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return pd.DataFrame({"macd": macd_line, "signal": signal_line,
                         "hist": macd_line - signal_line})


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range - stop-loss lagane ke liye."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=max(2, period // 2)).mean()


def max_drawdown(series: pd.Series) -> float | None:
    """Sabse bada gir (peak se trough) percentage me."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 2:
        return None
    running_max = s.cummax()
    dd = (s - running_max) / running_max * 100.0
    return float(dd.min())


def annualised_vol(returns: pd.Series, window: int = 20) -> float | None:
    r = pd.to_numeric(returns, errors="coerce").dropna().tail(window)
    if len(r) < 5:
        return None
    return float(r.std(ddof=0) * np.sqrt(252) * 100.0)


def days_between(a: str, b: str) -> int:
    try:
        return (pd.Timestamp(b) - pd.Timestamp(a)).days
    except Exception:  # noqa: BLE001
        return 0
