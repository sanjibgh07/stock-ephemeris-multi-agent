/* frontend/app.js - simple JavaScript (no framework, isliye kuch install nahi karna) */

const $ = (id) => document.getElementById(id);
const MISSING = "MISSING";

function fmt(v, suffix = "") {
  if (v === null || v === undefined || v === MISSING || v === "MISSING") return MISSING;
  if (typeof v === "number") {
    return v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) + suffix;
  }
  return String(v) + suffix;
}
function zoneText(z) {
  if (!Array.isArray(z) || z.length !== 2) return MISSING;
  return `${fmt(z[0])} - ${fmt(z[1])}`;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function h(tag, cls, html) {
  return `<${tag}${cls ? ` class="${cls}"` : ""}>${html}</${tag}>`;
}

async function loadStatus() {
  try {
    const r = await fetch("/status");
    const d = await r.json();
    const st = d.state || {};
    const ds = d.data_status || {};
    const disk = d.disk || {};
    const todo = d.todo_progress || {};
    $("status").innerHTML = `
      <div><b>Resume:</b> ${esc(d.resume_message || "-")}</div>
      <div><b>Phase:</b> ${esc(st.current_phase || "-")} &middot;
           <b>Last run:</b> ${esc(st.last_run_date || "-")}</div>
      <div><b>Last stock data date:</b> ${esc(st.last_data_date_stock || "-")} &middot;
           <b>Last ephemeris date:</b> ${esc(st.last_data_date_ephemeris || "-")}</div>
      <div><b>Data ready:</b> ${ds.ready ? "YES" : "NO"} &middot;
           stock rows ${fmt(ds.stock_rows)} (${fmt(ds.stock_symbols)} symbols) &middot;
           ephemeris rows ${fmt(ds.ephemeris_rows)}</div>
      <div><b>TODO progress:</b> ${todo.done || 0}/${todo.total || 0} steps ticked &middot;
           <b>Disk free:</b> ${fmt(disk.free_gb)} GB on ${esc(disk.drive || "-")}:
           ${disk.warning ? `<span style="color:#ffc857">${esc(disk.warning)}</span>` : "OK"}</div>
      ${(st.source_failures || []).length ? `<div><b>Source failures logged:</b> ${
        (st.source_failures || []).map((f) => esc(f.source)).join(", ")}</div>` : ""}
    `;

    const rows = (d.datasets || []).map((x) => `<tr>
        <td>${esc(x.dataset_name)}</td><td>${fmt(x.row_count)}</td>
        <td>${esc(x.first_date || "-")}</td><td>${esc(x.last_date || "-")}</td>
        <td>${fmt(x.symbols)}</td><td>${esc(x.sources || "-")}</td>
        <td><span class="badge ${x.quality === "OK" ? "ok" : x.quality === "PARTIAL" ? "na" : "no"}">${esc(x.quality)}</span></td>
      </tr>`).join("");
    $("datasets").innerHTML = rows
      ? `<table><thead><tr><th>Dataset</th><th>Rows</th><th>From</th><th>To</th><th>Symbols</th>
         <th>Sources</th><th>Quality</th></tr></thead><tbody>${rows}</tbody></table>`
      : `<p class="note">No datasets yet. Press RUN - the system will build them from the official sources.</p>`;
  } catch (e) {
    $("status").innerHTML = `<span style="color:#ff6b6b">status error: ${esc(e)}</span>`;
  }
}


function renderStocks(stocks, disclaimer) {
  if (!stocks || !stocks.available) {
    return h("div", "card", h("h3", "", "3-4 Week Stocks") +
      h("div", "error-box", esc((stocks && stocks.error) || "no result")));
  }
  const ctx = stocks.market_context || {};
  const cond = (ctx.ephemeris_conditions) || {};
  const model = (ctx.ephemeris_risk_model) || {};
  const lines = [];

  lines.push(h("div", "card",
    `<h3>3-4 Week Stocks &mdash; ${stocks.pick_count} names to study (of ${
      fmt(stocks.candidates_considered)} considered)</h3>` +
    h("div", "kv",
      `<span><b>As of:</b> ${esc(stocks.as_of)}</span>
       <span><b>Horizon:</b> ${esc(stocks.horizon)}</span>
       <span><b>Score weights:</b> technical ${stocks.weights.technical}, historical ${stocks.weights.historical}, ephemeris ${stocks.weights.ephemeris}</span>
       <span><b>Sky now:</b> ${((cond.retrograde || []).join(", ") || "no retrograde planets")}</span>`) +
    h("div", "note", esc(stocks.method_note || ""))));

  const policy = stocks.confirmation_policy || {};
  const wf = (stocks.market_context || {}).walk_forward_validation || {};
  lines.push(h("div", "card", h("h3", "", "Confirmation summary") +
    h("div", "kv", `<span><b>Confirmed:</b> ${fmt(stocks.confirmed_count)}</span>
      <span><b>Watchlist:</b> ${fmt(stocks.watchlist_count)}</span>
      <span><b>Rejected:</b> ${fmt(stocks.rejected_count)}</span>
      <span><b>Walk-forward quality:</b> ${esc(wf.quality || "NOT_AVAILABLE")}</span>
      <span><b>Rule:</b> ${esc(policy.rule || "Both technical and planetary evidence must be positive.")}</span>`)));

  if (!(stocks.picks || []).length) {
    lines.push(h("div", "card", h("div", "note",
      "No confirmed signal today. Stocks with only one positive evidence source remain in the watchlist.")));
  }

  (stocks.picks || []).forEach((p, i) => {
    const kn = p.key_numbers || {};
    const sb = p.score_breakdown || {};
    const pe = p.planetary_4w_evidence || {};
    const fn = p.fresh_news || {};
    lines.push(h("div", "card",
      `<div class="pick-head"><h3>${i + 1}. ${esc(p.symbol)}</h3>
         <span class="score">combined score ${fmt(p.combined_score)}/100</span></div>
       <div class="kv">
         <span><b>Status:</b> ${esc(p.signal_status || "confirmed")}</span>
         <span><b>Close:</b> ${fmt(p.close)}</span>
         <span><b>Technical:</b> ${fmt(sb.technical)}</span>
         <span><b>Historical:</b> ${fmt(sb.historical)}</span>
         <span><b>Ephemeris regime:</b> ${fmt(sb.ephemeris)}</span>
         <span><b>Planetary 4-week evidence:</b> ${pe.available ? `${fmt(pe.mean_return_pct, "%")} mean / ${fmt(pe.hit_rate_pct, "%")} positive (${fmt(pe.matched_days)} matches)` : "NOT AVAILABLE"}</span>
          <span><b>Fresh NSE disclosure:</b> ${esc(fn.status || "NOT_AVAILABLE")}${fn.high_risk_count ? ` (${fmt(fn.high_risk_count)} risk flags)` : ""}</span>
         <span><b>Data source:</b> ${esc((p.source || []).join(", ") || "-")}</span>
       </div>
       <div class="zones">
         <div class="zone entry"><div class="label">Entry zone (reference)</div>
              <div class="value">${zoneText(p.entry_zone)}</div></div>
         <div class="zone stop"><div class="label">Stop-loss zone (reference)</div>
              <div class="value">${zoneText(p.stop_loss_zone)}</div></div>
         <div class="zone target"><div class="label">Target zone (reference)</div>
              <div class="value">${zoneText(p.target_zone)}</div></div>
       </div>
       <div class="kv">
         <span><b>Risk:reward:</b> ${fmt(p.risk_reward)}</span>
         <span><b>RSI(14):</b> ${fmt(kn.rsi14)}</span>
         <span><b>MACD hist:</b> ${fmt(kn.macd_hist)}</span>
         <span><b>SMA 20/50/200:</b> ${fmt(kn.sma20)} / ${fmt(kn.sma50)} / ${fmt(kn.sma200)}</span>
         <span><b>52w high/low:</b> ${fmt(kn.high_52w)} / ${fmt(kn.low_52w)}</span>
         <span><b>Volume spike:</b> ${kn.volume_spike ? "YES" : "no"}</span>
         <span><b>1-year return:</b> ${fmt(kn.return_1y_pct, "%")}</span>
         <span><b>Worst 5-year fall:</b> ${fmt(kn.max_drawdown_5y_pct, "%")}</span>
         <span><b>Volatility (ann.):</b> ${fmt(kn.volatility_20d_annual_pct, "%")}</span>
       </div>` +
      h("div", "plain", esc(p.plain_reason || "")) +
      h("h4", "", "Why this stock") +
      h("ul", "reasons", (p.reasons || []).slice(0, 7).map((r) => `<li>${esc(r)}</li>`).join("")) +
      h("div", "risk", `<b>Risk note:</b> ${esc(p.risk_note)}`)));
  });

  const watch = (stocks.watchlist || []).slice(0, 8);
  if (watch.length) {
    const rows = watch.map((p) => `<tr><td>${esc(p.symbol)}</td>
      <td>${esc(p.signal_status || "watchlist")}</td><td>${fmt(p.combined_score)}</td>
      <td>${esc((p.decision_reasons || []).join("; "))}</td></tr>`).join("");
    lines.push(h("div", "card", h("h3", "", "Watchlist — one confirmation only") +
      `<table><thead><tr><th>Symbol</th><th>Status</th><th>Score</th><th>Why not confirmed</th></tr></thead>
       <tbody>${rows}</tbody></table>`));
  }

  const up = (ctx.upcoming_3_to_4_weeks) || {};
  if ((up.events || []).length || (up.tight_aspects || []).length) {
    const ev = (up.events || []).slice(0, 14).map((e) =>
      `<tr><td>${esc(e.date)}</td><td>${esc(e.event_type)}</td><td>${esc(e.body || "-")}</td>
       <td>${esc(e.detail || "")}</td></tr>`).join("");
    const asp = (up.tight_aspects || []).slice(0, 12).map((a) =>
      `<tr><td>${esc(a.date)}</td><td>${esc(a.body_a)} &ndash; ${esc(a.body_b)}</td>
       <td>${esc(a.aspect)}</td><td>${fmt(a.orb_deg)}</td></tr>`).join("");
    lines.push(h("div", "card",
      h("h3", "", "Ephemeris calendar - next 3-4 weeks") +
      h("div", "note", esc(up.note || "")) +
      h("h4", "", "Upcoming events") +
      (ev ? `<table><thead><tr><th>Date</th><th>Event</th><th>Body</th><th>Detail</th></tr></thead><tbody>${ev}</tbody></table>`
          : h("p", "note", MISSING)) +
      h("h4", "", "Tight aspects (orb &le; 2 degrees)") +
      (asp ? `<table><thead><tr><th>Date</th><th>Pair</th><th>Aspect</th><th>Orb</th></tr></thead><tbody>${asp}</tbody></table>`
           : h("p", "note", MISSING))));
  }

  if (model.available) {
    const rows = Object.entries(model.features || {}).map(([k, v]) =>
      `<tr><td>${esc(v.label || k)}</td><td>${fmt(v.sample_days)}</td>
       <td>${fmt(v.condition_avg_abs_move_pct, "%")}</td><td>${fmt(v.baseline_avg_abs_move_pct, "%")}</td>
       <td>${fmt(v.ratio)}</td><td>${esc(v.interpretation || v.note || "")}</td></tr>`).join("");
    lines.push(h("div", "card",
      h("h3", "", "Ephemeris &harr; market correlation (historical)") +
      h("div", "note", esc(model.note || "")) +
      h("div", "kv", `<span><b>Index used:</b> ${esc(model.index_used)}</span>
        <span><b>Days compared:</b> ${fmt(model.sample_days)}</span>
        <span><b>Baseline average daily move:</b> ${fmt(model.baseline_avg_abs_move_pct, "%")}</span>`) +
      `<table><thead><tr><th>Condition</th><th>Days</th><th>Avg move when active</th>
       <th>Baseline</th><th>Ratio</th><th>Reading</th></tr></thead><tbody>${rows}</tbody></table>`));
  }

  const fresh = ctx.fresh_news || {};
  lines.push(h("div", "card", h("h3", "", "Fresh NSE corporate-disclosure check") +
    h("div", "note", fresh.available
      ? `Source status: OK · as of ${esc(fresh.as_of || "-")}. Disclosures are risk context, not a price prediction.`
      : `NOT AVAILABLE — ${esc(fresh.reason || "NSE disclosure source did not return data; no positive claim was made.")}`)));

  lines.push(h("div", "card", h("div", "note",
    `<b>Disclaimer:</b> ${esc(disclaimer || "")} / ${esc(stocks.disclaimer_hi || "")}`)));
  return lines.join("");
}

function arbTable(title, data, note) {
  const items = (data && data.items) || [];
  if (!data || (!data.available && !items.length)) {
    return h("div", "card", `<h3>${esc(title)}</h3>` +
      h("div", "note", `NOT AVAILABLE - ${esc((data && data.reason) || "reason not recorded")}`));
  }
  if (!items.length) {
    return h("div", "card", `<h3>${esc(title)}</h3>` +
      h("div", "note", "No instruments returned by the sources for this check."));
  }
  const rows = items.map((it) => {
    const cls = it.profitable ? "profitable" : "notprofit";
    const gap = it.quote_date_gap_days;
    return `<tr class="${cls}">
      <td>${esc(it.symbol || it.ticker || "-")}</td>
      <td>${fmt(it.nse_close ?? it.spot_close ?? it.market_price)}</td>
      <td>${fmt(it.bse_close ?? it.future_close ?? it.nav)}</td>
      <td>${gap === null || gap === undefined ? "\u2013" : fmt(gap) + "d"}</td>
      <td>${fmt(it.gross_spread_pct, "%")}</td>
      <td>${fmt(it.estimated_cost_pct, "%")}</td>
      <td>${fmt(it.net_spread_pct, "%")}</td>
      <td>${esc(it.buy_on)} &rarr; ${esc(it.sell_on)}</td>
      <td>${esc(it.expiry)}</td>
      <td><span class="badge ${it.profitable ? "ok" : "no"}">${it.profitable ? "worth checking" : "NOT PROFITABLE"}</span></td>
    </tr>`;
  }).join("");
  return h("div", "card",
    `<h3>${esc(title)}</h3>` +
    h("div", "note", esc(note || "")) +
    `<table><thead><tr><th>Symbol</th><th>Leg A price</th><th>Leg B price</th>
      <th>Quote gap</th><th>Gross spread</th><th>Est. cost</th><th>Net spread</th>
      <th>Action</th><th>Expiry</th><th>Verdict</th></tr></thead><tbody>${rows}</tbody></table>` +
    h("div", "note", "Quote gap = how many days apart the two legs were quoted. "
      + "A gap of 1+ days makes the difference a stale-price artefact, so it is "
      + "marked NOT PROFITABLE. Reasons for every NOT PROFITABLE item are in the raw "
      + "JSON at <a href='/last-result'>/last-result</a>."));
}

function renderArbitrage(arb, disclaimer) {
  if (!arb) return h("div", "card", h("div", "error-box", "no arbitrage result"));
  const cost = arb.cost_model || {};
  const lines = [h("div", "card",
    `<h3>Arbitrage scan &mdash; ${fmt(arb.profitable_count)} worth checking,
      ${fmt(arb.not_profitable_count)} not profitable</h3>` +
    h("div", "kv",
      `<span><b>As of:</b> ${esc(arb.as_of)}</span>
       <span><b>Round-trip cost used:</b> ${fmt(cost.round_trip_cost_pct, "%")}</span>
       <span><b>Minimum spread required:</b> ${fmt(cost.min_required_spread_pct, "%")}</span>`) +
    h("div", "note", esc(arb.method_note || "")) +
    h("div", "note", esc(cost.note || "")))];

  lines.push(arbTable("1) NSE vs BSE - same stock, two exchanges",
    arb.nse_vs_bse,
    "Spread % = (BSE close - NSE close) / NSE close x 100. Buy on the cheaper exchange."));
  const fut = arb.futures_vs_spot || {};
  lines.push(arbTable("2) Futures vs Spot (official NSE F&O bhavcopy)",
    { items: fut.items || [], available: fut.available, reason: fut.reason },
    fut.as_of ? `F&O bhavcopy date: ${fut.as_of}. Positive premium = futures above spot (cash-and-carry).`
              : ""));
  const etf = arb.etf_vs_nav || {};
  lines.push(arbTable("3) ETF price vs NAV (official AMFI feed)",
    { items: etf.items || [], available: etf.available, reason: etf.reason },
    etf.nav_date ? `AMFI NAV date: ${etf.nav_date}. Premium = ETF price above NAV.` : ""));

  const tops = arb.top_opportunities || [];
  if (tops.length) {
    lines.push(h("div", "card", h("h3", "", "Top opportunities by net spread") +
      h("ul", "reasons", tops.slice(0, 8).map((t) =>
        `<li>${esc(t.symbol || t.ticker)} (${esc(t.type)}): gross ${fmt(t.gross_spread_pct, "%")},
         net ${fmt(t.net_spread_pct, "%")} - ${esc(t.reason || "")}</li>`).join(""))));
  }
  lines.push(h("div", "card", h("div", "note",
    `<b>Disclaimer:</b> ${esc(disclaimer || "")} / ${esc(arb.disclaimer_hi || "")}`)));
  return lines.join("");
}

function renderResult(res) {
  const dis = res.disclaimer || "This is research only, not investment advice.";
  let html = "";
  if (!res.ok) {
    html += h("div", "card", h("div", "error-box", esc(res.error || "unknown error")) +
      (res.data_status ? h("div", "note", "Data status: " +
        esc(JSON.stringify(res.data_status))) : ""));
  }
  if (res.resume_message) {
    html += h("div", "card", h("div", "note", `<b>${esc(res.resume_message)}</b>`));
  }
  if (res.stocks) html += renderStocks(res.stocks, dis);
  if (res.arbitrage) html += renderArbitrage(res.arbitrage, dis);
  if (res.duration_seconds !== undefined) {
    html += h("div", "card", h("div", "note",
      `Run ${esc(res.run_id || "")} finished in ${fmt(res.duration_seconds)} s` +
      (res.used_from_cache ? " (served from cache)" : "")));
  }
  $("results").innerHTML = html;
}

async function runResearch() {
  const wantStocks = $("opt-stocks").checked;
  const wantArb = $("opt-arbitrage").checked;
  const hint = $("hint");
  if (!wantStocks && !wantArb) {
    hint.textContent = "Please tick at least one option (Stocks or Arbitrage).";
    hint.classList.add("error");
    return;
  }
  hint.classList.remove("error");
  hint.textContent = "Working... please wait (first run can take up to 30 seconds).";
  const mode = wantStocks && wantArb ? "both" : (wantStocks ? "stocks" : "arbitrage");
  const btn = $("run");
  btn.disabled = true;
  $("spinner").classList.remove("hidden");
  const started = Date.now();
  const tick = setInterval(() => {
    $("timer").textContent = `${((Date.now() - started) / 1000).toFixed(1)} s`;
  }, 100);
  try {
    const r = await fetch("/run-research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: mode,
        universe: $("universe").value,
        top_n: parseInt($("topn").value, 10),
        use_cache: $("usecache").checked,
      }),
    });
    const res = await r.json();
    renderResult(res);
    hint.textContent = res.ok ? "Done. Scroll down for results." : "See the message below.";
    hint.classList.toggle("error", !res.ok);
  } catch (e) {
    $("results").innerHTML =
      `<div class="card"><div class="error-box">Request failed: ${esc(e)}</div></div>`;
    hint.textContent = "Request failed. Is the server still running?";
    hint.classList.add("error");
  } finally {
    clearInterval(tick);
    btn.disabled = false;
    $("spinner").classList.add("hidden");
    loadStatus();
  }
}

async function runWalkForward() {
  const button = $("validate");
  button.disabled = true;
  $("validation").innerHTML = `<div class="card"><div class="note">Running point-in-time validation...</div></div>`;
  try {
    const r = await fetch("/walk-forward", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ universe: $("universe").value, horizon_days: 20, folds: 6 }),
    });
    const body = await r.json();
    const v = body.validation || {};
    if (!body.ok || !v.available) {
      $("validation").innerHTML = `<div class="card"><div class="error-box">Validation unavailable: ${esc(v.reason || body.error || "unknown error")}</div></div>`;
      return;
    }
    $("validation").innerHTML = `<div class="card"><h3>Walk-forward validation</h3>
      <div class="kv"><span><b>Universe:</b> ${esc(v.universe)}</span>
      <span><b>Horizon:</b> ${fmt(v.horizon_days)} trading days</span>
      <span><b>Folds:</b> ${fmt(v.folds)}</span><span><b>Signals:</b> ${fmt(v.signals)}</span>
      <span><b>Threshold:</b> ${fmt(v.selected_threshold)}</span>
      <span><b>Holdout:</b> ${fmt(v.holdout?.quality || v.quality)}</span>
      <span><b>Signal positive rate:</b> ${fmt(v.signal_positive_rate_pct, "%")}</span>
      <span><b>Signal mean return:</b> ${fmt(v.signal_mean_return_pct, "%")}</span>
      <span><b>Baseline positive rate:</b> ${fmt(v.baseline_positive_rate_pct, "%")}</span></div>
      <div class="note">${esc(v.note || "Historical validation only.")}</div></div>`;
  } catch (e) {
    $("validation").innerHTML = `<div class="card"><div class="error-box">Validation request failed: ${esc(e)}</div></div>`;
  } finally {
    button.disabled = false;
  }
}

$("run").addEventListener("click", runResearch);
$("validate").addEventListener("click", runWalkForward);
loadStatus();
