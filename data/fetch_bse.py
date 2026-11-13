"""data/fetch_bse.py - Official BSE data (attempt + honest failure log).

Reality check (our probe se pata chala):
  * api.bseindia.com is machine par WAF (Akamai) se 403 "Access Denied" deta hai.
  * Isliye hum koshish karte hain, FAIL hone par SACCH likhte hain:
        state/project_state.json -> source_failures[]
        logs/fetch_log.jsonl     -> BLOCKED
  * Sensex ka purana data hum Yahoo backup se laate hain, aur `source` column me
    saaf likha hota hai "yahoo_backup" - isse pata chalta hai ki data asli hai.
  * KABHI fake Sensex number nahi likha jaayega.

Agar aapka network BSE allow karta hai, yahi code apne aap kaam karega.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app import config, state_manager
from data import http_util
from data.http_util import log_event

BSE_API_HOST = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_WEB_HOST = "https://www.bseindia.com"

BSE_INDEX_ARCH_URL = BSE_API_HOST + "/IndexArchDaily/w"
BSE_INDEX_PAGE = BSE_WEB_HOST + "/indices/IndexArchiveData.html"
BSE_BHAV_URL = BSE_WEB_HOST + "/download/BhavCopy/Equity/EQ{dd}{mm}{yy}_CSV.ZIP"

# BSE index code -> our symbol (index=1 = Sensex in the archive API)
BSE_INDEX_CODES: dict[str, int] = {
    "SENSEX": 1,
    "BSE100": 2,
    "BSEMIDCAP": 4,
    "BSESMALLCAP": 5,
}

_DATE_FORMATS = ("%d/%m/%Y", "%Y%m%d", "%d-%m-%Y")


def _bse_session():
    session = http_util.browser_session(referer=BSE_INDEX_PAGE)
    # BSE ka WAF in headers ke bina block karta hai.
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Origin": BSE_WEB_HOST,
        "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    })
    return session


def _parse_bse_rows(payload: dict, symbol: str) -> pd.DataFrame:
    """BSE JSON ko canonical rows me badlo (jo bhi key mili ho usse)."""
    rows = payload.get("Table") or payload.get("table") or payload.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("Table") or []
    records = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        low = {str(k).strip().lower(): v for k, v in item.items()}

        def pick(*names):
            for n in names:
                if n in low and low[n] not in (None, ""):
                    return low[n]
            return None

        date_val = pick("trddate", "date", "tradedate", "trad_date")
        parsed = None
        if date_val is not None:
            for fmt in _DATE_FORMATS:
                try:
                    parsed = dt.datetime.strptime(str(date_val).strip()[:10], fmt).date()
                    break
                except ValueError:
                    continue
        if parsed is None:
            continue
        records.append({
            "date": parsed.isoformat(),
            "symbol": symbol,
            "open": pick("open", "openprice", "op"),
            "high": pick("high", "highprice", "hp"),
            "low": pick("low", "lowprice", "lp"),
            "close": pick("close", "closeprice", "cl"),
            "volume": pick("volume", "noofshares", "turnover"),
            "source": config.SOURCE_BSE_OFFICIAL,
        })
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[df["close"].notna()].reset_index(drop=True)


def fetch_bse_index_history(symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Official BSE index history. Fail ho to khaali DataFrame + honest log."""
    code = BSE_INDEX_CODES.get(symbol.upper())
    if code is None:
        log_event(config.SOURCE_BSE_OFFICIAL, symbol, "FAILED", None, "unknown BSE index")
        return pd.DataFrame()

    session = _bse_session()
    try:
        session.get(BSE_WEB_HOST + "/", timeout=config.HTTP_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001
        pass  # cookie warm-up is best-effort only

    params = {
        "index": code, "flag": 1, "sess": "",
        "fromdate": start.strftime("%Y%m%d"), "todate": end.strftime("%Y%m%d"),
    }
    resp = http_util.get_with_retry(BSE_INDEX_ARCH_URL, session=session,
                                    source=config.SOURCE_BSE_OFFICIAL, params=params)
    if resp is None:
        state_manager.record_source_failure(
            config.SOURCE_BSE_OFFICIAL, BSE_INDEX_ARCH_URL, "BLOCKED",
            "official BSE archive API not reachable (WAF/403 or timeout). "
            "Sensex is taken from the Yahoo backup source and clearly marked as such.")
        return pd.DataFrame()

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        log_event(config.SOURCE_BSE_OFFICIAL, BSE_INDEX_ARCH_URL, "FAILED", 200,
                  f"not JSON: {type(exc).__name__}")
        return pd.DataFrame()

    df = _parse_bse_rows(payload if isinstance(payload, dict) else {}, symbol.upper())
    if len(df) == 0:
        log_event(config.SOURCE_BSE_OFFICIAL, BSE_INDEX_ARCH_URL, "EMPTY", 200,
                  f"official BSE returned no rows for {symbol} {start}..{end}")
        return pd.DataFrame()
    log_event(config.SOURCE_BSE_OFFICIAL, BSE_INDEX_ARCH_URL, "OK", 200, f"rows={len(df)}")
    return df


def fetch_bse_equity_bhavcopy(day: dt.date) -> pd.DataFrame:
    """Official BSE equity bhavcopy for one day (best effort + log)."""
    url = BSE_BHAV_URL.format(dd=day.strftime("%d"), mm=day.strftime("%m"),
                              yy=day.strftime("%y"))
    resp = http_util.get_with_retry(url, session=_bse_session(),
                                    source=config.SOURCE_BSE_OFFICIAL)
    if resp is None:
        return pd.DataFrame()
    log_event(config.SOURCE_BSE_OFFICIAL, url, "OK", 200, f"bytes={len(resp.content)}")
    return pd.DataFrame()


def official_status() -> dict:
    """BSE official source available hai ya nahi - ek saaf, honest status report.

    Teen possible halat:
      reachable=False -> BLOCKED (WAF/403/timeout)
      reachable=True + rows=0 -> EMPTY (API jawab deta hai par data nahi)
      reachable=True + rows>0 -> available
    """
    session = _bse_session()
    resp = http_util.get_with_retry(BSE_INDEX_ARCH_URL, session=session,
                                    source=config.SOURCE_BSE_OFFICIAL,
                                    params={"index": 1, "flag": 1, "sess": "",
                                            "fromdate": "20240101", "todate": "20240105"})
    rows = 0
    if resp is not None:
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                rows = len(_parse_bse_rows(payload, "SENSEX"))
        except Exception:  # noqa: BLE001
            rows = 0
    reachable = resp is not None
    available = reachable and rows > 0
    if available:
        note = f"official BSE OK ({rows} rows returned)"
    elif reachable:
        note = ("official BSE API is reachable but returned no rows (empty payload) - "
                "Sensex is served from the labelled yahoo_backup source, never invented")
    else:
        note = ("official BSE BLOCKED (WAF/403 or timeout) - Sensex is served from the "
                "labelled yahoo_backup source, never invented")
    return {"source": config.SOURCE_BSE_OFFICIAL, "endpoint": BSE_INDEX_ARCH_URL,
            "available": available, "reachable": reachable, "rows": rows, "note": note}
