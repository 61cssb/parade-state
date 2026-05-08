# Parade State Management System

A web application for managing battalion parade state through structured attendance tracking and access-controlled data management.

## Features

- CSV-based personnel establishment management
- Deployment-based personnel remapping and overrides
- Session-based attendance tracking (AM/PM)
- Role-based access control with subunit scoping
- Mobile-friendly attendance interface
- Audit trail for all changes

## Development

This project uses:
- Python 3.12+
- FastAPI for the API
- SQLAlchemy for data persistence
- NiceGUI for admin interface
- PostgreSQL for production database
- SQLite for testing (in-memory)

### Setup

```bash
# Install dependencies
uv sync

# Run tests (with coverage)
uv run pytest

# Run tests with detailed output
uv run pytest -v

# Run development server
uv run uvicorn src.parade_state.main:app --reload
```

### Testing

- **Coverage:** 93.77% (target: 80%+)
- **Test Framework:** pytest with async support
- **Database:** In-memory SQLite for complete test isolation
- **Static Analysis:** ruff (replacing mypy for better performance)
- **Test Isolation:** Fresh database per test ensures reproducible results

### Documentation

**Comprehensive Documentation:**
- [SPECIFICATION.md](docs/SPECIFICATION.md) - Complete technical specification with data models, business rules, and technical decisions
- [IMPLEMENTATION.md](docs/IMPLEMENTATION.md) - Technical implementation guide with setup, testing, and deployment details
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - System architecture overview with components and data flow

**Original Documents (referenced in specification):**
- [PRD v0.4](docs/parade-state-prd-v04.md) - Original Product Requirements Document
- [Schema Notes](docs/SCHEMA_NOTES.md) - Database design notes and implementation patterns

## License

[License information]