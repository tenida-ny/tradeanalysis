# Trading Performance Analyzer

A Streamlit app that turns a raw Fidelity **Accounts History** CSV export into daily and monthly trading performance dashboards — no pre-calculated P/L column required.

## What it does

1. **Upload** a Fidelity Accounts History CSV (Step 1). Preamble lines, footer rows, and minor column-name variations are handled automatically via fuzzy column detection.
2. **Analyze** (Step 2): pick an account, resolve any sells whose buys predate the file window, and view:
   - a **Monthly dashboard** replicating the spreadsheet Dashboard tab (Trade Count, Average Gain/Loss $ and %, WIN%, Largest Gain/Loss, capital per trade, win/loss counts, gain-loss ratio, Total Gain/Loss, Net P/L, cumulative net) with bar and cumulative charts
   - a **Daily summary** (trades, wins, losses, net, cumulative)
   - a **Trade detail** table (per round trip: direction, capital, proceeds, P/L $ and %, holding days)
   - a **Data quality** panel (excluded options, unmatched sells, open positions, split adjustments, ignored cash activity)

## Methodology

- **FIFO lot matching** per symbol, chronological
- **Partial and full sells** supported; a buy can close across multiple sells and vice versa
- **Short selling** supported: `SOLD SHORT` opens short lots, buys / `BUY TO COVER` close them FIFO
- **Same-day fills** of the same symbol/direction close are aggregated into a single round-trip trade (prevents double counting partial fills)
- Each completed trade is assigned to the **month its close occurred**
- **Net Amount ($)** used throughout — already inclusive of commissions and fees
- **Stock splits** (share distributions with no cash movement) scale open lots automatically
- **Options** are identified and excluded from equity metrics, but listed in the data-quality panel
- **Breakeven trades** (exactly $0) count in Trade Count but not in wins or losses
- **Sells without in-file cost basis** are surfaced in the UI; per ticker you choose to exclude them or enter a cost basis per share

### Notes on dashboard formulas

Three cells in the source spreadsheet were adjusted deliberately:

- *Average Margin per trade* is implemented as **average capital deployed per trade** (mean of open value)
- *Avg Gain/Win Trade* = **Total Gain ÷ win count** (symmetric with Avg Loss/Loss Trade)
- *WIN% and Batting Average* are mathematically identical; both are kept for parity with the sheet

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Run tests:

```bash
python test_engine.py
```

## Deploy to Streamlit Community Cloud

1. Push this folder to a GitHub repo (public or private):
   ```bash
   git init && git add . && git commit -m "Trading analyzer"
   git branch -M main
   git remote add origin https://github.com/<you>/trading-analyzer.git
   git push -u origin main
   ```
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with GitHub, click **Create app**.
3. Pick the repo, branch `main`, main file path `app.py`, and deploy.
4. Every push to `main` redeploys automatically.

## File structure

```
app.py               Streamlit UI (upload → resolve → dashboards)
fidelity_parser.py   CSV loading, column auto-detection, row classification
matcher.py           FIFO lot-matching engine (longs, shorts, splits)
metrics.py           Monthly/daily dashboard math
test_engine.py       Regression tests for the matching engine
requirements.txt     streamlit, pandas, numpy
```
