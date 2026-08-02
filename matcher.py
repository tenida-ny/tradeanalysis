"""FIFO lot-matching engine.

Methodology (agreed conventions):
- Buys and sells matched chronologically per symbol (FIFO)
- Long and short round trips both supported:
    * sell against open long lots  -> closes long
    * explicit short-sell          -> opens short lots
    * buy against open short lots  -> covers short
- Same-day closes of the same symbol/direction are aggregated into a
  single round-trip trade (prevents double counting partial fills)
- Each completed trade is assigned to the month its CLOSE occurred
- Net Amount ($) used throughout (already inclusive of fees)
- Stock splits adjust open lots (share qty scaled up, per-share cost down)
- Sells with no open lots and no explicit SHORT tag are *unmatched*:
  the caller decides per ticker to exclude them or supply a cost basis
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import pandas as pd


@dataclass
class Lot:
    date: pd.Timestamp
    qty: float          # always positive
    cost_per_share: float  # net cash per share at open (positive number)


@dataclass
class Closure:
    symbol: str
    direction: str            # "long" or "short"
    open_date: pd.Timestamp   # earliest matched lot open date
    close_date: pd.Timestamp
    qty: float
    open_value: float         # capital at open (positive $)
    close_value: float        # cash at close (positive $)
    pl: float                 # realized P/L in $ (net of fees)


@dataclass
class MatchResult:
    trades: pd.DataFrame            # aggregated round-trip trades
    closures: pd.DataFrame          # raw closure legs before aggregation
    open_positions: pd.DataFrame    # remaining open lots at file end
    unmatched_sells: pd.DataFrame   # sells with no basis in the window
    split_adjustments: list[str]


def _apply_split(lots: deque[Lot], add_qty: float) -> float | None:
    """Scale open lots for a split distribution of `add_qty` new shares.

    Returns the ratio applied, or None if there were no open lots."""
    existing = sum(l.qty for l in lots)
    if existing <= 0:
        return None
    ratio = (existing + add_qty) / existing
    for l in lots:
        l.qty *= ratio
        l.cost_per_share /= ratio
    return ratio


def match_trades(
    trade_rows: pd.DataFrame,
    split_rows: pd.DataFrame | None = None,
    manual_basis: dict[str, float] | None = None,
    excluded_symbols: set[str] | None = None,
) -> MatchResult:
    """Run FIFO matching over equity trade rows (options must be filtered
    out by the caller).

    manual_basis: {symbol: cost_per_share} supplied by the user for sells
        whose buys predate the file window. When present, unmatched sold
        shares for that symbol are closed against that basis.
    excluded_symbols: symbols whose unmatched sells should be dropped.
    """
    manual_basis = manual_basis or {}
    excluded_symbols = excluded_symbols or set()

    rows = trade_rows.sort_values("date", kind="stable")
    events: list[tuple] = [
        ("trade", r) for _, r in rows.iterrows()
    ]
    if split_rows is not None and len(split_rows):
        events += [("split", r) for _, r in split_rows.iterrows()]
    # stable order: by date, trades before splits on the same day is fine
    events.sort(key=lambda e: (e[1]["date"], 0 if e[0] == "trade" else 1))

    long_lots: dict[str, deque[Lot]] = {}
    short_lots: dict[str, deque[Lot]] = {}
    closures: list[Closure] = []
    unmatched: list[dict] = []
    split_msgs: list[str] = []

    def close_against(lots: deque[Lot], qty: float, cash_per_share: float,
                      date, symbol: str, direction: str) -> float:
        """Close up to `qty` shares FIFO. Returns qty actually closed."""
        remaining = qty
        while remaining > 1e-9 and lots:
            lot = lots[0]
            take = min(lot.qty, remaining)
            open_val = take * lot.cost_per_share
            close_val = take * cash_per_share
            pl = (close_val - open_val) if direction == "long" else (open_val - close_val)
            # for shorts: open_val is proceeds received at short-open,
            # close_val is cash paid to cover; pl = proceeds - cover cost
            closures.append(Closure(
                symbol=symbol, direction=direction, open_date=lot.date,
                close_date=date, qty=take,
                open_value=open_val, close_value=close_val, pl=pl,
            ))
            lot.qty -= take
            remaining -= take
            if lot.qty <= 1e-9:
                lots.popleft()
        return qty - remaining

    for kind, r in events:
        sym = r["symbol"]
        date = r["date"]
        if kind == "split":
            ratio = _apply_split(long_lots.setdefault(sym, deque()), abs(r["quantity"]))
            if ratio:
                split_msgs.append(
                    f"{sym}: split distribution of {abs(r['quantity']):g} shares on "
                    f"{date.date()} — open lots scaled by {ratio:.4g}x"
                )
            continue

        cat = r["category"]
        qty = abs(float(r["quantity"])) if pd.notna(r["quantity"]) else 0.0
        amt = float(r["amount"]) if pd.notna(r["amount"]) else 0.0
        if qty <= 0:
            continue
        cash_per_share = abs(amt) / qty  # net of fees

        if cat == "buy":
            covered = 0.0
            if short_lots.get(sym):
                covered = close_against(short_lots[sym], qty, cash_per_share,
                                        date, sym, "short")
            leftover = qty - covered
            if leftover > 1e-9:
                long_lots.setdefault(sym, deque()).append(
                    Lot(date=date, qty=leftover, cost_per_share=cash_per_share))

        elif cat == "buy_to_cover":
            covered = close_against(short_lots.setdefault(sym, deque()), qty,
                                    cash_per_share, date, sym, "short")
            if qty - covered > 1e-9:
                long_lots.setdefault(sym, deque()).append(
                    Lot(date=date, qty=qty - covered, cost_per_share=cash_per_share))

        elif cat == "short_sell":
            short_lots.setdefault(sym, deque()).append(
                Lot(date=date, qty=qty, cost_per_share=cash_per_share))
            # cost_per_share here = proceeds per share at short open

        elif cat == "sell":
            closed = 0.0
            if long_lots.get(sym):
                closed = close_against(long_lots[sym], qty, cash_per_share,
                                       date, sym, "long")
            leftover = qty - closed
            if leftover > 1e-9:
                if sym in excluded_symbols:
                    unmatched.append(dict(symbol=sym, date=date, qty=leftover,
                                          proceeds=leftover * cash_per_share,
                                          resolution="excluded"))
                elif sym in manual_basis:
                    basis = manual_basis[sym]
                    closures.append(Closure(
                        symbol=sym, direction="long", open_date=pd.NaT,
                        close_date=date, qty=leftover,
                        open_value=leftover * basis,
                        close_value=leftover * cash_per_share,
                        pl=leftover * (cash_per_share - basis),
                    ))
                    unmatched.append(dict(symbol=sym, date=date, qty=leftover,
                                          proceeds=leftover * cash_per_share,
                                          resolution=f"manual basis ${basis:g}/sh"))
                else:
                    unmatched.append(dict(symbol=sym, date=date, qty=leftover,
                                          proceeds=leftover * cash_per_share,
                                          resolution="pending"))

    # ---- aggregate same-day closes into round-trip trades -----------------
    if closures:
        cdf = pd.DataFrame([c.__dict__ for c in closures])
        grp = cdf.groupby(["symbol", "direction",
                           cdf["close_date"].dt.normalize().rename("close_day")],
                          dropna=False)
        agg = grp.agg(
            qty=("qty", "sum"),
            open_value=("open_value", "sum"),
            close_value=("close_value", "sum"),
            pl=("pl", "sum"),
            open_date=("open_date", "min"),
            close_date=("close_date", "max"),
        ).reset_index()
        agg["pct"] = agg.apply(
            lambda r: r["pl"] / r["open_value"] if r["open_value"] else float("nan"),
            axis=1)
        agg["month"] = agg["close_date"].dt.to_period("M").astype(str)
        agg["holding_days"] = (agg["close_date"] - agg["open_date"]).dt.days
        trades = agg.drop(columns=["close_day"]).sort_values("close_date").reset_index(drop=True)
    else:
        cdf = pd.DataFrame(columns=["symbol", "direction", "open_date", "close_date",
                                    "qty", "open_value", "close_value", "pl"])
        trades = pd.DataFrame(columns=["symbol", "direction", "qty", "open_value",
                                       "close_value", "pl", "open_date", "close_date",
                                       "pct", "month", "holding_days"])

    # ---- open positions ---------------------------------------------------
    open_rows = []
    for sym, lots in long_lots.items():
        for l in lots:
            if l.qty > 1e-9:
                open_rows.append(dict(symbol=sym, side="long", qty=l.qty,
                                      open_date=l.date, cost_per_share=l.cost_per_share))
    for sym, lots in short_lots.items():
        for l in lots:
            if l.qty > 1e-9:
                open_rows.append(dict(symbol=sym, side="short", qty=l.qty,
                                      open_date=l.date, cost_per_share=l.cost_per_share))
    open_positions = pd.DataFrame(open_rows, columns=["symbol", "side", "qty",
                                                      "open_date", "cost_per_share"])
    unmatched_df = pd.DataFrame(unmatched, columns=["symbol", "date", "qty",
                                                    "proceeds", "resolution"])
    return MatchResult(trades=trades, closures=cdf, open_positions=open_positions,
                       unmatched_sells=unmatched_df, split_adjustments=split_msgs)
