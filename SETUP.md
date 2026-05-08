# Parade State Management System

## Quick Start

```bash
# Install dependencies
uv sync

# Copy environment template
cp env.example .env

# Edit .env with your configuration
# - Set DATABASE_URL
# - Configure Google OAuth
# - Set SUPER_ADMIN_EMAIL

# Run database setup (when Alembic is implemented)
# uv run alembic upgrade head

# Start development server
uv run uvicorn src.parade_state.main:app --reload
```

## Configuration

The application requires several environment variables (see `env.example`):

### Required for Development:
- `DATABASE_URL` - PostgreSQL connection string or SQLite for testing
- `GOOGLE_CLIENT_ID` - Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` - Google OAuth client secret
- `SESSION_SECRET` - Random string for session encryption
- `SUPER_ADMIN_EMAIL` - Email for super admin bootstrap

### Optional:
- `APP_BASE_URL` - Application URL (default: http://localhost:8000)
- `DEBUG` - Enable debug mode (default: false)
- `ALLOWED_ORIGINS` - CORS allowed origins (default: *)

## Development

```bash
# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/parade_state --cov-report=html

# Check code style
uv run ruff check src/ tests/

# Format code
uv run ruff format src/ tests/
```

## API Documentation

Once running, visit:
- `http://localhost:8000/docs` - Interactive API documentation (Swagger UI)
- `http://localhost:8000/redoc` - Alternative API documentation (ReDoc)

## Health Check

```bash
curl http://localhost:8000/health
```

## License

[License information]
