"""Read-only client for the local Data-Octopus PostgreSQL database (Alpha Vantage,
FINRA and macro data downloaded to this machine). Used by market.py.

Connection string comes from the OCTOPUS_RO_URL environment variable, e.g.
    export OCTOPUS_RO_URL="postgresql://octopus_ro:<password>@localhost:5432/data_octopus"
If it is unset or the database is unreachable, connect() returns None and callers
fall back to yfinance.

Conventions (see the Data-Octopus consuming-data doc):
- Always filter valid_to IS NULL (SCD Type 2 history).
- Key tables: alpha_vantage.stock_symbols (symbol -> key_id). company_overview and
  institutional_ownership are keyed by symbol directly.
- percent_insiders, percent_institutions, total_institutional_ownership_percentage
  are percentages (74.0 = 74%); margins, returns and dividend_yield are ratios.

Requires psycopg (declared in market.py's inline dependencies).
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from decimal import Decimal


def _f(v):
    return float(v) if isinstance(v, Decimal) else v


class Octopus:
    def __init__(self, conn):
        self.conn = conn
        self._keys: dict[str, int | None] = {}

    def q(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [c.name for c in cur.description]
            return [{k: _f(v) for k, v in zip(cols, row)} for row in cur.fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------ symbols
    def symbol(self, ticker: str) -> str | None:
        """Return the DB's spelling of the ticker (BRK.B vs BRK-B), or None if not covered."""
        t = ticker.upper()
        if t not in self._keys:
            row = self.one("SELECT symbol, key_id FROM alpha_vantage.stock_symbols WHERE upper(symbol) = ANY(%s)",
                           ([t, t.replace("-", "."), t.replace(".", "-")],))
            self._keys[t] = row
        row = self._keys[t]
        return row["symbol"] if row else None

    def key(self, ticker: str) -> int | None:
        return self._keys[ticker.upper()]["key_id"] if self.symbol(ticker) else None

    def covered(self) -> list[str]:
        return [r["symbol"] for r in self.q("SELECT symbol FROM alpha_vantage.stock_symbols ORDER BY symbol")]

    # ------------------------------------------------------------ per stock
    def overview(self, ticker: str) -> dict | None:
        sym = self.symbol(ticker)
        if not sym:
            return None
        return self.one("SELECT *, valid_from::date AS as_of FROM alpha_vantage.company_overview "
                        "WHERE symbol = %s AND valid_to IS NULL", (sym,))

    def prices(self, ticker: str) -> list[dict]:
        """Daily rows oldest first: trading_date, close (raw), adjusted_close (splits+dividends), split_coefficient."""
        return self.q("SELECT trading_date, close, adjusted_close, split_coefficient, dividend_amount "
                      "FROM alpha_vantage.daily_prices_adjusted WHERE key_id = %s AND valid_to IS NULL "
                      "ORDER BY trading_date", (self.key(ticker),))

    def trailing_pe(self, ticker: str) -> dict | None:
        return self.one("SELECT trading_date, stock_price, reported_eps, pe_ratio, eps_currency, fx_rate "
                        "FROM alpha_vantage.daily_pe_ratios WHERE key_id = %s AND valid_to IS NULL "
                        "ORDER BY trading_date DESC LIMIT 1", (self.key(ticker),))

    def estimates(self, ticker: str) -> list[dict]:
        """Consensus for the current and future fiscal periods (reporting currency)."""
        return self.q("SELECT fiscal_date_ending, horizon, eps_estimate_average, eps_estimate_low, eps_estimate_high, "
                      "eps_estimate_analyst_count, eps_estimate_average_30_days_ago, eps_estimate_average_90_days_ago, "
                      "eps_estimate_revision_up_trailing_30_days, eps_estimate_revision_down_trailing_30_days, "
                      "revenue_estimate_average, revenue_estimate_low, revenue_estimate_high, revenue_estimate_analyst_count "
                      "FROM alpha_vantage.earnings_estimates WHERE key_id = %s AND valid_to IS NULL "
                      "AND fiscal_date_ending >= CURRENT_DATE - 45 ORDER BY horizon DESC, fiscal_date_ending",
                      (self.key(ticker),))

    def growth_estimates(self, ticker: str) -> list[dict]:
        return self.q("SELECT fiscal_date_ending, eps_estimate_average, prior_eps, prior_eps_source, eps_growth, "
                      "revenue_estimate_average, prior_revenue, revenue_growth, eps_estimate_analyst_count "
                      "FROM alpha_vantage.earnings_growth_estimates WHERE key_id = %s AND valid_to IS NULL "
                      "ORDER BY fiscal_date_ending", (self.key(ticker),))

    def surprises(self, ticker: str, n: int = 8) -> list[dict]:
        return self.q("SELECT fiscal_date_ending, reported_date, report_time, reported_eps, estimated_eps, surprise_percentage "
                      "FROM alpha_vantage.quarterly_earnings WHERE key_id = %s AND valid_to IS NULL "
                      "AND reported_eps IS NOT NULL ORDER BY fiscal_date_ending DESC LIMIT %s", (self.key(ticker), n))

    def next_earnings(self, ticker: str) -> dict | None:
        """None means no date announced yet (not a failed load)."""
        return self.one("SELECT report_date, fiscal_date_ending, estimate, currency, time_of_the_day "
                        "FROM alpha_vantage.earnings_calendar WHERE key_id = %s AND valid_to IS NULL "
                        "AND report_date >= CURRENT_DATE ORDER BY report_date LIMIT 1", (self.key(ticker),))

    def short_interest(self, ticker: str, n: int = 6) -> list[dict]:
        sym = self.symbol(ticker) or ticker.upper()
        return self.q("SELECT settlement_date, short_interest, change_percent, average_daily_volume, days_to_cover "
                      "FROM finra.short_interest_by_symbol WHERE symbol = %s AND valid_to IS NULL "
                      "ORDER BY settlement_date DESC LIMIT %s", (sym, n))

    def institutional_ownership(self, ticker: str) -> dict | None:
        return self.one("SELECT * FROM alpha_vantage.institutional_ownership WHERE symbol = %s AND valid_to IS NULL",
                        (self.symbol(ticker),))

    def top_holders(self, ticker: str, n: int = 10) -> list[dict]:
        return self.q("SELECT * FROM (SELECT DISTINCT ON (holder_name) holder_name, last_reported, shares_held, "
                      "shares_changed, shares_changed_percentage, change_type FROM alpha_vantage.institutional_holders "
                      "WHERE key_id = %s AND valid_to IS NULL ORDER BY holder_name, last_reported DESC) h "
                      "ORDER BY shares_held DESC NULLS LAST LIMIT %s", (self.key(ticker), n))

    # ------------------------------------------------------------ macro / fx
    def indicator(self, name: str) -> dict | None:
        return self.one("SELECT d.date, d.value FROM alpha_vantage.economic_indicator_keys k "
                        "JOIN alpha_vantage.economic_indicator_data d USING (key_id) "
                        "WHERE k.name = %s AND d.valid_to IS NULL AND d.value IS NOT NULL ORDER BY d.date DESC LIMIT 1", (name,))

    def risk_free_10y(self) -> dict | None:
        return self.indicator("10-Year Treasury Constant Maturity Rate")

    def usd_to(self, ccy: str) -> dict | None:
        """Latest USD->ccy rate (units of ccy per 1 USD)."""
        if ccy.upper() == "USD":
            return {"trading_date": date.today(), "close": 1.0}
        return self.one("SELECT d.trading_date, d.close FROM alpha_vantage.forex_symbols f "
                        "JOIN alpha_vantage.forex_daily d USING (key_id) WHERE f.from_symbol = 'USD' AND f.to_symbol = %s "
                        "AND d.valid_to IS NULL ORDER BY d.trading_date DESC LIMIT 1", (ccy.upper(),))


def connect() -> Octopus | None:
    url = os.environ.get("OCTOPUS_RO_URL", "").strip()
    if not url:
        print("note: OCTOPUS_RO_URL not set; using yfinance instead of the local Data-Octopus DB.", file=sys.stderr)
        return None
    try:
        import psycopg
        conn = psycopg.connect(url, connect_timeout=5, autocommit=True)  # role is read-only
        return Octopus(conn)
    except Exception as e:  # DB down, bad credentials, psycopg missing
        print(f"note: Data-Octopus DB unavailable ({type(e).__name__}: {str(e)[:120]}); falling back to yfinance.",
              file=sys.stderr)
        return None


def stale(d: date | None, days: int = 4) -> bool:
    return d is None or (date.today() - d) > timedelta(days=days)
