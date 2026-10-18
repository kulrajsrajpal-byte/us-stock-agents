from __future__ import annotations

import pandas as pd


def compute_rsi(close: pd.Series, period: int = 14) -> float | None:
    if close is None or len(close) < period + 1:
        return None
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        return 100.0
    rs = float(avg_gain) / float(avg_loss)
    return 100.0 - (100.0 / (1.0 + rs))


def is_rsi_oversold(df: pd.DataFrame | None, period: int = 14, threshold: float = 30.0) -> bool:
    if df is None or "close" not in df.columns:
        return False
    rsi = compute_rsi(df["close"], period)
    return rsi is not None and rsi <= threshold


def is_rsi_overbought(df: pd.DataFrame | None, period: int = 14, threshold: float = 70.0) -> bool:
    if df is None or "close" not in df.columns:
        return False
    rsi = compute_rsi(df["close"], period)
    return rsi is not None and rsi >= threshold


def is_ma_bullish(df: pd.DataFrame | None, fast: int, slow: int) -> bool:
    if df is None or len(df) < slow + 1 or "close" not in df.columns:
        return False
    close = df["close"].astype(float)
    f = float(close.rolling(fast).mean().iloc[-1])
    s = float(close.rolling(slow).mean().iloc[-1])
    if pd.isna(f) or pd.isna(s):
        return False
    return f > s


def is_ma_bearish(df: pd.DataFrame | None, fast: int, slow: int) -> bool:
    if df is None or len(df) < slow + 1 or "close" not in df.columns:
        return False
    close = df["close"].astype(float)
    f = float(close.rolling(fast).mean().iloc[-1])
    s = float(close.rolling(slow).mean().iloc[-1])
    if pd.isna(f) or pd.isna(s):
        return False
    return f < s


def breakout_high(df: pd.DataFrame | None, lookback: int = 20) -> bool:
    """True when latest close makes a new N-day high (classic breakout)."""
    if df is None or len(df) < lookback + 1 or "close" not in df.columns:
        return False
    close = df["close"].astype(float)
    prior_high = float(close.iloc[-(lookback + 1) : -1].max())
    last = float(close.iloc[-1])
    return last > prior_high


def momentum_positive(df: pd.DataFrame | None, lookback: int = 20) -> bool:
    """True when price is up over the lookback window (simple momentum)."""
    if df is None or len(df) < lookback + 1 or "close" not in df.columns:
        return False
    close = df["close"].astype(float)
    past = float(close.iloc[-(lookback + 1)])
    last = float(close.iloc[-1])
    if past == 0:
        return False
    return last > past
