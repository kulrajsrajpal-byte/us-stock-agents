from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from us_stock_agents.db import MistakeRow, StrategyRow, utcnow
from us_stock_agents.hermes import ask_hermes_for_strategy_patch
from us_stock_agents.settings import get_settings


@dataclass
class EvolutionDecision:
    action: str  # keep | mutate | promote_champion | retire
    strategy: StrategyRow
    message: str


class StrategyEvolver:
    """Promote profitable strategies; mutate losers until a champion emerges."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def evaluate(self, session: Session, strategy: StrategyRow) -> EvolutionDecision:
        initial = self.settings.initial_cash
        equity = strategy.paper_equity or initial
        ret = ((equity - initial) / initial) * 100.0
        strategy.paper_return_pct = round(ret, 2)
        strategy.updated_at = utcnow()

        # Hard stop: too much drawdown → retire + mutate
        if ret <= -self.settings.max_drawdown_pct:
            strategy.status = "retired"
            session.flush()
            child = self._spawn_mutant(session, strategy, reason="max_drawdown")
            return EvolutionDecision(
                action="retire",
                strategy=child,
                message=(
                    f"Strategy v{strategy.version} retired at {ret:.2f}% "
                    f"(max drawdown {self.settings.max_drawdown_pct}%). "
                    f"Spawned v{child.version} with MA {child.fast_ma}/{child.slow_ma}."
                ),
            )

        # Champion: hit profit target with enough trades
        if (
            ret >= self.settings.profit_target_pct
            and (strategy.trade_count or 0) >= self.settings.champion_min_trades
            and not strategy.is_champion
        ):
            # Demote previous champions
            for old in session.scalars(select(StrategyRow).where(StrategyRow.is_champion.is_(True))):
                old.is_champion = False
                if old.status == "champion":
                    old.status = "retired"
            strategy.is_champion = True
            strategy.status = "champion"
            session.flush()
            return EvolutionDecision(
                action="promote_champion",
                strategy=strategy,
                message=(
                    f"CHAMPION: v{strategy.version} paper return {ret:.2f}% "
                    f"with {strategy.trade_count} trades (target {self.settings.profit_target_pct}%)."
                ),
            )

        # Mild loss / flat with enough evidence → mutate parameters and continue paper trading
        if ret < 0 and (strategy.trade_count or 0) >= max(3, self.settings.champion_min_trades // 2):
            strategy.status = "retired"
            session.flush()
            child = self._spawn_mutant(session, strategy, reason="negative_paper_pnl")
            return EvolutionDecision(
                action="mutate",
                strategy=child,
                message=(
                    f"v{strategy.version} underperformed ({ret:.2f}%). "
                    f"Mutated to v{child.version} MA {child.fast_ma}/{child.slow_ma}."
                ),
            )

        return EvolutionDecision(
            action="keep",
            strategy=strategy,
            message=f"Keeping v{strategy.version}: paper return {ret:.2f}%, trades={strategy.trade_count}.",
        )

    def _spawn_mutant(self, session: Session, parent: StrategyRow, reason: str) -> StrategyRow:
        lessons = [
            r.lesson
            for r in session.scalars(
                select(MistakeRow)
                .where(MistakeRow.strategy_id == parent.id)
                .order_by(MistakeRow.id.desc())
                .limit(10)
            ).all()
        ]

        # Prefer Hermes (Railway self-improving agent) when configured
        hermes_patch = ask_hermes_for_strategy_patch(
            parent_fast=parent.fast_ma,
            parent_slow=parent.slow_ma,
            paper_return_pct=parent.paper_return_pct,
            trade_count=parent.trade_count or 0,
            lessons=lessons,
            reason=reason,
        )

        if hermes_patch:
            fast = int(hermes_patch["fast_ma"])
            slow = int(hermes_patch["slow_ma"])
            source = "hermes"
            notes = (
                f"Hermes-mutated from v{parent.version} due to {reason}. "
                f"{hermes_patch.get('rationale', '')}"
            )
        else:
            # Fallback: random nearby MA windows
            candidates_fast = [5, 8, 10, 12, 15, 20]
            candidates_slow = [20, 25, 30, 40, 50, 60]
            fast = random.choice(candidates_fast)
            slow = random.choice([s for s in candidates_slow if s > fast + 5])
            if fast == parent.fast_ma and slow == parent.slow_ma:
                slow = min(slow + 10, 80)
            source = "random"
            notes = f"Mutated from v{parent.version} due to {reason}"

        now = utcnow()
        child = StrategyRow(
            version=(parent.version or 1) + 1,
            name=parent.name,
            fast_ma=fast,
            slow_ma=slow,
            status="active",
            params={
                "fast_ma": fast,
                "slow_ma": slow,
                "parent_id": parent.id,
                "reason": reason,
                "source": source,
            },
            notes=notes[:1000],
            created_at=now,
            updated_at=now,
        )
        session.add(child)
        session.flush()
        return child
