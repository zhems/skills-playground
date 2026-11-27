#!/usr/bin/env python3
"""Shared SEC EDGAR helpers: polite cached HTTP and ticker/name -> CIK resolution.

Stdlib only. Other scripts in this folder import it.

SEC asks automated clients to send a descriptive User-Agent with a contact
address and to stay under 10 requests/second. Set SEC_USER_AGENT, e.g.
    export SEC_USER_AGENT="Jane Doe research jane@example.com"

CLI:
    python3 edgar_client.py resolve "nvidia"
    python3 edgar_client.py resolve NVDA
"""
from __future__ import annotations

import difflib
import gzip
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CACHE_DIR = Path(os.environ.get("COMPANY_RESEARCH_CACHE", Path.home() / ".cache" / "company-research"))
DEFAULT_UA = "company-research-skill research@example.com"
_MIN_INTERVAL = 0.12  # seconds between live requests (< 10 req/s)
_last_request = 0.0
_warned_ua = False


def user_agent() -> str:
    global _warned_ua
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        if not _warned_ua:
            print("note: SEC_USER_AGENT not set; using a generic User-Agent. "
                  "SEC may throttle it. Set SEC_USER_AGENT='Name contact@email'.", file=sys.stderr)
            _warned_ua = True
        return DEFAULT_UA
    return ua


def fetch(url: str, ttl_hours: float = 24, retries: int = 3) -> bytes:
    """GET with on-disk cache. ttl_hours=0 disables cache reads."""
    global _last_request
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = CACHE_DIR / hashlib.sha256(url.encode()).hexdigest()
    if ttl_hours > 0 and key.exists() and (time.time() - key.stat().st_mtime) < ttl_hours * 3600:
        return key.read_bytes()

    headers = {"User-Agent": user_agent(), "Accept-Encoding": "gzip"}
    for attempt in range(retries):
        wait = _MIN_INTERVAL - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            with urlopen(Request(url, headers=headers), timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
            key.write_bytes(data)
            return data
        except HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            if e.code == 403:
                raise SystemExit(f"SEC returned 403 for {url}. Set SEC_USER_AGENT to 'Name email'.") from e
            raise
        except URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_json(url: str, ttl_hours: float = 24):
    return json.loads(fetch(url, ttl_hours))


def fetch_text(url: str, ttl_hours: float = 24 * 30) -> str:
    return fetch(url, ttl_hours).decode("utf-8", errors="replace")


# ---------------------------------------------------------------- resolution

_SUFFIXES = r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|sa|nv|ag|se|holdings?|group|the|class [a-c])\b"


def _norm(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    name = re.sub(_SUFFIXES, " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _ticker_table() -> list[dict]:
    raw = fetch_json("https://www.sec.gov/files/company_tickers_exchange.json", ttl_hours=24 * 7)
    fields = raw["fields"]
    return [dict(zip(fields, row)) for row in raw["data"]]


def resolve(query: str) -> tuple[dict | None, list[dict]]:
    """Return (best_match, candidates). best_match is None when ambiguous/not found.

    Each record: {cik, name, ticker, exchange}. Matches tickers first
    (BRK.B == BRK-B), then normalized company names.
    """
    q = query.strip()
    rows = _ticker_table()
    qt = q.upper().replace(".", "-")
    exact = [r for r in rows if (r["ticker"] or "").upper().replace(".", "-") == qt]
    if exact:
        return exact[0], exact

    qn = _norm(q)
    by_cik: dict[int, dict] = {}
    for r in rows:  # several tickers per CIK (share classes, preferreds): keep first
        by_cik.setdefault(r["cik"], r)
    uniq = list(by_cik.values())

    same = [r for r in uniq if _norm(r["name"]) == qn]
    if len(same) == 1:
        return same[0], same
    starts = [r for r in uniq if _norm(r["name"]).startswith(qn)] if qn else []
    if len(starts) == 1:
        return starts[0], starts
    contains = [r for r in uniq if qn and qn in _norm(r["name"])]
    if len(contains) == 1:
        return contains[0], contains
    pool = same or starts or contains
    if pool:
        return None, pool[:15]
    names = {_norm(r["name"]): r for r in uniq}
    close = difflib.get_close_matches(qn, list(names), n=10, cutoff=0.6)
    return None, [names[c] for c in close]


def resolve_or_exit(query: str) -> dict:
    best, cands = resolve(query)
    if best:
        return best
    if not cands:
        raise SystemExit(f"No SEC registrant matches '{query}'. It may not file with the SEC "
                         "(non-US listing): fall back to market.py --yf-financials and the company's IR site.")
    lines = "\n".join(f"  {c['ticker']:<8} {c['name']} (CIK {c['cik']}, {c['exchange']})" for c in cands)
    raise SystemExit(f"'{query}' is ambiguous. Re-run with one of these tickers:\n{lines}")


def cik10(cik: int | str) -> str:
    return str(int(cik)).zfill(10)


def submissions(cik: int | str, ttl_hours: float = 12) -> dict:
    return fetch_json(f"https://data.sec.gov/submissions/CIK{cik10(cik)}.json", ttl_hours)


def recent_filings(cik: int | str, forms: set[str] | None = None) -> list[dict]:
    """Recent filings (newest first) as dicts with form, filingDate, reportDate, accessionNumber, primaryDocument, url."""
    sub = submissions(cik)
    rec = sub["filings"]["recent"]
    out = []
    for i in range(len(rec["form"])):
        f = {k: rec[k][i] for k in ("form", "filingDate", "reportDate", "accessionNumber", "primaryDocument")}
        if forms and f["form"] not in forms:
            continue
        acc = f["accessionNumber"].replace("-", "")
        f["folder"] = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/"
        f["url"] = f["folder"] + f["primaryDocument"]
        out.append(f)
    return out


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "resolve":
        r = resolve_or_exit(" ".join(sys.argv[2:]))
        sub = submissions(r["cik"])
        print(json.dumps({**r, "sic": sub.get("sic"), "sicDescription": sub.get("sicDescription"),
                          "fiscalYearEnd": sub.get("fiscalYearEnd"), "stateOfIncorporation": sub.get("stateOfIncorporation"),
                          "category": sub.get("category")}, indent=2))
    else:
        print(__doc__)
