from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from us_stock_agents.agents.paper_trading import PaperTradingAgent
from us_stock_agents.config import AppConfig
from us_stock_agents.memory import SharedMemory, StrategyState
from us_stock_agents.models import BacktestReport


def _cfg(state_dir: Path) -> AppConfig:
    return AppConfig(
        watchlist=["AAPL", "MSFT", "SPY"],
        benchmark="SPY",
        history_days=120,
        sentiment_lookback_hours=24,
        reports_dir=state_dir / "reports",
        state_dir=state_dir,
        initial_cash=100_000.0,
        max_position_pct=0.15,
        commission_bps=0.0,
        fast_ma=2,
        slow_ma=4,
        news_feeds=[],
        project_root=state_dir,
    )


def _prices_rising() -> pd.DataFrame:
    # fast MA > slow MA (bullish)
    closes = [100, 101, 102, 103, 104, 105, 106, 107]
    return pd.DataFrame({"close": closes})


def _prices_falling() -> pd.DataFrame:
    # fast MA < slow MA (bearish)
    closes = [110, 109, 108, 107, 106, 105, 104, 100]
    return pd.DataFrame({"close": closes})


def _backtest(symbols: list[str]) -> BacktestReport:
    return BacktestReport(
        generated_at=datetime.now(timezone.utc),
        benchmark="SPY",
        strategy_name="ma_crossover",
        per_symbol=[],
        aggregate_return_pct=0.0,
        recommended_symbols=symbols,
        parameter_suggestion={"fast_ma": 2, "slow_ma": 4},
    )


class PaperTradingAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.state = Path(self._tmpdir.name)
        self.cfg = _cfg(self.state)
        self.memory = SharedMemory(self.state)
        self.memory.save_strategy(
            StrategyState(version=9, fast_ma=2, slow_ma=4, strategy_id=9)
        )
        self.agent = PaperTradingAgent(self.cfg, self.memory)
        self._hydrate_patch = patch(
            "us_stock_agents.agents.paper_trading.reconstruct_positions_from_trades",
            return_value=None,
        )
        self._hydrate_patch.start()

    def tearDown(self) -> None:
        self._hydrate_patch.stop()
        self._tmpdir.cleanup()

    def _run_with_prices(
        self,
        price_map: dict[str, pd.DataFrame],
        last_prices: dict[str, float],
        symbols: list[str] | None = None,
    ):
        def fake_fetch(syms, days):
            return {s: price_map[s] for s in syms if s in price_map}

        def fake_last(prices, symbol):
            return last_prices.get(symbol)

        with (
            patch(
                "us_stock_agents.agents.paper_trading.fetch_history",
                side_effect=fake_fetch,
            ),
            patch(
                "us_stock_agents.agents.paper_trading.last_close",
                side_effect=fake_last,
            ),
        ):
            return self.agent.run(_backtest(symbols or ["AAPL"]))

    def test_01_buy_stores_entry_metadata(self) -> None:
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 107.0},
        )
        self.assertTrue(any(t.side == "buy" for t in report.trades_today))
        portfolio = json.loads((self.state / "portfolio.json").read_text(encoding="utf-8"))
        pos = portfolio["positions"]["AAPL"]
        self.assertIsInstance(pos, dict)
        self.assertGreater(pos["qty"], 0)
        self.assertEqual(pos["entry_price"], 107.0)
        self.assertIn("opened_at", pos)
        self.assertEqual(pos["entry_strategy_version"], 9)
        self.assertEqual(pos["entry_strategy_id"], 9)

    def test_02_legacy_numeric_position_does_not_crash(self) -> None:
        (self.state / "portfolio.json").write_text(
            json.dumps(
                {
                    "cash": 50_000.0,
                    "positions": {"AAPL": 10.0},
                    "strategy_version": 1,
                    "strategy_id": 1,
                }
            ),
            encoding="utf-8",
        )
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 100.0},
            symbols=["MSFT"],  # held AAPL still evaluated
        )
        self.assertIsNotNone(report.equity)
        self.assertIn("AAPL", report.positions)

    def test_03_stop_loss_at_exactly_minus_5(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 95.0},
        )
        sells = [t for t in report.trades_today if t.side == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertIn("Stop-loss", sells[0].reason)
        self.assertEqual(sells[0].pnl_pct, -5.0)

    def test_04_stop_loss_below_minus_5(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 90.0},
        )
        sells = [t for t in report.trades_today if t.side == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertIn("Stop-loss", sells[0].reason)
        self.assertLess(sells[0].pnl_pct or 0, -5.0)

    def test_05_take_profit_at_exactly_plus_10(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 110.0},
        )
        sells = [t for t in report.trades_today if t.side == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertIn("Take-profit", sells[0].reason)
        self.assertEqual(sells[0].pnl_pct, 10.0)

    def test_06_take_profit_above_plus_10(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 120.0},
        )
        sells = [t for t in report.trades_today if t.side == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertIn("Take-profit", sells[0].reason)
        self.assertGreater(sells[0].pnl_pct or 0, 10.0)

    def test_07_ma_bearish_exit_when_no_stop_target(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_falling()},
            {"AAPL": 99.0},  # -1% — inside stop/target band
        )
        sells = [t for t in report.trades_today if t.side == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertIn("MA bearish exit", sells[0].reason)

    def test_08_no_sell_when_bullish_and_inside_band(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 101.0},  # +1%
        )
        sells = [t for t in report.trades_today if t.side == "sell"]
        self.assertEqual(sells, [])
        self.assertIn("AAPL", report.positions)

    def test_09_open_position_evaluated_outside_recommended(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising(), "MSFT": _prices_rising()},
            {"AAPL": 90.0, "MSFT": 200.0},
            symbols=["MSFT"],
        )
        sells = [t for t in report.trades_today if t.symbol == "AAPL" and t.side == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertIn("Stop-loss", sells[0].reason)

    def test_10_sell_persists_pnl_and_removes_position(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 90.0},
        )
        sells = [t for t in report.trades_today if t.side == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertIsNotNone(sells[0].pnl_pct)
        portfolio = json.loads((self.state / "portfolio.json").read_text(encoding="utf-8"))
        self.assertNotIn("AAPL", portfolio["positions"])

    def test_11_negative_sell_creates_mistake(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 90.0},
        )
        self.assertTrue(any("Loss on AAPL" in lesson for lesson in report.lessons))
        mistakes = self.memory.recent_mistakes()
        self.assertTrue(any(m.get("symbol") == "AAPL" and m.get("pnl_pct", 0) < 0 for m in mistakes))

    def test_12_positive_sell_does_not_create_mistake(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        before = len(self.memory.recent_mistakes())
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 120.0},
        )
        self.assertFalse(any("Loss on AAPL" in lesson for lesson in report.lessons))
        self.assertEqual(len(self.memory.recent_mistakes()), before)

    def test_13_equity_handles_legacy_and_dict_positions(self) -> None:
        portfolio = {
            "cash": 1_000.0,
            "positions": {
                "AAPL": {"qty": 2.0, "entry_price": 50.0},
                "MSFT": 3.0,
            },
        }
        prices = {
            "AAPL": _prices_rising(),
            "MSFT": _prices_rising(),
        }
        with patch(
            "us_stock_agents.agents.paper_trading.last_close",
            side_effect=lambda _p, s: {"AAPL": 10.0, "MSFT": 20.0}[s],
        ):
            equity = self.agent._compute_equity(portfolio, prices)
        self.assertEqual(equity, 1_000.0 + 2 * 10.0 + 3 * 20.0)

    def test_14_no_duplicate_buy_when_position_exists(self) -> None:
        self._seed_position("AAPL", qty=5, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 101.0},
        )
        buys = [t for t in report.trades_today if t.side == "buy"]
        self.assertEqual(buys, [])

    def test_15_no_duplicate_sell_in_single_cycle(self) -> None:
        self._seed_position("AAPL", qty=10, entry=100.0)
        report = self._run_with_prices(
            {"AAPL": _prices_rising()},
            {"AAPL": 90.0},
        )
        sells = [t for t in report.trades_today if t.side == "sell" and t.symbol == "AAPL"]
        self.assertEqual(len(sells), 1)

    def test_positions_survive_strategy_mutation_metadata(self) -> None:
        self._seed_position("AAPL", qty=7, entry=50.0, version=1, strategy_id=1)
        loaded = self.agent._load_portfolio(strategy_version=99, strategy_id=99)
        self.assertEqual(loaded["strategy_version"], 99)
        self.assertEqual(loaded["strategy_id"], 99)
        self.assertEqual(self.agent._position_qty(loaded["positions"]["AAPL"]), 7)

    def test_16_rsi_overbought_exit(self) -> None:
        # Long rising series → RSI overbought; keep PnL inside stop/target band.
        closes = [100 + i * 0.5 for i in range(40)]
        df = pd.DataFrame({"close": closes})
        entry = float(closes[-1]) / 1.02  # about +2%
        self._seed_position("AAPL", qty=10, entry=entry)
        report = self._run_with_prices({"AAPL": df}, {"AAPL": float(closes[-1])})
        sells = [t for t in report.trades_today if t.side == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertIn("RSI mean-reversion", sells[0].reason)

    def test_17_rsi_oversold_can_buy(self) -> None:
        # Sharp decline → RSI oversold
        closes = [100 - i for i in range(30)]
        df = pd.DataFrame({"close": closes})
        report = self._run_with_prices({"AAPL": df}, {"AAPL": float(closes[-1])})
        buys = [t for t in report.trades_today if t.side == "buy"]
        self.assertTrue(buys)
        self.assertIn("RSI oversold", buys[0].reason)

    def _seed_position(
        self,
        symbol: str,
        qty: float,
        entry: float,
        version: int = 9,
        strategy_id: int = 9,
    ) -> None:
        data = {
            "cash": 50_000.0,
            "positions": {
                symbol: {
                    "qty": qty,
                    "entry_price": entry,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "entry_strategy_version": version,
                    "entry_strategy_id": strategy_id,
                }
            },
            "strategy_version": version,
            "strategy_id": strategy_id,
        }
        (self.state / "portfolio.json").write_text(json.dumps(data), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
