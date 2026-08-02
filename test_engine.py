"""Regression tests for the FIFO matching engine.

Run with:  python -m pytest test_engine.py   (or just: python test_engine.py)
"""

from fidelity_parser import parse_fidelity_csv
from matcher import match_trades

SYNTH = """\ufeff

Run Date,Account,Account Number,Action,Symbol,Description,Type,Price ($),Quantity,Commission ($),Fees ($),Accrued Interest ($),Amount ($),Settlement Date
03/02/2026,Individual - TOD,Z1, YOU BOUGHT ACME CORP (ACME) (Cash),ACME,ACME CORP,Cash,100,100,,,,-10000,03/03/2026
03/10/2026,Individual - TOD,Z1, YOU SOLD ACME CORP (ACME) (Cash),ACME,ACME CORP,Cash,110,-40,,,,4400,03/11/2026
04/05/2026,Individual - TOD,Z1, YOU SOLD ACME CORP (ACME) (Cash),ACME,ACME CORP,Cash,120,-60,,,,7200,04/06/2026
03/15/2026,Individual - TOD,Z1, YOU SOLD SHORT BETA INC (BETA) (Margin),BETA,BETA INC,Margin,50,-100,,,,5000,03/16/2026
03/20/2026,Individual - TOD,Z1, YOU BOUGHT TO COVER BETA INC (BETA) (Margin),BETA,BETA INC,Margin,45,100,,,,-4500,03/21/2026
02/01/2026,Individual - TOD,Z1, YOU BOUGHT GAMMA LTD (GAM) (Cash),GAM,GAMMA LTD,Cash,200,10,,,,-2000,02/02/2026
02/10/2026,Individual - TOD,Z1,DISTRIBUTION STOCK SPLIT GAMMA LTD (GAM) (Cash),GAM,GAMMA LTD,Cash,,30,,,,0,
02/20/2026,Individual - TOD,Z1, YOU SOLD GAMMA LTD (GAM) (Cash),GAM,GAMMA LTD,Cash,55,-40,,,,2200,02/21/2026
02/25/2026,Individual - TOD,Z1, YOU SOLD OLDSTOCK (OLD) (Cash),OLD,OLDSTOCK,Cash,30,-100,,,,3000,02/26/2026
Date downloaded 08/02/2026
"""


def _run():
    p = parse_fidelity_csv(SYNTH.encode())
    eq = p.trades[~p.trades["is_option"]]
    return p, eq


def test_pending_unmatched_detected():
    p, eq = _run()
    r = match_trades(eq, p.splits)
    assert r.unmatched_sells["symbol"].tolist() == ["OLD"]
    assert (r.unmatched_sells["resolution"] == "pending").all()


def test_full_scenario():
    p, eq = _run()
    r = match_trades(eq, p.splits, manual_basis={"OLD": 25.0})
    t = r.trades.set_index("symbol")
    assert abs(t.loc["BETA", "pl"] - 500) < 1e-6          # short round trip
    assert abs(t.loc["GAM", "pl"] - 200) < 1e-6           # split-adjusted
    assert abs(t.loc["OLD", "pl"] - 500) < 1e-6           # manual basis
    acme = r.trades[r.trades.symbol == "ACME"]
    assert len(acme) == 2                                  # partial sells
    assert abs(acme["pl"].sum() - 1600) < 1e-6
    assert set(r.trades["month"]) == {"2026-02", "2026-03", "2026-04"}
    assert r.open_positions.empty


def test_exclusion_path():
    p, eq = _run()
    r = match_trades(eq, p.splits, excluded_symbols={"OLD"})
    assert "OLD" not in r.trades["symbol"].values
    assert (r.unmatched_sells["resolution"] == "excluded").all()


if __name__ == "__main__":
    test_pending_unmatched_detected()
    test_full_scenario()
    test_exclusion_path()
    print("ALL TESTS PASSED")
