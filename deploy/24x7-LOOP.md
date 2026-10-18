# 24×7 US scan → paper trade → Postgres → evolve until profit

Runs **only on Railway** (not the laptop).

## Loop

1. Every `RUN_INTERVAL_MINUTES` (default **15**), FastAPI scheduler runs a cycle  
2. Sentiment + themes from RSS  
3. **Scan** a rotating batch from `US_LIQUID_UNIVERSE` (~100+ liquid US stocks/ETFs across tech, oil, gold, copper, banks, …)  
4. Backtest → pick top signals → paper buy/sell  
5. Persist runs/trades/mistakes/advice to **PostgreSQL**  
6. Local coach mutates strategy if flat/losing until a **real** champion (winning sells + ≥ `PROFIT_TARGET_PCT`)

## Railway variables

| Variable | Value |
|----------|--------|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `RUN_INTERVAL_MINUTES` | `15` |
| `MARKET_HOURS_ONLY` | `false` |
| `LOCAL_COACH_ENABLED` | `true` |
| `CONTINUE_UNTIL_PROFIT` | `true` |
| `PROFIT_TARGET_PCT` | `5.0` |

## Watch

- `GET /status` — active strategy + last run  
- `GET /trades` — every buy/sell + sell `pnl_pct`  
- `GET /coach` — learning events  

## Honest limit

“All US stocks” means the liquid universe we ship (~100+ names, rotated each cycle). Fetching every listed ticker every 15 minutes is not reliable on free market data; the rotator covers the full set continuously.
