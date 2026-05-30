# Auto-deploy API on merge to `main`

Repo: **`NeiH4207/primevocab-server`** → Railway service **py-server**

Workflow: [`.github/workflows/deploy-production.yml`](../.github/workflows/deploy-production.yml)

Triggers: push/merge to `main`, or manual **Run workflow** in GitHub Actions.

## GitHub secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Value |
|--------|----------|--------|
| `RAILWAY_TOKEN` | **Yes** | **Project token** — Railway → project **helpful-learning** → **Settings → Tokens** → Create. **Do not** use [Account → Tokens](https://railway.com/account/tokens) (CI error: `Project Token not found`). |
| `RAILWAY_SERVICE_ID` | No | `py-server` (default if omitted) or Service UUID from **py-server** → Settings |

`RAILWAY_PROJECT_ID` is **not** needed when using a project token (the token is already scoped to one project/environment).

## Avoid double deploy

If this workflow is enabled, **disconnect** GitHub auto-deploy on Railway for the same service (or disable Railway’s “Deploy on push” for `main`).

## Push workflow to GitHub

From your machine (repo root = this project, not the local monorepo folder name):

```bash
git add .github/workflows/deploy-production.yml docs/AUTO_DEPLOY.md
git commit -m "ci: deploy to Railway on merge to main"
git push origin main
```

## Verify

1. GitHub → **Actions** → **Deploy API (production)** → green  
2. `curl -s https://py-server-production.up.railway.app/api/v1/health`
