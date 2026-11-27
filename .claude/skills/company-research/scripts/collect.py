#!/usr/bin/env python3
"""Run every data script for one company into a research folder, in parallel. Stdlib only
(market.py is launched through `uv run` because it needs yfinance).

Usage:
    python3 collect.py AAPL --out ~/Downloads/company-research/AAPL-2026-11-22
    python3 collect.py AAPL --out DIR --peers MSFT,GOOGL,AMZN
    python3 collect.py AAPL --out DIR --skip insiders,market

Writes DIR/01_fundamentals.md, 02_segments.md, 03_market.md, 04_insiders.md,
05_risk_diff.md, 06_peers.md, 07_peer_valuation.md, filings/*.txt, data/*.json and prints a manifest.
Market data comes from the local Data-Octopus DB when OCTOPUS_RO_URL is set and the
ticker is on its watchlist, otherwise from yfinance (each market file states its source).
A failing step is reported and skipped; the rest still run.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company", help="ticker (preferred) or company name")
    ap.add_argument("--out", required=True)
    ap.add_argument("--peers", default="", help="comma-separated peer tickers")
    ap.add_argument("--skip", default="", help="comma-separated steps to skip")
    ap.add_argument("--discount", help="fixed discount rate (default: 10y Treasury + 5%% ERP from the local DB, else 9%%)")
    a = ap.parse_args()

    # Resolve once so every step uses the same ticker.
    res = subprocess.run([PY, str(HERE / "edgar_client.py"), "resolve", a.company], capture_output=True, text=True)
    ticker = a.company
    on_edgar = res.returncode == 0
    if on_edgar:
        import json
        ticker = json.loads(res.stdout)["ticker"]
    else:
        print(res.stderr.strip() or res.stdout.strip(), file=sys.stderr)

    out = Path(a.out)
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "filings").mkdir(exist_ok=True)
    uv = shutil.which("uv")
    steps = {
        "fundamentals": ([PY, HERE / "fundamentals.py", ticker, "--json", out / "data/fundamentals.json"], "01_fundamentals.md"),
        "segments": ([PY, HERE / "segments.py", ticker, "--filings", "2", "--json", out / "data/segments.json"], "02_segments.md"),
        "market": ([uv, "run", "-q", HERE / "market.py", ticker, *(["--discount", a.discount] if a.discount else []),
                    "--json", out / "data/market.json"] if uv else None, "03_market.md"),
        "insiders": ([PY, HERE / "insiders.py", ticker, "--json", out / "data/insiders.json"], "04_insiders.md"),
        "risk_diff": ([PY, HERE / "filings.py", "risk-diff", ticker], "05_risk_diff.md"),
        "sections": ([PY, HERE / "filings.py", "sections", ticker, "--out", out / "filings"], "filings/annual_manifest.txt"),
        "sections_q": ([PY, HERE / "filings.py", "sections", ticker, "--quarterly", "--out", out / "filings"], "filings/quarterly_manifest.txt"),
        "filing_list": ([PY, HERE / "filings.py", "list", ticker, "--limit", "30"], "filings/filing_list.txt"),
        "exhibits": ([PY, HERE / "filings.py", "exhibits", ticker, "--limit", "3"], "filings/recent_8k_exhibits.txt"),
    }
    if a.peers:
        steps["peers"] = ([PY, HERE / "fundamentals.py", ticker, *a.peers.split(","), "--peers",
                           "--json", out / "data/peers.json"], "06_peers.md")
        steps["peer_valuation"] = ([uv, "run", "-q", HERE / "market.py", "--compare", ticker, *a.peers.split(",")]
                                   if uv else None, "07_peer_valuation.md")
    if not on_edgar:  # non-SEC company: only Yahoo data is available from scripts
        steps = {"market": steps["market"],
                 "yf_financials": ([uv, "run", "-q", HERE / "market.py", ticker, "--yf-financials"] if uv else None,
                                   "01_yahoo_financials.md")}
    skip = set(filter(None, a.skip.split(",")))

    def run(item):
        name, (cmd, dest) = item
        if name in skip:
            return name, "skipped", dest
        if cmd is None:
            return name, "FAILED: uv not installed (needed for yfinance)", dest
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=900)
        (out / dest).write_text(p.stdout + (f"\n\n<!-- stderr:\n{p.stderr}\n-->" if p.returncode else ""))
        return name, "ok" if p.returncode == 0 else f"FAILED (exit {p.returncode}): {p.stderr.strip()[-300:]}", dest

    # SEC allows 10 req/s across all processes; 3 workers with the client's spacing stays under it.
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(run, steps.items()))
    print(f"# Collected data for {ticker} -> {out}")
    if not os.environ.get("OCTOPUS_RO_URL"):
        print("note: OCTOPUS_RO_URL is not set, so market data came from yfinance rather than the local Data-Octopus DB.")
    for name, status, dest in results:
        print(f"{name:<13} {status:<8}  {out / dest}" if status == "ok" else f"{name:<13} {status}")


if __name__ == "__main__":
    main()
