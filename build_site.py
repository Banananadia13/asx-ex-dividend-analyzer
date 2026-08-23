"""
Build the static GitHub Pages site.

GitHub Pages serves static files only — it cannot run the FastAPI backend. So
this script does the backend's work ahead of time: it runs exactly the same
analysis engine, writes the results as JSON files, and copies the frontend
alongside them with a flag telling it to read those files instead of calling
an API. A GitHub Actions workflow runs this on a schedule, so the published
site refreshes itself without anyone touching it.

The JSON is produced by the *same* functions the live API uses
(`main.build_analysis_payload`, `scanner._analyse_one`), so the hosted site and
the locally-run app cannot drift apart.

Usage:
    python build_site.py                # real data (needs internet)
    python build_site.py --demo         # synthetic data, for testing offline
    python build_site.py --out site     # output directory (default: site)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backend"))

import analysis            # noqa: E402
import data_source as ds   # noqa: E402
import main as api         # noqa: E402
import opportunity as op   # noqa: E402
import scanner             # noqa: E402

YEARS = 20
MAX_WORKERS = 4


def log(msg):
    print(msg, flush=True)


def write_json(path: str, payload) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), default=str)
    return os.path.getsize(path)


def universe(demo: bool):
    if demo:
        return [(f"DEMO{i:02d}", f"Demo Company {i:02d}") for i in range(1, 21)]
    return list(op.ASX20)


def build_company(code: str, name: str, demo: bool, out: str) -> dict:
    """Analyse one company and write every JSON file the frontend asks for."""
    symbol = code if demo else ds.normalize_ticker(code)

    payload = api.build_analysis_payload(symbol, years=YEARS)
    write_json(os.path.join(out, "data", "analyze", f"{code}.json"), payload)

    consensus = ds.fetch_consensus(symbol)
    write_json(os.path.join(out, "data", "consensus", f"{code}.json"), consensus)

    future = ds.fetch_future_events(symbol)
    write_json(os.path.join(out, "data", "future", f"{code}.json"), future)

    # Scored row + detail, via the same code path the live scanner uses.
    row = scanner._analyse_one(code, name, YEARS, False, demo)
    write_json(os.path.join(out, "data", "top20", "detail", f"{code}.json"), row["detail"])

    n_events = payload["aggregate"].get("n_usable", 0)
    log(f"  ✓ {code:<8} score {str(row.get('score')):<6} · {n_events} events")
    return row


def build(out: str, demo: bool) -> int:
    started = datetime.now(timezone.utc)
    uni = universe(demo)
    log(f"Building static site into {out}/  ({'DEMO data' if demo else 'live data'})")
    log(f"Analysing {len(uni)} companies with {MAX_WORKERS} workers…")

    if os.path.isdir(os.path.join(out, "data")):
        shutil.rmtree(os.path.join(out, "data"))

    rows, failures = [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(build_company, c, n, demo, out): (c, n) for c, n in uni}
        for fut in as_completed(futs):
            code, name = futs[fut]
            try:
                rows.append(fut.result())
            except Exception as exc:
                traceback.print_exc()
                log(f"  ✗ {code:<8} FAILED: {exc}")
                failures.append({"ticker": code, "name": name, "error": str(exc)[:300]})

    if not rows:
        raise SystemExit("No company could be analysed — refusing to publish an empty site.")

    ranked = scanner._rank(rows)
    finished = datetime.now(timezone.utc)

    # Same shape as GET /api/top20/status, minus the per-row detail blobs.
    status = {
        "status": "done",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "total": len(uni),
        "completed": len(uni),
        "current": [],
        "results": [{k: v for k, v in r.items() if k != "detail"} for r in ranked],
        "failures": failures,
        "years": YEARS,
        "demo": demo,
        "universe_as_at": op.UNIVERSE_AS_AT,
        "weights": dict(op.WEIGHTS),
        "disclaimer": op.DISCLAIMER,
    }
    write_json(os.path.join(out, "data", "top20", "status.json"), status)

    write_json(os.path.join(out, "data", "top20", "universe.json"), {
        "universe": [{"ticker": t, "name": n} for t, n in uni],
        "as_at": op.UNIVERSE_AS_AT,
        "weights": op.WEIGHTS,
        "factor_labels": op.FACTOR_LABELS,
        "bands": [{"min": m, "label": l, "key": k} for m, l, k in op.BANDS],
        "note": ("Default list is the S&P/ASX 20 index membership. S&P reviews the "
                 "index quarterly, so verify against asx.com.au if precision matters."),
        "disclaimer": op.DISCLAIMER,
    })

    try:
        write_json(os.path.join(out, "data", "market-context.json"), ds.fetch_market_context())
    except Exception as exc:
        log(f"  ! market context unavailable: {exc}")
        write_json(os.path.join(out, "data", "market-context.json"),
                   {"available": False, "note": f"Market context could not be retrieved: {exc}"})

    available = sorted(r["ticker"] for r in ranked)
    write_json(os.path.join(out, "data", "manifest.json"), {
        "built_at": finished.isoformat(),
        "years": YEARS,
        "demo": demo,
        "tickers": available,
        "failures": [f["ticker"] for f in failures],
    })

    copy_frontend(out, finished, available, demo, failures)

    secs = (finished - started).total_seconds()
    log(f"\nDone in {secs:.0f}s — {len(ranked)} companies"
        + (f", {len(failures)} failed" if failures else ""))
    return len(failures)


def copy_frontend(out: str, built_at, available, demo: bool, failures):
    """Copy the frontend and rewrite it for static hosting."""
    src = os.path.join(HERE, "frontend")
    for name in ("styles.css", "app.js"):
        shutil.copy2(os.path.join(src, name), os.path.join(out, name))
    os.makedirs(os.path.join(out, "vendor"), exist_ok=True)
    shutil.copy2(os.path.join(src, "vendor", "echarts.min.js"),
                 os.path.join(out, "vendor", "echarts.min.js"))

    # Absolute /static/... paths break under a project Pages URL
    # (username.github.io/repo/), so make every asset reference relative.
    html = open(os.path.join(src, "index.html"), encoding="utf-8").read()
    html = html.replace('href="/static/styles.css"', 'href="styles.css"')
    html = html.replace('src="/static/app.js"', 'src="app.js"')
    html = html.replace('src="/static/vendor/echarts.min.js"', 'src="vendor/echarts.min.js"')
    html = html.replace("</head>", '<script src="config.js"></script>\n</head>')
    if not re.search(r'src="config\.js"', html):
        raise SystemExit("Failed to inject config.js — index.html has no </head>.")
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)

    config = {
        "static": True,
        "builtAt": built_at.isoformat(),
        "years": YEARS,
        "demo": demo,
        "tickers": available,
        "failures": [f["ticker"] for f in failures],
    }
    with open(os.path.join(out, "config.js"), "w", encoding="utf-8") as fh:
        fh.write("window.XD_CONFIG = " + json.dumps(config) + ";\n")

    # Stop Pages running the output through Jekyll (it hides files starting with _).
    open(os.path.join(out, ".nojekyll"), "w").close()
    log("  ✓ frontend copied and patched for static hosting")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--demo", action="store_true",
                    help="build with synthetic data (no internet needed)")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    failures = build(out, args.demo)
    # A few individual tickers failing is tolerable; the site still publishes.
    return 0 if failures < len(universe(args.demo)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
