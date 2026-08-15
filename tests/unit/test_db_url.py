"""Unit tests for database URL normalization (sync → async driver schemes)."""

from parade_state.db import normalize_database_url


class TestSchemeTranslation:
    """Platform-injected sync Postgres URLs become asyncpg URLs."""

    def test_postgresql_scheme_gets_asyncpg_driver(self):
        assert (
            normalize_database_url("postgresql://user:pass@host:5432/db")
            == "postgresql+asyncpg://user:pass@host:5432/db"
        )

    def test_postgres_scheme_gets_asyncpg_driver(self):
        # Heroku-style spelling
        assert (
            normalize_database_url("postgres://user:pass@host:5432/db")
            == "postgresql+asyncpg://user:pass@host:5432/db"
        )

    def test_uppercase_scheme_normalized(self):
        assert (
            normalize_database_url("PostgreSQL://user:pass@host:5432/db")
            == "postgresql+asyncpg://user:pass@host:5432/db"
        )

    def test_already_async_url_unchanged(self):
        url = "postgresql+asyncpg://user:pass@host:5432/db"
        assert normalize_database_url(url) == url


class TestOtherSchemesPassThrough:
    """Non-Postgres URLs (and empty schemes) are returned untouched."""

    def test_sqlite_memory_unchanged(self):
        assert (
            normalize_database_url("sqlite+aiosqlite:///:memory:")
            == "sqlite+aiosqlite:///:memory:"
        )

    def test_sqlite_file_path_unchanged(self):
        assert (
            normalize_database_url("sqlite+aiosqlite:////tmp/test.db")
            == "sqlite+aiosqlite:////tmp/test.db"
        )


class TestQueryParameters:
    """Query params survive normalization; sslmode is translated to ssl."""

    def test_sslmode_translated_to_ssl(self):
        # Railway's public connection URL uses libpq's sslmode spelling
        assert (
            normalize_database_url(
                "postgresql://user:pass@host:5432/db?sslmode=require"
            )
            == "postgresql+asyncpg://user:pass@host:5432/db?ssl=require"
        )

    def test_sslmode_translated_even_on_async_url(self):
        # Someone hand-wrote the async scheme but kept the libpq spelling
        assert (
            normalize_database_url(
                "postgresql+asyncpg://user:pass@host:5432/db?sslmode=require"
            )
            == "postgresql+asyncpg://user:pass@host:5432/db?ssl=require"
        )

    def test_unrelated_query_params_preserved(self):
        url = (
            "postgresql://user:pass@host:5432/db?connect_timeout=10&application_name=ps"
        )
        assert (
            normalize_database_url(url)
            == "postgresql+asyncpg://user:pass@host:5432/db?connect_timeout=10&application_name=ps"
        )

    def test_no_query_string_stays_clean(self):
        assert (
            normalize_database_url("postgresql://host/db")
            == "postgresql+asyncpg://host/db"
        )


class TestUrlComponentsPreserved:
    """Credentials, hosts, ports and database names round-trip intact."""

    def test_password_with_reserved_characters_survives(self):
        # %40 is an encoded '@' inside the password
        assert (
            normalize_database_url("postgresql://user:p%40ss@host:5432/db")
            == "postgresql+asyncpg://user:p%40ss@host:5432/db"
        )

    def test_ipv6_host_and_custom_port_preserved(self):
        url = "postgresql://user:pass@[::1]:6543/prod"
        assert (
            normalize_database_url(url)
            == "postgresql+asyncpg://user:pass@[::1]:6543/prod"
        )
