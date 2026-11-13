"""data/fetch_nse.py - Official NSE data (ASLI primary source).

Kaunsa official NSE source use hota hai (verified by our network probe):
  1) Index daily file  : nsearchives.nseindia.com/content/indices/
                         ind_close_all_DDMMYYYY.csv        (2013 -> today)
  2) Equity bhavcopy   : nsearchives.nseindia.com/content/historical/EQUITIES/
                         YYYY/MON/cmDDMONYYYYbhav.csv.zip  (2000 -> today)
  3) Constituent list  : www.niftyindices.com/IndexConstituent/
                         ind_nifty50list.csv  etc.

IMPORTANT: agar official file na mile to hum us date ko MISSING rakhte hain
aur log likhte hain. Kabhi number invent nahi karte.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from app import config
from data import http_util
from data.http_util import log_event

NSE_ARCHIVE_HOST = "https://nsearchives.nseindia.com"
NIFTYINDICES_HOST = "https://www.niftyindices.com"

INDICES_URL = NSE_ARCHIVE_HOST + "/content/indices/ind_close_all_{stamp}.csv"
BHAV_URL = (NSE_ARCHIVE_HOST + "/content/historical/EQUITIES/{yyyy}/{mon_upper}/"
            "cm{dd}{mon_upper}{yyyy}bhav.csv.zip")
# NSE changed the bhavcopy location on 2024-07-08 -> naya format bhi support karo
BHAV_URL_NEW = NSE_ARCHIVE_HOST + "/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
NEW_BHAV_CUTOFF = dt.date(2024, 7, 8)
# canonical bhavcopy column -> possible names in old/new file formats
BHAV_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "SYMBOL": ("SYMBOL", "TCKRSYMB", "TCKRSYMBOL"),
    "SERIES": ("SERIES", "SCTYSRS"),
    "OPEN": ("OPEN", "OPNPRIC"),
    "HIGH": ("HIGH", "HGHPRIC"),
    "LOW": ("LOW", "LWPRIC"),
    "CLOSE": ("CLOSE", "CLSPRIC"),
    "TOTTRDQTY": ("TOTTRDQTY", "TTLTRADGVOL"),
    "ISIN": ("ISIN",),
}
CONSTITUENT_URL = NIFTYINDICES_HOST + "/IndexConstituent/{file}"

MONTHS_UPPER = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

CONSTITUENT_FILES: dict[str, str] = {
    "NIFTY50": "ind_nifty50list.csv",
    "NIFTY100": "ind_nifty100list.csv",
    "NIFTYBANK": "ind_niftybanklist.csv",
    "MIDCAP150": "ind_niftymidcap150list.csv",
}

# Index-file name (as written inside ind_close_all CSV) -> our short symbol
INDEX_NAME_MAP: dict[str, str] = {
    "nifty 50": "NIFTY50",
    "nifty bank": "BANKNIFTY",
    "nifty 100": "NIFTY100",
    "nifty midcap 150": "MIDCAP150",
    "nifty midcap 50": "MIDCAP50",
    "nifty 500": "NIFTY500",
    "nifty next 50": "NIFTYNEXT50",
}


def candidate_trading_dates(start: dt.date, end: dt.date) -> list[dt.date]:
    """Mon-Fri dates. Asli holiday hum official 404 se detect karte hain."""
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += dt.timedelta(days=1)
    return days


def _get(session, url: str, source: str):
    """Ek HTTP GET. 404 = market holiday (normal, isliye noise nahi)."""
    try:
        resp = session.get(url, timeout=config.HTTP_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        log_event(source, url, "FAILED", None, f"{type(exc).__name__}: {str(exc)[:150]}")
        return None
    if resp.status_code == 200:
        return resp
    if resp.status_code == 404:
        return None
    log_event(source, url, "BLOCKED" if resp.status_code in (401, 403, 405) else "FAILED",
              resp.status_code, "official NSE file not available")
    return None


# ---------------------------------------------------------------------------
# 1) Official constituent lists
# ---------------------------------------------------------------------------
def download_constituents() -> dict[str, pd.DataFrame]:
    """Nifty50 / Nifty100 / NiftyBank / Midcap150 ki ASLI company list."""
    session = http_util.browser_session(referer=NIFTYINDICES_HOST + "/")
    out: dict[str, pd.DataFrame] = {}
    for key, filename in CONSTITUENT_FILES.items():
        url = CONSTITUENT_URL.format(file=filename)
        resp = _get(session, url, config.SOURCE_NSE_OFFICIAL)
        if resp is None:
            log_event(config.SOURCE_NSE_OFFICIAL, url, "FAILED", None,
                      f"constituent list {filename} unavailable")
            continue
        try:
            text = resp.content.decode("utf-8-sig", errors="replace")
            df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
            df.columns = [str(c).strip() for c in df.columns]
            df = df.rename(columns={"Company Name": "company_name", "Symbol": "symbol",
                                    "Industry": "industry", "Series": "series",
                                    "ISIN Code": "isin"})
            if "symbol" not in df.columns:
                log_event(config.SOURCE_NSE_OFFICIAL, url, "EMPTY", 200,
                          f"no Symbol column in {filename}")
                continue
            df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
            df = df[df["symbol"].ne("") & df["symbol"].ne("NAN")]
            df["index_key"] = key
            df["source"] = config.SOURCE_NSE_OFFICIAL
            out[key] = df.reset_index(drop=True)
            log_event(config.SOURCE_NSE_OFFICIAL, url, "OK", 200, f"rows={len(df)}")
        except Exception as exc:  # noqa: BLE001
            log_event(config.SOURCE_NSE_OFFICIAL, url, "FAILED", 200,
                      f"parse error: {type(exc).__name__}: {str(exc)[:120]}")
    return out


# ---------------------------------------------------------------------------
# 2) Official index daily file (all indices in one CSV)
# ---------------------------------------------------------------------------
def _raw_cache_path(kind: str, day: dt.date, ext: str) -> Path:
    """Downloaded raw file ka path (data/raw/... - C: par kabhi nahi)."""
    folder = config.RAW_DIR / kind / day.strftime("%Y")
    config.assert_not_c_drive(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{kind}_{day.strftime('%Y%m%d')}.{ext}"


def _get_bytes(session, url: str, source: str, cache_path: Path | None = None):
    """Bytes lo. Pehle local cache (data/raw) check karo, phir internet.

    Ye cache reruns ko turant karta hai aur NSE par load bhi kam daalta hai.
    """
    if cache_path is not None and Path(cache_path).exists() and \
            Path(cache_path).stat().st_size > 0:
        return Path(cache_path).read_bytes()
    resp = _get(session, url, source)
    if resp is None:
        return None
    if cache_path is not None:
        try:
            Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
            Path(cache_path).write_bytes(resp.content)
        except Exception:  # noqa: BLE001
            pass
    return resp.content


def fetch_index_file(session, day: dt.date) -> pd.DataFrame | None:
    """Ek din ka official 'ind_close_all' CSV -> raw DataFrame."""
    url = INDICES_URL.format(stamp=day.strftime("%d%m%Y"))
    payload = _get_bytes(session, url, config.SOURCE_NSE_OFFICIAL,
                         _raw_cache_path("indices", day, "csv"))
    if payload is None:
        return None
    try:
        text = payload.decode("utf-8-sig", errors="replace")
        df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
        df.columns = [str(c).strip() for c in df.columns]
        if "Index Name" not in df.columns:
            return None
        df = df.rename(columns={"Index Name": "index_name", "Open Index Value": "open",
                                "High Index Value": "high", "Low Index Value": "low",
                                "Closing Index Value": "close", "Volume": "volume"})
        df["date"] = day.isoformat()
        return df
    except Exception as exc:  # noqa: BLE001
        log_event(config.SOURCE_NSE_OFFICIAL, url, "FAILED", 200,
                  f"parse error: {type(exc).__name__}: {str(exc)[:120]}")
        return None


# ---------------------------------------------------------------------------
# 3) Official equity bhavcopy (all listed stocks for one day)
# ---------------------------------------------------------------------------
def _canonical_bhav_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Old (2000-2024) aur new (2024+) bhavcopy dono ko ek shape me laao."""
    upper = {str(c).strip().upper().replace(" ", ""): c for c in df.columns}
    out = pd.DataFrame()
    for canonical, aliases in BHAV_COLUMN_ALIASES.items():
        for alias in aliases:
            key = alias.strip().upper().replace(" ", "")
            if key in upper:
                out[canonical] = df[upper[key]]
                break
    return out


def fetch_equity_bhavcopy(session, day: dt.date) -> pd.DataFrame | None:
    """Ek din ka official bhavcopy zip -> raw equity DataFrame (dono formats)."""
    new_url = BHAV_URL_NEW.format(yyyymmdd=day.strftime("%Y%m%d"))
    old_url = BHAV_URL.format(yyyy=day.strftime("%Y"), dd=day.strftime("%d"),
                              mon_upper=MONTHS_UPPER[day.month - 1])
    urls = ([("new", new_url), ("old", old_url)] if day >= NEW_BHAV_CUTOFF
            else [("old", old_url), ("new", new_url)])

    for tag, url in urls:
        payload = _get_bytes(session, url, config.SOURCE_NSE_OFFICIAL,
                             _raw_cache_path("bhavcopy", day, f"{tag}.zip"))
        if payload is None:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                raw = zf.read(zf.namelist()[0])
            df = pd.read_csv(io.BytesIO(raw), skipinitialspace=True, low_memory=False)
            canonical = _canonical_bhav_columns(df)
            if "SYMBOL" not in canonical.columns or "CLOSE" not in canonical.columns:
                log_event(config.SOURCE_NSE_OFFICIAL, url, "EMPTY", 200,
                          "bhavcopy parsed but SYMBOL/CLOSE missing")
                continue
            canonical["date"] = day.isoformat()
            canonical["_src_url"] = url
            return canonical
        except Exception as exc:  # noqa: BLE001
            log_event(config.SOURCE_NSE_OFFICIAL, url, "FAILED", 200,
                      f"unzip/parse error: {type(exc).__name__}: {str(exc)[:120]}")
    return None


# ---------------------------------------------------------------------------
# 4) Threaded backfill
# ---------------------------------------------------------------------------
def _indices_worker(day: dt.date):
    session = http_util.browser_session(referer=NIFTYINDICES_HOST + "/")
    df = fetch_index_file(session, day)
    return day, df


def backfill_indices(start: dt.date, end: dt.date,
                     wanted: tuple[str, ...] = ("NIFTY50", "BANKNIFTY", "NIFTY100", "MIDCAP150"),
                     workers: int | None = None,
                     progress=None) -> dict:
    """Official index history. Har successful file = asli market-open date."""
    workers = workers or config.HTTP_WORKERS
    days = candidate_trading_dates(start, end)
    frames: list[pd.DataFrame] = []
    market_open_dates: list[str] = []
    ok = failed = 0

    def _handle(day, raw):
        nonlocal ok, failed
        if raw is None or len(raw) == 0:
            failed += 1
            return
        ok += 1
        market_open_dates.append(day.isoformat())
        names = raw["index_name"].astype(str).str.strip().str.lower()
        raw = raw.assign(_key=names.map(INDEX_NAME_MAP))
        raw = raw[raw["_key"].isin(wanted)]
        if len(raw) == 0:
            return
        for _, row in raw.iterrows():
            frames.append(pd.DataFrame([{
                "date": day.isoformat(), "symbol": row["_key"],
                "open": row.get("open"), "high": row.get("high"),
                "low": row.get("low"), "close": row.get("close"),
                "volume": row.get("volume"), "source": config.SOURCE_NSE_OFFICIAL,
            }]))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_indices_worker, d): d for d in days}
        done = 0
        for fut in as_completed(futures):
            day, raw = fut.result()
            _handle(day, raw)
            done += 1
            if progress and done % 200 == 0:
                progress(f"official indices: {done}/{len(days)} days "
                         f"(ok={ok}, holiday/missing={failed})")

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    log_event(config.SOURCE_NSE_OFFICIAL, INDICES_URL, "OK", 200,
              f"index backfill {start}..{end}: files_ok={ok} missing={failed} rows={len(df)}")
    return {"frame": df, "market_open_dates": sorted(set(market_open_dates)),
            "files_ok": ok, "files_missing": failed, "days_tried": len(days)}


def _bhav_worker(day: dt.date):
    session = http_util.browser_session(referer=NIFTYINDICES_HOST + "/")
    df = fetch_equity_bhavcopy(session, day)
    return day, df


def backfill_equities(start: dt.date, end: dt.date,
                      symbols: set[str] | None = None,
                      series: str = "EQ",
                      workers: int | None = None,
                      progress=None) -> dict:
    """Official NSE bhavcopy -> per-stock OHLC (source = nse_official).

    symbols=None matlab SAARE stocks (bhaari). Symbols do to sirf wahi.
    """
    workers = workers or config.HTTP_WORKERS
    days = candidate_trading_dates(start, end)
    frames: list[pd.DataFrame] = []
    market_open_dates: list[str] = []
    ok = failed = 0

    def _handle(day, raw):
        nonlocal ok, failed
        if raw is None or len(raw) == 0:
            failed += 1
            return
        ok += 1
        market_open_dates.append(day.isoformat())
        work = raw
        if series and "SERIES" in work.columns:
            sers = work["SERIES"].astype(str).str.strip().str.upper()
            work = work[sers.eq(series)]
        elif "SERIES" not in work.columns:
            log_event(config.SOURCE_NSE_OFFICIAL, "bhavcopy", "EMPTY", 200,
                      "SERIES column missing in this bhavcopy day")
        work = work.assign(sym=work["SYMBOL"].astype(str).str.strip().str.upper())
        if symbols:
            work = work[work["sym"].isin(symbols)]
        if len(work) == 0:
            return
        frames.append(pd.DataFrame({
            "date": day.isoformat(),
            "symbol": work["sym"].to_numpy(),
            "open": pd.to_numeric(work["OPEN"], errors="coerce").to_numpy(),
            "high": pd.to_numeric(work["HIGH"], errors="coerce").to_numpy(),
            "low": pd.to_numeric(work["LOW"], errors="coerce").to_numpy(),
            "close": pd.to_numeric(work["CLOSE"], errors="coerce").to_numpy(),
            "volume": pd.to_numeric(work["TOTTRDQTY"], errors="coerce").to_numpy(),
            "source": config.SOURCE_NSE_OFFICIAL,
        }))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_bhav_worker, d): d for d in days}
        done = 0
        for fut in as_completed(futures):
            day, raw = fut.result()
            _handle(day, raw)
            done += 1
            if progress and done % 50 == 0:
                progress(f"official bhavcopy: {done}/{len(days)} days "
                         f"(ok={ok}, holiday/missing={failed})")

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    log_event(config.SOURCE_NSE_OFFICIAL, BHAV_URL, "OK", 200,
              f"bhavcopy backfill {start}..{end}: files_ok={ok} missing={failed} rows={len(df)}")
    return {"frame": df, "market_open_dates": sorted(set(market_open_dates)),
            "files_ok": ok, "files_missing": failed, "days_tried": len(days)}


def fetch_latest_official(date: dt.date | None = None) -> dict:
    """Daily update ke liye: ek din ka official data (index + equity)."""
    day = date or config.today_ist()
    session = http_util.browser_session(referer=NIFTYINDICES_HOST + "/")
    idx = fetch_index_file(session, day)
    bhav = fetch_equity_bhavcopy(session, day)
    return {"date": day.isoformat(), "index_raw": idx, "equity_raw": bhav}


# ---------------------------------------------------------------------------
# 5) Official F&O (derivatives) bhavcopy - Futures vs Spot arbitrage ke liye
# ---------------------------------------------------------------------------
FO_BHAV_URL = (NSE_ARCHIVE_HOST + "/content/historical/DERIVATIVES/{yyyy}/{mon_upper}/"
               "fo{dd}{mon_upper}{yyyy}bhav.csv.zip")
FO_BHAV_URL_NEW = NSE_ARCHIVE_HOST + "/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"

FO_ALIASES: dict[str, tuple[str, ...]] = {
    "SYMBOL": ("SYMBOL", "TCKRSYMB"),
    "EXPIRY_DT": ("EXPIRY_DT", "XPRYDT"),
    "INSTRUMENT": ("INSTRUMENT", "FININSTRMTP", "FININSTRMNNM"),
    "STRIKE_PR": ("STRIKE_PR", "STRKPRIC"),
    "OPTION_TYP": ("OPTION_TYP", "OPTNTP"),
    "OPEN": ("OPEN", "OPNPRIC"),
    "HIGH": ("HIGH", "HGHPRIC"),
    "LOW": ("LOW", "LWPRIC"),
    "CLOSE": ("CLOSE", "CLSPRIC"),
    "SETTLE_PR": ("SETTLE_PR", "STTLMPRIC"),
    "OPEN_INT": ("OPEN_INT", "OPNINTRST"),
    "CONTRACTS": ("CONTRACTS", "TTLTRADGVOL"),
}


def fetch_fo_bhavcopy(session, day: dt.date) -> pd.DataFrame | None:
    """Ek din ka official F&O bhavcopy (best effort, honest logs)."""
    urls = []
    if day >= NEW_BHAV_CUTOFF:
        urls.append(FO_BHAV_URL_NEW.format(yyyymmdd=day.strftime("%Y%m%d")))
    urls.append(FO_BHAV_URL.format(yyyy=day.strftime("%Y"), dd=day.strftime("%d"),
                                   mon_upper=MONTHS_UPPER[day.month - 1]))

    for url in urls:
        resp = _get(session, url, config.SOURCE_NSE_OFFICIAL)
        if resp is None:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                raw = zf.read(zf.namelist()[0])
            df = pd.read_csv(io.BytesIO(raw), skipinitialspace=True, low_memory=False)
            upper = {str(c).strip().upper().replace(" ", ""): c for c in df.columns}
            out = pd.DataFrame()
            for canonical, aliases in FO_ALIASES.items():
                for alias in aliases:
                    key = alias.strip().upper().replace(" ", "")
                    if key in upper:
                        out[canonical] = df[upper[key]]
                        break
            if "SYMBOL" not in out.columns or "CLOSE" not in out.columns:
                continue
            out["date"] = day.isoformat()
            return out
        except Exception as exc:  # noqa: BLE001
            log_event(config.SOURCE_NSE_OFFICIAL, url, "FAILED", 200,
                      f"fo bhavcopy parse error: {type(exc).__name__}: {str(exc)[:110]}")
    return None

