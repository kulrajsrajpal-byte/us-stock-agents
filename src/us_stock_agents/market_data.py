from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
import logging

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

_FETCH_TIMEOUT_SEC = 20
_MAX_WORKERS = 4


def _fetch_one(symbol: str, start, end) -> tuple[str, pd.DataFrame | None]:
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, auto_adjust=True)
        if df.empty:
            return symbol, None
        return symbol, df.rename(columns=str.lower)
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance failed for %s: %s", symbol, exc)
        return symbol, None


def fetch_history(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    end = datetime.now(timezone.utc)
    start = (end - timedelta(days=days + 30)).date()
    end_d = end.date()
    out: dict[str, pd.DataFrame] = {}
    uniq = list(dict.fromkeys(symbols))
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one, sym, start, end_d): sym for sym in uniq
        }
        for fut, sym in futures.items():
            try:
                symbol, df = fut.result(timeout=_FETCH_TIMEOUT_SEC)
                if df is not None:
                    out[symbol] = df
            except FuturesTimeout:
                log.warning("yfinance timeout for %s after %ss", sym, _FETCH_TIMEOUT_SEC)
            except Exception as exc:  # noqa: BLE001
                log.warning("yfinance error for %s: %s", sym, exc)
    return out


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
