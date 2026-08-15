# Performance Guide

**Purpose:** Guidelines and best practices for optimizing the performance of the Parade State application.

**Audience:** Developers working on performance optimization and scalability.

## Table of Contents

- [Database Performance](#database-performance)
- [Memory Management](#memory-management)
- [API Performance](#api-performance)
- [Monitoring and Profiling](#monitoring-and-profiling)

---

## Database Performance

### Query Optimization

**Use `select()` instead of `all()` for large datasets:**

❌ **Don't load entire tables into memory:**
```python
# BAD: Loads all personnel into memory
all_personnel = await db.execute(select(Personnel))
personnel_list = all_personnel.scalars().all()
```

✅ **Do use pagination and targeted queries:**
```python
# GOOD: Paginated query
result = await db.execute(
    select(Personnel)
    .limit(100)
    .offset(0)
)
personnel_list = result.scalars().all()
```

### Avoid N+1 Queries

**Problem:** Making separate database queries for related data in a loop.

❌ **Don't query in loops:**
```python
# BAD: N+1 query problem
sessions = await db.execute(select(Session).where(Session.grouping_id == grouping_id))
for session in sessions.scalars():
    # Separate query for each session
    attendance = await db.execute(
        select(AttendanceRecord).where(AttendanceRecord.session_id == session.id)
    )
```

✅ **Do use joins or select_in loading:**
```python
# GOOD: Single query with join
result = await db.execute(
    select(Session, AttendanceRecord)
    .join(AttendanceRecord, AttendanceRecord.session_id == Session.id)
    .where(Session.grouping_id == grouping_id)
)
```

### Database Indexes

**Add indexes for frequently queried columns:**

```python
class Personnel(Base):
    """Individual personnel record."""
    __tablename__ = "personnel"

    # Indexed for fast lookups
    pers_no: Mapped[str | None] = mapped_column(String(20), index=True)  # cross-roll person identity
    rank: Mapped[str] = mapped_column(String(50), index=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    unit: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", name="personnel_status"),
        default="active",
        index=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
```

**Guidelines for indexes:**
- ✅ Index columns used in WHERE clauses
- ✅ Index columns used in JOIN conditions
- ✅ Index columns used for sorting (ORDER BY)
- ✅ Index foreign key columns
- ⚠️ Don't over-index (indexes slow down writes)
- ⚠️ Consider composite indexes for multi-column queries

### Bulk Operations

**Use bulk operations for multiple inserts/updates:**

❌ **Don't insert records one at a time:**
```python
# BAD: N separate INSERT statements
for attendance_data in attendance_list:
    record = AttendanceRecord(**attendance_data)
    db.add(record)
    await db.commit()  # Commits N times
```

✅ **Do use bulk operations:**
```python
# GOOD: Single bulk INSERT
db.add_all([
    AttendanceRecord(**data)
    for data in attendance_list
])
await db.commit()  # Single commit
```

---

## Memory Management

### Use Generators for Large Datasets

**Problem:** Loading large datasets into memory causes high memory usage.

❌ **Don't return full lists:**
```python
# BAD: Loads entire result set into memory
async def get_all_personnel(db: AsyncSession):
    result = await db.execute(select(Personnel))
    return result.scalars().all()  # All records in memory
```

✅ **Do use generators or pagination:**
```python
# GOOD: Returns results as needed
async def stream_personnel(db: AsyncSession, batch_size: int = 100):
    offset = 0
    while True:
        result = await db.execute(
            select(Personnel)
            .limit(batch_size)
            .offset(offset)
        )
        batch = result.scalars().all()
        if not batch:
            break
        yield batch
        offset += batch_size
```

### Connection Pooling

**Configure connection pooling for efficient database connections:**

```python
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    database_url,
    pool_size=5,  # Number of connections to maintain
    max_overflow=10,  # Additional connections under load
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,  # Recycle connections after 1 hour
)
```

### Session Management

**Always close database sessions properly:**

```python
async with get_db_session() as db:
    # Database operations here
    result = await db.execute(query)
# Session automatically closed when exiting context
```

---

## API Performance

### Pagination

**Always paginate list endpoints:**

```python
@router.get("/personnel")
async def list_personnel(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
):
    # Enforce maximum page size
    limit = min(limit, 1000)

    result = await db.execute(
        select(Personnel)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()
```

### Selective Field Loading

**Only load fields that are needed:**

❌ **Don't load all columns if you only need a few:**
```python
# BAD: Loads entire row
result = await db.execute(select(Personnel))
```

✅ **Do specify required columns:**
```python
# GOOD: Loads only specified columns
result = await db.execute(
    select(Personnel.id, Personnel.full_name, Personnel.rank)
)
```

### Caching Strategies

**Cache frequently accessed, rarely changed data:**

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def get_access_level(role: str) -> AccessLevel:
    """Cache access level lookups."""
    return _get_access_level_from_db(role)

# For grouping status (changes infrequently)
@lru_cache(maxsize=32)
def get_grouping_status(grouping_id: str) -> str:
    """Cache grouping status lookups."""
    return _get_status_from_db(grouping_id)
```

---

## Monitoring and Profiling

### Query Performance Monitoring

**Enable SQL query logging in development:**

```python
# src/parade_state/db/__init__.py
_engine = create_async_engine(
    database_url,
    echo=True,  # Log all SQL queries (development only)
    pool_pre_ping=True,
)
```

### Performance Testing

**Add performance tests for critical operations:**

```python
@pytest.mark.asyncio
async def test_personnel_list_performance(client, admin_token_headers):
    """Test that personnel list performs acceptably."""
    import time

    start = time.time()
    response = client.get(
        "/api/v1/personnel",
        headers=admin_token_headers,
        params={"limit": 100}
    )
    duration = time.time() - start

    assert response.status_code == 200
    assert duration < 1.0  # Should complete in under 1 second
```

### Database Query Analysis

**Use EXPLAIN QUERY PLAN to analyze slow queries:**

```python
# For PostgreSQL
result = await db.execute(
    text("EXPLAIN ANALYZE SELECT * FROM personnel WHERE unit = :unit"),
    {"unit": "Alpha"}
)

# For SQLite
result = await db.execute(
    text("EXPLAIN QUERY PLAN SELECT * FROM personnel WHERE unit = :unit"),
    {"unit": "Alpha"}
)
```

---

## Performance Checklist

### Before Deploying New Features

- [ ] Database queries use indexes
- [ ] List endpoints are paginated
- [ ] No N+1 query problems
- [ ] Bulk operations used for multiple inserts/updates
- [ ] Generators used for large datasets
- [ ] Connection pooling configured
- [ ] Frequently accessed data cached
- [ ] Performance tests added for critical paths
- [ ] SQL query logging disabled in production

### Performance Optimization Workflow

1. **Measure:** Profile to identify bottlenecks
2. **Analyze:** Review query plans and execution times
3. **Optimize:** Apply appropriate optimization techniques
4. **Verify:** Measure performance improvement
5. **Document:** Add comments explaining optimization rationale

---

**Contributing:** When adding performance optimizations, update this document to share knowledge with the team.

**See Also:** [ARCHITECTURE.md](ARCHITECTURE.md) for system architecture and design decisions.
