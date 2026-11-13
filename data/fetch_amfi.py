"""data/fetch_amfi.py - Official AMFI NAV feed (ETF vs NAV arbitrage).

AMFI = Association of Mutual Funds in India (official body).
Public, free, no login: https://www.amfiindia.com/spages/NAVAll.txt

Kyun chahiye: ETF ka market price (exchange) vs uska NAV (asli value)
compare karke ETF-NAV arbitrage dekh sakte hain.
Agar feed na mile to hum NOT_AVAILABLE likhte hain - number invent nahi karte.
"""
from __future__ import annotations

import pandas as pd

from app import config
from app import state_manager
from data import http_util
from data.http_util import log_event

AMFI_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

# ETF jo hum NSE/BSE par track kar sakte hain (scheme code -> our label)
TRACKED_ETFS: dict[str, str] = {
    "NIFTYBEES.NS": "Nippon India ETF Nifty BeES",
    "BANKBEES.NS": "Nippon India ETF Bank BeES",
    "GOLDBEES.NS": "Nippon India ETF Gold BeES",
    "JUNIORBEES.NS": "Nippon India ETF Nifty Next 50 Junior BeES",
}


def fetch_amfi_nav(timeout: int = 15, max_retries: int = 1) -> pd.DataFrame:
    """Poori AMFI NAV file -> DataFrame (scheme_code, isin, scheme_name, nav, date).

    File semicolon-separated hai aur usme section headers bhi hote hain -
    unhe safe tarike se filter karte hain. Chhota timeout rakha hai taaki
    frontend 30 second ke andar rahe.
    """
    resp = http_util.get_with_retry(AMFI_NAV_URL, source=config.SOURCE_AMFI_OFFICIAL,
                                    log_ok=True, timeout=timeout,
                                    max_retries=max_retries,
                                    headers={"Accept": "text/plain,*/*"})
    if resp is None:
        state_manager.record_source_failure(
            config.SOURCE_AMFI_OFFICIAL,
            AMFI_NAV_URL,
            "BLOCKED",
            "AMFI NAV feed not reachable - ETF vs NAV check will report NOT_AVAILABLE",
        )
        log_event(config.SOURCE_AMFI_OFFICIAL, AMFI_NAV_URL, "BLOCKED", None,
                  "AMFI NAV feed not reachable - ETF vs NAV check will report NOT_AVAILABLE")
        return pd.DataFrame()

    rows: list[dict] = []
    nav_date = config.MISSING
    for line in resp.text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("open ended schemes") or line.lower().startswith("close ended"):
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 6:
            continue
        if parts[0].isdigit():
            rows.append({
                "scheme_code": parts[0], "isin_growth": parts[1], "isin_div": parts[2],
                "scheme_name": parts[3], "nav": parts[4], "nav_date": parts[5],
                "source": config.SOURCE_AMFI_OFFICIAL,
            })
            nav_date = parts[5]
    df = pd.DataFrame(rows)
    if len(df):
        df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
        df = df[df["nav"].notna()].reset_index(drop=True)
    log_event(config.SOURCE_AMFI_OFFICIAL, AMFI_NAV_URL, "OK" if len(df) else "EMPTY", 200,
              f"schemes={len(df)} nav_date={nav_date}")
    return df


def find_etf_nav(amfi_df: pd.DataFrame, scheme_name_hint: str) -> dict:
    """Naam ke hint se ETF ka NAV dhoondo (exact match, else partial)."""
    if amfi_df is None or len(amfi_df) == 0:
        return {"available": False, "reason": "AMFI feed unavailable"}
    names = amfi_df["scheme_name"].astype(str).str.lower()
    hint = scheme_name_hint.lower()
    exact = amfi_df[names == hint]
    if len(exact) == 0:
        key = hint.replace("etf", "").strip()
        partial = amfi_df[names.str.contains(key[:28], regex=False, na=False)]
        if len(partial) == 0:
            return {"available": False, "reason": f"no AMFI scheme matched '{scheme_name_hint}'"}
        exact = partial
    row = exact.iloc[0]
    return {"available": True, "scheme_name": str(row["scheme_name"]),
            "nav": float(row["nav"]), "nav_date": str(row["nav_date"]),
            "scheme_code": str(row["scheme_code"]),
            "source": config.SOURCE_AMFI_OFFICIAL}
