"""Central configuration for StockEphemerisProject.

Non-coder ke liye (in simple words):
  * Ye file decide karti hai ki SAARA data kahan save hoga.
  * Rule 1: C: drive par kuch bhi nahi likhna. (NEVER write to C: drive.)
  * Rule 2: Sab kuch PROJECT_ROOT ke andar hi rehta hai.
  * Rule 3: Python ka temp/pip cache bhi project ke andar chala jaata hai.

Drive badalna ho to sirf ".env" file me PROJECT_ROOT badlo.
(To change the drive, just edit PROJECT_ROOT in the .env file.)
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 1) Find PROJECT_ROOT
#    Priority:  env var PROJECT_ROOT  ->  .env file  ->  built-in default D:/
# ---------------------------------------------------------------------------
DEFAULT_PROJECT_ROOT = (
    "D:/StockEphemerisProject"
    if os.name == "nt"
    else str(Path(__file__).resolve().parent.parent)
)
_ENV_FILE_NAME = ".env"


def _parse_dotenv(path: Path) -> dict:
    """Chhota sa .env parser (koi extra library nahi chahiye)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def _apply_dotenv(path: Path) -> None:
    for key, val in _parse_dotenv(path).items():
        if val != "":
            # Real environment variables always win over the .env file.
            os.environ.setdefault(key, val)


# .env may live next to this package (project root) or in the current folder.
for _cand in (Path(__file__).resolve().parent.parent, Path.cwd()):
    _apply_dotenv(_cand / _ENV_FILE_NAME)

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT") or DEFAULT_PROJECT_ROOT).resolve()

# Re-read the .env that actually belongs to the resolved PROJECT_ROOT.
_apply_dotenv(PROJECT_ROOT / _ENV_FILE_NAME)

# ---------------------------------------------------------------------------
# 2) C-DRIVE PROTECTION  (sabse zaroori rule / the most important rule)
# ---------------------------------------------------------------------------
NO_C_DRIVE = os.environ.get("NO_C_DRIVE", "True").strip().lower() in {"1", "true", "yes", "y"}


class CDriveViolation(RuntimeError):
    """Raised when any storage path would land on the C: drive."""


def drive_letter(path: Path) -> str:
    return path.drive.replace(":", "").replace("\\", "").replace("/", "").upper()


def assert_not_c_drive(path: Path) -> Path:
    """Guard: C: drive par kabhi mat likho. (Never store anything on C:.)"""
    if NO_C_DRIVE and drive_letter(path) == "C":
        raise CDriveViolation(
            f"BLOCKED: '{path}' C: drive par hai. NO_C_DRIVE=True hai.\n"
            f".env file me PROJECT_ROOT ko D:/StockEphemerisProject (ya E:/, F:/) "
            f"rakho. / C: drive storage is forbidden - edit PROJECT_ROOT in .env."
        )
    return path


# ---------------------------------------------------------------------------
# 3) Derived paths (structure exactly as per project spec)
# ---------------------------------------------------------------------------
APP_DIR = PROJECT_ROOT / "app"
AGENTS_DIR = APP_DIR / "agents"
DATA_PKG_DIR = PROJECT_ROOT / "data"          # python fetcher modules live here
RAW_DIR = DATA_PKG_DIR / "raw"                # downloaded official CSVs/zips
PROCESSED_DIR = DATA_PKG_DIR / "processed"    # parquet OHLC book
EPHEMERIS_DIR = DATA_PKG_DIR / "ephemeris"    # parquet ephemeris book
DB_DIR = PROJECT_ROOT / "db"
DB_PATH = Path(os.environ.get("DB_PATH") or (DB_DIR / "stockephemeris.sqlite"))
FRONTEND_DIR = PROJECT_ROOT / "frontend"
STATE_DIR = PROJECT_ROOT / "state"
LOGS_DIR = PROJECT_ROOT / "logs"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
TESTS_DIR = PROJECT_ROOT / "tests"
CACHE_DIR = Path(os.environ.get("CACHE_DIR") or (PROJECT_ROOT / ".cache"))

PROJECT_STATE_PATH = STATE_DIR / "project_state.json"
TODO_PATH = STATE_DIR / "todo.md"
LAST_RESULT_PATH = STATE_DIR / "last_result.json"
DAILY_RUN_LOG = LOGS_DIR / "daily_run.jsonl"
FETCH_LOG_PATH = LOGS_DIR / "fetch_log.jsonl"

# Built-in source registry - ONLY these legitimate sources are allowed.
SOURCE_NSE_OFFICIAL = "nse_official"
SOURCE_BSE_OFFICIAL = "bse_official"
SOURCE_YAHOO_BACKUP = "yahoo_backup"
SOURCE_AMFI_OFFICIAL = "amfi_official"
SOURCE_SWISSEPH = "swisseph"

ALL_STORAGE_PATHS = [
    PROJECT_ROOT, APP_DIR, AGENTS_DIR, DATA_PKG_DIR, RAW_DIR, PROCESSED_DIR,
    EPHEMERIS_DIR, DB_DIR, DB_PATH, FRONTEND_DIR, STATE_DIR, LOGS_DIR,
    SCRIPTS_DIR, TESTS_DIR, CACHE_DIR,
]
for _p in ALL_STORAGE_PATHS:
    assert_not_c_drive(_p)

# ---------------------------------------------------------------------------
# 4) Settings from .env
# ---------------------------------------------------------------------------
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")
HISTORY_START_DATE = os.environ.get("HISTORY_START_DATE", "2000-01-01")
EPHEMERIS_START_DATE = os.environ.get("EPHEMERIS_START_DATE", "2000-01-01")
EPHEMERIS_HOUR_IST = float(os.environ.get("EPHEMERIS_HOUR_IST", "12.0"))
ASPECT_ORB_DEGREES = float(os.environ.get("ASPECT_ORB_DEGREES", "5.0"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

HTTP_TIMEOUT_SECONDS = int(os.environ.get("HTTP_TIMEOUT_SECONDS", "45"))
HTTP_MAX_RETRIES = int(os.environ.get("HTTP_MAX_RETRIES", "3"))
HTTP_WORKERS = int(os.environ.get("HTTP_WORKERS", "8"))
OFFICIAL_BACKFILL_START_YEAR = int(os.environ.get("OFFICIAL_BACKFILL_START_YEAR", "2013"))

ARB_COST_BPS = {
    "brokerage": float(os.environ.get("ARB_BROKERAGE_BPS", "3.0")),
    "stt": float(os.environ.get("ARB_STT_BPS", "2.5")),
    "exchange": float(os.environ.get("ARB_EXCHANGE_BPS", "0.35")),
    "gst": float(os.environ.get("ARB_GST_BPS", "0.63")),
    "stamp": float(os.environ.get("ARB_STAMP_BPS", "0.3")),
}
ARB_MIN_PROFITABLE_SPREAD_PCT = float(os.environ.get("ARB_MIN_PROFITABLE_SPREAD_PCT", "0.3"))

# ---------------------------------------------------------------------------
# 5) Helpers used everywhere
# ---------------------------------------------------------------------------
MISSING = "MISSING"          # std token: real data absent -> write MISSING, never 0
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30), name="IST")


def now_ist() -> _dt.datetime:
    return _dt.datetime.now(tz=IST)


def today_ist() -> _dt.date:
    return now_ist().date()


def ensure_dirs() -> None:
    """Saare folders bana do (idempotent / safe to run many times)."""
    for p in ALL_STORAGE_PATHS:
        assert_not_c_drive(p)
        if p.suffix:            # it points to a file
            p.parent.mkdir(parents=True, exist_ok=True)
        else:
            p.mkdir(parents=True, exist_ok=True)


def bootstrap_environment() -> None:
    """Redirect every cache/temp into the project folder, then create dirs.

    Ye Python ke temp files ko C: se hataata hai. (Moves all temp/cache away
    from the C: drive and into <PROJECT_ROOT>/.cache/.)
    """
    ensure_dirs()
    tmp_dir = CACHE_DIR / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = str(tmp_dir)
    os.environ["TEMP"] = str(tmp_dir)
    os.environ["TMPDIR"] = str(tmp_dir)
    os.environ["PIP_CACHE_DIR"] = str(CACHE_DIR / "pip")
    os.environ["MPLCONFIGDIR"] = str(CACHE_DIR / "matplotlib")
    os.environ["YF_CACHE_DIR"] = str(CACHE_DIR / "yfinance")
    try:
        import tempfile  # noqa: PLC0415

        tempfile.tempdir = str(tmp_dir)
    except Exception:  # noqa: BLE001
        pass


def disk_report(path: Path | None = None) -> dict:
    """Free-space report - warns when less than 2 GB remains."""
    import shutil  # noqa: PLC0415

    target = Path(path or PROJECT_ROOT)
    usage = shutil.disk_usage(str(target))
    free_gb = usage.free / (1024 ** 3)
    return {
        "path": str(target),
        "drive": drive_letter(target),
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(free_gb, 2),
        "warning": "LOW SPACE - keep at least 2 GB free!" if free_gb < 2.0 else None,
    }


def json_safe(value):
    """NaN/inf ko 'MISSING' bana do. (Never leak NaN as if it were a number.)"""
    import math  # noqa: PLC0415

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return MISSING
        return value
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value
