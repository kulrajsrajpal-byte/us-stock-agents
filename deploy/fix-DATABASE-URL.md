# Fix DATABASE_URL using Railway reference variables

Official docs: https://docs.railway.com/variables#reference-variables

## Correct pattern (from Railway docs)

Reference another service’s variable with template syntax:

```text
${{SERVICE_NAME.VAR}}
```

Example from docs (Clickhouse → your app):

```text
DATABASE_URL=${{Clickhouse.DATABASE_URL}}
```

For this project, the database service card is named **Postgres**, so on **`us-stock-agents`** set:

| Name | Value |
|------|--------|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |

Spelling of `Postgres` must match the **exact service name** on the Railway canvas (case-sensitive).

## Steps (app service Variables)

1. Open **`us-stock-agents` → Variables**.
2. Delete any old `DATABASE_URL` that contains `localhost` or a hardcoded password URL.
3. **+ New Variable**
   - Name: `DATABASE_URL`
   - Value: `${{Postgres.DATABASE_URL}}`  
     (use the autocomplete dropdown if it appears — Railway docs mention this)
4. Click **Add**.
5. **Important:** adding/updating variables only **stages** changes. Click **Deploy** to apply them (docs: “review and deploy”).

## What you should see

- On **Postgres → Variables**, the real URL looks like:  
  `postgresql://...@postgres.railway.internal:5432/railway`  
  That is normal (private network). Do **not** paste that into the app unless the reference fails; prefer `${{Postgres.DATABASE_URL}}`.
- On **`us-stock-agents`**, after deploy, `DATABASE_URL` should resolve to that same internal URL at runtime.
- Deploy logs should show `postgresql`, not `sqlite`.

## Do not

- Use `localhost` on the app service.
- Leave Suggested Variables (`REDIS_URL`, Telegram, blank `API_KEY`) empty and click Add.
- Forget to **Deploy** after editing variables.
