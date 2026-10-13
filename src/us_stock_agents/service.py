from __future__ import annotations

import logging
from typing import Any

from us_stock_agents.config import load_config
from us_stock_agents.db import init_db, session_scope
from us_stock_agents.evolution import StrategyEvolver
from us_stock_agents.hermes import teach_hermes_cycle
from us_stock_agents.market_hours import is_us_regular_session
from us_stock_agents.notify import send_telegram
from us_stock_agents.orchestrator import Orchestrator
from us_stock_agents.persist import sync_strategy_from_pipeline
from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)


def run_cycle(force: bool = False) -> dict[str, Any]:
    """One full sentiment→activity→backtest→paper→evolve cycle."""
    settings = get_settings()
    if settings.market_hours_only and not force and not is_us_regular_session():
        return {
            "skipped": True,
            "reason": "outside_us_market_hours",
        }

    init_db()
    cfg = load_config()
    orch = Orchestrator(cfg)
    result = orch.run_pipeline()

    with session_scope() as session:
        strategy = sync_strategy_from_pipeline(session, result, result.paper.lessons)
        decision = StrategyEvolver().evaluate(session, strategy)

        payload = {
            "skipped": False,
            "sentiment": result.sentiment.overall_label,
            "sentiment_score": result.sentiment.overall_score,
            "themes": result.activity.themes,
            "backtest_return_pct": result.backtest.aggregate_return_pct,
            "paper_equity": result.paper.equity,
            "paper_return_pct": strategy.paper_return_pct,
            "strategy_version": decision.strategy.version,
            "strategy_params": {
                "fast_ma": decision.strategy.fast_ma,
                "slow_ma": decision.strategy.slow_ma,
            },
            "evolution": decision.action,
            "evolution_message": decision.message,
            "champion": decision.strategy.is_champion,
            "trades": len(result.paper.trades_today),
            "lessons": result.paper.lessons,
        }

    # Feed Hermes long-term memory (Railway) so future mutations learn from outcomes
    payload["hermes_taught"] = teach_hermes_cycle(payload)

    text = (
        "US Stock Agents cycle\n"
        f"Sentiment: {payload['sentiment']} ({payload['sentiment_score']})\n"
        f"Paper equity: ${payload['paper_equity']:,.2f} ({payload['paper_return_pct']}%)\n"
        f"Strategy: v{payload['strategy_version']} "
        f"MA {payload['strategy_params']['fast_ma']}/{payload['strategy_params']['slow_ma']}\n"
        f"Evolution: {payload['evolution']}\n"
        f"{payload['evolution_message']}"
    )
    send_telegram(text)
    log.info("Cycle complete: %s", payload["evolution_message"])
    return payload
