from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class NewsItem:
    title: str
    summary: str
    link: str
    published: datetime | None
    source: str


@dataclass
class SentimentReport:
    generated_at: datetime
    overall_label: str
    overall_score: float
    headline_count: int
    bullish_pct: float
    bearish_pct: float
    neutral_pct: float
    top_bullish: list[dict[str, Any]] = field(default_factory=list)
    top_bearish: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""


@dataclass
class ImpactedTicker:
    symbol: str
    reason: str
    relevance: float
    sentiment_score: float | None = None


@dataclass
class ActivityReport:
    generated_at: datetime
    themes: list[str]
    impacted: list[ImpactedTicker]
    macro_events: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class BacktestMetrics:
    symbol: str
    fast_ma: int
    slow_ma: int
    return_pct: float
    max_drawdown_pct: float
    sharpe: float
    trades: int
    win_rate_pct: float


@dataclass
class BacktestReport:
    generated_at: datetime
    benchmark: str
    strategy_name: str
    per_symbol: list[BacktestMetrics]
    aggregate_return_pct: float
    recommended_symbols: list[str]
    parameter_suggestion: dict[str, int]
    notes: str = ""


@dataclass
class PaperTrade:
    timestamp: datetime
    symbol: str
    side: str
    qty: float
    price: float
    reason: str


@dataclass
class PaperTradingReport:
    generated_at: datetime
    cash: float
    equity: float
    positions: dict[str, float]
    trades_today: list[PaperTrade]
    lessons: list[str]
    strategy_version: int
    strategy_params: dict[str, int]
    notes: str = ""


@dataclass
class PipelineResult:
    sentiment: SentimentReport
    activity: ActivityReport
    backtest: BacktestReport
    paper: PaperTradingReport
