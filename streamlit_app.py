"""Streamlit entrypoint for the verified Stock + Ephemeris research system."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PROJECT_ROOT", str(ROOT))
os.environ.setdefault("NO_C_DRIVE", "False")
sys.path.insert(0, str(ROOT))

from app.orchestrator import Orchestrator  # noqa: E402
from app.validation import run_walk_forward  # noqa: E402
from app.agents import base  # noqa: E402

st.set_page_config(page_title="Stock + Ephemeris Research", page_icon="📊", layout="wide")
st.title("Indian Stock + Ephemeris Research")
st.caption("Historical, technical and ephemeris evidence only — not investment advice.")
st.warning("Research only. No result is a guarantee or a recommendation to buy or sell.")

with st.sidebar:
    st.header("Research controls")
    mode = st.selectbox("Calculation", ["stocks", "arbitrage", "both"])
    universe = st.selectbox("Universe", ["NIFTY50", "NIFTY100", "ALL"])
    top_n = st.slider("Maximum shortlist", 1, 10, 5)
    run_button = st.button("RUN REAL CALCULATION", type="primary", use_container_width=True)
    validate_button = st.button("RUN 6-FOLD VALIDATION", use_container_width=True)

if run_button:
    with st.spinner("Running technical, historical, ephemeris, news and validation agents..."):
        result = Orchestrator(top_n=top_n).run(mode=mode, universe=universe, use_cache=False)
    st.session_state["result"] = result

if validate_button:
    with st.spinner("Running point-in-time holdout validation..."):
        book = base.load_dataset("nifty100_stocks")
        validation = run_walk_forward(book, universe=universe, horizon_days=20, folds=6)
    st.session_state["validation"] = validation

result = st.session_state.get("result")
validation = st.session_state.get("validation")

if result:
    st.subheader("Real calculation result")
    st.json(result, expanded=False)
    stocks = result.get("stocks") or {}
    picks = stocks.get("picks") or []
    if picks:
        st.subheader("Shortlist")
        st.dataframe(picks, use_container_width=True, hide_index=True)
    else:
        st.info("No confirmed shortlist was produced by the evidence gate.")

if validation:
    st.subheader("Walk-forward validation")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Quality", validation.get("quality", "NOT_AVAILABLE"))
    c2.metric("Threshold", validation.get("selected_threshold", "n/a"))
    c3.metric("Holdout signals", (validation.get("holdout") or {}).get("signals", 0))
    c4.metric("Holdout positive %", (validation.get("holdout") or {}).get("positive_rate_pct", "n/a"))
    st.json(validation, expanded=False)

st.divider()
st.markdown("**Source and safety:** official NSE datasets where available; labelled backup sources remain labelled. Missing or failed sources are shown as unavailable, never invented.")
