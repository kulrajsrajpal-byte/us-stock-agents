from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import httpx
import pandas as pd

log = logging.getLogger(__name__)

_PER_SYMBOL_TIMEOUT_SEC = 6.0
_BATCH_TIMEOUT_SEC = 25
_MAX_WORKERS = 8
_UA = "Mozilla/5.0 (compatible; us-stock-agents/1.0)"


def _fetch_yahoo_chart(symbol: str, days: int) -> tuple[str, pd.DataFrame | None]:
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
            return symbol, None
        idx = pd.to_datetime(ts, unit="s", utc=True)
        df = pd.DataFrame({"close": closes}, index=idx).dropna()
        if df.empty:
            return symbol, None
        return symbol, df
    except Exception as exc:  # noqa: BLE001
        log.warning("price fetch failed for %s: %s", symbol, exc)
        return symbol, None


def fetch_history(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    uniq = list(dict.fromkeys(symbols))[:10]
    if not uniq:
        return {}
    days = min(int(days), 120)
    out: dict[str, pd.DataFrame] = {}
    pool = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
    try:
        futs = {pool.submit(_fetch_yahoo_chart, sym, days): sym for sym in uniq}
        try:
            for fut in as_completed(futs, timeout=_BATCH_TIMEOUT_SEC):
                try:
                    sym, df = fut.result()
                    if df is not None:
                        out[sym] = df
                except Exception as exc:  # noqa: BLE001
                    log.warning("future failed: %s", exc)
        except TimeoutError:
            log.warning("price batch timed out with %s/%s symbols", len(out), len(uniq))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    log.info("Fetched prices for %s/%s symbols", len(out), len(uniq))
    return out


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
