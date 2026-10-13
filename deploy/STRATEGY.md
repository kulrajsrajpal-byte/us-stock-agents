# Strategy reference (do you need a book?)

**No book is required** for the current system.

## What we use today

| Piece | Source |
|-------|--------|
| Signal | Classic **moving-average crossover** (fast MA vs slow MA) |
| Defaults | `fast_ma=10`, `slow_ma=30` in `config.yaml` |
| Learning | Paper P&amp;L + mistakes → mutate params; optional **Hermes** coach |
| News | Free RSS headlines + VADER sentiment (not a paid research book) |

This is a **starter / teaching strategy**, common in almost every technical-analysis intro — not copied from one paid course.

## Optional books (later, if you want better ideas)

You can plug ideas from these into new strategy modules later; none are required now:

- John J. Murphy — *Technical Analysis of the Financial Markets* (MAs, trends)
- Robert Colby — *The Encyclopedia of Technical Market Indicators*
- Ernie Chan — *Algorithmic Trading* / *Quantitative Trading* (backtest discipline)
- Lopez de Prado — *Advances in Financial Machine Learning* (advanced; later)

## How to change strategy later

1. Keep paper trading + Postgres evolution loop.
2. Replace or add rules under `src/us_stock_agents/agents/backtest.py` and `paper_trading.py`.
3. Or let **Hermes** propose parameter / rule changes from past mistakes.

Still **paper trading only** — not live broker orders.
