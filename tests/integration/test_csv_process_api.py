"""Behavioral tests for the CSV → NominalRoll process endpoint.

Covers: happy-path ingestion, auto-created 1:1 tagging, duplicate-CAA guard,
authorization, and the optional "import taggings from another NR" flow
(short_id matching + unmatched surfacing).
"""

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.models import (
    CsvUpload,
    NominalRoll,
    Personnel,
    Tagging,
    TaggingEntry,
)
from parade_state.utils import ids


SUPER_ADMIN_PARAMS = {"user_role": "super_admin"}
ADMIN_PARAMS = {"user_role": "admin"}
USER_PARAMS = {"user_role": "user"}


def _make_csv_bytes(rows: list[list[str]]) -> bytes:
    """Build an 18-column CSV matching ``CANONICAL_MAP`` in csv_constants."""
    header = [
        "",  # 0 unit
        "Sub Unit 1",
        "Sub Unit 2",
        "Sub Unit 3",
        "Rank",
        "Full Name",
        "Rank-Name",
        "Pers",
        "Callup Decision",
        "Reason",
        "Remarks",
        "ORNS Yrs",
        "HK ICT",
        "NPI",
        "SAR-21 Qual Date",
        "Cbt Shoot History",
        "Detail",
        "Remarks",  # 17 duplicate header
    ]
    lines = [",".join(header)]
    for r in rows:
        # Pad row to 18 columns to satisfy CANONICAL_MAP indexing.
        padded = list(r) + [""] * (18 - len(r))
        lines.append(",".join(padded[:18]))
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture
async def uploaded_csv(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
) -> tuple[str, bytes]:
    """Upload a small CSV with the canonical 18-column layout; return (upload_id, raw_bytes)."""
    raw = _make_csv_bytes(
        [
            ["61 CSSB", "S1", "S2", "S3", "PTE", "Alice", "PTE Alice", "p001",
             "Eligible", "", "ok", "5", "1", "n1", "2024-01-01", "3", "A", "rmk1"],
            ["61 CSSB", "S1", "S2", "", "CPL", "Bob", "CPL Bob", "p002",
             "Eligible", "", "", "6", "2", "n2", "2024-01-02", "2", "B", ""],
        ]
    )
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("fixture_caa260220.csv", raw, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=super_admin_token_headers,
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["id"]
    return upload_id, raw


@pytest.mark.asyncio
async def test_process_csv_creates_nr_personnel_and_tagging(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    uploaded_csv,
    db_session: AsyncSession,
):
    upload_id, raw = uploaded_csv
    expected_hash = hashlib.sha256(raw).hexdigest()

    response = client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"created_by": admin_id},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["personnel_inserted"] == 2
    assert data["rows_skipped"] == 0
    assert data["tagging_entries_imported"] == 0
    nr_id = data["nominal_roll_id"]

    # NominalRoll created with CAA derived from filename (caa260220 → 2026-02-20).
    nr = (await db_session.execute(
        select(NominalRoll).where(NominalRoll.id == nr_id)
    )).scalar_one()
    from datetime import date
    assert nr.caa == date(2026, 2, 20)
    assert nr.csv_hash == expected_hash
    assert nr.personnel_count == 2

    # Personnel rows.
    rows = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalars().all()
    assert len(rows) == 2

    # 1:1 tagging auto-created.
    tagging = (await db_session.execute(
        select(Tagging).where(Tagging.nominal_roll_id == nr_id)
    )).scalar_one()
    assert tagging.nominal_roll_id == nr_id

    # CsvUpload linked.
    upload = (await db_session.execute(
        select(CsvUpload).where(CsvUpload.id == upload_id)
    )).scalar_one()
    assert upload.nominal_roll_id == nr_id


@pytest.mark.asyncio
async def test_process_csv_refuses_already_processed(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    uploaded_csv,
):
    upload_id, _ = uploaded_csv
    first = client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"created_by": admin_id},
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"created_by": admin_id},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_process_csv_refuses_duplicate_caa(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    uploaded_csv,
    db_session: AsyncSession,
):
    upload_id, _ = uploaded_csv
    # Pre-create an NR with the same CAA that the upload will resolve to.
    from datetime import date
    db_session.add(NominalRoll(
        caa=date(2026, 2, 20),
        csv_hash="pre-empt",
        status="draft",
        personnel_count=0,
        uploaded_by=admin_id,
    ))
    await db_session.commit()

    response = client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"created_by": admin_id},
    )
    assert response.status_code == 409
    assert "CAA 2026-02-20" in response.json()["detail"]


@pytest.mark.asyncio
async def test_process_csv_as_user_forbidden(
    client: TestClient,
    user_token_headers: dict[str, str],
    admin_id: str,
    uploaded_csv,
):
    upload_id, _ = uploaded_csv
    response = client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=user_token_headers,
        params=USER_PARAMS,
        json={"created_by": admin_id},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_process_csv_imports_taggings_from_source_nr(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    uploaded_csv,
    sample_nominal_roll,
    sample_personnel,
    db_session: AsyncSession,
):
    """Import path: build a source NR/tagging, then process the upload with
    ``source_nominal_roll_id`` pointing at it. Personnel are matched by
    short_id; CSV rows auto-mint fresh random short_ids, so the source's
    entries won't collide with the new NR's personnel — every source entry
    surfaces as unmatched. This still exercises the full code path end-to-end."""
    upload_id, _ = uploaded_csv

    source_tagging = Tagging(
        label="source",
        nominal_roll_id=str(sample_nominal_roll.id),
        created_by=admin_id,
    )
    source_tagging.entries.append(TaggingEntry(
        personnel_id=str(sample_personnel[0].id),
        from_unit=sample_personnel[0].unit,
        from_sub_unit_1=sample_personnel[0].sub_unit_1,
        to_unit="Coy X",
    ))
    db_session.add(source_tagging)
    await db_session.commit()

    response = client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={
            "created_by": admin_id,
            "source_nominal_roll_id": str(sample_nominal_roll.id),
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    # CSV rows have fresh random short_ids; source has its own — no overlap.
    assert data["tagging_entries_imported"] == 0
    assert len(data["unmatched"]) == 1
    assert data["unmatched"][0]["short_id"] == sample_personnel[0].short_id


@pytest.mark.asyncio
async def test_process_csv_unknown_upload_404(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
):
    response = client.post(
        "/api/v1/csv/does-not-exist/process",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"created_by": admin_id},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_process_csv_unparseable_filename_400(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
):
    """Filename without a caaYYMMDD token is rejected with 400."""
    raw = _make_csv_bytes([["U", "S1", "", "", "PTE", "Carol"]])
    upload = client.post(
        "/api/v1/csv/upload",
        files={"file": ("no_caa_token.csv", raw, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=super_admin_token_headers,
    )
    upload_id = upload.json()["id"]

    response = client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"created_by": admin_id},
    )
    assert response.status_code == 400
    assert "caaYYMMDD" in response.json()["detail"]
