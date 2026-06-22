"""Tests for CSV upload API endpoints."""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from parade_state.models import AuditLog, CsvUpload


@pytest.mark.asyncio
async def test_upload_csv_success(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test successful CSV file upload."""
    csv_content = b"rank,name,unit\nPTE,John Doe,A Coy\nCPL,Jane Smith,B Coy\n"

    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("test.csv", csv_content, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()

    expected_hash = hashlib.sha256(csv_content).hexdigest()
    assert data["sha256_hash"] == expected_hash
    assert data["line_count"] == 2
    assert data["detected_columns"] == ["rank", "name", "unit"]
    assert data["status"] == "received"
    assert data["is_duplicate"] is False
    assert data["uploaded_by"] == admin_id

    # Verify CsvUpload record in DB
    result = await db_session.execute(
        select(CsvUpload).where(CsvUpload.id == data["id"])
    )
    upload = result.scalar_one()
    assert upload.sha256_hash == expected_hash
    assert upload.line_count == 2
    assert upload.raw_content == csv_content

    # Verify AuditLog entry created
    audit_result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "csv_upload",
            AuditLog.entity_id == data["id"],
        )
    )
    audit_log = audit_result.scalar_one()
    assert audit_log.action == "create"
    assert audit_log.user_id == admin_id


@pytest.mark.asyncio
async def test_upload_csv_duplicate_detection(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
):
    """Test that uploading the same file twice returns is_duplicate=True."""
    csv_content = b"rank,name\nPTE,John\n"

    # First upload
    response1 = client.post(
        "/api/v1/csv/upload",
        files={"file": ("test.csv", csv_content, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )
    assert response1.status_code == 200
    assert response1.json()["is_duplicate"] is False

    # Second upload (same content)
    response2 = client.post(
        "/api/v1/csv/upload",
        files={"file": ("test_copy.csv", csv_content, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["is_duplicate"] is True
    assert data2["id"] == response1.json()["id"]


@pytest.mark.asyncio
async def test_upload_csv_permission_denied(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_users,
):
    """Test that regular users cannot upload CSV files."""
    csv_content = b"rank,name\nPTE,John\n"
    user_id = str(sample_users["user"].id)

    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("test.csv", csv_content, "text/csv")},
        params={"user_id": user_id, "user_role": "user"},
        headers=user_token_headers,
    )

    assert response.status_code == 403
    assert "Only admins" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_csv_empty_file(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
):
    """Test that empty files are rejected."""
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("empty.csv", b"", "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_csv_wrong_extension(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
):
    """Test that non-CSV files are rejected."""
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("test.txt", b"some content", "text/plain")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 400
    assert ".csv" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_csv_user_not_found(
    client: TestClient,
    admin_token_headers: dict[str, str],
):
    """Test that uploading with a non-existent user ID returns 404."""
    csv_content = b"rank,name\nPTE,John\n"

    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("test.csv", csv_content, "text/csv")},
        params={
            "user_id": "nonexistent-user-id-12345",
            "user_role": "admin",
        },
        headers=admin_token_headers,
    )

    assert response.status_code == 404
    assert "User not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_csv_uploads(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test listing CSV uploads returns metadata only."""
    # Create uploads directly in DB
    upload1 = CsvUpload(
        raw_content=b"a,b\n1,2\n",
        sha256_hash="hash1" + "0" * 58,
        line_count=1,
        uploaded_by=admin_id,
    )
    upload2 = CsvUpload(
        raw_content=b"c,d\n3,4\n5,6\n",
        sha256_hash="hash2" + "0" * 58,
        line_count=2,
        uploaded_by=admin_id,
    )
    db_session.add_all([upload1, upload2])
    await db_session.commit()

    response = client.get(
        "/api/v1/csv/uploads",
        params={"user_id": admin_id, "user_role": "admin"},
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    # Verify raw_content is NOT in the response
    for item in data:
        assert "raw_content" not in item
        assert "sha256_hash" in item
        assert "line_count" in item
        assert "status" in item


@pytest.mark.asyncio
async def test_list_csv_uploads_pagination(
    client: TestClient,
    admin_token_headers: dict[str, str],
    admin_id: str,
    db_session,
):
    """Test pagination of CSV uploads list."""
    for i in range(5):
        upload = CsvUpload(
            raw_content=f"col\nval{i}\n".encode(),
            sha256_hash=f"hash{i}" + "0" * 59,
            line_count=1,
            uploaded_by=admin_id,
        )
        db_session.add(upload)
    await db_session.commit()

    response = client.get(
        "/api/v1/csv/uploads",
        params={"user_id": admin_id, "user_role": "admin", "limit": 2, "offset": 0},
        headers=admin_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    # Get next page
    response2 = client.get(
        "/api/v1/csv/uploads",
        params={"user_id": admin_id, "user_role": "admin", "limit": 2, "offset": 2},
        headers=admin_token_headers,
    )
    assert response2.status_code == 200
    assert len(response2.json()) == 2


@pytest.mark.asyncio
async def test_list_csv_uploads_permission_denied(
    client: TestClient,
    user_token_headers: dict[str, str],
    sample_users,
):
    """Test that regular users cannot list CSV uploads."""
    user_id = str(sample_users["user"].id)

    response = client.get(
        "/api/v1/csv/uploads",
        params={"user_id": user_id, "user_role": "user"},
        headers=user_token_headers,
    )

    assert response.status_code == 403
