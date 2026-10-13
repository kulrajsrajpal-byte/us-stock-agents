# US market hours (how this app schedules)

## Official US cash session (what the code uses)

| | |
|--|--|
| Session | Regular equity hours |
| Days | Monday–Friday (exchange holidays not modeled yet) |
| Timezone | `America/New_York` (Eastern Time) |
| Open | **09:30** |
| Close | **16:00** |

Env on Railway:

- `TIMEZONE=America/New_York`
- `MARKET_HOURS_ONLY=true` → automatic cycles only during that window  
- `MARKET_HOURS_ONLY=false` → automatic cycles 24/7 (still paper trading)

## If you are in India (IST)

US open **9:30 AM Eastern** is about:

| US Eastern | Rough IST |
|------------|-----------|
| 09:30 open | **~7:00 PM** IST (when US is on daylight time / EDT) |
| 09:30 open | **~8:00 PM** IST (when US is on standard time / EST) |
| 16:00 close | **~1:30 AM / 2:30 AM** IST next calendar day |

So “market starts ~7 PM” for you is usually **your local (IST) clock**, not 7 PM in New York.

Keep Railway `TIMEZONE=America/New_York` so trading logic matches the real US exchange.

## Option B — run now in the browser

Open:

```text
https://YOUR-DOMAIN.up.railway.app/run?force=true
```

`force=true` **ignores** market hours, so you can test even when the US market is closed.  
Automatic runs (every 30 minutes) still respect `MARKET_HOURS_ONLY`.
