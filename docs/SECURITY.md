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

**Identity is always session-derived (issue 31).** Resolve the caller with
the shared dependencies from `auth/dependencies.py` — never from query
parameters, request bodies, or anything the client sends:

✅ **Do use the session dependencies:**
```python
from parade_state.auth.dependencies import require_admin_user

async def update_personnel(
    personnel_id: str,
    user: User = Depends(require_admin_user),  # or require_super_admin_user
    db: AsyncSession = Depends(get_db_session),
):
    # user.id / user.role come from the validated session; unauthenticated
    # callers already got 401, under-tier callers 403.
    # Proceed with update, stamping provenance from str(user.id)
```

❌ **Never accept identity from the client** (the pre-#31 pattern — this
is the spoofing hole issue 31 closed):
```python
async def update_personnel(
    personnel_id: str,
    user_id: str = Query(...),    # anyone can send ?user_id=<super-admin>
    user_role: str = Query(...),  # ...and ?user_role=super_admin
    ...
):
```

A structural test (`tests/integration/test_no_client_identity.py`) fails
the suite if any `/api/v1` route reintroduces identity params, alongside
a spoof regression suite asserting the params have no effect.

### Implement Role-Based Access Control

**Roles are a database enum on `users.role`** — `super_admin`, `admin`,
`user` (see `models/access.py`) — not a Python class. Check the string
value directly:

```python
# roles as stored: "super_admin" | "admin" | "user"
if user.role == "super_admin":
    ...  # unrestricted; bypasses every scope check
```

**User-management rules (enforced in `api/users.py`):** any modification
of a `super_admin` account, promotion **to** `super_admin`, and user
deletion are super-admin-only — an admin acting on those gets 403.

**Data-scope enforcement is centralized in `api/subunit_access.py`** —
every scope decision flows through these functions (callers pass the
session-derived `user_id` / `user_role` since issue 31):

```python
from parade_state.api import subunit_access

grants = await subunit_access.get_scope_grants(db, user_id, nr_id)
locations = await subunit_access.resolve_effective_locations(db, pids, tagging_id)
await subunit_access.assert_nr_accessible(db, user_id, user_role, nr_id)   # raising (write gate)
locations = await subunit_access.assert_locations_in_scope(...)            # raising (write gate)
pids_allowed = await subunit_access.in_scope_pids(...)                     # non-raising (read filter)
```

`super_admin` bypasses every check; everyone else is deny-by-default.

### Grouping Access Control (issue 26 redesign)

The old per-grouping access model (GroupingUserAccess grants +
UserSubunitScope scoping) was **removed**. The redesigned groupings carry
no access scoping at all:

- **Mutations are super-admin only** (403 otherwise), enforced
  server-side on every grouping API route — not just hidden buttons
- **Reads require an authenticated admin** (issue 31 tightened the API
  from open-with-params to `require_admin_user`; anonymous gets 401)
- **Reachability follows the active nominal roll**: groupings on the roll
  currently active for attendance are listable/readable; groupings on
  non-active rolls are retained in the database but unreachable (404)
  until their roll is re-activated
- The whole feature sits behind the `FEATURE_GROUPING` flag (default
  off): flag-off means 404 for every role, super-admins included

```python
# Grouping mutations authorize via the shared dependency (issue 31):
user: User = Depends(require_super_admin_user)
```

### NR-Scoped Write Access

**Data Isolation Strategy:**

1. **Explicit Grants:** admins receive `UserSubunitAssignment` rows per
   nominal roll — (unit, sub_unit_1) pairs with the `*` wildcard sentinel
   (issue #28)
2. **Deny-by-default:** no grants means no access (403 naming the missing
   assignment); super_admin bypasses
3. **Audit Trail:** grants and revocations are logged

**Implementation:**

```python
# Personnel/attendance reads and writes gated per NR by scope grants
# (api/subunit_access.py — the single enforcement seam; callers pass the
# session-derived user.id / user.role since issue #31 landed). Read path:
scoped_pids = await in_scope_pids(
    db, user_id, user_role, nominal_roll_id, active_tagging_id, all_pids
)
# Write path — raises 403 naming the out-of-scope "unit/sub-unit" labels:
locations = await assert_locations_in_scope(
    db, user_id, user_role, nominal_roll_id, personnel_ids, active_tagging_id
)
```

### Subunit Scope Filtering

**Per-nominal-roll access control** — a grant covers a personnel row when
it matches the row's *effective* location (tagging overlay remap if
present, canonical `unit`/`sub_unit_1` otherwise):

```python
# api/subunit_access.py — the real matching helpers
grants = await get_scope_grants(db, user_id, nominal_roll_id)
locations = await resolve_effective_locations(db, personnel_ids, tagging_id)

# Deny-by-default: no grants means no access.
covered = grant_matches(
    grants, *locations[personnel_id]
)  # True when any (unit, sub_unit_1) grant covers the effective location
```

Most endpoints should not re-implement this — call
`in_scope_pids` (reads) or `assert_locations_in_scope` (writes) instead.

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
- Accept caller identity from query params or request bodies (session only)
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

**Session lifetimes (as implemented):**

- **Database session tokens** (`UserSession`) expire after **7 days**
  (`create_user_session` default `expires_days=7`, `auth/session.py`);
  validity is checked on every request
- **Auth cookie** (`session_token`) carries a **24-hour** max-age
  (`AUTH_COOKIE_MAX_AGE = 86400`, `utils/cookies.py`) — the browser
  forgets the token long before the server-side session lapses

Logout and revocation delete the `UserSession` row
(`invalidate_session` / `invalidate_user_sessions`), and
`cleanup_expired_sessions` prunes expired rows.

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

**Not implemented.** There is no rate-limiting middleware (no slowapi or
equivalent) in the codebase. Authentication sits behind Google OAuth and
the API is admin-only, which limits abuse surface; if the deployment ever
becomes a target, a reverse-proxy or gateway-level limiter (or slowapi)
would be the place to add it.

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
