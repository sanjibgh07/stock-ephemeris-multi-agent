"""Fresh Indian corporate-disclosure risk checks.

Primary source is NSE's public corporate-announcements endpoint.  This agent
does not turn a headline into a price prediction; it only reports disclosures
and blocks confirmation when a material risk filing is visible.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import urljoin

from app import config
from data import http_util

NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
NSE_API = "https://www.nseindia.com/api/corporate-announcements"

HIGH_RISK = ("fraud", "default", "bankrupt", "insolvency", "investigation",
             "penalty", "legal", "litigation", "rating downgrade", "downgrade",
             "resignation", "suspension", "pledge", "delisting", "forensic")
WATCH_RISK = ("financial results", "results", "earnings", "board meeting",
              "acquisition", "merger", "order", "contract", "credit rating",
              "capex", "fund raising", "fundraising", "dividend", "management")


def _text(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _classify(subject: str, detail: str) -> tuple[str, list[str]]:
    text = f"{subject} {detail}".lower()
    high = [word for word in HIGH_RISK if word in text]
    if high:
        return "HIGH", sorted(set(high))
    watch = [word for word in WATCH_RISK if word in text]
    return ("WATCH", sorted(set(watch))) if watch else ("INFO", [])


class NewsAgent:
    name = "news_agent"

    def run(self, symbols: list[str], days_back: int = 3) -> dict:
        wanted = {str(s).upper().strip() for s in (symbols or [])}
        today = config.today_ist()
        start = today - dt.timedelta(days=max(1, int(days_back)))
        params = {"index": "equities", "from_date": start.strftime("%d-%m-%Y"),
                  "to_date": today.strftime("%d-%m-%Y")}
        session = http_util.browser_session(referer=NSE_PAGE)
        # NSE normally requires a page visit before the JSON request sets its cookies.
        try:
            session.get(NSE_PAGE, timeout=min(5, config.HTTP_TIMEOUT_SECONDS))
        except Exception:  # noqa: BLE001
            pass
        response = http_util.get_with_retry(
            NSE_API, session=session, source="nse_corporate_announcements",
            params=params, headers={"Accept": "application/json, text/plain, */*"},
            timeout=min(8, config.HTTP_TIMEOUT_SECONDS), max_retries=1, log_ok=True)
        if response is None:
            return {"available": False, "status": "NOT_AVAILABLE", "items": [],
                    "per_symbol": {},
                    "reason": "NSE corporate-announcements endpoint was blocked or unreachable.",
                    "source": NSE_API, "as_of": today.isoformat()}
        try:
            payload = response.json()
        except ValueError:
            return {"available": False, "status": "NOT_AVAILABLE", "items": [],
                    "per_symbol": {}, "reason": "NSE returned a non-JSON response.",
                    "source": NSE_API, "as_of": today.isoformat()}
        raw = payload if isinstance(payload, list) else payload.get("data", [])
        if not isinstance(raw, list):
            raw = []
        items = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            symbol = _text(item, "symbol", "sym") .upper()
            if wanted and symbol not in wanted:
                continue
            subject = _text(item, "subject", "sm_name", "category")
            detail = _text(item, "desc", "details", "text")
            level, tags = _classify(subject, detail)
            attachment = _text(item, "attchmntFile", "attachment_url", "attachmentUrl")
            link = urljoin("https://www.nseindia.com", attachment) if attachment else NSE_PAGE
            items.append({"symbol": symbol or config.MISSING,
                          "date": _text(item, "dt", "date", "broadcastDate"),
                          "subject": subject or config.MISSING,
                          "detail": detail[:500] if detail else config.MISSING,
                          "risk_level": level, "risk_tags": tags,
                          "source": "nse_official", "source_url": link})
        per_symbol = {}
        for symbol in wanted:
            matches = [x for x in items if x["symbol"] == symbol]
            high = [x for x in matches if x["risk_level"] == "HIGH"]
            per_symbol[symbol] = {
                "status": "RISK_FLAG" if high else "CLEAR",
                "high_risk_count": len(high), "item_count": len(matches),
                "items": matches[:10],
            }
        return {"available": True, "status": "OK", "items": items,
                "per_symbol": per_symbol, "source": NSE_API,
                "as_of": today.isoformat(), "window_days": days_back,
                "note": "Disclosures are risk context, not a price prediction."}
