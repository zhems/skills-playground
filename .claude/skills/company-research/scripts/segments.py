#!/usr/bin/env python3
"""Revenue / operating income by segment, product, geography and major customer,
parsed from the inline XBRL in a 10-K / 20-F. Stdlib only.

The companyfacts API drops dimensional (segment) data, so this reads the filing
itself. Each annual filing carries ~3 years of comparatives.

Usage:
    python3 segments.py AAPL                # latest annual filing
    python3 segments.py AAPL --filings 2    # latest two (≈5 years of history)
    python3 segments.py AAPL --json out.json
    python3 segments.py AAPL --all-dims     # include multi-dimension facts (e.g. segment x product)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edgar_client import fetch_text, recent_filings, resolve_or_exit  # noqa: E402

AXES = {
    "StatementBusinessSegmentsAxis": "Business segment",
    "ProductOrServiceAxis": "Product / service",
    "StatementGeographicalAxis": "Geography",
    "SegmentsAxis": "Business segment",  # ifrs-full
    "ProductsAndServicesAxis": "Product / service",  # ifrs-full
    "GeographicalAreasAxis": "Geography",  # ifrs-full
    "MajorCustomersAxis": "Major customer",
    "ConcentrationRiskByBenchmarkAxis": "Concentration benchmark",
    "ConcentrationRiskByTypeAxis": "Concentration type",
}
CONCEPTS = {
    "Revenues": "Revenue", "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "Revenue", "Revenue": "Revenue",
    "RevenueFromExternalCustomers": "Revenue",
    "OperatingIncomeLoss": "Operating income", "ProfitLossFromOperatingActivities": "Operating income",
    "GrossProfit": "Gross profit", "CostOfRevenue": "Cost of revenue", "CostOfGoodsAndServicesSold": "Cost of revenue",
    "SegmentReportingInformationOperatingIncomeLoss": "Operating income",
    "Assets": "Assets", "NoncurrentAssets": "Long-lived assets",
    "LongLivedAssets": "Long-lived assets", "PropertyPlantAndEquipmentNet": "PP&E",
    "ConcentrationRiskPercentage1": "Concentration %",
}
_ATTR = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')


def _attrs(s: str) -> dict:
    return {k.lower(): v for k, v in _ATTR.findall(s)}


def _local(qname: str) -> str:
    return qname.split(":")[-1]


def pretty_member(qname: str) -> str:
    n = re.sub(r"(Segment)?Member$", "", _local(qname))
    n = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z]{2})(?=[A-Z][a-z])", " ", n)
    return n.strip() or qname


def parse_contexts(doc: str) -> dict:
    ctx = {}
    for m in re.finditer(r"<(?:\w+:)?context\b([^>]*)>(.*?)</(?:\w+:)?context>", doc, re.S | re.I):
        cid = _attrs(m.group(1)).get("id")
        body = m.group(2)
        dims = [(_local(a), mem.strip()) for a, mem in
                re.findall(r'<(?:\w+:)?explicitMember[^>]*dimension="([^"]+)"[^>]*>([^<]+)<', body, re.I)]
        typed = re.search(r"typedMember", body, re.I) is not None
        start = re.search(r"<(?:\w+:)?startDate>([^<]+)<", body, re.I)
        end = re.search(r"<(?:\w+:)?endDate>([^<]+)<", body, re.I)
        inst = re.search(r"<(?:\w+:)?instant>([^<]+)<", body, re.I)
        ctx[cid] = {"dims": dims, "typed": typed, "start": start.group(1).strip() if start else None,
                    "end": (end or inst).group(1).strip() if (end or inst) else None}
    return ctx


def _num(text: str, a: dict) -> float | None:
    t = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    fmt = a.get("format", "")
    if "zerodash" in fmt or "fixed-zero" in fmt or t in ("-", "—", "–", ""):
        return 0.0
    if "numcommadecimal" in fmt or "num-comma-decimal" in fmt:
        t = t.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        t = t.replace(",", "").replace(" ", "")
    try:
        v = float(re.sub(r"[^\d.]", "", t))
    except ValueError:
        return None
    v *= 10 ** int(a.get("scale", "0") or 0)
    return -v if a.get("sign") == "-" else v


def parse_facts(doc: str, ctx: dict, all_dims: bool) -> list[dict]:
    out = []
    for m in re.finditer(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", doc, re.S | re.I):
        a = _attrs(m.group(1))
        concept = _local(a.get("name", ""))
        if concept not in CONCEPTS:
            continue
        c = ctx.get(a.get("contextref"))
        if not c or c["typed"] or not c["end"]:
            continue
        # ConsolidationItemsAxis=OperatingSegmentsMember just qualifies segment facts; drop it.
        # Other consolidation members (eliminations, corporate) are reconciling items: skip them.
        cons = [d for d in c["dims"] if d[0] == "ConsolidationItemsAxis"]
        if cons and not all("OperatingSegments" in d[1] for d in cons):
            continue
        dims = [d for d in c["dims"] if d[0] != "ConsolidationItemsAxis"]
        dd = dict(dims)
        if CONCEPTS[concept] == "Concentration %" and dims:
            # typically customer x benchmark (revenue/receivables) x risk type
            who = dd.get("MajorCustomersAxis") or dd.get("StatementGeographicalAxis") or dd.get("StatementBusinessSegmentsAxis")
            bench = dd.get("ConcentrationRiskByBenchmarkAxis")
            if not who or not bench:
                continue
            axis = "Customer / concentration"
            member = f"{pretty_member(who)} — % of {pretty_member(bench).replace(' Benchmark', '').lower()}"
        elif not dims:
            axis, member = "Total", "Total"
        elif len(dims) == 1 and dims[0][0] in AXES:
            axis, member = AXES[dims[0][0]], pretty_member(dims[0][1])
        elif all_dims and any(d[0] in AXES for d in dims):
            axis = " × ".join(AXES.get(d[0], d[0]) for d in dims)
            member = " / ".join(pretty_member(d[1]) for d in dims)
        else:
            continue
        v = _num(m.group(2), a)
        if v is None:
            continue
        period = c["end"] if not c["start"] else f"{c['start']}..{c['end']}"
        days = (date.fromisoformat(c["end"]) - date.fromisoformat(c["start"])).days if c["start"] else None
        if days is not None and not 350 <= days <= 380:
            continue  # annual figures only
        out.append({"concept": CONCEPTS[concept], "raw_concept": a.get("name"), "axis": axis, "member": member,
                    "period_end": c["end"], "period": period, "value": v})
    return out


def collect(query: str, n_filings: int, all_dims: bool) -> dict:
    co = resolve_or_exit(query)
    filings = recent_filings(co["cik"], {"10-K", "20-F", "40-F"})[:n_filings]
    if not filings:
        raise SystemExit("No 10-K/20-F/40-F filings found.")
    facts: dict[tuple, float] = {}
    used = []
    for f in reversed(filings):  # older first so newer (restated) values overwrite
        doc = fetch_text(f["url"])
        if "ix:nonfraction" not in doc[:2_000_000].lower() and "ix:nonfraction" not in doc.lower():
            used.append({**f, "note": "no inline XBRL (pre-2019 or exhibit-based); read the segment note manually"})
            continue
        ctx = parse_contexts(doc)
        for x in parse_facts(doc, ctx, all_dims):
            facts[(x["concept"], x["axis"], x["member"], x["period_end"])] = x["value"]
        used.append(f)
    # tree: concept -> axis -> member -> period_end -> value
    tree: dict = defaultdict(lambda: defaultdict(dict))
    for (concept, axis, member, end), v in facts.items():
        tree[(concept, axis)][member][end] = v
    return {"company": co, "filings": used, "tree": tree}


def render(data: dict) -> str:
    co, tree = data["company"], data["tree"]
    lines = [f"# {co['name']} ({co['ticker']}) — breakdowns from inline XBRL", ""]
    for f in data["filings"]:
        lines.append(f"- Source: {f['form']} for period {f['reportDate']} filed {f['filingDate']}: {f['url']}"
                     + (f" — {f['note']}" if f.get("note") else ""))
    lines.append("")
    totals = {c: tree.get((c, "Total"), {}).get("Total", {}) for c, _ in tree}
    order = ["Revenue", "Operating income", "Gross profit", "Cost of revenue", "Long-lived assets", "PP&E", "Assets", "Concentration %"]
    keys = sorted([k for k in tree if k[1] != "Total"], key=lambda k: (order.index(k[0]) if k[0] in order else 99, k[1]))
    if not keys:
        lines.append("No dimensional breakdowns found. Read the segment note (usually 'Segment Information') in the filing.")
    for concept, axis in keys:
        members = tree[(concept, axis)]
        periods = sorted({p for m in members.values() for p in m})[-5:]
        is_pct = concept == "Concentration %"
        lines.append(f"## {concept} by {axis}")
        hdr = ["Member"] + [f"FYE {p}" for p in periods]
        total = totals.get(concept, {})
        latest = periods[-1]
        member_sum = sum(m.get(latest) or 0 for m in members.values())
        exceeds = bool(total.get(latest)) and member_sum > total[latest] * 1.05
        if not is_pct:
            share_label = ("% of segment sum (segments exceed consolidated: unallocated costs/overlaps)" if exceeds
                           else "% of consolidated" if total.get(latest) else "% of members shown (no total tagged!)")
            hdr += [f"{share_label} ({latest})", "YoY"]
        lines.append("| " + " | ".join(hdr) + " |")
        lines.append("|" + "---|" * len(hdr))
        ranked = sorted(members.items(), key=lambda kv: -(kv[1].get(latest) or 0))
        for member, vals in ranked:
            row = [member]
            for p in periods:
                v = vals.get(p)
                row.append("–" if v is None else (f"{v * 100:.1f}%" if is_pct else _money(v)))
            if not is_pct:
                denom = member_sum if exceeds or not total.get(latest) else total[latest]
                v = vals.get(latest)
                row.append(f"{v / denom * 100:.1f}%" if v is not None and denom else "–")
                prev = vals.get(periods[-2]) if len(periods) > 1 else None
                row.append(f"{(v / prev - 1) * 100:+.1f}%" if v is not None and prev and prev > 0 else "–")
            lines.append("| " + " | ".join(row) + " |")
        if total and not is_pct:
            lines.append("| **Consolidated total** | " + " | ".join(_money(total.get(p)) if total.get(p) is not None else "–" for p in periods) + " | 100% | |")
        lines.append("")
    lines.append("_Members can overlap (e.g. a country inside a region, or reconciling items). "
                 "Check the segment note before summing; '% of total' uses the consolidated figure where tagged._")
    return "\n".join(lines)


def _money(v) -> str:
    a = abs(v)
    return f"{v / 1e9:,.2f}B" if a >= 1e9 else f"{v / 1e6:,.1f}M" if a >= 1e6 else f"{v:,.0f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("company")
    ap.add_argument("--filings", type=int, default=1, help="number of annual filings to read (newest first)")
    ap.add_argument("--all-dims", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    data = collect(a.company, a.filings, a.all_dims)
    print(render(data))
    if a.json:
        out = {"company": data["company"], "filings": data["filings"],
               "breakdowns": [{"concept": c, "axis": ax, "members": m} for (c, ax), m in data["tree"].items()]}
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(out, indent=2))
        print(f"\n(saved {a.json})", file=sys.stderr)


if __name__ == "__main__":
    main()
