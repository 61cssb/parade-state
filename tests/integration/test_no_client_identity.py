"""Issue 31 machine gates: no client-supplied identity anywhere on the API.

Two layers of enforcement, so regressions can't slip back in:

1. **Structural** — walk every registered ``/api/v1`` route and assert no
   endpoint *declares* an identity parameter (query or request body). This
   is the machine-checked version of the issue's grep criteria: caller
   identity must be session-derived, so a ``user_role`` param can never
   reappear in a signature.
2. **Behavioral** — the spoof regression suite: appending
   ``?user_id=…&user_role=super_admin`` to a request must never change
   the outcome (no escalation, no scope bypass), and anonymous calls get
   401 rather than being treated as any identity.
"""

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from parade_state.main import app

# Query parameters that would smuggle caller identity (plus ``token``, the
# pre-#31 flexible-dependency leak vector).
IDENTITY_QUERY_PARAMS = {
    "user_id",
    "user_role",
    "requesting_user_id",
    "requesting_user_role",
    "granted_by",
    "revoked_by",
    "token",
}

# Request-body field names that would do the same (provenance is stamped
# server-side from the session, never accepted from the client).
IDENTITY_BODY_FIELDS = {
    "user_id",
    "user_role",
    "created_by",
    "granted_by",
    "revoked_by",
    "requesting_user_id",
    "requesting_user_role",
}

SPOOF_PARAMS = {"user_id": "super-admin-test-id", "user_role": "super_admin"}


def _api_v1_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1")
    ]


# ============================================================================
# 1. Structural gate — no identity params declared on any /api/v1 route
# ============================================================================


@pytest.mark.parametrize(
    "route",
    _api_v1_routes(),
    ids=lambda route: f"{','.join(route.methods)} {route.path}",
)
def test_api_route_declares_no_client_identity_params(route: APIRoute):
    """No endpoint may accept caller identity as a query or body param."""
    query_names = {p.alias or p.name for p in route.dependant.query_params}
    body_names = {p.alias or p.name for p in route.dependant.body_params}

    leaked_query = query_names & IDENTITY_QUERY_PARAMS
    assert not leaked_query, (
        f"{route.path} declares identity query params {leaked_query} — "
        "caller identity must come from the session (issue 31)"
    )
    leaked_body = body_names & IDENTITY_BODY_FIELDS
    assert not leaked_body, (
        f"{route.path} declares identity body fields {leaked_body} — "
        "provenance must be stamped server-side (issue 31)"
    )


def test_api_v1_surface_actually_covered():
    """Sanity: the structural gate sees a meaningful number of routes.

    Guards against the route list silently becoming empty (e.g. an import
    change) and the parametrized gate above vacuously passing.
    """
    assert len(_api_v1_routes()) >= 40


# ============================================================================
# 2. Behavioral gate — spoofed params change nothing
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/audit/logs"),
        ("get", "/api/v1/personnel"),
        ("get", "/api/v1/nominal-rolls"),
        ("get", "/api/v1/taggings"),  # super-admin only
        ("get", "/api/v1/deferments"),  # super-admin only
        ("get", "/api/v1/groupings/"),  # admin-tier read
        ("get", "/api/v1/csv/uploads"),
    ],
)
async def test_anonymous_api_call_is_401(client: TestClient, method: str, path: str):
    """Without a session every API surface rejects with 401."""
    response = getattr(client, method)(path)
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/v1/taggings", {}),
        ("post", "/api/v1/taggings", {"json": {"label": "x"}}),
        ("get", "/api/v1/deferments", {}),
        ("post", "/api/v1/csv/upload", {"files": {"file": ("t.csv", b"a,b\n", "text/csv")}}),
    ],
)
async def test_spoofed_params_cannot_escalate_to_super_admin(
    client: TestClient,
    client_as,
    method: str,
    path: str,
    kwargs: dict,
):
    """An admin session + spoofed super_admin params stays an admin."""
    client = await client_as("admin")

    plain = getattr(client, method)(path, **kwargs)
    spoofed = getattr(client, method)(path, params=SPOOF_PARAMS, **kwargs)

    # Identical outcome: these surfaces are super-admin only, so both 403
    # with the dependency's message — the query params are ignored utterly.
    assert plain.status_code == 403
    assert spoofed.status_code == 403
    assert spoofed.json()["detail"] == "Super admin access required"


@pytest.mark.asyncio
async def test_spoofed_params_cannot_bypass_scope(
    client: TestClient, client_as, sample_nominal_roll
):
    """A grant-less admin + spoofed super_admin params still gets the
    deny-by-default 403 on a scoped surface."""
    client = await client_as("admin")
    nr_id = str(sample_nominal_roll.id)

    plain = client.get(f"/api/v1/nominal-rolls/{nr_id}")
    spoofed = client.get(f"/api/v1/nominal-rolls/{nr_id}", params=SPOOF_PARAMS)

    assert plain.status_code == 403
    assert "No assignments" in plain.json()["detail"]
    assert spoofed.status_code == plain.status_code
    assert spoofed.json()["detail"] == plain.json()["detail"]


@pytest.mark.asyncio
async def test_spoofed_params_do_not_break_legitimate_admin_calls(
    client: TestClient, client_as, admin_subunit_assignment,
):
    """Params are ignored, not rejected: an in-scope admin with junk
    identity params appended still gets the normal 200."""
    client = await client_as("admin")

    plain = client.get("/api/v1/personnel")
    spoofed = client.get(
        "/api/v1/personnel", params=SPOOF_PARAMS
    )

    assert plain.status_code == 200
    assert spoofed.status_code == 200
    assert spoofed.json() == plain.json()


@pytest.mark.asyncio
async def test_regular_user_with_spoofed_params_still_rejected(
    client: TestClient, client_as
):
    """A user-role session cannot reach admin surfaces even with params."""
    client = await client_as("user")

    response = client.get("/api/v1/personnel", params=SPOOF_PARAMS)
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"
