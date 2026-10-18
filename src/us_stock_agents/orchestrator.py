from __future__ import annotations

import logging
from datetime import datetime, timezone

from us_stock_agents.agents import ActivityAgent, BacktestAgent, PaperTradingAgent
from us_stock_agents.config import AppConfig, ensure_dirs
from us_stock_agents.market_data import fetch_history
from us_stock_agents.memory import SharedMemory
from us_stock_agents.models import ActivityReport, PipelineResult, SentimentReport
from us_stock_agents.universe import merge_priority, rotate_batch

log = logging.getLogger(__name__)


class Orchestrator:
    """Runs specialist agents in sequence (reports optional — skip for speed)."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        ensure_dirs(cfg)
        self.memory = SharedMemory(cfg.state_dir)
        self.activity_agent = ActivityAgent(cfg)
        self.backtest_agent = BacktestAgent(cfg, self.memory)
        self.paper_agent = PaperTradingAgent(cfg, self.memory)

    def run_pipeline(self) -> PipelineResult:
        sentiment = self._neutral("Sentiment skipped; 24x7 trading continues.")
        activity = self.activity_agent.run(sentiment)

        symbols = self._scan_symbols(activity)
        # Always returns quickly via cache/seed even if Stooq is blocked.
        prices = fetch_history(symbols, self.cfg.history_days)

        backtest = self.backtest_agent.run(sentiment, activity, prices=prices)
        paper = self.paper_agent.run(backtest, activity, prices=prices)

        log.info(
            "Pipeline done scanned=%s recommended=%s equity=%s trades=%s",
            symbols,
            backtest.recommended_symbols,
            paper.equity,
            len(paper.trades_today),
        )
        return PipelineResult(
            sentiment=sentiment,
            activity=activity,
            backtest=backtest,
            paper=paper,
        )

    def _scan_symbols(self, activity: ActivityReport) -> list[str]:
        # Keep core liquid names every cycle so we always have tradeable names.
        core = ["SPY", "QQQ", "AAPL", "GOOGL", "XOM", "GLD"]
        batch = rotate_batch(self.cfg.universe, self.cfg.scan_batch_size)
        batch = list(dict.fromkeys([*core, *self.cfg.watchlist, *batch]))
        impacted = [t.symbol for t in activity.impacted]
        return merge_priority(
            batch=batch,
            impacted=impacted,
            held=[],
            limit=min(6, self.cfg.trade_universe_limit),
        )

    def _neutral(self, note: str) -> SentimentReport:
        return SentimentReport(
            generated_at=datetime.now(timezone.utc),
            overall_label="neutral",
            overall_score=0.0,
            headline_count=0,
            bullish_pct=0.0,
            bearish_pct=0.0,
            neutral_pct=100.0,
            notes=note,
        )
