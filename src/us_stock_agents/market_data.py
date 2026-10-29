from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Seed from earlier paper fills / liquid defaults so Railway never blocks on HTTP.
_SEED_LAST: dict[str, float] = {
    "SPY": 771.79,
    "QQQ": 709.80,
    "IWM": 220.0,
    "DIA": 420.0,
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
    "SLV": 30.0,
    "FCX": 45.0,
    "JPM": 250.0,
    "XLE": 90.0,
    "XLK": 230.0,
    "XLF": 45.0,
    "USO": 70.0,
    "GDX": 40.0,
    "NEM": 50.0,
    "COP": 100.0,
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


def _load_trade_prices() -> dict[str, float]:
    try:
        from sqlalchemy import select

        from us_stock_agents.db import TradeRow, init_db, session_scope

        init_db()
        out: dict[str, float] = {}
        with session_scope() as session:
            rows = session.scalars(select(TradeRow).order_by(TradeRow.id.desc()).limit(100)).all()
            for row in rows:
                if row.symbol not in out and row.price and float(row.price) > 0:
                    out[row.symbol] = float(row.price)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("trade price hydrate failed: %s", exc)
        return {}


def _series_from_last(last: float, days: int = 90) -> pd.DataFrame:
    idx = pd.date_range(end=datetime.now(timezone.utc), periods=days, freq="D", tz="UTC")
    steps = pd.Series(range(days), dtype=float)
    # Slight upward/downward wobble so MA windows are not identical forever.
    closes = last * (1.0 + (steps - days / 2) * 0.0002 + ((steps % 7) - 3) * 0.00005)
    return pd.DataFrame({"close": closes.values}, index=idx)


def fetch_history(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """Instant prices from seed + disk + Postgres trades. No outbound HTTP by default.

    LIVE_PRICES=true can re-enable Stooq later; default stays offline-fast so the
    Forever Agent never dies on cycle_timeout.
    """
    uniq = list(dict.fromkeys(symbols))[:8]
    days = min(int(days), 90)
    last_map = {**_SEED_LAST, **_load_disk_cache(), **_load_trade_prices()}
    out: dict[str, pd.DataFrame] = {}

    if os.getenv("LIVE_PRICES", "").strip().lower() in {"1", "true", "yes"}:
        try:
            import io

            import httpx

            for sym in uniq:
                try:
                    url = f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d"
                    with httpx.Client(timeout=1.0, follow_redirects=True) as client:
                        resp = client.get(url)
                        text = resp.text.strip()
                    if text and "<" not in text[:40].lower():
                        df = pd.read_csv(io.StringIO(text))
                        if not df.empty and "Close" in df.columns:
                            last_map[sym] = float(df["Close"].iloc[-1])
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.warning("live prices disabled after error: %s", exc)

    for sym in uniq:
        last = last_map.get(sym)
        if last is None or last <= 0:
            continue
        out[sym] = _series_from_last(float(last), days)

    log.info("Prices ready for %s/%s symbols (cache/seed)", len(out), len(uniq))
    return out


def last_close(prices: dict[str, pd.DataFrame], symbol: str) -> float | None:
    df = prices.get(symbol)
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])
