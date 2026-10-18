from __future__ import annotations

import unittest
from unittest.mock import patch

from us_stock_agents.hermes import hermes_enabled, hermes_status


class HermesStatusTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with patch("us_stock_agents.hermes.get_settings") as gs:
            gs.return_value.hermes_enabled = False
            gs.return_value.hermes_api_url = ""
            gs.return_value.hermes_api_key = ""
            gs.return_value.hermes_model = "default"
            gs.return_value.profit_target_pct = 5.0
            gs.return_value.continue_until_profit = True
            self.assertFalse(hermes_enabled())
            status = hermes_status()
            self.assertFalse(status["enabled"])
            self.assertTrue(status["continue_until_profit"])

    def test_enabled_when_configured(self) -> None:
        with patch("us_stock_agents.hermes.get_settings") as gs:
            gs.return_value.hermes_enabled = True
            gs.return_value.hermes_api_url = "https://hermes.example"
            gs.return_value.hermes_api_key = "secret"
            gs.return_value.hermes_model = "default"
            gs.return_value.profit_target_pct = 5.0
            gs.return_value.continue_until_profit = True
            self.assertTrue(hermes_enabled())
            self.assertTrue(hermes_status()["enabled"])


if __name__ == "__main__":
    unittest.main()
