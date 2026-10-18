from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from us_stock_agents.db import TradeRow, init_db, session_scope
from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)


def reconstruct_positions_from_trades(
    strategy_id: int | None = None,
) -> dict[str, Any] | None:
    """Rebuild open paper lots from Postgres trade history (BUY − SELL).

    When strategy_id is set, only that strategy's trades are used so each
    mutant starts from a clean book instead of stacking every historical buy.
    """
    try:
        init_db()
        with session_scope() as session:
            stmt = select(TradeRow).order_by(TradeRow.id.asc())
            if strategy_id is not None:
                stmt = stmt.where(TradeRow.strategy_id == strategy_id)
            rows = session.scalars(stmt).all()
            if not rows:
                return {
                    "cash": float(get_settings().initial_cash),
                    "positions": {},
                    "hydrated_from": "trades_empty",
                }

            lots: dict[str, list[dict[str, Any]]] = {}
            cash = float(get_settings().initial_cash)

            for row in rows:
                sym = row.symbol
                lots.setdefault(sym, [])
                side = (row.side or "").lower()
                qty = float(row.qty or 0)
                price = float(row.price or 0)
                if qty <= 0 or price <= 0:
                    continue

                if side == "buy":
                    cost = qty * price
                    # Never allow ledger cash to go deeply negative from bad history;
                    # clamp buys that exceed cash (defensive).
                    if cost > cash + 1e-6:
                        affordable = cash / price
                        if affordable * price < 100:
                            continue
                        qty = affordable
                        cost = qty * price
                    cash -= cost
                    lots[sym].append(
                        {
                            "qty": qty,
                            "entry_price": price,
                            "opened_at": row.created_at.isoformat() if row.created_at else None,
                            "entry_strategy_id": row.strategy_id,
                        }
                    )
                elif side == "sell":
                    remaining = qty
                    cash += qty * price
                    while remaining > 1e-9 and lots[sym]:
                        lot = lots[sym][0]
                        take = min(lot["qty"], remaining)
                        lot["qty"] -= take
                        remaining -= take
                        if lot["qty"] <= 1e-9:
                            lots[sym].pop(0)

            positions: dict[str, Any] = {}
            for sym, open_lots in lots.items():
                open_lots = [lot for lot in open_lots if lot["qty"] > 1e-9]
                if not open_lots:
                    continue
                total_qty = sum(lot["qty"] for lot in open_lots)
                entry = sum(lot["qty"] * lot["entry_price"] for lot in open_lots) / total_qty
                latest = open_lots[-1]
                positions[sym] = {
                    "qty": round(total_qty, 6),
                    "entry_price": round(entry, 4),
                    "opened_at": latest.get("opened_at"),
                    "entry_strategy_id": latest.get("entry_strategy_id"),
                    "entry_strategy_version": None,
                    "source": "postgres_trades",
                }

            return {
                "cash": round(max(cash, 0.0), 2),
                "positions": positions,
                "hydrated_from": "trades",
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not reconstruct portfolio from trades: %s", exc)
        return None
