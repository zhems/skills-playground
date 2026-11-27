# Reading Data-Octopus data with SQL

Read-only access to the Data-Octopus PostgreSQL database, for other projects and agents.

## Connect

```
postgresql://octopus_ro:hellozhems@localhost:5432/data_octopus
```

Ask the owner for the password and keep it in your project's own `.env` (e.g. `OCTOPUS_RO_URL`); never commit it. The role can read everything and write nothing. Any SQL client works (`psql`, SQLAlchemy, `pandas.read_sql`).

## 1. Start from the registry

`octopus.data_sources` lists every curated dataset and tells you how to query it:

```sql
SELECT data_source_id, db_schema, data_table, key_table, key_cols, date_col, value_cols
FROM octopus.data_sources
WHERE NOT is_disabled;
```

- `key_cols`: the columns that identify a series (e.g. `{symbol}`, `{name,interval,maturity,unit}`).
- `date_col`: the time axis. `value_cols`: the numbers.
- Schemas are per source: `alpha_vantage`, `data_gov_sg`, `preqin`, `sec_edgar`.
- Tables not in the registry (e.g. `data_gov_sg.raw_cea_property_transactions`, `sec_edgar.*`) are raw. Inspect them through `information_schema.columns` first.
- Other tables in `octopus` belong to a separate app. Ignore them.

## 2. Join key and data tables

When `key_table` is set (most datasets), the identifying columns live **only** in the key table. The data table holds just a `key_id`, so join on it:

```sql
SELECT symbol, fiscal_date_ending, reported_eps
FROM alpha_vantage.stock_symbols
JOIN alpha_vantage.quarterly_earnings USING (key_id)
WHERE symbol = 'AAPL' AND valid_to IS NULL
ORDER BY fiscal_date_ending;
```

When `key_table` is NULL (e.g. `preqin.*`), query `data_table` alone.

## 3. Always filter `valid_to IS NULL`

Data tables keep full history (SCD Type 2). Each change adds a new row version.

- Current data: `WHERE valid_to IS NULL`. Leave it out and you get duplicate rows for the same date.
- As of time `t`: `valid_from <= t AND (valid_to IS NULL OR valid_to > t)`.
- Key tables have no `valid_to`, so the filter is never ambiguous after a join.

More query examples: [query_patterns.md](query_patterns.md).
