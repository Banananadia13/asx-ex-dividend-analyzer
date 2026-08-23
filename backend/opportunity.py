"""
ASX Top 20 Historical Opportunity Score.

Turns the existing ex-dividend recovery analysis into a single transparent
0–100 score per company, so the user can rank the ASX 20 by how attractive an
entry point each one looks *relative to its own historical pattern* right now.

DESIGN PRINCIPLES
-----------------
1. Every component is computed from measured history or current published data.
   Nothing is predicted, and nothing is invented.
2. Weights live in WEIGHTS below — a single, readable dict. Change a number and
   the whole app re-ranks. The UI shows the weights and every sub-score, so a
   user can always see exactly why a company sits where it does.
3. Missing data is never guessed. A component that cannot be computed is
   dropped and the remaining weights are re-normalised, and the company's
   `confidence` falls accordingly. A company with too little data is flagged,
   not silently ranked.
4. The score is a *historical-pattern* score, not a valuation or a forecast.
   A high score means "this company's own history says the current moment
   resembles its historically better entry points" — nothing more.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from analysis import MONTH_NAMES

# --------------------------------------------------------------------------
# The S&P/ASX 20 index constituents (editable — see /api/top20/universe).
# Sources cross-checked Aug 2026: kalkine.com.au ASX 20 listing and
# companiesmarketcap.com Australian market-cap ranking.
# Index membership is reviewed quarterly by S&P, so this list can drift;
# the UI states the as-at date and lets the user edit the universe.
# --------------------------------------------------------------------------
ASX20 = [
    ("BHP", "BHP Group"),
    ("CBA", "Commonwealth Bank of Australia"),
    ("NAB", "National Australia Bank"),
    ("WBC", "Westpac Banking Corporation"),
    ("ANZ", "ANZ Group Holdings"),
    ("MQG", "Macquarie Group"),
    ("WES", "Wesfarmers"),
    ("CSL", "CSL Limited"),
    ("WDS", "Woodside Energy Group"),
    ("GMG", "Goodman Group"),
    ("FMG", "Fortescue"),
    ("RIO", "Rio Tinto"),
    ("WOW", "Woolworths Group"),
    ("TCL", "Transurban Group"),
    ("ALL", "Aristocrat Leisure"),
    ("QBE", "QBE Insurance Group"),
    ("COL", "Coles Group"),
    ("BXB", "Brambles"),
    ("REA", "REA Group"),
    ("WTC", "WiseTech Global"),
]
UNIVERSE_AS_AT = "2026-08-23"

# --------------------------------------------------------------------------
# Scoring weights — must sum to 100. Transparent and adjustable.
# --------------------------------------------------------------------------
WEIGHTS = {
    "recovery_probability": 17,   # how often the price got back to pre-dividend
    "cycle_position": 16,         # where the stock sits in its ex-div cycle NOW
    "recovery_speed": 14,         # how quickly it historically recovered
    "dividend_materiality": 14,   # is the dividend big enough for this to matter
    "consistency": 10,            # how repeatable the recovery time has been
    "seasonal_timing": 9,         # is this month historically a low-price month
    "broker_sentiment": 9,        # current published analyst view
    "drop_magnitude": 7,          # size of the typical post-ex discount
    "dividend_timing": 4,         # how soon the next entry window arrives
}

FACTOR_LABELS = {
    "recovery_probability": "Recovery probability",
    "cycle_position": "Current cycle position",
    "recovery_speed": "Recovery speed",
    "dividend_materiality": "Dividend materiality",
    "consistency": "Pattern consistency",
    "seasonal_timing": "Seasonal timing",
    "broker_sentiment": "Broker sentiment",
    "drop_magnitude": "Typical discount size",
    "dividend_timing": "Next window timing",
}

# A dividend yield at or above this is treated as fully "material" for
# ex-dividend timing purposes. Below it, the strategy has progressively less
# to work with, because there is barely a dip to buy.
MATERIAL_YIELD_PCT = 4.0

BANDS = [
    (80, "Excellent Historical Opportunity", "excellent"),
    (68, "Strong Historical Opportunity", "strong"),
    (55, "Neutral", "neutral"),
    (42, "Weak Historical Opportunity", "weak"),
    (0,  "Poor Historical Opportunity", "poor"),
]

DISCLAIMER = (
    "These rankings are derived from approximately 20 years of historical market "
    "behaviour and current publicly available broker sentiment. They are intended "
    "as a decision-support tool only and do not constitute financial advice or "
    "guarantee future performance."
)


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def band_for(score: float):
    for threshold, label, key in BANDS:
        if score >= threshold:
            return label, key
    return BANDS[-1][1], BANDS[-1][2]


# --------------------------------------------------------------------------
# Individual factor scores. Each returns (score 0-100, explanation) or
# (None, reason) when the underlying data is unavailable.
# --------------------------------------------------------------------------

def f_recovery_probability(agg):
    sr = agg.get("success_rate_pct")
    n = agg.get("n_decided", 0)
    if sr is None or n < 3:
        return None, "not enough completed ex-dividend cycles to measure"
    return clamp(sr), (f"recovered to the pre-dividend price in {sr:.0f}% of "
                       f"{n} completed cycles")


def f_recovery_speed(agg):
    med = agg.get("median_recovery_days")
    if med is None:
        return None, "no completed recoveries to time"
    # 5 trading days or fewer -> 100; 120 or more -> 0; linear between.
    score = clamp(100 * (1 - (med - 5) / 115))
    return score, f"typically recovered in {med:.0f} trading days (median)"


def f_consistency(agg, events):
    days = [e["recovery_days"] for e in events
            if e.get("recovered") is True and e.get("recovery_days") is not None]
    if len(days) < 4:
        return None, "too few recoveries to judge consistency"
    med = float(np.median(days))
    # Robust spread: median absolute deviation from the median.
    mad = float(np.median([abs(d - med) for d in days]))
    # Measure the spread against a floor of 10 trading days (about two weeks)
    # rather than against the median itself. Dividing by the median alone
    # punishes fast recoveries absurdly — a stock that recovers in 4 days give
    # or take 3 is highly predictable, but a raw ratio would score it 0.25.
    rel = mad / max(med, 10.0)
    score = clamp(100 * (1 - rel))
    if med <= 0 and mad <= 0:
        return 100.0, "recoveries were essentially immediate every time"
    return score, (f"recovery time varied by about ±{mad:.0f} trading days "
                   f"around the {med:.0f}-day median")


def f_drop_magnitude(agg):
    drop = agg.get("avg_drop_pct")
    sr = agg.get("success_rate_pct")
    if drop is None:
        return None, "no measurable ex-dividend price move"
    # A bigger habitual discount is only useful if the price actually comes back,
    # so the raw discount score is scaled by the recovery success rate.
    raw = clamp(drop / 6.0 * 100)           # a 6% average fall scores 100
    if sr is not None:
        raw *= sr / 100.0
    return clamp(raw), (f"average fall of {drop:.1f}% on the ex-dividend date"
                        + (f", recovered {sr:.0f}% of the time" if sr is not None else ""))


def f_dividend_materiality(ctx):
    """Is the dividend actually big enough for ex-dividend timing to matter?

    Without this, the score is dominated by low-yield growth stocks: a company
    paying 0.3% barely moves on its ex-date, so it "recovers" within a day,
    nearly always — scoring near-perfect on recovery speed and probability
    while offering no dip worth buying. That inverts the whole purpose of the
    ranking, so materiality is scored explicitly and weighted.
    """
    y = ctx.get("dividend_yield_pct")
    if y is None:
        return None, "no dividend paid in the last 12 months"
    score = clamp(y / MATERIAL_YIELD_PCT * 100)
    if y < 1.0:
        note = (f"trailing yield of just {y:.1f}% — the ex-dividend dip is too small "
                f"to time around")
    elif y < 2.5:
        note = f"modest trailing yield of {y:.1f}% — a small dip to work with"
    else:
        note = f"trailing yield of {y:.1f}% — a meaningful ex-dividend dip"
    return score, note


def f_seasonal_timing(agg, today: date):
    dist = agg.get("distributions", {})
    lows = {int(k): v for k, v in (dist.get("low_month") or {}).items()}
    highs = {int(k): v for k, v in (dist.get("high_month") or {}).items()}
    if not lows:
        return None, "no monthly low pattern available"
    m = today.month
    low_share = lows.get(m, 0) / max(lows.values())
    high_share = (highs.get(m, 0) / max(highs.values())) if highs else 0.0
    score = clamp(50 + 50 * low_share - 35 * high_share)
    month = MONTH_NAMES[m - 1]
    if low_share >= 0.99:
        note = f"{month} is historically this stock's most common low-price month"
    elif low_share > 0.4:
        note = f"{month} has often contained post-dividend lows"
    elif high_share > 0.6:
        note = f"{month} has more often contained highs than lows"
    else:
        note = f"{month} shows no strong historical low or high bias"
    return score, note


def f_cycle_position(agg, ctx):
    """Where the price sits in its ex-dividend cycle right now.

    Two halves, blended:
      timing    — how close today is to the point in the cycle where the low
                  has historically occurred;
      discount  — how far today's price sits below the last pre-dividend close.
    """
    td = ctx.get("td_since_last_ex")
    typical_low = agg.get("median_low_days_after_ex")
    typical_rec = agg.get("median_recovery_days")
    if td is None or typical_low is None or typical_rec is None:
        return None, "no recent ex-dividend event to locate the cycle"

    low = max(float(typical_low), 1.0)
    rec = max(float(typical_rec), low + 1.0)

    if td <= low:
        # Running into the historical low — improving as we approach it.
        timing = 70 + 30 * (td / low)
        tnote = (f"{td} trading days past the last ex-dividend date, approaching the "
                 f"~{low:.0f}-day mark where the low has typically formed")
    elif td <= rec:
        # Past the low, price historically climbing back — opportunity fading.
        timing = 100 - 60 * ((td - low) / (rec - low))
        tnote = (f"{td} trading days past the ex-dividend date — beyond the typical "
                 f"~{low:.0f}-day low, inside the usual {rec:.0f}-day recovery window")
    else:
        timing = 25
        tnote = (f"{td} trading days past the last ex-dividend date — beyond the "
                 f"typical {rec:.0f}-day recovery window")

    cur = ctx.get("current_price")
    ref = ctx.get("last_pre_div_close")
    if cur and ref:
        below = (ref - cur) / ref * 100.0
        discount = clamp(50 + below * 5)     # 10% below the reference -> 100
        dnote = (f"trading {abs(below):.1f}% {'below' if below >= 0 else 'above'} the "
                 f"last pre-dividend close of ${ref:,.2f}")
        score = 0.6 * timing + 0.4 * discount
        return clamp(score), f"{tnote}; {dnote}"

    return clamp(timing), tnote


def f_broker_sentiment(consensus, ctx):
    if not consensus or not consensus.get("available"):
        return None, "no published analyst data available"
    parts, notes = [], []

    counts = consensus.get("counts")
    if counts:
        buy = (counts.get("strongBuy", 0) or 0) + (counts.get("buy", 0) or 0)
        hold = counts.get("hold", 0) or 0
        sell = (counts.get("sell", 0) or 0) + (counts.get("strongSell", 0) or 0)
        total = buy + hold + sell
        if total:
            rating = clamp(50 + 50 * ((buy - sell) / total))
            parts.append((rating, 0.6))
            notes.append(f"{buy} buy / {hold} hold / {sell} sell among {total} analysts")

    pt = consensus.get("price_targets") or {}
    mean_t = pt.get("mean")
    cur = ctx.get("current_price") or pt.get("current")
    if mean_t and cur:
        upside = (float(mean_t) - float(cur)) / float(cur) * 100.0
        tgt = clamp(50 + upside * 2.5)       # +20% upside -> 100
        parts.append((tgt, 0.4))
        notes.append(f"average target ${float(mean_t):,.2f} is {upside:+.1f}% vs the last price")

    if not parts:
        return None, "analyst data present but unusable"
    total_w = sum(w for _, w in parts)
    score = sum(s * w for s, w in parts) / total_w
    return clamp(score), "; ".join(notes)


def f_dividend_timing(agg, ctx):
    """How soon the historically favourable entry window arrives."""
    td = ctx.get("td_since_last_ex")
    typical_low = agg.get("median_low_days_after_ex")
    typical_rec = agg.get("median_recovery_days")
    next_ex = ctx.get("next_ex_date")

    if td is not None and typical_low is not None and typical_rec is not None:
        if td <= max(typical_rec, typical_low):
            return 90.0, "currently inside the historical post-dividend window"

    if next_ex:
        try:
            d = datetime.strptime(str(next_ex)[:10], "%Y-%m-%d").date()
            days = (d - ctx.get("today", date.today())).days
            if days < 0:
                return None, "the published next ex-dividend date has already passed"
            if days <= 30:
                return clamp(85 - days), f"next ex-dividend date in about {days} days"
            return clamp(60 - (days - 30) * 0.4), f"next ex-dividend date in about {days} days"
        except Exception:
            pass

    return None, "no upcoming ex-dividend date published"


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def score_company(agg: dict, events: list, consensus: dict, ctx: dict) -> dict:
    today = ctx.get("today") or date.today()
    ctx = dict(ctx)
    ctx["today"] = today

    raw = {
        "recovery_probability": f_recovery_probability(agg),
        "cycle_position": f_cycle_position(agg, ctx),
        "recovery_speed": f_recovery_speed(agg),
        "dividend_materiality": f_dividend_materiality(ctx),
        "consistency": f_consistency(agg, events),
        "seasonal_timing": f_seasonal_timing(agg, today),
        "broker_sentiment": f_broker_sentiment(consensus, ctx),
        "drop_magnitude": f_drop_magnitude(agg),
        "dividend_timing": f_dividend_timing(agg, ctx),
    }

    factors, available_weight = [], 0.0
    weighted_sum = 0.0
    for key, weight in WEIGHTS.items():
        score, note = raw[key]
        entry = {
            "key": key, "label": FACTOR_LABELS[key], "weight": weight,
            "score": None if score is None else round(float(score), 1),
            "note": note, "available": score is not None,
        }
        if score is not None:
            weighted_sum += float(score) * weight
            available_weight += weight
            entry["contribution"] = round(float(score) * weight / 100.0, 2)
        factors.append(entry)

    if available_weight == 0:
        total = None
    else:
        # Re-normalise across whatever could actually be computed.
        total = round(weighted_sum / available_weight, 1)

    coverage = round(available_weight / sum(WEIGHTS.values()) * 100, 0)
    n_decided = agg.get("n_decided", 0)
    if total is None:
        confidence = "none"
    elif n_decided >= 15 and coverage >= 85:
        confidence = "high"
    elif n_decided >= 8 and coverage >= 60:
        confidence = "medium"
    else:
        confidence = "low"

    label, key = band_for(total) if total is not None else ("Insufficient data", "none")
    return {
        "score": total, "band": label, "band_key": key,
        "factors": factors, "coverage_pct": coverage, "confidence": confidence,
        "weights": dict(WEIGHTS),
    }


def strengths_and_risks(agg: dict, scored: dict, ctx: dict):
    """Plain-English historical strengths and risks, derived from the factors."""
    strengths, risks = [], []
    for f in scored["factors"]:
        if not f["available"]:
            continue
        if f["score"] >= 70:
            strengths.append(f"{f['label']}: {f['note']}.")
        elif f["score"] <= 40:
            risks.append(f"{f['label']}: {f['note']}.")

    mat = next((f for f in scored["factors"] if f["key"] == "dividend_materiality"), None)
    if mat and mat["available"] and mat["score"] < 30:
        risks.append("This company's dividend is small relative to its share price, so the "
                     "ex-dividend dip is minor — there is little for a dividend-timing "
                     "approach to work with, however cleanly the price recovers.")

    n_dec = agg.get("n_decided", 0)
    if n_dec and n_dec < 10:
        risks.append(f"Only {n_dec} completed ex-dividend cycles in the window — "
                     "the averages are statistically thin.")
    if agg.get("n_not_recovered"):
        risks.append(f"{agg['n_not_recovered']} past event(s) never recovered to the "
                     "pre-dividend price within 250 trading days.")
    if scored["coverage_pct"] < 100:
        missing = [f["label"] for f in scored["factors"] if not f["available"]]
        risks.append("Score computed without: " + ", ".join(missing) + ".")
    if not strengths:
        strengths.append("No factor currently scores in the strong range for this company.")
    return strengths, risks


def summarise(name: str, ticker: str, rank: int, agg: dict, scored: dict, ctx: dict) -> str:
    """One-paragraph explanation of the ranking, in historical language."""
    if scored["score"] is None:
        return (f"{name} ({ticker}) could not be scored — not enough historical "
                f"ex-dividend data was available.")

    bits = []
    med = agg.get("median_recovery_days")
    sr = agg.get("success_rate_pct")
    if med is not None and sr is not None:
        bits.append(f"it has historically recovered within about {med:.0f} trading days "
                    f"in {sr:.0f}% of completed cycles")
    elif med is not None:
        bits.append(f"it has historically recovered within about {med:.0f} trading days")

    cyc = next((f for f in scored["factors"] if f["key"] == "cycle_position"), None)
    if cyc and cyc["available"]:
        if cyc["score"] >= 70:
            bits.append("it is currently sitting in a historically favourable part of its "
                        "ex-dividend cycle")
        elif cyc["score"] <= 40:
            bits.append("it is currently past the part of its cycle that has historically "
                        "offered the better entry prices")

    season = next((f for f in scored["factors"] if f["key"] == "seasonal_timing"), None)
    if season and season["available"] and season["score"] >= 70:
        bits.append(season["note"])

    brok = next((f for f in scored["factors"] if f["key"] == "broker_sentiment"), None)
    if brok and brok["available"]:
        if brok["score"] >= 65:
            bits.append("and the majority of covering brokers currently rate it positively")
        elif brok["score"] <= 40:
            bits.append("though current broker sentiment is comparatively cautious")

    body = "; ".join(bits) if bits else "its historical pattern is mixed"
    conf = "" if scored["confidence"] == "high" else (
        f" This is a {scored['confidence']}-confidence score "
        f"({scored['coverage_pct']:.0f}% of factors available).")
    return (f"Historically, {ticker} ranks #{rank} with a score of {scored['score']:.0f} "
            f"because {body}.{conf} This describes past behaviour only.")


def build_context(px: pd.DataFrame, events: list, future: dict, today: Optional[date] = None) -> dict:
    """Current-state inputs the score needs (price now, position in the cycle)."""
    today = today or date.today()
    ctx = {"today": today}
    if px is not None and len(px):
        ctx["current_price"] = round(float(px["Close"].iloc[-1]), 4)
        ctx["price_as_at"] = str(px.index[-1].date())

    decided = [e for e in events if e.get("pre_div_close") is not None]
    if decided:
        last = decided[-1]
        ctx["last_ex_date"] = last["ex_date"]
        ctx["last_pre_div_close"] = last["pre_div_close"]
        if px is not None and len(px):
            ex_ts = pd.Timestamp(last["ex_date"])
            pos = int(px.index.searchsorted(ex_ts))
            ctx["td_since_last_ex"] = max(0, len(px) - 1 - pos)

    if future and future.get("ex_dividend_date"):
        ctx["next_ex_date"] = str(future["ex_dividend_date"])[:10]

    # trailing-12-month dividend yield, for filtering
    if decided and ctx.get("current_price"):
        cutoff = pd.Timestamp(today) - pd.DateOffset(years=1)
        ttm = sum(e["dividend"] for e in decided
                  if pd.Timestamp(e["ex_date"]) >= cutoff)
        if ttm > 0:
            ctx["dividend_yield_pct"] = round(ttm / ctx["current_price"] * 100, 2)
            ctx["ttm_dividends"] = round(ttm, 4)
    return ctx
