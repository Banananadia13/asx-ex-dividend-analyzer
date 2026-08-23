"""Ground-truth tests for the Historical Opportunity Score."""
import sys, os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

import opportunity as op


def base_agg(**over):
    agg = {
        "n_decided": 20, "n_recovered": 18, "n_not_recovered": 2,
        "success_rate_pct": 90.0,
        "median_recovery_days": 40.0, "avg_recovery_days": 45.0,
        "median_low_days_after_ex": 12.0,
        "avg_drop_pct": 3.0,
        "distributions": {
            "low_month": {"9": 8, "3": 4, "5": 2},
            "high_month": {"8": 7, "2": 5},
        },
    }
    agg.update(over)
    return agg


def events_with(days):
    return [{"recovered": True, "recovery_days": d, "pre_div_close": 10.0,
             "ex_date": "2024-02-20", "dividend": 0.5} for d in days]


def ctx_at(month=9, td=10, cur=9.0, ref=10.0, next_ex=None):
    return {"today": date(2026, month, 15), "td_since_last_ex": td,
            "current_price": cur, "last_pre_div_close": ref, "next_ex_date": next_ex}


# ---------------- individual factors ----------------

def test_recovery_probability_monotonic():
    lo, _ = op.f_recovery_probability(base_agg(success_rate_pct=40.0))
    hi, _ = op.f_recovery_probability(base_agg(success_rate_pct=95.0))
    assert lo < hi
    assert op.f_recovery_probability(base_agg(n_decided=2))[0] is None


def test_recovery_speed_monotonic_and_bounded():
    fast, _ = op.f_recovery_speed(base_agg(median_recovery_days=5))
    mid, _ = op.f_recovery_speed(base_agg(median_recovery_days=60))
    slow, _ = op.f_recovery_speed(base_agg(median_recovery_days=200))
    assert fast == 100.0
    assert slow == 0.0
    assert fast > mid > slow


def test_consistency_prefers_tight_spread():
    tight, _ = op.f_consistency(base_agg(), events_with([40, 41, 39, 40, 41, 40]))
    loose, _ = op.f_consistency(base_agg(), events_with([5, 90, 12, 150, 40, 3]))
    assert tight > loose
    assert op.f_consistency(base_agg(), events_with([40, 41]))[0] is None


def test_consistency_does_not_punish_fast_recoveries():
    """A stock recovering in ~4 days +/- 3 is predictable, not erratic.

    Scoring spread relative to the median alone would score this 25; it must
    be judged against an absolute trading-day yardstick instead.
    """
    fast_tight, _ = op.f_consistency(base_agg(), events_with([4, 7, 2, 4, 5, 3, 4, 6]))
    slow_wide, _ = op.f_consistency(base_agg(), events_with([100, 40, 160, 90, 200, 30]))
    assert fast_tight >= 65, f"fast, tight recoveries scored only {fast_tight}"
    assert fast_tight > slow_wide


def test_drop_magnitude_scaled_by_success():
    reliable, _ = op.f_drop_magnitude(base_agg(avg_drop_pct=5.0, success_rate_pct=95.0))
    unreliable, _ = op.f_drop_magnitude(base_agg(avg_drop_pct=5.0, success_rate_pct=20.0))
    assert reliable > unreliable, "a big drop that rarely recovers must not score well"


def test_seasonal_timing_uses_current_month():
    best, _ = op.f_seasonal_timing(base_agg(), date(2026, 9, 15))   # top low month
    worst, _ = op.f_seasonal_timing(base_agg(), date(2026, 8, 15))  # top high month
    assert best > worst
    assert best == 100.0


def test_cycle_position_peaks_near_typical_low():
    agg = base_agg()
    early, _ = op.f_cycle_position(agg, ctx_at(td=1, cur=10.0, ref=10.0))
    at_low, _ = op.f_cycle_position(agg, ctx_at(td=12, cur=10.0, ref=10.0))
    past, _ = op.f_cycle_position(agg, ctx_at(td=39, cur=10.0, ref=10.0))
    beyond, _ = op.f_cycle_position(agg, ctx_at(td=200, cur=10.0, ref=10.0))
    assert at_low > early
    assert at_low > past > beyond


def test_cycle_position_rewards_discount():
    agg = base_agg()
    cheap, _ = op.f_cycle_position(agg, ctx_at(td=12, cur=9.0, ref=10.0))
    dear, _ = op.f_cycle_position(agg, ctx_at(td=12, cur=11.0, ref=10.0))
    assert cheap > dear


def test_broker_sentiment():
    bull = {"available": True, "counts": {"strongBuy": 5, "buy": 5, "hold": 1, "sell": 0, "strongSell": 0},
            "price_targets": {"mean": 12.0}}
    bear = {"available": True, "counts": {"strongBuy": 0, "buy": 0, "hold": 2, "sell": 6, "strongSell": 3},
            "price_targets": {"mean": 8.0}}
    b1, _ = op.f_broker_sentiment(bull, {"current_price": 10.0})
    b2, _ = op.f_broker_sentiment(bear, {"current_price": 10.0})
    assert b1 > b2
    assert op.f_broker_sentiment({"available": False}, {})[0] is None


# ---------------- assembly ----------------

def test_score_bounds_and_band():
    s = op.score_company(base_agg(), events_with([40] * 10), {"available": False}, ctx_at())
    assert s["score"] is not None
    assert 0 <= s["score"] <= 100
    label, key = op.band_for(s["score"])
    assert s["band"] == label and s["band_key"] == key


def test_weights_sum_to_100():
    assert sum(op.WEIGHTS.values()) == 100


def test_missing_components_renormalise_not_zero_fill():
    """A company with no broker data must not be penalised as if it scored 0."""
    agg = base_agg()
    evs = events_with([40] * 10)
    with_broker = op.score_company(
        agg, evs,
        {"available": True, "counts": {"strongBuy": 0, "buy": 0, "hold": 1, "sell": 0, "strongSell": 0},
         "price_targets": {"mean": 10.0}},
        ctx_at())
    without = op.score_company(agg, evs, {"available": False}, ctx_at())
    # broker factor would have scored ~50 (neutral); dropping it should leave the
    # remaining score close, never crater it.
    assert without["score"] is not None
    assert abs(without["score"] - with_broker["score"]) < 12
    assert without["coverage_pct"] < 100
    assert without["confidence"] in ("medium", "low", "high")


def test_all_data_missing_gives_none():
    empty = {"n_decided": 0, "distributions": {}}
    s = op.score_company(empty, [], {"available": False}, {"today": date(2026, 9, 1)})
    assert s["score"] is None
    assert s["band_key"] == "none"


def test_confidence_downgrades_with_thin_history():
    thin = base_agg(n_decided=4)
    s = op.score_company(thin, events_with([40] * 4), {"available": False}, ctx_at())
    assert s["confidence"] in ("low", "medium")


def test_better_company_outranks_worse():
    good = op.score_company(
        base_agg(success_rate_pct=97.0, median_recovery_days=15.0),
        events_with([15, 16, 14, 15, 16, 15]), {"available": False},
        ctx_at(month=9, td=12, cur=9.0, ref=10.0))
    bad = op.score_company(
        base_agg(success_rate_pct=45.0, median_recovery_days=180.0),
        events_with([10, 200, 30, 190, 5, 150]), {"available": False},
        ctx_at(month=8, td=400, cur=11.0, ref=10.0))
    assert good["score"] > bad["score"]


def test_strengths_and_risks_populated():
    s = op.score_company(base_agg(), events_with([40] * 10), {"available": False}, ctx_at())
    st, rk = op.strengths_and_risks(base_agg(), s, ctx_at())
    assert st and isinstance(st, list)
    assert any("Score computed without" in r for r in rk)


def test_summary_mentions_rank_and_is_historical():
    s = op.score_company(base_agg(), events_with([40] * 10), {"available": False}, ctx_at())
    text = op.summarise("BHP Group", "BHP", 1, base_agg(), s, ctx_at())
    assert "#1" in text and "Historically" in text
    assert "will" not in text.lower().split("historically")[0]


def test_build_context_computes_cycle_and_yield():
    dates = pd.bdate_range("2024-01-01", periods=120)
    px = pd.DataFrame({"Close": np.linspace(10, 11, 120)}, index=dates)
    events = [{"ex_date": str(dates[100].date()), "pre_div_close": 10.9,
               "dividend": 0.30, "recovered": True, "recovery_days": 5}]
    ctx = op.build_context(px, events, {}, today=dates[-1].date())
    assert ctx["td_since_last_ex"] == 19
    assert ctx["last_pre_div_close"] == 10.9
    assert ctx["dividend_yield_pct"] > 0


def test_universe_is_twenty_unique():
    codes = [t for t, _ in op.ASX20]
    assert len(codes) == 20
    assert len(set(codes)) == 20


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
