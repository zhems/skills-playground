---
name: company-research
description: Build a structured, data-backed investor breakdown of a public company from a ticker or company name. Pulls multi-year financials, segment/geography/customer revenue mix, cash-flow quality, balance sheet, capital allocation, insider trades, valuation (multiples, history, reverse DCF) and risk-factor changes from SEC filings and market data, then writes a report that highlights opportunities and risks backed by the numbers. Use when the user asks to research, analyse, break down, do due diligence on, or evaluate a stock or company for investing (e.g. "research NVDA", "breakdown of Costco", "is TSMC a good business").
argument-hint: "<ticker or company name> [quick|full] [peers: T1,T2,...]"
---

# Company research

Produce an investor breakdown of **$ARGUMENTS** that is quantitative first, sourced,
and explicit about what the numbers imply. Default depth is `full`; `quick` keeps
steps 1–2 and 5 and writes only sections 1, 2, 5, 11, 12, 14 of the template.

Scripts live in `${CLAUDE_SKILL_DIR}/scripts` (if the variable is not substituted, use
`~/.claude/skills/company-research/scripts`). All are stdlib Python except
`market.py`, which runs through `uv run` (it needs `psycopg` and `yfinance`). Each
script prints markdown and takes `--help`.

**Data sources, in order of preference:**
1. SEC EDGAR (filings, XBRL, Form 4): all fundamentals, segments, insiders, risk factors.
2. The local **Data-Octopus** PostgreSQL DB (Alpha Vantage, FINRA, Treasury yields, FX),
   read by `market.py` when `OCTOPUS_RO_URL` is set and the ticker is on its watchlist:
   prices, vendor multiples, consensus estimates and revisions, earnings surprises,
   earnings calendar, analyst targets and ratings, FINRA short interest, ownership and
   top holders, the 10-year yield for the discount rate, FX for ADRs.
   `uv run ${CLAUDE_SKILL_DIR}/scripts/market.py --coverage` lists the watchlist.
3. yfinance, only for tickers the DB does not cover, when the DB is unreachable, and for
   statements of non-SEC companies. Every market output states which source it used.

Reference files (read when you reach the relevant step):
- `references/framework.md` — what to measure per section, thresholds, sector KPIs, red-flag checklist
- `references/sources.md` — source reliability tiers, where each kind of data lives, non-US exchanges
- `references/report-template.md` — report structure and citation rules

## Step 0 — Set up
1. If `SEC_USER_AGENT` is unset, tell the user once that SEC asks for
   `export SEC_USER_AGENT="Name email"` and continue with the generic default.
   Never put the user's own email into requests yourself.
   If `OCTOPUS_RO_URL` is unset, tell the user once that market data will come from
   yfinance instead of their local DB (`export OCTOPUS_RO_URL="postgresql://octopus_ro:<password>@localhost:5432/data_octopus"`).
   Never write the password into a file.
2. Resolve the company: `python3 ${CLAUDE_SKILL_DIR}/scripts/edgar_client.py resolve "<name or ticker>"`.
   If ambiguous, show the candidates and ask. If it is not on EDGAR (non-US listing
   without ADR), go to "Non-SEC companies" below.
3. Pick 3–5 peers (same industry and business model, similar scale). Use the user's
   peers if given; otherwise choose them and say why in one line. Prefer peers on the
   Data-Octopus watchlist when they are equally relevant; list any good peers that are
   missing from it in the report's data notes so the user can add them.
4. Output folder: `~/Downloads/company-research/<TICKER>-<YYYY-MM-DD>/` (always, regardless of
   the current directory) unless the user names another place. If that folder already exists
   from an earlier run today, reuse it; the scripts overwrite their own files.

## Step 1 — Collect the data (one command)
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/collect.py <TICKER> --out ~/Downloads/company-research/<TICKER>-<date> --peers P1,P2,P3
```
This runs, in parallel: `fundamentals.py` (10y statements, ratios, TTM, quarters,
Piotroski, Altman Z'', automatic flags), `segments.py` (segment / product /
geography / customer-concentration tables from inline XBRL), `market.py`
(price performance, beta, computed vs vendor multiples, valuation history, reverse DCF
discounted at 10y Treasury + 5% ERP, consensus and revisions, surprise record, analyst
targets, short interest, ownership), `insiders.py` (Form 4), `filings.py risk-diff`,
`filings.py sections` (10-K and latest 10-Q text split into business, risk factors,
MD&A …), the filing list with 8-K exhibits, and with `--peers` a fundamentals peer table
(`06_peers.md`) and a vendor valuation peer table (`07_peer_valuation.md`).
Re-run an individual script directly when you need different options.

Read every generated `0X_*.md` file before writing. Note every `WARNING`, `FAILED`,
`⚠ stale`, `STALE`, financial-sector notice, currency-conversion note, "no total
tagged" label and the market data source line: these change what you can claim.

## Step 2 — Verify the numbers
XBRL extraction is reliable but not perfect (concept changes, restatements, debt
approximation). Before using the figures:
- Grep the 10-K full text (`filings/*_full.txt`) for latest-year revenue, net income
  and operating cash flow and confirm they match `01_fundamentals.md`. Fix and note
  any mismatch; the filing wins.
- Check total debt against the balance sheet / debt note.
- If the latest 10-Q or 8-K earnings release is newer than the 10-K, use it for the
  latest quarter and guidance (`filings.py exhibits`, then `filings.py text <Ex-99.1 URL>`).
- If segment members overlap (subtotals, regions containing countries), say so and
  don't sum them.
- Where the computed (SEC) and vendor multiples in `03_market.md` differ by more than
  ~10%, find out why (different period, share count, currency, stale XBRL) before
  quoting either. For ADRs, vendor P/Sales is meaningless (currency mix) and is blanked.
- Vendor consensus and revenue estimates are in the reporting currency, which can
  change over time; sanity-check magnitudes against reported revenue.
- Vendor EPS (trailing P/E, PEG, beat/miss record, EPS estimates) is on Alpha Vantage's own
  adjusted basis, which can match neither GAAP nor the company's non-GAAP figure. Compare one
  quarter against the earnings release and label vendor P/E as "vendor basis" in the report.
- `fundamentals.py`'s total debt is an approximation; replace it with the principal stated in
  the 10-K/10-Q liquidity section (grep "aggregate principal amount of total debt").

## Step 3 — Read the filings for what numbers can't tell you
From `filings/`, read (or grep, for long sections):
- **Business (Item 1)**: products, customers, revenue model, competition, suppliers,
  manufacturing, seasonality, employees.
- **Risk factors (Item 1A)** together with `05_risk_diff.md`: name the new risks.
- **MD&A (Item 7)**: management's explanation of growth and margin changes, liquidity,
  capital return plans, contractual obligations, critical estimates.
- **Supply chain**: `grep -iE "sole source|single source|limited number of suppliers|contract manufactur|foundr|outsourc|tariff|export control"`.
- **Controls (Item 9A)**: any material weakness.
- **Proxy (DEF 14A)** in `full` mode: pay metrics, ownership table, related parties.
For 20-F filers, section splitting is rougher; fall back to grepping the full text.

## Step 4 — Web research for context (full mode)
Use web search for what filings don't contain, following the tiers in
`references/sources.md`:
- Latest earnings call: guidance, management tone, analyst questions.
- Market size and share, competitive moves, pricing (cite the research firm and outlet).
- Last 12 months of material news: regulation, litigation, export controls, M&A,
  leadership changes, product launches. Date every item.
- Sector KPIs from `references/framework.md` §13 that the company reports in
  presentations rather than XBRL.
Prefer Tier 1–2. Never cite a number you could not trace to a source.

## Step 5 — Analyse
Work through `references/framework.md` sections 1–12. For each automatic flag from
the scripts, investigate and record one of: **confirmed** (real issue), **explained**
(benign, with the reason, e.g. negative equity from buybacks), or **unresolved**.
Then:
- Compare against the company's own history and the peer table, not absolute rules.
- Reverse DCF: is implied growth above or below historical FCF/revenue CAGR and
  guidance? This is the core "what is priced in" judgement. State the discount rate
  and that it is the current 10y yield plus a 5% equity risk premium.
- Expectations: estimate revisions (EPS now vs 30/90 days ago, up vs down revisions),
  the beat/miss record, short-interest trend and holder changes describe sentiment.
  Use them as context, never as evidence about the business.
- Derive opportunities and risks **from the data**: each one cites the number that
  supports it (e.g. "Services is 26% of revenue but 45% of gross profit and growing
  13%: mix shift lifts margins").
- Separate facts, management claims, consensus estimates and your inferences.

## Step 6 — Write the report
Write `REPORT.md` in the research folder following `references/report-template.md`.
Rules:
- Every figure has its period and a source tag; label guidance, consensus and inference.
- Every table is followed by a short "So what" that links it to opportunity or risk.
- State data gaps honestly ("not disclosed", "XBRL missing for FY2025, taken from 20-F text").
- Give balanced bull and bear cases; do not issue buy/sell recommendations or price
  targets of your own. End with one line: not investment advice.

Then reply in chat with: the snapshot table, top 3 opportunities and top 3 risks
(each with its number), the reverse-DCF conclusion, data caveats, and the full path to
`REPORT.md` (in `~/Downloads/company-research/…`). Offer to publish it as a shareable page if useful.

## Non-SEC companies
For companies outside EDGAR (e.g. SGX `D05.SI`, HKEX `0700.HK`, LSE `ULVR.L`):
1. `uv run ${CLAUDE_SKILL_DIR}/scripts/market.py <YahooTicker>` for price, multiples, consensus, price history
   (from yfinance; the Data-Octopus watchlist holds US listings only).
2. `uv run ${CLAUDE_SKILL_DIR}/scripts/market.py <YahooTicker> --yf-financials` for ~4 years of statements (Tier 3).
3. Download the latest annual report and results from the exchange's announcement
   site or the IR page (see `references/sources.md`), extract segment, geography,
   debt and risk information from it, and reconcile the Yahoo figures against it.
4. Compute the same ratios by hand from the annual report where the scripts can't,
   and say which figures are from Yahoo versus the report.

## Troubleshooting
- `403` from SEC: set `SEC_USER_AGENT`. `429`: the client already backs off; retry later.
- Market file says "Yahoo Finance" for a watchlist ticker: `OCTOPUS_RO_URL` is unset or the DB
  is down (the stderr note says which). A `WARNING: latest price in the DB is from …` means the
  loader has not run recently: say so in the report or rerun with `--source yahoo` to cross-check.
- Cache lives in `~/.cache/company-research` (filings 30 days, facts 24h). Delete it to force a refresh.
- Banks/insurers/REITs: ignore gross margin, EBITDA, working-capital and Altman outputs;
  use the sector KPIs in the framework instead.
- Recent IPOs have little history: say so and lean on the S-1/424B4 (`filings.py list --forms S-1,424B4`).
