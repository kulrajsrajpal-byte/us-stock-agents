from __future__ import annotations

"""Broad liquid US equity + ETF universe for 24x7 scanning.

We cannot fetch every listed US ticker every cycle (thousands; yfinance would
time out). Instead we maintain a large liquid cross-sector universe and rotate
batches each cycle so the whole set is scanned continuously on Railway.
"""

from datetime import datetime, timezone

# Cross-sector liquid US names: mega-cap, energy/oil, metals, financials,
# healthcare, industrials, consumer, ETFs for broad market / sectors.
US_LIQUID_UNIVERSE: list[str] = [
    # Broad market / sectors
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "XLI", "XLP",
    "XLY", "XLU", "XLB", "XLRE", "SMH", "ARKK",
    # Precious / industrial metals & mining
    "GLD", "SLV", "GDX", "GDXJ", "FCX", "NEM", "GOLD", "AA", "SCCO",
    # Energy / oil / gas
    "XOM", "CVX", "COP", "SLB", "OXY", "EOG", "MPC", "PSX", "VLO", "HAL",
    "USO", "UNG", "BKR", "DVN", "FANG",
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "ORCL",
    "CRM", "AMD", "INTC", "QCOM", "TXN", "ADBE", "CSCO", "IBM", "NOW",
    # Communication / internet
    "NFLX", "DIS", "CMCSA", "T", "VZ", "SNAP", "UBER", "LYFT",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "V", "MA",
    "PYPL", "SQ",
    # Healthcare / biotech
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "BMY", "AMGN", "GILD", "MRNA",
    # Consumer
    "WMT", "COST", "HD", "TGT", "NKE", "SBUX", "MCD", "KO", "PEP", "PG",
    "PM", "MO",
    # Industrials / transport
    "CAT", "DE", "BA", "GE", "HON", "UPS", "FDX", "LMT", "RTX", "NOC",
    # Autos / semis adjacent
    "F", "GM", "RIVN", "LCID", "MU", "AMAT", "LRCX", "KLAC",
    # REITs / utilities / other liquid
    "AMT", "PLD", "O", "NEE", "DUK", "SO", "BAX", "MDT",
]


def rotate_batch(universe: list[str], batch_size: int, *, now: datetime | None = None) -> list[str]:
    """Return the next rotating slice so all symbols are scanned over time."""
    if not universe:
        return []
    batch_size = max(5, min(batch_size, len(universe)))
    now = now or datetime.now(timezone.utc)
    # Advance roughly once per half-hour slot.
    slot = int(now.timestamp() // 1800)
    start = (slot * batch_size) % len(universe)
    out: list[str] = []
    for i in range(batch_size):
        out.append(universe[(start + i) % len(universe)])
    return out


def merge_priority(
    *,
    batch: list[str],
    impacted: list[str],
    held: list[str],
    limit: int,
) -> list[str]:
    """Impacted + open positions first, then rotating scan batch."""
    merged = list(dict.fromkeys([*impacted, *held, *batch]))
    return merged[: max(limit, 1)]
