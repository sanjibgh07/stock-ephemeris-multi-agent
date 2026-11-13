"""run.py - EK CLICK STARTER (double-click karo, bas).

Ye script:
  1) Check karta hai ki C: drive use nahi ho rahi (aur D: / E: theek hai)
  2) Jaroori Python libraries check karta hai (na ho to install karta hai)
  3) state/project_state.json padhta hai -> "Resuming from Phase X"
  4) Data book khaali ho to bana leta hai (official sources se)
  5) FastAPI server chalu karta hai  ->  http://localhost:8000
  6) Browser khud khol deta hai

Chalane ka tarika (how to run):
    python run.py              (normal - data bana kar server chalu)
    python run.py --no-build   (sirf server chalu karo)
    python run.py --rebuild    (data dobara banao)
    python run.py --check      (sirf checks karo, server nahi)
    python run.py --test       (PHASE-5 verification tests chalao)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REQUIRED_MODULES = {
    "pandas": "pandas", "numpy": "numpy", "pyarrow": "pyarrow",
    "swisseph": "pyswisseph", "requests": "requests",
    "fastapi": "fastapi", "uvicorn": "uvicorn",
}


def banner(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72, flush=True)


def check_drive() -> dict:
    """C: drive par kuch nahi likhna - ye rule sabse pehle check hota hai."""
    banner("STEP 1 - DRIVE CHECK")
    from app import config

    info = config.disk_report()
    print(f"Project root : {config.PROJECT_ROOT}")
    print(f"Drive        : {info['drive']}:  (free {info['free_gb']} GB)")
    if config.drive_letter(config.PROJECT_ROOT) == "C":
        print("ABORT: PROJECT_ROOT is on C: drive. Edit .env and use D:/ or E:/.")
        raise SystemExit(2)
    print(f"OK: nothing will be stored on C:. All caches/temp go to {config.CACHE_DIR}")
    if info["warning"]:
        print("WARNING:", info["warning"])
    return info


def check_requirements(auto_install: bool = True) -> bool:
    """Requirements check; missing ho to install karo (pip cache project me)."""
    banner("STEP 2 - PYTHON LIBRARY CHECK")
    from app import config

    missing = [pip for mod, pip in REQUIRED_MODULES.items()
               if importlib.util.find_spec(mod) is None]
    if not missing:
        print("OK: all required libraries are already installed.")
        return True
    print("Missing libraries:", ", ".join(missing))
    req = PROJECT_ROOT / "requirements.txt"
    if not auto_install or not req.exists():
        print("Install them with:  python -m pip install -r requirements.txt")
        return False
    env = dict(os.environ)
    env["PIP_CACHE_DIR"] = str(config.CACHE_DIR / "pip")
    print("Installing (cache stays inside the project, never on C:) ...")
    return subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(req)],
                           env=env) == 0


def check_state() -> dict:
    banner("STEP 3 - RESUME CHECK")
    from app import state_manager
    from db import database

    database.init_db()
    state = state_manager.load_state()
    print("Resume:", state_manager.resume_message())
    print("Current phase   :", state.get("current_phase"))
    print("Completed steps :", len(state.get("completed_steps", [])))
    print("Last stock data :", state.get("last_data_date_stock"))
    print("Last ephemeris  :", state.get("last_data_date_ephemeris"))
    if state.get("source_failures"):
        print("Logged source failures (data never invented):")
        for f in state["source_failures"][:6]:
            print(f"   - {f.get('source')}: {f.get('status')} ({str(f.get('message', ''))[:70]})")
    return state


def data_is_ready() -> bool:
    from app.orchestrator import Orchestrator

    ready = Orchestrator.data_ready()
    print(f"Data check -> stock rows={ready['stock_rows']} "
          f"({ready['stock_symbols']} symbols), ephemeris rows={ready['ephemeris_rows']}")
    return bool(ready["ready"])


def build_data(skip_official_indices: bool = False) -> None:
    banner("STEP 4 - BUILDING THE DATA BOOKS (official sources only)")
    from app import config
    from data import pipeline

    def _p(msg: str) -> None:
        print(f"   [{config.now_ist().strftime('%H:%M:%S')}] {msg}", flush=True)

    summary = pipeline.build_all(skip_official_indices=skip_official_indices, progress=_p)
    print("\nBuild summary (short):")
    print(json.dumps({
        "resume_message": summary.get("resume_message"),
        "index_datasets": summary.get("stock_book", {}).get("index_datasets"),
        "missing_dates_recorded": summary.get("stock_book", {}).get("missing_dates_recorded"),
        "ephemeris_rows": summary.get("ephemeris_book", {}).get("rows"),
        "disk_after": summary.get("disk_after"),
    }, indent=2, default=str))
    print("Disk after build:", config.disk_report())


def run_tests() -> int:
    banner("PHASE 5 - VERIFICATION TESTS")
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-v", "tests/test_verification.py"],
        cwd=str(PROJECT_ROOT))


def serve(port: int, host: str, open_browser: bool = True, reload: bool = False) -> None:
    banner("STEP 5 - STARTING THE WEB APP")
    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"
    print(f"Serving at {url}")
    print("Press Ctrl+C in this window to stop the server.")
    if open_browser:
        def _open() -> None:
            time.sleep(2.0)
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_open, daemon=True).start()
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=reload, log_level="info")


def main() -> int:
    parser = argparse.ArgumentParser(description="One-click starter for the research system.")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--no-build", action="store_true", help="skip data build")
    parser.add_argument("--rebuild", action="store_true", help="rebuild the data book")
    parser.add_argument("--skip-official-indices", action="store_true")
    parser.add_argument("--check", action="store_true", help="only run checks, no server")
    parser.add_argument("--test", action="store_true", help="run the PHASE 5 tests")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    banner("INDIAN STOCK + EPHEMERIS MULTI-AGENT RESEARCH SYSTEM")
    check_drive()
    from app import config

    config.bootstrap_environment()
    if not check_requirements(auto_install=True):
        return 3
    check_state()
    if args.test:
        return run_tests()

    if not args.no_build:
        if args.rebuild or not data_is_ready():
            build_data(skip_official_indices=args.skip_official_indices)
        else:
            print("Data book already present. Use --rebuild to force a fresh build.")

    if args.check:
        print("\nChecks finished. Start the app with:  python run.py --no-build")
        return 0

    serve(port=args.port or config.PORT, host=args.host or config.HOST,
          open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user.")

