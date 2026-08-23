"""
Historical pattern engine: turns aggregate statistics into plain-English
observations. Every statement is derived from computed history; nothing is
predicted. The UI prefixes the section with a standing disclaimer, and each
sentence uses "historically" phrasing on purpose.
"""
from __future__ import annotations

from analysis import MONTH_NAMES


def _mn(m):
    return MONTH_NAMES[int(m) - 1] if m else None


def _fmt_days(d):
    return f"{d:.0f} trading days" if d is not None else "n/a"


def build_insights(ticker_label: str, agg: dict, years: int) -> dict:
    """Return {'headline': [...], 'timing': [...], 'seasonal': {...}, 'caveats': [...]}"""
    ins, timing, caveats = [], [], []
    n = agg.get("n_usable", 0)
    dec = agg.get("n_decided", 0)
    modes = agg.get("modes", {})
    dist = agg.get("distributions", {})
    typ = agg.get("typical", {})

    if n == 0:
        return {"headline": [f"No ex-dividend events with usable price data were found for {ticker_label} in the last {years} years."],
                "timing": [], "seasonal": {}, "caveats": []}

    ins.append(f"Over the last {years} years, {ticker_label} has gone ex-dividend {n} times"
               + (f" ({dec} events old enough to judge recovery)." if dec != n else "."))

    if agg.get("avg_drop_pct") is not None:
        direction = "fallen" if agg["avg_drop_pct"] > 0 else "risen"
        ins.append(f"Historically the share price has {direction} an average of "
                   f"{abs(agg['avg_drop_pct']):.1f}% on the ex-dividend date "
                   f"(median {abs(agg.get('median_drop_pct') or 0):.1f}%).")

    if agg.get("avg_drop_vs_dividend") is not None:
        r = agg["avg_drop_vs_dividend"]
        ins.append(f"On average the ex-date fall equalled {r:.2f}× the dividend amount "
                   f"({'more' if r > 1 else 'less'} than the theoretical 1.0× drop).")

    if agg.get("avg_recovery_days") is not None:
        ins.append(f"Historically the average recovery back to the pre-dividend close took "
                   f"{agg['avg_recovery_days']:.0f} trading days "
                   f"(median {agg.get('median_recovery_days'):.0f}); the fastest was "
                   f"{agg['fastest_recovery']['days']} days ({agg['fastest_recovery']['ex_date']}) "
                   f"and the slowest {agg['slowest_recovery']['days']} days ({agg['slowest_recovery']['ex_date']}).")

    if agg.get("success_rate_pct") is not None:
        sr = agg["success_rate_pct"]
        ins.append(f"The price recovered to its pre-dividend level within 250 trading days in "
                   f"{sr:.0f}% of decided events ({agg['n_recovered']} of {dec}).")

    # timing insights
    if modes.get("low_week_after_ex"):
        m = modes["low_week_after_ex"]
        timing.append(f"Historically the post-dividend low most commonly occurred during "
                      f"Week {m['value']} after the ex-dividend date "
                      f"({m['count']} of {dec} events, {m['share_pct']:.0f}%).")
    if agg.get("median_low_days_after_ex") is not None:
        timing.append(f"The typical (median) low came {_fmt_days(agg['median_low_days_after_ex'])} "
                      f"after the ex-dividend date, with an average further "
                      f"{_fmt_days(agg.get('avg_low_to_recovery_days'))} from the low back to recovery.")
    if agg.get("avg_low_drawdown_pct") is not None:
        timing.append(f"The average drawdown from pre-dividend close to the post-dividend low was "
                      f"{agg['avg_low_drawdown_pct']:.1f}%.")
    if modes.get("low_bucket"):
        b = modes["low_bucket"]
        timing.append(f"Across all events, the low fell {b['value']} of the ex-date most often "
                      f"({b['share_pct']:.0f}% of events).")
    if agg.get("median_low_days_after_ex") is not None and agg["median_low_days_after_ex"] > 2:
        timing.append("Historically, buying shortly AFTER the ex-dividend date — rather than just "
                      "before it — has produced better entry prices, because the low has typically "
                      "come after the ex-date drop.")

    # seasonal summary
    seasonal = {}
    if modes.get("low_month"):
        m = modes["low_month"]
        seasonal["weakest_month"] = {
            "month": _mn(m["value"]), "share_pct": m["share_pct"], "count": m["count"],
            "text": f"Historically {_mn(m['value'])} has most often contained the post-dividend low "
                    f"({m['count']} of {dec} events, {m['share_pct']:.0f}%), making it the most common "
                    f"historical buying window."}
    if modes.get("recovery_month"):
        m = modes["recovery_month"]
        seasonal["recovery_month"] = {
            "month": _mn(m["value"]), "share_pct": m["share_pct"], "count": m["count"],
            "text": f"Historically {_mn(m['value'])} has been the most common month for the price to "
                    f"complete its recovery ({m['share_pct']:.0f}% of recovered events)."}
    if modes.get("high_month"):
        m = modes["high_month"]
        seasonal["strongest_month"] = {
            "month": _mn(m["value"]), "share_pct": m["share_pct"], "count": m["count"],
            "text": f"Historically the pre-dividend high has most commonly occurred during "
                    f"{_mn(m['value'])} ({m['share_pct']:.0f}% of events) — the most common historical "
                    f"selling window."}
    if typ.get("low_after_ex_trading_days") is not None:
        seasonal["typical_path"] = {
            "text": (f"Typical historical path: low about {typ['low_after_ex_trading_days']:.0f} trading days "
                     f"(~{typ.get('low_after_ex_calendar_days')} calendar days) after the ex-date, "
                     f"full recovery about {typ.get('recovery_after_ex_trading_days'):.0f} trading days "
                     f"(~{typ.get('recovery_after_ex_calendar_days')} calendar days) after the ex-date.")}

    # best/worst ex-month by recovery speed (needs enough samples)
    by_m = [m for m in agg.get("by_ex_month", []) if m["events"] >= 3 and m["avg_recovery_days"] is not None]
    if len(by_m) >= 2:
        fast = min(by_m, key=lambda m: m["avg_recovery_days"])
        slow = max(by_m, key=lambda m: m["avg_recovery_days"])
        if fast["month"] != slow["month"]:
            seasonal["ex_month_speed"] = {
                "text": (f"Dividends going ex in {fast['month_name']} have historically recovered fastest "
                        f"(avg {fast['avg_recovery_days']:.0f} days over {fast['events']} events); "
                        f"those in {slow['month_name']} slowest "
                        f"(avg {slow['avg_recovery_days']:.0f} days over {slow['events']} events).")}

    # strongest year
    by_y = [y for y in agg.get("by_year", []) if y["avg_recovery_days"] is not None and y["recovered"] > 0]
    if by_y:
        best = min(by_y, key=lambda y: y["avg_recovery_days"])
        ins.append(f"The strongest (fastest-recovering) year historically was {best['year']} "
                   f"(avg {best['avg_recovery_days']:.0f} trading days).")

    # caveats
    q = agg.get("quality", {})
    if q.get("low_confidence"):
        caveats.append(q.get("note"))
    if q.get("events_with_flags"):
        caveats.append(f"{q['events_with_flags']} event(s) carry data-quality flags — open the event table to review them.")
    if dec and modes.get("low_month") and modes["low_month"]["share_pct"] < 30:
        caveats.append("Monthly patterns here are diffuse (no month dominates strongly); treat seasonal statements as weak tendencies, not rules.")
    caveats.append("All statements are historical observations only — not investment advice and not predictions of future performance.")

    return {"headline": ins, "timing": timing, "seasonal": seasonal,
            "caveats": [c for c in caveats if c]}
