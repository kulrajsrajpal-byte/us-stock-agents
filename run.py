#!/usr/bin/env python3
"""Entry point for the US stock multi-agent pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from us_stock_agents.config import load_config
from us_stock_agents.db import init_db
from us_stock_agents.orchestrator import Orchestrator
from us_stock_agents.service import run_cycle


def main() -> int:
    parser = argparse.ArgumentParser(description="US stock multi-agent orchestrator")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config.yaml",
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--agent",
        choices=["all", "sentiment", "activity", "backtest", "paper"],
        default="all",
        help="Run one specialist or the full pipeline",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start FastAPI + 24/7 scheduler (Railway entry uses uvicorn directly)",
    )
    parser.add_argument(
        "--cycle",
        action="store_true",
        help="Run one DB-backed evolve cycle (same as POST /run)",
    )
    args = parser.parse_args()

    if args.serve:
        import uvicorn

        uvicorn.run("us_stock_agents.api:app", host="0.0.0.0", port=8000, reload=False)
        return 0

    if args.cycle:
        init_db()
        print(run_cycle(force=True))
        return 0

    cfg = load_config(args.config)
    orch = Orchestrator(cfg)

    if args.agent == "all":
        result = orch.run_pipeline()
        print(
            f"Pipeline complete. Sentiment={result.sentiment.overall_label} "
            f"({result.sentiment.overall_score}). "
            f"Paper equity=${result.paper.equity:,.2f}. "
            f"Reports in {cfg.reports_dir}"
        )
        return 0

    if args.agent == "sentiment":
        orch.sentiment_agent.run()
    elif args.agent == "activity":
        orch.activity_agent.run()
    elif args.agent == "backtest":
        orch.backtest_agent.run()
    elif args.agent == "paper":
        backtest = orch.backtest_agent.run()
        orch.paper_agent.run(backtest)

    print(f"Agent '{args.agent}' finished. See {cfg.reports_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
