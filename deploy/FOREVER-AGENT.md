# Forever Agent — one 24×7 paper trader (no human in the loop)

## What it is

A **single autonomous agent** on Railway that:

1. Scans liquid US stocks/ETFs  
2. **Paper-trades** only (Postgres ledger — never a live broker)  
3. Stores every buy/sell/mistake  
4. Learns from losses and **mutates** the strategy  
5. Repeats every 15 minutes until a real profitable champion  

Inspired by [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) `paper_trade_step` (paper accounts, no broker path). We keep our own FastAPI + Postgres loop so Railway does not need Vibe’s LLM keys or heavy runtime.

## Why not install full Vibe-Trading in this service?

`vibe-trading-ai` is a full LLM agent OS (models, MCP, Web UI, ClickHouse). It needs provider API keys and is too heavy for this slim Railway paper loop. We adopt the **same paper-only idea**; optional local explore:

```bash
pip install -U vibe-trading-ai
```

See their README for `vibe-trading paper create|advance`.

## Railway (already enforced in code)

| Setting | Value |
|---------|--------|
| Interval | 15 minutes |
| Market hours gate | off (24×7) |
| Continue until profit | on |
| Local coach | on |

## Watch

- `GET /health` → version + interval  
- `GET /agent` → Forever Agent status  
- `GET /run?force=true` → one cycle now  
- `GET /trades` → every fill + sell `pnl_pct`  
- `GET /status` → strategy / champion  

Laptop is for git push / Railway UI only — never run the 24×7 loop locally.
