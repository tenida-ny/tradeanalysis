"""Trading Performance Analyzer — Streamlit app.

Step 1: upload a Fidelity Accounts History CSV
Step 2: pick the account, resolve any unmatched sells, and view
        daily + monthly performance dashboards.
"""

import pandas as pd
import streamlit as st

from fidelity_parser import parse_fidelity_csv
from matcher import match_trades
from metrics import monthly_dashboard, daily_summary

st.set_page_config(page_title="Trading Performance Analyzer", layout="wide")
st.title("📈 Trading Performance Analyzer")
st.caption(
    "FIFO lot matching · trades assigned to close month · net amounts "
    "(fee-inclusive) · options excluded from equity metrics"
)

# ---------------------------------------------------------------- Step 1
st.header("Step 1 — Upload transactions CSV")
uploaded = st.file_uploader(
    "Fidelity 'Accounts History' export (.csv)", type=["csv"],
    help="Preamble lines and footer rows are handled automatically. "
         "Column names are auto-detected, so minor format variations are fine.",
)

if not uploaded:
    st.info("Upload a CSV to begin.")
    st.stop()

try:
    parsed = parse_fidelity_csv(uploaded.getvalue())
except ValueError as exc:
    st.error(f"Could not parse this file: {exc}")
    st.stop()

for w in parsed.warnings:
    st.caption(f"ℹ️ {w}")

# ---------------------------------------------------------------- Account
accounts = parsed.accounts or ["All"]
account = st.selectbox("Account", accounts, index=0)
rows = parsed.trades[parsed.trades["account"] == account] \
    if parsed.accounts else parsed.trades
splits = parsed.splits[parsed.splits["account"] == account] \
    if parsed.accounts and len(parsed.splits) else parsed.splits

equity_rows = rows[~rows["is_option"]]
option_rows = rows[rows["is_option"]]

# ------------------------------------------------ Unmatched-sell resolution
# First pass to discover unmatched sells for this account
probe = match_trades(equity_rows, splits)
pending = probe.unmatched_sells
pending_syms = sorted(pending["symbol"].unique()) if len(pending) else []

manual_basis: dict[str, float] = {}
excluded: set[str] = set()

if pending_syms:
    st.header("Resolve sells without a cost basis in this file")
    st.warning(
        f"{len(pending_syms)} ticker(s) have sells whose buys predate this "
        "file's window. Choose how to handle each — excluded tickers are "
        "left out of matched P/L (they'll be listed in the data-quality "
        "panel); or enter the cost basis per share to include them."
    )
    for sym in pending_syms:
        qty = pending.loc[pending["symbol"] == sym, "qty"].sum()
        c1, c2 = st.columns([2, 1])
        with c1:
            choice = st.radio(
                f"{sym} — {qty:g} unmatched sold shares",
                ["Exclude from P/L", "Enter cost basis per share"],
                key=f"choice_{sym}", horizontal=True,
            )
        if choice == "Enter cost basis per share":
            with c2:
                basis = st.number_input(
                    f"{sym} cost basis ($/share)", min_value=0.0,
                    step=0.01, format="%.4f", key=f"basis_{sym}",
                )
            if basis > 0:
                manual_basis[sym] = basis
            else:
                excluded.add(sym)  # until a basis is entered
        else:
            excluded.add(sym)

# ---------------------------------------------------------------- Matching
result = match_trades(equity_rows, splits,
                      manual_basis=manual_basis, excluded_symbols=excluded)
trades = result.trades

# ---------------------------------------------------------------- Step 2
st.header("Step 2 — Performance analysis")

if trades.empty:
    st.info("No completed round-trip equity trades found for this account.")
else:
    total_net = trades["pl"].sum()
    wins = int((trades["pl"] > 0).sum())
    losses = int((trades["pl"] < 0).sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Completed trades", len(trades))
    c2.metric("Net P/L", f"${total_net:,.2f}")
    c3.metric("Win rate", f"{wins / len(trades):.0%}" if len(trades) else "—")
    c4.metric("W / L", f"{wins} / {losses}")

    tab_month, tab_day, tab_quality = st.tabs(
        ["Monthly dashboard", "Daily summary", "Data quality"]
    )

    with tab_month:
        years = sorted(trades["close_date"].dt.year.unique())
        year = years[-1] if len(years) == 1 else st.selectbox(
            "Year", years, index=len(years) - 1)
        mdf = monthly_dashboard(trades, int(year))

        def _money(v):
            if pd.isna(v):
                return "—"
            return f"$ ({abs(v):,.2f})" if v < 0 else f"$ {v:,.2f}"

        def _num(v):
            return "—" if pd.isna(v) else f"{v:,.2f}"

        def _pct(v):
            return "—" if pd.isna(v) else f"{v:.0%}"

        def _int(v):
            return "0" if pd.isna(v) else f"{int(v)}"

        fmt = {
            "Trade Count": _int, "No of Win Trades": _int,
            "No of Loss Trades": _int,
            "Average Gain": _money, "Avg Loss": _money,
            "Avg Gain/Win Trade": _money, "Avg Loss/Loss Trade": _money,
            "Average Margin per trade": _money,
            "Largest Gain": _num, "Largest Loss": _num,
            "Gain Loss Ratio": _num,
            "Total Loss": _num, "Total Gain": _num,
            "Net P/L": _num, "Cumulative Net P/L": _num,
            "WIN%": _pct, "Batting Average": _pct,
            "Average Gain%": _pct, "Average Loss %": _pct,
        }
        transpose = st.toggle(
            "Transpose (metrics as rows, months as columns)", value=False)
        show = mdf.T if transpose else mdf
        st.dataframe(show if transpose else show.style.format(fmt),
                     use_container_width=True, height=490)
        st.bar_chart(mdf["Net P/L"])
        st.line_chart(mdf["Cumulative Net P/L"])

    with tab_day:
        ddf = daily_summary(trades)
        st.dataframe(ddf.style.format({"Net P/L ($)": "{:,.2f}",
                                       "Cumulative Net P/L ($)": "{:,.2f}"}),
                     use_container_width=True)
        st.bar_chart(ddf["Net P/L ($)"])

    with tab_quality:
        st.subheader("Excluded / informational items")
        if len(option_rows):
            st.write(f"**Options transactions excluded from metrics:** "
                     f"{len(option_rows)} rows across "
                     f"{option_rows['underlying'].nunique()} underlying(s) "
                     f"({', '.join(sorted(option_rows['underlying'].unique()))})")
            with st.expander("Show option rows"):
                st.dataframe(option_rows[["date", "action", "symbol",
                                          "quantity", "amount"]],
                             use_container_width=True)
        if len(result.unmatched_sells):
            st.write("**Sells without in-file cost basis:**")
            st.dataframe(result.unmatched_sells, use_container_width=True)
        if len(result.open_positions):
            st.write("**Open positions at end of file (unrealized, excluded):**")
            st.dataframe(result.open_positions, use_container_width=True)
        for msg in result.split_adjustments:
            st.write(f"🔀 Split adjustment — {msg}")
        if len(parsed.ignored):
            with st.expander(
                    f"Non-trade rows ignored ({len(parsed.ignored)}) — "
                    "dividends, transfers, interest, etc."):
                st.dataframe(parsed.ignored[["date", "account", "action",
                                             "symbol", "amount"]],
                             use_container_width=True)

st.divider()
st.caption(
    "Methodology: FIFO lot matching per symbol · same-day fills of the same "
    "closing order aggregated into one round trip · trades assigned to the "
    "month of the closing sale · Net Amount ($) figures used throughout "
    "(fee-inclusive) · breakeven trades count in Trade Count but not in "
    "wins/losses · short sells matched sell-open → buy-to-cover."
)
