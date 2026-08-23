"""
ASX Ex-Dividend Recovery Pattern Analyzer — FastAPI backend.

Run:  uvicorn main:app  (or use ../run.sh)
Serves the frontend at /  and the JSON API under /api/.
"""
from __future__ import annotations

import os
import traceback

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import analysis
import data_source as ds
import insights as ins_mod
import opportunity as op
import scanner

app = FastAPI(title="ASX Ex-Dividend Recovery Pattern Analyzer", version="1.0")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


@app.get("/api/health")
def health():
    return {"ok": True}


def build_analysis_payload(tk: str, years: int = 20, refresh: bool = False) -> dict:
    """The full single-company analysis payload.

    Shared by the live API endpoint and by build_site.py, which pre-renders the
    same JSON for the static GitHub Pages build — so the hosted site and the
    local app can never drift apart.
    """
    px, divs, meta = ds.fetch_history(tk, refresh=refresh)
    px_adj = analysis.split_adjust(px)
    events = analysis.analyze_events(px_adj, divs, years=years)
    agg = analysis.aggregate(events)
    label = meta.get("name") or tk
    insights = ins_mod.build_insights(
        label.split(" (")[0] if meta.get("demo") else (tk[:-3] if tk.endswith(".AX") else tk),
        agg, years)

    # decimate the long price series for charting (weekly beyond 5y, daily within)
    import pandas as pd
    closes = px_adj["Close"]
    cutoff_daily = closes.index.max() - pd.DateOffset(years=5)
    daily = closes[closes.index >= cutoff_daily]
    older = closes[closes.index < cutoff_daily].resample("W-FRI").last().dropna()
    chart = pd.concat([older, daily]).sort_index()
    price_series = [[str(ts.date()), round(float(v), 4)] for ts, v in chart.items()]

    return {
        "ticker": tk,
        "meta": meta,
        "methodology": {
            "recovery": "Recovery = first trading day on/after the ex-dividend date whose close ≥ the last close before the ex-date (raw prices, split-adjusted, NOT dividend-adjusted). Ex-date = day 0.",
            "high_window": f"Pre-dividend high = highest close between the previous ex-date and this one, capped at {analysis.LOOKBACK_CAP} trading days.",
            "low_window": "Post-dividend low = lowest close from the ex-date to recovery (or to the 250-day horizon if no recovery).",
            "horizon": f"{analysis.HORIZON} trading days search horizon; events with <{analysis.MIN_POST_DAYS} post-ex trading days and no recovery are 'insufficient data'.",
            "prices": "Yahoo Finance raw daily closes, adjusted for splits only. Dividend amounts as reported by Yahoo (split-adjusted).",
        },
        "events": [e.to_dict() for e in events],
        "aggregate": agg,
        "insights": insights,
        "price_series": price_series,
        "disclaimer": ("Historical observations only — not investment advice or future predictions. "
                       "Past performance does not guarantee future results."),
    }


@app.get("/api/analyze")
def analyze(ticker: str = Query(..., min_length=1, max_length=12),
            years: int = Query(20, ge=1, le=50),
            refresh: bool = False):
    """Full analysis payload for one ticker."""
    try:
        tk = ds.normalize_ticker(ticker)
    except ds.DataError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        return build_analysis_payload(tk, years=years, refresh=refresh)
    except ds.DataError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")


@app.get("/api/consensus")
def consensus(ticker: str = Query(..., min_length=1, max_length=12)):
    try:
        tk = ds.normalize_ticker(ticker)
    except ds.DataError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ds.fetch_consensus(tk)


@app.get("/api/future-events")
def future_events(ticker: str = Query(..., min_length=1, max_length=12)):
    try:
        tk = ds.normalize_ticker(ticker)
    except ds.DataError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ds.fetch_future_events(tk)


@app.get("/api/market-context")
def market_context():
    return ds.fetch_market_context()


@app.get("/api/top20/universe")
def top20_universe():
    """The company list the ranking scans, and the scoring weights."""
    return {
        "universe": [{"ticker": t, "name": n} for t, n in op.ASX20],
        "as_at": op.UNIVERSE_AS_AT,
        "weights": op.WEIGHTS,
        "factor_labels": op.FACTOR_LABELS,
        "bands": [{"min": m, "label": l, "key": k} for m, l, k in op.BANDS],
        "note": ("Default list is the S&P/ASX 20 index membership. S&P reviews the "
                 "index quarterly, so verify against asx.com.au if precision matters."),
        "disclaimer": op.DISCLAIMER,
    }


@app.post("/api/top20/scan")
def top20_scan(years: int = Query(20, ge=1, le=50),
               refresh: bool = False,
               demo: bool = False):
    """Kick off a background scan of the universe."""
    return scanner.start_scan(years=years, refresh=refresh, demo=demo)


@app.get("/api/top20/status")
def top20_status():
    """Progress + ranked results so far (summary rows only)."""
    return scanner.get_state(include_details=False)


@app.get("/api/top20/detail")
def top20_detail(ticker: str = Query(..., min_length=1, max_length=12)):
    """Full score breakdown for one company from the last scan."""
    d = scanner.get_detail(ticker.strip().upper())
    if d is None:
        raise HTTPException(status_code=404,
                            detail="No scanned result for that ticker. Run a scan first.")
    return d


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
