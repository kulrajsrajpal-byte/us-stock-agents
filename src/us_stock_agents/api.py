from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Header, HTTPException
from sqlalchemy import desc, select

from us_stock_agents.db import HermesAdviceRow, RunRow, StrategyRow, TradeRow, init_db, session_scope
from us_stock_agents.hermes import hermes_status
from us_stock_agents.local_coach import coach_status
from us_stock_agents.service import run_cycle
from us_stock_agents.settings import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _scheduled_job() -> None:
    try:
        run_cycle(force=False)
    except Exception:  # noqa: BLE001
        log.exception("Scheduled cycle failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    db = settings.database_url
    scheme = db.split(":", 1)[0]
    log.info("Starting with database scheme=%s", scheme)
    if scheme.startswith("sqlite"):
        log.error(
            "DATABASE_URL is SQLite. On Railway, set DATABASE_URL from the Postgres "
            "service (not localhost). Refusing fragile default will still try to start."
        )
    init_db()
    scheduler.add_job(
        _scheduled_job,
        "interval",
        minutes=max(5, settings.run_interval_minutes),
        id="us_stock_cycle",
        replace_existing=True,
    )
    scheduler.start()
    log.info("Scheduler started every %s minutes", settings.run_interval_minutes)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="US Stock Multi-Agent",
    description="24/7 paper trading + strategy evolution on Railway",
    version="0.2.0",
    lifespan=lifespan,
)


def _check_api_key(x_api_key: str | None) -> None:
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "run_interval_minutes": settings.run_interval_minutes,
        "market_hours_only": settings.market_hours_only,
        "continue_until_profit": settings.continue_until_profit,
        "local_coach": True,
    }


@app.post("/run")
def trigger_run(
    force: bool = True,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_api_key(x_api_key)
    return run_cycle(force=force)


@app.get("/run")
def trigger_run_browser(
    force: bool = True,
    api_key: str | None = None,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Same as POST /run — open this URL in a browser (no terminal needed)."""
    _check_api_key(x_api_key or api_key)
    return run_cycle(force=force)


@app.get("/run/lite")
def trigger_run_lite(
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Faster cycle: force=True, always skip market-hours gate."""
    _check_api_key(x_api_key)
    return run_cycle(force=True)


@app.get("/status")
def status() -> dict[str, Any]:
    init_db()
    with session_scope() as session:
        active = session.scalar(
            select(StrategyRow)
            .where(StrategyRow.status.in_(["active", "champion"]))
            .order_by(desc(StrategyRow.id))
        )
        last = session.scalar(select(RunRow).order_by(desc(RunRow.id)))
        champions = session.scalars(
            select(StrategyRow).where(StrategyRow.is_champion.is_(True))
        ).all()
        return {
            "active_strategy": None
            if not active
            else {
                "id": active.id,
                "version": active.version,
                "fast_ma": active.fast_ma,
                "slow_ma": active.slow_ma,
                "status": active.status,
                "paper_equity": active.paper_equity,
                "paper_return_pct": active.paper_return_pct,
                "trade_count": active.trade_count,
                "is_champion": active.is_champion,
            },
            "champions": [
                {
                    "id": c.id,
                    "version": c.version,
                    "paper_return_pct": c.paper_return_pct,
                    "fast_ma": c.fast_ma,
                    "slow_ma": c.slow_ma,
                }
                for c in champions
            ],
            "last_run": None
            if not last
            else {
                "id": last.id,
                "created_at": last.created_at.isoformat() if last.created_at else None,
                "paper_equity": last.paper_equity,
                "paper_return_pct": last.paper_return_pct,
                "sentiment_label": last.sentiment_label,
                "themes": last.themes,
            },
            "coach": coach_status(),
            "hermes": hermes_status(),
            "profit_target_pct": get_settings().profit_target_pct,
            "continue_until_profit": get_settings().continue_until_profit,
        }


@app.get("/strategies")
def list_strategies(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with session_scope() as session:
        rows = session.scalars(select(StrategyRow).order_by(desc(StrategyRow.id)).limit(limit)).all()
        return [
            {
                "id": r.id,
                "version": r.version,
                "status": r.status,
                "fast_ma": r.fast_ma,
                "slow_ma": r.slow_ma,
                "paper_return_pct": r.paper_return_pct,
                "backtest_return_pct": r.backtest_return_pct,
                "trade_count": r.trade_count,
                "is_champion": r.is_champion,
                "notes": r.notes,
            }
            for r in rows
        ]
@app.get("/trades")
def list_trades(limit: int = 50) -> list[dict[str, Any]]:
    init_db()

    with session_scope() as session:
        rows = session.scalars(
            select(TradeRow)
            .order_by(desc(TradeRow.id))
            .limit(limit)
        ).all()

        return [
            {
                "id": r.id,
                "strategy_id": r.strategy_id,
                "symbol": r.symbol,
                "side": r.side,
                "qty": r.qty,
                "price": r.price,
                "reason": r.reason,
                "pnl_pct": r.pnl_pct,
                "created_at": (
                    r.created_at.isoformat()
                    if r.created_at
                    else None
                ),
            }
            for r in rows
        ]


@app.get("/hermes")
@app.get("/coach")
def coach_info(limit: int = 20) -> dict[str, Any]:
    """Built-in local coach (+ optional Hermes) status and Postgres advice log."""
    init_db()
    with session_scope() as session:
        rows = session.scalars(
            select(HermesAdviceRow).order_by(desc(HermesAdviceRow.id)).limit(limit)
        ).all()
        return {
            "coach": coach_status(),
            "hermes": hermes_status(),
            "recent_advice": [
                {
                    "id": r.id,
                    "strategy_id": r.strategy_id,
                    "event_type": r.event_type,
                    "advice": r.advice,
                    "applied": r.applied,
                    "notes": r.notes,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
