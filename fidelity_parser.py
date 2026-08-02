"""Parser for Fidelity 'Accounts History' CSV exports.

Handles:
- BOM / blank preamble lines before the header row
- Footer rows ("Date downloaded ...", disclaimers)
- Column auto-detection by fuzzy name matching (exports vary slightly)
- Row classification: equity buy/sell, short sell / buy-to-cover,
  option transactions, splits/distributions, and non-trade cash activity
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

# ---------------------------------------------------------------------------
# Column auto-detection
# ---------------------------------------------------------------------------

# canonical name -> list of regex patterns tried against lowercased headers
COLUMN_PATTERNS: dict[str, list[str]] = {
    "date": [r"^run\s*date$", r"^trade\s*date$", r"^date$"],
    "account": [r"^account$", r"^account\s*name$"],
    "account_number": [r"^account\s*number$", r"^acct", r"account\s*#"],
    "action": [r"^action$", r"^transaction", r"^description of transaction"],
    "symbol": [r"^symbol$", r"^ticker$"],
    "description": [r"^description$", r"^security\s*description$"],
    "type": [r"^type$", r"^security\s*type$"],
    "price": [r"^price", r"price\s*\(\$\)"],
    "quantity": [r"^quantity$", r"^qty$", r"^shares$"],
    "commission": [r"^commission"],
    "fees": [r"^fees"],
    "amount": [r"^amount", r"amount\s*\(\$\)", r"^net\s*amount"],
    "settlement_date": [r"^settlement\s*date$"],
}

REQUIRED = ["date", "action", "symbol", "quantity", "amount"]

# Action classification -----------------------------------------------------

NON_TRADE_KEYWORDS = (
    "DIVIDEND", "REINVESTMENT", "INTEREST", "JOURNAL", "TRANSFER",
    "ELECTRONIC FUNDS", "WIRE", "CHECK RECEIVED", "DIRECT DEPOSIT",
    "DIRECT DEBIT", "FEE", "FOREIGN TAX", "CASH CONTRIBUTION",
    "CONTRIBUTION", "DISTRIBUTION CASH", "MARGIN INTEREST", "ADJUSTMENT",
)

OPTION_SYMBOL_RE = re.compile(r"^\s*-?[A-Z]{1,6}\d{6}[CP][\d.]+\s*$")


@dataclass
class ParseResult:
    trades: pd.DataFrame          # normalized trade rows (equities + options)
    splits: pd.DataFrame          # detected split/share-distribution rows
    ignored: pd.DataFrame         # non-trade rows (dividends, transfers, ...)
    accounts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    column_map: dict[str, str] = field(default_factory=dict)


def _detect_columns(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    lowered = {h: h.strip().lower() for h in headers}
    for canon, patterns in COLUMN_PATTERNS.items():
        for pat in patterns:
            hit = next((h for h, low in lowered.items() if re.search(pat, low)), None)
            if hit is not None:
                mapping[canon] = hit
                break
    return mapping


def _find_header_line(lines: list[str]) -> int | None:
    """Return index of the first line that looks like the header row."""
    for i, line in enumerate(lines[:30]):
        low = line.lower()
        if ("date" in low) and ("action" in low or "transaction" in low) \
                and ("amount" in low or "quantity" in low):
            return i
    return None


def _to_float(val) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return float("nan")
    s = str(val).strip().replace("$", "").replace(",", "")
    if s in ("", "--", "-"):
        return float("nan")
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        f = float(s)
    except ValueError:
        return float("nan")
    return -f if neg else f


def classify_action(action: str, symbol: str, quantity: float, amount: float) -> str:
    """Return one of: buy, sell, short_sell, buy_to_cover,
    option_open, option_close, option_expire, option_assign,
    split, ignore."""
    a = (action or "").upper()
    sym = (symbol or "").strip()
    is_option = bool(OPTION_SYMBOL_RE.match(sym)) or sym.startswith("-") \
        or "OPENING TRANSACTION" in a or "CLOSING TRANSACTION" in a \
        or a.startswith("EXPIRED") or a.startswith("ASSIGNED") or a.startswith("EXERCISED")

    if any(k in a for k in NON_TRADE_KEYWORDS) and "YOU BOUGHT" not in a and "YOU SOLD" not in a:
        # reinvestment rows carry BOUGHT-like semantics only for SPAXX etc.;
        # treat pure cash-activity rows as ignore
        if "SPLIT" in a or ("DISTRIBUTION" in a and not pd.isna(quantity) and quantity != 0
                            and (pd.isna(amount) or amount == 0)):
            return "split"
        return "ignore"

    if "SPLIT" in a:
        return "split"
    # share distributions with no cash movement (stock splits often post this way)
    if "DISTRIBUTION" in a and not pd.isna(quantity) and quantity != 0 \
            and (pd.isna(amount) or amount == 0):
        return "split"

    if is_option:
        if a.startswith("EXPIRED"):
            return "option_expire"
        if a.startswith("ASSIGNED") or a.startswith("EXERCISED"):
            return "option_assign"
        if "OPENING TRANSACTION" in a:
            return "option_open"
        if "CLOSING TRANSACTION" in a:
            return "option_close"
        if "YOU BOUGHT" in a or "YOU SOLD" in a:
            # option trade without opening/closing tag — infer later
            return "option_open" if "BOUGHT" in a else "option_close"
        return "ignore"

    if "YOU SOLD SHORT" in a or ("SOLD" in a and "SHORT" in a):
        return "short_sell"
    if "BUY TO COVER" in a or "BOUGHT TO COVER" in a:
        return "buy_to_cover"
    if "YOU BOUGHT" in a or a.startswith("BOUGHT") or a.startswith("BUY"):
        return "buy"
    if "YOU SOLD" in a or a.startswith("SOLD") or a.startswith("SELL"):
        return "sell"
    return "ignore"


def parse_fidelity_csv(file_or_bytes) -> ParseResult:
    """Parse an uploaded Fidelity Accounts History CSV.

    Accepts a file-like object, bytes, or a path string.
    """
    if isinstance(file_or_bytes, (bytes, bytearray)):
        text = bytes(file_or_bytes).decode("utf-8-sig", errors="replace")
    elif hasattr(file_or_bytes, "read"):
        raw = file_or_bytes.read()
        text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    else:
        with open(file_or_bytes, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()

    lines = text.splitlines()
    hdr_idx = _find_header_line(lines)
    warnings: list[str] = []
    if hdr_idx is None:
        raise ValueError(
            "Could not locate a header row. Expected columns like "
            "'Run Date', 'Action', 'Symbol', 'Quantity', 'Amount ($)'."
        )

    df = pd.read_csv(io.StringIO("\n".join(lines[hdr_idx:])), dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    colmap = _detect_columns(list(df.columns))

    missing = [c for c in REQUIRED if c not in colmap]
    if missing:
        raise ValueError(
            f"Required column(s) not found in the file: {missing}. "
            f"Detected mapping: {colmap}"
        )

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[colmap["date"]], errors="coerce")
    out["account"] = df[colmap["account"]].str.strip() if "account" in colmap else "All"
    out["action"] = df[colmap["action"]].fillna("").str.strip()
    out["symbol"] = df[colmap["symbol"]].fillna("").str.strip()
    out["description"] = df[colmap["description"]].fillna("").str.strip() \
        if "description" in colmap else ""
    out["price"] = df[colmap["price"]].map(_to_float) if "price" in colmap else float("nan")
    out["quantity"] = df[colmap["quantity"]].map(_to_float)
    out["amount"] = df[colmap["amount"]].map(_to_float)

    n_before = len(out)
    out = out[out["date"].notna()].copy()   # drops footer rows
    dropped = n_before - len(out)
    if dropped:
        warnings.append(f"Dropped {dropped} non-data row(s) (footer/blank lines).")

    out["category"] = [
        classify_action(a, s, q, amt)
        for a, s, q, amt in zip(out["action"], out["symbol"], out["quantity"], out["amount"])
    ]
    out["is_option"] = out["category"].str.startswith("option")

    # Underlying ticker for options (e.g. "-NVDA260807P197.5" -> NVDA)
    def underlying(sym: str) -> str:
        m = re.match(r"^-?([A-Z]{1,6})\d{6}[CP]", sym.strip())
        return m.group(1) if m else sym

    out["underlying"] = out["symbol"].map(underlying)

    trades = out[out["category"].isin(
        ["buy", "sell", "short_sell", "buy_to_cover",
         "option_open", "option_close", "option_expire", "option_assign"]
    )].sort_values("date", kind="stable").reset_index(drop=True)
    splits = out[out["category"] == "split"].reset_index(drop=True)
    ignored = out[out["category"] == "ignore"].reset_index(drop=True)

    accounts = sorted(a for a in out["account"].dropna().unique() if a)
    return ParseResult(trades=trades, splits=splits, ignored=ignored,
                       accounts=accounts, warnings=warnings, column_map=colmap)
