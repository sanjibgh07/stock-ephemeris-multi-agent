"""data/http_util.py - ek hi jagah se saare network calls.

Simple English:
  * Government/exchange websites browser jaisa header maangte hain, warna 403.
    Is file me browser jaisa header set kiya gaya hai.
  * Har fail hone par hum LOG likhte hain - data INVENT nahi karte.
  * (Every failure is logged; we never invent data.)
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from app import config

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def browser_session(referer: str | None = None) -> requests.Session:
    """Browser jaisa session (headers + cookie support)."""
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    if referer:
        session.headers["Referer"] = referer
    return session


# ---------------------------------------------------------------------------
# Logging (jsonl + sqlite). No data is ever fabricated here.
# ---------------------------------------------------------------------------
def log_event(source: str, endpoint: str, status: str, http_status: int | None = None,
              message: str = "") -> dict:
    """Source health ko logs/fetch_log.jsonl + SQLite me likho."""
    record = {
        "ist": config.now_ist().isoformat(timespec="seconds"),
        "source": source,
        "endpoint": endpoint,
        "status": status,
        "http_status": http_status,
        "message": (message or "")[:500],
    }
    try:
        config.assert_not_c_drive(config.FETCH_LOG_PATH)
        config.FETCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with config.FETCH_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    try:
        from db import database  # noqa: PLC0415

        database.log_source(source, endpoint, status, http_status, message)
    except Exception:  # noqa: BLE001
        pass
    return record


def get_with_retry(url: str, session: requests.Session | None = None, *,
                   source: str, headers: dict | None = None, params: dict | None = None,
                   timeout: int | None = None, max_retries: int | None = None,
                   log_ok: bool = False) -> requests.Response | None:
    """GET with retries. Fail hone par None return + LOG (kabhi fake data nahi)."""
    session = session or browser_session()
    timeout = timeout or config.HTTP_TIMEOUT_SECONDS
    attempts = max_retries or config.HTTP_MAX_RETRIES
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            resp = session.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code == 200:
                if log_ok:
                    log_event(source, url, "OK", 200, f"bytes={len(resp.content)}")
                return resp
            last_error = f"HTTP {resp.status_code}"
            status = "BLOCKED" if resp.status_code in (401, 403, 405) else "FAILED"
            log_event(source, url, status, resp.status_code,
                      f"attempt {attempt}/{attempts}: {last_error}")
            if resp.status_code in (401, 403, 404, 405):
                return None  # no point retrying a hard block
        except requests.exceptions.RequestException as exc:  # noqa: PERF203
            last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            log_event(source, url, "FAILED", None, f"attempt {attempt}/{attempts}: {last_error}")
        time.sleep(min(1.5 * attempt, 5.0))
    return None


def post_with_retry(url: str, session: requests.Session | None = None, *,
                    source: str, json_body: Any = None, data: Any = None,
                    headers: dict | None = None, timeout: int | None = None,
                    max_retries: int | None = None) -> requests.Response | None:
    """POST with retries + logging."""
    session = session or browser_session()
    timeout = timeout or config.HTTP_TIMEOUT_SECONDS
    attempts = max_retries or config.HTTP_MAX_RETRIES
    for attempt in range(1, attempts + 1):
        try:
            resp = session.post(url, json=json_body, data=data, headers=headers,
                                timeout=timeout)
            if resp.status_code == 200:
                return resp
            status = "BLOCKED" if resp.status_code in (401, 403, 405) else "FAILED"
            log_event(source, url, status, resp.status_code,
                      f"attempt {attempt}/{attempts}: HTTP {resp.status_code}")
            if resp.status_code in (401, 403, 404, 405):
                return None
        except requests.exceptions.RequestException as exc:
            log_event(source, url, "FAILED", None,
                      f"attempt {attempt}/{attempts}: {type(exc).__name__}: {str(exc)[:160]}")
        time.sleep(min(1.5 * attempt, 5.0))
    return None


def looks_like_html(payload: bytes | str) -> bool:
    """Agar response JSON/CSV ki jagah website ka HTML hai to True.

    Ye check zaroori hai kyunki exchange websites galat URL par apna
    HTML page bhej deti hain - usse data samajhna galat hoga.
    """
    head = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
    head = head.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")
