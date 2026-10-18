from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

from us_stock_agents.config import load_config
from us_stock_agents.db import init_db, session_scope
from us_stock_agents.evolution import StrategyEvolver
from us_stock_agents.hermes import hermes_enabled, hermes_status, teach_hermes_cycle
from us_stock_agents.local_coach import coach_status, teach_local_cycle
from us_stock_agents.market_hours import is_us_regular_session
from us_stock_agents.memory import SharedMemory
from us_stock_agents.notify import send_telegram
from us_stock_agents.orchestrator import Orchestrator
from us_stock_agents.persist import sync_strategy_from_pipeline
from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)
_cycle_lock = threading.Lock()
_CYCLE_WALL_TIMEOUT_SEC = 150


def run_cycle(force: bool = False) -> dict[str, Any]:
    """One full sentiment→activity→backtest→paper→evolve cycle."""
    settings = get_settings()
    if settings.market_hours_only and not force and not is_us_regular_session():
        return {
            "skipped": True,
            "reason": "outside_us_market_hours",
            "coach": coach_status(),
            "hermes": hermes_status(),
        }

    if not _cycle_lock.acquire(blocking=False):
        return {
            "skipped": True,
            "reason": "cycle_already_running",
            "coach": coach_status(),
            "hermes": hermes_status(),
        }

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_run_cycle_locked, force, settings)
        try:
            return fut.result(timeout=_CYCLE_WALL_TIMEOUT_SEC)
        except FuturesTimeout:
            log.error("Cycle exceeded %ss wall timeout", _CYCLE_WALL_TIMEOUT_SEC)
            return {
                "skipped": True,
                "reason": "cycle_timeout",
                "coach": coach_status(),
                "hermes": hermes_status(),
            }
    finally:
        # Do not wait for a hung worker — that previously kept the lock forever.
        pool.shutdown(wait=False, cancel_futures=True)
        _cycle_lock.release()


def _run_cycle_locked(force: bool, settings: Any) -> dict[str, Any]:
    init_db()
    cfg = load_config()
    orch = Orchestrator(cfg)
    result = orch.run_pipeline()

    with session_scope() as session:
        strategy = sync_strategy_from_pipeline(session, result, result.paper.lessons)
        decision = StrategyEvolver(memory=SharedMemory(cfg.state_dir)).evaluate(
            session, strategy
        )

        payload: dict[str, Any] = {
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
                "source": (decision.strategy.params or {}).get("source"),
                "stop_loss_pct": (decision.strategy.params or {}).get("stop_loss_pct"),
                "take_profit_pct": (decision.strategy.params or {}).get("take_profit_pct"),
            },
            "evolution": decision.action,
            "evolution_message": decision.message,
            "champion": bool(decision.strategy.is_champion),
            "trades": len(result.paper.trades_today),
            "lessons": result.paper.lessons,
            "coach": coach_status(),
            "hermes": hermes_status(),
            "continue_until_profit": settings.continue_until_profit,
            "profit_target_pct": settings.profit_target_pct,
        }

        payload["coach_taught"] = teach_local_cycle(
            session,
            payload,
            strategy_id=decision.strategy.id,
        )
        payload["hermes_taught"] = False
        if hermes_enabled():
            payload["hermes_taught"] = teach_hermes_cycle(
                payload,
                session=session,
                strategy_id=decision.strategy.id,
            )

    text = (
        "US Stock Agents cycle\n"
        f"Sentiment: {payload['sentiment']} ({payload['sentiment_score']})\n"
        f"Paper equity: ${payload['paper_equity']:,.2f} ({payload['paper_return_pct']}%)\n"
        f"Strategy: v{payload['strategy_version']} "
        f"MA {payload['strategy_params']['fast_ma']}/{payload['strategy_params']['slow_ma']}\n"
        f"Evolution: {payload['evolution']} "
        f"(source={payload['strategy_params'].get('source')})\n"
        f"Local coach: on | Hermes: {'on' if hermes_enabled() else 'off'}\n"
        f"{payload['evolution_message']}"
    )
    send_telegram(text)
    log.info("Cycle complete: %s", payload["evolution_message"])
    return payload
