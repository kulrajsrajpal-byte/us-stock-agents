from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from us_stock_agents.db import HermesAdviceRow, utcnow
from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)


def hermes_enabled() -> bool:
    s = get_settings()
    return bool(s.hermes_enabled and s.hermes_api_url and s.hermes_api_key)


def hermes_status() -> dict[str, Any]:
    s = get_settings()
    return {
        "enabled": hermes_enabled(),
        "configured_url": bool(s.hermes_api_url),
        "configured_key": bool(s.hermes_api_key),
        "model": s.hermes_model,
        "profit_target_pct": s.profit_target_pct,
        "continue_until_profit": s.continue_until_profit,
    }


def ask_hermes_for_strategy_patch(
    *,
    parent_fast: int,
    parent_slow: int,
    paper_return_pct: float | None,
    trade_count: int,
    lessons: list[str],
    reason: str,
    recent_sells: list[dict[str, Any]] | None = None,
    session: Session | None = None,
    strategy_id: int | None = None,
) -> dict[str, Any] | None:
    """Ask Hermes for next MA params (+ optional risk hints). Persist advice to Postgres."""
    if not hermes_enabled():
        return None

    settings = get_settings()
    sells = recent_sells or []
    prompt = (
        "You are the strategy coach for a US equities PAPER-trading system "
        "(no real broker orders). Goal: reach positive paper return and keep improving.\n"
        "Stack: MA trend + RSI mean-reversion + breakout entries; "
        "exits: stop-loss, take-profit, RSI overbought, MA bearish, momentum fade.\n"
        f"Parent params: fast_ma={parent_fast}, slow_ma={parent_slow}.\n"
        f"Paper return %: {paper_return_pct}. Trades: {trade_count}.\n"
        f"Mutation/consult reason: {reason}.\n"
        f"Profit target %: {settings.profit_target_pct}. "
        f"Max drawdown %: {settings.max_drawdown_pct}.\n"
        f"Recent lessons: {lessons[:12]}.\n"
        f"Recent sells (symbol/pnl/reason): {sells[:12]}.\n\n"
        "Propose NEW integers different from the parent. Constraints: "
        "slow_ma >= fast_ma + 6, fast_ma in [5,25], slow_ma in [20,80]. "
        "Optional: stop_loss_pct in [3,8], take_profit_pct in [6,20].\n"
        "Reply with ONLY compact JSON (no markdown):\n"
        '{"fast_ma": <int>, "slow_ma": <int>, "stop_loss_pct": <num>, '
        '"take_profit_pct": <num>, "rationale": "<short>"}\n'
    )

    raw = _chat(
        system="Return only valid JSON for strategy patches. Paper trading only.",
        user=prompt,
        temperature=0.4,
    )
    if not raw:
        return None

    parsed = _extract_json(raw)
    if not parsed:
        log.warning("Hermes returned non-JSON content: %s", raw[:300])
        _store_advice(
            session,
            strategy_id=strategy_id,
            event_type="patch",
            request={"reason": reason, "parent_fast": parent_fast, "parent_slow": parent_slow},
            advice={"raw": raw[:2000]},
            applied=False,
            notes="parse_failed",
        )
        return None

    try:
        fast = int(parsed["fast_ma"])
        slow = int(parsed["slow_ma"])
    except (KeyError, TypeError, ValueError):
        return None

    if not (5 <= fast <= 25 and 20 <= slow <= 80 and slow >= fast + 6):
        log.warning("Hermes params out of bounds: %s", parsed)
        return None

    stop = parsed.get("stop_loss_pct")
    take = parsed.get("take_profit_pct")
    try:
        stop_f = float(stop) if stop is not None else None
        take_f = float(take) if take is not None else None
    except (TypeError, ValueError):
        stop_f, take_f = None, None

    advice = {
        "fast_ma": fast,
        "slow_ma": slow,
        "stop_loss_pct": stop_f,
        "take_profit_pct": take_f,
        "rationale": str(parsed.get("rationale", "hermes suggestion"))[:500],
        "source": "hermes",
    }
    _store_advice(
        session,
        strategy_id=strategy_id,
        event_type="patch",
        request={
            "reason": reason,
            "parent_fast": parent_fast,
            "parent_slow": parent_slow,
            "paper_return_pct": paper_return_pct,
            "trade_count": trade_count,
            "lessons": lessons[:12],
            "recent_sells": sells[:12],
        },
        advice=advice,
        applied=True,
        notes=advice["rationale"],
    )
    return advice


def teach_hermes_cycle(
    summary: dict[str, Any],
    *,
    session: Session | None = None,
    strategy_id: int | None = None,
) -> bool:
    """Send cycle outcome to Hermes memory and store a teach event in Postgres."""
    if not hermes_enabled():
        return False

    raw = _chat(
        system=(
            "Store this paper-trading cycle as long-term memory for future "
            "US stock strategy coaching. Acknowledge in one short sentence. "
            "Paper trading only — no live brokerage."
        ),
        user=json.dumps(summary, default=str)[:6000],
        temperature=0.2,
    )
    ok = bool(raw)
    _store_advice(
        session,
        strategy_id=strategy_id,
        event_type="teach",
        request={"summary_keys": list(summary.keys())},
        advice={"ack": (raw or "")[:500], "ok": ok},
        applied=ok,
        notes="cycle_teach",
    )
    return ok


def _chat(*, system: str, user: str, temperature: float) -> str | None:
    settings = get_settings()
    url = settings.hermes_api_url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.hermes_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.hermes_model or "default",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Hermes chat failed: %s", exc)
        return None


def _store_advice(
    session: Session | None,
    *,
    strategy_id: int | None,
    event_type: str,
    request: dict[str, Any],
    advice: dict[str, Any],
    applied: bool,
    notes: str,
) -> None:
    if session is None:
        return
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
        log.warning("Failed to persist Hermes advice: %s", exc)


def _extract_json(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
