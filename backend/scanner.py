"""
Background scanner for the ASX Top 20 Opportunities dashboard.

Scanning 20 companies means 20 full price histories plus analyst and calendar
lookups, so it runs as a background job with live progress rather than one long
blocking request. Design notes:

- Concurrency is capped (default 4 workers) — Yahoo Finance throttles bursts,
  and a throttled request that returns partial data is worse than a slow one.
- Each ticker is isolated: one failure is recorded against that ticker and the
  scan continues. A partial ranking is always better than no ranking, and the
  UI shows exactly which companies could not be scored and why.
- Results are cached in memory with the fetch timestamp so the dashboard can
  say how fresh it is; the underlying price data uses the normal disk cache.
"""
from __future__ import annotations

import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import analysis
import data_source as ds
import insights as ins_mod
import opportunity as op

MAX_WORKERS = 4

_lock = threading.Lock()
_state = {
    "status": "idle",        # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "completed": 0,
    "current": [],           # tickers currently in flight
    "results": [],           # ranked, scored companies
    "failures": [],          # {ticker, name, error}
    "years": 20,
    "demo": False,
    "universe_as_at": op.UNIVERSE_AS_AT,
}


def get_state(include_details: bool = False) -> dict:
    with _lock:
        s = dict(_state)
        s["results"] = [
            r if include_details else {k: v for k, v in r.items() if k != "detail"}
            for r in _state["results"]
        ]
        s["current"] = list(_state["current"])
        s["failures"] = list(_state["failures"])
        s["weights"] = dict(op.WEIGHTS)
        s["disclaimer"] = op.DISCLAIMER
    return s


def get_detail(ticker: str):
    with _lock:
        for r in _state["results"]:
            if r["ticker"] == ticker or r["symbol"] == ticker:
                return r.get("detail")
    return None


def _analyse_one(code: str, name: str, years: int, refresh: bool, demo: bool) -> dict:
    """Full analysis + score for a single company. Raises on unrecoverable error."""
    symbol = code if demo else ds.normalize_ticker(code)
    px, divs, meta = ds.fetch_history(symbol, refresh=refresh)
    px_adj = analysis.split_adjust(px)
    ev_objs = analysis.analyze_events(px_adj, divs, years=years)
    events = [e.to_dict() for e in ev_objs]
    agg = analysis.aggregate(ev_objs)

    consensus = ds.fetch_consensus(symbol)
    future = ds.fetch_future_events(symbol)
    ctx = op.build_context(px_adj, events, future)
    scored = op.score_company(agg, events, consensus, ctx)
    strengths, risks = op.strengths_and_risks(agg, scored, ctx)
    text = ins_mod.build_insights(code, agg, years)

    return {
        "ticker": code,
        "symbol": symbol,
        "name": meta.get("name") or name,
        "score": scored["score"],
        "band": scored["band"],
        "band_key": scored["band_key"],
        "confidence": scored["confidence"],
        "coverage_pct": scored["coverage_pct"],
        # sortable/filterable columns
        "median_recovery_days": agg.get("median_recovery_days"),
        "avg_recovery_days": agg.get("avg_recovery_days"),
        "success_rate_pct": agg.get("success_rate_pct"),
        "avg_drop_pct": agg.get("avg_drop_pct"),
        "n_events": agg.get("n_usable"),
        "n_decided": agg.get("n_decided"),
        "dividend_yield_pct": ctx.get("dividend_yield_pct"),
        "current_price": ctx.get("current_price"),
        "price_as_at": ctx.get("price_as_at"),
        "next_ex_date": ctx.get("next_ex_date"),
        "td_since_last_ex": ctx.get("td_since_last_ex"),
        "best_buy_month": _month_name(agg, "low_month"),
        "best_sell_month": _month_name(agg, "high_month"),
        "broker_label": _broker_label(consensus),
        "data_warnings": meta.get("warnings", []),
        "demo": bool(meta.get("demo")),
        "detail": {
            "aggregate": agg,
            "factors": scored["factors"],
            "weights": scored["weights"],
            "strengths": strengths,
            "risks": risks,
            "insights": text,
            "consensus": consensus,
            "future_events": future,
            "context": {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in ctx.items()},
            "meta": meta,
            "recent_events": events[-8:],
        },
    }


def _month_name(agg: dict, key: str):
    mode = (agg.get("modes") or {}).get(key)
    if not mode or mode.get("value") is None:
        return None
    return op.MONTH_NAMES[int(mode["value"]) - 1]


def _broker_label(consensus: dict):
    if not consensus or not consensus.get("available") or not consensus.get("counts"):
        return None
    c = consensus["counts"]
    buy = (c.get("strongBuy", 0) or 0) + (c.get("buy", 0) or 0)
    hold = c.get("hold", 0) or 0
    sell = (c.get("sell", 0) or 0) + (c.get("strongSell", 0) or 0)
    total = buy + hold + sell
    if not total:
        return None
    if buy > hold + sell:
        v = "Buy-leaning"
    elif sell > buy + hold:
        v = "Sell-leaning"
    else:
        v = "Mixed / Hold"
    return {"label": v, "buy": buy, "hold": hold, "sell": sell, "total": total}


def _rank(results: list) -> list:
    scored = [r for r in results if r.get("score") is not None]
    unscored = [r for r in results if r.get("score") is None]
    scored.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(scored, 1):
        r["rank"] = i
        r["summary"] = op.summarise(r["name"], r["ticker"], i,
                                    r["detail"]["aggregate"],
                                    {"score": r["score"], "band": r["band"],
                                     "confidence": r["confidence"],
                                     "coverage_pct": r["coverage_pct"],
                                     "factors": r["detail"]["factors"]},
                                    r["detail"]["context"])
    for r in unscored:
        r["rank"] = None
        r["summary"] = (f"{r['name']} ({r['ticker']}) could not be scored — "
                        "insufficient historical ex-dividend data.")
    return scored + unscored


def _run(universe, years, refresh, demo):
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {}
            for code, name in universe:
                fut = pool.submit(_analyse_one, code, name, years, refresh, demo)
                futures[fut] = (code, name)

            for fut in as_completed(futures):
                code, name = futures[fut]
                try:
                    rec = fut.result()
                    with _lock:
                        _state["results"].append(rec)
                except Exception as exc:
                    traceback.print_exc()
                    with _lock:
                        _state["failures"].append({
                            "ticker": code, "name": name, "error": str(exc)[:300]})
                finally:
                    with _lock:
                        _state["completed"] += 1
                        if code in _state["current"]:
                            _state["current"].remove(code)

        with _lock:
            _state["results"] = _rank(_state["results"])
            _state["status"] = "done"
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        traceback.print_exc()
        with _lock:
            _state["status"] = "error"
            _state["error"] = str(exc)[:500]
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()


def start_scan(years: int = 20, refresh: bool = False, demo: bool = False,
               universe=None) -> dict:
    with _lock:
        if _state["status"] == "running":
            return {"started": False, "reason": "A scan is already running."}
        uni = universe or ([(f"DEMO{i:02d}", f"Demo Company {i:02d}") for i in range(1, 21)]
                           if demo else list(op.ASX20))
        _state.update({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "total": len(uni),
            "completed": 0,
            "current": [c for c, _ in uni],
            "results": [],
            "failures": [],
            "years": years,
            "demo": demo,
            "error": None,
        })

    t = threading.Thread(target=_run, args=(uni, years, refresh, demo), daemon=True)
    t.start()
    return {"started": True, "total": len(uni)}
