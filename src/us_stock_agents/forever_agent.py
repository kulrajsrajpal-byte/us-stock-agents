from __future__ import annotations

"""Single autonomous 24×7 paper-trading agent.

Loop (no human in the middle):
  1. Scan liquid US names
  2. Paper-trade with current strategy
  3. Store fills / P&L / mistakes in Postgres
  4. Learn from losses → mutate strategy
  5. Repeat on Railway scheduler until a real profitable champion

Inspired by HKUDS/Vibe-Trading paper accounts (daily paper step, never a live broker).
We keep our own Postgres ledger so Railway stays self-contained without Vibe's LLM/runtime.
"""

import logging
from typing import Any

from us_stock_agents.local_coach import coach_status
from us_stock_agents.service import run_cycle
from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)


class ForeverAgent:
    """One agent. Paper only. Runs until profit, learning from every mistake."""

    name = "forever_agent"
    mode = "paper_only"

    def __init__(self) -> None:
        self.settings = get_settings()

    def status(self) -> dict[str, Any]:
        return {
            "agent": self.name,
            "mode": self.mode,
            "autonomous": True,
            "human_required": False,
            "run_interval_minutes": self.settings.run_interval_minutes,
            "market_hours_only": self.settings.market_hours_only,
            "continue_until_profit": self.settings.continue_until_profit,
            "profit_target_pct": self.settings.profit_target_pct,
            "local_coach": coach_status(),
            "broker": "none_paper_ledger_postgres",
            "inspired_by": "HKUDS/Vibe-Trading paper_trade_step (no live broker path)",
            "loop": [
                "scan_us_liquid_universe",
                "paper_trade",
                "persist_postgres",
                "learn_from_mistakes",
                "mutate_until_profit",
            ],
        }

    def run_once(self, *, force: bool = True) -> dict[str, Any]:
        """One unattended cycle. Scheduler calls this forever on Railway."""
        log.info("ForeverAgent cycle start force=%s", force)
        result = run_cycle(force=force)
        result["agent"] = self.name
        result["autonomous"] = True
        log.info(
            "ForeverAgent cycle done skipped=%s evo=%s ret=%s champ=%s",
            result.get("skipped"),
            result.get("evolution"),
            result.get("paper_return_pct"),
            result.get("champion"),
        )
        return result


_agent: ForeverAgent | None = None


def get_forever_agent() -> ForeverAgent:
    global _agent
    if _agent is None:
        _agent = ForeverAgent()
    return _agent
