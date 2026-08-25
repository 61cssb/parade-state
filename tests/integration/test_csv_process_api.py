"""Behavioral tests for the CSV → NominalRoll process endpoint (contract v2).

Covers: header-name matching and required-column errors, the strict
Yes-only row filter with skip counts, the storage map (core columns,
optional Pers, first-Remarks-only, ORNS alias, extra_fields int keys),
Reason/Callup Decision non-storage, extra-column tolerance, quoted-comma
names, old-format convergence, the canonical fixture's acceptance
numbers, auto-created 1:1 tagging, duplicate-CAA guard, authorization,
and the optional "import taggings from another NR" flow.
"""

import csv
import hashlib
import io
from datetime import date
from pathlib import Path

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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CANONICAL_FIXTURE = (
    REPO_ROOT / "fixtures"
    / "61 CSSB WY2627 ICT - Callup Eligible (caa260220) - Callup status.csv"
)

# The v2 contract's required columns plus the optional Pers — the shape
# live uploads are expected to carry. Tests mutate this to build errors.
STANDARD_HEADER = [
    "Unit",
    "Sub Unit 1",
    "Sub Unit 2",
    "Sub Unit 3",
    "Rank",
    "Full Name",
    "Pers",
    "Callup Decision",
    "Reason",
    "Remarks",
    "ORNS Yrs",
    "HK ICT",
]


def _make_csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    """Build a CSV (properly quoted) from a header and data rows."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _row(
    full_name: str,
    *,
    unit: str = "61 CSSB",
    su1: str = "S1",
    su2: str = "",
    su3: str = "",
    rank: str = "PTE",
    pers: str = "",
    decision: str = "Yes",
    reason: str = "",
    remarks: str = "",
    orns: str = "5",
    hk: str = "1",
) -> list[str]:
    """One data row aligned with ``STANDARD_HEADER``."""
    return [unit, su1, su2, su3, rank, full_name, pers, decision, reason, remarks, orns, hk]


@pytest.fixture
async def uploaded_csv(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
) -> tuple[str, bytes]:
    """Upload a small contract-conformant CSV; return (upload_id, raw_bytes)."""
    raw = _make_csv_bytes(
        STANDARD_HEADER,
        [
            _row("Alice", pers="p001", remarks="ok", orns="5", hk="1"),
            _row("Bob", rank="CPL", pers="p002", orns="6", hk="2"),
        ],
    )
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("fixture_caa260220.csv", raw, "text/csv")},
        headers=super_admin_token_headers,
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["id"]
    return upload_id, raw


def _upload(
    client: TestClient,
    headers: dict[str, str],
    raw: bytes,
    filename: str = "upload_caa260220.csv",
):
    return client.post(
        "/api/v1/csv/upload",
        files={"file": (filename, raw, "text/csv")},
        headers=headers,
    )


def _process(
    client: TestClient,
    headers: dict[str, str],
    upload_id: str,
    json: dict | None = None,
):
    return client.post(
        f"/api/v1/csv/{upload_id}/process",
        headers=headers,
        json=json if json is not None else {},
    )


# ----------------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------------


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

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["personnel_inserted"] == 2
    assert data["rows_skipped"] == 0
    assert data["decision_skipped"] == 0
    assert data["tagging_entries_imported"] == 0
    nr_id = data["nominal_roll_id"]

    # NominalRoll created with CAA derived from filename (caa260220 → 2026-02-20).
    nr = (await db_session.execute(
        select(NominalRoll).where(NominalRoll.id == nr_id)
    )).scalar_one()
    assert nr.caa == date(2026, 2, 20)
    assert nr.csv_hash == expected_hash
    assert nr.personnel_count == 2

    # Personnel rows: exact storage map — core columns, pers_no, first
    # Remarks → remarks, int extra_fields; Callup Decision / Reason nowhere.
    rows = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalars().all()
    assert len(rows) == 2
    by_name = {p.full_name: p for p in rows}
    assert {p.pers_no for p in rows} == {"p001", "p002"}

    alice = by_name["Alice"]
    assert alice.unit == "61 CSSB"
    assert alice.sub_unit_1 == "S1"
    assert alice.rank == "PTE"
    assert alice.category == "WOSE"
    assert alice.remarks == "ok"
    assert alice.extra_fields == {"orns": 5, "hk_ict": 1}
    assert alice.inpro_status == "yet_to_inpro"  # default (#32)
    assert alice.status == "active"

    bob = by_name["Bob"]
    assert bob.rank == "CPL"
    assert bob.remarks is None  # blank Remarks → NULL
    assert bob.extra_fields == {"orns": 6, "hk_ict": 2}

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
    """auto_process=true on upload runs the full pipeline in one step."""
    raw = _make_csv_bytes(
        STANDARD_HEADER,
        [_row("Alice", pers="p101"), _row("Bob", rank="CPL", pers="p102")],
    )
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("auto_caa260301.csv", raw, "text/csv")},
        params={"auto_process": "true"},
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


# ----------------------------------------------------------------------------
# Required-column validation (header-name contract)
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "expected_name"),
    [
        pytest.param(
            lambda h: [c for c in h if c != "Callup Decision"],
            "Callup Decision",
            id="missing-callup-decision",
        ),
        pytest.param(
            lambda h: [""] + h[1:],  # pre-fix export shape: blank Unit header
            "Unit",
            id="blank-unit-header",
        ),
        pytest.param(lambda h: [c for c in h if c != "Unit"], "Unit", id="missing-unit"),
        pytest.param(
            lambda h: [c for c in h if c != "HK ICT"], "HK ICT", id="missing-hk-ict"
        ),
        pytest.param(
            lambda h: [c for c in h if c != "Remarks"], "Remarks", id="missing-remarks"
        ),
    ],
)
async def test_process_missing_required_column_400(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
    mutate,
    expected_name,
):
    """A missing required column (blank header counts as missing) rejects
    processing with an error naming the column; no NR is created."""
    raw = _make_csv_bytes(mutate(list(STANDARD_HEADER)), [_row("Alice", pers="p001")])
    upload = _upload(client, super_admin_token_headers, raw, "bad_caa260220.csv")
    assert upload.status_code == 200, upload.text
    upload_id = upload.json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 400
    assert expected_name in response.json()["detail"]

    nrs = (await db_session.execute(select(NominalRoll))).scalars().all()
    assert nrs == []


@pytest.mark.asyncio
async def test_process_missing_orns_reports_alias_names(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
):
    """Neither ORNS nor ORNS Yrs present → error names both spellings."""
    header = [c for c in STANDARD_HEADER if c != "ORNS Yrs"]
    raw = _make_csv_bytes(header, [_row("Alice", pers="p001")])
    upload_id = _upload(
        client, super_admin_token_headers, raw, "no_orns_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "ORNS Yrs" in detail and "ORNS" in detail


@pytest.mark.asyncio
async def test_upload_auto_process_failure_keeps_upload_for_manual_step(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """A contract-violating CSV uploads fine; auto-processing reports the
    missing-column reason and leaves the upload stored for Step 2."""
    header = [c for c in STANDARD_HEADER if c != "Callup Decision"]
    raw = _make_csv_bytes(header, [_row("Alice", pers="p001")])

    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("badcols_caa260302.csv", raw, "text/csv")},
        params={"auto_process": "true"},
        headers=super_admin_token_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["process_result"] is None
    assert "Callup Decision" in data["process_error"]

    # Upload stored, unprocessed; no NR created.
    upload = (await db_session.execute(
        select(CsvUpload).where(CsvUpload.id == data["id"])
    )).scalar_one()
    assert upload.nominal_roll_id is None
    assert upload.status == "received"
    nrs = (await db_session.execute(select(NominalRoll))).scalars().all()
    assert nrs == []


# ----------------------------------------------------------------------------
# Strict Yes-only row filter
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_csv_strict_yes_filter(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """Only an exact Yes (case-insensitive) stores a row. No, blank, Y,
    Called Up and free text are all skipped and counted."""
    rows = [
        _row("Alpha", pers="p001", decision="Yes"),
        _row("Bravo", pers="p002", decision="YES"),
        _row("Charlie", pers="p003", decision="yes"),
        _row("Delta", pers="p004", decision="No"),
        _row("Echo", pers="p005", decision=""),
        _row("Foxtrot", pers="p006", decision="Y"),
        _row("Golf", pers="p007", decision="Called Up"),
        _row("Hotel", pers="p008", decision="deferred pending review"),
    ]
    raw = _make_csv_bytes(STANDARD_HEADER, rows)
    upload_id = _upload(
        client, super_admin_token_headers, raw, "decisions_caa260330.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["personnel_inserted"] == 3
    assert data["decision_skipped"] == 5
    assert data["rows_skipped"] == 5

    nr_id = data["nominal_roll_id"]
    stored = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalars().all()
    assert {p.full_name for p in stored} == {"Alpha", "Bravo", "Charlie"}


# ----------------------------------------------------------------------------
# Storage map details
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_orns_alias_both_spellings(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """ORNS and ORNS Yrs headers both land in extra_fields.orns."""
    for i, spelling in enumerate(("ORNS", "ORNS Yrs"), start=1):
        header = [
            c if c != "ORNS Yrs" else spelling for c in STANDARD_HEADER
        ]
        raw = _make_csv_bytes(header, [_row("Alice", pers="p001", orns="7")])
        upload_id = _upload(
            client,
            super_admin_token_headers,
            raw,
            f"orns{i}_caa26040{i}.csv",
        ).json()["id"]

        response = _process(client, super_admin_token_headers, upload_id)
        assert response.status_code == 201, response.text
        nr_id = response.json()["nominal_roll_id"]
        person = (await db_session.execute(
            select(Personnel).where(Personnel.nominal_roll_id == nr_id)
        )).scalar_one()
        assert person.extra_fields["orns"] == 7


@pytest.mark.asyncio
async def test_process_duplicate_remarks_first_wins(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """With two Remarks columns only the first is stored; the second is
    ignored (accepted loss per the signed-off contract)."""
    header = STANDARD_HEADER + ["Remarks"]  # duplicate header at the end
    raw = _make_csv_bytes(
        header,
        [_row("Alice", pers="p001", remarks="first remarks") + ["second remarks"]],
    )
    upload_id = _upload(
        client, super_admin_token_headers, raw, "duprmk_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    nr_id = response.json()["nominal_roll_id"]
    person = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalar_one()
    assert person.remarks == "first remarks"


@pytest.mark.asyncio
async def test_process_optional_pers_absent_column(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """A file without the optional Pers column stores NULL pers_no for
    every row (to be backfilled via the personnel edit flow)."""
    header = [c for c in STANDARD_HEADER if c != "Pers"]
    raw = _make_csv_bytes(
        header,
        [
            ["61 CSSB", "S1", "", "", "PTE", "Alice", "Yes", "", "rmk", "5", "1"],
            ["61 CSSB", "S1", "", "", "CPL", "Bob", "Yes", "", "", "6", "2"],
        ],
    )
    upload_id = _upload(
        client, super_admin_token_headers, raw, "nopers_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    assert response.json()["personnel_inserted"] == 2
    nr_id = response.json()["nominal_roll_id"]
    stored = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalars().all()
    assert {p.pers_no for p in stored} == {None}


@pytest.mark.asyncio
async def test_process_csv_blank_pers_no_stored_as_null(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """A CSV row with a blank Pers cell ingests with pers_no NULL (never '')."""
    raw = _make_csv_bytes(
        STANDARD_HEADER,
        [
            _row("Alice", pers="p001"),
            _row("Bob", rank="CPL"),
        ],
    )
    upload_id = _upload(
        client, super_admin_token_headers, raw, "blankpers_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    assert response.json()["personnel_inserted"] == 2

    nr_id = response.json()["nominal_roll_id"]
    rows = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalars().all()
    by_name = {p.full_name: p for p in rows}
    assert by_name["Alice"].pers_no == "p001"
    assert by_name["Bob"].pers_no is None


@pytest.mark.asyncio
async def test_process_age_yr_optional_storage(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """Age(Yr) is stored to extra_fields.age_yr when the file carries it;
    the key is absent when the column is missing."""
    header_with_age = STANDARD_HEADER + ["Age(Yr)"]
    raw_with = _make_csv_bytes(
        header_with_age, [_row("Alice", pers="p001") + ["31"]]
    )
    upload_with = _upload(
        client, super_admin_token_headers, raw_with, "age_caa260220.csv"
    ).json()["id"]
    response = _process(client, super_admin_token_headers, upload_with)
    assert response.status_code == 201, response.text
    nr_id = response.json()["nominal_roll_id"]
    person = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalar_one()
    assert person.extra_fields == {"orns": 5, "hk_ict": 1, "age_yr": 31}

    raw_without = _make_csv_bytes(STANDARD_HEADER, [_row("Alice", pers="p002")])
    upload_without = _upload(
        client, super_admin_token_headers, raw_without, "noage_caa260221.csv"
    ).json()["id"]
    response = _process(client, super_admin_token_headers, upload_without)
    assert response.status_code == 201, response.text
    nr_id = response.json()["nominal_roll_id"]
    person = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalar_one()
    assert person.extra_fields == {"orns": 5, "hk_ict": 1}
    assert "age_yr" not in person.extra_fields


@pytest.mark.asyncio
async def test_process_reason_and_decision_not_stored(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """Reason and Callup Decision are read (filter) but appear nowhere in
    stored data — not in remarks, not in extra_fields."""
    raw = _make_csv_bytes(
        STANDARD_HEADER,
        [_row("Alice", pers="p001", reason="course", remarks="att_out ok")],
    )
    upload_id = _upload(
        client, super_admin_token_headers, raw, "reason_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    nr_id = response.json()["nominal_roll_id"]
    person = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalar_one()
    assert person.remarks == "att_out ok"  # Remarks only — no Reason join
    assert set(person.extra_fields) == {"orns", "hk_ict"}
    assert person.inpro_status == "yet_to_inpro"


@pytest.mark.asyncio
async def test_process_extra_columns_tolerated_and_ignored(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """Unrecognized columns (the canonical fixture's extras) are tolerated
    and ignored — nothing is captured into extra_fields."""
    header = STANDARD_HEADER[:6] + ["Rank-Name"] + STANDARD_HEADER[6:] + [
        "NPI",
        "SAR-21 Qual Date",
        "Cbt Shoot History",
        "Detail",
    ]
    # Insert values for Rank-Name (after Full Name) and the trailing extras.
    base = _row("Alice", pers="p001", remarks="ok")
    row = base[:6] + ["PTE Alice"] + base[6:] + ["n1", "2024-01-01", "3", "admin"]
    raw = _make_csv_bytes(header, [row])
    upload_id = _upload(
        client, super_admin_token_headers, raw, "extras_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    assert response.json()["personnel_inserted"] == 1
    nr_id = response.json()["nominal_roll_id"]
    person = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalar_one()
    assert person.extra_fields == {"orns": 5, "hk_ict": 1}


@pytest.mark.asyncio
async def test_process_quoted_comma_name(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """Quoted commas inside Full Name cells parse correctly."""
    raw = _make_csv_bytes(
        STANDARD_HEADER, [_row("TAN, JOHN", pers="p001")]
    )
    upload_id = _upload(
        client, super_admin_token_headers, raw, "quoted_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    nr_id = response.json()["nominal_roll_id"]
    person = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalar_one()
    assert person.full_name == "TAN, JOHN"


@pytest.mark.asyncio
async def test_process_old_format_converged(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """The pre-v2 16-column WY2627 export (ORNS spelling, Age(Yr), no Pers,
    extra columns like Work Year 1 / Ineligible Reason) ingests cleanly
    under the converged name-matching contract."""
    header = [
        "Unit",
        "Sub Unit 1",
        "Sub Unit 2",
        "Sub Unit 3",
        "Rank",
        "Full Name",
        "Rank-Name",
        "Callup Decision",
        "Reason",
        "Remarks",
        "Age(Yr)",
        "Work Year 1",
        "ORNS",
        "HK ICT",
        "NPI",
        "Ineligible Reason",
    ]
    row = ["61 CSSB", "NON-ESTAB", "", "", "2LT", "BOH ZE KAI", "2LT BOH ZE KAI",
           "Yes", "", "", "31", "", "1", "0", "TRUE", ""]
    raw = _make_csv_bytes(header, [row])
    upload_id = _upload(
        client, super_admin_token_headers, raw, "oldfmt_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["personnel_inserted"] == 1
    nr_id = data["nominal_roll_id"]
    person = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalar_one()
    assert person.full_name == "BOH ZE KAI"
    assert person.category == "Officer"
    assert person.pers_no is None  # no Pers column in the old format
    assert person.extra_fields == {"orns": 1, "hk_ict": 0, "age_yr": 31}


@pytest.mark.asyncio
async def test_process_duplicate_names_without_pers_both_stored(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """Two roster rows with the same name and no Pers values are two
    distinct personnel rows (allowed; NULL pers_no never clashes)."""
    header = [c for c in STANDARD_HEADER if c != "Pers"]
    raw = _make_csv_bytes(
        header,
        [
            ["61 CSSB", "S1", "", "", "PTE", "John Doe", "Yes", "", "", "5", "1"],
            ["61 CSSB", "S2", "", "", "PTE", "John Doe", "Yes", "", "", "6", "2"],
        ],
    )
    upload_id = _upload(
        client, super_admin_token_headers, raw, "dupname_caa260220.csv"
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    assert response.json()["personnel_inserted"] == 2
    nr_id = response.json()["nominal_roll_id"]
    stored = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalars().all()
    assert len(stored) == 2
    assert len({p.id for p in stored}) == 2


# ----------------------------------------------------------------------------
# Canonical fixture acceptance (issue 34 acceptance criteria)
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_canonical_fixture_acceptance(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
    db_session: AsyncSession,
):
    """The canonical 560-row fixture processes into 397 personnel with 163
    No rows skipped; the 2 Yes rows without Pers store NULL pers_no."""
    raw = CANONICAL_FIXTURE.read_bytes()
    upload_id = _upload(
        client,
        super_admin_token_headers,
        raw,
        CANONICAL_FIXTURE.name,
    ).json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["personnel_inserted"] == 397
    assert data["decision_skipped"] == 163
    assert data["rows_skipped"] == 163

    nr_id = data["nominal_roll_id"]
    rows = (await db_session.execute(
        select(Personnel).where(Personnel.nominal_roll_id == nr_id)
    )).scalars().all()
    assert len(rows) == 397

    by_name = {p.full_name: p for p in rows}

    # The 2 Pers-less Yes rows store NULL pers_no.
    assert by_name["KECK TENG HONG"].pers_no is None
    assert by_name["MUHAMMAD AFIQ BIN MOHD NOOR"].pers_no is None

    # Spot-check a known row's full storage map.
    loh = by_name["LOH YOU WEI"]
    assert loh.pers_no == "10493101"
    assert loh.rank == "3WO"
    assert loh.category == "WOSE"
    assert loh.unit == "61 CSSB"
    assert loh.sub_unit_1 == "BN HQ"
    assert loh.sub_unit_2 == "CO OFFICE"
    assert loh.extra_fields == {"orns": 13, "hk_ict": 11}
    assert loh.inpro_status == "yet_to_inpro"

    # First Remarks only: a row whose text sits in the second Remarks
    # column stores NULL remarks (accepted loss).
    assert by_name["TSE SHU CHUN"].remarks is None

    # Reason / Callup Decision appear nowhere in stored data.
    for p in rows:
        assert "reason" not in p.extra_fields
        assert "callup_decision" not in p.extra_fields


# ----------------------------------------------------------------------------
# Guards, authorization, tagging import
# ----------------------------------------------------------------------------


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
    first = _process(client, super_admin_token_headers, upload_id)
    assert first.status_code == 201

    # Different content (different pers_no) so it is not a file duplicate.
    raw = _make_csv_bytes(STANDARD_HEADER, [_row("Carol", pers="p201")])
    response = client.post(
        "/api/v1/csv/upload",
        files={"file": ("second_caa260220.csv", raw, "text/csv")},
        params={"auto_process": "true"},
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
    raw = _make_csv_bytes(STANDARD_HEADER, [_row("Dan", pers="p301")])
    response = _upload(
        client, super_admin_token_headers, raw, "manual_caa260303.csv"
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
    first = _process(client, super_admin_token_headers, upload_id)
    assert first.status_code == 201

    second = _process(client, super_admin_token_headers, upload_id)
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
    db_session.add(NominalRoll(
        caa=date(2026, 2, 20),
        csv_hash="pre-empt",
        personnel_count=0,
        uploaded_by=admin_id,
    ))
    await db_session.commit()

    response = _process(client, super_admin_token_headers, upload_id)
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
    response = _process(client, user_token_headers, upload_id)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_process_csv_unknown_upload_404(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
):
    response = _process(
        client, super_admin_token_headers, "does-not-exist"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_process_csv_unparseable_filename_400(
    client: TestClient,
    super_admin_token_headers: dict[str, str],
    admin_id: str,
):
    """Filename without a caaYYMMDD token is rejected with 400."""
    raw = _make_csv_bytes(STANDARD_HEADER, [_row("Carol", pers="p001")])
    upload = _upload(client, super_admin_token_headers, raw, "no_caa_token.csv")
    upload_id = upload.json()["id"]

    response = _process(client, super_admin_token_headers, upload_id)
    assert response.status_code == 400
    assert "caaYYMMDD" in response.json()["detail"]


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

    response = _process(
        client,
        super_admin_token_headers,
        upload_id,
        {"source_nominal_roll_id": str(sample_nominal_roll.id)},
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

    raw = _make_csv_bytes(STANDARD_HEADER, [_row("Alice", pers="p001")])
    upload = _upload(
        client, super_admin_token_headers, raw, "match_caa260220.csv"
    )
    upload_id = upload.json()["id"]

    response = _process(
        client,
        super_admin_token_headers,
        upload_id,
        {"source_nominal_roll_id": str(sample_nominal_roll.id)},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["tagging_entries_imported"] == 1
    assert data["unmatched"] == []
