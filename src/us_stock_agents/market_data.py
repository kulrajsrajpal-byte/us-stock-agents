from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf


def fetch_history(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days + 30)
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start.date(), end=end.date(), auto_adjust=True)
        if df.empty:
            continue
        df = df.rename(columns=str.lower)
        out[symbol] = df
    return out


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
