"""Test helper functions for common test patterns."""

from fastapi.testclient import TestClient


def assert_pagination_works(
    client: TestClient,
    endpoint: str,
    headers: dict,
    params: dict | None = None,
    limit: int = 2,
):
    """Test that pagination works correctly for a list endpoint.

    Args:
        client: FastAPI TestClient
        endpoint: API endpoint path (with query params if needed)
        headers: Authentication headers
        params: Additional query parameters
        limit: Number of items per page
    """
    base_params = params or {}

    # Request first page
    response = client.get(
        endpoint, headers=headers, params={**base_params, "limit": limit, "offset": 0}
    )
    assert response.status_code == 200
    first_page = response.json()
    assert isinstance(first_page, list)
    assert len(first_page) <= limit

    # Request second page
    response = client.get(
        endpoint,
        headers=headers,
        params={**base_params, "limit": limit, "offset": limit},
    )
    assert response.status_code == 200
    second_page = response.json()
    assert isinstance(second_page, list)

    # Ensure no overlap between pages
    first_ids = {item.get("id") for item in first_page}
    second_ids = {item.get("id") for item in second_page}
    assert len(first_ids & second_ids) == 0, "Pages should not overlap"


def assert_404_response(
    client: TestClient,
    method: str,
    endpoint: str,
    headers: dict,
    params: dict | None = None,
):
    """Test that an endpoint returns 404 for non-existent resource.

    Args:
        client: FastAPI TestClient
        method: HTTP method ('get', 'post', 'patch', 'delete')
        endpoint: API endpoint path
        headers: Authentication headers
        params: Query or request parameters
    """
    response = getattr(client, method)(endpoint, headers=headers, params=params)
    assert response.status_code == 404


def assert_permission_denied(
    client: TestClient,
    method: str,
    endpoint: str,
    headers: dict,
    expected_detail: str | None = None,
    params: dict | None = None,
    json_data: dict | None = None,
):
    """Test that an endpoint returns 403 Forbidden.

    Args:
        client: FastAPI TestClient
        method: HTTP method ('get', 'post', 'patch', 'delete')
        endpoint: API endpoint path
        headers: Authentication headers
        expected_detail: Expected error message fragment
        params: Query parameters
        json_data: Request body for POST/PATCH
    """
    # Build kwargs dynamically to handle different parameter combinations
    kwargs = {"headers": headers}
    if params:
        kwargs["params"] = params
    if json_data:
        kwargs["json"] = json_data

    response = getattr(client, method)(endpoint, **kwargs)
    assert response.status_code == 403
    if expected_detail:
        assert expected_detail in response.json()["detail"]
