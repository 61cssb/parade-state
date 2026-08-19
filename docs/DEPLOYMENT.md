# Deployment Guide

**Purpose:** Complete guide for deploying the Parade State application to production.

**Audience:** DevOps engineers, developers deploying to production, and system administrators.

## Table of Contents

- [Pre-Deployment Checklist](#pre-deployment-checklist)
- [Environment Configuration](#environment-configuration)
- [Database Migrations](#database-migrations)
- [Deployment Platforms](#deployment-platforms)
- [Health Checks](#health-checks)
- [Monitoring and Logging](#monitoring-and-logging)
- [Rollback Procedures](#rollback-procedures)
- [Troubleshooting](#troubleshooting)

---

## Pre-Deployment Checklist

### Before Deploying

**Environment Setup:**
- [ ] Production database provisioned (PostgreSQL recommended)
- [ ] Environment variables configured and secured
- [ ] SSL/TLS certificates configured (HTTPS required)
- [ ] Domain name configured with DNS
- [ ] Google OAuth application configured
- [ ] Backup strategy in place
- [ ] Monitoring and logging configured

**Application Readiness:**
- [ ] All tests passing (`uv run pytest`)
- [ ] Database migrations prepared
- [ ] No hardcoded secrets in code
- [ ] Dependencies up to date (`uv run pip-audit`)
- [ ] Performance testing completed
- [ ] Security review completed

---

## Environment Configuration

### Required Environment Variables

**Environment:**
```bash
# "production" enables hardening: fail-fast secrets, strict CORS,
# Secure cookies, no /docs. Railway deployments are auto-detected as
# production; set this explicitly on any other platform.
ENVIRONMENT="production"
```

**Database:**
```bash
# Production database connection (PostgreSQL recommended)
# Both spellings work: the app normalizes sync-style URLs to the asyncpg
# driver at startup (postgresql:// and postgres:// → postgresql+asyncpg://),
# and translates ?sslmode=... to asyncpg's ?ssl=...
DATABASE_URL="postgresql://user:password@host:5432/dbname"
```

**Authentication:**
```bash
# Google OAuth configuration (required in production — the app refuses
# to boot without them)
GOOGLE_CLIENT_ID="your-google-client-id.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET="your-google-client-secret"

# Session management (required in production)
SESSION_SECRET="your-cryptographic-secret-min-32-characters"
```

**Super Admin:**
```bash
# Super admin email for initial bootstrap (required in production)
SUPER_ADMIN_EMAIL="admin@yourdomain.com"
```

**CORS:**
```bash
# Origins allowed to make credentialed requests (comma-separated).
# Required in production — "*" is rejected there; development defaults
# to permissive "*" for local tooling.
ALLOWED_ORIGINS="https://your-app-domain.com"
```

**Application:**
```bash
# Application base URL (for OAuth callbacks)
APP_BASE_URL="https://your-app-domain.com"

# Optional: Debug mode (disable in production!)
DEBUG=false

# Optional: auth cookie Secure flag (defaults to true in production;
# override to false only for local HTTP testing of a production build)
# AUTH_COOKIE_SECURE=true

# Optional: testing-only admin-UI purge of all Nominal Rolls and
# downstream data (Settings page; audit-logged). Defaults to false in
# production, true elsewhere. Keep disabled on the production deployment.
# PURGE_ENABLED=false
```

### Production Hardening Behavior

When `ENVIRONMENT=production` (explicitly or via Railway auto-detection):

- **Fail-fast startup:** the app refuses to boot if `SESSION_SECRET`,
  `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SUPER_ADMIN_EMAIL`, or
  explicit `ALLOWED_ORIGINS` is missing — no fallback secrets exist
- **Secure cookies:** the auth session cookie carries the `Secure` flag
  (HTTPS-only transmission)
- **Strict CORS:** credentialed requests are accepted only from
  `ALLOWED_ORIGINS`
- **No OpenAPI exposure:** `/docs`, `/redoc`, and `/openapi.json` return
  404 (they remain available in development)

### Generating Secure Secrets

**Session Secret:**
```bash
# Generate secure 32+ character secret
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Database Password:**
```bash
# Generate secure database password
python -c "import secrets; print(secrets.token_urlsafe(16))"
```

---

## Database Migrations

### Alembic Migration System

The application uses **Alembic** for database migrations.

**URL scheme note:** Alembic's `env.py` reads `DATABASE_URL` and applies the
same normalization as the application engine (`postgresql://` →
`postgresql+asyncpg://`, `sslmode` → `ssl`), so one environment variable
works for both migrations and the app.

**Migration Files Location:**
```
src/parade_state/migrations/
├── versions/
│   └── bef66a2a675e_add_audit_trail_to_personnel.py
├── env.py
└── script.py.mako
```

### Running Migrations

**Development (SQLite):**
```bash
# Run migrations
uv run alembic upgrade head

# Check current migration version
uv run alembic current

# View migration history
uv run alembic history

# Rollback one migration
uv run alembic downgrade -1
```

**Production (PostgreSQL):**
```bash
# Set production database URL
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"

# Run migrations
uv run alembic upgrade head
```

### Creating New Migrations

**After modifying models:**
```bash
# Generate migration from model changes
uv run alembic revision --autogenerate -m "Description of changes"

# Review generated migration
# Edit: src/parade_state/migrations/versions/[new_migration].py

# Apply migration
uv run alembic upgrade head
```

### Production Migration Strategy

**Safe Migration Process:**

1. **Backup Database First:**
   ```bash
   # PostgreSQL backup
   pg_dump -U user -h host dbname > backup_$(date +%Y%m%d_%H%M%S).sql
   
   # Or use pg_dump for custom format
   pg_dump -U user -h host -Fc dbname > backup_$(date +%Y%m%d_%H%M%S).dump
   ```

2. **Test Migration in Staging:**
   ```bash
   # Copy production database to staging
   # Run migration in staging first
   uv run alembic upgrade head
   
   # Test application thoroughly
   uv run pytest
   ```

3. **Run Production Migration:**
   ```bash
   # Set maintenance mode if needed
   # Run migration
   DATABASE_URL="prod_url" uv run alembic upgrade head
   
   # Verify migration success
   DATABASE_URL="prod_url" uv run alembic current
   ```

4. **Verify Application:**
   ```bash
   # Check application health
   curl https://your-app.com/health
   
   # Monitor logs for errors
   ```

### Migration Rollback

**If migration fails:**

1. **Identify Failed Migration:**
   ```bash
   uv run alembic current
   ```

2. **Rollback to Previous Version:**
   ```bash
   uv run alembic downgrade -1
   ```

3. **Restore Database (if needed):**
   ```bash
   # PostgreSQL restore
   psql -U user -h host dbname < backup_file.sql
   
   # Or for custom format
   pg_restore -U user -h host -d dbname backup_file.dump
   ```

---

## Deployment Platforms

### Railway (Recommended)

**Deployment Process:**

1. **Create New Project:**
   - Go to [railway.app](https://railway.app)
   - Create new project from GitHub repository

2. **Configure Services:**
   - **PostgreSQL Database:** Add PostgreSQL database
   - **Environment Variables:** Configure required variables
   - **Root Domain:** Configure custom domain (optional)

3. **Set Environment Variables:**
   ```
   DATABASE_URL           # Auto-injected by Railway (internal URL, no SSL needed)
   SUPER_ADMIN_EMAIL      # Your admin email
   GOOGLE_CLIENT_ID       # Google OAuth client ID
   GOOGLE_CLIENT_SECRET   # Google OAuth client secret
   SESSION_SECRET         # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
   ALLOWED_ORIGINS        # e.g. https://your-app.up.railway.app (no "*" in production)
   APP_BASE_URL           # Railway provides this
   ```

   Railway is auto-detected as production, so the deployment fails fast
   with a clear error if any required variable above is missing — this is
   intentional (see "Production Hardening Behavior" above).

4. **Build and Start:**
   - Railway detects the [Dockerfile](../Dockerfile) at the repo root and builds it
   - The image's start command runs `alembic upgrade head` (migrations) and
     then serves uvicorn on the platform-injected `$PORT`
   - No start-command override is needed in the Railway dashboard

5. **Deploy:**
   - Push to main branch
   - Railway auto-deploys on push
   - Monitor deployment logs

**Railway-Specific Features:**

- **Auto-scaling:** Configurable based on CPU/memory
- **Metrics:** Built-in monitoring dashboards
- **Logs:** Real-time log streaming
- **Deployments:** Automatic on git push
- **Rollback:** One-click rollback to previous deployment

### Environments: Production and Development

The Railway project `parade-state` (workspace "JS Ng's Projects") hosts two
independent environments, each with its own `parade-state` app service and
`Postgres` database (region asia-southeast1). **Databases are fully
separated** — a migration tested in development cannot touch the production
schema (the shared-DB approach was considered and rejected in issue #15).

| | Production | Development |
|---|---|---|
| Railway environment | `production` | `development` |
| Domain | `parade-state-production.up.railway.app` | `parade-state-development.up.railway.app` |
| Tracks branch | `main` (GitHub auto-deploy) | `dev` (GitHub auto-deploy) |
| Database | Only real data | Empty start; resettable (`PURGE_ENABLED=true`) |
| `SESSION_SECRET` | own value | **own value** (never share between environments) |
| `ALLOWED_ORIGINS` / `APP_BASE_URL` | production domain | development domain |
| `PURGE_ENABLED` | `false` | `true` (admin-UI data purge for testing) |

`DATABASE_URL` is the environment-scoped reference
`${{Postgres.DATABASE_URL}}` in both environments — it resolves to that
environment's own Postgres, so it must never be hardcoded.

**Google OAuth:** one client serves both environments; each environment's
callback (`https://<domain>/auth/callback`) is registered as a redirect URI
on the client. The redirect URI is derived from the request host at
runtime, so no code/config distinguishes environments.

**Branch model and promotion:**

1. Feature branches → PR into `dev` → auto-deploys to `development`, where
   test users try changes first
2. When validated, PR `dev` → `main` → auto-deploys to `production`
   (production always tracks `main`)

**Deploying to development manually** (GitHub auto-deploy handles pushes to
`dev`; use this to work around it):

```bash
railway link --workspace "JS Ng's Projects" --project parade-state --environment development
railway up --detach -m "what changed"
railway deployment list   # poll to a terminal status — never trust queued/building
```

Free-tier note: deploys to asia-southeast1 are refused during peak hours
(08:00–20:00 SGT). Schedule deploys outside that window rather than
retrying.

**Bootstrapping a fresh development database:** the DB starts empty (no
users). The first sign-in matching `SUPER_ADMIN_EMAIL` bootstraps as
super_admin; pre-provision everyone else (active, correct role) at
`/admin/users` before their first sign-in, otherwise they are created as
`unrecognised`.

**Refreshing development from a production dump** (when prod-realistic data
is wanted — read-realistic, write-isolated):

```bash
# 1. Dump production via its public TCP proxy (client must be PG 18+)
pg_dump -Fc \
  "postgresql://postgres:<password>@shinkansen.proxy.rlwy.net:48032/railway?sslmode=require" \
  -f prod.dump

# 2. Restore into development via the admin UI: sign in on the development
#    domain as super_admin → admin "Restore Backup" page
#    (/admin/database-restore) → upload prod.dump (RESTORE_ENABLED; the
#    image ships postgresql-client-18, and the restore validates the archive
#    and swaps via a temporary database)
```

Warning: this copies real PII onto the development database and wipes
whatever dev data existed — coordinate with test users first.

**Never point at development:** the nightly backup workflow
(`RAILWAY_PUBLIC_DATABASE_URL` secret) targets production's public proxy
only; see [BACKUP_SETUP.md](BACKUP_SETUP.md).

### Other Platforms

**Docker Deployment:**

The repo root ships the production [Dockerfile](../Dockerfile). It installs
dependencies with uv (pinned, `--frozen --no-dev`), copies the source, and
runs as an unprivileged user. The start command runs migrations before
serving:

```bash
# Build and run (migrations run automatically, then uvicorn on port 8000)
docker build -t parade-state .
docker run -p 8000:8000 --env-file .env parade-state

# To use a different port (e.g. Railway's injected PORT)
docker run -p 8000:8000 -e PORT=8000 --env-file .env parade-state
```

**Traditional VPS (DigitalOcean, AWS, etc.):**

```bash
# Install dependencies
sudo apt update
sudo apt install -y python3.12 postgresql nginx

# Clone repository
git clone <repo-url>
cd parade-state

# Install uv
pip install uv

# Install dependencies
uv sync

# Configure environment
sudo nano .env

# Run with systemd
sudo nano /etc/systemd/system/parade-state.service
```

---

## Health Checks

### Health Endpoint

The application includes a health check endpoint:

```bash
curl https://your-app.com/health
```

**Response (Healthy):**
```json
{
  "status": "healthy",
  "timestamp": "2026-05-10T12:00:00Z"
}
```

### Monitoring Health

**Setup Monitoring (Railway):**

1. **Enable Metrics:** Railway provides built-in metrics
2. **Set Up Alerts:** Configure alerting for:
   - CPU usage > 80%
   - Memory usage > 80%
   - Response time > 1s
   - Error rate > 5%

**External Monitoring (Optional):**

```python
# Add to application: /ping endpoint
@app.get("/ping")
async def ping():
    """Simple ping endpoint for external monitoring."""
    return {"status": "ok"}
```

---

## Monitoring and Logging

### Application Logging

**Log Levels:**
- `DEBUG`: Detailed information for diagnosing problems
- `INFO`: General information about application flow
- `WARNING`: Something unexpected happened
- `ERROR`: Serious problem occurred
- `CRITICAL`: Critical error, application may be unable to continue

**Enable Logging (Production):**
```python
# In production, configure structured logging
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

### Database Logging

**Enable SQL Query Logging (Development Only):**
```python
# In src/parade_state/db/__init__.py
_engine = create_async_engine(
    database_url,
    echo=True,  # Log all SQL queries (development only!)
)
```

### Log Aggregation

**Railway:** Built-in log streaming

**External Services (Optional):**
- **Datadog:** Full-stack monitoring
- **Loggly:** Log aggregation
- **Papertrail:** Log management
- **Sentry:** Error tracking

---

## Rollback Procedures

### Application Rollback

**Railway:**
1. Go to Railway project
2. Select "Deployments"
3. Click on previous successful deployment
4. Click "Rollback"

**Manual Rollback:**
```bash
# Revert to previous commit
git revert HEAD
git push origin main

# Or checkout previous deployment commit
git checkout <previous-commit-hash>
git push origin main --force
```

### Database Rollback

Verified on fresh PostgreSQL 16: the full chain upgrades cleanly, and
`alembic downgrade` works for realistic rollback windows (e.g. `downgrade -4`
then `upgrade head` round-trips). Full `downgrade base` also completes, but
leaves the native enum types behind (Postgres does not drop types with
tables) — re-upgrading *that same database* then fails on type creation.
After a downgrade-to-base, migrate into a fresh database instead.

**If Migration Failed:**
```bash
# Identify current migration
uv run alembic current

# Rollback one migration
uv run alembic downgrade -1

# Or rollback to specific migration
uv run alembic downgrade <target-revision>
```

**Restore from Backup (if needed):**

See the [Backup Strategy](#backup-strategy) section for the full, tested
restore procedure (age decryption + `pg_restore`).

---

## Troubleshooting

### Common Deployment Issues

**1. Migration Fails:**

**Problem:** Migration fails with "relation already exists"

**Solution:**
```bash
# Check current migration state
uv run alembic current

# Mark migration as complete without running
uv run alembic stamp head

# Or resolve manually and continue
```

**2. Database Connection Errors:**

**Problem:** "Could not connect to database"

**Solution:**
- Verify DATABASE_URL is correct
- Check database is accessible from application
- Verify firewall rules allow connections
- Check database credentials

**3. OAuth Callback Errors:**

**Problem:** OAuth redirect fails

**Solution:**
- Verify APP_BASE_URL matches OAuth configuration
- Check GOOGLE_CLIENT_ID and SECRET are correct
- Verify OAuth redirect URIs match application URL
- Check for HTTP vs HTTPS mismatch

**4. Session Errors:**

**Problem:** "Session secret not configured"

**Solution:**
```bash
# Generate and set SESSION_SECRET
export SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

**5. Import Errors:**

**Problem:** "Module not found" errors

**Solution:**
```bash
# Reinstall dependencies
uv sync

# Clear Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
```

### Performance Issues

**Slow Database Queries:**

**Diagnose:**
```bash
# Enable query logging
# Check slow queries in logs
```

**Solutions:**
- Add database indexes (see [docs/PERFORMANCE.md](PERFORMANCE.md))
- Use pagination for large result sets
- Optimize N+1 queries
- Use connection pooling

**High Memory Usage:**

**Solutions:**
- Use generators instead of lists
- Close database sessions properly
- Increase memory limits
- Check for memory leaks

---

## Security Considerations

### Production Security Checklist

**Before Production Deployment:**

- [ ] HTTPS enforced (SSL/TLS configured)
- [ ] Environment variables secured (not in git)
- [ ] Database access restricted (firewall rules)
- [ ] Session secret is cryptographically secure
- [ ] Debug mode is disabled
- [ ] OAuth secrets are rotated regularly
- [ ] Database backups are encrypted
- [ ] Access logs are monitored
- [ ] Rate limiting is configured
- [ ] CORS is properly configured

### Security Monitoring

**Monitor For:**
- Failed login attempts
- Unusual access patterns
- SQL injection attempts
- XSS attempts
- CSRF attacks
- Data exfiltration attempts

**Tools:**
- **Sentry:** Error tracking and alerting
- **Datadog:** Security monitoring
- **Cloudflare:** WAF and DDoS protection

---

## Backup Strategy

### Decision

Backups run as a **GitHub Actions scheduled job** ([`.github/workflows/backup-db.yml`](../.github/workflows/backup-db.yml)):
daily `pg_dump` (custom format) over Railway's public TCP proxy → **age public-key
encryption** → upload to a **Google Drive** folder owned by the designated
super-admin → 30-day retention sweep.

Why this setup:

- Railway's built-in backups require the Pro plan; the free tier has none.
- Public repo → Actions minutes are free, and the backup lives off-platform
  (GitHub secrets hold only the *public* age key; the private key never
  leaves the super-admin).
- Usage is concentrated in one two-week window per year with sporadic use
  otherwise; daily granularity plus on-demand manual runs cover both, and
  minor data loss (≤1 day) is acceptable per the DR posture.

### One-Time Setup (super-admin)

The full step-by-step runbook — including every pitfall hit during the
original setup (full-URL requirement, `sslmode`, variable-vs-secret
distinction, Google's service-account storage-quota change, client/server
version rules) and a troubleshooting table — lives in
**[BACKUP_SETUP.md](BACKUP_SETUP.md)**.

Summary of what it provisions:

- Railway public TCP proxy → GitHub secret `RAILWAY_PUBLIC_DATABASE_URL`
- age keypair → secret `AGE_PUBLIC_KEY` (private key in the super-admin's
  password manager — the only way to restore)
- Google Drive OAuth token from `rclone authorize "drive"` run by the
  super-admin (service accounts can no longer own My Drive files) →
  secret `GDRIVE_OAUTH_TOKEN`
- Super-admin-owned Drive folder → repository **variable**
  `GDRIVE_ROOT_FOLDER_ID`
- One manual workflow run to verify a `parade-state-<timestamp>.dump.age`
  lands in Drive

### Schedule and Retention

- Runs daily at 19:23 UTC (03:23 SGT), plus manual dispatch anytime.
- Backups older than 30 days are deleted from Drive by the same job.
- **GitHub disables scheduled workflows after 60 days without repo
  activity.** Before the annual intensive-use window (and during long
  dormant stretches), push any commit or trigger a manual run to keep the
  schedule alive.

### Restore Procedure (tested 2026-08-19)

**Primary path — admin UI.** Sign in as the super-admin → **DB Restore**
(`/admin/database-restore`), upload the *decrypted* `backup.dump`
(decrypt offline with the age key first — see
[BACKUP_SETUP.md](BACKUP_SETUP.md) Step 7), type the database name to
confirm. The app restores into a scratch database, verifies it (schema
revision not newer than the build, core tables present), and swaps it
into place without touching the live data until verification passes.
The page reports a summary; the operation is audit-logged
(`database` / `restore`). Requires `RESTORE_ENABLED` (default true) and
ships `pg_restore` in the container image.

**Fallback path — CLI** (app down, or restoring into a brand-new
database):

> The CLI path was verified end-to-end: seeded database → `pg_dump` →
> age encrypt/decrypt round-trip → `pg_restore` into a fresh PostgreSQL →
> row counts matched and the app booted and served `/health` and `/docs`.

**1. Fetch and decrypt** (super-admin, any machine):

```bash
# download parade-state-<timestamp>.dump.age from the Drive folder
age -d -i parade-state-backup.key -o backup.dump parade-state-<timestamp>.dump.age
```

**2. Optional local verification** before touching production:

```bash
docker run -d --name restore-check -e POSTGRES_PASSWORD=test \
  -e POSTGRES_USER=test -p 127.0.0.1:55433:5432 postgres:18-alpine
# pg_restore must come from a client >= the dump's major version (18)
docker run --rm -i postgres:18-alpine pg_restore -U test --no-owner \
  -h 172.17.0.1 -d postgres < backup.dump   # adjust host for your docker network
DATABASE_URL=postgresql://test:test@127.0.0.1:55433/postgres \
  uv run uvicorn parade_state.main:app --port 8123
curl -sf http://127.0.0.1:8123/health
```

**3. Restore to Railway:**

```bash
# Fresh Postgres service (or recreated volume) in the Railway project;
# copy its internal/public connection URL, then from any machine:
pg_restore --no-owner --no-privileges --dbname="$RESTORE_DATABASE_URL" backup.dump
```

Use a `pg_restore` matching the server's major version (the production
server is PostgreSQL 18 and the workflow installs `postgresql-client-18`
from the PGDG repo). Clients must be same-major-or-newer than the dump;
mismatched newer clients also emit settings older servers reject (seen
with a v18 client against a v16 server).

The dump includes `alembic_version`, so the app's startup
`alembic upgrade head` is a no-op after restore. Point the app service's
`DATABASE_URL` at the restored database, redeploy, and verify `/health`,
then spot-check data via the admin UI.

### Disaster Recovery

1. **Assess:** what data is lost, and as of when? Pick the newest backup
   that predates the damage.
2. **Restore** using the procedure above (backup age is at most ~24 h).
3. **Verify:** `/health`, admin UI spot checks, monitor logs for errors.
4. **Post-incident:** if the database URL changed, update
   `RAILWAY_PUBLIC_DATABASE_URL` (and Railway variable references) to match
   the new database.

---

## Maintenance

### Regular Maintenance Tasks

**Daily:**
- Monitor application logs
- Check error rates
- Verify backup completion

**Weekly:**
- Review security logs
- Check disk space
- Monitor performance metrics

**Monthly:**
- Apply security updates
- Review and rotate secrets
- Test disaster recovery
- Update dependencies

**Quarterly:**
- Security audit
- Performance review
- Capacity planning
- Documentation update

---

## Support

**For deployment issues:**
- Check logs: `Railway → Logs`
- Run diagnostics: See [Troubleshooting](#troubleshooting)
- Review architecture: [docs/ARCHITECTURE.md](ARCHITECTURE.md)

**For application issues:**
- Check health: `GET /health`
- Review API docs: `/docs`
- Check testing: [docs/TESTING.md](TESTING.md)

---

**Contributing:** When deploying to new environments or discovering deployment issues, update this document.

**See Also:** 
- [ARCHITECTURE.md](ARCHITECTURE.md) for system architecture
- [IMPLEMENTATION.md](IMPLEMENTATION.md) for implementation details
- [PERFORMANCE.md](PERFORMANCE.md) for performance optimization
