from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
import logging

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SEC = 40


def _download_batch(symbols: list[str], start, end) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if not symbols:
        return out
    # One network call for the whole watchlist.
    data = yf.download(
        tickers=" ".join(symbols),
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        threads=True,
        group_by="ticker",
    )
    if data is None or data.empty:
        return out

    if len(symbols) == 1:
        sym = symbols[0]
        df = data.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if "close" in df.columns:
            out[sym] = df
        return out

    for sym in symbols:
        try:
            if sym not in data.columns.get_level_values(0):
                continue
            df = data[sym].copy()
            df.columns = [str(c).lower() for c in df.columns]
            if "close" in df.columns and not df["close"].dropna().empty:
                out[sym] = df.dropna(how="all")
        except Exception as exc:  # noqa: BLE001
            log.warning("parse failed for %s: %s", sym, exc)
    return out


def fetch_history(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    end = datetime.now(timezone.utc)
    days = min(int(days), 120)
    start = (end - timedelta(days=days + 15)).date()
    end_d = end.date()
    uniq = list(dict.fromkeys(symbols))[:10]
    if not uniq:
        return {}

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_download_batch, uniq, start, end_d)
        try:
            return fut.result(timeout=_DOWNLOAD_TIMEOUT_SEC)
        except FuturesTimeout:
            log.warning("yfinance download timed out after %ss", _DOWNLOAD_TIMEOUT_SEC)
            return {}
        except Exception as exc:  # noqa: BLE001
            log.warning("yfinance download failed: %s", exc)
            return {}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
