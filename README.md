# Parade State Management System

A web application for managing battalion parade state through structured attendance tracking and access-controlled data management.

## Features

- CSV-based personnel establishment management
- Deployment-based personnel remapping and overrides
- Session-based attendance tracking (AM/PM)
- Role-based access control with subunit scoping
- Mobile-friendly attendance interface
- Audit trail for all changes

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

This project uses:
- Python 3.12+
- FastAPI for the API
- SQLAlchemy for data persistence
- NiceGUI for admin interface
- PostgreSQL for production database
- SQLite for testing

### Development Commands

```bash
# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/parade_state --cov-report=html

# Run tests with detailed output
uv run pytest -v

# Check code style
uv run ruff check src/ tests/

# Format code
uv run ruff format src/ tests/

# Run development server
uv run uvicorn src.parade_state.main:app --reload
```

### Testing

- **Coverage:** 93.77% (target: 80%+)
- **Test Framework:** pytest with async support
- **Database:** File-based SQLite for proper test isolation
- **Static Analysis:** ruff (replacing mypy for better performance)
- **Test Isolation:** Fresh database per test ensures reproducible results

## Documentation

### 🚀 Quick Start for Developers

**New to this project? Start here:**

1. **📖 Read [CLAUDE.md](CLAUDE.md)** - Critical development patterns (what you need every session)
2. **📋 Review [docs/CODE_STYLE.md](docs/CODE_STYLE.md)** - Utility patterns and import conventions
3. **🏗️ Check [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System design and data flow
4. **✅ Write tests** - See [docs/TESTING.md](docs/TESTING.md) for testing patterns

### 📚 Complete Documentation

**Essential Guides:**
- **[CLAUDE.md](CLAUDE.md)** - 🔥 Development patterns (READ FIRST)
- **[docs/CODE_STYLE.md](docs/CODE_STYLE.md)** - Coding standards and utility patterns
- **[docs/TESTING.md](docs/TESTING.md)** - Testing strategies and approaches
- **[docs/SECURITY.md](docs/SECURITY.md)** - Security patterns and access control

**Technical Documentation:**
- **[docs/SPECIFICATION.md](docs/SPECIFICATION.md)** - Complete technical specification with data models and business rules
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System architecture and design decisions
- **[docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md)** - Technical implementation guide with setup and deployment details
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** - Production deployment guides (Railway, Docker)

**Planning & History:**
- **[docs/NEXT_PHASE.md](docs/NEXT_PHASE.md)** - Roadmap and implementation status
- **[tests/README.md](tests/README.md)** - Testing organization and patterns

**Historical Documents:**
- **[docs/parade-state-prd-v04.md](docs/parade-state-prd-v04.md)** - Original Product Requirements Document
- **[docs/SCHEMA_NOTES.md](docs/SCHEMA_NOTES.md)** - Database design notes and implementation patterns

## API Documentation

Once running, visit:
- `http://localhost:8000/docs` - Interactive API documentation (Swagger UI)
- `http://localhost:8000/redoc` - Alternative API documentation (ReDoc)

## Current System Status

✅ **Production-Ready** with admin + user-facing interface:
- 292 tests passing (100% pass rate)
- 57 API endpoints fully implemented and tested
- **Admin interface with Jinja2 templates** (9 pages: dashboard, estabs, deployments, deferments, users, CSV upload, settings, audit, plus deployment-personnel sub-view)
- **User-facing views** (deployment summary, attendance marking, estab browser)
- **Host-independent Google OAuth** (works with any domain)
- Multi-tenant deployment access control
- Comprehensive audit trails
- **Current Phase:** Frontend development — mobile optimization next

## Health Check

```bash
curl http://localhost:8000/health
```

## License

[License information]
