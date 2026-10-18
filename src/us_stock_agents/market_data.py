from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
import pandas as pd

log = logging.getLogger(__name__)

_PER_SYMBOL_TIMEOUT_SEC = 4.0
_UA = "Mozilla/5.0 (compatible; us-stock-agents/1.0)"


def _fetch_yahoo_chart(symbol: str, days: int) -> pd.DataFrame | None:
    range_str = "3mo" if days <= 100 else "6mo"
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={range_str}&interval=1d&includePrePost=false"
    )
    try:
        with httpx.Client(timeout=_PER_SYMBOL_TIMEOUT_SEC, headers={"User-Agent": _UA}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        result = payload["chart"]["result"][0]
        ts = result.get("timestamp") or []
        quote = (result.get("indicators") or {}).get("quote") or [{}]
        closes = quote[0].get("close") or []
        if not ts or not closes:
            return None
        idx = pd.to_datetime(ts, unit="s", utc=True)
        df = pd.DataFrame({"close": closes}, index=idx).dropna()
        return None if df.empty else df
    except Exception as exc:  # noqa: BLE001
        log.warning("price fetch failed for %s: %s", symbol, exc)
        return None


def fetch_history(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """Sequential fetches — avoids nested ThreadPool deadlocks under uvicorn."""
    uniq = list(dict.fromkeys(symbols))[:8]
    days = min(int(days), 90)
    out: dict[str, pd.DataFrame] = {}
    for sym in uniq:
        df = _fetch_yahoo_chart(sym, days)
        if df is not None:
            out[sym] = df
    log.info("Fetched prices for %s/%s symbols", len(out), len(uniq))
    return out


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
