from __future__ import annotations

import logging
import random
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from us_stock_agents.db import HermesAdviceRow, StrategyRow, utcnow
from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)

# Search grid used by the built-in coach (no external Hermes/LLM required).
FAST_GRID = [5, 7, 8, 10, 12, 15, 18, 20, 22, 25]
SLOW_GRID = [20, 25, 30, 35, 40, 45, 50, 55, 60, 70, 80]


def coach_status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "enabled": bool(settings.local_coach_enabled),
        "engine": "local_postgres_learner",
        "profit_target_pct": settings.profit_target_pct,
        "continue_until_profit": settings.continue_until_profit,
        "needs_external_api": False,
    }


def propose_strategy_patch(
    session: Session,
    *,
    parent: StrategyRow,
    lessons: list[str],
    recent_sells: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    """Learn from all prior strategies/trades in Postgres and propose next params."""
    settings = get_settings()
    history = session.scalars(select(StrategyRow).order_by(StrategyRow.id.asc())).all()

    tried = {(int(s.fast_ma), int(s.slow_ma)) for s in history}
    scored = [
        s
        for s in history
        if s.paper_return_pct is not None and (s.trade_count or 0) >= 1
    ]
    scored.sort(key=lambda s: float(s.paper_return_pct or -999), reverse=True)

    best = scored[0] if scored else None
    worst = scored[-1] if scored else None

    # Start from parent, then bias toward historically better windows.
    fast = int(parent.fast_ma)
    slow = int(parent.slow_ma)
    stop = float((parent.params or {}).get("stop_loss_pct") or 5.0)
    take = float((parent.params or {}).get("take_profit_pct") or 10.0)
    rationale_parts: list[str] = [f"reason={reason}"]

    if best and best.id != parent.id and float(best.paper_return_pct or 0) > float(
        parent.paper_return_pct or -999
    ):
        # Move halfway toward the best historical strategy.
        fast = int(round((fast + int(best.fast_ma)) / 2))
        slow = int(round((slow + int(best.slow_ma)) / 2))
        rationale_parts.append(
            f"biased toward best v{best.version} "
            f"({best.fast_ma}/{best.slow_ma}, {best.paper_return_pct}%)"
        )

    # Learn from sell outcomes.
    stop_hits = [s for s in recent_sells if "Stop-loss" in str(s.get("reason", ""))]
    take_hits = [s for s in recent_sells if "Take-profit" in str(s.get("reason", ""))]
    ma_exits = [s for s in recent_sells if "MA bearish" in str(s.get("reason", ""))]
    neg = [s for s in recent_sells if (s.get("pnl_pct") is not None and float(s["pnl_pct"]) < 0)]
    pos = [s for s in recent_sells if (s.get("pnl_pct") is not None and float(s["pnl_pct"]) > 0)]

    if len(stop_hits) >= 2:
        slow = min(slow + 5, 80)
        stop = min(stop + 1.0, 8.0)
        rationale_parts.append("many stop-outs → slower MA + wider stop")
    if len(take_hits) == 0 and len(recent_sells) >= 2:
        take = max(take - 1.0, 6.0)
        rationale_parts.append("few take-profits → slightly lower target")
    if len(ma_exits) >= 2 and neg:
        fast = max(fast - 1, 5)
        rationale_parts.append("choppy MA exits → faster signal")
    if neg and not pos:
        slow = min(slow + 5, 80)
        rationale_parts.append("only losing sells → slower trend filter")
    if pos and mean(float(s["pnl_pct"]) for s in pos) >= 5:
        # Keep winners' neighborhood but explore a bit.
        rationale_parts.append("positive sells present → explore nearby window")

    # Lesson text heuristics
    blob = " ".join(lessons).lower()
    if "drawdown" in blob or "risk-off" in blob:
        slow = min(slow + 5, 80)
        stop = min(stop + 0.5, 8.0)
        rationale_parts.append("lessons mention risk-off/drawdown")

    if worst and (fast, slow) == (int(worst.fast_ma), int(worst.slow_ma)):
        slow = min(slow + 10, 80)
        rationale_parts.append("avoided repeating worst historical pair")

    # Snap to grid and ensure constraints.
    fast = _nearest(FAST_GRID, fast)
    slow = _nearest([s for s in SLOW_GRID if s >= fast + 6], slow)
    if slow < fast + 6:
        slow = min(fast + 10, 80)

    # Never reuse an already-failed pair if alternatives exist.
    failed_pairs = {
        (int(s.fast_ma), int(s.slow_ma))
        for s in scored
        if float(s.paper_return_pct or 0) < 0
    }
    candidate = (fast, slow)
    if candidate in failed_pairs or candidate in tried:
        alternatives = [
            (f, s)
            for f in FAST_GRID
            for s in SLOW_GRID
            if s >= f + 6 and (f, s) not in failed_pairs
        ]
        # Prefer untried; else any non-failed.
        untried = [p for p in alternatives if p not in tried]
        pool = untried or alternatives
        if pool:
            # Rank by distance to best (or parent)
            anchor_f = int(best.fast_ma) if best else int(parent.fast_ma)
            anchor_s = int(best.slow_ma) if best else int(parent.slow_ma)
            pool.sort(key=lambda p: abs(p[0] - anchor_f) + abs(p[1] - anchor_s))
            # Small random jitter among top-5 nearby options
            pick = random.choice(pool[: max(1, min(5, len(pool)))])
            fast, slow = pick
            rationale_parts.append(f"selected untried/non-failed pair {fast}/{slow}")

    if (fast, slow) == (int(parent.fast_ma), int(parent.slow_ma)):
        # Force a change
        for f, s in [
            (fast, min(slow + 5, 80)),
            (max(fast - 2, 5), slow),
            (min(fast + 2, 25), min(slow + 10, 80)),
        ]:
            if s >= f + 6 and (f, s) != (parent.fast_ma, parent.slow_ma):
                fast, slow = f, s
                break
        rationale_parts.append("forced parameter change from parent")

    advice = {
        "fast_ma": int(fast),
        "slow_ma": int(slow),
        "stop_loss_pct": round(float(stop), 2),
        "take_profit_pct": round(float(take), 2),
        "rationale": "; ".join(rationale_parts)[:500],
        "source": "local_coach",
        "best_historical": None
        if not best
        else {
            "version": best.version,
            "fast_ma": best.fast_ma,
            "slow_ma": best.slow_ma,
            "paper_return_pct": best.paper_return_pct,
        },
        "history_count": len(history),
        "profit_target_pct": settings.profit_target_pct,
    }

    _store_advice(
        session,
        strategy_id=parent.id,
        event_type="local_patch",
        request={
            "reason": reason,
            "parent_fast": parent.fast_ma,
            "parent_slow": parent.slow_ma,
            "paper_return_pct": parent.paper_return_pct,
            "trade_count": parent.trade_count,
            "lessons": lessons[:12],
            "recent_sells": recent_sells[:12],
        },
        advice=advice,
        applied=True,
        notes=advice["rationale"],
    )
    log.info("Local coach proposal: %s", advice)
    return advice


def teach_local_cycle(
    session: Session,
    summary: dict[str, Any],
    *,
    strategy_id: int | None,
) -> bool:
    """Persist cycle learning event in Postgres (Hermes-free memory)."""
    _store_advice(
        session,
        strategy_id=strategy_id,
        event_type="local_teach",
        request={"summary_keys": list(summary.keys())},
        advice={
            "paper_return_pct": summary.get("paper_return_pct"),
            "paper_equity": summary.get("paper_equity"),
            "evolution": summary.get("evolution"),
            "strategy_version": summary.get("strategy_version"),
            "champion": summary.get("champion"),
            "lessons": summary.get("lessons"),
        },
        applied=True,
        notes="cycle_teach_local",
    )
    return True


def _nearest(options: list[int], value: int) -> int:
    if not options:
        return value
    return min(options, key=lambda x: abs(x - value))


def _store_advice(
    session: Session,
    *,
    strategy_id: int | None,
    event_type: str,
    request: dict[str, Any],
    advice: dict[str, Any],
    applied: bool,
    notes: str,
) -> None:
    try:
        session.add(
            HermesAdviceRow(
                strategy_id=strategy_id,
                event_type=event_type,
                request_summary=request,
                advice=advice,
                applied=applied,
                notes=notes[:1000],
                created_at=utcnow(),
            )
        )
        session.flush()
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to persist local coach advice: %s", exc)
