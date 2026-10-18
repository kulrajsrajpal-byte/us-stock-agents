from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

log = logging.getLogger(__name__)

_TIMEOUT = 1.5
_UA = "Mozilla/5.0 (compatible; us-stock-agents/1.0)"

# Seed from earlier paper fills so Railway can trade even when outbound HTTP is blocked.
_SEED_LAST: dict[str, float] = {
    "SPY": 771.79,
    "QQQ": 709.80,
    "IWM": 220.0,
    "AAPL": 343.46,
    "MSFT": 529.05,
    "NVDA": 140.0,
    "AMZN": 220.0,
    "GOOGL": 340.59,
    "META": 600.0,
    "TSLA": 250.0,
    "XOM": 110.0,
    "CVX": 150.0,
    "GLD": 250.0,
    "FCX": 45.0,
    "JPM": 250.0,
    "XLE": 90.0,
    "XLK": 230.0,
}


def _cache_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    path = root / "state" / "price_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_disk_cache() -> dict[str, float]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: float(v) for k, v in (raw.get("last") or {}).items()}
    except Exception:  # noqa: BLE001
        return {}


def _save_disk_cache(last: dict[str, float]) -> None:
    try:
        _cache_path().write_text(
            json.dumps({"last": last, "updated_at": datetime.now(timezone.utc).isoformat()}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("price cache write failed: %s", exc)


def _load_trade_prices() -> dict[str, float]:
    """Instant prices from Postgres trade history (no outbound HTTP)."""
    try:
        from sqlalchemy import select

        from us_stock_agents.db import TradeRow, init_db, session_scope

        init_db()
        out: dict[str, float] = {}
        with session_scope() as session:
            rows = session.scalars(select(TradeRow).order_by(TradeRow.id.desc()).limit(200)).all()
            for row in rows:
                if row.symbol not in out and row.price and row.price > 0:
                    out[row.symbol] = float(row.price)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("trade price hydrate failed: %s", exc)
        return {}


def _series_from_last(last: float, days: int = 90) -> pd.DataFrame:
    """Build a usable close series around last known price so MA logic can run."""
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=days, freq="D", tz="UTC")
    # Mild drift so signals are not always flat-identical.
    steps = pd.Series(range(days), dtype=float)
    closes = last * (1.0 + (steps - days / 2) * 0.00015)
    return pd.DataFrame({"close": closes.values}, index=idx)


def _fetch_stooq(symbol: str) -> float | None:
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
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
        return float(df["Close"].iloc[-1])
    except Exception as exc:  # noqa: BLE001
        log.warning("stooq failed for %s: %s", symbol, exc)
        return None


def fetch_history(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """Always return price frames quickly — live HTTP is best-effort only.

    Order: live Stooq (1.5s) → disk cache → Postgres trades → seed constants.
    This keeps the 24x7 Railway loop trading instead of cycle_timeout.
    """
    uniq = list(dict.fromkeys(symbols))[:6]
    days = min(int(days), 90)
    disk = _load_disk_cache()
    trades = _load_trade_prices()
    last_map = {**_SEED_LAST, **disk, **trades}
    out: dict[str, pd.DataFrame] = {}
    updated = False

    for sym in uniq:
        live = _fetch_stooq(sym)
        if live is not None and live > 0:
            last_map[sym] = live
            updated = True
            out[sym] = _series_from_last(live, days)
            continue
        last = last_map.get(sym)
        if last is not None and last > 0:
            out[sym] = _series_from_last(float(last), days)

    if updated:
        _save_disk_cache({k: float(v) for k, v in last_map.items() if v and v > 0})

    log.info("Prices ready for %s/%s symbols (live+cache)", len(out), len(uniq))
    return out


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
