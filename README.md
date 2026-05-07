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

### Setup

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run development server
uv run uvicorn src.parade_state.main:app --reload
```

## License

[License information]