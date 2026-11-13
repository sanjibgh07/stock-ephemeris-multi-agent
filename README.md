# Indian Stock + Ephemeris Multi-Agent Research System

A complete, working research system that combines **official Indian market data** with
**Swiss Ephemeris planetary data**, and runs 5 research agents over both books.

> **Disclaimer:** This is research only, not investment advice.
> Ye sirf research hai - SEBI registered advisor se salah lene ke baad hi trade karein.

---

## 1. What this system does (simple words / aasan bhasha)

You tick ONE option in a simple web page and press **RUN**:

* **3-4 Week Stocks** - 4 to 5 stock names worth studying for the next 3-4 weeks, each with
  a reason, an entry zone, a stop-loss zone and a target zone.
* **Arbitrage** - NSE vs BSE price differences, Futures vs Spot, and ETF vs NAV, with
  spread %, estimated cost, expiry, and a clear **NOT PROFITABLE** verdict when the spread
  does not beat the cost.

It also shows an **Ephemeris calendar** for the next 3-4 weeks (retrogrades, ingresses,
new/full moons, eclipses) and a historical **correlation table** (how much the market moved
when a given planetary condition was active). It never predicts prices.

---

## 2. How to run (double-click)

```
Double-click:   D:\StockEphemerisProject\run.py
```

`run.py` will, in order:

1. **Check the drive** - refuse to run if storage would land on `C:`.
2. **Check Python libraries** - install `requirements.txt` if something is missing
   (pip cache goes to `<project>/.cache/pip`, never `C:`).
3. **Print the resume line** - e.g. `Resuming from Phase PHASE_3_MULTI_AGENT_SYSTEM ...`
   so a restart never loses progress.
4. **Build the data books** if they are empty (first run only).
5. **Start the web app** at <http://localhost:8000> and open your browser.

Useful options:

```bat
python run.py                 :: normal run (build if needed, then serve)
python run.py --no-build      :: just start the server (data already built)
python run.py --rebuild       :: rebuild the data books from scratch
python run.py --check         :: run the checks and exit (no server)
python run.py --test          :: run the PHASE 5 verification tests
python run.py --port 9000     :: use a different port
```

---

## 3. How to change the drive (if D: is not what you want)

1. Copy `.env.example` to `.env` (same folder).
2. Edit one line:

```ini
PROJECT_ROOT=E:/StockEphemerisProject
NO_C_DRIVE=True
```

3. Move/copy the whole project folder to that drive and run `python run.py`.

`NO_C_DRIVE=True` means the app **aborts** instead of silently writing to `C:`.
Bulk data lives in Parquet + SQLite only (no Docker, no Postgres), so the footprint stays small.

---

## 4. How to update the data manually

```bat
python -m data.pipeline                     :: full rebuild (idempotent)
python scripts\daily_update.py              :: daily incremental update (idempotent)
python scripts\daily_update.py --days-back 30
python -m data.validate_data                :: write logs/validation_report.json
```

Running either of them twice does **not** create duplicates.

### Automatic daily update (Windows Task Scheduler, 6:30 PM IST)

Double-click `scripts\setup_windows_task.bat` (once). It creates a task named
`StockEphemerisDailyUpdate` that runs `scripts\daily_update.py` every day at 18:30 and
appends output to `logs\task_daily.log`.

```bat
schtasks /Query  /TN "StockEphemerisDailyUpdate" /V /FO LIST    :: check
schtasks /Run    /TN "StockEphemerisDailyUpdate"                :: run now (test)
schtasks /Delete /TN "StockEphemerisDailyUpdate" /F             :: remove
```


---

## 5. Data sources - what is used, and what is honestly missing

Every row carries a `source` column so you can always tell where it came from.
**Nothing is ever invented. A missing value stays `MISSING`, never `0`.**

| Priority | Source | Endpoint / method | Verified status |
|---|---|---|---|
| 1 (official) | NSE index archive | `nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv` | **OK**, 2013 → today |
| 1 (official) | NSE equity bhavcopy | `nsearchives.nseindia.com/content/historical/EQUITIES/.../cmDDMONYYYYbhav.csv.zip`, and after 2024-07-08 `content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` | **OK**, 2000-01-03 → today |
| 1 (official) | NSE F&O bhavcopy (futures) | `nsearchives.nseindia.com/content/historical/DERIVATIVES/...` | best effort, logged if absent |
| 1 (official) | NIFTY index constituents | `niftyindices.com/IndexConstituent/ind_nifty*.csv` | **OK** (50 / 100 / 14 / 150 rows) |
| 1 (official) | AMFI NAV feed (ETF vs NAV) | `amfiindia.com/spages/NAVAll.txt` | attempted each run, honest log |
| 2 (official) | BSE index archive | `api.bseindia.com/BseIndiaAPI/api/IndexArchDaily/w` | **BLOCKED** by the BSE WAF (Akamai 403) from this network - logged in `state.source_failures` |
| 3 (backup only) | Yahoo Finance | `query1/query2.finance.yahoo.com/v8/finance/chart/...` | **OK**, labelled `yahoo_backup`, used for cross-check and the Sensex / BSE-side history |
| - | Swiss Ephemeris | `pyswisseph` (JPL DE431/DE441 based) | **OK**, computed offline |
| - | astro.com / astro-seek | manual reference only | used to cross-check the method, not for bulk data |

### Genuine coverage (measured on this machine, not claimed)

| Dataset | Coverage | Rows | Source |
|---|---|---|---|
| `sensex.parquet` | 2000-01-03 → 2026-11-09 (**6,620** trading days) | 6,620 | `yahoo_backup` (BSE official blocked) |
| `nifty100_stocks.parquet` | 2000-01-03 → today, per stock (listing-date aware) | **505,996** | `yahoo_backup` + `nse_official` |
| `arbitrage_quotes.parquet` | 2000 → today, BSE `.BO` side | 258,837 | `yahoo_backup` |
| `nifty50.parquet` | 2007-09-17 → 2026-11-11 (4,705 days) | 4,705 | `yahoo_backup` + `nse_official` |
| `banknifty.parquet` | 2007-09-17 → 2026-11-11 | 4,716 | `yahoo_backup` + `nse_official` |
| `nifty100_index.parquet` | 2005-11-30 → 2026-11-11 | 5,161 | `yahoo_backup` + `nse_official` |
| `midcap.parquet` | 2016-04-01 → 2026-11-11 | 2,604 | `nse_official` |
| `ephemeris_daily.parquet` | 2000-01-01 → today, 12 bodies/day | **117,756** | `swisseph` |
| `aspects_daily.parquet` | 2000-01-01 → today, orb 5° | **149,674** | `swisseph` |
| `ephemeris_events.parquet` | retrogrades, ingresses, new/full moons, eclipses | **6,936** | `swisseph` |

Official NSE index files: **3,369 daily files fetched successfully**, 231 were genuine
non-trading days (404). Official NSE equity bhavcopy: 228 files OK. Total on-disk footprint
≈ **106 MB** (75 MB of the raw download cache plus 29 MB of Parquet).

Two coverage notes that are *real facts*, not bugs:
* `midcap` starts 2016-04-01 because the **NIFTY Midcap 150 index itself was launched in
  April 2016** - the earlier official files simply do not contain it.
* NIFTY-50 index values before 2007-09-17 are unavailable (see the gap note below).

Exact counts for your machine are printed by `python -m data.validate_data` and shown in the
frontend under **System status → Data book details**.

**Known, documented gap:** NIFTY-50 *index* values before 2007-09-17 are not available from
any source reachable from this network (the official NSE all-indices archive begins in 2013
and Yahoo's `^NSEI` begins 2007-09-17). This gap is **reported, never filled with invented
numbers**. `SENSEX` and the individual stocks do carry 2000-01-03 onwards.

---

## 6. How to interpret the results

**Stocks option**

* `combined score` = 0.45 x technical + 0.35 x historical + 0.20 x ephemeris-regime (0-100).
* `entry zone` = close ± 0.5 x ATR(14) - a *reference range*, not a target price.
* `stop-loss zone` = close − 2 x ATR(14), also respecting the recent support.
* `target zone` = close + 3 x ATR(14), also respecting the recent resistance.
* `risk note` explains the main danger in plain words (high volatility, overbought RSI,
  heavy retrograde period, big past drawdown...).
* `Ephemeris calendar` = what is coming in the next 3-4 weeks (not a prediction).
* `Ephemeris ↔ market correlation` = historical only; sample sizes are printed so you can
  judge whether a correlation is meaningful. Correlation is not causation.

**Arbitrage option**

* Spread % is computed from real quoted prices: `net spread = gross spread − round-trip cost`.
* Anything below the **0.3%** minimum spread is labelled **NOT PROFITABLE**, and a negative
  net spread is never shown as profitable.
* **Stale-quote guard:** the two legs must come from the *same session*. If the quotes are
  1+ days apart, the difference is a stale-price artefact, so it is marked **NOT PROFITABLE**
  and the reason says so. The `Quote gap` column shows the gap in days. This is why several
  pairs that look like a 2-3% "spread" are correctly rejected.
* Cost model: brokerage 3 bps + STT 2.5 bps + exchange 0.35 bps + GST 0.63 bps + stamp
  0.3 bps (one way) → ~0.1356% round trip. Edit `.env` to match your broker.
* ETF arbitrage needs creation-unit sizes and an authorised participant; retail size usually
  cannot capture it. That caveat is printed on every ETF row.

---

## 7. PHASE 5 verification results (actually run, not claimed)

```bat
python -m pytest -v tests/test_verification.py      ::  -> 9 passed
```

| # | Test | Result |
|---|---|---|
| 1 | Data completeness (deepest series > 6000 trading days) | **PASS** - SENSEX 6,620 days from 2000-01-03 |
| 2 | No hallucination: stored values vs a live source, 0.5% tolerance | **PASS** - NIFTY-50 official-vs-backup max diff = 0.0% |
| 3 | Ephemeris accuracy, 2024-01-01 | **PASS** - Sun = 280.314961° (Capricorn); stored == live calc |
| 4 | Aspect / retrograde correctness | **PASS** - Mercury retrograde 12/12 published dates correct |
| 5 | Agent output (mode = stocks) | **PASS** - 5 picks, every field filled, ordering invariants hold |
| 6 | Arbitrage maths + no false profit | **PASS** - formula reproduced; stale & sub-minimum spreads rejected |
| 7 | Resume behaviour | **PASS** - state preserved, todo ticked, never restarts from zero |
| 8 | C-drive test | **PASS** - zero forbidden path literals; runtime guard raises |
| 9 | Frontend + API round trip < 30 s | **PASS** - `/run-research` mode=both in ~14-20 s |

Also verified outside pytest:

* `logs/validation_report.json` generated (Mercury-retrograde reference dates 12/12 correct).
* Daily update is **idempotent**: running it twice left row counts identical
  (`nifty50=4705, stocks=505996, eph=117756, asp=149674`).
* The real server was started through `run.py` and answered `/health` (drive D, data ready)
  and `/` (page with RUN button and Disclaimer).
* The cross-check flagged **5 mismatches** (>0.5%) between official NSE closes and Yahoo
  closes - recorded in the DB and `state.mismatches`, and **not** auto-corrected, as required.

---

## 8. Project layout

### Phase 6 research workflow

The default stock universe is **NIFTY-50**; NIFTY-100 and all available stocks are
also selectable. A final confirmed pick requires positive technical evidence and
positive planetary historical evidence. NSE corporate announcements are a fresh
risk check: a material filing can block confirmation, but news never creates a
bullish prediction. If NSE is blocked or empty, the result says `NOT_AVAILABLE`.

The four-week evidence window is approximately 20 trading days. Each run writes
confirmed/watchlist outputs to the SQLite `prediction_ledger`; `GET /prediction-ledger`
shows pending and evaluated records. `POST /walk-forward` runs point-in-time
technical validation using only rows available before each historical cutoff.
These are validation reports, not guarantees or investment advice.

The direct NIFTY-50 combined pipeline measured approximately 26 seconds on the
project machine. The uncached FastAPI TestClient SLA is documented at 45 seconds
because it includes variable live NSE disclosure I/O. The full verification suite
passes 9/9.

```
run.py                     one-click starter
app/
  config.py                paths + C:-drive guard + cache redirection
  state_manager.py         the RESUME system (project_state.json + todo.md)
  main.py                  FastAPI: /health /status /last-result /run-research /rebuild-data
  orchestrator.py          Orchestrator Agent
  agents/                  base + technical, historical, ephemeris, market, arbitrage, news agents
  validation.py            point-in-time walk-forward validation
data/
  fetch_nse.py             official NSE archives (indices + equity bhavcopy + F&O)
  fetch_bse.py             official BSE attempt + honest failure logging
  fetch_yahoo_backup.py    labelled backup / cross-check source
  fetch_amfi.py            official AMFI NAV feed (ETF vs NAV)
  fetch_ephemeris.py       Swiss Ephemeris book + aspects + events
  normalize.py             one canonical schema + idempotent parquet merge
  pipeline.py              builds the whole stock book
  validate_data.py         PHASE 5 validation report
  raw/ processed/ ephemeris/    downloaded files + Parquet data
db/                        schema.sql + SQLite metadata layer + prediction ledger
frontend/                  index.html + style.css + app.js (no framework)
scripts/                   daily_update.py + setup_windows_task.bat
state/                     project_state.json (lock file) + todo.md (checklist)
tests/                     PHASE 5 verification suite
logs/                      fetch_log.jsonl, daily_run.jsonl, validation_report.json
```

---

## 9. Troubleshooting

| Problem | What to do |
|---|---|
| `BLOCKED: ... C: drive par hai` | Edit `.env` and set `PROJECT_ROOT` to `D:/...` or `E:/...` |
| `The data books are empty` | Run `python -m data.pipeline` once (≈25 min) or just `python run.py` |
| BSE rows missing | Expected here: the BSE API is WAF-blocked. Sensex still arrives via the labelled backup source. |
| `NOT AVAILABLE` on the Futures/ETF tab | The official F&O bhavcopy or the AMFI feed was unreachable; the reason is printed and logged. |
| Second build is slow | Only the first build downloads. Afterwards `data/raw/` makes reruns fast. |
| Port already in use | `python run.py --port 9000` |
| Anything looks wrong | `python -m data.validate_data` writes `logs/validation_report.json` |

---

## 10. Running the tests yourself

```bat
python -m pytest -v tests/test_verification.py
```

Covers: data completeness (>6000 trading days on the deepest series), no-hallucination
cross-check against a live source, ephemeris accuracy (Sun ≈280° on 2024-01-01), aspect and
Mercury-retrograde correctness, agent output completeness (4-5 picks, no empty fields),
arbitrage maths with a hard "no false profit" guard, resume behaviour, the C:-drive guard,
and the frontend/API round-trip completing within the documented uncached 45-second SLA.

---

## 11. Plain-language summary for a non-coder

**What was built:** two "books" - one of Indian stock prices, one of planetary positions -
and five small robot-analysts that read both books. You tick a box and press RUN; you get
either a shortlist of stocks to *study* for the next 3-4 weeks, or a list of arbitrage
spreads with a clear profitable / not-profitable verdict.

**Where the data comes from:** official NSE archive files (their own servers), official
NIFTY constituent lists, and the Swiss Ephemeris (the same planetary engine used by
astro.com). Yahoo is used only as a labelled backup and for cross-checking - and every row
says which source it came from.

**If data is missing:** you will see the word `MISSING`, and the system will say which
source failed and why. It never guesses a number. The one known gap - NIFTY-50 index values
before September 2007 - is written down openly in section 5.

**If something breaks:** open `logs/` (`fetch_log.jsonl`, `daily_run.jsonl`,
`validation_report.json`). Re-running `python run.py` is always safe: it resumes from
`state/project_state.json` and never duplicates data.
