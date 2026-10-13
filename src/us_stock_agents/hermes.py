from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)


def hermes_enabled() -> bool:
    s = get_settings()
    return bool(s.hermes_enabled and s.hermes_api_url and s.hermes_api_key)


def ask_hermes_for_strategy_patch(
    *,
    parent_fast: int,
    parent_slow: int,
    paper_return_pct: float | None,
    trade_count: int,
    lessons: list[str],
    reason: str,
) -> dict[str, Any] | None:
    """Ask Hermes (Railway self-improving agent) for next MA params.

    Returns {"fast_ma": int, "slow_ma": int, "rationale": str} or None on failure.
    """
    if not hermes_enabled():
        return None

    settings = get_settings()
    prompt = (
        "You are the strategy coach for a US equities paper-trading system.\n"
        "Current strategy is a long-only moving-average crossover.\n"
        f"Parent params: fast_ma={parent_fast}, slow_ma={parent_slow}.\n"
        f"Paper return %: {paper_return_pct}. Trades: {trade_count}.\n"
        f"Mutation reason: {reason}.\n"
        f"Recent lessons/mistakes: {lessons[:10]}.\n\n"
        "Propose NEW integer parameters that are different from the parent, "
        "with slow_ma >= fast_ma + 6, fast_ma between 5 and 25, slow_ma between 20 and 80.\n"
        "Reply with ONLY compact JSON (no markdown): "
        '{"fast_ma": <int>, "slow_ma": <int>, "rationale": "<short>"}\n'
    )

    url = settings.hermes_api_url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.hermes_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.hermes_model or "default",
        "messages": [
            {"role": "system", "content": "Return only valid JSON for strategy patches."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        parsed = _extract_json(content)
        if not parsed:
            log.warning("Hermes returned non-JSON content: %s", content[:300])
            return None
        fast = int(parsed["fast_ma"])
        slow = int(parsed["slow_ma"])
        if not (5 <= fast <= 25 and 20 <= slow <= 80 and slow >= fast + 6):
            log.warning("Hermes params out of bounds: %s", parsed)
            return None
        return {
            "fast_ma": fast,
            "slow_ma": slow,
            "rationale": str(parsed.get("rationale", "hermes suggestion"))[:500],
            "source": "hermes",
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("Hermes strategy patch failed: %s", exc)
        return None


def teach_hermes_cycle(summary: dict[str, Any]) -> bool:
    """Send cycle outcome to Hermes so it can remember what worked/failed."""
    if not hermes_enabled():
        return False

    settings = get_settings()
    url = settings.hermes_api_url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.hermes_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.hermes_model or "default",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Store this paper-trading cycle as long-term memory for future "
                    "US stock strategy coaching. Acknowledge in one short sentence."
                ),
            },
            {"role": "user", "content": json.dumps(summary, default=str)[:6000]},
        ],
        "temperature": 0.2,
    }
    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Hermes teach failed: %s", exc)
        return False


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
