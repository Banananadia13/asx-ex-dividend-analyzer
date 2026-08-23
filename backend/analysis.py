"""
Ex-dividend event analysis engine.

All calculations operate on a split-adjusted (but NOT dividend-adjusted) daily
price series, so that the actual cash-price behaviour around ex-dividend dates
is preserved while stock splits/consolidations don't distort comparisons.

Definitions (documented in the UI under "Methodology"):

- Pre-dividend price (P0): the closing price on the last trading day before
  the ex-dividend date.
- Recovery: the first trading day on or after the ex-dividend date whose CLOSE
  is >= P0. The ex-dividend date itself counts as day 0; recovery_days is the
  number of trading days elapsed from the ex-date to the recovery date.
- Pre-dividend high: highest close in the window between the previous
  ex-dividend date (exclusive) and this ex-dividend date (exclusive), capped
  at LOOKBACK_CAP trading days.
- Post-dividend low: lowest close from the ex-dividend date (inclusive) up to
  the recovery date (inclusive), or, if the price never recovered, up to
  HORIZON trading days / the end of available data.
- Events with fewer than MIN_POST_DAYS trading days of data after the ex-date
  and no recovery are marked "insufficient data" and excluded from recovery
  averages (but still shown).
- Low-confidence flags are attached where the numbers look unreliable
  (e.g. price move wildly inconsistent with the dividend, likely special
  dividend/demerger, gaps in price data).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

HORIZON = 250          # trading days we search for a recovery (~1 calendar year)
LOOKBACK_CAP = 130     # max trading days for the pre-dividend-high window (~6 months)
MIN_POST_DAYS = 40     # events with < this many post days and no recovery => insufficient

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _week_of_month(d: date) -> int:
    """1-based week-of-month (days 1-7 => week 1, 8-14 => week 2, ...)."""
    return (d.day - 1) // 7 + 1


def low_delay_bucket(days: int) -> str:
    if days <= 5:
        return "within 1 week"
    if days <= 10:
        return "within 2 weeks"
    if days <= 22:
        return "within 1 month"
    if days <= 44:
        return "within 2 months"
    return "3+ months"


@dataclass
class ExDivEvent:
    ex_date: str
    dividend: float
    # prices
    pre_div_close: Optional[float] = None
    pre_div_date: Optional[str] = None
    ex_open: Optional[float] = None
    ex_close: Optional[float] = None
    drop_abs: Optional[float] = None            # P0 - ex_close
    drop_pct: Optional[float] = None            # (P0 - ex_close)/P0 * 100
    drop_vs_dividend: Optional[float] = None    # drop_abs / dividend
    # recovery
    recovered: Optional[bool] = None            # None => insufficient data
    recovery_date: Optional[str] = None
    recovery_days: Optional[int] = None
    post_days_available: int = 0
    # pre-dividend high
    high_date: Optional[str] = None
    high_price: Optional[float] = None
    high_month: Optional[int] = None
    high_iso_week: Optional[int] = None
    high_week_of_month: Optional[int] = None
    # post-dividend low
    low_date: Optional[str] = None
    low_price: Optional[float] = None
    low_days_after_ex: Optional[int] = None
    low_to_recovery_days: Optional[int] = None
    low_month: Optional[int] = None
    low_iso_week: Optional[int] = None
    low_week_of_month: Optional[int] = None
    low_bucket: Optional[str] = None
    # recovery calendar info
    recovery_month: Optional[int] = None
    recovery_iso_week: Optional[int] = None
    # meta
    ex_year: int = 0
    ex_month: int = 0
    flags: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def split_adjust(px: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of the OHLC frame with prices adjusted for splits only.

    Yahoo raw (auto_adjust=False) prices are as-traded; a 1:N split makes the
    series discontinuous. We divide every price BEFORE a split date by the
    cumulative split factor of all splits on/after that date. Dividends from
    Yahoo are already split-adjusted, so they are left untouched.
    """
    px = px.copy()
    if "Stock Splits" not in px.columns:
        return px
    splits = px["Stock Splits"].replace(0, np.nan).dropna()
    if splits.empty:
        return px
    # cumulative factor applying to each row: product of splits strictly after that row
    factor = pd.Series(1.0, index=px.index)
    cum = 1.0
    # iterate in reverse chronological order
    ratios = splits.sort_index(ascending=False)
    for dt, ratio in ratios.items():
        if ratio and ratio > 0:
            cum *= ratio
            factor.loc[:dt] = cum  # rows up to and including split date? split applies FROM split date
    # Yahoo reports the split ON its effective date, and prices from that date
    # are post-split; so rows strictly BEFORE the split date must be divided.
    factor = pd.Series(1.0, index=px.index)
    for dt, ratio in ratios.items():
        if ratio and ratio > 0:
            factor.loc[factor.index < dt] *= ratio
    for col in ("Open", "High", "Low", "Close"):
        if col in px.columns:
            px[col] = px[col] / factor
    return px


def analyze_events(px: pd.DataFrame, dividends: pd.Series,
                   years: int = 20) -> list[ExDivEvent]:
    """px: DataFrame indexed by date with Open/High/Low/Close (split-adjusted).
       dividends: Series indexed by ex-dividend date, value = dividend/share."""
    px = px.sort_index()
    closes = px["Close"]
    opens = px["Open"] if "Open" in px.columns else closes
    idx = px.index

    cutoff = pd.Timestamp(date.today()) - pd.DateOffset(years=years)
    if idx.tz is not None:
        cutoff = cutoff.tz_localize(idx.tz)
    divs = dividends[dividends.index >= cutoff].sort_index()

    events: list[ExDivEvent] = []
    div_dates = list(divs.index)

    for i, (ex_ts, amount) in enumerate(divs.items()):
        d = ExDivEvent(ex_date=str(ex_ts.date()), dividend=round(float(amount), 6),
                       ex_year=ex_ts.year, ex_month=ex_ts.month)
        # locate ex-date row (or next trading day if the recorded ex-date
        # falls on a non-trading day)
        pos = int(idx.searchsorted(ex_ts))
        if pos >= len(idx):
            d.flags.append("ex-date beyond available price data")
            events.append(d)
            continue
        if idx[pos] != ex_ts:
            d.flags.append("ex-date not a trading day in price data; used next trading day")
        ex_pos = pos
        if ex_pos == 0:
            d.flags.append("no price data before ex-date")
            events.append(d)
            continue

        pre_pos = ex_pos - 1
        p0 = float(closes.iloc[pre_pos])
        d.pre_div_close = round(p0, 4)
        d.pre_div_date = str(idx[pre_pos].date())
        d.ex_open = round(float(opens.iloc[ex_pos]), 4) if not math.isnan(float(opens.iloc[ex_pos])) else None
        exc = float(closes.iloc[ex_pos])
        d.ex_close = round(exc, 4)
        d.drop_abs = round(p0 - exc, 4)
        d.drop_pct = round((p0 - exc) / p0 * 100, 3) if p0 else None
        if amount and amount > 0:
            d.drop_vs_dividend = round((p0 - exc) / amount, 3)

        # sanity flags
        if amount and p0 and amount / p0 > 0.20:
            d.flags.append("dividend > 20% of price — likely special dividend/demerger; treat with caution")
        if d.drop_vs_dividend is not None and abs(d.drop_vs_dividend) > 4:
            d.flags.append("price move far larger than dividend — other news likely dominated this event")

        # pre-dividend high window: since previous ex-date (exclusive), capped
        win_start = max(0, ex_pos - LOOKBACK_CAP)
        if i > 0:
            prev_pos = int(idx.searchsorted(div_dates[i - 1]))
            win_start = max(win_start, prev_pos + 1)
        if win_start < ex_pos:
            window = closes.iloc[win_start:ex_pos]
            hi_pos_local = int(np.argmax(window.values))
            hi_ts = window.index[hi_pos_local]
            d.high_price = round(float(window.iloc[hi_pos_local]), 4)
            d.high_date = str(hi_ts.date())
            d.high_month = hi_ts.month
            d.high_iso_week = int(hi_ts.isocalendar().week)
            d.high_week_of_month = _week_of_month(hi_ts.date())

        # recovery search
        post = closes.iloc[ex_pos: ex_pos + HORIZON + 1]  # includes ex-date (day 0)
        d.post_days_available = max(0, len(closes) - 1 - ex_pos)
        rec_local = None
        for j in range(len(post)):
            if float(post.iloc[j]) >= p0:
                rec_local = j
                break

        if rec_local is not None:
            d.recovered = True
            rec_ts = post.index[rec_local]
            d.recovery_date = str(rec_ts.date())
            d.recovery_days = rec_local
            d.recovery_month = rec_ts.month
            d.recovery_iso_week = int(rec_ts.isocalendar().week)
            low_span = post.iloc[: rec_local + 1]
        else:
            if d.post_days_available < MIN_POST_DAYS:
                d.recovered = None
                d.flags.append(f"only {d.post_days_available} trading days of data after ex-date — too early to judge recovery")
            else:
                d.recovered = False
                if d.post_days_available < HORIZON:
                    d.flags.append(f"no recovery within available {d.post_days_available} trading days")
            low_span = post

        if len(low_span) > 0:
            lo_local = int(np.argmin(low_span.values))
            lo_ts = low_span.index[lo_local]
            d.low_price = round(float(low_span.iloc[lo_local]), 4)
            d.low_date = str(lo_ts.date())
            d.low_days_after_ex = lo_local
            d.low_month = lo_ts.month
            d.low_iso_week = int(lo_ts.isocalendar().week)
            d.low_week_of_month = _week_of_month(lo_ts.date())
            d.low_bucket = low_delay_bucket(lo_local)
            if d.recovery_days is not None:
                d.low_to_recovery_days = d.recovery_days - lo_local

        # data-gap check around the event
        span = px.iloc[max(0, ex_pos - 5): ex_pos + 5]
        if len(span) >= 2:
            gaps = span.index.to_series().diff().dt.days.dropna()
            if (gaps > 7).any():
                d.flags.append("gap of >7 calendar days in price data near this event")

        events.append(d)

    return events


def _safe_mean(vals):
    vals = [v for v in vals if v is not None]
    return round(float(np.mean(vals)), 2) if vals else None


def _safe_median(vals):
    vals = [v for v in vals if v is not None]
    return round(float(np.median(vals)), 2) if vals else None


def _mode_with_share(counter: dict):
    """Return (key, count, share%) of most common value, or None."""
    if not counter:
        return None
    total = sum(counter.values())
    k = max(counter, key=counter.get)
    return {"value": k, "count": counter[k], "share_pct": round(counter[k] / total * 100, 1)}


def aggregate(events: list[ExDivEvent]) -> dict:
    """Aggregate statistics across all events."""
    usable = [e for e in events if e.pre_div_close is not None]
    decided = [e for e in usable if e.recovered is not None]
    recovered = [e for e in decided if e.recovered]
    insufficient = [e for e in usable if e.recovered is None]

    rec_days = [e.recovery_days for e in recovered]
    agg = {
        "n_events": len(events),
        "n_usable": len(usable),
        "n_decided": len(decided),
        "n_recovered": len(recovered),
        "n_not_recovered": len(decided) - len(recovered),
        "n_insufficient": len(insufficient),
        "success_rate_pct": round(len(recovered) / len(decided) * 100, 1) if decided else None,
        "avg_recovery_days": _safe_mean(rec_days),
        "median_recovery_days": _safe_median(rec_days),
        "fastest_recovery": None,
        "slowest_recovery": None,
        "avg_dividend": _safe_mean([e.dividend for e in usable]),
        "avg_drop_pct": _safe_mean([e.drop_pct for e in usable]),
        "median_drop_pct": _safe_median([e.drop_pct for e in usable]),
        "avg_drop_vs_dividend": _safe_mean([e.drop_vs_dividend for e in usable if e.drop_vs_dividend is not None and abs(e.drop_vs_dividend) <= 4]),
        "avg_low_days_after_ex": _safe_mean([e.low_days_after_ex for e in recovered]),
        "median_low_days_after_ex": _safe_median([e.low_days_after_ex for e in recovered]),
        "avg_low_to_recovery_days": _safe_mean([e.low_to_recovery_days for e in recovered]),
        "avg_low_drawdown_pct": _safe_mean([
            round((e.pre_div_close - e.low_price) / e.pre_div_close * 100, 3)
            for e in decided if e.low_price is not None and e.pre_div_close
        ]),
    }
    if rec_days:
        fastest = min(recovered, key=lambda e: e.recovery_days)
        slowest = max(recovered, key=lambda e: e.recovery_days)
        agg["fastest_recovery"] = {"days": fastest.recovery_days, "ex_date": fastest.ex_date}
        agg["slowest_recovery"] = {"days": slowest.recovery_days, "ex_date": slowest.ex_date}

    # distributions
    def count_by(items):
        c = {}
        for k in items:
            if k is not None:
                c[k] = c.get(k, 0) + 1
        return c

    low_bucket_counts = count_by([e.low_bucket for e in decided])
    low_month_counts = count_by([e.low_month for e in decided])
    high_month_counts = count_by([e.high_month for e in usable])
    recovery_month_counts = count_by([e.recovery_month for e in recovered])
    ex_month_counts = count_by([e.ex_month for e in usable])
    low_iso_week_counts = count_by([e.low_iso_week for e in decided])
    high_iso_week_counts = count_by([e.high_iso_week for e in usable])
    recovery_iso_week_counts = count_by([e.recovery_iso_week for e in recovered])
    # "week N after ex-date" distribution (1-based calendar-ish weeks of 5 trading days)
    low_week_after_ex_counts = count_by([
        (min(e.low_days_after_ex // 5 + 1, 13) if e.low_days_after_ex is not None else None)
        for e in decided
    ])

    agg["distributions"] = {
        "low_bucket": low_bucket_counts,
        "low_month": low_month_counts,
        "high_month": high_month_counts,
        "recovery_month": recovery_month_counts,
        "ex_month": ex_month_counts,
        "low_iso_week": low_iso_week_counts,
        "high_iso_week": high_iso_week_counts,
        "recovery_iso_week": recovery_iso_week_counts,
        "low_week_after_ex": low_week_after_ex_counts,
    }
    agg["modes"] = {
        "low_bucket": _mode_with_share(low_bucket_counts),
        "low_month": _mode_with_share(low_month_counts),
        "high_month": _mode_with_share(high_month_counts),
        "recovery_month": _mode_with_share(recovery_month_counts),
        "low_week_after_ex": _mode_with_share(low_week_after_ex_counts),
    }

    # year-by-year
    years = {}
    for e in usable:
        y = years.setdefault(e.ex_year, {"events": 0, "recovered": 0, "not_recovered": 0,
                                         "insufficient": 0, "rec_days": [], "drops": [], "dividends": []})
        y["events"] += 1
        y["dividends"].append(e.dividend)
        if e.drop_pct is not None:
            y["drops"].append(e.drop_pct)
        if e.recovered is True:
            y["recovered"] += 1
            y["rec_days"].append(e.recovery_days)
        elif e.recovered is False:
            y["not_recovered"] += 1
        else:
            y["insufficient"] += 1
    agg["by_year"] = [
        {"year": y,
         "events": v["events"], "recovered": v["recovered"],
         "not_recovered": v["not_recovered"], "insufficient": v["insufficient"],
         "avg_recovery_days": _safe_mean(v["rec_days"]),
         "avg_drop_pct": _safe_mean(v["drops"]),
         "total_dividends": round(sum(v["dividends"]), 4)}
        for y, v in sorted(years.items())
    ]

    # seasonal: stats grouped by ex-dividend month
    months = {}
    for e in decided:
        m = months.setdefault(e.ex_month, {"events": 0, "recovered": 0, "rec_days": [], "drops": [], "low_days": []})
        m["events"] += 1
        if e.recovered:
            m["recovered"] += 1
            m["rec_days"].append(e.recovery_days)
        if e.drop_pct is not None:
            m["drops"].append(e.drop_pct)
        if e.low_days_after_ex is not None:
            m["low_days"].append(e.low_days_after_ex)
    agg["by_ex_month"] = [
        {"month": m, "month_name": MONTH_NAMES[m - 1],
         "events": v["events"], "recovered": v["recovered"],
         "success_rate_pct": round(v["recovered"] / v["events"] * 100, 1) if v["events"] else None,
         "avg_recovery_days": _safe_mean(v["rec_days"]),
         "avg_drop_pct": _safe_mean(v["drops"]),
         "avg_low_days": _safe_mean(v["low_days"])}
        for m, v in sorted(months.items())
    ]

    # average calendar timing: typical low/recovery date offsets
    lows = [e.low_days_after_ex for e in recovered if e.low_days_after_ex is not None]
    agg["typical"] = {
        "low_after_ex_trading_days": _safe_median(lows),
        "recovery_after_ex_trading_days": _safe_median(rec_days),
        "low_after_ex_calendar_days": round(_safe_median(lows) * 7 / 5) if lows else None,
        "recovery_after_ex_calendar_days": round(_safe_median(rec_days) * 7 / 5) if rec_days else None,
    }

    # confidence
    flagged = sum(1 for e in usable if e.flags)
    agg["quality"] = {
        "events_with_flags": flagged,
        "low_confidence": len(decided) < 10,
        "note": ("Fewer than 10 decided events — averages are statistically weak."
                 if len(decided) < 10 else None),
    }
    return agg
