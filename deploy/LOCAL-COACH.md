# Built-in self-improving coach (no Hermes login)

You do **not** need Hermes. The FastAPI app now includes a **local Postgres learner** that:

1. Runs paper trading 24×7 on Railway  
2. Stores every run / trade / mistake / strategy in PostgreSQL  
3. Learns from prior strategies (best/worst MA windows, stop-outs, lessons)  
4. Mutates parameters until a **champion** hits `PROFIT_TARGET_PCT`  

## Enable (defaults are already on)

On `us-stock-agents` Variables:

| Variable | Value |
|----------|--------|
| `LOCAL_COACH_ENABLED` | `true` |
| `CONTINUE_UNTIL_PROFIT` | `true` |
| `PROFIT_TARGET_PCT` | `5.0` |
| `MAX_DRAWDOWN_PCT` | `10.0` |
| `CHAMPION_MIN_TRADES` | `5` |
| `RUN_INTERVAL_MINUTES` | `30` |

Leave Hermes vars unset/`HERMES_ENABLED=false`.

## Check

- `GET /coach` → `"coach.enabled": true`, `"needs_external_api": false`  
- `GET /run?force=true` → `"coach_taught": true`, mutations show `source=local_coach`  
- Advice rows land in Postgres table `hermes_advice` with `event_type` like `local_patch` / `local_teach`

## Honest limit

This finds parameters that look better in **paper** history. It does not guarantee live profit.
