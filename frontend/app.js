/* ASX Ex-Dividend Recovery Pattern Analyzer — frontend logic.
   Vanilla JS + ECharts (vendored). No build step. */

"use strict";

/* ---------------- state ---------------- */
const state = {
  ticker: null,          // normalized, e.g. "BHP.AX" or "DEMO"
  data: null,            // /api/analyze payload
  charts: {},            // echarts instances by element id
  themeMode: localStorage.getItem("xd.theme") || "auto",
  saved: JSON.parse(localStorage.getItem("xd.saved") || "[]"),
  years: parseInt(localStorage.getItem("xd.years") || "20", 10),
};

const $ = (id) => document.getElementById(id);
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const MONTHS_FULL = ["January","February","March","April","May","June","July","August",
                     "September","October","November","December"];

/* ---------------- theme ---------------- */
function effectiveTheme() {
  if (state.themeMode === "auto")
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  return state.themeMode;
}
function applyTheme() {
  document.documentElement.setAttribute("data-theme", effectiveTheme());
  document.querySelectorAll("#theme-toggle button").forEach(b =>
    b.classList.toggle("on", b.dataset.mode === state.themeMode));
  if (state.data) renderAllCharts();   // re-render with new tokens
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function tokens() {
  return {
    surface: cssVar("--surface"), surface2: cssVar("--surface-2"),
    ink: cssVar("--ink"), ink2: cssVar("--ink-2"), muted: cssVar("--muted"),
    grid: cssVar("--grid"), baseline: cssVar("--baseline"),
    s1: cssVar("--series-1"), s2: cssVar("--series-2"), s3: cssVar("--series-3"),
    seq: [cssVar("--seq-100"), cssVar("--seq-200"), cssVar("--seq-300"),
          cssVar("--seq-400"), cssVar("--seq-500"), cssVar("--seq-600"), cssVar("--seq-700")],
    good: cssVar("--good"), goodText: cssVar("--good-text"),
    warning: cssVar("--warning"), critical: cssVar("--critical"),
  };
}

/* ---------------- utils ---------------- */
const fmt = {
  num: (v, d = 1) => v == null ? "—" : Number(v).toLocaleString("en-AU", { maximumFractionDigits: d, minimumFractionDigits: 0 }),
  money: (v, d = 2) => v == null ? "—" : "$" + Number(v).toLocaleString("en-AU", { minimumFractionDigits: d, maximumFractionDigits: d }),
  pct: (v, d = 1) => v == null ? "—" : Number(v).toFixed(d) + "%",
  days: (v) => v == null ? "—" : `${Math.round(v)} d`,
  date: (s) => {
    if (!s) return "—";
    const d = new Date(s + "T00:00:00");
    return d.toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" });
  },
};
function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c])); }
function displayTicker(tk) { return tk === "DEMO" ? "DEMO" : tk.replace(/\.AX$/, ""); }

/* ---------------- static vs live backend ----------------
   The same app runs two ways:
     • locally, talking to the FastAPI backend at /api/*
     • on GitHub Pages, where there is no backend — a scheduled job has already
       written the identical JSON to data/*.json, so API calls are rewritten
       to those files.
   config.js sets window.XD_CONFIG on the hosted build only.            */
const CFG = window.XD_CONFIG || null;
const IS_STATIC = !!(CFG && CFG.static);

function staticPath(url) {
  const [path, qs] = url.split("?");
  const q = new URLSearchParams(qs || "");
  const tk = (q.get("ticker") || "").toUpperCase();
  switch (path) {
    case "/api/analyze":       return `data/analyze/${encodeURIComponent(tk)}.json`;
    case "/api/consensus":     return `data/consensus/${encodeURIComponent(tk)}.json`;
    case "/api/future-events": return `data/future/${encodeURIComponent(tk)}.json`;
    case "/api/market-context":return `data/market-context.json`;
    case "/api/top20/status":  return `data/top20/status.json`;
    case "/api/top20/universe":return `data/top20/universe.json`;
    case "/api/top20/detail":  return `data/top20/detail/${encodeURIComponent(tk)}.json`;
    default: return null;
  }
}

function unavailableMessage(tk) {
  const list = (CFG && CFG.tickers) || [];
  return `${tk} isn't available on the hosted version. This site is refreshed daily by an ` +
    `automated job that pre-analyses the ASX 20: ${list.join(", ")}. ` +
    `To analyse any other ASX company, download and run the app locally — see the link at the bottom of the sidebar.`;
}

async function getJSON(url) {
  let target = url;
  if (IS_STATIC) {
    const mapped = staticPath(url);
    if (!mapped) throw new Error("That action needs the local app — this is the hosted version.");
    target = mapped;
  }
  const r = await fetch(target);
  if (IS_STATIC && r.status === 404) {
    const tk = (new URLSearchParams((url.split("?")[1]) || "")).get("ticker");
    throw new Error(tk ? unavailableMessage(tk.toUpperCase()) : "That data isn't on the hosted version.");
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
  return body;
}

/* ---------------- chart helpers ---------------- */
function chart(id) {
  const dom = $(id);
  if (state.charts[id]) { state.charts[id].dispose(); }
  const c = echarts.init(dom, null, { renderer: "canvas" });
  state.charts[id] = c;
  return c;
}
function baseAxis(t) {
  return {
    axisLine: { lineStyle: { color: t.baseline } },
    axisTick: { show: false },
    axisLabel: { color: t.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: t.grid, width: 1, type: "solid" } },
  };
}
function baseTooltip(t) {
  return {
    backgroundColor: t.surface, borderColor: t.grid, borderWidth: 1,
    textStyle: { color: t.ink, fontSize: 12.5 },
    extraCssText: "box-shadow:0 4px 16px rgba(0,0,0,.12);border-radius:9px;padding:10px 13px;",
    confine: true,
  };
}
function legendRow(containerId, keys) {
  const c = $(containerId);
  c.innerHTML = "";
  keys.forEach(k => {
    const item = el("span", "key");
    const dot = el("i", k.line ? "line" : "");
    if (k.line) dot.style.borderTopColor = k.color; else dot.style.background = k.color;
    item.appendChild(dot);
    item.appendChild(document.createTextNode(" " + k.label));
    c.appendChild(item);
  });
}

/* ---------------- rendering: charts ---------------- */
function renderPriceChart() {
  const t = tokens();
  const d = state.data;
  const c = chart("price-chart");
  const events = d.events.filter(e => e.ex_close != null);

  const exPoints = events.map(e => ({ value: [e.ex_date, e.ex_close], ev: e }));
  const recPoints = events.filter(e => e.recovered === true)
    .map(e => ({ value: [e.recovery_date, e.pre_div_close], ev: e }));

  legendRow("price-legend", [
    { label: "Close (split-adjusted)", color: t.s1, line: true },
    { label: "Ex-dividend date", color: t.s2 },
    { label: "Recovery point", color: t.s3 },
  ]);

  c.setOption({
    animation: false,
    grid: { left: 58, right: 18, top: 16, bottom: 74 },
    tooltip: {
      ...baseTooltip(t), trigger: "axis",
      axisPointer: { type: "cross", lineStyle: { color: t.baseline }, label: { backgroundColor: t.surface2, color: t.ink } },
      formatter: (ps) => {
        const lines = [];
        ps.forEach(p => {
          if (p.seriesName === "Close") lines.push(`<b>${fmt.date(p.value[0])}</b><br>Close ${fmt.money(p.value[1])}`);
          else if (p.data && p.data.ev) {
            const e = p.data.ev;
            if (p.seriesName === "Ex-dividend") {
              lines.push(`<b>Ex-dividend ${fmt.date(e.ex_date)}</b><br>Dividend ${fmt.money(e.dividend)} · fell ${fmt.pct(e.drop_pct)}<br>` +
                (e.recovered === true ? `Recovered in ${e.recovery_days} trading days` :
                 e.recovered === false ? "Did not recover within 250 trading days" : "Too recent to judge recovery"));
            } else {
              lines.push(`<b>Recovered ${fmt.date(e.recovery_date)}</b><br>Back to ${fmt.money(e.pre_div_close)} — ${e.recovery_days} trading days after ex-date`);
            }
          }
        });
        return lines.join("<hr style='border:none;border-top:1px solid " + t.grid + ";margin:6px 0'>");
      },
    },
    xAxis: { type: "time", ...baseAxis(t), splitLine: { show: false } },
    yAxis: { type: "value", scale: true, ...baseAxis(t),
             axisLabel: { color: t.muted, fontSize: 11, formatter: v => "$" + v } },
    dataZoom: [
      { type: "inside", throttle: 50 },
      { type: "slider", height: 34, bottom: 12, borderColor: t.grid,
        backgroundColor: "transparent", fillerColor: "rgba(120,140,180,.12)",
        dataBackground: { lineStyle: { color: t.baseline }, areaStyle: { color: t.surface2 } },
        selectedDataBackground: { lineStyle: { color: t.s1 }, areaStyle: { color: t.s1, opacity: .15 } },
        handleStyle: { color: t.surface, borderColor: t.baseline },
        moveHandleStyle: { color: t.baseline }, textStyle: { color: t.muted, fontSize: 10 } },
    ],
    series: [
      { name: "Close", type: "line", data: d.price_series, showSymbol: false,
        lineStyle: { color: t.s1, width: 2, join: "round", cap: "round" },
        areaStyle: { color: t.s1, opacity: 0.08 }, z: 1 },
      { name: "Ex-dividend", type: "scatter", data: exPoints, symbolSize: 9,
        itemStyle: { color: t.s2, borderColor: t.surface, borderWidth: 2 }, z: 3 },
      { name: "Recovery", type: "scatter", data: recPoints, symbolSize: 9, symbol: "diamond",
        itemStyle: { color: t.s3, borderColor: t.surface, borderWidth: 2 }, z: 2 },
    ],
  });
}

function renderTimelineChart() {
  const t = tokens();
  const d = state.data;
  const c = chart("timeline-chart");
  const evs = d.events.filter(e => e.pre_div_close != null);
  const HZN = 250;

  legendRow("timeline-legend", [
    { label: "Recovered — days to recovery", color: t.s1 },
    { label: `✕ No recovery within ${HZN} trading days`, color: t.critical },
    { label: "· Too recent to judge", color: t.muted },
  ]);

  const bars = evs.map(e => ({
    value: [e.ex_date, e.recovered === true ? e.recovery_days : (e.recovered === false ? HZN : null)],
    itemStyle: e.recovered === true
      ? { color: t.s1, borderRadius: [4, 4, 0, 0] }
      : { color: t.critical, opacity: .75, borderRadius: [4, 4, 0, 0] },
    ev: e,
  })).filter(b => b.value[1] != null);
  const pending = evs.filter(e => e.recovered === null)
    .map(e => ({ value: [e.ex_date, 4], itemStyle: { color: t.muted }, ev: e }));

  c.setOption({
    animation: false,
    grid: { left: 58, right: 18, top: 34, bottom: 40 },
    tooltip: {
      ...baseTooltip(t), trigger: "item",
      formatter: (p) => {
        const e = p.data.ev;
        const rows = [
          `<b>Ex-dividend ${fmt.date(e.ex_date)}</b> — dividend ${fmt.money(e.dividend)}`,
          e.high_date ? `Pre-div high&nbsp; ${fmt.money(e.high_price)} on ${fmt.date(e.high_date)}` : null,
          `Pre-div close ${fmt.money(e.pre_div_close)} (${fmt.date(e.pre_div_date)})`,
          `Ex-date close ${fmt.money(e.ex_close)} — fell ${fmt.pct(e.drop_pct)}`,
          e.low_date ? `Low&nbsp; ${fmt.money(e.low_price)} on ${fmt.date(e.low_date)} (+${e.low_days_after_ex} td)` : null,
          e.recovered === true ? `<b>Recovered ${fmt.date(e.recovery_date)} — ${e.recovery_days} trading days</b>`
            : e.recovered === false ? `<b>No recovery within ${HZN} trading days</b>`
            : "<b>Too recent to judge recovery</b>",
          e.flags && e.flags.length ? `<span style="color:${t.warning}">⚠ ${esc(e.flags.join("; "))}</span>` : null,
        ];
        return rows.filter(Boolean).join("<br>");
      },
    },
    xAxis: { type: "time", ...baseAxis(t), splitLine: { show: false } },
    yAxis: { type: "value", name: "trading days", nameTextStyle: { color: t.muted, fontSize: 11 },
             ...baseAxis(t) },
    series: [
      { name: "recovery", type: "bar", data: bars, barMaxWidth: 10, barMinWidth: 2 },
      { name: "pending", type: "bar", data: pending, barMaxWidth: 10, barMinWidth: 2, barGap: "-100%" },
    ],
  });
}

function heatCells(dist, rowIdx, nCols, keyOffset = 0) {
  const cells = [];
  for (let i = 1; i <= nCols; i++) {
    const v = dist?.[String(i + keyOffset)] ?? dist?.[i + keyOffset] ?? 0;
    cells.push([i - 1, rowIdx, v]);
  }
  return cells;
}

function renderMonthHeatmap() {
  const t = tokens();
  const dist = state.data.aggregate.distributions;
  const c = chart("month-heatmap");
  const rows = ["Recovery", "Pre-div high", "Post-div low"];
  const data = [
    ...heatCells(dist.recovery_month, 0, 12),
    ...heatCells(dist.high_month, 1, 12),
    ...heatCells(dist.low_month, 2, 12),
  ];
  const maxV = Math.max(1, ...data.map(d => d[2]));
  c.setOption({
    animation: false,
    grid: { left: 92, right: 14, top: 10, bottom: 54 },
    tooltip: {
      ...baseTooltip(t), position: "top",
      formatter: p => `<b>${MONTHS_FULL[p.value[0]]}</b> — ${rows[p.value[1]]}<br>${p.value[2]} event${p.value[2] === 1 ? "" : "s"}`,
    },
    xAxis: { type: "category", data: MONTHS, ...baseAxis(t), splitLine: { show: false },
             axisLine: { show: false } },
    yAxis: { type: "category", data: rows, ...baseAxis(t), splitLine: { show: false },
             axisLine: { show: false }, axisLabel: { color: t.ink2, fontSize: 12 } },
    visualMap: {
      min: 0, max: maxV, orient: "horizontal", left: "center", bottom: 0,
      itemHeight: 110, itemWidth: 11, text: ["more events", "0"],
      textStyle: { color: t.muted, fontSize: 11 },
      inRange: { color: [t.surface2, ...t.seq] },
    },
    series: [{
      type: "heatmap", data,
      itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 3 },
      label: { show: true, fontSize: 10.5, color: t.ink2,
               formatter: p => p.value[2] || "" },
      emphasis: { itemStyle: { shadowBlur: 6, shadowColor: "rgba(0,0,0,.25)" } },
    }],
  });
}

function renderLowDist() {
  const t = tokens();
  const agg = state.data.aggregate;
  const c = chart("lowdist-chart");
  const order = ["within 1 week", "within 2 weeks", "within 1 month", "within 2 months", "3+ months"];
  const counts = order.map(k => agg.distributions.low_bucket?.[k] ?? 0);
  const total = counts.reduce((a, b) => a + b, 0) || 1;
  const maxI = counts.indexOf(Math.max(...counts));
  c.setOption({
    animation: false,
    grid: { left: 46, right: 16, top: 18, bottom: 56 },
    tooltip: { ...baseTooltip(t), trigger: "item",
      formatter: p => `<b>${p.name}</b><br>${p.value} events (${(p.value / total * 100).toFixed(0)}%)` },
    xAxis: { type: "category", data: order.map(o => o.replace("within ", "≤ ")),
             ...baseAxis(t), splitLine: { show: false },
             axisLabel: { color: t.muted, fontSize: 11, interval: 0, rotate: 18 } },
    yAxis: { type: "value", ...baseAxis(t), minInterval: 1 },
    series: [{
      type: "bar", data: counts.map((v, i) => ({
        value: v,
        label: i === maxI && v > 0 ? { show: true, position: "top", color: cssVar("--ink"),
          fontWeight: 600, formatter: `${(v / total * 100).toFixed(0)}%` } : undefined,
      })),
      itemStyle: { color: t.s1, borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 24,
    }],
  });
}

function renderWeekHeatmap() {
  const t = tokens();
  const dist = state.data.aggregate.distributions;
  const c = chart("week-heatmap");
  const rows = ["Recovery", "Pre-div high", "Post-div low"];
  const data = [
    ...heatCells(dist.recovery_iso_week, 0, 53),
    ...heatCells(dist.high_iso_week, 1, 53),
    ...heatCells(dist.low_iso_week, 2, 53),
  ];
  const maxV = Math.max(1, ...data.map(d => d[2]));
  c.setOption({
    animation: false,
    grid: { left: 92, right: 14, top: 10, bottom: 54 },
    tooltip: {
      ...baseTooltip(t), position: "top",
      formatter: p => `<b>ISO week ${p.value[0] + 1}</b> — ${rows[p.value[1]]}<br>${p.value[2]} event${p.value[2] === 1 ? "" : "s"}`,
    },
    xAxis: { type: "category", data: Array.from({ length: 53 }, (_, i) => i + 1),
             ...baseAxis(t), splitLine: { show: false }, axisLine: { show: false },
             axisLabel: { color: t.muted, fontSize: 10, interval: 3 } },
    yAxis: { type: "category", data: rows, ...baseAxis(t), splitLine: { show: false },
             axisLine: { show: false }, axisLabel: { color: t.ink2, fontSize: 12 } },
    visualMap: {
      min: 0, max: maxV, orient: "horizontal", left: "center", bottom: 0,
      itemHeight: 110, itemWidth: 11, text: ["more events", "0"],
      textStyle: { color: t.muted, fontSize: 11 },
      inRange: { color: [t.surface2, ...t.seq] },
    },
    series: [{
      type: "heatmap", data,
      itemStyle: { borderColor: t.surface, borderWidth: 1.5, borderRadius: 2 },
    }],
  });
}

function renderYearChart() {
  const t = tokens();
  const by = state.data.aggregate.by_year || [];
  const c = chart("year-chart");
  legendRow("year-legend", [{ label: "Average recovery time (trading days)", color: t.s1 }]);
  const years = by.map(y => y.year);
  const vals = by.map(y => y.avg_recovery_days);
  const worst = vals.reduce((m, v, i) => v != null && (m.v == null || v > m.v) ? { v, i } : m, { v: null, i: -1 });
  c.setOption({
    animation: false,
    grid: { left: 52, right: 16, top: 34, bottom: 34 },
    tooltip: { ...baseTooltip(t), trigger: "item",
      formatter: p => {
        const y = by[p.dataIndex];
        return `<b>${y.year}</b> — ${y.events} event${y.events === 1 ? "" : "s"}<br>` +
          `Avg recovery ${y.avg_recovery_days != null ? y.avg_recovery_days.toFixed(0) + " td" : "n/a"}<br>` +
          `Avg ex-date drop ${fmt.pct(y.avg_drop_pct)}<br>` +
          `Recovered ${y.recovered}/${y.events}` +
          (y.insufficient ? ` · ${y.insufficient} too recent` : "") +
          `<br>Dividends paid ${fmt.money(y.total_dividends)}`;
      } },
    xAxis: { type: "category", data: years, ...baseAxis(t), splitLine: { show: false },
             axisLabel: { color: t.muted, fontSize: 10.5 } },
    yAxis: { type: "value", name: "trading days", nameTextStyle: { color: t.muted, fontSize: 11 },
             ...baseAxis(t) },
    series: [{
      type: "bar",
      data: vals.map((v, i) => ({
        value: v,
        label: i === worst.i && v != null ? { show: true, position: "top", color: cssVar("--ink"),
          fontSize: 10.5, formatter: `${v.toFixed(0)}` } : undefined,
      })),
      itemStyle: { color: t.s1, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 22,
    }],
  });
}

function renderAllCharts() {
  renderPriceChart(); renderTimelineChart(); renderMonthHeatmap();
  renderLowDist(); renderWeekHeatmap(); renderYearChart();
}

/* ---------------- rendering: panels ---------------- */
function renderHead() {
  const { meta, aggregate } = state.data;
  const label = displayTicker(state.ticker);
  $("r-title").textContent = (meta.name ? `${meta.name} ` : "") + `(${label})`;
  const bits = [];
  if (meta.sector) bits.push(meta.sector);
  bits.push(`${meta.first_date} → ${meta.last_date}`);
  bits.push(`${fmt.num(meta.n_bars, 0)} daily bars`);
  bits.push(`${aggregate.n_events} ex-div events in window`);
  bits.push(`Source: ${meta.source}`);
  $("r-sub").textContent = bits.join(" · ");
  updateSaveBtn();
}

function renderBanners() {
  const { meta } = state.data;
  const box = $("banners");
  box.innerHTML = "";
  const add = (cls, ic, html) => box.appendChild(el("div", `banner ${cls}`, `<span class="ic">${ic}</span><span>${html}</span>`));
  if (meta.demo) add("demo", "🧪", "DEMO MODE — this is randomly generated synthetic data, not a real security. Use it only to explore the interface.");
  if (meta.stale) add("error", "⚠", `Live download failed (<code>${esc(meta.fetch_error || "network error")}</code>). Showing cached data from ${meta.cache_age_hours} hours ago.`);
  else if (meta.from_cache) add("info", "ℹ", `Served from local cache (${meta.cache_age_hours}h old). Use “Refresh data” for the latest prices.`);
  (meta.warnings || []).forEach(w => add("warn", "⚠", esc(w)));
}

function card(label, value, note) {
  return `<div class="card"><div class="label">${label}</div><div class="value">${value}</div>${note ? `<div class="note">${note}</div>` : ""}</div>`;
}
function renderCards() {
  const a = state.data.aggregate;
  const h = [];
  h.push(card("Events analysed", `${a.n_usable}`, a.n_insufficient ? `${a.n_insufficient} too recent to judge` : `${a.n_decided} decided`));
  h.push(card("Avg recovery time", a.avg_recovery_days != null ? `${Math.round(a.avg_recovery_days)} <small>td</small>` : "—",
              a.median_recovery_days != null ? `median ${Math.round(a.median_recovery_days)} td` : null));
  h.push(card("Recovery success", a.success_rate_pct != null ? fmt.pct(a.success_rate_pct, 0) : "—",
              `within 250 td · ${a.n_recovered}/${a.n_decided}`));
  h.push(card("Avg dividend", a.avg_dividend != null ? fmt.money(a.avg_dividend) : "—", "per event"));
  h.push(card("Avg ex-date drop", a.avg_drop_pct != null ? fmt.pct(a.avg_drop_pct) : "—",
              a.avg_drop_vs_dividend != null ? `${a.avg_drop_vs_dividend.toFixed(2)}× the dividend` : null));
  h.push(card("Fastest recovery", a.fastest_recovery ? `${a.fastest_recovery.days} <small>td</small>` : "—",
              a.fastest_recovery ? fmt.date(a.fastest_recovery.ex_date) : null));
  h.push(card("Slowest recovery", a.slowest_recovery ? `${a.slowest_recovery.days} <small>td</small>` : "—",
              a.slowest_recovery ? fmt.date(a.slowest_recovery.ex_date) : null));
  h.push(card("Typical low", a.median_low_days_after_ex != null ? `${Math.round(a.median_low_days_after_ex)} <small>td after ex</small>` : "—",
              a.avg_low_drawdown_pct != null ? `avg drawdown ${fmt.pct(a.avg_low_drawdown_pct)}` : null));
  $("stat-cards").innerHTML = h.join("");
}

function renderInsights() {
  const ins = state.data.insights;
  const box = $("insights");
  box.innerHTML = "";
  (ins.headline || []).forEach((s, i) => box.appendChild(el("p", i === 0 ? "lead" : "", esc(s))));
  if (ins.timing && ins.timing.length) {
    const ul = el("ul", "obs timing");
    ins.timing.forEach(s => ul.appendChild(el("li", "", esc(s))));
    box.appendChild(ul);
  }
  if (ins.caveats && ins.caveats.length) {
    const cv = el("div", "caveats");
    ins.caveats.forEach(s => cv.appendChild(el("p", "", "· " + esc(s))));
    box.appendChild(cv);
  }
}

function renderSeasonal() {
  const s = state.data.insights.seasonal || {};
  const grid = $("seasonal");
  grid.innerHTML = "";
  const items = [
    ["weakest_month", "Best historical buying month"],
    ["recovery_month", "Most common recovery month"],
    ["strongest_month", "Best historical selling month"],
    ["ex_month_speed", "Fastest vs slowest ex-months"],
    ["typical_path", "Typical historical path"],
  ];
  let any = false;
  items.forEach(([k, label]) => {
    const v = s[k];
    if (!v) return;
    any = true;
    grid.appendChild(el("div", "seasonal-item",
      `<div class="k">${label}</div>` +
      (v.month ? `<div class="month">${esc(v.month)}</div>` : "") +
      `<div class="t">${esc(v.text)}</div>`));
  });
  if (!any) grid.appendChild(el("div", "side-empty", "Not enough decided events to summarise seasonal timing."));
}

function renderEventsTable() {
  const d = state.data;
  const evs = [...d.events].reverse();  // newest first
  $("events-sub").textContent = `${evs.length} ex-dividend events, newest first. All prices split-adjusted; “td” = trading days.`;
  const head = `<thead><tr>
    <th>Ex-date</th><th>Dividend</th><th>Pre-div close</th><th>Ex close</th><th>Drop</th>
    <th>High (before)</th><th>Low (after)</th><th>Low +td</th><th>Recovery</th><th>Days</th><th>Status</th></tr></thead>`;
  const rows = evs.map(e => {
    const status = e.recovered === true ? `<span class="pill ok">✓ recovered</span>`
      : e.recovered === false ? `<span class="pill no">✕ not in 250 td</span>`
      : `<span class="pill na">… pending</span>`;
    const flag = e.flags && e.flags.length ? ` <span class="flag-ic" title="${esc(e.flags.join("\n"))}">⚠</span>` : "";
    return `<tr>
      <td>${fmt.date(e.ex_date)}${flag}</td>
      <td>${fmt.money(e.dividend)}</td>
      <td>${fmt.money(e.pre_div_close)}</td>
      <td>${fmt.money(e.ex_close)}</td>
      <td>${e.drop_pct != null ? fmt.pct(e.drop_pct) : "—"}</td>
      <td>${e.high_date ? `${fmt.money(e.high_price)} · ${fmt.date(e.high_date)}` : "—"}</td>
      <td>${e.low_date ? `${fmt.money(e.low_price)} · ${fmt.date(e.low_date)}` : "—"}</td>
      <td>${e.low_days_after_ex ?? "—"}</td>
      <td>${e.recovery_date ? fmt.date(e.recovery_date) : "—"}</td>
      <td>${e.recovery_days ?? "—"}</td>
      <td>${status}</td></tr>`;
  }).join("");
  $("events-table").innerHTML = head + `<tbody>${rows}</tbody>`;
}

function renderConsensus(cons) {
  const box = $("consensus");
  if (!cons || (!cons.available)) {
    box.innerHTML = `<div class="side-empty">${esc(cons?.note || "No analyst data available for this security.")}` +
      (cons?.error ? `<br><small>${esc(cons.error)}</small>` : "") + `</div>`;
    return;
  }
  const t = tokens();
  let html = `<div class="consensus-grid"><div>`;
  if (cons.counts) {
    const c = cons.counts;
    const buy = (c.strongBuy || 0) + (c.buy || 0), hold = c.hold || 0, sell = (c.sell || 0) + (c.strongSell || 0);
    const total = buy + hold + sell || 1;
    const verdict = buy > hold + sell ? "Buy-leaning" : sell > buy + hold ? "Sell-leaning" : "Mixed / Hold";
    html += `<div class="mini-stat" style="margin-bottom:10px;"><div class="k">Overall sentiment (${total} analysts)</div><div class="v">${verdict}</div></div>`;
    html += `<div class="consensus-meter"><div class="meter-bar">
      <div style="width:${buy / total * 100}%;background:${t.good}" title="Buy ${buy}"></div>
      <div style="width:${hold / total * 100}%;background:${t.warning}" title="Hold ${hold}"></div>
      <div style="width:${sell / total * 100}%;background:${t.critical}" title="Sell ${sell}"></div>
    </div></div>
    <div class="consensus-counts">
      <span>🟢 Buy <b>${buy}</b>${c.strongBuy ? ` (incl. ${c.strongBuy} strong)` : ""}</span>
      <span>🟡 Hold <b>${hold}</b></span>
      <span>🔴 Sell <b>${sell}</b>${c.strongSell ? ` (incl. ${c.strongSell} strong)` : ""}</span>
    </div>`;
  }
  if (cons.price_targets) {
    const pt = cons.price_targets;
    html += `<div class="target-stats">
      ${pt.mean != null ? `<div class="mini-stat"><div class="k">Avg target</div><div class="v">${fmt.money(pt.mean)}</div></div>` : ""}
      ${pt.median != null ? `<div class="mini-stat"><div class="k">Consensus (median)</div><div class="v">${fmt.money(pt.median)}</div></div>` : ""}
      ${pt.low != null ? `<div class="mini-stat"><div class="k">Low</div><div class="v">${fmt.money(pt.low)}</div></div>` : ""}
      ${pt.high != null ? `<div class="mini-stat"><div class="k">High</div><div class="v">${fmt.money(pt.high)}</div></div>` : ""}
      ${pt.current != null ? `<div class="mini-stat"><div class="k">Last price</div><div class="v">${fmt.money(pt.current)}</div></div>` : ""}
    </div>`;
  }
  html += `<div class="tbl-note" style="margin-top:12px;">Updated ${new Date(cons.as_of).toLocaleDateString("en-AU")} · ${esc(cons.note)}</div>`;
  html += `</div><div>`;
  if (cons.recent_actions && cons.recent_actions.length) {
    html += `<div class="panel-sub" style="margin-bottom:6px;">Recent rating changes by named firms</div>
      <div class="tbl-wrap"><table class="data"><thead><tr><th>Firm</th><th>Action</th><th>To</th><th>From</th><th>Date</th></tr></thead><tbody>`;
    cons.recent_actions.forEach(a => {
      html += `<tr><td>${esc(a.firm || "—")}</td><td>${esc(a.action || "—")}</td><td>${esc(a.to_grade || "—")}</td><td>${esc(a.from_grade || "—")}</td><td>${a.date ? fmt.date(a.date) : "—"}</td></tr>`;
    });
    html += `</tbody></table></div><div class="tbl-note">${esc(cons.actions_note || "")}</div>`;
  } else {
    html += `<div class="side-empty">No recent named-firm rating changes reported by the data source.</div>`;
  }
  html += `</div></div>`;
  box.innerHTML = html;
}

function renderFutureEvents(fe) {
  const box = $("future-events");
  if (!fe || !fe.available) {
    box.innerHTML = `<div class="side-empty">${esc(fe?.note || "No upcoming events reported.")}</div>`;
    return;
  }
  const kv = (k, v) => `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  let html = "";
  if (fe.ex_dividend_date) html += kv("Next ex-dividend date", fmt.date(String(fe.ex_dividend_date).slice(0, 10)));
  if (fe.dividend_payment_date) html += kv("Dividend payment date", fmt.date(String(fe.dividend_payment_date).slice(0, 10)));
  if (fe.earnings_dates) {
    const ds = Array.isArray(fe.earnings_dates) ? fe.earnings_dates : [fe.earnings_dates];
    html += kv("Earnings announcement", ds.map(x => fmt.date(String(x).slice(0, 10))).join(" – "));
  }
  html += `<div class="tbl-note" style="margin-top:8px;">${esc(fe.note)}</div>`;
  box.innerHTML = html || `<div class="side-empty">No dates reported.</div>`;
}

function renderContext(ctx) {
  const box = $("market-context");
  if (!$("ctx-check").checked) { $("ctx-panel").style.display = "none"; return; }
  $("ctx-panel").style.display = "";
  if (!ctx || !ctx.available) {
    box.innerHTML = `<div class="side-empty">${esc(ctx?.note || "Market context unavailable.")}</div>`;
    return;
  }
  let html = "";
  ctx.items.forEach(it => {
    const up = it.chg_1m_pct >= 0;
    html += `<div class="kv"><span class="k">${esc(it.label)}</span>
      <span class="v">${fmt.num(it.last, 4)}<span class="ctx-chg ${up ? "up" : "down"}">${up ? "▲" : "▼"} ${Math.abs(it.chg_1m_pct).toFixed(1)}% /1mo</span></span></div>`;
  });
  html += `<div class="tbl-note" style="margin-top:8px;">${esc(ctx.note)}</div>`;
  box.innerHTML = html;
}

function renderMethodology() {
  const m = state.data.methodology;
  $("methodology").innerHTML = Object.values(m).map(v => `<p>${esc(v)}</p>`).join("");
  $("footnote").textContent = state.data.disclaimer;
}

/* ---------------- saved companies ---------------- */
function persistSaved() { localStorage.setItem("xd.saved", JSON.stringify(state.saved)); }
function renderSaved() {
  const list = $("saved-list");
  list.innerHTML = "";
  if (!state.saved.length) {
    list.appendChild(el("div", "side-empty", "No saved companies yet — analyse one and press ☆ Save."));
    return;
  }
  state.saved.forEach(tk => {
    const item = el("div", "saved-item" + (tk === state.ticker ? " active" : ""));
    item.appendChild(el("span", "tk", esc(displayTicker(tk))));
    const rm = el("button", "rm", "×");
    rm.title = "Remove";
    rm.onclick = (ev) => { ev.stopPropagation(); state.saved = state.saved.filter(x => x !== tk); persistSaved(); renderSaved(); updateSaveBtn(); };
    item.appendChild(rm);
    item.onclick = () => analyze(displayTicker(tk));
    list.appendChild(item);
  });
}
function updateSaveBtn() {
  const on = state.ticker && state.saved.includes(state.ticker);
  const b = $("save-btn");
  b.textContent = on ? "★ Saved" : "☆ Save";
  b.classList.toggle("saved-on", !!on);
}

/* ---------------- flow ---------------- */
let busy = false;
async function analyze(raw, opts = {}) {
  if (busy) return;
  const tk = (raw || "").trim().toUpperCase();
  if (!tk) return;
  busy = true;
  $("search-error").style.display = "none";
  $("analyze-btn").disabled = true;

  const hadReport = $("report").classList.contains("visible");
  if (hadReport) $("report").classList.add("refreshing");
  else { $("placeholder").style.display = "none"; $("loading").style.display = "block"; }
  $("loading-msg").textContent = `Downloading ${tk === "DEMO" ? "demo data" : tk + " price history"} — first fetch can take ~20s…`;

  try {
    const qs = `ticker=${encodeURIComponent(tk)}&years=${state.years}${opts.refresh ? "&refresh=true" : ""}`;
    const data = await getJSON(`/api/analyze?${qs}`);
    state.data = data;
    state.ticker = data.ticker;
    $("ticker-input").value = displayTicker(data.ticker);

    renderHead(); renderBanners(); renderCards(); renderInsights(); renderSeasonal();
    renderEventsTable(); renderMethodology(); renderSaved();
    $("loading").style.display = "none";
    $("report").classList.add("visible");
    $("report").classList.remove("refreshing");
    renderAllCharts();

    // secondary panels load after the main report (never block it)
    $("consensus").innerHTML = `<div class="side-empty">Loading analyst data…</div>`;
    $("future-events").innerHTML = `<div class="side-empty">Loading…</div>`;
    $("market-context").innerHTML = `<div class="side-empty">Loading…</div>`;
    getJSON(`/api/consensus?ticker=${encodeURIComponent(tk)}`).then(renderConsensus)
      .catch(e => renderConsensus({ available: false, note: "Analyst data unavailable: " + e.message }));
    getJSON(`/api/future-events?ticker=${encodeURIComponent(tk)}`).then(renderFutureEvents)
      .catch(e => renderFutureEvents({ available: false, note: "Unavailable: " + e.message }));
    if ($("ctx-check").checked)
      getJSON(`/api/market-context`).then(renderContext)
        .catch(e => renderContext({ available: false, note: "Unavailable: " + e.message }));
  } catch (e) {
    $("loading").style.display = "none";
    $("report").classList.remove("refreshing");
    if (!hadReport) $("placeholder").style.display = "block";
    const err = $("search-error");
    err.textContent = e.message;
    err.style.display = "block";
  } finally {
    busy = false;
    $("analyze-btn").disabled = false;
  }
}

/* ================= ASX TOP 20 OPPORTUNITIES ================= */
const t20 = {
  rows: [], universe: null, polling: null, scanning: false, lastState: null,
  filters: { sort: "score", band: "", minprob: 0, minyield: 0, hidelow: false },
};

const BAND_ORDER = { excellent: 4, strong: 3, neutral: 2, weak: 1, poor: 0, none: -1 };
const BAND_DOT = { excellent: "🟢", strong: "🟢", neutral: "🟡", weak: "🟠", poor: "🔴", none: "⚪" };

function switchView(view) {
  document.querySelectorAll("#side-nav .nav-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.view === view));
  $("view-top20").style.display = view === "top20" ? "" : "none";
  $("view-single").style.display = view === "single" ? "" : "none";
  if (view === "single" && state.data) setTimeout(() => renderAllCharts(), 30);
}

async function loadUniverse() {
  try {
    const u = await getJSON("/api/top20/universe");
    t20.universe = u;
    const wp = $("weights-preview");
    wp.innerHTML = "";
    Object.entries(u.weights).forEach(([k, v]) => {
      wp.appendChild(el("span", "weight-chip",
        `${esc(u.factor_labels[k] || k)} <b>${v}%</b>`));
    });
    $("t20-sub").textContent =
      `Ranked by Historical Opportunity Score — highest to lowest. Universe: S&P/ASX 20 as at ${u.as_at}.`;
    renderT20Method(u);
  } catch (e) { /* non-fatal */ }
}

function renderT20Method(u) {
  const rows = Object.entries(u.weights)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<tr><td>${esc(u.factor_labels[k] || k)}</td><td>${v}%</td></tr>`).join("");
  $("t20-method").innerHTML = `
    <p>Each company is analysed with the same ex-dividend recovery engine used on the
       single-company page. Eight factors are each scored 0–100, then combined using
       these weights:</p>
    <table class="method-table"><tbody>${rows}</tbody></table>
    <p><strong>Missing data is never guessed.</strong> If a factor cannot be computed
       (for example, no broker coverage), it is dropped and the remaining weights are
       re-normalised — the company is not penalised as though it scored zero. The
       "coverage" figure on each row shows how much of the weighting was actually
       available, and confidence falls when coverage or history is thin.</p>
    <p><strong>Bands:</strong> ${u.bands.map(b => `${BAND_DOT[b.key]} ${esc(b.label)} (${b.min}+)`).join(" · ")}</p>
    <p>${esc(u.disclaimer)}</p>`;
}

async function startScan(demo) {
  if (IS_STATIC) return;   // hosted build ships pre-computed results
  if (t20.scanning) return;
  t20.scanning = true;
  $("t20-scan-btn").disabled = true;
  $("t20-demo-btn").disabled = true;
  $("t20-intro").style.display = "none";
  $("t20-progress").style.display = "";
  $("t20-banners").innerHTML = "";
  $("t20-progress-fill").style.width = "0%";
  $("t20-progress-label").textContent = demo ? "Scanning demo companies…" : "Scanning the ASX 20…";
  $("t20-progress-count").textContent = "";
  $("t20-progress-current").textContent = demo ? "" :
    "Downloading 20 price histories — the first run takes a few minutes.";

  try {
    const r = await fetch(`/api/top20/scan?demo=${demo ? "true" : "false"}&years=${state.years}`,
      { method: "POST" });
    const body = await r.json();
    if (!body.started) throw new Error(body.reason || "Could not start the scan.");
  } catch (e) {
    t20.scanning = false;
    $("t20-scan-btn").disabled = false;
    $("t20-demo-btn").disabled = false;
    $("t20-progress").style.display = "none";
    $("t20-banners").innerHTML =
      `<div class="banner error"><span class="ic">⚠</span><span>${esc(e.message)}</span></div>`;
    return;
  }
  pollScan();
}

function pollScan() {
  clearInterval(t20.polling);
  t20.polling = setInterval(async () => {
    let s;
    try { s = await getJSON("/api/top20/status"); }
    catch { return; }
    t20.lastState = s;

    const pct = s.total ? Math.round(s.completed / s.total * 100) : 0;
    $("t20-progress-fill").style.width = pct + "%";
    $("t20-progress-count").textContent = `${s.completed} of ${s.total}`;
    if (s.results.length) {
      $("t20-progress-current").textContent =
        `${s.results.length} scored${s.failures.length ? `, ${s.failures.length} failed` : ""}…`;
    }

    if (s.status === "done" || s.status === "error") {
      clearInterval(t20.polling);
      t20.scanning = false;
      $("t20-scan-btn").disabled = false;
      $("t20-demo-btn").disabled = false;
      $("t20-progress").style.display = "none";
      if (s.status === "error") {
        $("t20-banners").innerHTML =
          `<div class="banner error"><span class="ic">⚠</span><span>Scan failed: ${esc(s.error || "unknown error")}</span></div>`;
        return;
      }
      renderT20(s);
    }
  }, 900);
}

function renderT20(s) {
  t20.rows = s.results || [];
  $("t20-results").style.display = "";
  $("t20-intro").style.display = "none";   // results supersede the "run a scan" card

  const banners = [];
  if (s.demo) banners.push(`<div class="banner demo"><span class="ic">🧪</span><span>DEMO DATA — these are 20 randomly generated synthetic companies, not real securities.</span></div>`);
  if (IS_STATIC && s.finished_at) banners.push(`<div class="banner info"><span class="ic">ℹ</span><span>Hosted version — this ranking was computed automatically on ${esc(new Date(s.finished_at).toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" }))} and refreshes daily. Click any company for its full breakdown.</span></div>`);
  if (s.failures.length) banners.push(`<div class="banner warn"><span class="ic">⚠</span><span>${s.failures.length} of ${s.total} companies could not be scored — see the bottom of this page.</span></div>`);
  const lowConf = t20.rows.filter(r => r.confidence === "low").length;
  if (lowConf) banners.push(`<div class="banner info"><span class="ic">ℹ</span><span>${lowConf} compan${lowConf === 1 ? "y has" : "ies have"} a low-confidence score (thin history or missing factors). Filter them out with the checkbox below.</span></div>`);
  $("t20-banners").innerHTML = banners.join("");

  const scored = t20.rows.filter(r => r.score != null);
  const avg = scored.length ? scored.reduce((a, r) => a + r.score, 0) / scored.length : null;
  const top = scored[0];
  const cards = [];
  cards.push(card("Companies ranked", `${scored.length}`, `of ${s.total} scanned`));
  if (top) cards.push(card("Top ranked", esc(top.ticker), `score ${top.score.toFixed(0)} · ${esc(top.band)}`));
  if (avg != null) cards.push(card("Average score", avg.toFixed(0), "across ranked companies"));
  const exc = scored.filter(r => r.band_key === "excellent" || r.band_key === "strong").length;
  cards.push(card("Strong or better", `${exc}`, "companies in the top two bands"));
  const finished = s.finished_at ? new Date(s.finished_at) : null;
  cards.push(card("Last scanned", finished ? finished.toLocaleTimeString("en-AU", { hour: "numeric", minute: "2-digit" }) : "—",
    finished ? finished.toLocaleDateString("en-AU") : null));
  $("t20-cards").innerHTML = cards.join("");

  $("t20-rank-sub").textContent =
    `Click any company for the full breakdown of its score. Sorted from strongest to weakest historical opportunity.`;

  if (s.failures.length) {
    $("t20-fail-panel").style.display = "";
    $("t20-failures").innerHTML = s.failures.map(f =>
      `<div class="kv"><span class="k">${esc(f.ticker)} — ${esc(f.name)}</span><span class="v" style="font-weight:400;color:var(--muted);font-size:12.5px;">${esc(f.error)}</span></div>`).join("");
  } else {
    $("t20-fail-panel").style.display = "none";
  }

  renderT20Table();
}

function t20Filtered() {
  const f = t20.filters;
  let rows = t20.rows.filter(r => r.score != null);
  if (f.band) rows = rows.filter(r => BAND_ORDER[r.band_key] >= BAND_ORDER[f.band]);
  if (f.minprob) rows = rows.filter(r => (r.success_rate_pct ?? -1) >= f.minprob);
  if (f.minyield) rows = rows.filter(r => (r.dividend_yield_pct ?? -1) >= f.minyield);
  if (f.hidelow) rows = rows.filter(r => r.confidence !== "low");

  const dir = { score: -1, success_rate_pct: -1, avg_drop_pct: -1, dividend_yield_pct: -1 };
  const key = f.sort;
  rows = rows.slice().sort((a, b) => {
    if (key === "best_buy_month") {
      const am = a.best_buy_month ? MONTHS_FULL.indexOf(a.best_buy_month) : 99;
      const bm = b.best_buy_month ? MONTHS_FULL.indexOf(b.best_buy_month) : 99;
      return am - bm || b.score - a.score;
    }
    if (key === "broker") {
      const rank = x => x.broker_label ? ({ "Buy-leaning": 0, "Mixed / Hold": 1, "Sell-leaning": 2 }[x.broker_label.label] ?? 3) : 4;
      return rank(a) - rank(b) || b.score - a.score;
    }
    if (key === "next_ex_date") {
      const av = a.next_ex_date || "9999", bv = b.next_ex_date || "9999";
      return av < bv ? -1 : av > bv ? 1 : b.score - a.score;
    }
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return b.score - a.score;
    if (av == null) return 1;
    if (bv == null) return -1;
    return (dir[key] === -1 ? bv - av : av - bv) || b.score - a.score;
  });
  return rows;
}

function scoreColour(key) {
  return { excellent: "var(--good)", strong: "var(--good)", neutral: "var(--warning)",
           weak: "var(--serious)", poor: "var(--critical)" }[key] || "var(--muted)";
}

function renderT20Table() {
  const rows = t20Filtered();
  const head = `<thead><tr>
    <th class="l">Rank</th><th class="l">Company</th>
    <th>Score</th><th class="l">Recommendation</th>
    <th>Recovery</th><th>Prob.</th><th>Avg drop</th><th>Yield</th>
    <th class="l">Best buy month</th><th class="l">Brokers</th>
    <th>Next ex-div</th></tr></thead>`;
  const body = rows.map(r => `
    <tr data-tk="${esc(r.ticker)}">
      <td class="rank-num l">${r.rank ?? "—"}</td>
      <td class="l"><span class="co-name"><span class="tk">${esc(r.ticker)}</span><span class="nm">${esc(r.name || "")}</span></span></td>
      <td><span class="score-cell"><span class="score-val">${r.score.toFixed(0)}</span>
        <span class="score-bar"><i style="width:${r.score}%;background:${scoreColour(r.band_key)}"></i></span></span></td>
      <td class="l"><span class="band ${r.band_key}"><i></i>${esc(r.band)}</span>
        ${r.confidence === "low" ? ` <span class="conf-tag low">low conf</span>` : ""}</td>
      <td>${r.median_recovery_days != null ? Math.round(r.median_recovery_days) + " td" : "—"}</td>
      <td>${r.success_rate_pct != null ? r.success_rate_pct.toFixed(0) + "%" : "—"}</td>
      <td>${r.avg_drop_pct != null ? r.avg_drop_pct.toFixed(1) + "%" : "—"}</td>
      <td>${r.dividend_yield_pct != null ? r.dividend_yield_pct.toFixed(1) + "%" : "—"}</td>
      <td class="l">${esc(r.best_buy_month || "—")}</td>
      <td class="l">${r.broker_label ? esc(r.broker_label.label) : "—"}</td>
      <td>${r.next_ex_date ? fmt.date(r.next_ex_date) : "—"}</td>
    </tr>`).join("");
  $("t20-table").innerHTML = head + `<tbody>${body}</tbody>`;
  $("t20-count-note").textContent =
    `Showing ${rows.length} of ${t20.rows.filter(r => r.score != null).length} ranked companies.` +
    ` Click a row for the full score breakdown.`;
  $("t20-table").querySelectorAll("tbody tr").forEach(tr =>
    tr.addEventListener("click", () => openDrawer(tr.dataset.tk)));
}

async function openDrawer(ticker) {
  const row = t20.rows.find(r => r.ticker === ticker);
  if (!row) return;
  $("drawer-title").textContent = `${row.name || row.ticker} (${row.ticker})`;
  $("drawer-sub").textContent = `Rank #${row.rank ?? "—"} · score ${row.score != null ? row.score.toFixed(0) : "—"} · ${row.confidence} confidence · ${row.coverage_pct}% factor coverage`;
  $("drawer-body").innerHTML = `<div class="side-empty">Loading breakdown…</div>`;
  $("t20-drawer").classList.add("open");
  $("t20-drawer").setAttribute("aria-hidden", "false");
  $("drawer-backdrop").classList.add("open");

  let d;
  try { d = await getJSON(`/api/top20/detail?ticker=${encodeURIComponent(ticker)}`); }
  catch (e) {
    $("drawer-body").innerHTML = `<div class="banner error"><span class="ic">⚠</span><span>${esc(e.message)}</span></div>`;
    return;
  }
  renderDrawer(row, d);
}

function renderDrawer(row, d) {
  const agg = d.aggregate || {};
  const seasonal = (d.insights && d.insights.seasonal) || {};
  const ctx = d.context || {};
  const h = [];

  h.push(`<div class="drawer-summary">${esc(row.summary || "")}</div>`);

  h.push(`<h4>Key historical statistics</h4><div class="drawer-stats">
    ${dstat("Avg recovery", agg.avg_recovery_days != null ? Math.round(agg.avg_recovery_days) + " td" : "—")}
    ${dstat("Median recovery", agg.median_recovery_days != null ? Math.round(agg.median_recovery_days) + " td" : "—")}
    ${dstat("Recovery rate", agg.success_rate_pct != null ? agg.success_rate_pct.toFixed(0) + "%" : "—")}
    ${dstat("Avg ex-date drop", agg.avg_drop_pct != null ? agg.avg_drop_pct.toFixed(1) + "%" : "—")}
    ${dstat("Typical low", agg.median_low_days_after_ex != null ? Math.round(agg.median_low_days_after_ex) + " td after" : "—")}
    ${dstat("Events analysed", agg.n_usable ?? "—")}
    ${dstat("Fastest", agg.fastest_recovery ? agg.fastest_recovery.days + " td" : "—")}
    ${dstat("Slowest", agg.slowest_recovery ? agg.slowest_recovery.days + " td" : "—")}
  </div>`);

  h.push(`<h4>Score breakdown</h4><div class="factor-list">`);
  d.factors.forEach(f => {
    const avail = f.available;
    h.push(`<div class="factor${avail ? "" : " unavailable"}">
      <span class="fname">${esc(f.label)} <span class="fweight">${f.weight}% weight</span></span>
      <span class="fscore">${avail ? f.score.toFixed(0) : "n/a"}</span>
      <span class="fnote">${esc(f.note || "")}</span>
      ${avail ? `<span class="fmeter"><i style="width:${f.score}%"></i></span>` : ""}
    </div>`);
  });
  h.push(`</div>`);

  h.push(`<h4>Historical strengths</h4><ul class="plain good">${
    (d.strengths || []).map(s => `<li>${esc(s)}</li>`).join("")}</ul>`);
  h.push(`<h4>Historical risks</h4><ul class="plain bad">${
    (d.risks || []).map(s => `<li>${esc(s)}</li>`).join("")}</ul>`);

  const seasonBits = [];
  if (seasonal.weakest_month) seasonBits.push(`<li><strong>Best historical buying period:</strong> ${esc(seasonal.weakest_month.text)}</li>`);
  if (seasonal.strongest_month) seasonBits.push(`<li><strong>Best historical selling period:</strong> ${esc(seasonal.strongest_month.text)}</li>`);
  if (seasonal.recovery_month) seasonBits.push(`<li>${esc(seasonal.recovery_month.text)}</li>`);
  if (seasonal.typical_path) seasonBits.push(`<li>${esc(seasonal.typical_path.text)}</li>`);
  if (seasonBits.length) h.push(`<h4>Seasonal trends</h4><ul class="plain">${seasonBits.join("")}</ul>`);

  const cons = d.consensus || {};
  h.push(`<h4>Broker consensus</h4>`);
  if (cons.available && cons.counts) {
    const c = cons.counts;
    const buy = (c.strongBuy || 0) + (c.buy || 0), hold = c.hold || 0, sell = (c.sell || 0) + (c.strongSell || 0);
    const pt = cons.price_targets || {};
    h.push(`<div class="consensus-counts" style="margin-top:0;">
      <span>🟢 Buy <b>${buy}</b></span><span>🟡 Hold <b>${hold}</b></span><span>🔴 Sell <b>${sell}</b></span>
      ${pt.mean != null ? `<span>Avg target <b>${fmt.money(pt.mean)}</b></span>` : ""}
      ${ctx.current_price != null ? `<span>Last <b>${fmt.money(ctx.current_price)}</b></span>` : ""}
    </div><div class="tbl-note">${esc(cons.note || "")}</div>`);
  } else {
    h.push(`<div class="side-empty">${esc(cons.note || "No analyst data available.")}</div>`);
  }

  const fe = d.future_events || {};
  h.push(`<h4>Upcoming dates</h4>`);
  if (fe.available) {
    let k = "";
    if (fe.ex_dividend_date) k += `<div class="kv"><span class="k">Next ex-dividend</span><span class="v">${fmt.date(String(fe.ex_dividend_date).slice(0, 10))}</span></div>`;
    if (fe.dividend_payment_date) k += `<div class="kv"><span class="k">Payment date</span><span class="v">${fmt.date(String(fe.dividend_payment_date).slice(0, 10))}</span></div>`;
    if (fe.earnings_dates) {
      const ds = Array.isArray(fe.earnings_dates) ? fe.earnings_dates : [fe.earnings_dates];
      k += `<div class="kv"><span class="k">Earnings</span><span class="v">${ds.map(x => fmt.date(String(x).slice(0, 10))).join(" – ")}</span></div>`;
    }
    h.push(k + `<div class="tbl-note">${esc(fe.note || "")}</div>`);
  } else {
    h.push(`<div class="side-empty">${esc(fe.note || "No upcoming dates published.")}</div>`);
  }

  if (ctx.td_since_last_ex != null) {
    h.push(`<h4>Where it sits right now</h4><div class="kv"><span class="k">Trading days since last ex-dividend</span><span class="v">${ctx.td_since_last_ex}</span></div>
      ${ctx.last_ex_date ? `<div class="kv"><span class="k">Last ex-dividend date</span><span class="v">${fmt.date(ctx.last_ex_date)}</span></div>` : ""}
      ${ctx.last_pre_div_close != null ? `<div class="kv"><span class="k">Last pre-dividend close</span><span class="v">${fmt.money(ctx.last_pre_div_close)}</span></div>` : ""}
      ${ctx.current_price != null ? `<div class="kv"><span class="k">Current price${ctx.price_as_at ? " (" + fmt.date(ctx.price_as_at) + ")" : ""}</span><span class="v">${fmt.money(ctx.current_price)}</span></div>` : ""}`);
  }

  if (!row.demo) {
    h.push(`<h4>Go deeper</h4><button class="ghost" id="drawer-full">Open full analysis for ${esc(row.ticker)} →</button>`);
  }
  h.push(`<div class="tbl-note" style="margin-top:22px;">${esc((t20.universe && t20.universe.disclaimer) || "")}</div>`);

  $("drawer-body").innerHTML = h.join("");
  const btn = $("drawer-full");
  if (btn) btn.addEventListener("click", () => {
    closeDrawer();
    switchView("single");
    analyze(row.ticker);
  });
}

function dstat(k, v) {
  return `<div class="drawer-stat"><div class="k">${esc(k)}</div><div class="v">${esc(String(v))}</div></div>`;
}

function closeDrawer() {
  $("t20-drawer").classList.remove("open");
  $("t20-drawer").setAttribute("aria-hidden", "true");
  $("drawer-backdrop").classList.remove("open");
}

/* ---------------- wiring ---------------- */
window.addEventListener("resize", () => Object.values(state.charts).forEach(c => c.resize()));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (state.themeMode === "auto") applyTheme();
});
document.querySelectorAll("#theme-toggle button").forEach(b =>
  b.addEventListener("click", () => { state.themeMode = b.dataset.mode; localStorage.setItem("xd.theme", state.themeMode); applyTheme(); }));

$("analyze-btn").addEventListener("click", () => analyze($("ticker-input").value));
$("ticker-input").addEventListener("keydown", e => { if (e.key === "Enter") analyze($("ticker-input").value); });
document.querySelectorAll(".chip").forEach(c => c.addEventListener("click", () => analyze(c.dataset.tk)));
$("refresh-btn").addEventListener("click", () => state.ticker && analyze(displayTicker(state.ticker), { refresh: true }));
$("save-btn").addEventListener("click", () => {
  if (!state.ticker) return;
  if (state.saved.includes(state.ticker)) state.saved = state.saved.filter(x => x !== state.ticker);
  else state.saved.push(state.ticker);
  persistSaved(); renderSaved(); updateSaveBtn();
});
$("years-select").value = String(state.years);
$("years-select").addEventListener("change", () => {
  state.years = parseInt($("years-select").value, 10);
  localStorage.setItem("xd.years", String(state.years));
  if (state.ticker) analyze(displayTicker(state.ticker));
});
$("ctx-check").addEventListener("change", () => {
  if (!state.data) return;
  if ($("ctx-check").checked) getJSON(`/api/market-context`).then(renderContext).catch(() => {});
  else renderContext(null);
});

document.querySelectorAll("#side-nav .nav-btn").forEach(b =>
  b.addEventListener("click", () => switchView(b.dataset.view)));
$("t20-scan-btn").addEventListener("click", () => startScan(false));
$("t20-demo-btn").addEventListener("click", () => startScan(true));
$("drawer-close").addEventListener("click", closeDrawer);
$("drawer-backdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

$("t20-sort").addEventListener("change", () => { t20.filters.sort = $("t20-sort").value; renderT20Table(); });
$("t20-band").addEventListener("change", () => { t20.filters.band = $("t20-band").value; renderT20Table(); });
$("t20-minprob").addEventListener("change", () => { t20.filters.minprob = +$("t20-minprob").value; renderT20Table(); });
$("t20-minyield").addEventListener("change", () => { t20.filters.minyield = +$("t20-minyield").value; renderT20Table(); });
$("t20-hidelow").addEventListener("change", () => { t20.filters.hidelow = $("t20-hidelow").checked; renderT20Table(); });
$("t20-reset").addEventListener("click", () => {
  t20.filters = { sort: "score", band: "", minprob: 0, minyield: 0, hidelow: false };
  $("t20-sort").value = "score"; $("t20-band").value = ""; $("t20-minprob").value = "0";
  $("t20-minyield").value = "0"; $("t20-hidelow").checked = false;
  renderT20Table();
});

/* Hosted build: no backend to scan with, so present the pre-computed results
   and make plain what is and isn't possible here. */
function applyStaticMode() {
  if (!IS_STATIC) return;
  ["t20-scan-btn", "t20-demo-btn", "refresh-btn", "save-btn"].forEach(id => {
    const b = $(id); if (b) b.style.display = "none";
  });
  const ys = $("years-select");
  if (ys) { ys.disabled = true; ys.title = "Fixed at 20 years on the hosted version."; }
  const built = CFG.builtAt ? new Date(CFG.builtAt) : null;
  const when = built ? built.toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" }) : "recently";
  const note = el("div", "side-disclaimer",
    `<strong>Hosted version.</strong> Data refreshed automatically — last updated ${esc(when)}. ` +
    `Covers the ASX 20 only. <a href="https://github.com/Banananadia13/asx-ex-dividend-analyzer" target="_blank" rel="noopener">Download the full app</a> to analyse any ASX company.`);
  const side = document.querySelector(".sidebar .side-disclaimer");
  if (side && side.parentNode) side.parentNode.insertBefore(note, side);
  const ph = $("placeholder");
  if (ph) {
    const ex = ph.querySelector(".examples");
    if (ex) {
      ex.innerHTML = "";
      (CFG.tickers || []).forEach(t => {
        const c = el("button", "chip", esc(t));
        c.dataset.tk = t;
        c.addEventListener("click", () => analyze(t));
        ex.appendChild(c);
      });
    }
    const p = ph.querySelector("p");
    if (p) p.textContent = "Pick a company below to see 20 years of price behaviour around every ex-dividend date.";
  }
}
applyStaticMode();

// If a scan already ran in this session (e.g. page reload), show it immediately.
(async () => {
  try {
    const s = await getJSON("/api/top20/status");
    if (s.status === "done" && s.results.length) renderT20(s);
    else if (s.status === "running") {
      t20.scanning = true;
      $("t20-intro").style.display = "none";
      $("t20-progress").style.display = "";
      $("t20-scan-btn").disabled = true; $("t20-demo-btn").disabled = true;
      pollScan();
    }
  } catch { /* ignore */ }
})();

applyTheme();
renderSaved();
loadUniverse();
