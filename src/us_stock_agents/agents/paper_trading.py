from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from us_stock_agents.config import AppConfig
from us_stock_agents.market_data import fetch_history, last_close
from us_stock_agents.memory import SharedMemory
from us_stock_agents.models import (
    ActivityReport,
    BacktestReport,
    PaperTrade,
    PaperTradingReport,
)


class PaperTradingAgent:
    """Simulates trades, journals outcomes, and records lessons for the next run."""

    STOP_LOSS_PCT = 0.05
    TAKE_PROFIT_PCT = 0.10

    def __init__(self, cfg: AppConfig, memory: SharedMemory) -> None:
        self.cfg = cfg
        self.memory = memory
        self.portfolio_path = cfg.state_dir / "portfolio.json"

    def run(self, backtest: BacktestReport, activity: ActivityReport | None = None) -> PaperTradingReport:
        strategy = self.memory.load_strategy(self.cfg.fast_ma, self.cfg.slow_ma)
        portfolio = self._load_portfolio(strategy.version, strategy.strategy_id)
        symbols = backtest.recommended_symbols or self.cfg.watchlist[:3]
        prices = fetch_history(symbols + list(portfolio.get("positions", {}).keys()), 120)

        trades: list[PaperTrade] = []
        lessons: list[str] = []

        for symbol in symbols:
            signal = self._signal(prices.get(symbol), strategy.fast_ma, strategy.slow_ma)
            price = last_close(prices, symbol)
            if price is None:
                continue

            pos = float(portfolio["positions"].get(symbol, 0))
            cash = float(portfolio["cash"])

            if signal == "buy" and pos == 0:
                budget = cash * self.cfg.max_position_pct
                qty = budget / price
                if qty * price < 100:
                    continue
                cost = qty * price * (1 + self.cfg.commission_bps / 10_000)
                if cost > cash:
                    continue
                portfolio["cash"] = cash - cost
                portfolio["positions"][symbol] = qty
                trade = PaperTrade(
                    timestamp=datetime.now(timezone.utc),
                    symbol=symbol,
                    side="buy",
                    qty=round(qty, 4),
                    price=round(price, 2),
                    reason=f"MA crossover buy (fast={strategy.fast_ma}, slow={strategy.slow_ma})",
                )
                trades.append(trade)
                self.memory.append_journal(
                    {
                        "type": "paper_trade",
                        "timestamp": trade.timestamp.isoformat(),
                        "symbol": trade.symbol,
                        "side": trade.side,
                        "qty": trade.qty,
                        "price": trade.price,
                        "reason": trade.reason,
                    }
                )

            elif signal == "sell" and pos > 0:
                proceeds = pos * price * (1 - self.cfg.commission_bps / 10_000)
                portfolio["cash"] = float(portfolio["cash"]) + proceeds
                trade = PaperTrade(
                    timestamp=datetime.now(timezone.utc),
                    symbol=symbol,
                    side="sell",
                    qty=round(pos, 4),
                    price=round(price, 2),
                    reason="MA crossover sell",
                )
                trades.append(trade)
                portfolio["positions"][symbol] = 0
                self._evaluate_exit(trade, lessons)

        equity = float(portfolio["cash"])
        for sym, qty in portfolio["positions"].items():
            px = last_close(prices, sym)
            if px is not None:
                equity += qty * px

        strategy.last_paper_equity = equity
        self.memory.save_strategy(strategy)
        portfolio["strategy_version"] = strategy.version
        portfolio["strategy_id"] = strategy.strategy_id
        self._save_portfolio(portfolio)

        if activity and activity.themes:
            lessons.append(f"Active themes this run: {', '.join(activity.themes)}")

        return PaperTradingReport(
            generated_at=datetime.now(timezone.utc),
            cash=round(float(portfolio["cash"]), 2),
            equity=round(equity, 2),
            positions={k: v for k, v in portfolio["positions"].items() if v},
            trades_today=trades,
            lessons=lessons,
            strategy_version=strategy.version,
            strategy_params={"fast_ma": strategy.fast_ma, "slow_ma": strategy.slow_ma},
            notes="Paper trading only — no real orders. Review state/portfolio.json and trade_journal.jsonl.",
        )

    def _signal(self, df, fast: int, slow: int) -> str:
        if df is None or len(df) < slow + 2:
            return "hold"
        close = df["close"].astype(float)
        f = close.rolling(fast).mean().iloc[-1]
        s = close.rolling(slow).mean().iloc[-1]
        prev_f = close.rolling(fast).mean().iloc[-2]
        prev_s = close.rolling(slow).mean().iloc[-2]
        if prev_f <= prev_s and f > s:
            return "buy"
        if prev_f >= prev_s and f < s:
            return "sell"
        if f > s:
            return "buy"
        if f < s:
            return "sell"
        return "hold"

    def _evaluate_exit(self, trade: PaperTrade, lessons: list[str]) -> None:
        mistakes = self.memory.recent_mistakes()
        journal_path = self.memory.journal_path
        if not journal_path.exists():
            return
        lines = journal_path.read_text(encoding="utf-8").strip().splitlines()
        last_buy = None
        for line in reversed(lines):
            row = json.loads(line)
            if row.get("symbol") == trade.symbol and row.get("side") == "buy":
                last_buy = row
                break
        if not last_buy:
            return
        buy_px = float(last_buy["price"])
        sell_px = trade.price
        pnl_pct = (sell_px - buy_px) / buy_px * 100
        if pnl_pct < 0:
            lesson = (
                f"Loss on {trade.symbol} ({pnl_pct:.1f}%): consider wider slow MA or "
                f"reducing size when macro themes are risk-off."
            )
            lessons.append(lesson)
            self.memory.append_mistake(
                {
                    "timestamp": trade.timestamp.isoformat(),
                    "symbol": trade.symbol,
                    "pnl_pct": round(pnl_pct, 2),
                    "lesson": lesson,
                    "mistake_count": len(mistakes) + 1,
                }
            )

    def _load_portfolio(
        self,
        strategy_version: int,
        strategy_id: int | None,
    ) -> dict:
        fresh = {
            "cash": self.cfg.initial_cash,
            "positions": {},
            "strategy_version": strategy_version,
            "strategy_id": strategy_id,
        }

        if not self.portfolio_path.exists():
            return fresh

        try:
            data = json.loads(
                self.portfolio_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError, TypeError):
            return fresh

        # Preserve cash and open positions when the strategy mutates.
        # Otherwise existing positions are forgotten before they can be sold.
        data.setdefault("cash", self.cfg.initial_cash)
        data.setdefault("positions", {})

        # Record which strategy currently manages the portfolio,
        # without deleting positions opened by an earlier version.
        data["strategy_version"] = strategy_version
        data["strategy_id"] = strategy_id

        return data

    def _save_portfolio(self, portfolio: dict) -> None:
        self.portfolio_path.parent.mkdir(parents=True, exist_ok=True)
        self.portfolio_path.write_text(json.dumps(portfolio, indent=2), encoding="utf-8")
