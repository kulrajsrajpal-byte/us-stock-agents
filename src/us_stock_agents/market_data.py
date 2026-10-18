from __future__ import annotations

import io
import logging

import httpx
import pandas as pd

log = logging.getLogger(__name__)

_TIMEOUT = 2.0
_UA = "Mozilla/5.0 (compatible; us-stock-agents/1.0)"


def _stooq_symbol(symbol: str) -> str:
    s = symbol.lower()
    return s if s.endswith(".us") else f"{s}.us"


def _fetch_stooq(symbol: str) -> pd.DataFrame | None:
    url = f"https://stooq.com/q/d/l/?s={_stooq_symbol(symbol)}&i=d"
    try:
        with httpx.Client(timeout=_TIMEOUT, headers={"User-Agent": _UA}, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text.strip()
        if not text or "<" in text[:40].lower():
            return None
        df = pd.read_csv(io.StringIO(text))
        if df.empty or "Close" not in df.columns:
            return None
        df = df.rename(columns=str.lower)
        df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
        df = df.dropna(subset=["date", "close"]).set_index("date")[["close"]].tail(90)
        return None if df.empty else df
    except Exception as exc:  # noqa: BLE001
        log.warning("stooq failed for %s: %s", symbol, exc)
        return None


def fetch_history(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    uniq = list(dict.fromkeys(symbols))[:4]
    out: dict[str, pd.DataFrame] = {}
    for sym in uniq:
        df = _fetch_stooq(sym)
        if df is not None:
            out[sym] = df
    log.info("Fetched prices for %s/%s symbols", len(out), len(uniq))
    return out


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
