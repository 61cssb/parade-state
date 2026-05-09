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

**Database:**
```bash
# Production database connection (PostgreSQL recommended)
DATABASE_URL="postgresql://user:password@host:5432/dbname"
```

**Authentication:**
```bash
# Google OAuth configuration
GOOGLE_CLIENT_ID="your-google-client-id.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET="your-google-client-secret"

# Session management
SESSION_SECRET="your-cryptographic-secret-min-32-characters"
```

**Super Admin:**
```bash
# Super admin email for initial bootstrap
SUPER_ADMIN_EMAIL="admin@yourdomain.com"
```

**Application:**
```bash
# Application base URL (for OAuth callbacks)
APP_BASE_URL="https://your-app-domain.com"

# Optional: Debug mode (disable in production!)
DEBUG=false
```

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
   DATABASE_URL           # Auto-injected by Railway
   SUPER_ADMIN_EMAIL      # Your admin email
   GOOGLE_CLIENT_ID       # Google OAuth client ID
   GOOGLE_CLIENT_SECRET   # Google OAuth client secret
   SESSION_SECRET         # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
   APP_BASE_URL           # Railway provides this
   ```

4. **Configure Start Command:**
   ```bash
   # Railway detects Python app automatically
   # Start command: uvicorn src.parade_state.main:app --host 0.0.0.0 --port $PORT
   ```

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

### Other Platforms

**Docker Deployment:**

```dockerfile
# Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY README.md ./
COPY src/ ./src/

RUN pip install uv
RUN uv sync --frozen

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "src.parade_state.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
# Build and run
docker build -t parade-state .
docker run -p 8000:8000 --env-file .env parade-state
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
```bash
# PostgreSQL restore
psql -U user -h host -d dbname < backup_file.sql
```

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

### Database Backups

**Automated Backups:**
```bash
# Daily backup cron job
0 2 * * * pg_dump -U user -h host dbname > /backups/db_$(date +\%Y\%m\%d).sql
```

**Backup Retention:**
- Daily backups: Keep 7 days
- Weekly backups: Keep 4 weeks
- Monthly backups: Keep 12 months

### Disaster Recovery

**Recovery Plan:**

1. **Assess Damage:**
   - What data is lost?
   - What systems are affected?

2. **Restore from Backup:**
   ```bash
   # Restore most recent good backup
   psql -U user -h host dbname < backup_file.sql
   ```

3. **Run Migrations:**
   ```bash
   # Apply any migrations since backup
   uv run alembic upgrade head
   ```

4. **Verify System:**
   - Test application endpoints
   - Verify data integrity
   - Monitor logs for errors

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
