from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from us_stock_agents.config import AppConfig
from us_stock_agents.market_data import fetch_history
from us_stock_agents.memory import SharedMemory, StrategyState
from us_stock_agents.models import ActivityReport, BacktestMetrics, BacktestReport, SentimentReport
from us_stock_agents.universe import merge_priority, rotate_batch


class BacktestAgent:
    """Backtests a moving-average crossover on the watchlist and suggests parameter tweaks."""

    def __init__(self, cfg: AppConfig, memory: SharedMemory) -> None:
        self.cfg = cfg
        self.memory = memory

    def run(
        self,
        sentiment: SentimentReport | None = None,
        activity: ActivityReport | None = None,
    ) -> BacktestReport:
        strategy = self.memory.load_strategy(self.cfg.fast_ma, self.cfg.slow_ma)
        symbols = self._priority_symbols(activity)
        prices = fetch_history(symbols, self.cfg.history_days)

        per_symbol: list[BacktestMetrics] = []
        for symbol in symbols:
            df = prices.get(symbol)
            if df is None or len(df) < strategy.slow_ma + 5:
                continue
            metrics = self._backtest_symbol(df, symbol, strategy.fast_ma, strategy.slow_ma)
            per_symbol.append(metrics)

        if not per_symbol:
            return BacktestReport(
                generated_at=datetime.now(timezone.utc),
                benchmark=self.cfg.benchmark,
                strategy_name="ma_crossover",
                per_symbol=[],
                aggregate_return_pct=0.0,
                recommended_symbols=[],
                parameter_suggestion={
                    "fast_ma": strategy.fast_ma,
                    "slow_ma": strategy.slow_ma,
                },
                notes="Insufficient price history for backtest.",
            )

        aggregate = float(np.mean([m.return_pct for m in per_symbol]))
        ranked = sorted(per_symbol, key=lambda m: m.sharpe, reverse=True)
        # Trade the strongest signals from this scan batch (up to 5).
        recommended = [m.symbol for m in ranked if m.return_pct > 0 and m.sharpe > 0][:5]
        if not recommended:
            recommended = [m.symbol for m in ranked[:3]]

        old_fast, old_slow = strategy.fast_ma, strategy.slow_ma
        suggestion = self._suggest_params(strategy, per_symbol, sentiment)

        strategy.last_backtest_return_pct = aggregate
        strategy.fast_ma = suggestion["fast_ma"]
        strategy.slow_ma = suggestion["slow_ma"]
        if (strategy.fast_ma, strategy.slow_ma) != (old_fast, old_slow):
            strategy.version += 1
        self.memory.save_strategy(strategy)

        return BacktestReport(
            generated_at=datetime.now(timezone.utc),
            benchmark=self.cfg.benchmark,
            strategy_name="ma_crossover",
            per_symbol=per_symbol,
            aggregate_return_pct=round(aggregate, 2),
            recommended_symbols=recommended,
            parameter_suggestion=suggestion,
            notes="Backtest uses long-only MA crossover with next-bar execution.",
        )

    def _priority_symbols(self, activity: ActivityReport | None) -> list[str]:
        batch = rotate_batch(self.cfg.universe, self.cfg.scan_batch_size)
        # Always include core watchlist so mega-caps stay in play.
        batch = list(dict.fromkeys([*self.cfg.watchlist, *batch]))
        impacted = [t.symbol for t in activity.impacted] if activity else []
        return merge_priority(
            batch=batch,
            impacted=impacted,
            held=[],
            limit=self.cfg.trade_universe_limit,
        )

    def _backtest_symbol(
        self,
        df: pd.DataFrame,
        symbol: str,
        fast: int,
        slow: int,
    ) -> BacktestMetrics:
        close = df["close"].astype(float)
        fast_ma = close.rolling(fast).mean()
        slow_ma = close.rolling(slow).mean()
        signal = (fast_ma > slow_ma).astype(int)
        position = signal.shift(1).fillna(0)
        returns = close.pct_change().fillna(0) * position
        equity = (1 + returns).cumprod()

        total_return = (equity.iloc[-1] - 1) * 100
        rolling_max = equity.cummax()
        drawdown = (equity / rolling_max - 1) * 100
        max_dd = float(drawdown.min())

        if returns.std() > 0:
            sharpe = float(np.sqrt(252) * returns.mean() / returns.std())
        else:
            sharpe = 0.0

        trades = int((position.diff().abs() > 0).sum())
        wins = int((returns[position.shift(1) == 1] > 0).sum())
        active_days = int((position.shift(1) == 1).sum())
        win_rate = (100 * wins / active_days) if active_days else 0.0

        return BacktestMetrics(
            symbol=symbol,
            fast_ma=fast,
            slow_ma=slow,
            return_pct=round(float(total_return), 2),
            max_drawdown_pct=round(max_dd, 2),
            sharpe=round(sharpe, 2),
            trades=trades,
            win_rate_pct=round(win_rate, 1),
        )

    def _suggest_params(
        self,
        strategy: StrategyState,
        results: list[BacktestMetrics],
        sentiment: SentimentReport | None,
    ) -> dict[str, int]:
        fast, slow = strategy.fast_ma, strategy.slow_ma
        avg_sharpe = float(np.mean([r.sharpe for r in results])) if results else 0.0
        mistakes = self.memory.recent_mistakes()

        if avg_sharpe < 0.3 and slow - fast < 25:
            slow = min(slow + 5, 60)
        if mistakes and avg_sharpe < 0.5:
            fast = max(fast - 1, 5)

        if sentiment and sentiment.overall_label == "bearish" and slow < 45:
            slow += 5

        return {"fast_ma": fast, "slow_ma": slow}
