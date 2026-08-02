"""Dashboard metrics — replicates the 'Dashboard' tab of the sample
spreadsheet, computed from matched round-trip trades.

Column-by-column mapping to the sheet formulas (Trade Sheet 2025 cols
S=Sale Month, T=P/L $, U=% P/L, V=P/L Flag, O=Total Buy Price):

  Trade Count            = COUNTIF(S, month)
  Average Gain ($)       = AVERAGEIFS(T, V="P", S=month)
  Avg Loss ($)           = AVERAGEIFS(T, V="L", S=month)
  WIN%                   = wins / trade count
  Largest Gain ($)       = MAXIFS(T, V="P", S=month)
  Largest Loss ($)       = MINIFS(T, V="L", S=month)
  Avg Capital per Trade  = mean(open capital)          [sheet cell was
                           AVERAGEIFS(U)/count — dimensionally off; the
                           intended "average margin per trade" is
                           implemented as average capital deployed]
  No of Win Trades       = COUNTIFS(V="P", S=month)
  Avg Gain/Win Trade     = Total Gain / win count      [sheet had
                           AvgGain$/win count — made symmetric with the
                           loss-side formula]
  Batting Average        = wins / trade count (same as WIN%, kept for
                           parity with the sheet)
  No of Loss Trades      = COUNTIFS(V="L", S=month)
  Avg Loss/Loss Trade    = Total Loss / loss count
  Gain Loss Ratio        = win count / loss count
  Total Loss ($)         = SUMIFS(T, V="L", S=month)
  Total Gain ($)         = SUMIFS(T, V="P", S=month)
  Net P/L ($)            = Total Gain + Total Loss
  Average Gain %         = AVERAGEIFS(U, V="P", S=month)
  Average Loss %         = AVERAGEIFS(U, V="L", S=month)

Breakeven trades (P/L exactly $0) count toward Trade Count but not
toward wins or losses.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MONTH_LABELS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _month_metrics(g: pd.DataFrame) -> dict:
    """Metrics for one month's trades (g may be empty)."""
    wins = g[g["pl"] > 0]
    losses = g[g["pl"] < 0]
    n, nw, nl = len(g), len(wins), len(losses)
    total_gain = float(wins["pl"].sum())
    total_loss = float(losses["pl"].sum())
    return {
        "Trade Count": n,
        "Average Gain": wins["pl"].mean() if nw else np.nan,
        "Avg Loss": losses["pl"].mean() if nl else np.nan,
        "WIN%": nw / n if n else np.nan,
        "Largest Gain": wins["pl"].max() if nw else np.nan,
        "Largest Loss": losses["pl"].min() if nl else np.nan,
        "Average Margin per trade": g["open_value"].mean() if n else np.nan,
        "No of Win Trades": nw,
        "Avg Gain/Win Trade": total_gain / nw if nw else np.nan,
        "Batting Average": nw / n if n else np.nan,
        "No of Loss Trades": nl,
        "Avg Loss/Loss Trade": total_loss / nl if nl else np.nan,
        "Gain Loss Ratio": nw / nl if nl else np.nan,
        "Total Loss": total_loss,
        "Total Gain": total_gain,
        "Net P/L": total_gain + total_loss,
        "Average Gain%": wins["pct"].mean() if nw else np.nan,
        "Average Loss %": losses["pct"].mean() if nl else np.nan,
    }


DASHBOARD_COLUMNS = ["Trade Count", "Average Gain", "Avg Loss", "WIN%",
                     "Largest Gain", "Largest Loss", "Average Margin per trade",
                     "No of Win Trades", "Avg Gain/Win Trade", "Batting Average",
                     "No of Loss Trades", "Avg Loss/Loss Trade", "Gain Loss Ratio",
                     "Total Loss", "Total Gain", "Net P/L",
                     "Average Gain%", "Average Loss %"]


def monthly_dashboard(trades: pd.DataFrame, year: int) -> pd.DataFrame:
    """Fixed JAN→DEC grid for one year — one row per month, all 12 months
    always present (like the spreadsheet Dashboard tab). Counts and sums
    are 0 for empty months; undefined averages are NaN (rendered as '—')."""
    if trades.empty:
        sub = trades
    else:
        sub = trades[trades["close_date"].dt.year == year]
    rows = {}
    for i, label in enumerate(MONTH_LABELS, start=1):
        g = sub[sub["close_date"].dt.month == i] if len(sub) else sub
        rows[label] = _month_metrics(g)
    df = pd.DataFrame.from_dict(rows, orient="index")[DASHBOARD_COLUMNS]
    df.index.name = "Month"
    df["Cumulative Net P/L"] = df["Net P/L"].cumsum()
    return df



def daily_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["Trades", "Wins", "Losses", "Net P/L ($)",
                                     "Cumulative Net P/L ($)"])
    t = trades.assign(_day=trades["close_date"].dt.normalize(),
                      _win=(trades["pl"] > 0).astype(int),
                      _loss=(trades["pl"] < 0).astype(int))
    g = t.groupby("_day")
    df = pd.DataFrame({
        "Trades": g.size(),
        "Wins": g["_win"].sum(),
        "Losses": g["_loss"].sum(),
        "Net P/L ($)": g["pl"].sum(),
    })
    df.index.name = "Date"
    df = df.sort_index()
    df["Cumulative Net P/L ($)"] = df["Net P/L ($)"].cumsum()
    return df
