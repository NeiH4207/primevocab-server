# Railway API (`py-server-production`)

## Fix CORS for https://primevocab.com

In **Railway → your API service → Variables**, set (then redeploy):

```env
APP_ENV=production
DEBUG=false
FRONTEND_BASE_URL=https://primevocab.com
CORS_ORIGINS=["https://primevocab.com","https://www.primevocab.com"]
GOOGLE_REDIRECT_URI=https://primevocab.com/auth/callback
```

`FRONTEND_BASE_URL` is also merged into allowed CORS origins automatically (code in `aiforen/core/config.py`), but an explicit `CORS_ORIGINS` list is still recommended.

### Verify after redeploy

```bash
curl -sI -X OPTIONS "https://py-server-production.up.railway.app/api/v1/health" \
  -H "Origin: https://primevocab.com" \
  -H "Access-Control-Request-Method: GET" | grep -i access-control-allow-origin
```

Expected: `access-control-allow-origin: https://primevocab.com`

## Google OAuth (must match frontend)

`GOOGLE_CLIENT_SECRET` on Railway must be the secret for the **same** OAuth client as `GOOGLE_CLIENT_ID` / Vercel `NEXT_PUBLIC_GOOGLE_CLIENT_ID`. If redirect login fails with `auth_failed`, Railway logs often show `invalid_client` / invalid client secret — regenerate the secret in [Google Cloud Console](https://console.cloud.google.com/apis/credentials) and run:

```bash
railway variable set GOOGLE_CLIENT_SECRET='YOUR_SECRET' -s py-server -e production
```

**Authorized JavaScript origins**

- `https://primevocab.com`
- `https://www.primevocab.com`

**Authorized redirect URIs**

- `https://primevocab.com/auth/callback`

## Vercel frontend env

```env
NEXT_PUBLIC_API_BASE_URL=https://py-server-production.up.railway.app/api/v1
NEXT_PUBLIC_GOOGLE_CLIENT_ID=<same as GOOGLE_CLIENT_ID on Railway>
```

## Databases (Postgres + Redis)

PrimeVocab API is **Postgres-only** for persistent data, plus **Redis** for queues and cache.

| Store | Role | Examples |
|-------|------|----------|
| **Postgres** | Source of truth | `users`, plans, `vocab_lexemes`, `writing_*`, `grammar_structures`, `vocab_user_word_state`, `user_learning_stats`, daily missions |
| **Redis** | Cache / streams / quota | Assessment SSE, `stream:assess`, hot keys |

**MongoDB is no longer used.** After deploy:

1. Run `seed_content_pg` (via `python -m aiforen.scripts.seed`) or one-shot `migrate_mongo_to_postgres.py` if you need legacy docs (`pip install -r requirements-migrate.txt` first).
2. Remove the MongoDB service from Railway project `helpful-learning`.
3. Unset `MONGO_URL` / `MONGO_DB` on `py-server`.

### Update Postgres schema (production)

Production may show `alembic_version = 0017` while this repo ships migrations `0001`–`0009`. **Do not** run `alembic upgrade head` on production unless you reconcile that version first.

Preferred path:

1. Deploy latest `py-server` — startup runs `apply_pg_schema_repairs()` (`aiforen/core/schema_repair.py`), which creates migration `0009` tables if missing (`writing_*`, `grammar_*`, `user_learning_stats`, `vocab_attempts`, `progress_data` column).
2. Confirm columns on `vocab_questions`: `task_type`, `difficulty`, `options`, `status`, `correct_option_id`.

```bash
cd primevocab-server
railway link   # project helpful-learning, service Postgres, env production
export DATABASE_PUBLIC_URL=$(railway variables --json | python3 -c "import json,sys; print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])")

python3 -c "
import os, asyncio, asyncpg
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_PUBLIC_URL'])
    print('alembic:', await c.fetchval('SELECT version_num FROM alembic_version'))
    rows = await c.fetch(\"\"\"
      SELECT column_name FROM information_schema.columns
      WHERE table_name='vocab_questions'
        AND column_name IN ('task_type','difficulty','options','status')
      ORDER BY 1
    \"\"\")
    print('columns:', [r['column_name'] for r in rows])
    await c.close()
asyncio.run(main())
"
```

Manual repair (if needed):

```sql
ALTER TABLE vocab_questions ADD COLUMN IF NOT EXISTS difficulty INTEGER NOT NULL DEFAULT 3;
-- App maps ORM field `type` → column `task_type` (do not add a separate `type` column).
```

### Seed writing / grammar content

After schema repair, seed content if tables are empty:

```bash
cd primevocab-server
python -m aiforen.scripts.seed
```

Lexicon lives in **Postgres** (`vocab_lexemes`, packs). See `aiforen/scripts/vocab/README.md` for optional import from `vocab_storage/`.

### Health check

```bash
curl -s https://py-server-production.up.railway.app/api/v1/health | python3 -m json.tool
```

Expect `"postgres": true`, `"redis": true`.
