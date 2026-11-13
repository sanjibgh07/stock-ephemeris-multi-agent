"""tests/conftest.py - pytest setup (project root ko import path me daalo)."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import config  # noqa: E402

config.bootstrap_environment()
try:
    from db import database  # noqa: E402

    database.init_db()
except Exception:  # noqa: BLE001
    pass
