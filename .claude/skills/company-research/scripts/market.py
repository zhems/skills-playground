# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg[binary]>=3.1", "yfinance>=0.2.50"]
# ///
"""Market data and valuation: price performance, beta, multiples (computed from SEC
fundamentals and as reported by the data vendor), valuation vs own history, reverse
DCF with a Treasury-based discount rate, consensus estimates and revisions, earnings
surprise record, analyst targets, short interest and ownership.

Data source order:
  1. Local Data-Octopus DB (Alpha Vantage + FINRA + Treasury yields + FX), when
     OCTOPUS_RO_URL is set and the ticker is on the watchlist.
  2. yfinance (Yahoo Finance) for tickers not in the DB, or if the DB is unreachable.

Usage (uv installs psycopg + yfinance in an isolated env):
    uv run market.py AAPL
    uv run market.py AAPL --erp 0.05 --terminal 0.025 --json out.json
    uv run market.py AAPL --discount 0.10            # fixed discount rate instead of 10y + ERP
    uv run market.py --compare AAPL MSFT GOOG META   # peer valuation table
    uv run market.py --coverage                      # which tickers the local DB covers
    uv run market.py D05.SI --yf-financials          # non-SEC company: Yahoo statements only
    uv run market.py AAPL --source yahoo             # force Yahoo (cross-check)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import octopus_db  # noqa: E402

DB_LABEL = "Data-Octopus (Alpha Vantage)"


# ------------------------------------------------------------------ formatting

def money(v, ccy=""):
    if v is None:
        return "–"
    a = abs(v)
    s = f"{v / 1e12:,.2f}T" if a >= 1e12 else f"{v / 1e9:,.2f}B" if a >= 1e9 else f"{v / 1e6:,.1f}M" if a >= 1e6 else f"{v:,.2f}"
    return f"{s} {ccy}".strip()


def pct(v, signed=False):
    return "–" if v is None else (f"{v * 100:+.1f}%" if signed else f"{v * 100:.1f}%")


def x(v):
    return "–" if v is None else f"{v:.1f}x"


def num(v, nd=2):
    return "–" if v is None else f"{v:,.{nd}f}"


def clean(v):
    """None for NaN / non-numeric; float otherwise."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def epoch_date(v):
    """Yahoo returns some dates as Unix timestamps."""
    if isinstance(v, (int, float)) and v > 10**8:
        return date.fromtimestamp(v)
    return v


def safe(fn, default=None):
    try:
        v = fn()
        return default if v is None else v
    except Exception:
        return default


# ------------------------------------------------------------------ analytics

def reverse_dcf(ev: float, fcf: float, r: float, g_term: float, years: int = 10) -> float | None:
    """FCF growth rate over `years` that makes PV(FCF + terminal value) equal to EV."""
    if not ev or not fcf or fcf <= 0 or ev <= 0 or r <= g_term:
        return None

    def pv(g):
        total, f = 0.0, fcf
        for t in range(1, years + 1):
            f *= 1 + g
            total += f / (1 + r) ** t
        return total + f * (1 + g_term) / (r - g_term) / (1 + r) ** years

    lo, hi = -0.5, 1.0
    if pv(lo) > ev or pv(hi) < ev:
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if pv(mid) < ev else (lo, mid)
    return (lo + hi) / 2


def price_stats(dates: list, adj: list, close: list) -> dict:
    """Returns use total-return (adjusted) prices; 52-week range uses split-adjusted closes."""
    out = {}
    if len(adj) < 2:
        return out
    last = adj[-1]
    for label, yrs in (("1y", 1), ("3y", 3), ("5y", 5), ("10y", 10)):
        n = int(252 * yrs)
        if len(adj) > n:
            out[f"Annualised total return {label}"] = (last / adj[-n - 1]) ** (1 / yrs) - 1
    window = adj[-252 * 5:]
    peak, mdd = window[0], 0.0
    for p in window:
        peak = max(peak, p)
        mdd = min(mdd, p / peak - 1)
    out["Max drawdown (5y)"] = mdd
    rets = [b / a - 1 for a, b in zip(adj[-253:-1], adj[-252:])]
    if len(rets) > 20:
        m = sum(rets) / len(rets)
        out["Volatility (1y, annualised)"] = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
    yr = close[-252:]
    out["Below 52-week high"] = close[-1] / max(yr) - 1
    out["_52w_low"], out["_52w_high"] = min(yr), max(yr)
    return out


def beta(d1: list, p1: list, d2: list, p2: list, years: int = 2) -> float | None:
    """Weekly-return beta of series 1 against series 2 (e.g. SPY) over `years`."""
    m2 = dict(zip(d2, p2))
    common = [(d, a, m2[d]) for d, a in zip(d1, p1) if d in m2][-252 * years:]
    common = common[::-5][::-1]  # every 5th trading day, ending on the latest
    if len(common) < 30:
        return None
    ra = [b[1] / a[1] - 1 for a, b in zip(common, common[1:])]
    rb = [b[2] / a[2] - 1 for a, b in zip(common, common[1:])]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((a - ma) * (b - mb) for a, b in zip(ra, rb))
    var = sum((b - mb) ** 2 for b in rb)
    return cov / var if var else None


# ------------------------------------------------------------------ loaders

def _blank(ticker: str, source: str) -> dict:
    return {"ticker": ticker, "source": source, "notes": [], "hist": {"dates": [], "adj": [], "close": []},
            "provider_multiples": {}, "analyst": {}, "estimates": [], "growth": [], "surprises": [],
            "next_earnings": None, "short": None, "ownership": {}, "top_holders": [], "provider_ttm": {}}


def load_db(db: octopus_db.Octopus, ticker: str) -> dict:
    m = _blank(ticker, DB_LABEL)
    ov = db.overview(ticker) or {}
    rows = db.prices(ticker)
    # split-adjusted (not dividend-adjusted) close, for price-at-date valuation and 52w range
    factor, sadj = 1.0, [0.0] * len(rows)
    for i in range(len(rows) - 1, -1, -1):
        sadj[i] = rows[i]["close"] / factor
        if rows[i]["split_coefficient"] and rows[i]["split_coefficient"] != 1:
            factor *= rows[i]["split_coefficient"]
    m["hist"] = {"dates": [r["trading_date"] for r in rows], "adj": [r["adjusted_close"] for r in rows], "close": sadj}
    m.update({"name": ov.get("name"), "sector": ov.get("sector"), "industry": ov.get("industry"),
              "exchange": ov.get("exchange"), "country": ov.get("country"), "currency": ov.get("currency") or "USD",
              "price": rows[-1]["close"] if rows else None, "price_date": rows[-1]["trading_date"] if rows else None,
              "market_cap": ov.get("market_capitalization"), "shares_outstanding": ov.get("shares_outstanding"),
              "shares_float": ov.get("shares_float"), "beta_provider": ov.get("beta"), "overview_as_of": ov.get("as_of"),
              "fiscal_year_end": ov.get("fiscal_year_end")})
    pe = db.trailing_pe(ticker) or {}
    m["provider_multiples"] = {"Trailing P/E": pe.get("pe_ratio"), "Forward P/E": ov.get("forward_pe"),
                               "PEG": ov.get("peg_ratio"), "EV/Revenue": ov.get("ev_to_revenue"),
                               "EV/EBITDA": ov.get("ev_to_ebitda"), "P/Sales (TTM)": ov.get("price_to_sales_ratio_ttm"),
                               "P/Book": ov.get("price_to_book_ratio")}
    m["dividend"] = {"yield": ov.get("dividend_yield"), "per_share": ov.get("dividend_per_share"),
                     "ex_date": ov.get("ex_dividend_date"), "pay_date": ov.get("dividend_date")}
    m["provider_ttm"] = {k: ov.get(k) for k in ("latest_quarter", "revenue_ttm", "gross_profit_ttm", "ebitda", "profit_margin",
                                                "operating_margin_ttm", "return_on_assets_ttm", "return_on_equity_ttm",
                                                "quarterly_revenue_growth_yoy", "quarterly_earnings_growth_yoy")}
    ratings = {k: ov.get(f"analyst_rating_{k}") for k in ("strong_buy", "buy", "hold", "sell", "strong_sell")}
    n = sum(v or 0 for v in ratings.values())
    m["analyst"] = {"target_mean": ov.get("analyst_target_price"), "target_low": None, "target_high": None,
                    "ratings": ratings, "count": n or None}
    m["estimates"] = [{"period": str(r["fiscal_date_ending"]), "horizon": r["horizon"],
                       "eps_avg": r["eps_estimate_average"], "eps_low": r["eps_estimate_low"], "eps_high": r["eps_estimate_high"],
                       "eps_n": r["eps_estimate_analyst_count"], "eps_30d": r["eps_estimate_average_30_days_ago"],
                       "eps_90d": r["eps_estimate_average_90_days_ago"],
                       "up30": r["eps_estimate_revision_up_trailing_30_days"],
                       "down30": r["eps_estimate_revision_down_trailing_30_days"],
                       "rev_avg": r["revenue_estimate_average"], "rev_low": r["revenue_estimate_low"],
                       "rev_high": r["revenue_estimate_high"], "rev_n": r["revenue_estimate_analyst_count"]}
                      for r in db.estimates(ticker)]
    m["growth"] = [{"period": str(r["fiscal_date_ending"]), "eps_growth": r["eps_growth"], "revenue_growth": r["revenue_growth"],
                    "basis": f"vs {r['prior_eps_source']} prior year"} for r in db.growth_estimates(ticker)]
    m["surprises"] = db.surprises(ticker)
    ne = db.next_earnings(ticker)
    m["next_earnings"] = ({"date": ne["report_date"], "time": ne["time_of_the_day"], "estimate": ne["estimate"]}
                          if ne else "not announced yet")
    si = db.short_interest(ticker)
    if si:
        flt = m["shares_float"]
        m["short"] = {"source": "FINRA", "as_of": si[0]["settlement_date"], "shares": si[0]["short_interest"],
                      "pct_float": si[0]["short_interest"] / flt if flt else None, "days_to_cover": si[0]["days_to_cover"],
                      "history": [(r["settlement_date"], r["short_interest"], r["days_to_cover"]) for r in si]}
    io = db.institutional_ownership(ticker) or {}
    m["ownership"] = {"pct_insiders": (ov["percent_insiders"] / 100) if ov.get("percent_insiders") is not None else None,
                      "pct_institutions": (ov["percent_institutions"] / 100) if ov.get("percent_institutions") is not None else None,
                      "inst_13f_pct": (io["total_institutional_ownership_percentage"] / 100)
                      if io.get("total_institutional_ownership_percentage") is not None else None,
                      "inst_holders": io.get("total_institutional_holders"), "inst_as_of": io.get("latest_reported"),
                      "holders_increased": io.get("holders_with_increased_holdings"),
                      "holders_decreased": io.get("holders_with_decreased_holdings")}
    so = m["shares_outstanding"]
    holders = db.top_holders(ticker, 20)
    newest = max((h["last_reported"] for h in holders if h["last_reported"]), default=None)
    fresh = [h for h in holders if newest and h["last_reported"] and (newest - h["last_reported"]).days <= 120][:10]
    if len(fresh) < len(holders[:10]):
        m["notes"].append("Top-holder list excludes entries last reported more than a quarter before the latest 13F date "
                          "(usually renamed filers, e.g. an old 'Vanguard Group Inc' line).")
    m["top_holders"] = [{"name": h["holder_name"], "shares": h["shares_held"],
                         "pct_out": h["shares_held"] / so if so and h["shares_held"] else None,
                         "change_pct": (h["shares_changed_percentage"] / 100) if h["shares_changed_percentage"] is not None else None,
                         "reported": h["last_reported"]} for h in fresh]
    m["notes"].append("Alpha Vantage EPS (trailing P/E, PEG, surprises, estimates) is on the vendor's own adjusted basis, which "
                      "can match neither GAAP nor the company's non-GAAP figure (AMD Q3 2026: vendor $1.84, GAAP $1.46, company "
                      "non-GAAP $2.09). 'Computed' multiples use GAAP figures from SEC filings. Quote vendor EPS only as 'consensus basis'.")
    if octopus_db.stale(m["price_date"]):
        m["notes"].append(f"WARNING: latest price in the DB is from {m['price_date']}; the loader may not have run recently.")
    spy = db.prices("SPY") if db.symbol("SPY") else []
    m["beta_calc"] = beta(m["hist"]["dates"], m["hist"]["adj"], [r["trading_date"] for r in spy],
                          [r["adjusted_close"] for r in spy]) if spy else None
    return m


def load_yahoo(ticker: str) -> dict:
    import yfinance as yf
    m = _blank(ticker, "Yahoo Finance (unofficial)")
    t = yf.Ticker(ticker)
    info = safe(lambda: t.info, {}) or {}
    h = safe(lambda: t.history(period="max", auto_adjust=False))
    if h is not None and not h.empty:
        m["hist"] = {"dates": [d.date() for d in h.index], "adj": [float(v) for v in h["Adj Close"]],
                     "close": [float(v) for v in h["Close"]]}
    m.update({"name": info.get("longName"), "sector": info.get("sector"), "industry": info.get("industry"),
              "exchange": info.get("exchange"), "country": info.get("country"), "currency": info.get("currency"),
              "price": info.get("currentPrice") or info.get("regularMarketPrice") or (m["hist"]["close"][-1] if m["hist"]["close"] else None),
              "price_date": m["hist"]["dates"][-1] if m["hist"]["dates"] else None,
              "market_cap": info.get("marketCap"), "shares_outstanding": info.get("sharesOutstanding"),
              "shares_float": info.get("floatShares"), "beta_provider": info.get("beta"),
              "financial_currency": info.get("financialCurrency")})
    m["provider_multiples"] = {"Trailing P/E": info.get("trailingPE"), "Forward P/E": info.get("forwardPE"),
                               "PEG": info.get("pegRatio") or info.get("trailingPegRatio"),
                               "EV/Revenue": info.get("enterpriseToRevenue"), "EV/EBITDA": info.get("enterpriseToEbitda"),
                               "P/Sales (TTM)": info.get("priceToSalesTrailing12Months"), "P/Book": info.get("priceToBook")}
    m["dividend"] = {"yield": info.get("trailingAnnualDividendYield"), "per_share": info.get("trailingAnnualDividendRate"),
                     "ex_date": epoch_date(info.get("exDividendDate")),
                     "pay_date": epoch_date(info.get("dividendDate"))}
    m["provider_ttm"] = {"revenue_ttm": info.get("totalRevenue"), "ebitda": info.get("ebitda"),
                         "profit_margin": info.get("profitMargins"), "operating_margin_ttm": info.get("operatingMargins"),
                         "return_on_equity_ttm": info.get("returnOnEquity"),
                         "quarterly_revenue_growth_yoy": info.get("revenueGrowth"),
                         "quarterly_earnings_growth_yoy": info.get("earningsGrowth")}
    m["analyst"] = {"target_mean": info.get("targetMeanPrice"), "target_low": info.get("targetLowPrice"),
                    "target_high": info.get("targetHighPrice"), "recommendation": info.get("recommendationKey"),
                    "ratings": {}, "count": info.get("numberOfAnalystOpinions")}
    ee = safe(lambda: t.earnings_estimate)
    re_ = safe(lambda: t.revenue_estimate)
    for period in (list(ee.index) if ee is not None else []):
        e = ee.loc[period]
        r = re_.loc[period] if re_ is not None and period in re_.index else {}
        m["estimates"].append({"period": period, "horizon": "fiscal year" if "y" in period else "fiscal quarter",
                               "eps_avg": clean(e.get("avg")), "eps_low": clean(e.get("low")), "eps_high": clean(e.get("high")),
                               "eps_n": clean(e.get("numberOfAnalysts")), "rev_avg": clean(r.get("avg")) if len(r) else None,
                               "rev_low": clean(r.get("low")) if len(r) else None, "rev_high": clean(r.get("high")) if len(r) else None,
                               "rev_n": clean(r.get("numberOfAnalysts")) if len(r) else None})
    ge = safe(lambda: t.growth_estimates)
    for period in (list(ge.index) if ge is not None else []):
        m["growth"].append({"period": period, "eps_growth": clean(ge.loc[period].get("stockTrend")), "revenue_growth": None,
                            "basis": "Yahoo"})
    cal = safe(lambda: t.calendar, {}) or {}
    ed = cal.get("Earnings Date")
    m["next_earnings"] = {"date": ed[0] if isinstance(ed, list) and ed else ed, "time": None,
                          "estimate": cal.get("Earnings Average")} if ed else "unknown"
    if info.get("sharesShort"):
        m["short"] = {"source": "Yahoo", "as_of": epoch_date(info.get("dateShortInterest")), "shares": info.get("sharesShort"),
                      "pct_float": info.get("shortPercentOfFloat"), "days_to_cover": info.get("shortRatio"), "history": []}
    m["ownership"] = {"pct_insiders": info.get("heldPercentInsiders"), "pct_institutions": info.get("heldPercentInstitutions")}
    ih = safe(lambda: t.institutional_holders)
    if ih is not None and not ih.empty:
        m["top_holders"] = [{"name": r.get("Holder"), "shares": clean(r.get("Shares")), "pct_out": clean(r.get("pctHeld")),
                             "change_pct": clean(r.get("pctChange")), "reported": str(r.get("Date Reported"))[:10]}
                            for _, r in ih.head(10).iterrows()]
    spy = safe(lambda: yf.Ticker("SPY").history(period="5y", auto_adjust=True))
    if spy is not None and not spy.empty and m["hist"]["dates"]:
        m["beta_calc"] = beta(m["hist"]["dates"], m["hist"]["adj"], [d.date() for d in spy.index],
                              [float(v) for v in spy["Close"]])
    return m


def load(ticker: str, db, source: str) -> dict:
    if source != "yahoo" and db and db.symbol(ticker):
        return load_db(db, db.symbol(ticker))
    if source == "db":
        raise SystemExit(f"{ticker} is not in the Data-Octopus DB (or OCTOPUS_RO_URL is unset).")
    m = load_yahoo(ticker)
    if db:
        m["notes"].append(f"{ticker} is not on the Data-Octopus watchlist; market data from Yahoo Finance.")
    return m


# ------------------------------------------------------------------ SEC valuation

def sec_valuation(m: dict, r: float, g_term: float, db) -> None:
    try:
        import fundamentals as F
        f = F.analyze(m["ticker"], 10)
    except SystemExit as e:
        m["notes"].append(f"SEC fundamentals unavailable: {e}")
        return
    except Exception as e:
        m["notes"].append(f"SEC fundamentals failed: {e}")
        return
    stale_sec = [n for n in f.get("notes", []) if n.startswith("WARNING")]
    m["notes"] += [f"SEC: {n} Computed multiples below use that older period." for n in stale_sec]
    fin_ccy, q_ccy = f["currency"], m.get("currency") or "USD"
    conv, adr = 1.0, fin_ccy != q_ccy
    y = f["years"][-1] if f["years"] else {}
    ttm = f["ttm"] or {}
    lb = {k: v["value"] for k, v in f["latest_balance"].items()}
    if adr:
        fx = db.usd_to(fin_ccy) if db and q_ccy == "USD" else None
        if not fx or not m.get("market_cap"):
            m["notes"].append(f"Financials are in {fin_ccy}, quote in {q_ccy}, and no FX rate or market cap is available: "
                              "SEC-based multiples skipped.")
            return
        conv = 1 / fx["close"]
        mcap = m["market_cap"]  # vendor market cap in quote currency (handles the ADR ratio)
        m["notes"].append(f"Vendor P/Sales and TTM revenue/EBITDA are in {fin_ccy} against a {q_ccy} market cap, so vendor "
                          "P/Sales is not meaningful for this ADR; use the computed figure or vendor EV/Revenue.")
        m["provider_multiples"]["P/Sales (TTM)"] = None
        m["notes"].append(f"SEC financials converted {fin_ccy}→{q_ccy} at {fx['close']:.4f} per USD ({fx['trading_date']}); "
                          "flows ideally use average rates, so treat multiples as ±3%. Market cap from the data vendor (ADR basis).")
    else:
        shares = (f["latest_balance"].get("shares_outstanding") or {}).get("value") or m.get("shares_outstanding") or y.get("shares_diluted")
        mcap = m["price"] * shares if m.get("price") and shares else m.get("market_cap")
        if mcap and m.get("market_cap") and abs(mcap / m["market_cap"] - 1) > 0.1:
            m["notes"].append(f"Computed market cap {money(mcap)} differs >10% from vendor's {money(m['market_cap'])}: check share count.")

    def c(v):
        return v * conv if v is not None else None
    debt = c(F.total_debt(lb))
    cash = c((lb.get("cash") or 0) + (lb.get("st_investments") or 0))
    ev = mcap + (debt or 0) - cash if mcap else None
    rev = c(ttm.get("revenue") or y.get("revenue"))
    ebit = c(ttm.get("operating_income") or y.get("operating_income"))
    ni = c(ttm.get("net_income") or y.get("net_income"))
    fcf = c(ttm.get("fcf") or y.get("fcf"))
    sbc = c(ttm.get("sbc") or y.get("sbc"))
    da = c(ttm.get("da") or y.get("da"))
    returns = c((ttm.get("buybacks") or y.get("buybacks") or 0) + (ttm.get("dividends") or y.get("dividends") or 0))
    eq = c(lb.get("equity"))
    m["sec_valuation"] = {
        "basis": (f"TTM to {ttm.get('period_end')}" if ttm.get("revenue") else f"FY {y.get('fye')}") + (" — STALE" if stale_sec else ""),
        "Market cap": mcap, "Enterprise value": ev, "Net debt": (debt or 0) - cash,
        "P/E": mcap / ni if mcap and ni and ni > 0 else None,
        "EV/Revenue": ev / rev if ev and rev else None,
        "P/Sales (TTM)": mcap / rev if mcap and rev else None,
        "EV/EBIT": ev / ebit if ev and ebit and ebit > 0 else None,
        "EV/EBITDA": ev / (ebit + da) if ev and ebit and da and ebit + da > 0 else None,
        "P/FCF": mcap / fcf if mcap and fcf and fcf > 0 else None,
        "P/Book": mcap / eq if mcap and eq and eq > 0 else None,
        "FCF yield": fcf / mcap if mcap and fcf else None,
        "FCF yield after SBC": (fcf - sbc) / mcap if mcap and fcf and sbc is not None else None,
        "Earnings yield": ni / mcap if mcap and ni else None,
        "Shareholder yield": returns / mcap if mcap else None,
    }
    m["reverse_dcf"] = {"fcf_base": fcf, "terminal_growth": g_term, "discount": r, "years": 10, "implied_growth": {
        f"{rr:.1%}": reverse_dcf(ev, fcf, rr, g_term) for rr in sorted({round(r - 0.01, 4), r, round(r + 0.01, 4), round(r + 0.02, 4)})}}
    if sbc:
        m["reverse_dcf"]["implied_growth_sbc_adjusted"] = reverse_dcf(ev, fcf - sbc, r, g_term)
    m["sec_growth"] = f["growth"]
    # valuation vs own history: split-adjusted close at each fiscal year end x diluted shares (same-currency only)
    histv = []
    if not adr and m["hist"]["dates"]:
        dates, close = m["hist"]["dates"], m["hist"]["close"]
        for yr in f["years"]:
            sh, fye = yr.get("shares_diluted"), date.fromisoformat(yr["fye"])
            idx = max((i for i, d in enumerate(dates) if d <= fye), default=None)
            if not sh or idx is None:
                continue
            mc = close[idx] * sh
            histv.append({"fye": yr["fye"], "price": close[idx],
                          "pe": mc / yr["net_income"] if yr.get("net_income") and yr["net_income"] > 0 else None,
                          "p_sales": mc / yr["revenue"] if yr.get("revenue") else None,
                          "p_fcf": mc / yr["fcf"] if yr.get("fcf") and yr["fcf"] > 0 else None})
    elif adr:
        m["notes"].append("Valuation-vs-history skipped for ADRs (needs the ADR ratio); use the trailing P/E history in the DB instead.")
    m["valuation_history"] = histv
    m["sec_currency"] = fin_ccy


# ------------------------------------------------------------------ output

def render(m: dict) -> str:
    c = m.get("currency") or ""
    ps = price_stats(m["hist"]["dates"], m["hist"]["adj"], m["hist"]["close"]) if m["hist"]["adj"] else {}
    out = [f"# Market & valuation: {m.get('name') or m['ticker']} ({m['ticker']})",
           f"Source: **{m['source']}** · price as of {m.get('price_date')}"
           + (f" · overview as of {m['overview_as_of']}" if m.get("overview_as_of") else ""),
           f"Price {num(m.get('price'))} {c} · Vendor market cap {money(m.get('market_cap'), c)} · "
           f"{m.get('sector')} / {m.get('industry')} · {m.get('exchange')} {m.get('country') or ''}",
           f"52-week range {num(ps.get('_52w_low'))}–{num(ps.get('_52w_high'))} · Beta: vendor {num(m.get('beta_provider'))}, "
           f"calculated {num(m.get('beta_calc'))} (2y weekly returns vs SPY; vendor betas are usually 5y monthly)", ""]
    for n in m["notes"]:
        out.append(f"> {n}")
    if m["notes"]:
        out.append("")
    if ps:
        out += ["## Price performance", "| Metric | Value |", "|---|---|"]
        out += [f"| {k} | {pct(v)} |" for k, v in ps.items() if not k.startswith("_")]
        out.append("")

    v, pm = m.get("sec_valuation"), m.get("provider_multiples", {})
    if v or any(val is not None for val in pm.values()):
        basis = v["basis"] if v else "n/a"
        out += [f"## Valuation multiples", f"Computed = SEC XBRL fundamentals ({basis}) × current price. Vendor = {m['source']}.",
                "| Multiple | Computed | Vendor |", "|---|---|---|"]
        keys = ["P/E", "Forward P/E", "PEG", "EV/EBIT", "EV/EBITDA", "EV/Revenue", "P/Sales (TTM)", "P/FCF", "P/Book"]
        for k in keys:
            comp = v.get(k) if v else None
            vend = pm.get("Trailing P/E") if k == "P/E" else pm.get(k)
            if comp is None and vend is None:
                continue
            fmt = num if k == "PEG" else x
            out.append(f"| {k} | {fmt(comp) if comp is not None else '–'} | {fmt(vend) if vend is not None else '–'} |")
        if v:
            out += [f"| Market cap | {money(v['Market cap'], c)} | {money(m.get('market_cap'), c)} |",
                    f"| Enterprise value | {money(v['Enterprise value'], c)} | |",
                    f"| Net debt (neg = net cash) | {money(v['Net debt'], c)} | |"]
            for k in ("FCF yield", "FCF yield after SBC", "Earnings yield", "Shareholder yield"):
                out.append(f"| {k} | {pct(v[k])} | |")
        dv = m.get("dividend") or {}
        if dv.get("yield"):
            out.append(f"| Dividend yield | | {pct(dv['yield'])} (ex-div {dv.get('ex_date')}, paid {dv.get('pay_date')}) |")
        out.append("")

    rf = m.get("risk_free")
    rd = m.get("reverse_dcf")
    if rf or rd:
        out.append("## Rates context")
        if rf:
            out.append(f"- 10-year Treasury yield: **{rf['value']:.2f}%** ({rf['date']}, Data-Octopus)")
        out.append(f"- Discount rate used: **{pct(m['discount_rate'])}** ({m['discount_basis']})")
        if rf and v and v.get("FCF yield") is not None:
            out.append(f"- FCF yield minus 10y yield: {(v['FCF yield'] * 100 - rf['value']):+.2f}pp "
                       "(negative = investors are paying for growth, not current cash)")
        out.append("")
    if rd:
        out += ["## Reverse DCF — what growth is the price assuming?",
                f"FCF base {money(rd['fcf_base'], c)}, 10-year explicit period, terminal growth {pct(rd['terminal_growth'])}.",
                "| Discount rate | Implied 10y FCF CAGR |", "|---|---|"]
        out += [f"| {k}{' (base)' if abs(float(k.strip('%')) / 100 - rd['discount']) < 1e-6 else ''} | {pct(g)} |"
                for k, g in rd["implied_growth"].items()]
        if rd.get("implied_growth_sbc_adjusted") is not None:
            out.append(f"| base rate, FCF minus SBC | {pct(rd['implied_growth_sbc_adjusted'])} |")
        g = m.get("sec_growth") or {}
        hist_bits = [f"{k.replace('_cagr_', ' ').replace('_', ' ')} {pct(val)}" for k, val in g.items()
                     if val is not None and (k.startswith("fcf_cagr") or k.startswith("revenue_cagr"))]
        if hist_bits:
            out.append(f"\nHistorical CAGRs for comparison (SEC): {', '.join(hist_bits)}.")
        out += ["", "_If the price implies more growth than the business has ever delivered, the margin of safety is thin._", ""]

    hv = m.get("valuation_history")
    if hv:
        out += ["## Valuation vs own history (split-adjusted price at fiscal year end)", "| FYE | Price | P/E | P/Sales | P/FCF |",
                "|---|---|---|---|---|"]
        out += [f"| {h['fye']} | {h['price']:.2f} | {x(h['pe'])} | {x(h['p_sales'])} | {x(h['p_fcf'])} |" for h in hv]
        if v:
            out.append(f"| **now** | {num(m['price'])} | {x(v['P/E'])} | {x(v['P/Sales (TTM)'])} | {x(v['P/FCF'])} |")
        out.append("")

    est = m.get("estimates") or []
    if est:
        out += ["## Consensus estimates", "| Period | Horizon | EPS avg (low–high) | # | EPS 30d ago | EPS 90d ago | Revisions 30d ↑/↓ | Revenue avg (low–high) | # |",
                "|---|---|---|---|---|---|---|---|---|"]
        for e in est:
            rng = f"{num(e['eps_avg'])} ({num(e['eps_low'])}–{num(e['eps_high'])})" if e.get("eps_avg") is not None else "–"
            rrng = f"{money(e['rev_avg'])} ({money(e['rev_low'])}–{money(e['rev_high'])})" if e.get("rev_avg") else "–"
            rev = f"{e.get('up30') or 0}/{e.get('down30') or 0}" if "up30" in e else "–"
            out.append(f"| {e['period']} | {e['horizon']} | {rng} | {num(e.get('eps_n'), 0)} | {num(e.get('eps_30d'))} | "
                       f"{num(e.get('eps_90d'))} | {rev} | {rrng} | {num(e.get('rev_n'), 0)} |")
        if m.get("sec_currency") and m["sec_currency"] != (m.get("currency") or "USD"):
            out.append(f"\n_Estimates are in the reporting currency, which can change over time (TSM's revenue estimates switch "
                       f"from USD to TWD in late 2024). Check magnitudes against reported {m['sec_currency']} revenue._")
        out.append("")
    if m.get("growth"):
        out += ["**Consensus growth**", "| Fiscal period | EPS growth | Revenue growth | Basis |", "|---|---|---|---|"]
        out += [f"| {g['period']} | {pct(g['eps_growth'])} | {pct(g['revenue_growth'])} | {g['basis']} |" for g in m["growth"]]
        out.append("")
    if m.get("surprises"):
        s = m["surprises"]
        beats = sum(1 for r in s if (r.get("surprise_percentage") or 0) > 0)
        out += [f"## Earnings track record (last {len(s)} quarters: {beats} beats)",
                "| Quarter end | Reported | Time | EPS actual | EPS est. | Surprise |", "|---|---|---|---|---|---|"]
        out += [f"| {r['fiscal_date_ending']} | {r['reported_date']} | {r.get('report_time') or '–'} | {num(r['reported_eps'])} | "
                f"{num(r['estimated_eps'])} | {pct((r['surprise_percentage'] or 0) / 100, signed=True) if r.get('surprise_percentage') is not None else '–'} |"
                for r in s]
        out.append("")
    ne = m.get("next_earnings")
    if ne:
        out.append(f"**Next earnings:** {ne if isinstance(ne, str) else f'{ne['date']} {ne.get('time') or ''} (consensus EPS {num(ne.get('estimate'))})'}")
        out.append("")

    a = m.get("analyst") or {}
    if a.get("target_mean"):
        up = a["target_mean"] / m["price"] - 1 if m.get("price") else None
        out += ["## Analysts", f"- Mean price target {num(a['target_mean'])} ({pct(up, signed=True)} vs price)"
                + (f", range {num(a['target_low'])}–{num(a['target_high'])}" if a.get("target_low") else "")]
        if a.get("ratings") and a.get("count"):
            r = a["ratings"]
            out.append(f"- Ratings ({a['count']} analysts): strong buy {r['strong_buy']}, buy {r['buy']}, hold {r['hold']}, "
                       f"sell {r['sell']}, strong sell {r['strong_sell']}")
        elif a.get("recommendation"):
            out.append(f"- Consensus: {a['recommendation']} ({a.get('count')} analysts)")
        out.append("")

    s = m.get("short")
    if s:
        out += [f"## Short interest ({s['source']}, settlement {s['as_of']})",
                f"- {num(s['shares'], 0)} shares short · {pct(s['pct_float'])} of float · {num(s['days_to_cover'], 1)} days to cover"]
        if len(s.get("history", [])) > 1:
            out.append("- Trend: " + " → ".join(f"{d}: {sh / 1e6:,.1f}M" for d, sh, _ in reversed(s["history"])))
        out.append("")
    o = m.get("ownership") or {}
    if any(val is not None for val in o.values()):
        out += ["## Ownership", f"- Insiders {pct(o.get('pct_insiders'))} · institutions {pct(o.get('pct_institutions'))}"
                + (f" · 13F-based {pct(o['inst_13f_pct'])} across {o.get('inst_holders'):,} holders (as of {o.get('inst_as_of')})"
                   if o.get("inst_13f_pct") is not None and o.get("inst_holders") else "")]
        if o.get("holders_increased") is not None:
            out.append(f"- Last quarter: {o['holders_increased']:,} holders increased, {o['holders_decreased']:,} decreased")
    if m.get("top_holders"):
        out += ["", "| Top holder | Shares | % of shares out | Change | Reported |", "|---|---|---|---|---|"]
        out += [f"| {h['name']} | {num(h['shares'], 0)} | {pct(h['pct_out'])} | {pct(h['change_pct'], signed=True)} | {h['reported']} |"
                for h in m["top_holders"]]
    return "\n".join(out)


def compare(tickers: list[str], db, source: str) -> str:
    """Peer valuation table from the vendor (DB first, Yahoo fallback per ticker)."""
    rows = []
    for t in tickers:
        ov = db.overview(t) if source != "yahoo" and db and db.symbol(t) else None
        if ov:
            sym = db.symbol(t)
            pe = db.trailing_pe(sym) or {}
            si = db.short_interest(sym, 1)
            price = pe.get("stock_price")
            flt = ov.get("shares_float")
            n = sum(ov.get(f"analyst_rating_{k}") or 0 for k in ("strong_buy", "buy", "hold", "sell", "strong_sell"))
            rows.append([sym, "DB", price, ov.get("market_capitalization"), pe.get("pe_ratio"), ov.get("forward_pe"),
                         ov.get("peg_ratio"), ov.get("ev_to_ebitda"), ov.get("ev_to_revenue"), ov.get("price_to_sales_ratio_ttm"),
                         ov.get("dividend_yield"), ov.get("beta"), si[0]["short_interest"] / flt if si and flt else None,
                         ov["analyst_target_price"] / price - 1 if ov.get("analyst_target_price") and price else None, n or None,
                         ov.get("quarterly_revenue_growth_yoy"), ov.get("operating_margin_ttm")])
        else:
            try:
                import yfinance as yf
                i = yf.Ticker(t).info
            except Exception:
                i = {}
            p = i.get("currentPrice") or i.get("regularMarketPrice")
            rows.append([t, "Yahoo" if i else "none", p, i.get("marketCap"), i.get("trailingPE"), i.get("forwardPE"),
                         i.get("trailingPegRatio"), i.get("enterpriseToEbitda"), i.get("enterpriseToRevenue"),
                         i.get("priceToSalesTrailing12Months"), i.get("trailingAnnualDividendYield"), i.get("beta"),
                         i.get("shortPercentOfFloat"), i["targetMeanPrice"] / p - 1 if i.get("targetMeanPrice") and p else None,
                         i.get("numberOfAnalystOpinions"), i.get("revenueGrowth"), i.get("operatingMargins")])
    hdr = ["Ticker", "Src", "Price", "Mkt cap", "P/E", "Fwd P/E", "PEG", "EV/EBITDA", "EV/Rev", "P/S", "Div yld", "Beta",
           "Short % float", "Target upside", "# analysts", "Rev growth (q YoY)", "Op margin TTM"]
    fmts = [str, str, num, money, x, x, num, x, x, x, pct, num, pct, lambda z: pct(z, True), lambda z: num(z, 0), pct, pct]
    mixed = []
    for r in rows:  # P/S far below EV/Revenue means revenue in another currency than market cap (ADRs)
        if r[8] and r[9] and r[8] / r[9] > 5:
            mixed.append(r[0])
            r[9] = None
    lines = ["# Peer valuation (vendor data; DB = Data-Octopus/Alpha Vantage + FINRA)", "",
             "| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for r in rows:
        lines.append("| " + " | ".join(f(v) if v is not None else "–" for f, v in zip(fmts, r)) + " |")
    lines += ["", "_Vendor multiples use each vendor's own TTM definitions; for like-for-like fundamentals use "
              "`fundamentals.py --peers`. Market caps are in the quote currency._"]
    if mixed:
        lines.append(f"_P/S blanked for {', '.join(mixed)}: the vendor divides a USD market cap by revenue in the reporting "
                     "currency. EV/Revenue is consistent._")
    return "\n".join(lines)


def yf_financials(ticker: str) -> str:
    """Fallback for companies outside EDGAR: Yahoo's annual statements (≈4 years)."""
    import yfinance as yf
    t = yf.Ticker(ticker)
    out = [f"# Yahoo Finance statements for {ticker} (secondary source; cross-check with the annual report)"]
    for label, df in (("Income statement", safe(lambda: t.income_stmt)), ("Balance sheet", safe(lambda: t.balance_sheet)),
                      ("Cash flow", safe(lambda: t.cashflow))):
        if df is None or df.empty:
            continue
        df = (df / 1e6).round(1)
        df.columns = [str(col)[:10] for col in df.columns]
        out += ["", f"## {label} (millions, reporting currency)", df.dropna(how="all").to_string()]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*", help="ticker (Yahoo format for non-US, e.g. D05.SI)")
    ap.add_argument("--discount", type=float, help="fixed discount rate (default: 10y Treasury + ERP, or 9%% without DB)")
    ap.add_argument("--erp", type=float, default=0.05, help="equity risk premium added to the 10y yield (default 5%%)")
    ap.add_argument("--terminal", type=float, default=0.025, help="terminal growth (default 2.5%%)")
    ap.add_argument("--source", choices=["auto", "db", "yahoo"], default="auto")
    ap.add_argument("--compare", action="store_true", help="peer valuation table for all tickers given")
    ap.add_argument("--coverage", action="store_true", help="list tickers covered by the local DB")
    ap.add_argument("--yf-financials", action="store_true", help="print Yahoo statements (non-SEC companies)")
    ap.add_argument("--json")
    a = ap.parse_args()

    if a.yf_financials:
        print(yf_financials(a.tickers[0]))
        return
    db = octopus_db.connect() if a.source != "yahoo" else None
    if a.coverage:
        print(", ".join(db.covered()) if db else "Data-Octopus DB not available.")
        return
    if not a.tickers:
        ap.error("ticker required")
    if a.compare or len(a.tickers) > 1:
        print(compare(a.tickers, db, a.source))
        return

    m = load(a.tickers[0], db, a.source)
    rf = db.risk_free_10y() if db else None
    if a.discount is not None:
        r, basis = a.discount, "fixed by --discount"
    elif rf:
        r, basis = round(rf["value"] / 100 + a.erp, 4), f"10y Treasury {rf['value']:.2f}% + {a.erp:.1%} equity risk premium"
    else:
        r, basis = 0.09, "default 9% (no DB for the Treasury yield)"
    m.update({"risk_free": rf, "discount_rate": r, "discount_basis": basis})
    sec_valuation(m, r, a.terminal, db)
    print(render(m))
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        dump = {k: val for k, val in m.items() if k != "hist"}
        Path(a.json).write_text(json.dumps(dump, indent=2, default=str))


if __name__ == "__main__":
    main()
