# US Stock Multi-Agent (Railway 24/7)

Four specialist agents + continuous paper trading that **mutates strategy parameters until a champion** hits your profit target.

## Railway tools we use (from your catalog)

| Deploy | Role |
|--------|------|
| **FastAPI / Python / Docker** | This app — agents, scheduler, API |
| **PostgreSQL** | Strategies, trades, mistakes, runs |
| **Hermes Self Improving** | Learns from old strategies; suggests mutations |
| **Redis** | Optional cache later |
| **n8n** | Optional extra cron → `POST /run` |
| **Telegram Bot** | Cycle + champion alerts |
| **Metabase** | Equity / leaderboard dashboards |

Hermes wiring: [`deploy/hermes.md`](./deploy/hermes.md).

Full deploy steps: **[RAILWAY.md](./RAILWAY.md)**

## Agents

1. **Sentiment** — US news RSS + VADER  
2. **Activity** — themes + impacted tickers  
3. **Backtest** — MA crossover + param hints  
4. **Paper trading** — simulated book; losses → lessons → mutate  

## Local

```powershell
cd C:\Users\C9D7WP\us-stock-agents
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python run.py                 # one pipeline
python run.py --serve         # FastAPI + scheduler on :8000
```

Docker Compose (API + Postgres + Redis):

```powershell
docker compose up --build
```

## API

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Railway healthcheck |
| `POST /run` | Force one cycle (`x-api-key` if set) |
| `GET /status` | Active strategy + last run |
| `GET /strategies` | Evolution history |

## Evolution knobs (env)

- `PROFIT_TARGET_PCT=5` — promote champion  
- `MAX_DRAWDOWN_PCT=10` — retire + mutate  
- `CHAMPION_MIN_TRADES=5` — minimum evidence  
- `RUN_INTERVAL_MINUTES=30` — loop cadence  
- `MARKET_HOURS_ONLY=true` — US cash session only  

**Paper trading only — no live broker orders.**
