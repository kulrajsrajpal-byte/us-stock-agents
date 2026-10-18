from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from us_stock_agents.local_coach import coach_status, propose_strategy_patch


class LocalCoachTests(unittest.TestCase):
    def test_coach_status_enabled(self) -> None:
        with patch("us_stock_agents.local_coach.get_settings") as gs:
            gs.return_value.local_coach_enabled = True
            gs.return_value.profit_target_pct = 5.0
            gs.return_value.continue_until_profit = True
            status = coach_status()
            self.assertTrue(status["enabled"])
            self.assertEqual(status["engine"], "local_postgres_learner")
            self.assertFalse(status["needs_external_api"])

    def test_propose_avoids_failed_pair(self) -> None:
        parent = MagicMock()
        parent.id = 1
        parent.version = 1
        parent.fast_ma = 10
        parent.slow_ma = 30
        parent.paper_return_pct = -2.0
        parent.trade_count = 5
        parent.params = {"stop_loss_pct": 5.0, "take_profit_pct": 10.0}

        loser = MagicMock()
        loser.id = 1
        loser.version = 1
        loser.fast_ma = 10
        loser.slow_ma = 30
        loser.paper_return_pct = -2.0
        loser.trade_count = 5

        winner = MagicMock()
        winner.id = 2
        winner.version = 2
        winner.fast_ma = 12
        winner.slow_ma = 40
        winner.paper_return_pct = 3.0
        winner.trade_count = 8

        session = MagicMock()
        session.scalars.return_value.all.return_value = [loser, winner]

        with patch("us_stock_agents.local_coach.get_settings") as gs:
            gs.return_value.profit_target_pct = 5.0
            gs.return_value.local_coach_enabled = True
            patch_out = propose_strategy_patch(
                session,
                parent=parent,
                lessons=["risk-off sentiment"],
                recent_sells=[
                    {"symbol": "AAPL", "pnl_pct": -5.1, "reason": "Stop-loss hit"},
                    {"symbol": "MSFT", "pnl_pct": -5.0, "reason": "Stop-loss hit"},
                ],
                reason="negative_paper_pnl",
            )

        self.assertNotEqual((patch_out["fast_ma"], patch_out["slow_ma"]), (10, 30))
        self.assertGreaterEqual(patch_out["slow_ma"], patch_out["fast_ma"] + 6)
        self.assertEqual(patch_out["source"], "local_coach")
        self.assertIn("rationale", patch_out)
        session.add.assert_called()


if __name__ == "__main__":
    unittest.main()
