#!/usr/bin/env python3
"""Insider transactions from SEC Form 4 filings (officers, directors, 10% owners). Stdlib only.

Open-market purchases (code P) are the informative signal; sales (S) are often
pre-planned (Rule 10b5-1) or for taxes/diversification. Awards (A), option
exercises (M) and tax withholding (F) are compensation mechanics, not views.

Usage:
    python3 insiders.py AAPL                 # last 12 months, up to 80 Form 4s
    python3 insiders.py AAPL --months 24 --max 150 --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edgar_client import fetch, recent_filings, resolve_or_exit  # noqa: E402

CODES = {"P": "Open-market buy", "S": "Open-market sale", "A": "Grant/award", "M": "Option exercise",
         "F": "Tax withholding", "G": "Gift", "C": "Conversion", "X": "Option exercise", "D": "Disposition to issuer",
         "J": "Other"}


def _t(el, path):
    x = el.find(path)
    return x.text.strip() if x is not None and x.text else None


def parse_form4(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    owner = root.find("reportingOwner")
    name = _t(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    roles = []
    if rel is not None:
        if _t(rel, "isDirector") in ("1", "true"):
            roles.append("Director")
        if _t(rel, "isOfficer") in ("1", "true"):
            roles.append(_t(rel, "officerTitle") or "Officer")
        if _t(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("10% owner")
    plan = _t(root, "aff10b5One") in ("1", "true")
    footnotes = " ".join((f.text or "") for f in root.iter("footnote")).lower()
    if "10b5-1" in footnotes:
        plan = True
    out = []
    for tx in root.iter("nonDerivativeTransaction"):
        code = _t(tx, "transactionCoding/transactionCode")
        shares = _t(tx, "transactionAmounts/transactionShares/value")
        price = _t(tx, "transactionAmounts/transactionPricePerShare/value")
        out.append({
            "owner": name, "role": ", ".join(roles), "date": _t(tx, "transactionDate/value"), "code": code,
            "type": CODES.get(code, code), "ad": _t(tx, "transactionAmounts/transactionAcquiredDisposedCode/value"),
            "shares": float(shares) if shares else 0.0, "price": float(price) if price else None,
            "owned_after": float(_t(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value") or 0),
            "plan_10b5_1": plan,
        })
    return out


def collect(query: str, months: int, max_filings: int) -> dict:
    co = resolve_or_exit(query)
    cutoff = (date.today() - timedelta(days=months * 30.5)).isoformat()
    filings = [f for f in recent_filings(co["cik"], {"4", "4/A"}) if f["filingDate"] >= cutoff][:max_filings]
    txs = []
    for f in filings:
        doc = f["primaryDocument"].split("/")[-1]  # strip xsl rendering prefix to get raw XML
        try:
            txs += [{**t, "url": f["folder"] + doc} for t in parse_form4(fetch(f["folder"] + doc, ttl_hours=24 * 365))]
        except Exception as e:  # malformed or non-XML filing; skip but report
            print(f"skip {f['url']}: {e}", file=sys.stderr)
    return {"company": co, "since": cutoff, "filings_read": len(filings), "transactions": txs}


def render(d: dict) -> str:
    co, txs = d["company"], d["transactions"]
    out = [f"# Insider transactions: {co['name']} ({co['ticker']}) since {d['since']}",
           f"Form 4 filings read: {d['filings_read']} (SEC EDGAR)", ""]
    by_code = defaultdict(lambda: [0, 0.0, 0])
    for t in txs:
        b = by_code[t["type"]]
        b[0] += t["shares"]
        b[1] += t["shares"] * (t["price"] or 0)
        b[2] += 1
    out += ["## Summary by transaction type", "| Type | # | Shares | Value |", "|---|---|---|---|"]
    for k, (sh, val, n) in sorted(by_code.items(), key=lambda kv: -kv[1][1]):
        out.append(f"| {k} | {n} | {sh:,.0f} | ${val:,.0f} |")
    buys = [t for t in txs if t["code"] == "P"]
    sells = [t for t in txs if t["code"] == "S"]
    planned = sum(1 for t in sells if t["plan_10b5_1"])
    out += ["", f"**Open-market buys: {len(buys)}** · open-market sales: {len(sells)} "
            f"({planned} flagged as 10b5-1 pre-planned)", ""]
    per = defaultdict(lambda: {"role": "", "buy": 0.0, "sell": 0.0, "owned": 0.0, "last": ""})
    for t in txs:
        p = per[t["owner"]]
        p["role"] = t["role"]
        v = t["shares"] * (t["price"] or 0)
        if t["code"] == "P":
            p["buy"] += v
        elif t["code"] == "S":
            p["sell"] += v
        if t["date"] and t["date"] >= p["last"]:
            p["last"], p["owned"] = t["date"], t["owned_after"]
    out += ["## By insider (open-market only)", "| Insider | Role | Bought $ | Sold $ | Shares held after last tx |", "|---|---|---|---|---|"]
    for name, p in sorted(per.items(), key=lambda kv: -(kv[1]["buy"] + kv[1]["sell"])):
        if p["buy"] or p["sell"]:
            out.append(f"| {name} | {p['role']} | ${p['buy']:,.0f} | ${p['sell']:,.0f} | {p['owned']:,.0f} |")
    if buys:
        out += ["", "## Open-market purchases (the meaningful signal)"]
        for t in sorted(buys, key=lambda t: t["date"] or "", reverse=True):
            out.append(f"- {t['date']} {t['owner']} ({t['role']}): {t['shares']:,.0f} sh @ ${t['price'] or 0:,.2f} — {t['url']}")
    out += ["", "_Ownership above is direct holdings reported on the Form 4; indirect holdings (trusts, funds) may be extra._"]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--max", type=int, default=80, help="max Form 4 filings to fetch")
    ap.add_argument("--json")
    a = ap.parse_args()
    d = collect(a.company, a.months, a.max)
    print(render(d))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(d, indent=2))


if __name__ == "__main__":
    main()
