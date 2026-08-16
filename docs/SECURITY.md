# Security Guide

**Purpose:** Security patterns, best practices, and guidelines for the Parade State application.

**Audience:** Developers implementing security features and reviewing code for security issues.

## Table of Contents

- [Input Validation](#input-validation)
- [Access Control](#access-control)
- [Authentication](#authentication)
- [Data Protection](#data-protection)
- [API Security](#api-security)
- [Common Vulnerabilities](#common-vulnerabilities)

---

## Input Validation

### Always Validate User Input

**Use Pydantic models for request validation:**

✅ **Do validate with Pydantic:**
```python
from pydantic import BaseModel, Field

class PersonnelUpdate(BaseModel):
    """Schema for updating personnel."""

    rank: str | None = Field(None, min_length=1, max_length=50)
    name: str | None = Field(None, min_length=1, max_length=255)
    status: str | None = Field(
        None,
        pattern="^(active|archived)$",
    )
```

❌ **Don't trust user input:**
```python
# BAD: No validation
async def update_personnel(personnel_id: str, data: dict):
    personnel.rank = data["rank"]  # Could be malicious input
```

### Sanitize Data Before Database Operations

**Use parameterized queries to prevent SQL injection:**

✅ **Do use SQLAlchemy's parameterized queries:**
```python
# SAFE: SQLAlchemy handles parameterization
result = await db.execute(
    select(Personnel).where(Personnel.id == personnel_id)
)
```

❌ **Don't concatenate strings:**
```python
# DANGEROUS: SQL injection vulnerability
query = f"SELECT * FROM personnel WHERE id = '{user_input}'"
result = await db.execute(query)
```

### Validate IDs and Formats

**Validate UUIDs and other ID formats:**

✅ **Do validate UUIDs:**
```python
from parade_state.utils import ids

async def get_personnel(personnel_id: str):
    """Validate UUID format before querying."""
    try:
        uuid.UUID(personnel_id)  # Validate format
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid personnel ID format"
        )

    result = await db.execute(
        select(Personnel).where(Personnel.id == personnel_id)
    )
    return result.scalar_one_or_none()
```

✅ **Do use validation utilities:**
```python
from parade_state.utils import ids

# Validate UUID format
if not ids.is_valid(personnel_id):
    raise HTTPException(status_code=400, detail="Invalid ID format")
```

---

## Access Control

### Use Dependency Injection for Authorization

**Check permissions at the endpoint level:**

✅ **Do use dependency injection:**
```python
from fastapi import Depends

async def update_personnel(
    personnel_id: str,
    user_id: str = Query(...),
    user_role: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
):
    # Check permissions
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=403,
            detail="Only admins can update personnel"
        )
    # Proceed with update
```

### Implement Role-Based Access Control

**Define clear roles and permissions:**

```python
# User roles
class UserRole:
    SUPER_ADMIN = "super_admin"  # Full system access
    ADMIN = "admin"              # Can manage groupings and users
    USER = "user"                # Can record attendance

# Permission checks
def can_update_personnel(user_role: str) -> bool:
    """Check if user can update personnel records."""
    return user_role in ["admin", "super_admin"]

def can_delete_grouping(user_role: str) -> bool:
    """Check if user can delete groupings."""
    return user_role == "super_admin"
```

### Grouping-Based Access Control

**Ensure users can only access groupings they're assigned to:**

```python
async def verify_grouping_access(
    grouping_id: str,
    user_id: str,
    user_role: str,
    db: AsyncSession,
) -> Grouping:
    """Verify user has access to grouping and return it."""

    # Super admins have full access to all groupings
    if user_role == "super_admin":
        result = await db.execute(select(Grouping).where(grouping_id == grouping_id))
        grouping = result.scalar_one_or_none()
        if not grouping:
            raise HTTPException(status_code=404, detail="Grouping not found")
        return grouping

    # Check for explicit grouping access
    access_result = await db.execute(
        select(GroupingUserAccess).where(
            and_(
                GroupingUserAccess.user_id == user_id,
                GroupingUserAccess.grouping_id == grouping_id,
                GroupingUserAccess.revoked_at.is_(None),
            )
        )
    )
    access = access_result.scalar_one_or_none()

    # Both admins and regular users need explicit grouping access
    if access:
        result = await db.execute(select(Grouping).where(grouping_id == grouping_id))
        return result.scalar_one_or_none()

    # No access found
    raise HTTPException(
        status_code=403,
        detail="Insufficient permissions to access this grouping"
    )
```

### Multi-Tenant Security Patterns

**Data Isolation Strategy:**

1. **Automatic Filtering:** All data queries automatically filter by grouping access
2. **Explicit Grants:** Users must be explicitly granted access to groupings
3. **Scope Enforcement:** Subunit-level filtering within groupings
4. **Audit Trail:** All access grants and revocations are logged

**Implementation Across APIs:**

✅ **Personnel API:**
```python
# Filter personnel by grouping access
async def list_personnel(grouping_id: str, user_id: str, user_role: str):
    # Verify grouping access first
    grouping = await verify_grouping_access(grouping_id, user_id, user_role, db)
    
    # Return personnel only from accessible grouping
    return await get_personnel_for_grouping(grouping.id)
```

✅ **Sessions API:**
```python
# Create sessions only for accessible groupings
async def create_session(session_data: SessionCreate, user_id: str, user_role: str):
    # Verify grouping access
    grouping = await verify_grouping_access(
        session_data.grouping_id, user_id, user_role, db
    )
    
    # Create session with access verified
    return await create_session_for_grouping(session_data, grouping)
```

✅ **Attendance API:**
```python
# Record attendance only for accessible groupings
async def create_attendance(attendance_data: AttendanceCreate, user_id: str, user_role: str):
    # Verify session and grouping access
    session = await verify_session_and_grouping_access(
        attendance_data.session_id, user_id, user_role, db
    )
    
    # Record attendance with access verified
    return await record_attendance(attendance_data, session)
```

### Subunit Scope Filtering

**Hierarchical access control within groupings:**

```python
async def check_subunit_access(
    user_id: str,
    grouping_id: str,
    unit: str | None = None,
    sub_unit_1: str | None = None,
    sub_unit_2: str | None = None,
    sub_unit_3: str | None = None,
    db: AsyncSession,
) -> bool:
    """Check if user has access to specific subunit within grouping."""

    # Get user's subunit scopes for this grouping
    scopes = await get_user_subunit_scopes(user_id, grouping_id, db)

    # If no scopes defined, user has grouping-wide access
    if not scopes:
        return True

    # Check if any scope matches the requested unit hierarchy
    for scope in scopes:
        # If scope has no restrictions, grant access
        if not any([scope.unit, scope.sub_unit_1, scope.sub_unit_2, scope.sub_unit_3]):
            return True

        # Check hierarchical unit matching
        if scope.unit and unit != scope.unit:
            continue
        if scope.sub_unit_1 and sub_unit_1 != scope.sub_unit_1:
            continue
        if scope.sub_unit_2 and sub_unit_2 != scope.sub_unit_2:
            continue
        if scope.sub_unit_3 and sub_unit_3 != scope.sub_unit_3:
            continue

        # All checks passed
        return True

    # No matching scope found
    return False
```

### Access Control Best Practices

**✅ DO:**
- Verify grouping access at the beginning of each endpoint
- Filter all data queries by grouping scope
- Use dependency injection for authentication
- Implement audit trails for access changes
- Test access control with multiple user roles
- Use HTTP 403 Forbidden for access denied
- Include user context in audit logs

**❌ DON'T:**
- Skip access checks for "read-only" operations
- Assume super admins don't need validation
- Filter data after retrieval (filter at query level)
- Use hardcoded user IDs in production
- Ignore subunit scope restrictions
- Return 404 for access denied (use 403)
- Forget to log access control decisions

    return await get_grouping(grouping_id, db)
```

### Row-Level Security

**Implement row-level security where appropriate:**

```python
async def get_user_notes(
    user_id: str,
    current_user_role: str,
    db: AsyncSession,
):
    """Only show notes appropriate to user's role."""

    query = select(Note)

    # Regular users only see their own notes
    if current_user_role == "user":
        query = query.where(Note.user_id == user_id)

    # Admins can see all notes
    elif current_user_role in ["admin", "super_admin"]:
        pass  # No filtering

    result = await db.execute(query)
    return result.scalars().all()
```

---

## Authentication

### Secure Session Management

**Use secure, HTTP-only cookies:**

```python
# Set secure cookie flags
response.set_cookie(
    key="session_token",
    value=session_token,
    httponly=True,      # Prevent JavaScript access
    secure=True,        # Only send over HTTPS
    samesite="lax",     # CSRF protection
)
```

Enforced centrally by `parade_state.utils.cookies`: the `Secure` flag is
env-driven (`AUTH_COOKIE_SECURE`) and defaults to on in production. There
are no fallback session secrets — production refuses to boot without a
real `SESSION_SECRET` (see `Settings.validate()` in `config.py`).

### Session Expiration

**Implement appropriate session timeouts:**

```python
# Set session expiration
SESSION_EXPIRY_MINUTES = 60 * 8  # 8 hours

def is_session_expired(session: UserSession) -> bool:
    """Check if session has expired."""
    from parade_state.utils import utc_dt

    if utc_dt.is_expired(session.expires_at):
        return True
    return False
```

### OAuth Security

**Follow OAuth security best practices:**

- ✅ Validate state parameter to prevent CSRF
- ✅ Use PKCE (Proof Key for Code Exchange)
- ✅ Validate redirect URIs
- ✅ Store tokens securely
- ✅ Implement token revocation

---

## Data Protection

### Sensitive Data Handling

**Never log sensitive information:**

```python
# BAD: Logs sensitive data
logger.info(f"User login: {email}, password: {password}")

# GOOD: Logs only necessary information
logger.info(f"User login attempt: {email}")
```

### Password Security

**Never store passwords in plain text:**

- ✅ Use strong password hashing (bcrypt, argon2)
- ✅ Implement password complexity requirements
- ✅ Use secure password reset flows
- ❌ Never store passwords in plain text
- ❌ Never log passwords

### API Keys and Secrets

**Never expose API keys in code:**

```python
# BAD: Hardcoded secrets
GOOGLE_CLIENT_SECRET = "abc123"

# GOOD: Environment variables
from parade_state.utils import env

GOOGLE_CLIENT_SECRET = env.get_required("GOOGLE_CLIENT_SECRET")
```

---

## API Security

### Rate Limiting

**Implement rate limiting to prevent abuse:**

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/v1/attendance")
@limiter.limit("10/minute")
async def record_attendance():
    """Limit to 10 requests per minute."""
    pass
```

### CORS Configuration

**Configure CORS properly:**

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://trusted-domain.com"],  # Specific origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
```

The application wires `Settings.ALLOWED_ORIGINS` (env `ALLOWED_ORIGINS`)
into `CORSMiddleware`. Production must list explicit origins — `*` is
rejected at startup because wildcard origins combined with
`allow_credentials=True` let any site make credentialed requests.

### Error Messages

**Don't expose sensitive information in error messages:**

❌ **Don't reveal internal details:**
```python
# BAD: Exposes database structure
raise HTTPException(
    status_code=500,
    detail="Database connection failed: connection to localhost:5432 refused"
)
```

✅ **Do use generic error messages:**
```python
# GOOD: Generic error message
raise HTTPException(
    status_code=500,
    detail="An error occurred. Please try again later."
)
```

---

## Common Vulnerabilities

### SQL Injection

**Prevention:** Use parameterized queries (SQLAlchemy handles this)

### XSS (Cross-Site Scripting)

**Prevention:** Validate and sanitize user input, use FastAPI's automatic HTML escaping

### CSRF (Cross-Site Request Forgery)

**Prevention:** Use SameSite cookies, validate CSRF tokens for state-changing operations

### Authentication Bypass

**Prevention:**
- Always check permissions on protected endpoints
- Use secure session management
- Implement proper logout functionality

### Data Exposure

**Prevention:**
- Never log sensitive data
- Use HTTPS in production
- Validate file uploads
- Implement proper access controls

---

## Security Checklist

### Before Deploying Code

- [ ] All user input is validated with Pydantic models
- [ ] Database queries use parameterized queries
- [ ] UUIDs and IDs are validated before use
- [ ] Permission checks on all protected endpoints
- [ ] Sensitive data is not logged
- [ ] API keys and secrets use environment variables
- [ ] Session management is secure (HTTP-only, secure flags)
- [ ] Error messages don't expose internal details
- [ ] CORS is properly configured
- [ ] Rate limiting is implemented where appropriate

### Code Review Security Checklist

- [ ] User input validation
- [ ] SQL injection prevention
- [ ] XSS prevention
- [ ] CSRF protection
- [ ] Authentication and authorization
- [ ] Sensitive data handling
- [ ] Error handling
- [ ] Logging security
- [ ] Dependencies are up to date

---

## Incident Response

### If You Discover a Security Vulnerability

1. **Do not commit security fixes to public repositories**
2. **Report to security team immediately**
3. **Follow responsible disclosure practices**
4. **Document the vulnerability and fix**
5. **Test thoroughly before deploying**
6. **Monitor for exploitation attempts**

---

**Contributing:** When adding security features or discovering vulnerabilities, update this document.

**See Also:** [ARCHITECTURE.md](ARCHITECTURE.md) for system architecture and [TESTING.md](TESTING.md) for security testing practices.
