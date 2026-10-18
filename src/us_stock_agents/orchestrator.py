from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone

from us_stock_agents.agents import (
    ActivityAgent,
    BacktestAgent,
    PaperTradingAgent,
    SentimentAgent,
)
from us_stock_agents.config import AppConfig, ensure_dirs
from us_stock_agents.market_data import fetch_history
from us_stock_agents.memory import SharedMemory
from us_stock_agents.models import ActivityReport, PipelineResult, SentimentReport
from us_stock_agents.reporting import write_report
from us_stock_agents.universe import merge_priority, rotate_batch

log = logging.getLogger(__name__)
_SENTIMENT_TIMEOUT_SEC = 20


class Orchestrator:
    """Runs specialist agents in sequence and writes per-agent reports."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        ensure_dirs(cfg)
        self.memory = SharedMemory(cfg.state_dir)
        self.sentiment_agent = SentimentAgent(cfg)
        self.activity_agent = ActivityAgent(cfg)
        self.backtest_agent = BacktestAgent(cfg, self.memory)
        self.paper_agent = PaperTradingAgent(cfg, self.memory)

    def run_pipeline(self) -> PipelineResult:
        sentiment = self._run_sentiment_fast()
        write_report(self.cfg.reports_dir, "sentiment", sentiment)

        activity = self.activity_agent.run(sentiment)
        write_report(self.cfg.reports_dir, "activity", activity)

        # One shared price download for backtest + paper (halves network time).
        symbols = self._scan_symbols(activity)
        prices = fetch_history(symbols, self.cfg.history_days)

        backtest = self.backtest_agent.run(sentiment, activity, prices=prices)
        write_report(self.cfg.reports_dir, "backtest", backtest)

        paper = self.paper_agent.run(backtest, activity, prices=prices)
        write_report(self.cfg.reports_dir, "paper_trading", paper)

        write_report(self.cfg.reports_dir, "pipeline_summary", {
            "sentiment_label": sentiment.overall_label,
            "sentiment_score": sentiment.overall_score,
            "themes": activity.themes,
            "impacted": [t.symbol for t in activity.impacted],
            "scanned": symbols,
            "backtest_return_pct": backtest.aggregate_return_pct,
            "recommended": backtest.recommended_symbols,
            "paper_equity": paper.equity,
            "lessons": paper.lessons,
        })

        return PipelineResult(
            sentiment=sentiment,
            activity=activity,
            backtest=backtest,
            paper=paper,
        )

    def _scan_symbols(self, activity: ActivityReport) -> list[str]:
        batch = rotate_batch(self.cfg.universe, self.cfg.scan_batch_size)
        batch = list(dict.fromkeys([*self.cfg.watchlist, *batch]))
        impacted = [t.symbol for t in activity.impacted]
        return merge_priority(
            batch=batch,
            impacted=impacted,
            held=[],
            limit=self.cfg.trade_universe_limit,
        )

    def _run_sentiment_fast(self) -> SentimentReport:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(self.sentiment_agent.run)
            try:
                return fut.result(timeout=_SENTIMENT_TIMEOUT_SEC)
            except FuturesTimeout:
                log.warning("Sentiment timed out; using neutral fallback")
                return SentimentReport(
                    generated_at=datetime.now(timezone.utc),
                    overall_label="neutral",
                    overall_score=0.0,
                    headline_count=0,
                    bullish_pct=0.0,
                    bearish_pct=0.0,
                    neutral_pct=100.0,
                    notes="Sentiment skipped after timeout; trading continues.",
                )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
