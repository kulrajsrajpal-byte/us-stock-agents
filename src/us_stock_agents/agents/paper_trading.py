from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from us_stock_agents.config import AppConfig
from us_stock_agents.market_data import fetch_history, last_close
from us_stock_agents.memory import SharedMemory, StrategyState
from us_stock_agents.models import (
    ActivityReport,
    BacktestReport,
    PaperTrade,
    PaperTradingReport,
)


class PaperTradingAgent:
    """Simulates paper trades with stop-loss, take-profit, and MA exits."""

    # Percent points: compare against pnl_pct = ((px - entry) / entry) * 100
    STOP_LOSS_PCT = 5.0
    TAKE_PROFIT_PCT = 10.0

    def __init__(self, cfg: AppConfig, memory: SharedMemory) -> None:
        self.cfg = cfg
        self.memory = memory
        self.portfolio_path = cfg.state_dir / "portfolio.json"

    def run(
        self,
        backtest: BacktestReport,
        activity: ActivityReport | None = None,
    ) -> PaperTradingReport:
        strategy = self.memory.load_strategy(self.cfg.fast_ma, self.cfg.slow_ma)
        portfolio = self._load_portfolio(strategy.version, strategy.strategy_id)

        candidates = list(
            dict.fromkeys(backtest.recommended_symbols or self.cfg.watchlist[:3])
        )
        held_symbols = [
            sym
            for sym, data in portfolio.get("positions", {}).items()
            if self._position_qty(data) > 0
        ]
        # Evaluate held names every cycle; only candidates may open new BUYs.
        universe = list(dict.fromkeys(candidates + held_symbols))

        prices = fetch_history(universe, 120)
        trades: list[PaperTrade] = []
        lessons: list[str] = []
        candidate_set = set(candidates)

        for symbol in universe:
            price = last_close(prices, symbol)
            if price is None:
                continue

            position_data = portfolio["positions"].get(symbol)
            qty = self._position_qty(position_data)
            entry_price = self._position_entry(position_data)

            # --- exits first (every open position, even non-candidates) ---
            if qty > 0:
                exit_decision = self._decide_exit(
                    entry_price=entry_price,
                    current_price=price,
                    df=prices.get(symbol),
                    fast=strategy.fast_ma,
                    slow=strategy.slow_ma,
                )
                if exit_decision is not None:
                    reason, pnl_pct = exit_decision
                    self._execute_sell(
                        portfolio=portfolio,
                        symbol=symbol,
                        qty=qty,
                        price=price,
                        reason=reason,
                        pnl_pct=pnl_pct,
                        trades=trades,
                        lessons=lessons,
                    )
                continue

            # --- entries only for current candidates with no open position ---
            if symbol not in candidate_set:
                continue

            signal = self._entry_signal(
                prices.get(symbol), strategy.fast_ma, strategy.slow_ma
            )
            if signal == "buy":
                self._execute_buy(
                    portfolio=portfolio,
                    strategy=strategy,
                    symbol=symbol,
                    price=price,
                    trades=trades,
                )

        equity = self._compute_equity(portfolio, prices)
        strategy.last_paper_equity = equity
        self.memory.save_strategy(strategy)
        portfolio["strategy_version"] = strategy.version
        portfolio["strategy_id"] = strategy.strategy_id
        self._save_portfolio(portfolio)

        if activity and activity.themes:
            lessons.append(f"Active themes this run: {', '.join(activity.themes)}")

        report_positions = {
            symbol: self._position_qty(data)
            for symbol, data in portfolio["positions"].items()
            if self._position_qty(data) > 0
        }

        return PaperTradingReport(
            generated_at=datetime.now(timezone.utc),
            cash=round(float(portfolio["cash"]), 2),
            equity=round(equity, 2),
            positions=report_positions,
            trades_today=trades,
            lessons=lessons,
            strategy_version=strategy.version,
            strategy_params={"fast_ma": strategy.fast_ma, "slow_ma": strategy.slow_ma},
            notes=(
                "Paper trading only — no real orders. "
                "Review state/portfolio.json and trade_journal.jsonl."
            ),
        )

    @staticmethod
    def _position_qty(position_data: Any) -> float:
        if position_data is None:
            return 0.0
        if isinstance(position_data, dict):
            return float(position_data.get("qty", 0) or 0)
        return float(position_data or 0)

    @staticmethod
    def _position_entry(position_data: Any) -> float | None:
        if not isinstance(position_data, dict):
            return None
        raw = position_data.get("entry_price")
        if raw is None:
            return None
        return float(raw)

    def _decide_exit(
        self,
        *,
        entry_price: float | None,
        current_price: float,
        df: pd.DataFrame | None,
        fast: int,
        slow: int,
    ) -> tuple[str, float | None] | None:
        """Return (reason, pnl_pct) when an exit triggers; else None.

        Priority: stop-loss → take-profit → MA bearish.
        """
        pnl_pct: float | None = None
        if entry_price is not None and entry_price > 0:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100.0

            if pnl_pct <= -self.STOP_LOSS_PCT:
                return (
                    f"Stop-loss triggered at {pnl_pct:.2f}%",
                    round(pnl_pct, 2),
                )
            if pnl_pct >= self.TAKE_PROFIT_PCT:
                return (
                    f"Take-profit triggered at {pnl_pct:.2f}%",
                    round(pnl_pct, 2),
                )

        if self._is_ma_bearish(df, fast, slow):
            return (
                f"MA bearish exit (fast={fast}, slow={slow})",
                round(pnl_pct, 2) if pnl_pct is not None else None,
            )
        return None

    @staticmethod
    def _is_ma_bearish(df: pd.DataFrame | None, fast: int, slow: int) -> bool:
        if df is None or len(df) < slow + 1:
            return False
        close = df["close"].astype(float)
        fast_ma = float(close.rolling(fast).mean().iloc[-1])
        slow_ma = float(close.rolling(slow).mean().iloc[-1])
        if pd.isna(fast_ma) or pd.isna(slow_ma):
            return False
        return fast_ma < slow_ma

    @staticmethod
    def _entry_signal(df: pd.DataFrame | None, fast: int, slow: int) -> str:
        """BUY only on bullish MA state (fast > slow); otherwise hold."""
        if df is None or len(df) < slow + 1:
            return "hold"
        close = df["close"].astype(float)
        fast_ma = float(close.rolling(fast).mean().iloc[-1])
        slow_ma = float(close.rolling(slow).mean().iloc[-1])
        if pd.isna(fast_ma) or pd.isna(slow_ma):
            return "hold"
        if fast_ma > slow_ma:
            return "buy"
        return "hold"

    def _execute_buy(
        self,
        *,
        portfolio: dict[str, Any],
        strategy: StrategyState,
        symbol: str,
        price: float,
        trades: list[PaperTrade],
    ) -> None:
        if self._position_qty(portfolio["positions"].get(symbol)) > 0:
            return

        cash = float(portfolio["cash"])
        budget = cash * self.cfg.max_position_pct
        qty = budget / price
        if qty * price < 100:
            return
        cost = qty * price * (1 + self.cfg.commission_bps / 10_000)
        if cost > cash:
            return

        portfolio["cash"] = cash - cost
        portfolio["positions"][symbol] = {
            "qty": qty,
            "entry_price": price,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "entry_strategy_version": strategy.version,
            "entry_strategy_id": strategy.strategy_id,
        }
        trade = PaperTrade(
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            side="buy",
            qty=round(qty, 4),
            price=round(price, 2),
            reason=f"MA bullish buy (fast={strategy.fast_ma}, slow={strategy.slow_ma})",
            pnl_pct=None,
        )
        trades.append(trade)
        self._journal(trade)

    def _execute_sell(
        self,
        *,
        portfolio: dict[str, Any],
        symbol: str,
        qty: float,
        price: float,
        reason: str,
        pnl_pct: float | None,
        trades: list[PaperTrade],
        lessons: list[str],
    ) -> None:
        if qty <= 0:
            return
        # Guard against duplicate sells in the same cycle.
        if self._position_qty(portfolio["positions"].get(symbol)) <= 0:
            return

        proceeds = qty * price * (1 - self.cfg.commission_bps / 10_000)
        portfolio["cash"] = float(portfolio["cash"]) + proceeds
        portfolio["positions"].pop(symbol, None)

        trade = PaperTrade(
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            side="sell",
            qty=round(qty, 4),
            price=round(price, 2),
            reason=reason,
            pnl_pct=pnl_pct,
        )
        trades.append(trade)
        self._journal(trade)
        self._record_loss_lesson(trade, lessons)

    def _journal(self, trade: PaperTrade) -> None:
        self.memory.append_journal(
            {
                "type": "paper_trade",
                "timestamp": trade.timestamp.isoformat(),
                "symbol": trade.symbol,
                "side": trade.side,
                "qty": trade.qty,
                "price": trade.price,
                "reason": trade.reason,
                "pnl_pct": trade.pnl_pct,
            }
        )

    def _record_loss_lesson(self, trade: PaperTrade, lessons: list[str]) -> None:
        if trade.side != "sell":
            return
        if trade.pnl_pct is None or trade.pnl_pct >= 0:
            return

        mistakes = self.memory.recent_mistakes()
        lesson = (
            f"Loss on {trade.symbol} ({trade.pnl_pct:.2f}%): {trade.reason}. "
            "Review stop/target sizing and MA window."
        )
        lessons.append(lesson)
        self.memory.append_mistake(
            {
                "timestamp": trade.timestamp.isoformat(),
                "symbol": trade.symbol,
                "pnl_pct": round(float(trade.pnl_pct), 2),
                "reason": trade.reason,
                "lesson": lesson,
                "mistake_count": len(mistakes) + 1,
            }
        )

    def _compute_equity(
        self,
        portfolio: dict[str, Any],
        prices: dict[str, pd.DataFrame],
    ) -> float:
        equity = float(portfolio["cash"])
        for sym, position_data in portfolio.get("positions", {}).items():
            qty = self._position_qty(position_data)
            if qty <= 0:
                continue
            px = last_close(prices, sym)
            if px is not None:
                equity += qty * px
        return equity

    def _load_portfolio(
        self,
        strategy_version: int,
        strategy_id: int | None,
    ) -> dict[str, Any]:
        fresh: dict[str, Any] = {
            "cash": self.cfg.initial_cash,
            "positions": {},
            "strategy_version": strategy_version,
            "strategy_id": strategy_id,
        }

        if not self.portfolio_path.exists():
            return fresh

        try:
            data = json.loads(self.portfolio_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, TypeError):
            return fresh

        # Preserve cash and open positions across strategy mutations.
        data.setdefault("cash", self.cfg.initial_cash)
        data.setdefault("positions", {})
        data["strategy_version"] = strategy_version
        data["strategy_id"] = strategy_id
        return data

    def _save_portfolio(self, portfolio: dict[str, Any]) -> None:
        self.portfolio_path.parent.mkdir(parents=True, exist_ok=True)
        self.portfolio_path.write_text(
            json.dumps(portfolio, indent=2),
            encoding="utf-8",
        )
