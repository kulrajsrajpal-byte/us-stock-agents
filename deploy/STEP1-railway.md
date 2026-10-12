# Step-by-step: Railway deploy (do these in order)

> **Cloud-only:** After deploy, the laptop can sleep/shut down. Scheduling, paper trading, and DB all run on Railway.

## Prerequisites on this PC (setup only — not 24/7 runtime)

- [x] Project code ready in `us-stock-agents`
- [ ] Git installed
- [ ] GitHub account logged in
- [ ] Railway account logged in at https://railway.app

---

## Step 1 — Put code on GitHub

### Option A: GitHub website (easiest if CLI is missing)

1. Open https://github.com/new
2. Repository name: `us-stock-agents`
3. Visibility: **Private** recommended
4. Do **not** add README/license (we already have files)
5. Create repository
6. On your PC (Git Bash or PowerShell after Git install):

```powershell
cd C:\Users\C9D7WP\us-stock-agents
git init -b main
git add .
git commit -m "Initial US stock multi-agent for Railway"
git remote add origin https://github.com/YOUR_USER/us-stock-agents.git
git push -u origin main
```

Replace `YOUR_USER` with your GitHub username.

### Option B: GitHub Desktop

1. File → Add Local Repository → `C:\Users\C9D7WP\us-stock-agents`
2. Publish repository → Private → Publish

---

## Step 2 — New Railway project from GitHub

1. Open https://railway.app/new
2. Choose **Deploy from GitHub repo**
3. Authorize Railway → select `us-stock-agents`
4. Railway should detect `Dockerfile` + `railway.toml`
5. Wait for first build (may fail until Postgres + env are set — OK)

---

## Step 3 — Add PostgreSQL

1. In the Railway project → **+ New** → **Database** → **PostgreSQL**
2. Open the FastAPI/service → **Variables**
3. Click **Add Reference** / connect Postgres so `DATABASE_URL` is injected  
   (or copy `DATABASE_URL` from the Postgres service Variables and paste onto the app)

---

## Step 4 — Set app environment variables

On the **app service** → Variables → add:

| Variable | Suggested value |
|----------|-----------------|
| `RUN_INTERVAL_MINUTES` | `30` |
| `MARKET_HOURS_ONLY` | `true` |
| `PROFIT_TARGET_PCT` | `5.0` |
| `MAX_DRAWDOWN_PCT` | `10.0` |
| `CHAMPION_MIN_TRADES` | `5` |
| `INITIAL_CASH` | `100000` |
| `TIMEZONE` | `America/New_York` |
| `API_KEY` | (random secret string) |
| `TELEGRAM_BOT_TOKEN` | (leave empty until bot ready) |
| `TELEGRAM_CHAT_ID` | (leave empty until bot ready) |

`DATABASE_URL` should already come from Postgres.

Redeploy after saving variables.

---

## Step 5 — Public URL + smoke test

1. App service → **Settings** → **Networking** → **Generate Domain**
2. Open `https://YOUR-DOMAIN.up.railway.app/health` → expect `{"status":"ok"}`
3. Force a cycle:

```powershell
curl -X POST "https://YOUR-DOMAIN.up.railway.app/run?force=true" -H "x-api-key: YOUR_API_KEY"
```

4. Check `https://YOUR-DOMAIN.up.railway.app/status`

---

## Step 6 (optional later)

- n8n workflow: import `deploy/n8n-workflow.json`
- Metabase: connect same Postgres (`deploy/metabase-queries.md`)
- Telegram: fill `TELEGRAM_*` vars

---

## What to tell Cursor when each step is done

Reply with one of:

- `Step 1 done — repo URL is https://github.com/...`
- `Step 2 done — Railway project created`
- `Step 3 done — Postgres linked`
- `Step 4 done — env set`
- `Step 5 done — health URL is ...`
