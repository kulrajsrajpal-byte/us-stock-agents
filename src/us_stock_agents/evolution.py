from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from us_stock_agents.db import MistakeRow, StrategyRow, TradeRow, utcnow
from us_stock_agents.hermes import ask_hermes_for_strategy_patch, hermes_enabled
from us_stock_agents.local_coach import propose_strategy_patch
from us_stock_agents.memory import SharedMemory, StrategyState
from us_stock_agents.settings import get_settings


@dataclass
class EvolutionDecision:
    action: str  # keep | mutate | promote_champion | retire | consult
    strategy: StrategyRow
    message: str


class StrategyEvolver:
    """Promote profitable strategies; mutate with local coach (Hermes optional)."""

    def __init__(self, memory: SharedMemory | None = None) -> None:
        self.settings = get_settings()
        self.memory = memory

    def evaluate(self, session: Session, strategy: StrategyRow) -> EvolutionDecision:
        initial = self.settings.initial_cash
        equity = strategy.paper_equity or initial
        ret = ((equity - initial) / initial) * 100.0
        strategy.paper_return_pct = round(ret, 2)
        strategy.updated_at = utcnow()

        sells = self._recent_sells(session, strategy.id)
        sell_pnls = [
            float(s["pnl_pct"])
            for s in sells
            if s.get("pnl_pct") is not None
        ]
        avg_sell = sum(sell_pnls) / len(sell_pnls) if sell_pnls else 0.0
        # Inflated equity with only losing/no sells is accounting noise — keep searching.
        bogus_equity = ret >= 25.0 and (not sell_pnls or avg_sell < 0)

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
                    f"Spawned v{child.version} MA {child.fast_ma}/{child.slow_ma} "
                    f"(source={child.params.get('source')})."
                ),
            )

        if strategy.is_champion or strategy.status == "champion":
            if bogus_equity or (
                len(sell_pnls) >= 2 and avg_sell < 0 and ret < self.settings.profit_target_pct
            ):
                strategy.is_champion = False
                strategy.status = "retired"
                session.flush()
                child = self._spawn_mutant(session, strategy, reason="false_champion_unprofitable_sells")
                return EvolutionDecision(
                    action="mutate",
                    strategy=child,
                    message=(
                        f"Demoted fake champion v{strategy.version} "
                        f"(equity ret {ret:.2f}%, avg sell {avg_sell:.2f}%). "
                        f"Spawned v{child.version} MA {child.fast_ma}/{child.slow_ma}."
                    ),
                )
            return EvolutionDecision(
                action="keep",
                strategy=strategy,
                message=(
                    f"Champion v{strategy.version} still active: "
                    f"paper return {ret:.2f}%, avg sell {avg_sell:.2f}%."
                ),
            )

        if (
            ret >= self.settings.profit_target_pct
            and not bogus_equity
            and len(sell_pnls) >= self.settings.champion_min_trades
            and avg_sell > 0
            and not strategy.is_champion
        ):
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
                    f"(avg sell {avg_sell:.2f}% over {len(sell_pnls)} sells, "
                    f"target {self.settings.profit_target_pct}%). "
                    "Self-improving loop pauses promotion search on this champion."
                ),
            )

        min_evidence = max(3, self.settings.champion_min_trades // 2)

        if ret < 0 and (strategy.trade_count or 0) >= min_evidence:
            strategy.status = "retired"
            session.flush()
            child = self._spawn_mutant(session, strategy, reason="negative_paper_pnl")
            return EvolutionDecision(
                action="mutate",
                strategy=child,
                message=(
                    f"v{strategy.version} underperformed ({ret:.2f}%). "
                    f"Mutated to v{child.version} MA {child.fast_ma}/{child.slow_ma} "
                    f"(source={child.params.get('source')})."
                ),
            )

        if (
            self.settings.continue_until_profit
            and abs(ret) <= self.settings.hermes_flat_band_pct
            and (strategy.trade_count or 0)
            >= max(min_evidence, self.settings.champion_min_trades)
        ):
            strategy.status = "retired"
            session.flush()
            child = self._spawn_mutant(session, strategy, reason="flat_no_champion_yet")
            return EvolutionDecision(
                action="consult",
                strategy=child,
                message=(
                    f"v{strategy.version} flat at {ret:.2f}% after {strategy.trade_count} trades. "
                    f"Coach spawned v{child.version} MA {child.fast_ma}/{child.slow_ma} "
                    f"(source={child.params.get('source')})."
                ),
            )

        return EvolutionDecision(
            action="keep",
            strategy=strategy,
            message=(
                f"Keeping v{strategy.version}: paper return {ret:.2f}%, "
                f"trades={strategy.trade_count}. "
                f"Searching until +{self.settings.profit_target_pct}% champion."
            ),
        )

    def _recent_sells(self, session: Session, strategy_id: int) -> list[dict[str, Any]]:
        rows = session.scalars(
            select(TradeRow)
            .where(TradeRow.strategy_id == strategy_id, TradeRow.side == "sell")
            .order_by(TradeRow.id.desc())
            .limit(12)
        ).all()
        return [
            {
                "symbol": r.symbol,
                "pnl_pct": r.pnl_pct,
                "reason": r.reason,
                "price": r.price,
            }
            for r in rows
        ]

    def _spawn_mutant(self, session: Session, parent: StrategyRow, reason: str) -> StrategyRow:
        lessons = [
            r.lesson
            for r in session.scalars(
                select(MistakeRow)
                .where(MistakeRow.strategy_id == parent.id)
                .order_by(MistakeRow.id.desc())
                .limit(12)
            ).all()
        ]
        recent_sells = self._recent_sells(session, parent.id)

        patch: dict[str, Any] | None = None
        source = "random"

        # 1) Built-in Postgres learner (no Hermes login required)
        if self.settings.local_coach_enabled:
            patch = propose_strategy_patch(
                session,
                parent=parent,
                lessons=lessons,
                recent_sells=recent_sells,
                reason=reason,
            )
            source = "local_coach"

        # 2) Optional external Hermes override if enabled and reachable
        if hermes_enabled():
            hermes_patch = ask_hermes_for_strategy_patch(
                parent_fast=parent.fast_ma,
                parent_slow=parent.slow_ma,
                paper_return_pct=parent.paper_return_pct,
                trade_count=parent.trade_count or 0,
                lessons=lessons,
                reason=reason,
                recent_sells=recent_sells,
                session=session,
                strategy_id=parent.id,
            )
            if hermes_patch:
                patch = hermes_patch
                source = "hermes"

        if patch:
            fast = int(patch["fast_ma"])
            slow = int(patch["slow_ma"])
            notes = (
                f"{source}-mutated from v{parent.version} due to {reason}. "
                f"{patch.get('rationale', '')}"
            )
            stop = patch.get("stop_loss_pct")
            take = patch.get("take_profit_pct")
        else:
            candidates_fast = [5, 8, 10, 12, 15, 20]
            candidates_slow = [20, 25, 30, 40, 50, 60]
            fast = random.choice(candidates_fast)
            slow = random.choice([s for s in candidates_slow if s > fast + 5])
            if fast == parent.fast_ma and slow == parent.slow_ma:
                slow = min(slow + 10, 80)
            source = "random"
            notes = f"Mutated from v{parent.version} due to {reason}"
            stop = None
            take = None

        now = utcnow()
        params: dict[str, Any] = {
            "fast_ma": fast,
            "slow_ma": slow,
            "parent_id": parent.id,
            "reason": reason,
            "source": source,
        }
        if stop is not None:
            params["stop_loss_pct"] = stop
        if take is not None:
            params["take_profit_pct"] = take

        child = StrategyRow(
            version=(parent.version or 1) + 1,
            name=parent.name,
            fast_ma=fast,
            slow_ma=slow,
            status="active",
            params=params,
            notes=notes[:1000],
            created_at=now,
            updated_at=now,
        )
        session.add(child)
        session.flush()

        if self.memory is not None:
            self.memory.save_strategy(
                StrategyState(
                    version=child.version,
                    fast_ma=fast,
                    slow_ma=slow,
                    strategy_id=child.id,
                    stop_loss_pct=float(stop) if stop is not None else None,
                    take_profit_pct=float(take) if take is not None else None,
                    source=source,
                )
            )
        return child
