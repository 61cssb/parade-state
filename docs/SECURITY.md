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
    ADMIN = "admin"              # Can manage users and most data
    USER = "user"                # Can record attendance

# Permission checks
def can_update_personnel(user_role: str) -> bool:
    """Check if user can update personnel records."""
    return user_role in ["admin", "super_admin"]

def can_delete_grouping(user_role: str) -> bool:
    """Check if user can delete groupings."""
    return user_role == "super_admin"
```

### Grouping Access Control (issue 26 redesign)

The old per-grouping access model (GroupingUserAccess grants +
UserSubunitScope scoping) was **removed**. The redesigned groupings carry
no access scoping at all:

- **Mutations are super-admin only** (403 otherwise), enforced
  server-side on every grouping API route — not just hidden buttons
- **Reads are open to every authenticated role** (page and API)
- **Reachability follows the active nominal roll**: groupings on the roll
  currently active for attendance are listable/readable; groupings on
  non-active rolls are retained in the database but unreachable (404)
  until their roll is re-activated
- The whole feature sits behind the `FEATURE_GROUPING` flag (default
  off): flag-off means 404 for every role, super-admins included

```python
def _require_super_admin(user_role: str) -> None:
    """Authorize super_admin only (grouping mutations)."""
    if user_role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can manage groupings",
        )
```

### NR-Scoped Write Access

**Data Isolation Strategy:**

1. **Explicit Grants:** admins receive `UserSubunitAssignment` rows per
   nominal roll (one effective `sub_unit_1` each)
2. **Deny-by-default:** no assignments means no write access (403);
   super_admin bypasses
3. **Audit Trail:** grants and revocations are logged

**Implementation:**

```python
# Attendance/personnel writes gated per NR by sub_unit_1 assignment
async def list_writable_personnel(user_id: str, nominal_roll_id: str):
    subunits = await get_assigned_subunit_1s(user_id, nominal_roll_id)
    return await query_personnel_in_subunits(nominal_roll_id, subunits)
```

### Subunit Scope Filtering

**Per-nominal-roll access control:**

```python
async def check_subunit_access(
    user_id: str,
    nominal_roll_id: str,
    effective_sub_unit_1: str,
    db: AsyncSession,
) -> bool:
    """Check the user may write rows with this effective sub_unit_1 on this roll."""

    # Deny-by-default: no assignments means no write access
    assignments = await get_user_subunit_assignments(
        user_id, nominal_roll_id, db
    )
    return any(
        a.sub_unit_1 == effective_sub_unit_1 for a in assignments
    )
```

### Access Control Best Practices

**✅ DO:**
- Verify role/scope at the beginning of each endpoint
- Filter data queries by scope at query level
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
