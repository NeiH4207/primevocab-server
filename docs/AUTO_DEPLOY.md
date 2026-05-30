# Auto-deploy API on merge to `main`

Repo: **`NeiH4207/primevocab-server`** → Railway service **py-server**

Workflow: [`.github/workflows/deploy-production.yml`](../.github/workflows/deploy-production.yml)

Triggers: push/merge to `main`, or manual **Run workflow** in GitHub Actions.

## GitHub secrets

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|--------|--------|
| `RAILWAY_TOKEN` | [Railway → Account → Tokens](https://railway.com/account/tokens) — **never commit or paste in chat** |
| `RAILWAY_PROJECT_ID` | Railway project → Settings → **Project ID** |
| `RAILWAY_SERVICE_ID` | `py-server` (service name) or Service UUID from **py-server** → Settings |

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
