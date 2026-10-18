from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from us_stock_agents.universe import US_LIQUID_UNIVERSE


@dataclass(frozen=True)
class AppConfig:
    watchlist: list[str]
    universe: list[str]
    benchmark: str
    history_days: int
    scan_batch_size: int
    trade_universe_limit: int
    use_full_us_universe: bool
    sentiment_lookback_hours: int
    reports_dir: Path
    state_dir: Path
    initial_cash: float
    max_position_pct: float
    commission_bps: float
    fast_ma: int
    slow_ma: int
    news_feeds: list[str]
    project_root: Path


def load_config(path: Path | None = None) -> AppConfig:
    root = Path(__file__).resolve().parents[2]
    cfg_path = path or (root / "config.yaml")
    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    market = raw.get("market", {})
    scheduling = raw.get("scheduling", {})
    reports = raw.get("reports", {})
    state = raw.get("state", {})
    paper = raw.get("paper_trading", {})
    backtest = raw.get("backtest", {})

    reports_dir = root / reports.get("directory", "reports")
    state_dir = root / state.get("directory", "state")

    watchlist = list(raw.get("watchlist", []))
    use_full = bool(market.get("use_full_us_universe", True))
    universe = list(dict.fromkeys([*watchlist, *US_LIQUID_UNIVERSE])) if use_full else list(watchlist)

    initial_cash = float(paper.get("initial_cash", 100_000))
    try:
        from us_stock_agents.settings import get_settings

        initial_cash = float(get_settings().initial_cash or initial_cash)
    except Exception:  # noqa: BLE001
        pass

    return AppConfig(
        watchlist=watchlist,
        universe=universe,
        benchmark=str(market.get("benchmark", "SPY")),
        history_days=int(market.get("history_days", 120)),
        scan_batch_size=int(market.get("scan_batch_size", 25)),
        trade_universe_limit=int(market.get("trade_universe_limit", 20)),
        use_full_us_universe=use_full,
        sentiment_lookback_hours=int(scheduling.get("sentiment_lookback_hours", 24)),
        reports_dir=reports_dir,
        state_dir=state_dir,
        initial_cash=initial_cash,
        max_position_pct=float(paper.get("max_position_pct", 0.12)),
        commission_bps=float(paper.get("commission_bps", 5)),
        fast_ma=int(backtest.get("fast_ma", 10)),
        slow_ma=int(backtest.get("slow_ma", 30)),
        news_feeds=list(raw.get("news_feeds", [])),
        project_root=root,
    )


def ensure_dirs(cfg: AppConfig) -> None:
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
