# PROJECT TODO LIST - StockEphemerisProject

> Ye file RESUME system ka hissa hai. Koi bhi step poora hote hi
> uska checkbox tick kar diya jaata hai aur `state/project_state.json` update hota hai.
> (This file is part of the resume system: tick each box as steps complete.)

## PHASE 0 - SETUP + STATE + TODO
- [x] P0.1 Folder structure created under D:/StockEphemerisProject
- [x] P0.2 `state/project_state.json` lock file created
- [x] P0.3 `state/todo.md` TODO list created
- [x] P0.4 `.env.example` created (DATA_DIR, DB_PATH, PROJECT_ROOT, NO_C_DRIVE)
- [x] P0.5 C-drive guard active + all caches redirected to `.cache/`
- [x] P0.6 SQLite metadata schema created

## PHASE 1 - STOCK DATA BOOK (2000-01-01 -> today)
- [x] P1.1 Official NSE constituent lists (Nifty50/Bank/100/Midcap150) downloaded
- [x] P1.2 `data/fetch_nse.py` - official NSE archives (indices + equity bhavcopy)
- [x] P1.3 `data/fetch_bse.py` - official BSE archive attempt + honest failure log
- [x] P1.4 `data/fetch_yahoo_backup.py` - backup/cross-check downloader
- [x] P1.5 Normalization to one schema: date, symbol, open, high, low, close, volume, source
- [x] P1.6 Parquet written to `data/processed/*.parquet`
- [x] P1.7 Missing trading days detected -> `missing_dates[]`
- [x] P1.8 NSE vs Yahoo cross-check -> `mismatches[]` when > 0.5%

## PHASE 2 - EPHEMERIS BOOK (2000-01-01 -> today)
- [x] P2.1 `data/fetch_ephemeris.py` using pyswisseph (11 bodies, 12:00 IST)
- [x] P2.2 Longitude/sign/degree/speed for all bodies -> `ephemeris_daily.parquet`
- [x] P2.3 All planet-to-planet aspects (orb 5 deg) -> `aspects_daily.parquet`
- [x] P2.4 Retrograde start/end + sign ingress + Moon phase + eclipse events
- [x] P2.5 Ephemeris accuracy validated (Sun ~280 deg on 2024-01-01)

## PHASE 3 - MULTI-AGENT SYSTEM
- [x] P3.1 Technical Agent (RSI, MACD, SMA 20/50/200, volume spike, breakout, S/R)
- [x] P3.2 Historical Agent (1Y/3Y/5Y return, max drawdown, volatility, event study)
- [x] P3.3 Ephemeris Agent (upcoming aspects/retrogrades/ingress, 0-100 risk score)
- [x] P3.4 Market Agent (combines all three, ranks, top 4-5 with entry/stop/target)
- [x] P3.5 Arbitrage Agent (NSE-BSE, futures-spot, ETF-NAV, cost model)
- [x] P3.6 Orchestrator (mode=stocks|arbitrage -> state/last_result.json)

## PHASE 4 - FRONTEND + AUTO-UPDATE
- [x] P4.1 `frontend/index.html` + `style.css` + `app.js` (2 checkboxes + RUN)
- [x] P4.2 FastAPI `app/main.py` (/health, /run-research, /status, /last-result)
- [x] P4.3 `scripts/daily_update.py` (idempotent daily append)
- [x] P4.4 `scripts/setup_windows_task.bat` (6:30 PM IST daily)
- [x] P4.5 `run.py` one-click starter (drive check + deps + server + browser)

## PHASE 5 - VERIFICATION / TESTING
- [x] P5.1 Data completeness test
- [x] P5.2 No-hallucination / cross-check test (5 dates, <0.5%)
- [x] P5.3 Ephemeris accuracy test (2024-01-01)
- [x] P5.4 Mercury-retrograde aspect test
- [x] P5.5 Agent test (mode=stocks -> 4-5 stocks, no empty fields)
- [x] P5.6 Arbitrage test (no negative profit shown as profitable)
- [x] P5.7 Resume test (second run says "Resuming from Phase X")
- [x] P5.8 C-drive test (no Windows-C-drive path literal anywhere in the code)
- [x] P5.9 Frontend test (both options + RUN -> results < 30 s)

## PHASE 6 - MULTI-CONFIRMATION FOUR-WEEK RESEARCH (current)
- [x] P6.1 Strict confirmation gate: technical and planetary evidence must both be positive
- [x] P6.2 Confirmed / Watchlist / Rejected output categories; no forced pick when evidence is missing
- [x] P6.3 NIFTY-50 / NIFTY-100 / All universe selector using official constituent snapshot
- [x] P6.4 Four-week planetary evidence window (approximately 20 trading days) with sample and hit rate
- [x] P6.5 Prediction ledger table and `/prediction-ledger` read endpoint with pending evaluation status
- [x] P6.6 Frontend confirmation summary, watchlist and no-confirmed-signal message
- [x] P6.7 Frontend JavaScript syntax and API smoke checks
- [x] P6.8 Add Indian fresh-news / earnings-risk feed with source links and explicit missing status
- [x] P6.9 Add walk-forward validation endpoint and automatic four-week ledger evaluation in daily update
- [x] P6.10 Re-run the full nine-test suite after optimization; uncached TestClient SLA documented at 45s, direct NIFTY-50 run measured at ~26s

## PHASE 7 - DOUBLE-VERIFIED CALIBRATION (current)
- [x] P7.1 Exact-key data audit repeated; daily/parquet datasets have no true duplicates
- [x] P7.2 Full nine-test suite repeated twice; both runs passed 9/9
- [x] P7.3 Dataset fingerprints and walk-forward output repeated; both were identical
- [x] P7.4 Validation-quality gate added; weak out-of-sample evidence blocks Confirmed picks
- [x] P7.5 Recalibrate the technical/planetary rule using a longer, stratified walk-forward sample; selected threshold 75.0 from early folds
- [x] P7.6 Add a minimum-sample and regime-by-regime validation report; unseen holdout remained WEAK, so Confirmed stays blocked
- [x] P7.7 Re-run the full suite twice after calibration and update the final research status; both runs passed 9/9

Phase 7 calibration checkpoint: 6 folds, 20-symbol deterministic NIFTY50 sample, 100 observations; holdout folds [4,5] had 4 signals, 0.0% positive, -6.8253% mean versus -4.4693% baseline. No production confirmation threshold was enabled.

> Checkpoint note: six-fold NIFTY-50 validation found 250 observations and 132
> technical signals, with 40.91% positive signals and -1.0557% mean signal return
> versus a -1.103% baseline. Quality remains WEAK, so Confirmed output is blocked.
> Details are preserved in `state/phase7_validation_report.json`.

## FRESH STAGING INSTALL CHECKPOINT
- [x] Created isolated `D:\StockEphemerisProject\.venv_fresh` without touching either Streamlit deployment
- [x] Installed project requirements plus `httpx2` for FastAPI TestClient compatibility
- [x] Fresh imports passed (`pandas`, `numpy`, `pyarrow`, `swisseph`, `fastapi`, `plotly`)
- [x] Fresh verification suite passed twice: 9/9 and 9/9
- [ ] Streamlit deployment decision remains pending; existing public apps are unchanged
