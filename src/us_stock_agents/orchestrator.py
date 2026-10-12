from __future__ import annotations

from us_stock_agents.agents import (
    ActivityAgent,
    BacktestAgent,
    PaperTradingAgent,
    SentimentAgent,
)
from us_stock_agents.config import AppConfig, ensure_dirs
from us_stock_agents.memory import SharedMemory
from us_stock_agents.models import PipelineResult
from us_stock_agents.reporting import write_report


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
        sentiment = self.sentiment_agent.run()
        write_report(self.cfg.reports_dir, "sentiment", sentiment)

        activity = self.activity_agent.run(sentiment)
        write_report(self.cfg.reports_dir, "activity", activity)

        backtest = self.backtest_agent.run(sentiment, activity)
        write_report(self.cfg.reports_dir, "backtest", backtest)

        paper = self.paper_agent.run(backtest, activity)
        write_report(self.cfg.reports_dir, "paper_trading", paper)

        write_report(self.cfg.reports_dir, "pipeline_summary", {
            "sentiment_label": sentiment.overall_label,
            "sentiment_score": sentiment.overall_score,
            "themes": activity.themes,
            "impacted": [t.symbol for t in activity.impacted],
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
