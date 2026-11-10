"""Defaulter release API + page integration tests (FEATURE_IPPT).

Covers the ingest contract (quarter chosen by the user, replace-on-
re-ingest of the current quarter, archiving on a new quarter, archived
quarters immutable, nothing written on rejection), PERS/name matching
against the attendance-active roll, effective sub-unit resolution,
reason/remarks editing with audit trails, permissions, flag gating,
and the dashboard/upload page rendering.
"""

import csv
import io
import json
from datetime import date

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from parade_state.api.admin_purge import PURGE_TABLES
from parade_state.models import (
    AuditLog,
    DefaulterQuarantinedRow,
    DefaulterReport,
    DefaulterRow,
    NominalRoll,
    Personnel,
    User,
)

# ============================================================================
# Helpers: synthetic defaulter releases
# ============================================================================

PENDING_HEADER = [
    "TYPE",
    "UNIT",
    "PERS",
    "RANK",
    "NAME",
    "OFFENCE TYPE",
    "OFFENCE WINDOW",
    "DEFAULT COUNT",
    "SAF264 ISSUED",
    "SCHEDULED SUMMARY TRIAL",
    "MAIL MERGE DATE",
    "DUE IN (DAYS)",
    "AMENDMENT STATUS",
    "",
    "",
    "RANK GROUP",
]
ARCHIVED_HEADER = [
    "TYPE",
    "UNIT",
    "PERS",
    "RANK",
    "NAME",
    "OFFENCE TYPE",
    "OFFENCE WINDOW",
    "DEFAULT COUNT",
    "DEFAULTER COUNT",
    "DISCIPLINARY ACTION",
    "CHARGE STATUS",
    "SCHEDULED SUMMARY TRIAL",
    "VERDICT",
    "JUSTIFICATION",
    "",
    "",
    "RANK GROUP",
]


def _pending_row(
    pers: str,
    rank: str,
    name: str,
    *,
    row_type: str = "Pending ST",
    window: str = "25/08/2025 - 24/08/2026",
) -> list[str]:
    return [
        row_type,
        "61 CSSB",
        pers,
        rank,
        name,
        "NS FIT Defaulter",
        window,
        "6",
        "3",
        "20/10/2026, 10:45",
        "28/09/2026",
        "294",
        "N.A.",
        "",
        "",
        "Commander",
    ]


def _archived_row(pers: str, rank: str, name: str) -> list[str]:
    return [
        "Archived",
        "61 CSSB",
        pers,
        rank,
        name,
        "NS FIT Defaulter",
        "03/07/2025 - 02/07/2026",
        "3",
        "1",
        "Issued SWL (Reset)",
        "SWL issued",
        "",
        "Issued SWL (Reset)",
        "N.A.",
        "",
        "",
        "Commander",
    ]


def _release_csv(
    pending: list[list[str]], archived: list[list[str]] | None = None
) -> bytes:
    """One release file: pending section, blank, archived section. The
    preamble header of the live file (a different report format's
    header, no data rows) is folded in — it must be ignored."""
    rows: list[list[str]] = [
        [
            "TYPE",
            "UNIT",
            "PERS",
            "RANK",
            "NAME",
            "OFFENCE TYPE",
            "OFFENCE WINDOW",
            "DEFAULT COUNT",
            "PAST OFFENCES",
            "DISCIPLINARY ACTION",
            "COMPOSITION AMOUNT",
            "DUE IN (DAYS)",
        ],
        [],
        PENDING_HEADER,
        *pending,
        [],
    ]
    if archived is not None:
        rows.extend([ARCHIVED_HEADER, *archived, []])
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode()


def _release_xlsx(pending: list[list[str]]) -> bytes:
    """The same release as an .xlsx first tab (pending section only)."""
    buffer = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(PENDING_HEADER)
    for row in pending:
        sheet.append([int(cell) if cell.isdigit() and cell else cell for cell in row])
    workbook.save(buffer)
    return buffer.getvalue()


async def _seed_active_roll(db_session, rows: list[tuple[str, str, str]]) -> None:
    """Attendance-active roll; ``rows`` = (pers_no, rank, full_name)."""
    admin = await db_session.get(User, "super-admin-test-id")
    roll = NominalRoll(
        caa=date(2026, 1, 1),
        csv_hash="defaulter-test-roll",
        personnel_count=len(rows),
        uploaded_by=str(admin.id),
    )
    db_session.add(roll)
    await db_session.flush()
    for pers_no, rank, name in rows:
        db_session.add(
            Personnel(
                nominal_roll_id=str(roll.id),
                pers_no=pers_no,
                rank=rank,
                category="WOSE",
                full_name=name,
                unit="61 CSSB",
                sub_unit_1="BN HQ",
                created_by=str(admin.id),
            )
        )
    roll.attendance_active = True
    await db_session.commit()


def _upload(client, content: bytes, filename: str, year=2026, quarter=4):
    return client.post(
        "/api/v1/ippt/defaulters",
        files=[("file", (filename, content, "text/csv"))],
        data={"year": str(year), "quarter": str(quarter)},
    )


ROSTER = (
    ("11104490", "LCP", "ARAVIND NAIDU S/O GOVINDARAJOO"),
    ("11155051", "CPL", "MUHAMMAD NUR HAZRI BIN HAMZAH"),
    ("10665005", "PTE", "NURHAYAT BIN ZAINE"),
)


@pytest.fixture
async def active_roll(db_session):
    await _seed_active_roll(db_session, list(ROSTER))


# ============================================================================
# Permissions & flag gating
# ============================================================================


async def test_upload_requires_super_admin(client_as):
    admin = await client_as("admin")
    response = _upload(admin, _release_csv([_pending_row(*ROSTER[0])]), "q4.csv")
    assert response.status_code == 403

    anonymous = await client_as("admin")
    anonymous.cookies.clear()
    assert (
        _upload(
            anonymous, _release_csv([_pending_row(*ROSTER[0])]), "q4.csv"
        ).status_code
        == 401
    )


async def test_flag_off_hides_defaulter_routes(client_as, monkeypatch):
    client = await client_as("super_admin")
    from parade_state.config import get_settings
    from parade_state.main import app as main_app

    for settings_obj in {get_settings(), main_app.state.settings}:
        monkeypatch.setattr(settings_obj, "FEATURE_IPPT", False)
    try:
        assert client.get("/api/v1/ippt/defaulters").status_code == 404
        assert client.post("/api/v1/ippt/defaulters").status_code == 404
    finally:
        for settings_obj in {get_settings(), main_app.state.settings}:
            monkeypatch.setattr(settings_obj, "FEATURE_IPPT", True)


# ============================================================================
# Ingest
# ============================================================================


async def test_upload_ingests_and_matches(client_as, db_session, active_roll):
    client = await client_as("super_admin")
    content = _release_csv(
        [_pending_row(*ROSTER[0]), _pending_row(*ROSTER[1])],
        archived=[_archived_row(*ROSTER[2])],
    )
    response = _upload(client, content, "release.csv")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["report"]["label"] == "Q4 2026"
    assert body["pending_rows"] == 2
    assert body["archived_rows"] == 1
    assert body["quarantined"] == 0
    assert body["match"] == {"matched": 3, "unmatched": 0, "ambiguous": 0}
    assert body["archived"] == []

    rows = (
        (await db_session.execute(select(DefaulterRow).order_by(DefaulterRow.id)))
        .scalars()
        .all()
    )
    assert [row.row_type for row in rows] == ["pending", "pending", "archived"]
    assert all(row.sub_unit == "BN HQ" for row in rows)
    assert all(row.match_status == "matched" for row in rows)

    # GET mirrors what the dashboard table shows: pending only.
    listing = client.get("/api/v1/ippt/defaulters").json()
    assert listing["report"]["label"] == "Q4 2026"
    assert len(listing["rows"]) == 2
    assert listing["rows"][0]["defaulted_count"] == 3

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity_type == "defaulter_report")
            )
        )
        .scalars()
        .one()
    )
    assert json.loads(audit.description)["quarter"] == "Q4 2026"


async def test_upload_rejects_bad_quarter(client_as, db_session):
    client = await client_as("super_admin")
    for quarter in ("0", "5"):
        response = _upload(
            client,
            _release_csv([_pending_row(*ROSTER[0])]),
            "release.csv",
            quarter=int(quarter),
        )
        assert response.status_code == 422
        assert "Quarter must be 1–4" in response.json()["detail"]
    assert (await db_session.execute(select(DefaulterReport))).scalars().all() == []


async def test_upload_rejects_bad_file_without_writing(client_as, db_session):
    client = await client_as("super_admin")
    response = _upload(client, b"garbage,not,a,header\n1,2,3\n", "release.csv")
    assert response.status_code == 422
    assert "first row is not a section header" in response.json()["detail"]
    assert (await db_session.execute(select(DefaulterReport))).scalars().all() == []


async def test_xlsx_first_tab_ingests(client_as, db_session, active_roll):
    client = await client_as("super_admin")
    response = _upload(
        client,
        _release_xlsx([_pending_row(*ROSTER[0])]),
        "release.xlsx",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["pending_rows"] == 1


async def test_reingest_replaces_current_quarter(client_as, db_session, active_roll):
    client = await client_as("super_admin")
    first = _release_csv([_pending_row(*ROSTER[0]), _pending_row(*ROSTER[1])])
    assert _upload(client, first, "release.csv").status_code == 200

    rows = (await db_session.execute(select(DefaulterRow))).scalars().all()
    patched = client.patch(
        f"/api/v1/ippt/defaulters/rows/{rows[0].id}",
        json={"reason": "hsp", "remarks": "counselled"},
    )
    assert patched.status_code == 200

    # Same quarter again, now with one row: the replacement must drop the
    # old rows and their annotations with them.
    second = _release_csv([_pending_row(*ROSTER[2])])
    response = _upload(client, second, "release-v2.csv")
    assert response.status_code == 200
    body = response.json()
    assert body["replaced"] is True
    assert body["archived"] == []
    assert body["pending_rows"] == 1

    db_session.expire_all()  # drop the identity-mapped pre-replace rows
    rows = (await db_session.execute(select(DefaulterRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].full_name == ROSTER[2][2]
    assert rows[0].reason is None  # annotation did not survive the replace

    reports = (await db_session.execute(select(DefaulterReport))).scalars().all()
    assert len(reports) == 1
    assert reports[0].status == "current"


async def test_new_quarter_archives_previous(client_as, db_session, active_roll):
    client = await client_as("super_admin")
    assert (
        _upload(
            client, _release_csv([_pending_row(*ROSTER[0])]), "q4.csv", 2026, 4
        ).status_code
        == 200
    )
    old_rows = (await db_session.execute(select(DefaulterRow))).scalars().all()
    client.patch(
        f"/api/v1/ippt/defaulters/rows/{old_rows[0].id}",
        json={"remarks": "contacted, awaiting response"},
    )

    response = _upload(
        client, _release_csv([_pending_row(*ROSTER[1])]), "q1.csv", 2027, 1
    )
    assert response.status_code == 200
    body = response.json()
    assert body["archived"] == ["Q4 2026"]
    assert body["replaced"] is False

    db_session.expire_all()  # the API session archived + inserted rows
    reports = {
        (report.year, report.quarter): report.status
        for report in (
            (await db_session.execute(select(DefaulterReport))).scalars().all()
        )
    }
    assert reports == {(2026, 4): "archived", (2027, 1): "current"}

    # Archived quarter's rows are retained (future validation), including
    # their annotations.
    rows = (
        (
            await db_session.execute(
                select(DefaulterRow)
                .join(DefaulterReport, DefaulterRow.report_id == DefaulterReport.id)
                .where(DefaulterReport.year == 2026, DefaulterReport.quarter == 4)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].remarks == "contacted, awaiting response"

    # GET/Dashboard surface only the current quarter.
    listing = client.get("/api/v1/ippt/defaulters").json()
    assert listing["report"]["label"] == "Q1 2027"
    assert len(listing["rows"]) == 1


async def test_archived_quarter_cannot_be_reingested(client_as):
    client = await client_as("super_admin")
    assert (
        _upload(
            client, _release_csv([_pending_row(*ROSTER[0])]), "q4.csv", 2026, 4
        ).status_code
        == 200
    )
    assert (
        _upload(
            client, _release_csv([_pending_row(*ROSTER[1])]), "q1.csv", 2027, 1
        ).status_code
        == 200
    )

    response = _upload(
        client, _release_csv([_pending_row(*ROSTER[2])]), "q4-fixed.csv", 2026, 4
    )
    assert response.status_code == 422
    assert "already archived" in response.json()["detail"]


async def test_quarantined_rows_are_kept(client_as, db_session, active_roll):
    client = await client_as("super_admin")
    bad = _pending_row(*ROSTER[0])
    bad[7] = "six"  # DEFAULT COUNT must be a whole number
    content = _release_csv([bad, _pending_row(*ROSTER[1])])
    response = _upload(client, content, "release.csv")
    assert response.status_code == 200
    assert response.json()["quarantined"] == 1

    quarantined = (
        (await db_session.execute(select(DefaulterQuarantinedRow))).scalars().all()
    )
    assert len(quarantined) == 1
    assert "DEFAULT COUNT" in quarantined[0].reason


async def test_name_matching_without_pers(client_as, db_session):
    """No PERS column and no active roll: everything ingests unmatched,
    subunit unknown."""
    client = await client_as("super_admin")
    rows = [_pending_row(*ROSTER[0])]
    rows[0][2] = ""  # blank PERS cell
    content = _release_csv(rows)
    response = _upload(client, content, "release.csv")
    assert response.status_code == 200
    body = response.json()
    assert body["match"]["unmatched"] == 1
    assert body["unmatched_names"] == [ROSTER[0][2]]

    row = (await db_session.execute(select(DefaulterRow))).scalars().one()
    assert row.sub_unit is None


async def test_pers_takes_priority_and_falls_back_to_name(
    client_as, db_session, active_roll
):
    """A stale/wrong PERS falls through to the name match, not to
    unmatched."""
    client = await client_as("super_admin")
    rows = [_pending_row(*ROSTER[0])]
    rows[0][2] = "99999999"  # nobody's PERS
    response = _upload(client, _release_csv(rows), "release.csv")
    assert response.status_code == 200
    assert response.json()["match"]["matched"] == 1

    row = (await db_session.execute(select(DefaulterRow))).scalars().one()
    assert row.pers_no == "99999999"  # file value kept verbatim
    assert row.personnel_id is not None  # matched via the name


# ============================================================================
# Row annotations (reason / remarks)
# ============================================================================


async def _current_row_id(client) -> int:
    return client.get("/api/v1/ippt/defaulters").json()["rows"][0]["id"]


async def test_patch_reason_and_remarks(client_as, db_session, active_roll):
    client = await client_as("super_admin")
    assert (
        _upload(
            client, _release_csv([_pending_row(*ROSTER[0])]), "release.csv"
        ).status_code
        == 200
    )
    row_id = await _current_row_id(client)

    response = client.patch(
        f"/api/v1/ippt/defaulters/rows/{row_id}",
        json={"reason": "attitude_issue", "remarks": "  counselor session booked "},
    )
    assert response.status_code == 200
    assert response.json()["reason"] == "attitude_issue"
    assert response.json()["remarks"] == "counselor session booked"

    # Clearing: empty strings store NULL.
    response = client.patch(
        f"/api/v1/ippt/defaulters/rows/{row_id}",
        json={"reason": "", "remarks": ""},
    )
    assert response.status_code == 200
    assert response.json()["reason"] is None
    assert response.json()["remarks"] is None

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity_type == "defaulter_row")
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 2
    assert json.loads(audit[0].description)["reason"] == "attitude_issue"


async def test_patch_validation(client_as, db_session, active_roll):
    client = await client_as("super_admin")
    assert (
        _upload(
            client, _release_csv([_pending_row(*ROSTER[0])]), "release.csv"
        ).status_code
        == 200
    )
    row_id = await _current_row_id(client)

    bad_reason = client.patch(
        f"/api/v1/ippt/defaulters/rows/{row_id}", json={"reason": "because"}
    )
    assert bad_reason.status_code == 422  # pydantic Literal

    unknown = client.patch("/api/v1/ippt/defaulters/rows/999999", json={"remarks": "x"})
    assert unknown.status_code == 404


async def test_patch_rejects_archived_quarter_rows(client_as, db_session, active_roll):
    client = await client_as("super_admin")
    assert (
        _upload(
            client,
            _release_csv(
                [_pending_row(*ROSTER[0])], archived=[_archived_row(*ROSTER[1])]
            ),
            "release.csv",
        ).status_code
        == 200
    )
    old_report = (await db_session.execute(select(DefaulterReport))).scalars().one()
    assert (
        _upload(
            client, _release_csv([_pending_row(*ROSTER[2])]), "next.csv", 2027, 1
        ).status_code
        == 200
    )

    archived_row = (
        (
            await db_session.execute(
                select(DefaulterRow).where(
                    DefaulterRow.report_id == old_report.id,
                    DefaulterRow.row_type == "archived",
                )
            )
        )
        .scalars()
        .one()
    )
    response = client.patch(
        f"/api/v1/ippt/defaulters/rows/{archived_row.id}", json={"remarks": "x"}
    )
    assert response.status_code == 409
    assert "archived" in response.json()["detail"]


async def test_patch_requires_admin(client_as, active_roll):
    client = await client_as("super_admin")
    assert (
        _upload(
            client, _release_csv([_pending_row(*ROSTER[0])]), "release.csv"
        ).status_code
        == 200
    )
    row_id = await _current_row_id(client)

    plain = await client_as("user")
    response = plain.patch(
        f"/api/v1/ippt/defaulters/rows/{row_id}", json={"remarks": "x"}
    )
    assert response.status_code == 403


# ============================================================================
# Pages
# ============================================================================


async def test_dashboard_shows_defaulter_table_below_filters(client_as, active_roll):
    client = await client_as("super_admin")
    assert (
        _upload(
            client,
            _release_csv(
                [
                    _pending_row(*ROSTER[0]),
                    _pending_row(*ROSTER[1], window="01/10/2025 - 30/09/2026"),
                ]
            ),
            "release.csv",
        ).status_code
        == 200
    )

    page = await client_as("admin")
    response = page.get("/ippt/dashboard")
    assert response.status_code == 200
    text = response.text
    assert "Defaulters — Q4 2026" in text
    # Below the filter bar (PR #103): after the filter/sort form, before
    # the first roster section. The card is a <details>, open by default.
    assert 'id="defaulterCard" open' in text
    assert text.index("Defaulters — Q4 2026") > text.index('class="ippt-filters"')
    assert text.index("Defaulters — Q4 2026") < text.index('class="card ippt-section"')
    # Display columns + the editable controls.
    for needle in (
        ">Subunit<",
        ">Defaulter Count<",
        ">Reason for defaulting<",
        ">Remarks<",
        "ARAVIND NAIDU S/O GOVINDARAJOO",
        "25/08/2025 - 24/08/2026",
        'value="hsp"',
    ):
        assert needle in text


async def test_dashboard_without_defaulter_data(client_as, active_roll):
    page = await client_as("admin")
    response = page.get("/ippt/dashboard")
    assert response.status_code == 200
    assert "Defaulters —" not in response.text


async def test_upload_page_shows_defaulter_cards(client_as, active_roll):
    client = await client_as("super_admin")
    assert (
        _upload(
            client,
            _release_csv(
                [_pending_row(*ROSTER[0])], archived=[_archived_row(*ROSTER[1])]
            ),
            "release.csv",
        ).status_code
        == 200
    )

    response = client.get("/ippt/upload")
    assert response.status_code == 200
    text = response.text
    assert "Upload the defaulter report" in text
    assert 'id="defaulterQuarter"' in text
    # Workyear quarters (the workyear runs 1 Apr – 31 Mar) — the option
    # labels pin the mapping; corrected 2026-11-08 after shipping the
    # calendar-quarter reading by mistake.
    assert '<option value="1">Q1 (Apr–Jun)</option>' in text
    assert '<option value="4">Q4 (Jan–Mar)</option>' in text
    assert "Defaulter quarters" in text
    assert "Q4 2026" in text
    assert "current" in text

    # Admins get the no-access response (same as the rest of the page).
    admin_page = await client_as("admin")
    response = admin_page.get("/ippt/upload")
    assert response.status_code == 403
    assert "Upload the defaulter report" not in response.text


# ============================================================================
# Purge
# ============================================================================


async def test_purge_covers_defaulter_tables():
    names = {model.__tablename__ for model in PURGE_TABLES}
    assert {
        "defaulter_reports",
        "defaulter_rows",
        "defaulter_quarantined_rows",
    } <= names
