from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select


@dataclass
class StrategyState:
    version: int
    fast_ma: int
    slow_ma: int
    last_backtest_return_pct: float | None = None
    last_paper_equity: float | None = None
    updated_at: str | None = None
    strategy_id: int | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    source: str | None = None


class SharedMemory:
    """File + Postgres shared memory for specialist agents."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.strategy_path = self.state_dir / "strategy.json"
        self.journal_path = self.state_dir / "trade_journal.jsonl"
        self.mistakes_path = self.state_dir / "mistakes.jsonl"

    def load_strategy(self, default_fast: int, default_slow: int) -> StrategyState:
        # Prefer active DB strategy (Railway Postgres)
        try:
            from us_stock_agents.db import StrategyRow, init_db, session_scope

            init_db()
            with session_scope() as session:
                row = session.scalar(
                    select(StrategyRow)
                    .where(StrategyRow.status.in_(["active", "champion"]))
                    .order_by(StrategyRow.id.desc())
                )
                if row:
                    state = StrategyState(
                        version=row.version,
                        fast_ma=row.fast_ma,
                        slow_ma=row.slow_ma,
                        last_backtest_return_pct=row.backtest_return_pct,
                        last_paper_equity=row.paper_equity,
                        strategy_id=row.id,
                    )
                    self.save_strategy(state)  # mirror to disk for local tools
                    return state
        except Exception:  # noqa: BLE001
            pass

        if not self.strategy_path.exists():
            return StrategyState(
                version=1,
                fast_ma=default_fast,
                slow_ma=default_slow,
            )
        data = json.loads(self.strategy_path.read_text(encoding="utf-8"))
        return StrategyState(**{k: v for k, v in data.items() if k in StrategyState.__annotations__})

    def save_strategy(self, state: StrategyState) -> None:
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self.strategy_path.write_text(
            json.dumps(asdict(state), indent=2),
            encoding="utf-8",
        )
        try:
            from us_stock_agents.db import StrategyRow, init_db, session_scope, utcnow

            init_db()
            with session_scope() as session:
                row = None
                if state.strategy_id:
                    row = session.get(StrategyRow, state.strategy_id)
                if row is None:
                    row = session.scalar(
                        select(StrategyRow)
                        .where(StrategyRow.status.in_(["active", "champion"]))
                        .order_by(StrategyRow.id.desc())
                    )
                if row:
                    row.fast_ma = state.fast_ma
                    row.slow_ma = state.slow_ma
                    row.version = state.version
                    row.backtest_return_pct = state.last_backtest_return_pct
                    row.paper_equity = state.last_paper_equity
                    row.params = {"fast_ma": state.fast_ma, "slow_ma": state.slow_ma}
                    row.updated_at = utcnow()
        except Exception:  # noqa: BLE001
            pass

    def append_journal(self, record: dict[str, Any]) -> None:
        with self.journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def append_mistake(self, record: dict[str, Any]) -> None:
        with self.mistakes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def recent_mistakes(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            from us_stock_agents.db import MistakeRow, init_db, session_scope

            init_db()
            with session_scope() as session:
                rows = session.scalars(
                    select(MistakeRow).order_by(MistakeRow.id.desc()).limit(limit)
                ).all()
                if rows:
                    return [
                        {"lesson": r.lesson, "pnl_pct": r.pnl_pct, "symbol": r.symbol}
                        for r in rows
                    ]
        except Exception:  # noqa: BLE001
            pass

        if not self.mistakes_path.exists():
            return []
        lines = self.mistakes_path.read_text(encoding="utf-8").strip().splitlines()
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            if line.strip():
                out.append(json.loads(line))
        return out
