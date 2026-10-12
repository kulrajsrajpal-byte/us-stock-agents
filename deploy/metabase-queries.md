# Metabase queries (connect to Railway Postgres)

## Paper equity over time

```sql
SELECT created_at, paper_equity, paper_return_pct, sentiment_label
FROM pipeline_runs
ORDER BY created_at;
```

## Strategy leaderboard

```sql
SELECT version, status, fast_ma, slow_ma, paper_return_pct, trade_count, is_champion
FROM strategies
ORDER BY paper_return_pct DESC NULLS LAST;
```

## Losing trades / lessons

```sql
SELECT created_at, symbol, pnl_pct, lesson
FROM mistakes
ORDER BY created_at DESC
LIMIT 100;
```

## Champions only

```sql
SELECT *
FROM strategies
WHERE is_champion = true;
```
