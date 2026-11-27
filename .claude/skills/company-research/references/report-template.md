# Report template

Write `REPORT.md` in the research folder with these sections, in this order. Keep
tables compact (latest 5 fiscal years unless a longer trend matters). After each
table, add 1–3 "**So what:**" bullets that tie the numbers to an opportunity or a
risk. Every figure carries its period and a source tag.

Source tags: `[XBRL]` (fundamentals.py), `[10-K FY25 p.X / Item 7]`, `[10-Q Q3 FY26]`,
`[8-K 2026-10-30 Ex99.1]`, `[DEF 14A 2026]`, `[Form 4]`, `[AV YYYY-MM-DD]` (Data-Octopus/Alpha Vantage), `[FINRA YYYY-MM-DD]`, `[Yahoo YYYY-MM-DD]`,
`[Reuters YYYY-MM-DD](url)`. Label anything that is not a reported fact:
*(guidance)*, *(consensus)*, *(estimate)*, *(my inference)*.

---

```markdown
# <Company> (<TICKER>) — investor breakdown
As of <date> · Price <x> <ccy> · Market cap <x> · Latest filing: <form, period>
Depth: <quick|full> · Not investment advice.

## 1. Snapshot
| Metric | Value | Trend / context |
(Revenue TTM and growth, gross/operating/FCF margin, ROIC, net debt/EBITDA,
P/E, EV/EBIT, FCF yield, reverse-DCF implied growth, Piotroski, share-count change)

**Thesis in three lines.** What the business is, what the market is pricing in, and
the single most important thing that decides whether that is right.

**Top opportunities** (3–5, each backed by a number) · **Top risks** (3–5, each backed by a number)

## 2. Business model
How the company makes money, in plain language. Customers, pricing model, recurring vs
one-off. Revenue by segment / product / geography table with % share and YoY.
Segment operating income and margin. Customer concentration.

## 3. Growth
Revenue/EPS/FCF CAGR table (3/5/10y). Quarterly YoY trend (acceleration or not).
Organic vs acquired. Guidance and consensus vs history. TAM and market share
(sourced). Growth drivers named by management and whether the numbers support them.

## 4. Profitability
Margin trend table. Operating leverage. Segment margins. Peer comparison.

## 5. Cash flow and earnings quality
OCF, capex, FCF, SBC, FCF−SBC; cash conversion; working capital days; accruals;
Piotroski. GAAP vs non-GAAP gap from the latest earnings release.

## 6. Balance sheet and liquidity
Cash, debt, net debt/EBITDA, coverage, maturities (from debt note), leases and
purchase obligations, goodwill %, Altman Z''.

## 7. Capital allocation
ROIC vs ~9% cost of capital, share count trend, buybacks/dividends vs FCF, M&A,
buyback timing vs valuation history. What management is paid to optimise (DEF 14A).

## 8. Competitive position
Peer table (fundamentals.py --peers). Evidence of moat or erosion. Market share.

## 9. Supply chain and operating dependencies
Key suppliers / sole sources, manufacturing geography, customer concentration,
inventory trend, tariff / export-control exposure. Quantify.

## 10. Management, ownership and insiders
Insider buying/selling (Form 4) with 10b5-1 context, insider and institutional
ownership, governance notes, leadership changes.

## 11. Valuation
Multiples now vs own history vs peers. Reverse DCF table and what it implies vs
historical growth. Simple bear/base/bull framing: for each, the growth and margin
assumptions and the resulting value range (state assumptions explicitly).

## 12. Risks
(a) Quantitative flags from the scripts, each with the number and a one-line
investigation result (confirmed / explained / unresolved).
(b) New or expanded risk factors this year (risk-diff).
(c) Other material risks: regulatory, legal, geopolitical, technology, key-person.
Rank by likelihood × impact.

## 13. Opportunities and catalysts
Each tied to evidence (segment growth, margin trend, new product disclosed, capacity
additions, buybacks at low valuation). Dated catalysts: earnings date, product
launches, regulatory decisions, debt maturities.

## 14. What to monitor
5–8 KPIs with the current value and a "thesis breaker" threshold, e.g.
"Data-center revenue growth — currently +45% YoY; below 15% for two quarters would
break the growth case." Plus open questions the data could not answer.

## 15. Sources and data notes
List of filings and articles used (with URLs and dates). Data caveats: XBRL gaps,
approximated debt, currency, stale data warnings, fields not verified.
```
