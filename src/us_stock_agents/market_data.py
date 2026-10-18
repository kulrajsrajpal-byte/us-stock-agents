from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
import logging

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

_PER_SYMBOL_TIMEOUT_SEC = 12
_BATCH_TIMEOUT_SEC = 35
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
    # Cap lookback so Railway cycles stay under wall timeout.
    days = min(int(days), 180)
    start = (end - timedelta(days=days + 20)).date()
    end_d = end.date()
    out: dict[str, pd.DataFrame] = {}
    uniq = list(dict.fromkeys(symbols))[:12]
    if not uniq:
        return out

    pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
    try:
        futures = {pool.submit(_fetch_one, sym, start, end_d): sym for sym in uniq}
        try:
            for fut in as_completed(futures, timeout=_BATCH_TIMEOUT_SEC):
                sym = futures[fut]
                try:
                    symbol, df = fut.result(timeout=_PER_SYMBOL_TIMEOUT_SEC)
                    if df is not None:
                        out[symbol] = df
                except Exception as exc:  # noqa: BLE001
                    log.warning("yfinance error for %s: %s", sym, exc)
        except FuturesTimeout:
            log.warning(
                "yfinance batch timeout after %ss (%s/%s symbols)",
                _BATCH_TIMEOUT_SEC,
                len(out),
                len(uniq),
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
