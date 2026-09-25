# developlocal: low-cost development branch

Branched from `develop` (`5e1c7c9`). The goal is to pay (almost) nothing while Creative Studio isn't being used, and to need no manual start when you use it again.

## Changes

| Area | Upstream | developlocal |
|---|---|---|
| Backend Cloud Run minimum instances | `1` (always one warm instance, ~$25/month) | `be_min_instances = 0`: scales to zero when idle |
| Stopped Cloud SQL | Backend fails to start | `db_autostart = true`: the backend starts the database on the first request |
| Cloud SQL tier / edition | Hard-coded `db-perf-optimized-N-2` | Variables `db_tier` / `db_edition` (default unchanged) |

Files:
- `backend/src/database_autostart.py`: starts a stopped instance through the Cloud SQL Admin API (activation policy `ALWAYS`), waits for a working connection, then runs migrations.
- `backend/main.py`: when `DB_AUTOSTART=true`, migrations run in the background at startup, and a middleware holds API requests until the database is ready. Health endpoints (`/`, `/api/version`) never wait.
- `backend/src/config/config_service.py`: `DB_AUTOSTART`, `DB_AUTOSTART_TIMEOUT_SECONDS` (600), `DB_AUTOSTART_REQUEST_WAIT_SECONDS` (45).
- `frontend/src/app/db-starting.interceptor.ts` (registered in `app.module.ts`): retries API calls while the database starts.
- `infra/modules/platform`, `infra/modules/postgresql`, `infra/modules/cloud-run-service`, `infra/environments/dev-infra-example`: the new variables, `DB_AUTOSTART` env var, and `roles/cloudsql.editor` for the backend service account (only when `db_autostart = true`).

## What happens on the first request after idle

1. The browser loads the frontend from Firebase Hosting (always available).
2. The first API call cold-starts the backend (a few seconds). The backend sees the database is stopped and starts it (usually 1–3 minutes).
3. Firebase Hosting proxies `/api/**` with a 60-second limit. So after 45 s the backend answers `503` + `X-DB-Starting: true`, and the frontend retries every 10 s for up to ~5 minutes. Requests are retried only when the backend hasn't processed them (or GET on a proxy 504), so a generation is never submitted twice.
4. Once the database is up, the page loads and everything is normal. Later requests don't wait.

## What stops it again

Nothing in this branch stops the database. Stopping is done from outside:
- the pipeline's `cap gcc schedule` (Cloud Scheduler, default 17:00 region-local time, development only), or
- `cap gcc sleep` / `gcloud sql instances patch <instance> --activation-policy=NEVER`.

The backend scales to zero by itself after ~15 minutes without traffic.

## Deploy

Run `bootstrap.sh` from this branch and answer `developlocal` when it asks for the branch. GCC's Cloud Build trigger then deploys every push to `developlocal`. For an existing deployment, re-run Terraform in `infra/environments/<env>` so the backend minimum instances, the env var and the IAM role are applied.

Cheaper database for development (optional, set in `infra/environments/<env>/<env>.tfvars` **before the first apply**; changing it later restarts the instance):

```hcl
db_edition = "ENTERPRISE"
db_tier    = "db-custom-1-3840"   # 1 vCPU / 3.75 GB, roughly $50/month when running 24/7
```

Keep `main` (upstream defaults: warm backend, Enterprise Plus database, no autostart) for enterprise/production.
