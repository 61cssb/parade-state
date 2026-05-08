# Tests Guide

This directory contains all tests for the Parade State application. Tests are organized by type and purpose to make them easier to find, run, and maintain.

## 📁 Test Organization

```
tests/
├── unit/              # Isolated unit tests for individual functions/modules
├── integration/       # API endpoint tests with full stack
├── behavioral/        # Domain logic and business rule tests
├── conftest.py        # Shared pytest fixtures and configuration
└── README.md          # This file
```

### 🧪 Unit Tests (`unit/`)

**Purpose:** Test individual functions, classes, and modules in isolation.

**Characteristics:**
- Fast execution (no network/database I/O when possible)
- Test specific functions with mocked dependencies
- Focus on code paths and edge cases
- No external service dependencies

**Example:** `test_utc_dt.py` - Tests datetime utility functions with various inputs.

**When to write unit tests:**
- Testing utility functions (e.g., `utils/` modules)
- Testing model methods and business logic
- Testing complex algorithms or calculations
- When you need to test edge cases and error conditions

### 🔗 Integration Tests (`integration/`)

**Purpose:** Test API endpoints with real database and application stack.

**Characteristics:**
- Test HTTP requests/responses
- Use test database (SQLite in-memory)
- Test authentication, authorization, permissions
- Test request validation and error handling
- Slower than unit tests but more realistic

**Example:** `test_attendance_api.py` - Tests the attendance API endpoints.

**When to write integration tests:**
- Testing API endpoints (CRUD operations)
- Testing database interactions
- Testing authentication and authorization flows
- Testing request/response schemas
- When you need to test the full request lifecycle

### 🧠 Behavioral Tests (`behavioral/`)

**Purpose:** Test domain logic, business rules, and system behaviors.

**Characteristics:**
- Test application behavior and business rules
- Test complex interactions between models
- Test data constraints and validations
- Test system state changes

**Example:** `test_access_control.py` - Tests access control logic and hierarchies.

**When to write behavioral tests:**
- Testing business rules and constraints
- Testing model relationships and constraints
- Testing complex domain logic
- Testing system state management
- When you need to verify system invariants

## 🚀 Running Tests

### Run All Tests
```bash
pytest
```

### Run Specific Test Types
```bash
# Only unit tests (fastest)
pytest tests/unit/

# Only integration tests
pytest tests/integration/

# Only behavioral tests
pytest tests/behavioral/
```

### Run Specific Test Files
```bash
pytest tests/unit/test_utc_dt.py
pytest tests/integration/test_attendance_api.py
```

### Run Specific Test Functions
```bash
pytest tests/unit/test_utc_dt.py::TestTimeRetrieval::test_utcnow_returns_timezone_aware
```

### Run with Coverage
```bash
# Generate coverage report
pytest --cov=src/parade_state --cov-report=html

# Open coverage report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Run with Verbose Output
```bash
pytest -v  # Show individual test names
pytest -vv  # Show more detailed output
```

### Run Only Failed Tests
```bash
pytest --lf  # "last failed" - rerun only failed tests
```

### Run Tests Matching Pattern
```bash
pytest -k "attendance"  # Run tests containing "attendance"
pytest -k "test_create"  # Run tests containing "test_create"
```

## 📝 Writing New Tests

### 1. Choose the Right Test Type

| Need | Test Type |
|------|-----------|
| Testing a utility function | Unit |
| Testing an API endpoint | Integration |
| Testing business logic | Behavioral |
| Testing database models | Behavioral |
| Testing authentication | Integration |

### 2. Unit Test Template

```python
"""Tests for [module_name]."""

import pytest
from parade_state.utils.module_name import function_name

class TestFunctionName:
    """Test function_name behavior."""

    def test_function_name_with_valid_input(self):
        """Test that function_name works with valid input."""
        # Arrange
        input_data = "test"

        # Act
        result = function_name(input_data)

        # Assert
        assert result == "expected"

    def test_function_name_with_invalid_input(self):
        """Test that function_name handles invalid input."""
        with pytest.raises(ValueError):
            function_name(invalid_input)
```

### 3. Integration Test Template

```python
"""Tests for [feature] API endpoints."""

import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_resource_as_admin(
    async_client: AsyncClient,
    admin_token_headers: dict[str, str],
    db_session,
):
    """Test resource creation by admin."""
    # Arrange
    resource_data = {"name": "Test Resource"}

    # Act
    response = await async_client.post(
        "/api/v1/resources/",
        json=resource_data,
        headers=admin_token_headers,
        params={"user_id": "admin-id", "user_role": "admin"},
    )

    # Assert
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Resource"
```

### 4. Behavioral Test Template

```python
"""Tests for [domain] behavior."""

import pytest
from sqlalchemy import select

from parade_state.models import ModelName

class TestDomainBehavior:
    """Test domain behavior and business rules."""

    @pytest.mark.asyncio
    async def test_business_rule_constraint(self, db_session):
        """Test that business rule is enforced."""
        # Arrange
        entity = ModelName(field="value")
        db_session.add(entity)

        # Act
        await db_session.commit()

        # Assert
        result = await db_session.execute(select(ModelName))
        assert result.scalar_one_or_none() is not None
```

## 🔧 Test Fixtures

### Available Fixtures (in `conftest.py`)

- `async_client` - HTTP client for API testing
- `db_session` - Database session for database operations
- `admin_token_headers` - Authentication headers for admin user
- `sample_deployment` - Sample deployment entity
- `sample_personnel` - Sample personnel entities
- `sample_users` - Sample user entities
- `sample_estab` - Sample establishment entity

### Creating Custom Fixtures

```python
# In conftest.py or your test file

@pytest.fixture
async def custom_resource(db_session):
    """Create a custom resource for testing."""
    resource = Resource(name="Test")
    db_session.add(resource)
    await db_session.commit()
    await db_session.refresh(resource)
    return resource
```

## 📏 Test Conventions

### Naming Conventions

- **Test files:** `test_<module_name>.py`
- **Test classes:** `Test<ClassName>` or `Test<FeatureName>`
- **Test functions:** `test_<what_is_being_tested>`

### Test Structure

Use the **Arrange-Act-Assert** pattern:

```python
def test_feature():
    # Arrange - Set up test data and conditions
    input_data = prepare_data()

    # Act - Execute the function/method being tested
    result = function_under_test(input_data)

    # Assert - Verify expected outcomes
    assert result == expected_value
```

### Test Descriptions

Write descriptive test names that explain **what** is being tested:

```python
✅ Good:
def test_create_attendance_record_with_deployment_notes_snapshot()
def test_user_cannot_delete_themselves()
def test_expired_session_returns_401()

❌ Bad:
def test_attendance()
def test_user()
def test_session()
```

### Async Tests

Always use `@pytest.mark.asyncio` for async test functions:

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result is not None
```

## 🎯 Coverage Requirements

### Minimum Coverage: 80%

The project requires 80% code coverage. Check coverage with:

```bash
pytest --cov=src/parade_state --cov-report=term-missing
```

### Coverage Goals by Component

| Component | Target Coverage |
|-----------|-----------------|
| Utils modules | 90%+ (isolated, easy to test) |
| API endpoints | 85%+ (critical paths) |
| Models | 80%+ (business logic) |
| Middleware | 75%+ (harder to test) |

## 🐛 Debugging Failed Tests

### Run with Detailed Output

```bash
pytest --showlocals  # Show local variables on failure
pytest -l  # Same as --showlocals
pytest --tb=long  # Show full tracebacks
pytest --tb=short  # Show shorter tracebacks
```

### Drop into Debugger

```python
def test_failing_function():
    result = function_under_test()
    breakpoint()  # Python 3.7+
    assert result == expected
```

### Run with PDB on Failure

```bash
pytest --pdb  # Drop into debugger on failure
pytest --trace  # Start debugger immediately
```

## ⚡ Performance Tips

### 1. Use Fixtures Effectively

Fixtures are cached and reused, making tests faster:

```python
@pytest.fixture(scope="session")  # Created once per test session
def expensive_resource():
    return create_expensive_resource()

@pytest.fixture(scope="function")  # Default: created for each test
def fresh_resource():
    return create_fresh_resource()
```

### 2. Run Only What You Need

```bash
# During development, run only relevant tests
pytest tests/unit/test_utc_dt.py  # Specific file
pytest -k "test_create"  # Specific pattern
pytest tests/unit/  # Only fast unit tests
```

### 3. Use Mocks for External Services

```python
from unittest.mock import patch

def test_with_external_service():
    with patch('parade_state.external_api.call') as mock_call:
        mock_call.return_value = {"status": "ok"}
        result = function_using_external_api()
        assert result is True
```

## 📚 Additional Resources

### pytest Documentation
- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio documentation](https://pytest-asyncio.readthedocs.io/)
- [pytest-cov documentation](https://pytest-cov.readthedocs.io/)

### Project-Specific Documentation
- [CLAUDE.md](../CLAUDE.md) - Development patterns and conventions
- [ARCHITECTURE.md](../docs/ARCHITECTURE.md) - System architecture and design

### Testing Best Practices
- Write tests **before** or **alongside** code (TDD)
- Keep tests **independent** (no test dependencies)
- Make tests **deterministic** (same results every time)
- Use **descriptive names** that explain what is being tested
- Test **edge cases** and **error conditions**, not just happy paths

## 🤝 Contributing Tests

When contributing new tests:

1. **Choose the right test type** (unit/integration/behavioral)
2. **Use existing fixtures** from `conftest.py` when possible
3. **Follow naming conventions** and code style
4. **Add descriptive docstrings** to explain what is being tested
5. **Ensure all tests pass** before submitting
6. **Maintain coverage** above 80%

### Test Review Checklist

- [ ] Tests are in the correct directory (unit/integration/behavioral)
- [ ] Test names are descriptive and follow conventions
- [ ] Tests use Arrange-Act-Assert pattern
- [ ] Tests are independent (can run in any order)
- [ ] Tests have descriptive docstrings
- [ ] Edge cases are covered
- [ ] Code coverage is maintained above 80%

---

**Quick Reference:**

| Need | Command |
|------|---------|
| Run all tests | `pytest` |
| Run only unit tests | `pytest tests/unit/` |
| Run with coverage | `pytest --cov` |
| Run failed tests | `pytest --lf` |
| Run with verbose output | `pytest -v` |
| Debug with pdb | `pytest --pdb` |
| Run specific test | `pytest tests/path/to/test.py::test_function` |

For questions or help with testing, please refer to the project documentation or ask the team.
