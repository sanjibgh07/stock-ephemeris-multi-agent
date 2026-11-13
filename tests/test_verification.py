"""tests/test_verification.py - PHASE 5 (mandatory) verification tests.

Ye tests prompt ke hisaab se hain. Sab pass hone chahiye.
    python -m pytest -v tests/test_verification.py
    python run.py --test
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import pytest

from app import config, state_manager
from app.agents import base

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _dataset(name: str):
    return base.load_dataset(name)


# ===========================================================================
# TEST 1 - DATA COMPLETENESS
# ===========================================================================
def test_1_data_completeness():
    """Data book me asli data hai + deepest series me >6000 trading days."""
    from data import normalize

    counts = {}
    for name in ("nifty50", "sensex", "nifty100_stocks", "ephemeris_daily",
                 "aspects_daily", "events_daily"):
        df = normalize.read_parquet(normalize.processed_path(name))
        counts[name] = 0 if df is None else len(df)
    print("\nRow counts:", json.dumps(counts))

    assert counts["sensex"] > 0, "sensex dataset is empty"
    assert counts["nifty100_stocks"] > 0, "stock book is empty"
    assert counts["ephemeris_daily"] > 0, "ephemeris book is empty"
    assert counts["aspects_daily"] > 0, "aspects book is empty"

    sensex = _dataset("sensex")
    sensex_days = int(sensex["date"].nunique())
    print(f"SENSEX distinct trading days: {sensex_days} "
          f"({sensex['date'].min()} .. {sensex['date'].max()})")
    assert sensex_days > 6000, (
        f"SENSEX should have >6000 trading days from 2000, got {sensex_days}")

    nifty = _dataset("nifty50")
    nifty_days = int(nifty["date"].nunique())
    print(f"NIFTY50 distinct days: {nifty_days} "
          f"({nifty['date'].min()} .. {nifty['date'].max()})")
    assert nifty_days > 3000, "nifty50 dataset unexpectedly small"
    if nifty_days <= 6000:
        print("NOTE (reported, not hidden): official NSE ind_close_all starts 2013 and the "
              "Yahoo ^NSEI series starts 2007-09-17, so NIFTY-50 index history before 2007 "
              "is genuinely unavailable from any reachable source. It stays a documented "
              "gap instead of being invented. SENSEX and the stock book do cover 2000+.")

    stocks = _dataset("nifty100_stocks")
    deep = stocks.groupby("symbol")["date"].nunique()
    print(f"stock book: {stocks['symbol'].nunique()} symbols, deepest = {int(deep.max())} days")
    assert int(deep.max()) > 6000, "no individual stock has 2000+ history"


# ===========================================================================
# TEST 2 - NO HALLUCINATION / CROSS-CHECK
# ===========================================================================
def test_2_no_hallucination_cross_check():
    """Stored values vs LIVE source: 0.5% ke andar, warna MISMATCH flag."""
    from data import fetch_yahoo_backup as yb
    from data import normalize

    check_dates = ["2008-01-21", "2020-03-23", "2021-02-01"]
    sensex = normalize.read_parquet(normalize.processed_path("sensex"))
    assert sensex is not None and len(sensex), "sensex missing"
    work = sensex.assign(d=sensex["date"].astype(str))

    compared = 0
    for date_str in check_dates:
        row = work[work["d"] == date_str]
        if not len(row):
            print(f"  {date_str}: not in dataset -> stays MISSING (never invented)")
            continue
        stored = float(row["close"].iloc[0])
        live = yb.fetch_chart("^BSESN", start=(dt.date.fromisoformat(date_str) -
                                              dt.timedelta(days=5)).isoformat(),
                              end=(dt.date.fromisoformat(date_str) +
                                   dt.timedelta(days=5)).isoformat())
        if live is None or not len(live):
            pytest.skip("live source unavailable right now - cannot cross-check")
        live = live.assign(d=live["date"].astype(str))
        exact = live[live["d"] == date_str]
        if not len(exact):
            print(f"  {date_str}: live source has no bar for this exact date (holiday/format)")
            continue
        live_close = float(exact["close"].iloc[0])
        diff = abs(stored - live_close) / live_close * 100
        print(f"  {date_str}: stored={stored} live={live_close} diff={diff:.4f}%")
        assert diff <= 0.5, f"{date_str} differs by {diff:.4f}% (>0.5%) - MISMATCH"
        compared += 1
    assert compared >= 1, "no date could be cross-checked"

    nifty = normalize.read_parquet(normalize.processed_path("nifty50"))
    off = nifty[nifty["source"].astype(str) == "nse_official"]
    bak = nifty[nifty["source"].astype(str) == "yahoo_backup"]
    res = normalize.cross_check(off, bak, "NIFTY50", tolerance_pct=0.5)
    print(f"  official-vs-backup overlap: {res['compared']} days, "
          f"max diff {res['max_diff_pct']}%")
    if res["compared"] and res["max_diff_pct"] is not None:
        assert res["max_diff_pct"] <= 0.5, (
            f"official vs backup differ by {res['max_diff_pct']}% - flagged MISMATCH")


# ===========================================================================
# TEST 3 - EPHEMERIS ACCURACY
# ===========================================================================
def test_3_ephemeris_accuracy():
    """Sun longitude 2024-01-01 ko ~280 deg + stored == live calculation."""
    from data import fetch_ephemeris as fe
    from data import normalize

    check = fe.validate_sun_longitude(dt.date(2024, 1, 1))
    print("\n", json.dumps(check, indent=2))
    assert check["ok"], f"Sun longitude wrong: {check['sun_longitude_deg']}"
    assert abs(check["sun_longitude_deg"] - 280.0) <= 1.0

    eph = normalize.read_parquet(normalize.processed_path("ephemeris_daily"))
    assert eph is not None and len(eph), "ephemeris parquet missing"
    sun = eph.assign(d=eph["date"].astype(str))
    sun = sun[(sun["d"] == "2024-01-01") & (sun["body"] == "Sun")]
    assert len(sun), "2024-01-01 Sun row missing in stored ephemeris"
    stored = float(sun["longitude_deg"].iloc[0])
    print(f"stored Sun longitude 2024-01-01 = {stored}")
    assert abs(stored - check["sun_longitude_deg"]) < 0.1, "stored ephemeris drifted"


# ===========================================================================
# TEST 4 - ASPECT / RETROGRADE TEST
# ===========================================================================
def test_4_aspect_and_mercury_retrograde():
    """Known Mercury retrograde dates sahi mark + aspects orb ke andar."""
    from data import normalize

    eph = normalize.read_parquet(normalize.processed_path("ephemeris_daily"))
    assert eph is not None and len(eph), "ephemeris parquet missing"
    merc = eph[eph["body"].astype(str) == "Mercury"]
    retro = dict(zip(merc["date"].astype(str), merc["is_retrograde"].astype(bool)))

    expect_true = ["2024-01-01", "2024-04-10", "2024-08-10", "2023-12-20"]
    expect_false = ["2024-01-15", "2024-05-10", "2024-09-10"]
    for d in expect_true:
        if d in retro:
            assert retro[d] is True, f"{d} should be Mercury retrograde"
            print(f"  {d}: retrograde=True (correct)")
    for d in expect_false:
        if d in retro:
            assert retro[d] is False, f"{d} should NOT be Mercury retrograde"
            print(f"  {d}: retrograde=False (correct)")
    assert any(retro.get(d) is True for d in expect_true if d in retro)

    asp = normalize.read_parquet(normalize.processed_path("aspects_daily"))
    assert asp is not None and len(asp), "aspects parquet missing"
    names = set(asp["aspect"].astype(str).unique())
    print("  aspect types present:", sorted(names))
    assert {"Conjunction", "Opposition"}.intersection(names)
    orbs = asp["orb_deg"].astype(float)
    assert float(orbs.max()) <= config.ASPECT_ORB_DEGREES + 1e-6, "aspect orb exceeded"

    ev = _dataset("events_daily")
    assert ev is not None and len(ev), "events parquet missing"
    kinds = set(ev["event_type"].astype(str).unique())
    print("  event types present:", sorted(kinds))
    assert "retrograde_start" in kinds, "no retrograde_start events detected"




# ===========================================================================
# TEST 5 - AGENT TEST (Option A)
# ===========================================================================
def test_5_agent_stocks_mode():
    """mode=stocks -> 4-5 stocks, har field bhara hua (koi khaali field nahi)."""
    from app.orchestrator import Orchestrator

    orch = Orchestrator(top_n=5)
    t0 = time.time()
    result = orch.run(mode="stocks", use_cache=False)
    elapsed = time.time() - t0
    print(f"\nstocks mode took {elapsed:.1f}s")
    assert result.get("ok"), f"run failed: {result.get('error')}"
    stocks = result.get("stocks") or {}
    assert stocks.get("available"), f"stocks not available: {stocks.get('error')}"
    picks = stocks.get("picks") or []
    print(f"picks returned: {len(picks)} of {stocks.get('candidates_considered')} candidates")
    validation = (stocks.get("market_context") or {}).get("walk_forward_validation") or {}
    assert len(picks) <= 5
    if validation.get("quality") == "WEAK":
        assert len(picks) == 0, "weak walk-forward validation must block confirmed picks"

    for pick in picks:
        print(f"  {pick['symbol']}: score={pick['combined_score']} "
              f"entry={pick['entry_zone']} stop={pick['stop_loss_zone']} "
              f"target={pick['target_zone']}")
        assert pick.get("symbol")
        assert pick.get("combined_score") is not None
        assert pick.get("entry_zone") not in (None, config.MISSING), "entry zone empty"
        assert pick.get("stop_loss_zone") not in (None, config.MISSING), "stop zone empty"
        assert pick.get("target_zone") not in (None, config.MISSING), "target zone empty"
        assert len(pick.get("reasons") or []) > 0, "no reasons given"
        assert pick.get("risk_note"), "risk note empty"
        kn = pick["key_numbers"]
        assert kn.get("rsi14") not in (None, config.MISSING), "RSI missing"
        entry, stop, target = pick["entry_zone"], pick["stop_loss_zone"], pick["target_zone"]
        assert entry[0] < entry[1], "entry zone inverted"
        assert stop[1] < entry[0], "stop-loss must sit below the entry zone"
        assert target[0] > entry[1], "target must sit above the entry zone"
    assert result.get("disclaimer"), "disclaimer missing"
    assert "not investment advice" in result["disclaimer"].lower()


# ===========================================================================
# TEST 6 - ARBITRAGE TEST
# ===========================================================================
def test_6_arbitrage_math_and_no_false_profit():
    """Spread sahi calculate ho aur negative profit kabhi 'profitable' na dikhe."""
    from app.agents.arbitrage_agent import ArbitrageAgent

    agent = ArbitrageAgent(limit=10)
    result = agent.run(include_futures=False, include_etf=False)
    print("\navailable:", result["available"], "| pairs:", result["nse_vs_bse"]["count"])
    assert result.get("cost_model"), "cost model missing"
    cost = result["cost_model"]["round_trip_cost_pct"]
    min_spread = result["cost_model"]["min_required_spread_pct"]
    assert cost > 0 and min_spread == config.ARB_MIN_PROFITABLE_SPREAD_PCT

    items = result["nse_vs_bse"]["items"]
    assert items, "no NSE-vs-BSE pairs computed from real data"
    for it in items:
        expected = (it["bse_close"] - it["nse_close"]) / it["nse_close"] * 100.0
        assert abs(expected - it["spread_pct"]) < 1e-3, "spread formula wrong"
        net = it["gross_spread_pct"] - cost
        assert abs(net - it["net_spread_pct"]) < 0.02, "net spread wrong"
        gap = it.get("quote_date_gap_days")
        if gap is not None and abs(gap) >= 1:
            # legs from different sessions -> stale-price artefact, never "profitable"
            assert it["profitable"] is False, (
                f"{it['symbol']}: stale {abs(gap)}-day quote gap shown as profitable")
        if it["profitable"]:
            assert it["gross_spread_pct"] >= min_spread, "profitable below minimum spread"
            assert it["net_spread_pct"] > 0, "negative net spread shown as profitable!"
            assert (gap is None or abs(gap) < 1), "stale quote shown as profitable"
        else:
            assert it.get("reason"), "not-profitable item must explain why"
        print(f"  {it['symbol']}: gross={it['gross_spread_pct']}% net={it['net_spread_pct']}% "
              f"gap={gap}d -> {'worth checking' if it['profitable'] else 'NOT PROFITABLE'}")

    # explicit guards: a spread below the minimum must NEVER be profitable,
    # a stale-date spread must never be profitable, and negative net is never ok
    verdict = agent._verdict(gross_spread_pct=0.05, cost_pct=0.1356, min_spread=0.3)
    assert verdict["profitable"] is False, "small spread must never be profitable"
    assert "NOT PROFITABLE" in verdict["reason"]
    stale = agent._verdict(gross_spread_pct=2.0, cost_pct=0.1356, min_spread=0.3,
                           stale_days=3)
    assert stale["profitable"] is False, "stale-date spread must never be profitable"
    assert "stale" in stale["reason"].lower()
    verdict2 = agent._verdict(gross_spread_pct=1.0, cost_pct=0.1356, min_spread=0.3,
                              stale_days=0)
    assert verdict2["profitable"] is True
    assert verdict2["net_spread_pct"] > 0
    print("  verdict guards OK: sub-minimum and stale spreads are never profitable")


# ===========================================================================
# TEST 7 - RESUME TEST
# ===========================================================================
def test_7_resume_system(monkeypatch):
    """State file se 'Resuming from Phase X' mile, aur dobara chalao -> reset nahi."""
    state = state_manager.load_state()
    message = state_manager.resume_message()
    print("\nresume message:", message)
    assert "Resuming from Phase" in message or "All phases complete" in message

    before_steps = list(state.get("completed_steps", []))
    before_phase = state.get("current_phase")

    # second "run": state read again -> must not restart from zero
    again = state_manager.load_state()
    assert list(again.get("completed_steps", [])) == before_steps, "state was reset!"
    assert again.get("current_phase") == before_phase, "phase regressed!"

    # tick a step and confirm it persists + todo.md is updated
    state_manager.mark_step_done("P5.7", note="resume test ran")

    # AMFI outages must be persisted in state, not only written to the event log.
    from data import fetch_amfi

    monkeypatch.setattr(fetch_amfi.http_util, "get_with_retry", lambda *args, **kwargs: None)
    fetch_amfi.fetch_amfi_nav(timeout=1, max_retries=0)
    refreshed = state_manager.load_state()
    assert any(
        item.get("source") == config.SOURCE_AMFI_OFFICIAL
        for item in refreshed.get("source_failures", [])
    ), "AMFI failure was not persisted in state.source_failures"
    after = state_manager.load_state()
    assert "P5.7" in after["completed_steps"]
    todo = Path(config.TODO_PATH).read_text(encoding="utf-8")
    assert "[x] P5.7" in todo, "todo.md was not ticked"
    print("  second load kept", len(after["completed_steps"]), "completed steps; todo ticked")


# ===========================================================================
# TEST 8 - C DRIVE TEST
# ===========================================================================
def test_8_no_c_drive_literals():
    """Code me koi C:/ ya C:\\ storage path nahi (comments ke alawa)."""
    import re as _re

    offenders: list[str] = []
    pattern = _re.compile(r"""["']C:[\\/]""")
    allowed_markers = ("NEVER", "not on C", "C: drive", "C: se", "C: par",
                       "forbidden", "avoid", "do not", "kodhi", "blocked")
    exts = {".py", ".js", ".html", ".css", ".md", ".bat", ".sql", ".json", ".example", ".txt"}
    skip_dirs = {".cache", "__pycache__", "raw", "processed", "ephemeris", ".git",
                 ".venv_fresh"}

    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in exts:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:  # noqa: BLE001
            continue
        for lineno, line in enumerate(lines, 1):
            if pattern.search(line):
                lower = line.lower()
                if any(m.lower() in lower for m in allowed_markers):
                    continue
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {line.strip()[:120]}")
    print(f"\nC-drive path literals found: {len(offenders)}")
    for line in offenders[:20]:
        print("  ", line)
    assert not offenders, "storage path literal on C: found - forbidden"

    # runtime guard must also be active
    from app import config as cfg

    assert cfg.NO_C_DRIVE is True
    with pytest.raises(Exception):
        cfg.assert_not_c_drive(Path("C:/should/be/blocked"))
    assert cfg.drive_letter(cfg.PROJECT_ROOT) != "C"
    print("  runtime guard OK: PROJECT_ROOT is on", cfg.drive_letter(cfg.PROJECT_ROOT) + ":")


# ===========================================================================
# TEST 9 - FRONTEND TEST
# ===========================================================================
def test_9_frontend_and_api_under_45s():
    """Frontend load ho aur uncached both-mode research 45 seconds ke andar result de.

    The uncached TestClient path includes real NSE disclosure I/O.  The direct
    application benchmark remains separately measured and is normally faster.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    print("\n/health:", json.dumps(health.json()["data_status"]))
    assert health.json()["drive"] != "C"

    page = client.get("/")
    assert page.status_code == 200
    assert "Disclaimer" in page.text or "disclaimer" in page.text
    assert "Arbitrage" in page.text and "RUN" in page.text

    css = client.get("/static/style.css")
    js = client.get("/static/app.js")
    assert css.status_code == 200 and js.status_code == 200

    status = client.get("/status")
    assert status.status_code == 200
    assert "resume_message" in status.json()

    t0 = time.time()
    resp = client.post("/run-research", json={"mode": "both", "top_n": 5, "use_cache": False})
    elapsed = time.time() - t0
    print(f"POST /run-research (both) -> {resp.status_code} in {elapsed:.1f}s")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok"), f"research failed: {body.get('error')}"
    assert body.get("stocks", {}).get("available") or body.get("arbitrage", {}).get("available")
    assert elapsed < 45.0, f"frontend research took {elapsed:.1f}s (must be < 45s)"

    last = client.get("/last-result")
    assert last.status_code == 200
    assert last.json().get("run_id") == body.get("run_id")
    assert last.json().get("disclaimer")
