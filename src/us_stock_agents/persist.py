from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from us_stock_agents.db import (
    MistakeRow,
    PortfolioRow,
    RunRow,
    StrategyRow,
    TradeRow,
    get_or_create_active_strategy,
    utcnow,
)
from us_stock_agents.models import PipelineResult
from us_stock_agents.settings import get_settings


def sync_strategy_from_pipeline(
    session: Session,
    result: PipelineResult,
    lessons: list[str],
) -> StrategyRow:
    settings = get_settings()
    strategy = get_or_create_active_strategy(
        session,
        result.paper.strategy_params.get("fast_ma", 10),
        result.paper.strategy_params.get("slow_ma", 30),
    )
    # Align params with what agents just used / suggested
    strategy.fast_ma = int(result.paper.strategy_params.get("fast_ma", strategy.fast_ma))
    strategy.slow_ma = int(result.paper.strategy_params.get("slow_ma", strategy.slow_ma))
    strategy.backtest_return_pct = result.backtest.aggregate_return_pct
    strategy.paper_equity = result.paper.equity
    strategy.trade_count = (strategy.trade_count or 0) + len(result.paper.trades_today)
    strategy.params = {
        "fast_ma": strategy.fast_ma,
        "slow_ma": strategy.slow_ma,
        "recommended": result.backtest.recommended_symbols,
    }
    strategy.updated_at = utcnow()

    # Portfolio upsert
    port = session.scalar(
        select(PortfolioRow).where(PortfolioRow.strategy_id == strategy.id).order_by(PortfolioRow.id.desc())
    )
    if port is None:
        port = PortfolioRow(
            strategy_id=strategy.id,
            cash=result.paper.cash,
            positions=result.paper.positions,
            equity=result.paper.equity,
            updated_at=utcnow(),
        )
        session.add(port)
    else:
        port.cash = result.paper.cash
        port.positions = result.paper.positions
        port.equity = result.paper.equity
        port.updated_at = utcnow()

    for t in result.paper.trades_today:
        session.add(
            TradeRow(
                strategy_id=strategy.id,
                symbol=t.symbol,
                side=t.side,
                qty=t.qty,
                price=t.price,
                reason=t.reason,
                created_at=t.timestamp,
            )
        )

    for lesson in lessons:
        session.add(
            MistakeRow(
                strategy_id=strategy.id,
                symbol="",
                pnl_pct=0.0,
                lesson=lesson,
                created_at=utcnow(),
            )
        )

    initial = settings.initial_cash
    paper_ret = ((result.paper.equity - initial) / initial) * 100.0
    session.add(
        RunRow(
            strategy_id=strategy.id,
            sentiment_label=result.sentiment.overall_label,
            sentiment_score=result.sentiment.overall_score,
            themes=result.activity.themes,
            backtest_return_pct=result.backtest.aggregate_return_pct,
            paper_equity=result.paper.equity,
            paper_return_pct=round(paper_ret, 2),
            lessons=lessons,
            summary={
                "impacted": [i.symbol for i in result.activity.impacted],
                "recommended": result.backtest.recommended_symbols,
            },
            created_at=utcnow(),
        )
    )
    session.flush()
    return strategy


def recent_mistakes(session: Session, strategy_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(MistakeRow)
        .where(MistakeRow.strategy_id == strategy_id)
        .order_by(MistakeRow.id.desc())
        .limit(limit)
    ).all()
    return [{"lesson": r.lesson, "pnl_pct": r.pnl_pct, "symbol": r.symbol} for r in rows]
