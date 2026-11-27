# Analysis framework

What to look at, why it matters to an investor, how to measure it, and where the
number comes from. "Script" means the field is already computed by `scripts/`.
Thresholds are rules of thumb that start an investigation; they are not verdicts.
Compare against the company's own history and its peers before judging.

## Contents
1. Business model and revenue mix
2. Growth
3. Profitability and unit economics
4. Cash flow and earnings quality
5. Balance sheet and liquidity
6. Capital allocation and returns
7. Competitive position (moat)
8. Supply chain and operating dependencies
9. Management, governance and insiders
10. Valuation
11. Market sentiment
12. Risk checklist (qualitative red flags)
13. Sector-specific KPIs

---

## 1. Business model and revenue mix
Questions: What does the company sell, to whom, and how does it get paid (one-off,
recurring, usage, transaction take-rate, licensing, advertising)? Which streams
drive growth and which drive profit?

| Measure | Source |
|---|---|
| Revenue by segment, product, geography with % and YoY | `segments.py` (inline XBRL); segment note in 10-K |
| Operating income by segment → which segment earns the profit | `segments.py` |
| Customer concentration (% of revenue from top customers) | `segments.py` (Concentration %); 10-K Item 1 / Item 1A / revenue note ("one customer accounted for…") |
| Recurring share of revenue, backlog, RPO, deferred revenue | `fundamentals.py` (RPO, deferred revenue); MD&A |
| Pricing vs volume contribution to growth | MD&A, earnings call |

Interpretation: a segment growing fast but losing money changes the mix toward lower
margins; one customer > 10% is a disclosed concentration risk; a single region > 30%
(e.g. China) is a geopolitical exposure worth stating as a number.

## 2. Growth
| Measure | Source |
|---|---|
| Revenue, operating income, EPS, FCF CAGR (3/5/10y) | `fundamentals.py` |
| Recent quarterly YoY growth (acceleration or deceleration) | `fundamentals.py` recent quarters |
| Organic vs acquired growth | Acquisitions line (script) + MD&A ("excluding acquisitions", "constant currency") |
| Guidance and consensus | Latest earnings release (8-K Ex 99.1), call transcript; `market.py` consensus |
| TAM / market growth and share | Company investor-day decks; industry bodies; reputable research quoted in Reuters/Bloomberg |

Interpretation: EPS growing much faster than revenue is either operating leverage
(good, but finite) or buybacks/financial engineering. Growth from acquisitions
deserves a lower multiple than organic growth. Decelerating quarterly growth often
matters more to the stock than the annual CAGR.

## 3. Profitability and unit economics
| Measure | Threshold / note | Source |
|---|---|---|
| Gross margin trend | ±2pp YoY is material (script flags it) | script |
| Operating margin trend, operating leverage | Opex growing slower than revenue = leverage | script |
| R&D and SG&A % revenue | Compare with peers | script |
| Effective tax rate | Unusually low rates may not persist | script |
| Segment margins | Which business funds the others | `segments.py` |
| Unit economics: CAC/LTV, ARPU, same-store sales, take-rate, utilization | Company-reported KPIs | MD&A, investor presentation |

## 4. Cash flow and earnings quality
| Measure | Threshold | Source |
|---|---|---|
| FCF = OCF − capex; FCF margin | > 15% strong for non-capital-intensive businesses | script |
| FCF minus SBC | SBC is a real cost; compare FCF yield before and after | script |
| OCF / net income (cash conversion) | 3-year average < 0.8 = flag | script |
| Accruals ratio (NI − OCF) / avg assets | > 10% = flag | script |
| DSO, DIO, DPO, cash conversion cycle | DSO/DIO rising > 15% faster than sales = flag | script |
| Capex / D&A | < 0.7 for long = underinvesting; > 2 = growth capex phase | script |
| Piotroski F-score | 0–3 weak, 7–9 strong (designed for value stocks; noisy for hyper-growth) | script |
| Non-GAAP vs GAAP gap | Large or growing adjustments ("one-time" every year) = flag | Earnings release reconciliation |

## 5. Balance sheet and liquidity
| Measure | Threshold | Source |
|---|---|---|
| Net debt, net debt / EBITDA | > 3x = flag (lower for cyclicals) | script |
| Interest coverage (EBIT / interest) | < 4x = flag | script |
| Current ratio | < 1 = check (normal for some retailers with negative working capital) | script |
| Debt maturity ladder, fixed vs floating, covenants | Big maturities in next 2 years during high rates = refinancing risk | Debt note in 10-K |
| Goodwill + intangibles % assets | > 40% = acquisition-heavy, impairment risk | script |
| Altman Z'' | < 1.1 distress (not meaningful for financials) | script |
| Off-balance-sheet: leases, purchase obligations, guarantees, pensions | Material commitments | Contractual obligations table in MD&A, notes |

## 6. Capital allocation and returns
| Measure | Threshold | Source |
|---|---|---|
| ROIC vs cost of capital (~8–10%) | ROIC > 15% sustained suggests a moat; < WACC = growth destroys value | script |
| ROE (watch leverage-inflated ROE) | Compare with ROIC | script |
| Diluted share count trend | > 2%/yr dilution = flag | script |
| Buybacks + dividends vs FCF | > 110% = funded by balance sheet | script |
| Buyback price discipline | Buying heavily at peak multiples is value-destructive | `market.py` valuation history vs buyback years |
| M&A track record | Impairments after deals = poor | Notes, news |

## 7. Competitive position (moat)
Evidence, not adjectives: sustained high ROIC and gross margins vs peers, market
share trend, pricing power (price increases without volume loss), switching costs
(retention / net revenue retention), network effects, scale cost advantage,
regulatory licences, patents expiring. Run `fundamentals.py` with 3–5 peers for the
comparison table; name the sources for market-share numbers.

## 8. Supply chain and operating dependencies
Where to find it:
- 10-K Item 1 ("Manufacturing", "Suppliers", "Raw materials") and Item 1A: grep the
  full text for `sole source|single source|limited number of suppliers|contract manufactur|foundr|outsourc|raw material|tariff|export control|concentrat`.
- Geographic concentration of manufacturing / long-lived assets: `segments.py` (Long-lived assets by geography).
- Purchase obligations and prepayments to suppliers: MD&A contractual obligations; notes.
- Inventory build vs sales (DIO): script.
- Reverse lookup: large suppliers' and customers' own 10-K/20-F disclosures (e.g. a
  foundry's "Customer A = 22% of revenue"), company-published supplier lists,
  ImportYeti (US bill-of-lading data) for physical goods.
Quantify where possible: % of COGS from top suppliers, % of production in one country,
number of qualified suppliers for critical inputs, tariff exposure ($ or % of COGS).

## 9. Management, governance and insiders
| Item | Source |
|---|---|
| Open-market insider buys/sells, 10b5-1 plans | `insiders.py` (Form 4) |
| Insider and institutional ownership % | DEF 14A beneficial ownership table; `market.py` |
| Executive pay metrics (what is management paid to maximise? revenue, EPS, TSR, ROIC?) | DEF 14A Compensation Discussion & Analysis |
| Dual-class shares, controlling holders, related-party transactions | DEF 14A, 10-K Item 13 |
| CEO/CFO tenure and turnover, auditor changes | 8-K Item 5.02 / 4.01, news |
| Material weaknesses in internal control | 10-K Item 9A (`filings.py sections` → controls) |

## 10. Valuation
`market.py` computes multiples from SEC fundamentals and price, shows the vendor's
multiples beside them, the history of P/E, P/S, P/FCF at each fiscal year end, and a
reverse DCF discounted at the current 10-year Treasury yield plus a 5% equity risk
premium (override with `--discount` or `--erp`). `market.py --compare` gives a vendor
valuation table across peers (forward P/E, PEG, EV/EBITDA, short interest, target upside).
- Compare current multiples to the company's own 5–10 year range and to peers.
- Reverse DCF: the FCF growth the price implies. Compare with historical FCF and
  revenue CAGR and with guidance. Implied growth far above anything the company has
  delivered = thin margin of safety; implied growth below a credible base case = potential opportunity.
- FCF yield after SBC vs 10-year Treasury yield is a quick sanity check.
- For cyclicals, use mid-cycle margins, not peak. For unprofitable growth companies use
  EV/Sales vs growth and gross margin, and a path-to-profitability check.

## 11. Market sentiment (lower reliability; context only)
From `market.py`: consensus estimates and their 30/90-day revisions (up vs down
revisions), the last 8 quarters of EPS beats/misses, mean price target and rating
distribution, FINRA short interest trend (% float, days to cover), institutional
ownership and holders increasing vs decreasing, top holders' changes. Rising estimates
plus consistent beats = expectations being met; falling estimates into an earnings date
or rising short interest = the market is getting more sceptical. Use to understand
expectations, never as evidence about the business.

## 12. Risk checklist (qualitative red flags)
- Auditor change, going-concern language, restatement (8-K Item 4.02), late filing (NT 10-K)
- Material weakness in internal controls (Item 9A)
- Frequent "one-time" restructuring charges; widening non-GAAP adjustments
- Revenue recognition changes; channel inventory build; extended payment terms
- Related-party transactions; pledged founder shares; complex structures (VIEs)
- Customer or supplier > 10%; single-country manufacturing; export-control exposure
- Litigation, regulatory investigations (Item 3, legal note, news)
- New risk factors this year (`filings.py risk-diff`): these are what management's lawyers
  newly think is material. Name them explicitly in the report.
- CFO departure, insider selling clusters outside 10b5-1 plans
- Debt maturities and covenant headroom; floating-rate exposure

## 13. Sector-specific KPIs
Standard ratios mislead in some sectors (`fundamentals.py` warns for SIC 6000–6799).

| Sector | KPIs to find (MD&A, supplements, investor presentations) |
|---|---|
| Software / SaaS | ARR, net revenue retention, gross retention, RPO/cRPO, Rule of 40 (growth % + FCF margin %), CAC payback, SBC % revenue |
| Semiconductors | Book-to-bill, inventory days (customer and own), node mix, capacity utilisation, top-customer %, export-control exposure |
| Retail / consumer | Same-store sales (traffic vs ticket), sales per sq ft, inventory turns, e-commerce %, membership renewal rate |
| Banks | Net interest margin, CET1 ratio, efficiency ratio, NPL ratio, net charge-offs, loan/deposit ratio, deposit beta, ROTCE, P/TBV |
| Insurance | Combined ratio, reserve development, float growth, investment yield, book value per share growth |
| REITs | FFO / AFFO per share, occupancy, same-property NOI, lease expiry ladder, debt/EBITDA, cap rates |
| Energy / materials | Production volumes, realised prices vs benchmark, reserve life, breakeven cost per unit, capex per unit |
| Pharma / biotech | Pipeline phase and readout dates, patent expiry cliff, revenue concentration by drug, cash runway (cash / annual burn) |
| Internet / platforms | DAU/MAU, ARPU by region, take rate, GMV, paid users, engagement |
| Industrials | Backlog and book-to-bill, organic growth, incremental margins, aftermarket % of revenue |
| Airlines / transport | RASM/CASM, load factor, fuel hedging, fleet age |
| Telecom / utilities | ARPU, churn, capex intensity, regulated rate base growth, allowed ROE |
