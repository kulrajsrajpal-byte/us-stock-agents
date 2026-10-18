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
from us_stock_agents.portfolio_sync import reconstruct_positions_from_trades
from us_stock_agents.strategies.signals import (
    breakout_high,
    compute_rsi,
    is_ma_bearish,
    is_ma_bullish,
    is_rsi_overbought,
    is_rsi_oversold,
    momentum_positive,
)


class PaperTradingAgent:
    """Paper trades with multi-strategy exits (SMC-style playbook).

    Exit priority:
      1) stop-loss (-5%)
      2) take-profit (+10%)
      3) RSI overbought mean-reversion exit
      4) MA bearish trend exit
      5) momentum failure (optional soft exit when below entry and momentum faded)

    Entry (candidates only): MA bullish OR RSI oversold OR breakout high.
    """

    STOP_LOSS_PCT = 5.0
    TAKE_PROFIT_PCT = 10.0
    RSI_PERIOD = 14
    RSI_OVERSOLD = 30.0
    RSI_OVERBOUGHT = 70.0
    BREAKOUT_LOOKBACK = 20
    MOMENTUM_LOOKBACK = 20

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

            if symbol not in candidate_set:
                continue

            buy_reason = self._entry_reason(
                prices.get(symbol), strategy.fast_ma, strategy.slow_ma
            )
            if buy_reason:
                self._execute_buy(
                    portfolio=portfolio,
                    strategy=strategy,
                    symbol=symbol,
                    price=price,
                    reason=buy_reason,
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
                "Paper trading only. Strategies: MA trend, RSI mean-reversion, "
                "breakout entry, stop/target risk exits."
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

        rsi = compute_rsi(df["close"], self.RSI_PERIOD) if df is not None and "close" in df else None
        if is_rsi_overbought(df, self.RSI_PERIOD, self.RSI_OVERBOUGHT):
            return (
                f"RSI mean-reversion exit (RSI={rsi:.1f} >= {self.RSI_OVERBOUGHT})",
                round(pnl_pct, 2) if pnl_pct is not None else None,
            )

        if is_ma_bearish(df, fast, slow):
            return (
                f"MA bearish exit (fast={fast}, slow={slow})",
                round(pnl_pct, 2) if pnl_pct is not None else None,
            )

        # Soft momentum exit: underwater and momentum no longer positive
        if (
            pnl_pct is not None
            and pnl_pct < 0
            and df is not None
            and not momentum_positive(df, self.MOMENTUM_LOOKBACK)
        ):
            return (
                f"Momentum fade exit at {pnl_pct:.2f}%",
                round(pnl_pct, 2),
            )

        return None

    def _entry_reason(self, df: pd.DataFrame | None, fast: int, slow: int) -> str | None:
        if is_rsi_oversold(df, self.RSI_PERIOD, self.RSI_OVERSOLD):
            rsi = compute_rsi(df["close"], self.RSI_PERIOD) if df is not None else None
            return f"RSI oversold buy (RSI={rsi:.1f})" if rsi is not None else "RSI oversold buy"
        if is_ma_bullish(df, fast, slow):
            return f"MA bullish buy (fast={fast}, slow={slow})"
        if breakout_high(df, self.BREAKOUT_LOOKBACK):
            return f"Breakout buy (new {self.BREAKOUT_LOOKBACK}-day high)"
        return None

    def _execute_buy(
        self,
        *,
        portfolio: dict[str, Any],
        strategy: StrategyState,
        symbol: str,
        price: float,
        reason: str,
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
            reason=reason,
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
            "Review stop/target sizing and strategy filters."
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

        data: dict[str, Any] | None = None
        if self.portfolio_path.exists():
            try:
                data = json.loads(self.portfolio_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, TypeError):
                data = None

        has_open = bool(
            data
            and any(self._position_qty(v) > 0 for v in (data.get("positions") or {}).values())
        )

        # Railway: restore open lots from durable Postgres trades when local file empty.
        if not has_open:
            hydrated = reconstruct_positions_from_trades()
            if hydrated and hydrated.get("positions"):
                open_cost = sum(
                    self._position_qty(pos) * float(pos.get("entry_price") or 0)
                    for pos in hydrated["positions"].values()
                    if isinstance(pos, dict)
                )
                data = {
                    "cash": round(max(self.cfg.initial_cash - open_cost, 0.0), 2),
                    "positions": hydrated["positions"],
                    "hydrated_from": "postgres_trades",
                }

        if not data:
            return fresh

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
