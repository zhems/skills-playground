#!/usr/bin/env python3
"""Multi-year fundamentals from SEC XBRL (companyfacts API), with derived ratios,
quality scores and rule-based flags. Stdlib only.

Usage:
    python3 fundamentals.py AAPL                     # markdown report to stdout
    python3 fundamentals.py AAPL --json out.json     # also save full data
    python3 fundamentals.py MSFT GOOGL AMZN --peers  # side-by-side peer table
    python3 fundamentals.py NVDA --years 5

Data are as-reported in 10-K/20-F/40-F XBRL. Where a later filing restates a
period, the latest filing wins. Values are in the filer's reporting currency.
Spot-check headline numbers against the filing before relying on them.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edgar_client import cik10, fetch_json, resolve_or_exit, submissions  # noqa: E402

# metric -> (kind, [concepts in priority order]); kind: dur=flow, inst=balance, ps=per share, sh=shares
CONCEPTS: dict[str, tuple[str, list[str]]] = {
    "revenue": ("dur", ["us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax", "us-gaap:SalesRevenueNet",
                        "us-gaap:SalesRevenueGoodsNet", "us-gaap:RevenuesNetOfInterestExpense", "ifrs-full:Revenue"]),
    "cost_of_revenue": ("dur", ["us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsAndServicesSold",
                                "us-gaap:CostOfGoodsSold", "ifrs-full:CostOfSales"]),
    "gross_profit": ("dur", ["us-gaap:GrossProfit", "ifrs-full:GrossProfit"]),
    "rnd": ("dur", ["us-gaap:ResearchAndDevelopmentExpense",
                    "us-gaap:ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
                    "ifrs-full:ResearchAndDevelopmentExpense"]),
    "sga": ("dur", ["us-gaap:SellingGeneralAndAdministrativeExpense", "ifrs-full:SellingGeneralAndAdministrativeExpense"]),
    "operating_income": ("dur", ["us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"]),
    "interest_expense": ("dur", ["us-gaap:InterestExpense", "us-gaap:InterestExpenseNonoperating",
                                 "us-gaap:InterestExpenseDebt", "us-gaap:InterestAndDebtExpense", "ifrs-full:FinanceCosts"]),
    "pretax_income": ("dur", ["us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                              "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
                              "ifrs-full:ProfitLossBeforeTax"]),
    "income_tax": ("dur", ["us-gaap:IncomeTaxExpenseBenefit", "ifrs-full:IncomeTaxExpenseContinuingOperations"]),
    "net_income": ("dur", ["us-gaap:NetIncomeLoss", "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic",
                           "us-gaap:ProfitLoss", "ifrs-full:ProfitLossAttributableToOwnersOfParent", "ifrs-full:ProfitLoss"]),
    "eps_diluted": ("ps", ["us-gaap:EarningsPerShareDiluted", "us-gaap:EarningsPerShareBasicAndDiluted",
                           "ifrs-full:DilutedEarningsLossPerShare"]),
    "shares_diluted": ("sh", ["us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",
                              "ifrs-full:AdjustedWeightedAverageShares", "ifrs-full:WeightedAverageShares"]),
    "da": ("dur", ["us-gaap:DepreciationDepletionAndAmortization", "us-gaap:DepreciationAmortizationAndAccretionNet",
                   "us-gaap:DepreciationAndAmortization", "us-gaap:Depreciation",
                   "ifrs-full:DepreciationAndAmortisationExpense", "ifrs-full:DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"]),
    "sbc": ("dur", ["us-gaap:ShareBasedCompensation", "us-gaap:AllocatedShareBasedCompensationExpense",
                    "ifrs-full:AdjustmentsForSharebasedPayments"]),
    "ocf": ("dur", ["us-gaap:NetCashProvidedByUsedInOperatingActivities",
                    "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
                    "ifrs-full:CashFlowsFromUsedInOperatingActivities"]),
    "capex": ("dur", ["us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", "us-gaap:PaymentsToAcquireProductiveAssets",
                      "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"]),
    "acquisitions": ("dur", ["us-gaap:PaymentsToAcquireBusinessesNetOfCashAcquired",
                             "ifrs-full:CashFlowsUsedInObtainingControlOfSubsidiariesOrOtherBusinessesClassifiedAsInvestingActivities"]),
    "buybacks": ("dur", ["us-gaap:PaymentsForRepurchaseOfCommonStock", "ifrs-full:PaymentsToAcquireOrRedeemEntitysShares"]),
    "dividends": ("dur", ["us-gaap:PaymentsOfDividends", "us-gaap:PaymentsOfDividendsCommonStock",
                          "ifrs-full:DividendsPaidClassifiedAsFinancingActivities"]),
    "cash": ("inst", ["us-gaap:CashAndCashEquivalentsAtCarryingValue",
                      "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "ifrs-full:CashAndCashEquivalents"]),
    "st_investments": ("inst", ["us-gaap:ShortTermInvestments", "us-gaap:MarketableSecuritiesCurrent",
                                "us-gaap:AvailableForSaleSecuritiesDebtSecuritiesCurrent", "ifrs-full:CurrentFinancialAssets"]),
    "receivables": ("inst", ["us-gaap:AccountsReceivableNetCurrent", "us-gaap:ReceivablesNetCurrent",
                             "ifrs-full:TradeAndOtherCurrentReceivables", "ifrs-full:CurrentTradeReceivables"]),
    "inventory": ("inst", ["us-gaap:InventoryNet", "ifrs-full:Inventories"]),
    "current_assets": ("inst", ["us-gaap:AssetsCurrent", "ifrs-full:CurrentAssets"]),
    "total_assets": ("inst", ["us-gaap:Assets", "ifrs-full:Assets"]),
    "goodwill": ("inst", ["us-gaap:Goodwill", "ifrs-full:Goodwill"]),
    "intangibles": ("inst", ["us-gaap:IntangibleAssetsNetExcludingGoodwill", "us-gaap:FiniteLivedIntangibleAssetsNet",
                             "ifrs-full:IntangibleAssetsOtherThanGoodwill"]),
    "ppe": ("inst", ["us-gaap:PropertyPlantAndEquipmentNet", "ifrs-full:PropertyPlantAndEquipment"]),
    "accounts_payable": ("inst", ["us-gaap:AccountsPayableCurrent", "ifrs-full:TradeAndOtherCurrentPayables",
                                  "ifrs-full:CurrentTradePayables"]),
    "current_liabilities": ("inst", ["us-gaap:LiabilitiesCurrent", "ifrs-full:CurrentLiabilities"]),
    "total_liabilities": ("inst", ["us-gaap:Liabilities", "ifrs-full:Liabilities"]),
    "lt_debt_total": ("inst", ["us-gaap:LongTermDebt", "us-gaap:DebtLongtermAndShorttermCombinedAmount",
                               "us-gaap:LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"]),
    "lt_debt_noncurrent": ("inst", ["us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligations",
                                    "ifrs-full:NoncurrentPortionOfNoncurrentBorrowings", "ifrs-full:NoncurrentBorrowings"]),
    "lt_debt_current": ("inst", ["us-gaap:LongTermDebtCurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent",
                                 "ifrs-full:CurrentPortionOfNoncurrentBorrowings", "ifrs-full:CurrentBorrowings"]),
    "st_borrowings": ("inst", ["us-gaap:ShortTermBorrowings", "us-gaap:CommercialPaper", "us-gaap:DebtCurrent"]),
    "operating_lease_liab": ("inst", ["us-gaap:OperatingLeaseLiability", "ifrs-full:LeaseLiabilities"]),
    "equity": ("inst", ["us-gaap:StockholdersEquity",
                        "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                        "ifrs-full:EquityAttributableToOwnersOfParent", "ifrs-full:Equity"]),
    "retained_earnings": ("inst", ["us-gaap:RetainedEarningsAccumulatedDeficit", "ifrs-full:RetainedEarnings"]),
    "deferred_revenue": ("inst", ["us-gaap:ContractWithCustomerLiabilityCurrent", "us-gaap:DeferredRevenueCurrent"]),
    "rpo": ("inst", ["us-gaap:RevenueRemainingPerformanceObligation"]),
    "shares_outstanding": ("sh_inst", ["dei:EntityCommonStockSharesOutstanding"]),
}
ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT", "20-F", "20-F/A", "40-F", "40-F/A"}
# Only periodic reports: proxies (DEF 14A pay-vs-performance), 8-Ks and S-1s also carry XBRL and must not win.
PERIODIC_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A", "10-QT"}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _days(s: str, e: str) -> int:
    return (_d(e) - _d(s)).days


# ------------------------------------------------------------------ extraction

def _reporting_currency(facts: dict) -> str:
    counts: dict[str, int] = defaultdict(int)
    for tax in ("us-gaap", "ifrs-full"):
        for c in ("Revenues", "Revenue", "Assets", "NetIncomeLoss", "ProfitLoss",
                  "RevenueFromContractWithCustomerExcludingAssessedTax"):
            for unit, rows in facts.get(tax, {}).get(c, {}).get("units", {}).items():
                if len(unit) == 3 and unit.isupper():
                    counts[unit] += len(rows)
    return max(counts, key=counts.get) if counts else "USD"


def _rows(facts: dict, concept: str, kind: str, ccy: str) -> list[dict]:
    tax, name = concept.split(":")
    units = facts.get(tax, {}).get(name, {}).get("units", {})
    if kind in ("sh", "sh_inst"):
        rows = units.get("shares", [])
    elif kind == "ps":
        rows = units.get(f"{ccy}/shares", [])
    else:
        rows = units.get(ccy, [])
    return [r for r in rows if r.get("form") in PERIODIC_FORMS]


def _latest(rows: list[dict]) -> dict[tuple, dict]:
    """Key rows by (start, end); keep the most recently filed value."""
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r.get("start"), r["end"])
        if k not in best or r["filed"] > best[k]["filed"]:
            best[k] = r
    return best


def extract(facts: dict) -> dict:
    """Return {ccy, annual: {metric: {fye: val}}, fy_periods, quarterly: {metric: {qend: val}}, latest_instant}."""
    ccy = _reporting_currency(facts)
    annual: dict[str, dict[str, float]] = {}
    quarterly: dict[str, dict[str, float]] = {}
    latest_instant: dict[str, tuple[str, float]] = {}
    fy_periods: dict[str, str] = {}  # fye -> fy start

    # pass 1: flows (needed to establish fiscal-year periods)
    for metric, (kind, concepts) in CONCEPTS.items():
        if kind not in ("dur", "ps", "sh"):
            continue
        ann: dict[str, float] = {}
        qtr: dict[str, float] = {}
        for concept in concepts:
            rows = [r for r in _rows(facts, concept, kind, ccy) if r.get("start")]
            if not rows:
                continue
            by_period = _latest(rows)
            # annual: ~1 year duration reported in an annual form
            for (s, e), r in by_period.items():
                if 350 <= _days(s, e) <= 380 and r.get("form") in ANNUAL_FORMS and e not in ann:
                    ann[e] = r["val"]
                    if metric in ("revenue", "net_income", "ocf"):
                        fy_periods.setdefault(e, s)
            # quarterly: direct 3-month values, then differences of cumulative YTD values
            durations = {k: r["val"] for k, r in by_period.items()}
            q_here = {e: (s, v) for (s, e), v in durations.items() if 80 <= _days(s, e) <= 100}
            if kind != "ps":  # per-share and shares are not additive
                by_start: dict[str, list] = defaultdict(list)
                for (s, e), v in durations.items():
                    if _days(s, e) > 100:
                        by_start[s].append((e, v))
                for s, items in by_start.items():
                    first = [(e, v) for e, (qs, v) in q_here.items() if qs == s]
                    if not first:
                        continue
                    chain = sorted(first[:1] + items)
                    for (pe, pv), (e, v) in zip(chain, chain[1:]):
                        if e not in q_here and 80 <= _days(pe, e) <= 100:
                            q_here[e] = ((_d(pe) + timedelta(days=1)).isoformat(), v - pv)
            for e, (_, v) in q_here.items():
                qtr.setdefault(e, v)
        if ann:
            annual[metric] = ann
        if qtr:
            quarterly[metric] = qtr

    fyes = sorted(fy_periods)
    # pass 2: balance-sheet instants aligned to fiscal year ends (+-7 days)
    for metric, (kind, concepts) in CONCEPTS.items():
        if kind not in ("inst", "sh_inst"):
            continue
        ann = {}
        for concept in concepts:
            rows = [r for r in _rows(facts, concept, kind, ccy) if not r.get("start")]
            if not rows:
                continue
            by_end = {k[1]: r for k, r in _latest(rows).items()}
            for fye in fyes:
                if fye in ann:
                    continue
                for e, r in by_end.items():
                    if abs(_days(fye, e)) <= 7 or (kind == "sh_inst" and 0 <= _days(fye, e) <= 120):
                        ann[fye] = r["val"]
                        break
            if by_end and metric not in latest_instant:
                e = max(by_end)
                latest_instant[metric] = (e, by_end[e]["val"])
        if ann:
            annual[metric] = ann
    splits = adjust_splits(annual, quarterly)
    return {"currency": ccy, "annual": annual, "fy_periods": fy_periods, "quarterly": quarterly,
            "latest_instant": latest_instant, "splits": splits}


def adjust_splits(annual: dict, quarterly: dict) -> list[str]:
    """Older filings report pre-split share counts. Detect jumps in diluted shares that look like
    splits (ratio near a whole number or 1/n) and restate earlier periods to the latest share basis."""
    notes = []
    sh = annual.get("shares_diluted", {})
    ends = sorted(sh)
    for prev, cur in zip(ends, ends[1:]):
        if not sh.get(prev):
            continue
        r = sh[cur] / sh[prev]
        factor = None
        if r >= 1.8 and abs(r - round(r)) / round(r) < 0.08:
            factor = float(round(r))
        elif r <= 0.55 and abs(1 / r - round(1 / r)) / round(1 / r) < 0.08:
            factor = 1 / round(1 / r)
        if not factor:
            continue
        for series, op in ((annual.get("shares_diluted", {}), "mul"), (annual.get("shares_outstanding", {}), "mul"),
                           (annual.get("eps_diluted", {}), "div"), (quarterly.get("shares_diluted", {}), "mul"),
                           (quarterly.get("eps_diluted", {}), "div")):
            for e in [e for e in series if e <= prev]:
                series[e] = series[e] * factor if op == "mul" else series[e] / factor
        notes.append(f"Detected ~{factor:g}:1 split between FYE {prev} and {cur}; earlier share counts/EPS restated.")
    return notes


# --------------------------------------------------------------- derivations

def _div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def _sub(a, b):
    return None if a is None or b is None else a - b


def _growth(cur, prev):
    if cur is None or prev in (None, 0) or prev < 0:
        return None
    return cur / prev - 1


def _cagr(end, start, years):
    if end is None or start is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def total_debt(row: dict) -> float | None:
    lt = row.get("lt_debt_total")
    if lt is None and (row.get("lt_debt_noncurrent") is not None or row.get("lt_debt_current") is not None):
        lt = (row.get("lt_debt_noncurrent") or 0) + (row.get("lt_debt_current") or 0)
    st = row.get("st_borrowings")
    if lt is None and st is None:
        return None
    return (lt or 0) + (st or 0)


def build_years(ex: dict, n_years: int) -> list[dict]:
    fyes = sorted(ex["fy_periods"])[-(n_years + 1):]  # one extra for YoY / averages
    years = []
    for fye in fyes:
        row = {"fye": fye}
        for metric, series in ex["annual"].items():
            row[metric] = series.get(fye)
        years.append(row)

    for i, r in enumerate(years):
        p = years[i - 1] if i > 0 else {}
        rev = r.get("revenue")
        if r.get("total_liabilities") is None and r.get("total_assets") is not None and r.get("equity") is not None:
            r["total_liabilities"] = r["total_assets"] - r["equity"]  # many filers don't tag total liabilities
        if r.get("gross_profit") is None and rev is not None and r.get("cost_of_revenue") is not None:
            r["gross_profit"] = rev - r["cost_of_revenue"]
        r["fcf"] = _sub(r.get("ocf"), r.get("capex"))
        r["fcf_after_sbc"] = _sub(r["fcf"], r.get("sbc")) if r.get("sbc") is not None else None
        r["ebitda"] = (r["operating_income"] + r["da"]) if r.get("operating_income") is not None and r.get("da") is not None else None
        r["total_debt"] = total_debt(r)
        liquid = (r.get("cash") or 0) + (r.get("st_investments") or 0) if r.get("cash") is not None else None
        r["net_debt"] = _sub(r["total_debt"], liquid) if r["total_debt"] is not None else (-liquid if liquid is not None else None)

        r["rev_growth"] = _growth(rev, p.get("revenue"))
        r["eps_growth"] = _growth(r.get("eps_diluted"), p.get("eps_diluted"))
        r["fcf_growth"] = _growth(r["fcf"], p.get("fcf"))
        r["gross_margin"] = _div(r.get("gross_profit"), rev)
        r["operating_margin"] = _div(r.get("operating_income"), rev)
        r["net_margin"] = _div(r.get("net_income"), rev)
        r["fcf_margin"] = _div(r["fcf"], rev)
        r["rnd_pct_rev"] = _div(r.get("rnd"), rev)
        r["sga_pct_rev"] = _div(r.get("sga"), rev)
        r["sbc_pct_rev"] = _div(r.get("sbc"), rev)
        r["capex_pct_rev"] = _div(r.get("capex"), rev)
        r["capex_to_da"] = _div(r.get("capex"), r.get("da"))
        r["tax_rate"] = _div(r.get("income_tax"), r.get("pretax_income"))
        r["cash_conversion"] = _div(r.get("ocf"), r.get("net_income")) if (r.get("net_income") or 0) > 0 else None
        r["net_debt_to_ebitda"] = _div(r["net_debt"], r["ebitda"]) if (r["ebitda"] or 0) > 0 else None
        r["interest_coverage"] = _div(r.get("operating_income"), r.get("interest_expense"))
        r["current_ratio"] = _div(r.get("current_assets"), r.get("current_liabilities"))
        r["debt_to_equity"] = _div(r["total_debt"], r.get("equity")) if (r.get("equity") or 0) > 0 else None
        r["goodwill_intang_pct_assets"] = _div((r.get("goodwill") or 0) + (r.get("intangibles") or 0), r.get("total_assets"))
        r["share_change"] = _growth(r.get("shares_diluted"), p.get("shares_diluted"))
        r["shareholder_returns"] = (r.get("buybacks") or 0) + (r.get("dividends") or 0)
        r["payout_of_fcf"] = _div(r["shareholder_returns"], r["fcf"]) if (r["fcf"] or 0) > 0 else None
        cogs = r.get("cost_of_revenue") or (_sub(rev, r.get("gross_profit")))
        r["dso"] = _div(r.get("receivables"), rev) * 365 if _div(r.get("receivables"), rev) is not None else None
        r["dio"] = _div(r.get("inventory"), cogs) * 365 if _div(r.get("inventory"), cogs) is not None else None
        r["dpo"] = _div(r.get("accounts_payable"), cogs) * 365 if _div(r.get("accounts_payable"), cogs) is not None else None
        r["ccc"] = (r["dso"] or 0) + (r["dio"] or 0) - (r["dpo"] or 0) if r["dso"] is not None and r["dpo"] is not None else None

        def avg(k):
            a, b = r.get(k), p.get(k)
            return (a + b) / 2 if a is not None and b is not None else a
        r["roe"] = _div(r.get("net_income"), avg("equity")) if (avg("equity") or 0) > 0 else None
        r["roa"] = _div(r.get("net_income"), avg("total_assets"))
        t = r["tax_rate"] if r["tax_rate"] is not None and 0 <= r["tax_rate"] <= 0.35 else 0.21
        nopat = r["operating_income"] * (1 - t) if r.get("operating_income") is not None else None
        ic_now = None
        if r.get("equity") is not None:
            ic_now = r["equity"] + (r["total_debt"] or 0) - (r.get("cash") or 0) - (r.get("st_investments") or 0)
        ic_prev = None
        if p.get("equity") is not None:
            ic_prev = p["equity"] + (p.get("total_debt") or 0) - (p.get("cash") or 0) - (p.get("st_investments") or 0)
        ic = (ic_now + ic_prev) / 2 if ic_now is not None and ic_prev is not None else ic_now
        r["invested_capital"] = ic_now
        r["roic"] = _div(nopat, ic) if ic and ic > 0 else None
        gw = lambda row: (row.get("goodwill") or 0) + (row.get("intangibles") or 0)  # noqa: E731
        tic_now = ic_now - gw(r) if ic_now is not None else None
        tic_prev = ic_prev - gw(p) if ic_prev is not None else None
        tic = (tic_now + tic_prev) / 2 if tic_now is not None and tic_prev is not None else tic_now
        r["roic_tangible"] = _div(nopat, tic) if tic and tic > 0 else None
        r["accruals_ratio"] = _div(_sub(r.get("net_income"), r.get("ocf")), avg("total_assets"))
    return years[1:] if len(years) > n_years else years


def ttm(ex: dict) -> dict:
    """Trailing-twelve-month flows from the last four contiguous discrete quarters."""
    out: dict = {}
    for metric in ("revenue", "gross_profit", "cost_of_revenue", "operating_income", "net_income", "ocf", "capex", "sbc",
                   "rnd", "da", "buybacks", "dividends", "interest_expense"):
        q = ex["quarterly"].get(metric, {})
        ends = sorted(q)[-4:]
        if len(ends) == 4 and 250 <= _days(ends[0], ends[-1]) <= 300:  # 4 contiguous quarter-ends
            out[metric] = sum(q[e] for e in ends)
            out.setdefault("period_end", ends[-1])
    if out.get("revenue") and out.get("gross_profit") is None and out.get("cost_of_revenue") is not None:
        out["gross_profit"] = out["revenue"] - out["cost_of_revenue"]
    if out.get("ocf") is not None and out.get("capex") is not None:
        out["fcf"] = out["ocf"] - out["capex"]
    for k, d in (("gross_margin", "gross_profit"), ("operating_margin", "operating_income"),
                 ("net_margin", "net_income"), ("fcf_margin", "fcf")):
        out[k] = _div(out.get(d), out.get("revenue"))
    return out


def recent_quarters(ex: dict, n: int = 8) -> list[dict]:
    rev = ex["quarterly"].get("revenue", {})
    ends = sorted(rev)[-n:]
    rows = []
    for e in ends:
        r = {"qend": e}
        for m in ("revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "ocf", "capex"):
            r[m] = ex["quarterly"].get(m, {}).get(e)
        if r["gross_profit"] is None and r["revenue"] is not None:
            cogs = ex["quarterly"].get("cost_of_revenue", {}).get(e)
            r["gross_profit"] = r["revenue"] - cogs if cogs is not None else None
        r["gross_margin"] = _div(r["gross_profit"], r["revenue"])
        r["operating_margin"] = _div(r["operating_income"], r["revenue"])
        prior = [k for k in rev if 350 <= _days(k, e) <= 380]
        r["rev_yoy"] = _growth(r["revenue"], rev[prior[0]]) if prior else None
        rows.append(r)
    return rows


def growth_summary(years: list[dict], ex: dict) -> dict:
    out = {}
    allfy = sorted(ex["fy_periods"])
    for metric in ("revenue", "eps_diluted", "fcf", "operating_income", "shares_diluted"):
        series = {}
        if metric == "fcf":
            for fye in allfy:
                o = ex["annual"].get("ocf", {}).get(fye)
                c = ex["annual"].get("capex", {}).get(fye)
                if o is not None and c is not None:
                    series[fye] = o - c
        else:
            series = ex["annual"].get(metric, {})
        for n in (3, 5, 10):
            if len(allfy) > n:
                out[f"{metric}_cagr_{n}y"] = _cagr(series.get(allfy[-1]), series.get(allfy[-1 - n]), n)
    return out


# ------------------------------------------------------------------- scores

def piotroski(cur: dict, prev: dict) -> tuple[int | None, list[str]]:
    tests = []
    def t(name, cond):
        if cond is not None:
            tests.append((name, bool(cond)))
    roa_c, roa_p = _div(cur.get("net_income"), cur.get("total_assets")), _div(prev.get("net_income"), prev.get("total_assets"))
    t("ROA > 0", roa_c > 0 if roa_c is not None else None)
    t("OCF > 0", cur["ocf"] > 0 if cur.get("ocf") is not None else None)
    t("ROA improved", roa_c > roa_p if roa_c is not None and roa_p is not None else None)
    t("OCF > net income", cur["ocf"] > cur["net_income"] if cur.get("ocf") is not None and cur.get("net_income") is not None else None)
    lev_c, lev_p = _div(cur.get("total_debt"), cur.get("total_assets")), _div(prev.get("total_debt"), prev.get("total_assets"))
    t("Leverage fell", lev_c <= lev_p if lev_c is not None and lev_p is not None else None)
    t("Current ratio up", cur["current_ratio"] > prev["current_ratio"] if cur.get("current_ratio") and prev.get("current_ratio") else None)
    t("No dilution", cur["shares_diluted"] <= prev["shares_diluted"] * 1.005 if cur.get("shares_diluted") and prev.get("shares_diluted") else None)
    t("Gross margin up", cur["gross_margin"] > prev["gross_margin"] if cur.get("gross_margin") is not None and prev.get("gross_margin") is not None else None)
    at_c, at_p = _div(cur.get("revenue"), cur.get("total_assets")), _div(prev.get("revenue"), prev.get("total_assets"))
    t("Asset turnover up", at_c > at_p if at_c is not None and at_p is not None else None)
    if len(tests) < 7:
        return None, [f"only {len(tests)}/9 tests computable"]
    return sum(ok for _, ok in tests), [f"{'✓' if ok else '✗'} {n}" for n, ok in tests]


def altman_z2(r: dict) -> float | None:
    """Altman Z'' (non-manufacturing / book-equity variant). >2.6 safe, 1.1-2.6 grey, <1.1 distress."""
    ta = r.get("total_assets")
    need = [ta, r.get("current_assets"), r.get("current_liabilities"), r.get("retained_earnings"),
            r.get("operating_income"), r.get("equity"), r.get("total_liabilities")]
    if any(v is None for v in need) or not ta or not r["total_liabilities"]:
        return None
    x1 = (r["current_assets"] - r["current_liabilities"]) / ta
    x2 = r["retained_earnings"] / ta
    x3 = r["operating_income"] / ta
    x4 = r["equity"] / r["total_liabilities"]
    return 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4


# -------------------------------------------------------------------- flags

def flags(years: list[dict], growth: dict, is_financial: bool) -> tuple[list[str], list[str]]:
    """Rule-based observations. Returns (risks, strengths). Each is a prompt for investigation, not a verdict."""
    risks, strengths = [], []
    if len(years) < 2:
        return risks, strengths
    c, p = years[-1], years[-2]
    last3 = years[-3:]

    def pct(x):
        return f"{x * 100:.1f}%"

    if c.get("rev_growth") is not None and c["rev_growth"] < 0:
        risks.append(f"Revenue declined {pct(-c['rev_growth'])} YoY (FYE {c['fye']}).")
    for key, label in (("gross_margin", "Gross"), ("operating_margin", "Operating")):
        if c.get(key) is not None and p.get(key) is not None:
            d = c[key] - p[key]
            if d <= -0.02:
                risks.append(f"{label} margin compressed {d * 100:+.1f}pp YoY to {pct(c[key])}.")
            elif d >= 0.02:
                strengths.append(f"{label} margin expanded {d * 100:+.1f}pp YoY to {pct(c[key])}.")
    if not is_financial:
        for key, label in (("dso", "receivables (DSO)"), ("dio", "inventory (DIO)")):
            if c.get(key) and p.get(key) and c[key] > p[key] * 1.15 and c[key] - p[key] > 5:
                risks.append(f"Days {label} rose from {p[key]:.0f} to {c[key]:.0f}: working capital growing faster than sales "
                             "(possible channel stuffing, weak demand or collection issues).")
        if len(years) >= 4:
            b = years[-4]
            for key, label in (("dso", "receivables (DSO)"), ("dio", "inventory (DIO)")):
                if c.get(key) and b.get(key) and c[key] > b[key] * 1.3 and c[key] - b[key] > 15 \
                        and not (p.get(key) and c[key] > p[key] * 1.15):
                    risks.append(f"Days {label} up from {b[key]:.0f} to {c[key]:.0f} over 3 years (FYE {b['fye']}→{c['fye']}): "
                                 "slow build that YoY checks miss. Check whether it is strategic (new products, supply security) or weak sell-through.")
        cc = [y["cash_conversion"] for y in last3 if y.get("cash_conversion") is not None]
        if len(cc) >= 2 and sum(cc) / len(cc) < 0.8:
            risks.append(f"Avg OCF/net income over last {len(cc)} years is {sum(cc) / len(cc):.2f} (<0.8): earnings not backed by cash.")
        if c.get("accruals_ratio") is not None and c["accruals_ratio"] > 0.10:
            risks.append(f"Accruals ratio {pct(c['accruals_ratio'])} of assets (>10%): low earnings quality signal.")
        if c.get("fcf") is not None and c["fcf"] < 0:
            risks.append("Free cash flow negative in latest fiscal year.")
        if c.get("fcf_margin") is not None and c["fcf_margin"] > 0.15:
            strengths.append(f"FCF margin {pct(c['fcf_margin'])}.")
        if c.get("net_debt_to_ebitda") is not None and c["net_debt_to_ebitda"] > 3:
            risks.append(f"Net debt/EBITDA {c['net_debt_to_ebitda']:.1f}x (>3x).")
        if c.get("net_debt") is not None and c["net_debt"] < 0:
            strengths.append("Net cash balance sheet (cash + ST investments exceed debt).")
        if c.get("interest_coverage") is not None and 0 < c["interest_coverage"] < 4:
            risks.append(f"Interest coverage (EBIT/interest) only {c['interest_coverage']:.1f}x.")
        if c.get("current_ratio") is not None and c["current_ratio"] < 1:
            risks.append(f"Current ratio {c['current_ratio']:.2f} (<1): relies on operating cash or refinancing for near-term obligations.")
        z = altman_z2(c)
        if z is not None and z < 1.1:
            risks.append(f"Altman Z'' = {z:.2f} (distress zone <1.1).")
    if c.get("sbc_pct_rev") is not None and c["sbc_pct_rev"] > 0.10:
        risks.append(f"Stock-based comp is {pct(c['sbc_pct_rev'])} of revenue: real cost to shareholders not in FCF.")
    if c.get("share_change") is not None:
        if c["share_change"] > 0.02:
            risks.append(f"Diluted share count up {pct(c['share_change'])} YoY (dilution).")
        elif c["share_change"] < -0.01:
            strengths.append(f"Diluted share count down {pct(-c['share_change'])} YoY (net buybacks).")
    if c.get("goodwill_intang_pct_assets") is not None and c["goodwill_intang_pct_assets"] > 0.4:
        risks.append(f"Goodwill + intangibles are {pct(c['goodwill_intang_pct_assets'])} of assets: acquisition-heavy, impairment risk.")
    if c.get("equity") is not None and c["equity"] < 0:
        risks.append("Negative shareholders' equity (check whether from buybacks — benign — or accumulated losses).")
    if c.get("payout_of_fcf") is not None and c["payout_of_fcf"] > 1.1:
        risks.append(f"Buybacks + dividends = {c['payout_of_fcf']:.0%} of FCF: shareholder returns partly funded by balance sheet.")
    if c.get("roic") is not None:
        if c["roic"] > 0.15:
            strengths.append(f"ROIC {pct(c['roic'])} (>15%): suggests durable competitive advantage if sustained.")
        elif c["roic"] < 0.06:
            rt = c.get("roic_tangible")
            if rt is not None and rt > 0.15:
                risks.append(f"ROIC {pct(c['roic'])} is below cost of capital on total capital, but {pct(rt)} excluding goodwill "
                             "and intangibles: the operating business earns well; the acquisition price is what dilutes returns.")
            else:
                risks.append(f"ROIC {pct(c['roic'])} likely below cost of capital: growth may destroy value.")
    rc = growth.get("revenue_cagr_5y") or growth.get("revenue_cagr_3y")
    if rc is not None and rc > 0.15:
        strengths.append(f"Revenue compounding at {pct(rc)} per year.")
    if c.get("capex_to_da") is not None and c["capex_to_da"] < 0.7 and not is_financial:
        risks.append(f"Capex is {c['capex_to_da']:.2f}x D&A: possible under-investment flattering FCF.")
    if c.get("rev_growth") is not None and c.get("eps_growth") is not None and c["rev_growth"] > 0 and c["eps_growth"] > c["rev_growth"] + 0.05:
        strengths.append("EPS growing faster than revenue: operating leverage and/or buybacks.")
    return risks, strengths


# ------------------------------------------------------------------- output

ROWS = [  # (key, label, fmt)
    ("revenue", "Revenue", "money"), ("rev_growth", "  YoY growth", "pct"),
    ("gross_profit", "Gross profit", "money"), ("gross_margin", "  Gross margin", "pct"),
    ("operating_income", "Operating income", "money"), ("operating_margin", "  Operating margin", "pct"),
    ("net_income", "Net income", "money"), ("net_margin", "  Net margin", "pct"),
    ("eps_diluted", "Diluted EPS", "num"), ("eps_growth", "  EPS growth", "pct"),
    ("rnd_pct_rev", "R&D % revenue", "pct"), ("sga_pct_rev", "SG&A % revenue", "pct"),
    ("ebitda", "EBITDA (OpInc + D&A)", "money"),
    ("ocf", "Operating cash flow", "money"), ("capex", "Capex", "money"), ("fcf", "Free cash flow", "money"),
    ("fcf_margin", "  FCF margin", "pct"), ("sbc", "Stock-based comp", "money"), ("sbc_pct_rev", "  SBC % revenue", "pct"),
    ("fcf_after_sbc", "FCF minus SBC", "money"), ("cash_conversion", "OCF / net income", "x"),
    ("capex_pct_rev", "Capex % revenue", "pct"), ("capex_to_da", "Capex / D&A", "x"),
    ("cash", "Cash & equivalents", "money"), ("total_debt", "Total debt (approx.)", "money"),
    ("net_debt", "Net debt (neg = net cash)", "money"), ("net_debt_to_ebitda", "Net debt / EBITDA", "x"),
    ("interest_coverage", "Interest coverage", "x"), ("current_ratio", "Current ratio", "x"),
    ("debt_to_equity", "Debt / equity", "x"), ("goodwill_intang_pct_assets", "Goodwill+intang % assets", "pct"),
    ("equity", "Shareholders' equity", "money"),
    ("roe", "ROE", "pct"), ("roic", "ROIC (NOPAT / avg IC)", "pct"),
    ("roic_tangible", "ROIC ex goodwill & intangibles", "pct"), ("accruals_ratio", "Accruals ratio", "pct"),
    ("dso", "DSO (days)", "d"), ("dio", "DIO (days)", "d"), ("dpo", "DPO (days)", "d"), ("ccc", "Cash conversion cycle", "d"),
    ("shares_diluted", "Diluted shares", "shares"), ("share_change", "  Share count change", "pct"),
    ("buybacks", "Buybacks", "money"), ("dividends", "Dividends paid", "money"), ("acquisitions", "Acquisitions", "money"),
    ("payout_of_fcf", "Shareholder returns / FCF", "pct"),
    ("deferred_revenue", "Deferred revenue (current)", "money"), ("rpo", "Remaining perf. obligations", "money"),
]


def fmt(v, kind: str) -> str:
    if v is None:
        return "–"
    if kind == "money":
        a = abs(v)
        if a >= 1e9:
            return f"{v / 1e9:,.2f}B"
        if a >= 1e6:
            return f"{v / 1e6:,.1f}M"
        return f"{v:,.0f}"
    if kind == "shares":
        return f"{v / 1e6:,.1f}M"
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if kind == "x":
        return f"{v:.2f}x"
    if kind == "d":
        return f"{v:.0f}"
    return f"{v:,.2f}"


def table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def _annual_filings(sub: dict) -> list[dict]:
    rec = sub["filings"]["recent"]
    return [{"form": rec["form"][i], "reportDate": rec["reportDate"][i], "filingDate": rec["filingDate"][i]}
            for i in range(len(rec["form"])) if rec["form"][i] in ANNUAL_FORMS and rec["reportDate"][i]]


def analyze(query: str, n_years: int = 10) -> dict:
    co = resolve_or_exit(query)
    sub = submissions(co["cik"])
    facts = fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10(co['cik'])}.json")["facts"]
    ex = extract(facts)
    sic = int(sub.get("sic") or 0)
    is_financial = 6000 <= sic <= 6799
    years = build_years(ex, n_years)
    growth = growth_summary(years, ex)
    risks, strengths = flags(years, growth, is_financial)
    pio = piotroski(years[-1], years[-2]) if len(years) >= 2 else (None, [])
    notes = list(ex["splits"])
    annual_filings = _annual_filings(sub)
    if annual_filings and years and annual_filings[0]["reportDate"] > years[-1]["fye"]:
        f = annual_filings[0]
        notes.insert(0, f"WARNING: latest {f['form']} (period {f['reportDate']}, filed {f['filingDate']}) has no XBRL data "
                        f"in companyfacts; figures stop at FYE {years[-1]['fye']}. Read that filing's statements directly.")
    return {
        "company": {**co, "sic": sic, "sicDescription": sub.get("sicDescription"), "fiscalYearEnd": sub.get("fiscalYearEnd"),
                    "is_financial": is_financial},
        "currency": ex["currency"],
        "years": years,
        "ttm": ttm(ex),
        "quarters": recent_quarters(ex),
        "growth": growth,
        "latest_balance": {k: {"as_of": e, "value": v} for k, (e, v) in ex["latest_instant"].items()},
        "scores": {"piotroski_f": pio[0], "piotroski_detail": pio[1],
                   "altman_z2": altman_z2(years[-1]) if years else None},
        "flags": {"risks": risks, "strengths": strengths},
        "notes": notes,
        "source": f"SEC XBRL companyfacts CIK{cik10(co['cik'])} (https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10(co['cik'])}.json)",
    }


def render(d: dict) -> str:
    co, ccy, years = d["company"], d["currency"], d["years"]
    out = [f"# {co['name']} ({co['ticker']}) — fundamentals from SEC XBRL",
           f"CIK {co['cik']} · SIC {co['sic']} {co['sicDescription'] or ''} · Exchange {co['exchange']} · Currency {ccy}",
           f"Source: {d['source']}", ""]
    if co["is_financial"]:
        out += ["> **Financial-sector company.** Gross margin, working-capital, EBITDA and debt ratios are not meaningful. "
                "Use sector KPIs (NIM, CET1, loan losses, combined ratio, book value growth) from the filing instead.", ""]
    hdr = ["Metric"] + [f"FYE {y['fye']}" for y in years]
    rows = []
    for key, label, kind in ROWS:
        vals = [y.get(key) for y in years]
        if all(v is None for v in vals):
            continue
        rows.append([label] + [fmt(v, kind) for v in vals])
    out += ["## Annual (fiscal years)", table(hdr, rows), ""]

    g = {k: v for k, v in d["growth"].items() if v is not None}
    if g:
        out += ["## Compound annual growth", table(["Metric", "CAGR"], [[k, fmt(v, "pct")] for k, v in g.items()]), ""]

    t = d["ttm"]
    if t.get("revenue") is not None:
        out += [f"## Trailing twelve months (to {t.get('period_end')})",
                table(["Metric", "TTM"], [[k, fmt(t.get(k), "pct" if "margin" in k else "money")]
                                          for k in ("revenue", "gross_margin", "operating_income", "operating_margin",
                                                    "net_income", "net_margin", "ocf", "capex", "fcf", "fcf_margin", "sbc")
                                          if t.get(k) is not None]), ""]
    q = d["quarters"]
    if q:
        qrows = []
        for key, label, kind in (("revenue", "Revenue", "money"), ("rev_yoy", "  YoY", "pct"),
                                 ("gross_margin", "Gross margin", "pct"), ("operating_income", "Operating income", "money"),
                                 ("operating_margin", "Operating margin", "pct"), ("net_income", "Net income", "money"),
                                 ("eps_diluted", "Diluted EPS", "num"), ("ocf", "OCF", "money")):
            qrows.append([label] + [fmt(r.get(key), kind) for r in q])
        out += ["## Recent quarters (Q4 derived as FY − 9M where not reported)",
                table(["Metric"] + [r["qend"] for r in q], qrows), ""]

    s = d["scores"]
    out += ["## Quality scores (latest FY)",
            f"- Piotroski F-score: **{s['piotroski_f'] if s['piotroski_f'] is not None else 'n/a'}/9** — "
            + "; ".join(s["piotroski_detail"]),
            f"- Altman Z'' (book-equity variant): **{fmt(s['altman_z2'], 'num')}** (>2.6 safe · 1.1–2.6 grey · <1.1 distress)"
            + (" — not meaningful for financials" if co["is_financial"] else ""), ""]
    out += ["## Automatic flags (investigate, don't conclude)", "**Risks**"]
    out += [f"- {x}" for x in d["flags"]["risks"]] or ["- none triggered"]
    out += ["", "**Strengths**"]
    out += [f"- {x}" for x in d["flags"]["strengths"]] or ["- none triggered"]
    out += [""] + [f"- Note: {n}" for n in d.get("notes", [])]
    out += ["", "_Total debt is an approximation from XBRL debt tags; verify against the balance sheet and debt note._"]
    return "\n".join(out)


def render_peers(ds: list[dict]) -> str:
    keys = [("revenue", "Revenue", "money"), ("rev_growth", "Rev growth", "pct"), ("revenue_cagr_5y", "Rev CAGR 5y", "pct"),
            ("gross_margin", "Gross margin", "pct"), ("operating_margin", "Op margin", "pct"), ("fcf_margin", "FCF margin", "pct"),
            ("sbc_pct_rev", "SBC % rev", "pct"), ("roic", "ROIC", "pct"), ("roic_tangible", "ROIC ex GW", "pct"), ("net_debt_to_ebitda", "ND/EBITDA", "x"),
            ("rnd_pct_rev", "R&D % rev", "pct"), ("share_change", "Share chg", "pct"), ("piotroski_f", "Piotroski", "d")]
    hdr = ["Ticker", "FYE", "Ccy"] + [k[1] for k in keys]
    rows = []
    for d in ds:
        y = d["years"][-1] if d["years"] else {}
        src = {**y, **d["growth"], "piotroski_f": d["scores"]["piotroski_f"]}
        stale = " ⚠ stale" if any(n.startswith("WARNING") for n in d.get("notes", [])) else ""
        rows.append([d["company"]["ticker"], y.get("fye", "–") + stale, d["currency"]] + [fmt(src.get(k), f) for k, _, f in keys])
    return "# Peer comparison (latest fiscal year, SEC XBRL)\n\n" + table(hdr, rows) + \
        "\n\n_Fiscal year ends differ between companies; compare trends, not exact periods._"


def _json_default(o):
    return str(o)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("companies", nargs="+", help="ticker(s) or company name(s)")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--peers", action="store_true", help="print a single comparison table")
    ap.add_argument("--json", help="write full data to this path")
    a = ap.parse_args()
    results = [analyze(c, a.years) for c in a.companies]
    if a.peers or len(results) > 1:
        print(render_peers(results))
    else:
        print(render(results[0]))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(results if len(results) > 1 else results[0], indent=2, default=_json_default))
        print(f"\n(saved {a.json})", file=sys.stderr)


if __name__ == "__main__":
    main()
