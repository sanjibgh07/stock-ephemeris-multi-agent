"""data/normalize.py - saara data ek hi shape me laao.

Canonical schema (spec ke hisaab se):
    date | symbol | open | high | low | close | volume | source

Simple English:
  * `date`   -> YYYY-MM-DD (IST)
  * `symbol` -> RELIANCE ya NIFTY50 / SENSEX type naam
  * `source` -> yah data KAHAN se aaya (nse_official / yahoo_backup ...)
                 Isse hum hamesha bata sakte hain ki data asli hai.
  * Missing value = NaN (parquet me), frontend me "MISSING" dikhega. 0 kabhi nahi.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app import config

CANONICAL_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume", "source"]
PRICE_COLUMNS = ["open", "high", "low", "close"]

# dataset name -> parquet file name (spec: nifty50, banknifty, sensex, midcap, nifty100_*)
DATASET_FILES: dict[str, str] = {
    "nifty50": "nifty50.parquet",
    "banknifty": "banknifty.parquet",
    "sensex": "sensex.parquet",
    "midcap": "midcap.parquet",
    "nifty100_index": "nifty100_index.parquet",
    "nifty100_stocks": "nifty100_stocks.parquet",
    "constituents": "nifty100_constituents.parquet",
    "nifty_bank_stocks": "niftybank_stocks.parquet",
    "arbitrage_quotes": "arbitrage_quotes.parquet",
}

EPHEMERIS_FILES: dict[str, str] = {
    "ephemeris_daily": "ephemeris_daily.parquet",
    "aspects_daily": "aspects_daily.parquet",
    "events_daily": "ephemeris_events.parquet",
}


def processed_path(dataset_name: str) -> Path:
    if dataset_name in DATASET_FILES:
        path = config.PROCESSED_DIR / DATASET_FILES[dataset_name]
    elif dataset_name in EPHEMERIS_FILES:
        path = config.EPHEMERIS_DIR / EPHEMERIS_FILES[dataset_name]
    else:
        raise KeyError(f"Unknown dataset '{dataset_name}'")
    return config.assert_not_c_drive(path)


def empty_ohlc_frame() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in CANONICAL_COLUMNS})


def normalize_ohlc(df: pd.DataFrame, symbol: str, source: str,
                   date_column: str = "date") -> pd.DataFrame:
    """Kisi bhi raw frame ko canonical schema me badlo.

    Rules:
      * close missing ho to us row ko phenk do (hum close invent nahi karte).
      * date ko YYYY-MM-DD string banao.
      * duplicate date par aakhri (latest) row rakho.
    """
    if df is None or len(df) == 0:
        return empty_ohlc_frame()

    work = df.copy()
    if date_column not in work.columns:
        raise KeyError(f"column '{date_column}' not found in frame")

    work["date"] = pd.to_datetime(work[date_column], errors="coerce").dt.date
    work = work[work["date"].notna()]

    for col in PRICE_COLUMNS:
        if col not in work.columns:
            work[col] = pd.NA
        work[col] = pd.to_numeric(work[col], errors="coerce")

    if "volume" not in work.columns:
        work["volume"] = pd.NA
    work["volume"] = pd.to_numeric(work["volume"], errors="coerce")

    work = work[work["close"].notna()]                 # no fake close values
    work["symbol"] = str(symbol).upper().strip()
    work["source"] = str(source)
    work["date"] = work["date"].astype(str)

    out = work[CANONICAL_COLUMNS].copy()
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return out.reset_index(drop=True)


def read_parquet(path: Path) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def merge_idempotent(existing: pd.DataFrame, new: pd.DataFrame,
                     keys: tuple[str, ...] = ("date", "symbol")) -> pd.DataFrame:
    """Purana + naya data jodo. Ek hi (date,symbol) do baar na aaye.

    Ye idempotent hai -> daily_update do baar chalane par duplicate nahi hoga.
    """
    frames = [f for f in (existing, new) if f is not None and len(f) > 0]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    # sirf wahi keys use karo jo frame me maujood hain (safe for any dataset)
    use_keys = [k for k in keys if k in merged.columns]
    sort_cols = [c for c in ("date", "symbol") if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols)
    if not use_keys:
        return merged.reset_index(drop=True)
    return merged.drop_duplicates(subset=use_keys, keep="last").reset_index(drop=True)


def sanitize_for_write(df: pd.DataFrame) -> pd.DataFrame:
    """Parquet likhne se pehle dtypes theek karo (string numbers ko float banao).

    Ye safety net hai: chahe data CSV se aaye ya JSON se, parquet write
    kabhi fail nahi hona chahiye.
    """
    if df is None or len(df) == 0:
        return df
    out = df.copy()
    for col in PRICE_COLUMNS + ["volume"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    for col in ("date", "symbol", "source", "body", "body_a", "body_b", "aspect",
                "sign", "nakshatra", "event_type", "detail"):
        if col in out.columns:
            out[col] = out[col].astype("string").astype("object")
    if "is_retrograde" in out.columns:
        out["is_retrograde"] = out["is_retrograde"].astype("boolean")
    return out


def write_dataset(dataset_name: str, new_df: pd.DataFrame, *,
                  keys: tuple[str, ...] = ("date", "symbol"),
                  register: bool = True) -> Path:
    """Parquet me idempotent merge karke likho + DB catalogue update karo."""
    path = processed_path(dataset_name)
    existing = read_parquet(path)
    merged = merge_idempotent(existing, new_df, keys=keys)
    merged = sanitize_for_write(merged)
    config.assert_not_c_drive(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if merged is None or len(merged) == 0:
        merged = sanitize_for_write(new_df) if new_df is not None else pd.DataFrame()
    merged.to_parquet(path, index=False)
    if register:
        register_dataset(dataset_name, merged, path)
    return path


def register_dataset(dataset_name: str, df: pd.DataFrame, path: Path) -> None:
    """DB me dataset catalogue update karo (row count, first/last date, sources)."""
    try:
        from db import database  # noqa: PLC0415

        if df is None or len(df) == 0:
            quality, first_date, last_date, rows, symbols, sources = (
                "MISSING", None, None, 0, 0, "")
        else:
            rows = int(len(df))
            symbols = int(df["symbol"].nunique()) if "symbol" in df.columns else 0
            dates = pd.to_datetime(df["date"], errors="coerce")
            first_date = dates.min().date().isoformat() if dates.notna().any() else None
            last_date = dates.max().date().isoformat() if dates.notna().any() else None
            srcs = sorted({str(s) for s in df.get("source", pd.Series(dtype=str)).dropna().unique()})
            sources = ",".join(srcs)
            quality = "OK" if "nse_official" in srcs else ("PARTIAL" if srcs else "MISSING")
        try:
            rel = str(Path(path).relative_to(config.PROJECT_ROOT))
        except ValueError:
            rel = str(path)
        database.upsert_dataset(dataset_name, rel, rows, first_date, last_date,
                                symbols, sources, quality)
    except Exception:  # noqa: BLE001
        pass


def find_missing_trading_days(symbol_df: pd.DataFrame, symbol: str,
                              market_open_dates: list[str],
                              listed_from: str | None = None,
                              listed_to: str | None = None) -> list[dict]:
    """Market khula tha par is symbol ka data nahi -> gap list.

    Market-open dates asli official files se aate hain, isliye ye guess nahi hai.
    """
    if not market_open_dates:
        return []
    if symbol_df is not None and len(symbol_df):
        present = set(symbol_df["date"].astype(str))
    else:
        present = set()
    gaps: list[dict] = []
    for date_str in market_open_dates:
        if listed_from and date_str < listed_from:
            continue
        if listed_to and date_str > listed_to:
            continue
        if date_str not in present:
            gaps.append({"date": date_str, "symbol": symbol, "reason": "market_open_no_data"})
    return gaps


def cross_check(primary_df: pd.DataFrame, backup_df: pd.DataFrame, symbol: str,
                tolerance_pct: float = 0.5) -> dict:
    """NSE official vs Yahoo backup compare. >0.5% fark = MISMATCH.

    IMPORTANT: hum data auto-correct NAHI karte - sirf flag karte hain.
    """
    result = {"symbol": symbol, "compared": 0, "mismatches": [], "max_diff_pct": None,
              "tolerance_pct": tolerance_pct}
    if primary_df is None or backup_df is None or len(primary_df) == 0 or len(backup_df) == 0:
        result["note"] = "no overlapping data to compare"
        return result

    left = primary_df[["date", "close", "source"]].copy()
    right = backup_df[["date", "close", "source"]].copy()
    merged = left.merge(right, on="date", suffixes=("_primary", "_backup"))
    if merged.empty:
        result["note"] = "no overlapping dates"
        return result

    result["compared"] = int(len(merged))
    diff = (merged["close_primary"] - merged["close_backup"]).abs()
    base = merged["close_primary"].replace(0, pd.NA)
    pct = (diff / base) * 100.0
    merged = merged.assign(diff_pct=pct)
    worst = merged["diff_pct"].max()
    result["max_diff_pct"] = None if pd.isna(worst) else round(float(worst), 4)

    flagged = merged[merged["diff_pct"] > tolerance_pct]
    for _, row in flagged.iterrows():
        result["mismatches"].append({
            "date": str(row["date"]),
            "primary_close": float(row["close_primary"]),
            "backup_close": float(row["close_backup"]),
            "diff_pct": round(float(row["diff_pct"]), 4),
            "primary_source": str(row["source_primary"]),
            "backup_source": str(row["source_backup"]),
        })
    return result


def frame_records(df: pd.DataFrame, limit: int | None = None) -> list[dict]:
    """DataFrame -> JSON-safe records (NaN -> 'MISSING')."""
    if df is None or len(df) == 0:
        return []
    work = df.tail(limit) if limit else df
    return [config.json_safe(rec) for rec in work.to_dict(orient="records")]
