"""
Data retrieval layer.

Primary source: Yahoo Finance via the `yfinance` library (ASX tickers use the
`.AX` suffix, added automatically). All downloaded data is cached on disk so
repeat lookups are fast and the app keeps working offline with stale data
(staleness is reported to the UI, never hidden).

A clearly-labelled DEMO mode generates a synthetic dataset so the interface
can be explored without a network connection. Demo data is never presented as
real market data.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_TTL_HOURS = 12


class DataError(Exception):
    """Raised when data cannot be retrieved or fails validation."""


def normalize_ticker(raw: str) -> str:
    t = raw.strip().upper()
    if not t:
        raise DataError("Empty ticker.")
    if t == "DEMO":
        return "DEMO"
    if not t.endswith(".AX"):
        t += ".AX"
    return t


def _cache_path(ticker: str, kind: str) -> str:
    safe = ticker.replace(".", "_")
    return os.path.join(CACHE_DIR, f"{safe}_{kind}")


def _cache_age_hours(path: str):
    if not os.path.exists(path):
        return None
    return (time.time() - os.path.getmtime(path)) / 3600


def validate_prices(px: pd.DataFrame, ticker: str) -> list[str]:
    """Return a list of data-quality warnings (empty list = clean)."""
    warns = []
    if px.empty:
        raise DataError(f"No price data returned for {ticker}.")
    if len(px) < 250:
        warns.append(f"Only {len(px)} daily bars available — less than one year of history.")
    if (px["Close"] <= 0).any():
        n = int((px["Close"] <= 0).sum())
        warns.append(f"{n} non-positive close prices removed.")
    nan_close = int(px["Close"].isna().sum())
    if nan_close:
        warns.append(f"{nan_close} missing close prices removed.")
    # large gaps
    gaps = px.index.to_series().diff().dt.days.dropna()
    big = gaps[gaps > 14]
    if len(big) > 0:
        worst = int(big.max())
        warns.append(f"{len(big)} gaps of more than 14 calendar days in the price history (largest {worst} days) — recoveries spanning a gap may be imprecise.")
    span_years = (px.index.max() - px.index.min()).days / 365.25
    if span_years < 19:
        warns.append(f"History covers {span_years:.1f} years — less than the requested 20.")
    return warns


def fetch_history(ticker: str, refresh: bool = False):
    """Return (prices_df, dividends_series, meta_dict). Raises DataError on failure."""
    if ticker == "DEMO":
        return demo_history()
    if ticker.startswith("DEMO") and ticker[4:].isdigit():
        return demo_history(seed=int(ticker[4:]), label=ticker)

    px_path = _cache_path(ticker, "px.pkl")
    div_path = _cache_path(ticker, "div.json")
    meta_path = _cache_path(ticker, "meta.json")
    age = _cache_age_hours(px_path)

    if not refresh and age is not None and age < CACHE_TTL_HOURS and os.path.exists(div_path):
        px = pd.read_pickle(px_path)
        divs = pd.read_json(div_path, typ="series")
        divs.index = pd.to_datetime(divs.index)
        meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
        meta["cache_age_hours"] = round(age, 1)
        meta["from_cache"] = True
        return px, divs, meta

    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        px = t.history(period="max", auto_adjust=False, actions=True)
    except Exception as exc:
        # fall back to stale cache if present, clearly labelled
        if os.path.exists(px_path) and os.path.exists(div_path):
            px = pd.read_pickle(px_path)
            divs = pd.read_json(div_path, typ="series")
            divs.index = pd.to_datetime(divs.index)
            meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
            meta["from_cache"] = True
            meta["stale"] = True
            meta["cache_age_hours"] = round(_cache_age_hours(px_path), 1)
            meta["fetch_error"] = str(exc)
            return px, divs, meta
        raise DataError(
            f"Could not download data for {ticker}: {exc}. "
            "Check the ticker code and your internet connection.") from exc

    if px is None or px.empty:
        raise DataError(
            f"Yahoo Finance returned no data for {ticker}. "
            "Check the ticker code on asx.com.au (some securities use different symbols) "
            "and that you are connected to the internet.")

    # tidy: drop tz for simpler date handling, drop bad rows
    if px.index.tz is not None:
        px.index = px.index.tz_localize(None)
    px = px[px["Close"].notna() & (px["Close"] > 0)].sort_index()

    divs = px["Dividends"][px["Dividends"] > 0].copy() if "Dividends" in px.columns else pd.Series(dtype=float)

    meta = {
        "ticker": ticker,
        "source": "Yahoo Finance (yfinance)",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "first_date": str(px.index.min().date()),
        "last_date": str(px.index.max().date()),
        "n_bars": int(len(px)),
        "n_dividends_all_time": int(len(divs)),
        "from_cache": False,
        "warnings": validate_prices(px, ticker),
    }

    # company name / currency (best-effort, non-fatal)
    try:
        info = t.get_info()
        meta["name"] = info.get("longName") or info.get("shortName")
        meta["currency"] = info.get("currency")
        meta["sector"] = info.get("sector")
    except Exception:
        pass

    px.to_pickle(px_path)
    divs.to_json(div_path)
    json.dump(meta, open(meta_path, "w"), default=str)
    return px, divs, meta


def demo_history(seed: int = 7, label: str = "DEMO"):
    """Synthetic 20-year semiannual-dividend stock. CLEARLY LABELLED DEMO."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=252 * 20)
    n = len(dates)
    # geometric random walk with mild upward drift + seasonal wobble
    rets = rng.normal(0.00025, 0.012, n)
    seasonal = 0.0004 * np.sin(np.arange(n) / 252 * 2 * np.pi * 2)
    closes = 20 * np.exp(np.cumsum(rets + seasonal))
    # dividends every Feb/Aug ~ 2.5% of price; price gaps down on ex-date then mean-reverts
    div_dates, div_amts = [], []
    month = dates.month
    day = dates.day
    for i in range(1, n):
        if month[i] in (2, 8) and month[i - 1] == month[i] and 24 <= day[i] <= 28 and (
                not div_dates or (dates[i] - div_dates[-1]).days > 100):
            amt = round(closes[i - 1] * 0.025, 2)
            div_dates.append(dates[i])
            div_amts.append(amt)
            drop = amt * rng.uniform(0.8, 1.2)
            closes[i:] -= drop
            # mean reversion: claw back the drop over ~40-90 trading days
            rec = min(n - i, int(rng.uniform(40, 90)))
            closes[i:i + rec] += np.linspace(0, drop * rng.uniform(0.9, 1.3), rec)
    closes = np.maximum(closes, 1.0)
    opens = closes * (1 + rng.normal(0, 0.003, n))
    px = pd.DataFrame({"Open": opens, "High": np.maximum(opens, closes) * 1.004,
                       "Low": np.minimum(opens, closes) * 0.996, "Close": closes,
                       "Dividends": 0.0, "Stock Splits": 0.0}, index=dates)
    divs = pd.Series(div_amts, index=pd.DatetimeIndex(div_dates))
    meta = {
        "ticker": label, "name": f"Demo Company {label[4:] or ''} (synthetic data)".replace("  ", " "),
        "source": "SYNTHETIC DEMO DATA — not a real security",
        "demo": True, "currency": "AUD",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "first_date": str(px.index.min().date()), "last_date": str(px.index.max().date()),
        "n_bars": n, "n_dividends_all_time": len(divs), "from_cache": False,
        "warnings": ["This is randomly generated demonstration data, not a real company."],
    }
    return px, divs, meta


def fetch_consensus(ticker: str) -> dict:
    """Best-effort analyst consensus from Yahoo Finance. Every field optional."""
    out = {"available": False, "source": "Yahoo Finance aggregate analyst data",
           "as_of": datetime.now(timezone.utc).isoformat(),
           "note": ("Aggregate of analysts tracked by Yahoo Finance/LSEG. "
                    "Not a complete list of covering brokers; individual current "
                    "recommendations per broker are not freely licensed."),
           }
    if ticker == "DEMO":
        out["note"] = "Demo mode — no analyst data."
        return out
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
    except Exception as exc:
        out["error"] = str(exc)
        return out

    try:
        rec = t.recommendations_summary
        if rec is not None and len(rec):
            row = rec.iloc[0]  # period "0m" = current
            counts = {k: int(row[k]) for k in ("strongBuy", "buy", "hold", "sell", "strongSell") if k in row}
            if sum(counts.values()) > 0:
                out["counts"] = counts
                out["n_analysts"] = sum(counts.values())
                out["available"] = True
    except Exception as exc:
        out.setdefault("errors", []).append(f"recommendations: {exc}")

    try:
        pt = t.analyst_price_targets
        if pt and any(v is not None for v in pt.values()):
            out["price_targets"] = {k: pt.get(k) for k in ("low", "high", "mean", "median", "current")}
            out["available"] = True
    except Exception as exc:
        out.setdefault("errors", []).append(f"price targets: {exc}")

    try:
        ud = t.upgrades_downgrades
        if ud is not None and len(ud):
            ud = ud.reset_index().head(12)
            actions = []
            for _, r in ud.iterrows():
                actions.append({
                    "date": str(pd.Timestamp(r.get("GradeDate")).date()) if pd.notna(r.get("GradeDate")) else None,
                    "firm": r.get("Firm"),
                    "to_grade": r.get("ToGrade"),
                    "from_grade": r.get("FromGrade"),
                    "action": r.get("Action"),
                })
            out["recent_actions"] = actions
            out["actions_note"] = ("Recent rating CHANGES by named firms (from Yahoo Finance). "
                                   "These are historical actions, not each firm's current standing recommendation.")
    except Exception as exc:
        out.setdefault("errors", []).append(f"upgrades/downgrades: {exc}")

    return out


def fetch_future_events(ticker: str) -> dict:
    """Upcoming ex-dividend / payment / earnings dates, best-effort."""
    out = {"available": False, "source": "Yahoo Finance calendar",
           "note": "Informational only. Dates can change; confirm with ASX announcements."}
    if ticker == "DEMO":
        out["note"] = "Demo mode — no upcoming events."
        return out
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cal = t.calendar
        if isinstance(cal, dict):
            def fmt(v):
                if v is None:
                    return None
                if isinstance(v, (list, tuple)):
                    return [str(x) for x in v]
                return str(v)
            mapping = {"Ex-Dividend Date": "ex_dividend_date",
                       "Dividend Date": "dividend_payment_date",
                       "Earnings Date": "earnings_dates"}
            for k, key in mapping.items():
                if k in cal and cal[k]:
                    out[key] = fmt(cal[k])
                    out["available"] = True
    except Exception as exc:
        out["error"] = str(exc)
    return out


def fetch_market_context() -> dict:
    """Optional qualitative context: a few benchmark quotes. Best-effort."""
    out = {"available": False,
           "note": ("Qualitative context only — entirely separate from the historical "
                    "analysis. Levels are recent Yahoo Finance quotes and may be delayed."),
           "as_of": datetime.now(timezone.utc).isoformat(), "items": []}
    series = [("^AXJO", "S&P/ASX 200"), ("AUDUSD=X", "AUD/USD"),
              ("^GSPC", "S&P 500"), ("GC=F", "Gold (USD/oz)"),
              ("CL=F", "WTI Crude (USD/bbl)")]
    try:
        import yfinance as yf
        for sym, label in series:
            try:
                h = yf.Ticker(sym).history(period="1mo", auto_adjust=True)
                if h is None or h.empty:
                    continue
                last = float(h["Close"].iloc[-1])
                first = float(h["Close"].iloc[0])
                out["items"].append({
                    "symbol": sym, "label": label,
                    "last": round(last, 4 if last < 10 else 2),
                    "chg_1m_pct": round((last / first - 1) * 100, 2),
                    "as_of": str(h.index[-1].date()),
                })
            except Exception:
                continue
        out["available"] = len(out["items"]) > 0
    except Exception as exc:
        out["error"] = str(exc)
    return out
