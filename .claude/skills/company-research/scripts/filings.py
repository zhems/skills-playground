#!/usr/bin/env python3
"""List SEC filings, extract narrative sections of 10-K / 20-F filings to text,
and diff risk factors against the prior year. Stdlib only.

Usage:
    python3 filings.py list AAPL                          # recent 10-K/Q, 8-K, proxy, 20-F, 6-K with URLs
    python3 filings.py list AAPL --forms 8-K --limit 10
    python3 filings.py sections AAPL --out DIR            # latest annual: business, risks, MD&A, ... -> DIR/*.txt
    python3 filings.py sections AAPL --out DIR --quarterly  # latest 10-Q: MD&A, risk factor updates
    python3 filings.py risk-diff AAPL --out DIR           # new / removed risk-factor paragraphs vs prior year
    python3 filings.py exhibits AAPL --forms 8-K --limit 4  # 8-K exhibit list (99.1 = earnings press release)
    python3 filings.py text URL --out FILE                # any EDGAR HTML document -> plain text

Section splitting is heuristic (headings vary by filer); check the char counts
printed and fall back to grepping the full text (saved as *_full.txt).
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edgar_client import fetch_json, fetch_text, recent_filings, resolve_or_exit  # noqa: E402

TENK_ITEMS = [("1", "business"), ("1A", "risk_factors"), ("1B", "unresolved_staff_comments"), ("1C", "cybersecurity"),
              ("2", "properties"), ("3", "legal_proceedings"), ("4", "mine_safety"), ("5", "market_for_equity"),
              ("6", "reserved"), ("7", "mdna"), ("7A", "market_risk"), ("8", "financial_statements"),
              ("9", "accountant_changes"), ("9A", "controls"), ("9B", "other_information"), ("9C", "foreign_inspections"),
              ("10", "directors_officers"), ("11", "executive_comp"), ("12", "ownership"), ("13", "relationships"),
              ("14", "accountant_fees"), ("15", "exhibits")]
TENQ_ITEMS = [("2", "mdna"), ("3", "market_risk"), ("4", "controls"), ("1A", "risk_factors"), ("5", "other_information"),
              ("6", "exhibits")]
TWENTYF_ITEMS = [(str(i), n) for i, n in [(1, "identity"), (2, "offer_stats"), (3, "key_information_and_risk_factors"),
                                          (4, "information_on_company"), (5, "operating_financial_review"),
                                          (6, "directors_employees"), (7, "major_shareholders"), (8, "financial_information"),
                                          (9, "offer_listing"), (10, "additional_information"), (11, "market_risk"),
                                          (12, "other_securities"), (13, "defaults"), (14, "modifications"),
                                          (15, "controls"), (17, "financial_statements_17"), (18, "financial_statements"),
                                          (19, "exhibits")]] + [("4A", "unresolved_staff_comments"), ("12D", "adr_fees")] + \
    [(f"16{c}", f"item16{c.lower()}") for c in "ABCDEFGHIJK"]
# Fallback when 'Item' headings only appear in the table of contents (common in 20-Fs): plain headings.
HEADINGS = {
    "risk_factors": r"risk factors",
    "business": r"(business overview|information on the company|our business|business)",
    "mdna": r"(operating and financial review and prospects|management.s discussion and analysis[^\n]*|operating results)",
    "market_risk": r"quantitative and qualitative disclosures? about market risk",
    "major_shareholders": r"major shareholders",
    "legal_proceedings": r"legal proceedings",
}
KEEP = {"business", "risk_factors", "major_shareholders", "cybersecurity", "properties", "legal_proceedings", "mdna", "market_risk",
        "financial_statements", "controls", "key_information_and_risk_factors", "information_on_company",
        "operating_financial_review", "major_shareholders", "directors_employees", "financial_information",
        "other_information"}


def html_to_text(doc: str) -> str:
    doc = re.sub(r"(?is)<(script|style|ix:header)\b.*?</\1>", " ", doc)
    doc = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", doc)
    doc = re.sub(r"(?i)</t[dh]>", "\t", doc)
    doc = re.sub(r"<[^>]+>", "", doc)
    doc = html.unescape(doc).replace("\xa0", " ")
    doc = re.sub(r"[ \t]+", " ", doc)
    doc = re.sub(r"\n\s*\n+", "\n\n", doc)
    return doc.strip()


def split_items(text: str, items: list[tuple[str, str]]) -> dict[str, str]:
    """Locate 'Item X.' headings at line starts. Table-of-contents entries also match,
    so for each item choose the occurrence giving the longest section."""
    pat = re.compile(r"^\s*item\s*(\d{1,2}[A-K]?)\s*[.:\-—–]?\s", re.I | re.M)
    hits = [(m.start(), m.group(1).upper()) for m in pat.finditer(text)]
    valid = {code for code, _ in items}
    hits = [h for h in hits if h[1] in valid]
    out: dict[str, str] = {}
    for code, name in items:
        best = ""
        for i, (pos, c) in enumerate(hits):
            if c != code:
                continue
            nxt = next((p for p, c2 in hits[i + 1:] if c2 != code), len(text))
            chunk = text[pos:nxt].strip()
            if len(chunk) > len(best):
                best = chunk
        if best:
            out[name] = best
    return out


def split_headings(text: str) -> dict[str, str]:
    """Sections keyed by standalone heading lines; for each, keep the longest span to the next known heading."""
    hits = []
    for name, rx in HEADINGS.items():
        for m in re.finditer(rf"^[ \t]*{rx}[ \t]*$", text, re.I | re.M):
            hits.append((m.start(), name))
    hits.sort()
    out: dict[str, str] = {}
    for i, (pos, name) in enumerate(hits):
        nxt = next((p for p, n in hits[i + 1:] if n != name), len(text))
        chunk = text[pos:nxt].strip()
        if len(chunk) > len(out.get(name, "")) and len(chunk) < 0.6 * len(text):
            out[name] = chunk
    return out


def _annual(cik, n=2):
    return recent_filings(cik, {"10-K", "20-F", "40-F"})[:n]


def cmd_list(a):
    co = resolve_or_exit(a.company)
    forms = set(a.forms.split(",")) if a.forms else {"10-K", "10-Q", "8-K", "DEF 14A", "20-F", "6-K", "40-F", "S-1", "424B4"}
    rows = recent_filings(co["cik"], forms)[: a.limit]
    print(f"# {co['name']} ({co['ticker']}) filings — https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={co['cik']}")
    for f in rows:
        print(f"{f['filingDate']}  {f['form']:<8} period {f['reportDate'] or '-':<10}  {f['url']}")


def cmd_exhibits(a):
    co = resolve_or_exit(a.company)
    forms = set(a.forms.split(",")) if a.forms else {"8-K"}
    for f in recent_filings(co["cik"], forms)[: a.limit]:
        idx = fetch_json(f["folder"] + "index.json")
        docs = [d["name"] for d in idx["directory"]["item"] if d["name"].lower().endswith((".htm", ".html", ".txt", ".pdf"))]
        print(f"{f['filingDate']} {f['form']} {f['folder']}")
        for d in docs:
            tag = "  <- likely press release / earnings exhibit" if re.search(r"ex-?99|ex99|dex99|99[._-]?1\.htm|991\.htm|pressrelease|earnings", d, re.I) else ""
            print(f"    {f['folder']}{d}{tag}")


def _extract(filing: dict, quarterly: bool) -> tuple[str, dict[str, str]]:
    text = html_to_text(fetch_text(filing["url"]))
    form = filing["form"]
    items = TENQ_ITEMS if quarterly else (TWENTYF_ITEMS if form.startswith("20-F") else TENK_ITEMS)
    secs = split_items(text, items)
    if not quarterly:
        for name, body in split_headings(text).items():
            if len(secs.get(name, "")) < 3000 <= len(body):
                secs[name] = body
        # a 20-F's Item 3 holds the risk factors; use the heading split if Item 3 is a stub
        if form.startswith("20-F") and len(secs.get("key_information_and_risk_factors", "")) < 3000 and "risk_factors" in secs:
            secs["key_information_and_risk_factors"] = secs["risk_factors"]
    return text, secs


def cmd_sections(a):
    co = resolve_or_exit(a.company)
    if a.quarterly:
        filing = recent_filings(co["cik"], {"10-Q"})[0]
    else:
        filing = _annual(co["cik"], 1)[0]
    text, secs = _extract(filing, a.quarterly)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{co['ticker']}_{filing['form'].replace('/', '')}_{filing['reportDate']}"
    (out / f"{stem}_full.txt").write_text(text)
    print(f"# {filing['form']} period {filing['reportDate']} filed {filing['filingDate']}\n# {filing['url']}")
    print(f"full text: {out / (stem + '_full.txt')} ({len(text):,} chars)")
    for name, body in secs.items():
        if name not in KEEP:
            continue
        p = out / f"{stem}_{name}.txt"
        p.write_text(body)
        warn = "  (short: heading may be a cross-reference; grep the full text)" if len(body) < 3000 else ""
        print(f"{name:<35} {len(body):>9,} chars  {p}{warn}")
    if not secs:
        print("No 'Item' headings detected — use the full text file.")


def _paragraphs(s: str) -> list[str]:
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z][^\n]{20,200}\n)", s)]
    return [p for p in paras if len(p) > 60 and not re.fullmatch(r"[\d\s|.\-]+", p)]


def cmd_risk_diff(a):
    co = resolve_or_exit(a.company)
    filings = _annual(co["cik"], 2)
    if len(filings) < 2:
        raise SystemExit("Need two annual filings to diff.")
    key = "risk_factors"
    new_secs = _extract(filings[0], False)[1]
    old_secs = _extract(filings[1], False)[1]
    new, old = new_secs.get(key, ""), old_secs.get(key, "")
    if not new or not old:
        raise SystemExit("Could not locate risk factor sections in both filings; diff the *_full.txt files manually.")
    np_, op = _paragraphs(new), _paragraphs(old)
    def shingles(p):
        w = re.findall(r"[a-z0-9]+", p.lower())
        return {" ".join(w[i:i + 3]) for i in range(max(1, len(w) - 2))}
    ns, os_ = [shingles(p) for p in np_], [shingles(p) for p in op]

    def best(x, pool):  # max Jaccard similarity of word 3-grams
        return max((len(x & y) / len(x | y) for y in pool if x and y), default=0.0)

    # most novel first: scores near 0 are genuinely new/removed risks; 0.3-0.5 are mostly rewording
    added = sorted([(p, sc) for p, x in zip(np_, ns) if (sc := best(x, os_)) < a.threshold], key=lambda t: t[1])
    removed = sorted([(p, sc) for p, x in zip(op, os_) if (sc := best(x, ns)) < a.threshold], key=lambda t: t[1])
    lines = [f"# Risk-factor changes: {co['name']} ({co['ticker']})",
             f"New: {filings[0]['form']} period {filings[0]['reportDate']} ({filings[0]['url']})",
             f"Old: {filings[1]['form']} period {filings[1]['reportDate']} ({filings[1]['url']})",
             f"Section length: {len(old):,} -> {len(new):,} chars; paragraphs {len(op)} -> {len(np_)}",
             f"Jaccard (word 3-gram) threshold {a.threshold}; sorted most novel first. Scores < 0.15 are new or dropped risks; 0.3–0.5 are usually rewording.", "",
             f"## Added or substantially rewritten ({len(added)})"]
    lines += [f"- [{s:.2f}] {p[:a.width]}{'…' if len(p) > a.width else ''}" for p, s in added]
    lines += ["", f"## Removed or substantially rewritten ({len(removed)})"]
    lines += [f"- [{s:.2f}] {p[:a.width]}{'…' if len(p) > a.width else ''}" for p, s in removed]
    report = "\n".join(lines)
    print(report)
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / f"{co['ticker']}_risk_diff.md").write_text(report)


def cmd_text(a):
    text = html_to_text(fetch_text(a.url))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text)
        print(f"{len(text):,} chars -> {a.out}")
    else:
        print(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("list"); p.add_argument("company"); p.add_argument("--forms"); p.add_argument("--limit", type=int, default=25)
    p.set_defaults(fn=cmd_list)
    p = sp.add_parser("exhibits"); p.add_argument("company"); p.add_argument("--forms"); p.add_argument("--limit", type=int, default=4)
    p.set_defaults(fn=cmd_exhibits)
    p = sp.add_parser("sections"); p.add_argument("company"); p.add_argument("--out", required=True)
    p.add_argument("--quarterly", action="store_true"); p.set_defaults(fn=cmd_sections)
    p = sp.add_parser("risk-diff"); p.add_argument("company"); p.add_argument("--out")
    p.add_argument("--threshold", type=float, default=0.5); p.add_argument("--width", type=int, default=400)
    p.set_defaults(fn=cmd_risk_diff)
    p = sp.add_parser("text"); p.add_argument("url"); p.add_argument("--out"); p.set_defaults(fn=cmd_text)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
