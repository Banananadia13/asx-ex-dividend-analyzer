"""Ground-truth unit tests for the analysis engine."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
from analysis import analyze_events, aggregate, split_adjust, low_delay_bucket


def make_px(dates, closes, opens=None):
    closes = np.asarray(closes, dtype=float)
    opens = closes if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({"Open": opens, "High": closes, "Low": closes,
                         "Close": closes, "Stock Splits": 0.0},
                        index=pd.DatetimeIndex(dates))


def bdays(start, n):
    return pd.bdate_range(start, periods=n)


def test_simple_recovery():
    # 10 pre days flat at 100, spike to 105 on day 5 (the high),
    # ex-date drops to 96, low 94 two days later, recovers to 100 on day 6.
    dates = bdays("2024-01-01", 20)
    closes = [100, 100, 100, 100, 100, 105, 100, 100, 100, 100,   # idx 0..9 pre
              96,  95,  94,  95,  97,  99, 100, 101, 102, 103]    # idx 10 = ex-date
    px = make_px(dates, closes)
    divs = pd.Series([4.0], index=[dates[10]])
    ev = analyze_events(px, divs, years=50)[0]

    assert ev.pre_div_close == 100
    assert ev.pre_div_date == str(dates[9].date())
    assert ev.ex_close == 96
    assert ev.drop_abs == 4.0
    assert ev.drop_pct == 4.0
    assert ev.drop_vs_dividend == 1.0
    assert ev.recovered is True
    assert ev.recovery_days == 6          # ex-date is day 0
    assert ev.recovery_date == str(dates[16].date())
    assert ev.high_price == 105
    assert ev.high_date == str(dates[5].date())
    assert ev.low_price == 94
    assert ev.low_date == str(dates[12].date())
    assert ev.low_days_after_ex == 2
    assert ev.low_to_recovery_days == 4
    assert ev.low_bucket == "within 1 week"


def test_no_recovery_and_insufficient():
    dates = bdays("2020-01-01", 300)
    closes = [100.0] * 100 + list(np.linspace(90, 80, 200))  # never recovers
    px = make_px(dates, closes)
    divs = pd.Series([2.0], index=[dates[100]])
    ev = analyze_events(px, divs, years=50)[0]
    assert ev.recovered is False

    # insufficient: ex-date near end of data, not recovered yet
    dates2 = bdays("2020-01-01", 110)
    closes2 = [100.0] * 100 + [95.0] * 10
    px2 = make_px(dates2, closes2)
    divs2 = pd.Series([2.0], index=[dates2[100]])
    ev2 = analyze_events(px2, divs2, years=50)[0]
    assert ev2.recovered is None
    assert any("too early" in f for f in ev2.flags)


def test_recovery_day_zero():
    # closes above P0 on the ex-date itself
    dates = bdays("2024-01-01", 10)
    closes = [100] * 5 + [101, 102, 102, 102, 102]
    px = make_px(dates, closes)
    divs = pd.Series([1.0], index=[dates[5]])
    ev = analyze_events(px, divs, years=50)[0]
    assert ev.recovered is True and ev.recovery_days == 0
    assert ev.low_days_after_ex == 0


def test_high_window_capped_by_previous_exdate():
    dates = bdays("2024-01-01", 40)
    closes = [200] + [100] * 19 + [110] + [100] * 19   # big high at idx 0, lesser high idx 20
    px = make_px(dates, closes)
    divs = pd.Series([1.0, 1.0], index=[dates[10], dates[30]])
    evs = analyze_events(px, divs, years=50)
    # second event's high window starts after first ex-date (idx 10) => high = 110 at idx 20
    assert evs[1].high_price == 110
    assert evs[1].high_date == str(dates[20].date())
    # first event's window includes idx 0 => 200
    assert evs[0].high_price == 200


def test_ex_date_on_non_trading_day():
    dates = bdays("2024-01-01", 10)
    closes = [100] * 5 + [96, 97, 98, 99, 100]
    px = make_px(dates, closes)
    # ex-date recorded on a Saturday between dates[4] (Fri) and dates[5] (Mon)
    sat = dates[4] + pd.Timedelta(days=1)
    assert sat.dayofweek == 5
    divs = pd.Series([4.0], index=[sat])
    ev = analyze_events(px, divs, years=50)[0]
    assert ev.pre_div_close == 100
    assert ev.ex_close == 96
    assert any("not a trading day" in f for f in ev.flags)


def test_split_adjust():
    dates = bdays("2024-01-01", 6)
    px = pd.DataFrame({"Open": [100, 100, 100, 25, 25, 25],
                       "High": [100, 100, 100, 25, 25, 25],
                       "Low":  [100, 100, 100, 25, 25, 25],
                       "Close": [100, 100, 100, 25, 25, 25],
                       "Stock Splits": [0, 0, 0, 4.0, 0, 0]},
                      index=pd.DatetimeIndex(dates))
    adj = split_adjust(px)
    assert list(adj["Close"].round(4)) == [25, 25, 25, 25, 25, 25]


def test_special_dividend_flag():
    dates = bdays("2024-01-01", 60)
    closes = [100] * 30 + [70] * 30
    px = make_px(dates, closes)
    divs = pd.Series([30.0], index=[dates[30]])
    ev = analyze_events(px, divs, years=50)[0]
    assert any("special dividend" in f for f in ev.flags)


def test_aggregate():
    dates = bdays("2015-01-01", 260 * 8)
    rng = np.random.default_rng(42)
    n = len(dates)
    closes = 100 + np.cumsum(rng.normal(0.01, 0.4, n))
    px = make_px(dates, closes)
    # dividends every ~126 trading days
    div_idx = list(range(120, n - 60, 126))
    divs = pd.Series([1.0] * len(div_idx), index=[dates[i] for i in div_idx])
    evs = analyze_events(px, divs, years=50)
    agg = aggregate(evs)
    assert agg["n_events"] == len(div_idx)
    assert agg["n_decided"] + agg["n_insufficient"] == agg["n_usable"]
    if agg["avg_recovery_days"] is not None:
        assert agg["fastest_recovery"]["days"] <= agg["avg_recovery_days"] <= agg["slowest_recovery"]["days"]
    assert agg["success_rate_pct"] == round(agg["n_recovered"] / agg["n_decided"] * 100, 1)
    # distributions consistent
    assert sum(agg["distributions"]["low_bucket"].values()) == agg["n_decided"]
    assert sum(agg["distributions"]["recovery_month"].values()) == agg["n_recovered"]


def test_buckets():
    assert low_delay_bucket(0) == "within 1 week"
    assert low_delay_bucket(5) == "within 1 week"
    assert low_delay_bucket(6) == "within 2 weeks"
    assert low_delay_bucket(10) == "within 2 weeks"
    assert low_delay_bucket(22) == "within 1 month"
    assert low_delay_bucket(44) == "within 2 months"
    assert low_delay_bucket(45) == "3+ months"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
