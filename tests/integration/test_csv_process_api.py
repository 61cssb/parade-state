"""Behavioral tests for the CSV → NominalRoll process endpoint.

Covers: happy-path ingestion, auto-created 1:1 tagging, duplicate-CAA guard,
authorization, and the optional "import taggings from another NR" flow
(pers_no matching + unmatched surfacing).
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
    # pers_no populated from the CSV Pers column.
    assert {p.pers_no for p in rows} == {"p001", "p002"}

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
async def test_upload_with_auto_process_creates_nr_and_tagging(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """auto_process=true on upload runs the full pipeline in one step.

    The response carries the process result; the NR's 1:1 empty tagging
    is auto-created and the upload is linked.
    """
    raw = _make_csv_bytes(
        [
            ["61 CSSB", "S1", "", "", "PTE", "Alice", "", "p101",
             "Eligible", "", "", "", "", "", "", "", "", ""],
            ["61 CSSB", "S1", "", "", "CPL", "Bob", "", "p102",
             "Eligible", "", "", "", "", "", "", "", "", ""],
        ]
    )
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("auto_caa260301.csv", raw, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin", "auto_process": "true"},
        headers=super_admin_token_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["is_duplicate"] is False
    assert data["process_error"] is None
    assert data["process_result"]["personnel_inserted"] == 2
    nr_id = data["process_result"]["nominal_roll_id"]

    nr = (await db_session.execute(
        select(NominalRoll).where(NominalRoll.id == nr_id)
    )).scalar_one()
    assert nr.personnel_count == 2

    # 1:1 tagging auto-created and empty.
    tagging = (await db_session.execute(
        select(Tagging).where(Tagging.nominal_roll_id == nr_id)
    )).scalar_one()
    entries = (await db_session.execute(
        select(TaggingEntry).where(TaggingEntry.tagging_id == tagging.id)
    )).scalars().all()
    assert entries == []

    # Upload linked to the created NR.
    upload = (await db_session.execute(
        select(CsvUpload).where(CsvUpload.id == data["id"])
    )).scalar_one()
    assert upload.nominal_roll_id == nr_id


@pytest.mark.asyncio
async def test_upload_auto_process_failure_keeps_upload_for_manual_step(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """A wrong-column-count CSV uploads fine; auto-processing reports the
    failure reason and leaves the upload stored/unprocessed for Step 2."""
    csv_content = b"rank,name,unit\nPTE,John Doe,A Coy\n"

    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("badcols_caa260302.csv", csv_content, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin", "auto_process": "true"},
        headers=super_admin_token_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["process_result"] is None
    assert "columns" in data["process_error"]

    # Upload stored, unprocessed; no NR created.
    upload = (await db_session.execute(
        select(CsvUpload).where(CsvUpload.id == data["id"])
    )).scalar_one()
    assert upload.nominal_roll_id is None
    assert upload.status == "received"
    nrs = (await db_session.execute(select(NominalRoll))).scalars().all()
    assert nrs == []


@pytest.mark.asyncio
async def test_upload_auto_process_reports_duplicate_caa(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    uploaded_csv,
    db_session: AsyncSession,
):
    """Auto-processing an upload whose CAA already has an NR reports the
    conflict instead of failing the upload."""
    upload_id, _ = uploaded_csv  # caa260220, will be processed below
    first = client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=super_admin_token_headers,
        params=SUPER_ADMIN_PARAMS,
        json={"created_by": admin_id},
    )
    assert first.status_code == 201

    # Different content (different pers_no) so it is not a file duplicate.
    raw = _make_csv_bytes(
        [
            ["61 CSSB", "S9", "", "", "PTE", "Carol", "", "p201",
             "", "", "", "", "", "", "", "", "", ""],
        ]
    )
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("second_caa260220.csv", raw, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin", "auto_process": "true"},
        headers=super_admin_token_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["process_result"] is None
    assert "already exists" in data["process_error"]

    upload = (await db_session.execute(
        select(CsvUpload).where(CsvUpload.id == data["id"])
    )).scalar_one()
    assert upload.nominal_roll_id is None


@pytest.mark.asyncio
async def test_upload_without_auto_process_stays_manual(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """Default upload behavior is unchanged: nothing is processed."""
    raw = _make_csv_bytes(
        [
            ["61 CSSB", "S1", "", "", "PTE", "Dan", "", "p301",
             "", "", "", "", "", "", "", "", "", ""],
        ]
    )
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("manual_caa260303.csv", raw, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=super_admin_token_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["process_result"] is None
    assert data["process_error"] is None
    nrs = (await db_session.execute(select(NominalRoll))).scalars().all()
    assert nrs == []


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
    pers_no; the CSV rows carry pers_no p001/p002 while the source person's
    pers_no (10000001, from conftest) does not overlap — so the source entry
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
    # CSV rows carry p001/p002; the source person has 10000001 — no overlap.
    assert data["tagging_entries_imported"] == 0
    assert len(data["unmatched"]) == 1
    assert data["unmatched"][0]["pers_no"] == sample_personnel[0].pers_no


@pytest.mark.asyncio
async def test_process_csv_imports_taggings_matching_pers_no(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    sample_nominal_roll,
    sample_users,
    db_session: AsyncSession,
):
    """A source person whose pers_no equals a CSV row's Pers value is matched:
    their tagging entry is copied onto the new NR's auto-created tagging."""
    # Source person with the same pers_no as the CSV's Alice row ("p001").
    source_person = Personnel(
        nominal_roll_id=str(sample_nominal_roll.id),
        pers_no="p001",
        rank="PTE",
        category="WOSE",
        full_name="Alice Alias",
        unit="Coy A",
        created_by=str(sample_users["admin"].id),
    )
    db_session.add(source_person)
    await db_session.flush()

    source_tagging = Tagging(
        label="source",
        nominal_roll_id=str(sample_nominal_roll.id),
        created_by=admin_id,
    )
    source_tagging.entries.append(TaggingEntry(
        personnel_id=str(source_person.id),
        from_unit=source_person.unit,
        from_sub_unit_1=source_person.sub_unit_1,
        to_unit="Coy X",
    ))
    db_session.add(source_tagging)
    await db_session.commit()

    raw = _make_csv_bytes(
        [
            ["61 CSSB", "S1", "S2", "S3", "PTE", "Alice", "PTE Alice", "p001",
             "Eligible", "", "ok", "5", "1", "n1", "2024-01-01", "3", "A", "rmk1"],
        ]
    )
    upload = client.post(
        "/api/v1/csv/upload",
        files={"file": ("fixture_caa260220.csv", raw, "text/csv")},
        params={"user_id": admin_id, "user_role": "admin"},
        headers=super_admin_token_headers,
    )
    upload_id = upload.json()["id"]

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
    assert data["tagging_entries_imported"] == 1
    assert data["unmatched"] == []


@pytest.mark.asyncio
async def test_process_csv_blank_pers_no_stored_as_null(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """A CSV row with a blank Pers cell ingests with pers_no NULL (never '')."""
    raw = _make_csv_bytes(
        [
            ["61 CSSB", "S1", "S2", "S3", "PTE", "Alice", "PTE Alice", "p001",
             "Eligible", "", "ok", "5", "1", "n1", "2024-01-01", "3", "A", "rmk1"],
            ["61 CSSB", "S1", "S2", "", "CPL", "Bob", "CPL Bob", "",
             "Eligible", "", "", "6", "2", "n2", "2024-01-02", "2", "B", ""],
        ]
    )
    upload = client.post(
        "/api/v1/csv/upload",
        files={"file": ("fixture_caa260220.csv", raw, "text/csv")},
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
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["personnel_inserted"] == 2

    nr_id = data["nominal_roll_id"]
    rows = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalars().all()
    by_name = {p.full_name: p for p in rows}
    assert by_name["Alice"].pers_no == "p001"
    assert by_name["Bob"].pers_no is None


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
