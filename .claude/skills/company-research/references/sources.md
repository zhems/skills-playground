# Sources and reliability tiers

Use the highest tier available for every number. Cite tier implicitly through the
source tag in the report (e.g. `[10-K FY2025]`, `[XBRL]`, `[Reuters 2026-10-03]`, `[Yahoo]`).

## Tier 1: primary (use for every financial figure where possible)
| Source | What it gives | Access |
|---|---|---|
| SEC XBRL companyfacts API | Every tagged number in 10-K/10-Q/20-F since ~2009 | `fundamentals.py` |
| 10-K / 20-F / 40-F | Business, risk factors, MD&A, segment note, debt note, contractual obligations | `filings.py sections`, `segments.py` |
| 10-Q | Latest quarter, updated risk factors, liquidity | `filings.py sections --quarterly` |
| 8-K (Item 2.02 + Ex 99.1) | Earnings releases with guidance and non-GAAP reconciliations | `filings.py exhibits` then `filings.py text URL` |
| 8-K other items | 1.01 material agreements, 2.01 acquisitions, 4.01 auditor change, 4.02 restatement, 5.02 executive changes | `filings.py list --forms 8-K` |
| DEF 14A (proxy) | Executive pay and its metrics, board, beneficial ownership, related-party transactions | `filings.py list --forms "DEF 14A"`, then `text URL` |
| Form 4 | Insider transactions | `insiders.py` |
| 13F-HR (filed by institutions) | Institutional holdings, quarterly | EDGAR full-text search |
| SC 13D / 13G | Activist or > 5% holders | `filings.py list --forms "SC 13D,SC 13G"` |
| Company IR site | Investor presentations, investor-day TAM claims, KPI supplements, call webcasts/transcripts | web search `<company> investor relations` |
| Regulators | FDA (approvals, warning letters), FTC/DOJ/EC (antitrust), FCC, NHTSA recalls, BIS (export controls) | official sites |

EDGAR full-text search (all filings since 2001, any phrase):
`https://efts.sec.gov/LATEST/search-index?q="sole source"&dateRange=custom&ciks=0000320193`
(UI: https://www.sec.gov/edgar/search/). Useful to find which other companies name
this company as a customer or supplier.

## Non-US listings (outside EDGAR)
| Market | Filings / announcements |
|---|---|
| Singapore | SGX company announcements (sgx.com/securities/company-announcements); annual reports on IR site |
| Hong Kong | HKEXnews (hkexnews.hk) |
| UK | RNS via londonstockexchange.com; Companies House |
| EU | Company IR sites; ESEF annual reports (XBRL) via filings.xbrl.org |
| Japan | EDINET (disclosure2.edinet-fsa.go.jp), TDnet for timely disclosure |
| Korea | DART (dart.fss.or.kr) |
| Canada | SEDAR+ (sedarplus.ca) — many Canadians also file 40-F on EDGAR |
| Australia | ASX announcements |
| China A-shares | cninfo.com.cn |
Many large foreign companies also file 20-F/6-K with the SEC (ADRs): try EDGAR first.
For statements, `market.py TICKER --yf-financials` gives Yahoo's version (Tier 3) — reconcile
headline figures against the annual report PDF.

## Local Data-Octopus DB (preferred over Yahoo for market data)
Read-only PostgreSQL on this machine (`OCTOPUS_RO_URL`), loaded on a schedule. Used by
`market.py` automatically for tickers on its watchlist (`market.py --coverage`).
| Data | Table | Tier | Notes |
|---|---|---|---|
| Daily prices (raw, split- and dividend-adjusted) | `alpha_vantage.daily_prices_adjusted` | 2 | Since 1999; SPY included for beta |
| Trailing P/E (FX-adjusted EPS) | `alpha_vantage.daily_pe_ratios` | 3 | Vendor calculation |
| Company profile, vendor multiples, TTM figures, dividend dates, analyst target and ratings | `alpha_vantage.company_overview` | 3 | TTM figures in reporting currency; percent_* are percentages, margins are ratios; history only from 2026-11-23 |
| Consensus EPS/revenue estimates, revisions | `alpha_vantage.earnings_estimates`, view `earnings_growth_estimates` | 3 | Reporting currency, which can change over time |
| EPS actual vs estimate, report timing | `alpha_vantage.quarterly_earnings` | 3 | |
| Next earnings date | `alpha_vantage.earnings_calendar` | 3 | No row = not announced yet |
| Short interest | `finra.short_interest_by_symbol` | 1 | Twice monthly from FINRA; % float uses vendor float |
| Institutional ownership, top 100 holders | `alpha_vantage.institutional_ownership`, `institutional_holders` | 3 | 13F-derived; lags a quarter |
| 10y and other Treasury yields, CPI, GDP, jobs, retail sales, oil | `alpha_vantage.economic_indicator_data`, `commodities_data` | 2 | FRED-sourced series |
| FX (USD to 16 currencies) | `alpha_vantage.forex_daily` | 2 | Used to convert ADR financials |
Not used: `alpha_vantage.insider_transactions` (its `acquisition_or_disposal` column mixes
transaction codes); `insiders.py` reads Form 4 from the SEC directly instead.
Always filter `valid_to IS NULL` when querying by hand.

## Tier 2: reputable secondary (context, industry data, events)
- News of record: Reuters, Bloomberg, Financial Times, Wall Street Journal, Nikkei Asia,
  The Economist, CNBC (for reported facts, not opinions). Business Times / Straits Times for SGX names.
- Industry and government data: SIA/WSTS (semis), IEA/EIA (energy), BLS, Census, FRED
  (macro, rates), Eurostat, company-cited market research (Gartner, IDC, Counterpoint,
  Canalys, IQVIA) as quoted in reputable outlets.
- Earnings call transcripts: company IR site, Motley Fool (free), Seeking Alpha / Investing.com.
- Credit ratings and outlooks: S&P, Moody's, Fitch press releases.

## Tier 3: aggregators (convenient, verify before citing)
Yahoo Finance (`market.py` fallback), Stockanalysis.com, Macrotrends, Finviz, Morningstar
(free pages), Koyfin, TIKR, GuruFocus, Simply Wall St. Fine for price history,
consensus and short interest; reconcile any fundamental figure against Tier 1.

## Avoid as evidence
Social media, Reddit/StockTwits, promotional newsletters, paid-placement "analysis",
undated blog posts, AI-generated content farms, press releases from unrelated third
parties. Seeking Alpha contributor articles are opinions: use only for idea
generation, never as a data source.

## Freshness rules
- Always state the "as of" date of price data and the fiscal period of every figure.
- After pulling the latest 10-K, check `filings.py list` for any newer 10-Q or 8-K
  earnings release and use those for the latest quarter.
- For news, prefer the last 12 months and date every item.
