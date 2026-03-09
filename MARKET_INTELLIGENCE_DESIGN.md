# Nifty 500 Daily Market Intelligence System

## Objective
Build a reliable daily market intelligence workflow for investors and portfolio managers using Nifty 500 EOD data, and deliver it by Gmail every day at 7:00 AM.

## Data Sources
- Constituents: NSE Indices CSV (`ind_nifty500list.csv`)
- OHLCV: Yahoo Finance via `yfinance`
- Metadata: Yahoo Finance company profile fields (`marketCap`, `sector`, `industry`)

## Daily Data Fields
The pipeline captures, per stock:
- Open, High, Low, Close
- Previous close
- Daily % change
- Volume
- 20-day average volume
- Unusual volume ratio (`volume / avg_volume_20`)
- 20-day volatility (% daily return std-dev)
- Move-vs-volatility score (`abs(daily_pct_change) / volatility_20d_pct`)
- Market cap
- Sector / industry

## Analytics Computed
- Top 10 gainers / losers
- Sector performance ranking (mean daily return per sector)
- Sector rotation: momentum delta vs prior 5-day average sector return
- Breadth:
  - Advancers vs Decliners
  - % stocks above previous close
- Unusual volume detection (`>= 2x` 20-day average)
- Largest moves relative to historical volatility

## Generated Report Sections
- Market Summary
- Top Movers
- Sector Performance
- Notable Activity
- Key Observations (3-5 investor-focused insights)

## Storage Design
Local project storage:
- `data/market_intelligence/nifty500_metadata.csv` (metadata cache)
- `data/market_intelligence/daily_snapshots.csv` (historical master)
- `data/market_intelligence/nifty500_snapshot_YYYY-MM-DD.csv` (daily snapshot)
- `reports/nifty500_market_report_YYYY-MM-DD.md` (markdown report)
- `reports/nifty500_market_report_YYYY-MM-DD.html` (email-ready HTML)

## Email Delivery (Gmail)
Required environment variables:
- `GMAIL_SENDER_EMAIL` (example: `aaravgupta1009@gmail.com`)
- `GMAIL_APP_PASSWORD` (Google App Password)

Manual local run:
```powershell
.\venv\Scripts\python.exe .\daily_market_intelligence.py --email-to "aaravgupta1009@gmail.com"
```

## Automation at 7:00 AM Daily (GitHub Actions)
This repo includes workflow:
- `.github/workflows/daily_market_report.yml`

Schedule:
- `30 1 * * *` (UTC) = **7:00 AM IST** daily

Required GitHub repository secrets:
- `GMAIL_SENDER_EMAIL` (set to `aaravgupta1009@gmail.com`)
- `GMAIL_APP_PASSWORD` (Google App Password)

Manual trigger:
- Actions -> **Nifty 500 Daily Market Email** -> **Run workflow**

Workflow artifacts:
- Markdown + HTML reports
- Daily CSV snapshots

## Operational Notes
- If metadata for some names fails in one run, cache/backfill keeps system operational.
- If market coverage drops (missing prices), report still generates with available data.
- Recommended hardening for production:
  - retry/backoff
  - centralized logging
  - email failure alerts
  - universe coverage thresholds
