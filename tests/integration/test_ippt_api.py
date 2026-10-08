"""IPPT monitoring API integration tests (FEATURE_IPPT).

Covers the §4 atomicity contract (complete six-file set, one report
date, replace-on-re-ingest, nothing written on rejection), the §3.2
identity matching, window derivation/backfill, quarantine, permissions,
flag gating, and the read endpoints. The canonical-fixture test reuses
``fixtures/ippt/`` and skips when the gitignored directory is absent.
"""

import csv
import io
import json
from datetime import date

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.api.admin_purge import PURGE_TABLES
from parade_state.models import (
    AuditLog,
    IpptServiceman,
    IpptSnapshot,
    IpptStateObservation,
    IpptWindow,
    NominalRoll,
    Personnel,
    User,
)
from parade_state.utils.ippt_reports import parse_filename, parse_report_file

REPO_ROOT = __file__.rsplit("/", 3)[0]


def _fixtures_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent / "fixtures" / "ippt"


# ============================================================================
# Helpers: synthetic six-file snapshots
# ============================================================================

FAMILY_A_HEADER = "Rank,Name,Unit,Sub-unit,IPPT status,FIT sessions completed"
FAMILY_B_FAILED_HEADER = (
    "Rank,Name,Unit,Sub-unit,Window close,IPPT status,FIT sessions completed,"
    "Reminder sent,Last sent"
)
FAMILY_B_NSFIT_HEADER = (
    "Rank,Name,Unit,Sub-unit,Window close,FIT sessions completed,IPPT status,"
    "Reminder sent,Last sent"
)
FAMILY_C_HEADER = (
    "Rank,Name,Unit,Sub-unit,Window close,Booked Status,FFI,Reminder sent,Last sent"
)


def _synthetic_snapshot(
    report_date: str, rows: dict[str, list[str]]
) -> dict[str, bytes]:
    """Build the six canonical files for a report date.

    ``rows`` maps file_kind → list of CSV data lines; kinds omitted get
    a header-only file.
    """

    def build(header: str, kind: str) -> bytes:
        body = "\n".join([header, *rows.get(kind, [])]) + "\n"
        return body.encode()

    return {
        "IPPT_COMPLETED_{d}.csv".format(d=report_date.replace("-", "")): build(
            FAMILY_A_HEADER, "ippt_completed"
        ),
        "IPPT_FAILED_{d}.csv".format(d=report_date.replace("-", "")): build(
            FAMILY_B_FAILED_HEADER, "ippt_failed"
        ),
        "IPPT_NOT_ATTEMPTED_{d}.csv".format(d=report_date.replace("-", "")): build(
            FAMILY_C_HEADER, "ippt_not_attempted"
        ),
        "NSFIT_COMPLETED_{d}.csv".format(d=report_date.replace("-", "")): build(
            FAMILY_A_HEADER, "nsfit_completed"
        ),
        "NSFIT_IN_PROGRESS_{d}.csv".format(d=report_date.replace("-", "")): build(
            FAMILY_B_NSFIT_HEADER, "nsfit_in_progress"
        ),
        "NSFIT_NOT_STARTED_{d}.csv".format(d=report_date.replace("-", "")): build(
            FAMILY_C_HEADER, "nsfit_not_started"
        ),
    }


def _multipart(files: dict[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, content, "text/csv")) for name, content in files.items()]


# ============================================================================
# Helpers: .xlsx variants of the same snapshots
# ============================================================================

# Numeric columns carry real numbers in the unit's Excel exports.
_NUMERIC_COLUMNS: dict[str, set[int]] = {
    FAMILY_A_HEADER: {5},  # FIT sessions completed
    FAMILY_B_FAILED_HEADER: {4, 6},  # Window close, FIT sessions completed
    FAMILY_B_NSFIT_HEADER: {4, 5},
    FAMILY_C_HEADER: {4},  # Window close
}


def _xlsx_file(header: str, kind: str, rows: dict[str, list[str]]) -> bytes:
    """One canonical report as a one-sheet .xlsx workbook."""
    buffer = io.BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None  # a new Workbook always has one sheet
    sheet.append(next(csv.reader([header])))
    for line in rows.get(kind, []):
        sheet.append(
            [
                int(value)
                if index in _NUMERIC_COLUMNS[header] and value != "-"
                else value
                for index, value in enumerate(next(csv.reader([line])))
            ]
        )
    workbook.save(buffer)
    return buffer.getvalue()


def _synthetic_xlsx_snapshot(
    report_date: str, rows: dict[str, list[str]]
) -> dict[str, bytes]:
    """The six canonical files as .xlsx, same shapes as the CSV builder."""
    d = report_date.replace("-", "")
    return {
        f"IPPT_COMPLETED_{d}.xlsx": _xlsx_file(FAMILY_A_HEADER, "ippt_completed", rows),
        f"IPPT_FAILED_{d}.xlsx": _xlsx_file(
            FAMILY_B_FAILED_HEADER, "ippt_failed", rows
        ),
        f"IPPT_NOT_ATTEMPTED_{d}.xlsx": _xlsx_file(
            FAMILY_C_HEADER, "ippt_not_attempted", rows
        ),
        f"NSFIT_COMPLETED_{d}.xlsx": _xlsx_file(
            FAMILY_A_HEADER, "nsfit_completed", rows
        ),
        f"NSFIT_IN_PROGRESS_{d}.xlsx": _xlsx_file(
            FAMILY_B_NSFIT_HEADER, "nsfit_in_progress", rows
        ),
        f"NSFIT_NOT_STARTED_{d}.xlsx": _xlsx_file(
            FAMILY_C_HEADER, "nsfit_not_started", rows
        ),
    }


async def _seed_active_roll(db_session: AsyncSession, names: list[str]) -> None:
    """Attendance-active roll with one WOSE row per given full name."""
    admin = await db_session.get(User, "super-admin-test-id")
    roll = NominalRoll(
        caa=date(2026, 1, 1),
        csv_hash="ippt-test-roll",
        personnel_count=len(names),
        uploaded_by=str(admin.id),
    )
    db_session.add(roll)
    await db_session.flush()
    for index, name in enumerate(names):
        db_session.add(
            Personnel(
                nominal_roll_id=str(roll.id),
                pers_no=f"9000000{index}",
                rank="CPL",
                category="WOSE",
                full_name=name,
                unit="DK314",
                sub_unit_1="BN HQ",
                created_by=str(admin.id),
            )
        )
    roll.attendance_active = True
    await db_session.commit()


# ============================================================================
# Flag gating
# ============================================================================


async def test_flag_off_hides_ippt_routes(client_as, monkeypatch):
    client = await client_as("super_admin")
    from parade_state.config import get_settings
    from parade_state.main import app as main_app

    for settings_obj in {get_settings(), main_app.state.settings}:
        monkeypatch.setattr(settings_obj, "FEATURE_IPPT", False)
    try:
        assert client.get("/api/v1/ippt/dashboard").status_code == 404
        assert client.get("/ippt/dashboard").status_code == 404
        assert client.get("/ippt/upload").status_code == 404
    finally:
        for settings_obj in {get_settings(), main_app.state.settings}:
            monkeypatch.setattr(settings_obj, "FEATURE_IPPT", True)


# ============================================================================
# Upload permissions & atomicity
# ============================================================================


async def test_upload_requires_super_admin(client_as):
    admin = await client_as("admin")
    response = admin.post(
        "/api/v1/ippt/snapshots",
        files=_multipart(_synthetic_snapshot("2026-09-11", {})),
    )
    assert response.status_code == 403

    anonymous = await client_as("admin")
    anonymous.cookies.clear()
    assert (
        anonymous.post(
            "/api/v1/ippt/snapshots",
            files=_multipart(_synthetic_snapshot("2026-09-11", {})),
        ).status_code
        == 401
    )


async def test_upload_rejects_incomplete_set(client_as, db_session):
    client = await client_as("super_admin")
    files = _synthetic_snapshot("2026-09-11", {})
    five = dict(list(files.items())[:5])
    response = client.post("/api/v1/ippt/snapshots", files=_multipart(five))
    assert response.status_code == 422
    assert "full set of six" in response.json()["detail"]
    assert (await db_session.execute(select(IpptSnapshot))).scalars().all() == []


async def test_upload_rejects_mixed_dates(client_as, db_session):
    client = await client_as("super_admin")
    files = _synthetic_snapshot("2026-09-11", {})
    files["IPPT_FAILED_20261012.csv"] = files.pop("IPPT_FAILED_20260911.csv")
    response = client.post("/api/v1/ippt/snapshots", files=_multipart(files))
    assert response.status_code == 422
    assert "same report date" in response.json()["detail"]
    assert (await db_session.execute(select(IpptSnapshot))).scalars().all() == []


async def test_upload_rejects_non_canonical_name(client_as):
    client = await client_as("super_admin")
    files = _synthetic_snapshot("2026-09-11", {})
    files["report.csv"] = files.pop("IPPT_COMPLETED_20260911.csv")
    response = client.post("/api/v1/ippt/snapshots", files=_multipart(files))
    assert response.status_code == 422
    assert "does not match" in response.json()["detail"]


# ============================================================================
# Excel (.xlsx) uploads
# ============================================================================


async def test_upload_ingests_full_xlsx_snapshot(client_as, db_session):
    await _seed_active_roll(db_session, ["XLSX PERSON"])
    client = await client_as("super_admin")
    rows = {"ippt_failed": ["CPL   ,XLSX PERSON,DK314,BN HQ,12,Fail,-,-,-"]}
    response = client.post(
        "/api/v1/ippt/snapshots",
        files=_multipart(_synthetic_xlsx_snapshot("2026-09-11", rows)),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["per_kind"]["ippt_failed"] == {"rows": 1, "quarantined": 0}

    observation = (await db_session.execute(select(IpptStateObservation))).scalar_one()
    assert observation.state == "failed_can_reattempt"
    assert observation.window_close_days == 12  # numeric cell, not "12" text
    assert observation.fit_sessions is None  # '-' sentinel survived the round-trip


async def test_upload_accepts_mixed_csv_and_xlsx(client_as, db_session):
    await _seed_active_roll(db_session, ["CSV PERSON", "XLSX PERSON 2"])
    client = await client_as("super_admin")
    files = _synthetic_snapshot(
        "2026-09-11",
        {"ippt_not_attempted": ["PTE   ,CSV PERSON,DK314,BN HQ,300,No,NA,-,-"]},
    )
    files.pop("IPPT_COMPLETED_20260911.csv")
    files["IPPT_COMPLETED_20260911.xlsx"] = _xlsx_file(
        FAMILY_A_HEADER, "ippt_completed", {}
    )
    files.pop("NSFIT_NOT_STARTED_20260911.csv")
    files["NSFIT_NOT_STARTED_20260911.xlsx"] = _xlsx_file(
        FAMILY_C_HEADER,
        "nsfit_not_started",
        {"nsfit_not_started": ["LCP   ,XLSX PERSON 2,DK314,MEDICAL COY,20,-,-,-,-"]},
    )
    response = client.post("/api/v1/ippt/snapshots", files=_multipart(files))
    assert response.status_code == 200
    assert response.json()["total_rows"] == 2


async def test_upload_rejects_corrupt_xlsx_file(client_as, db_session):
    client = await client_as("super_admin")
    files = _synthetic_xlsx_snapshot("2026-09-11", {})
    files["IPPT_COMPLETED_20260911.xlsx"] = b"definitely not a zip"
    response = client.post("/api/v1/ippt/snapshots", files=_multipart(files))
    assert response.status_code == 422
    assert "unreadable .xlsx" in response.json()["detail"]
    assert (await db_session.execute(select(IpptSnapshot))).scalars().all() == []


# ============================================================================
# Ingest semantics on a synthetic snapshot
# ============================================================================


async def test_upload_ingests_and_matches(client_as, db_session):
    await _seed_active_roll(
        db_session,
        ["OUTSTANDING PERSON", "HOMONYM PERSON", "HOMONYM PERSON"],
    )
    # The second HOMONYM PERSON must be a distinct roll row (homonyms).
    homonyms = (
        await db_session.execute(
            select(Personnel).where(Personnel.full_name == "HOMONYM PERSON")
        )
    ).scalars()
    second = list(homonyms)[1]
    second.full_name = "HOMONYM PERSON"  # unchanged; uniqueness not enforced

    rows = {
        "ippt_failed": ["CPL   ,OUTSTANDING PERSON,DK314,BN HQ,12,Fail,-,-,-"],
        "ippt_not_attempted": [
            "PTE   ,HOMONYM PERSON,DK314,BN HQ,30,No,NA,-,-",
            "PTE   ,UNKNOWN PERSON,DK314,SECURITY COY,45,No,NA,-,-",
        ],
        "nsfit_not_started": ["LCP   ,HOMONYM PERSON,DK314,MEDICAL COY,20,No,Fit,-,-"],
    }
    client = await client_as("super_admin")
    response = client.post(
        "/api/v1/ippt/snapshots",
        files=_multipart(_synthetic_snapshot("2026-09-11", rows)),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["report_date"] == "2026-09-11"
    assert payload["replaced"] is False
    assert payload["total_rows"] == 4
    assert payload["match"] == {"matched": 1, "unmatched": 1, "ambiguous": 1}

    servicemen = {
        s.full_name_norm: s
        for s in (await db_session.execute(select(IpptServiceman))).scalars()
    }
    assert servicemen["OUTSTANDING PERSON"].personnel_id is not None
    assert servicemen["OUTSTANDING PERSON"].match_status == "matched"
    assert servicemen["UNKNOWN PERSON"].match_status == "unmatched"
    assert servicemen["HOMONYM PERSON"].personnel_id is None
    assert servicemen["HOMONYM PERSON"].match_status == "ambiguous"

    # Audit log entry for the ingest.
    audit = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.entity_type == "ippt_snapshot")
        )
    ).scalar_one()
    assert audit.entity_id == "2026-09-11"


async def test_multi_snapshot_display_effects(client_as, db_session):
    """Staleness badges, FFI vintage, and effective match status across two
    snapshots (the decided 2026-10-08 display behaviours)."""
    await _seed_active_roll(db_session, ["STALE PERSON", "FFI PERSON", "LINKED PERSON"])
    client = await client_as("super_admin")

    first = _synthetic_snapshot(
        "2026-09-11",
        {
            "ippt_failed": ["CPL   ,STALE PERSON,DK314,BN HQ,300,Fail,-,-,-"],
            "ippt_not_attempted": [
                "PTE   ,FFI PERSON,DK314,BN HQ,320,No,Pending,-,-",
                "CPL   ,LINKED PERSON,DK314,BN HQ,320,No,NA,-,-",
            ],
        },
    )
    assert (
        client.post("/api/v1/ippt/snapshots", files=_multipart(first)).status_code
        == 200
    )

    second = _synthetic_snapshot(
        "2026-10-12",
        {"ippt_failed": ["PTE   ,FFI PERSON,DK314,BN HQ,280,Fail,-,-,-"]},
    )
    assert (
        client.post("/api/v1/ippt/snapshots", files=_multipart(second)).status_code
        == 200
    )

    dashboard = client.get("/api/v1/ippt/dashboard").json()
    by_name = {e["full_name"]: e for e in dashboard["servicemen"]}

    # STALE PERSON is absent from October: kept, flagged stale at the
    # September observation.
    stale = by_name["STALE PERSON"]
    assert stale["report_date"] == "2026-09-11"
    assert stale["stale"] is True

    # FFI PERSON's latest observation (October, Family B) carries no FFI;
    # the displayed value is the September one with its vintage.
    ffi = by_name["FFI PERSON"]
    assert ffi["report_date"] == "2026-10-12"
    # The displayed FFI stays the latest known value (September's), per the
    # §6.3 caveat — without a vintage stamp (dropped 2026-10-08).
    assert ffi["ffi"] == "pending"
    assert ffi["stale"] is False

    # LINKED PERSON is absent from October too, but was fresh-matched.
    linked = by_name["LINKED PERSON"]
    assert linked["stale"] is True
    assert linked["match_status"] == "matched"

    # Deleting the Personnel row (e.g. roll replacement) degrades the
    # displayed match without any re-ingest: the link no longer resolves.
    personnel = (
        await db_session.execute(
            select(Personnel).where(Personnel.full_name == "LINKED PERSON")
        )
    ).scalar_one()
    await db_session.delete(personnel)
    await db_session.commit()

    dashboard = client.get("/api/v1/ippt/dashboard").json()
    linked = next(
        e for e in dashboard["servicemen"] if e["full_name"] == "LINKED PERSON"
    )
    assert linked["match_status"] == "unmatched"
    assert linked["personnel_id"] is None
    assert dashboard["summary"]["match"]["matched"] == 2
    assert dashboard["summary"]["match"]["unmatched"] == 1


async def test_window_derivation_and_completed_backfill(client_as, db_session):
    await _seed_active_roll(db_session, ["WINDOW PERSON"])
    client = await client_as("super_admin")

    # Report 1: outstanding row, closes in 12 days.
    first = _synthetic_snapshot(
        "2026-09-11",
        {"ippt_failed": ["CPL   ,WINDOW PERSON,DK314,BN HQ,12,Fail,-,-,-"]},
    )
    assert (
        client.post("/api/v1/ippt/snapshots", files=_multipart(first)).status_code
        == 200
    )

    observation = (await db_session.execute(select(IpptStateObservation))).scalar_one()
    assert observation.window_id is not None
    window = await db_session.get(IpptWindow, observation.window_id)
    assert window.window_end == date(2026, 9, 23)  # report + 12 days
    assert window.window_start == date(2025, 9, 24)  # (end + 1 day) - 1 year

    # Report 2 (still inside the window, before the 2026-09-23 close):
    # the person completed — no window close on the row; the observation
    # must backfill report 1's window.
    second = _synthetic_snapshot(
        "2026-09-20",
        {"ippt_completed": ["CPL   ,WINDOW PERSON,DK314,BN HQ,Pass,-"]},
    )
    assert (
        client.post("/api/v1/ippt/snapshots", files=_multipart(second)).status_code
        == 200
    )

    windows = (await db_session.execute(select(IpptWindow))).scalars().all()
    assert len(windows) == 1  # same window, not a new one
    completed = (
        await db_session.execute(
            select(IpptStateObservation).where(
                IpptStateObservation.state == "met_award"
            )
        )
    ).scalar_one()
    assert completed.window_id == observation.window_id


async def test_reingest_replaces_same_date(client_as, db_session):
    await _seed_active_roll(db_session, ["REPLACE PERSON"])
    client = await client_as("super_admin")

    rows = {"ippt_failed": ["CPL   ,REPLACE PERSON,DK314,BN HQ,12,Fail,-,-,-"]}
    first = client.post(
        "/api/v1/ippt/snapshots",
        files=_multipart(_synthetic_snapshot("2026-09-11", rows)),
    )
    assert first.status_code == 200
    assert first.json()["replaced"] is False

    # Same date, different data: full replace, no duplicates.
    rows_v2 = {"ippt_not_attempted": ["CPL   ,REPLACE PERSON,DK314,BN HQ,12,No,NA,-,-"]}
    second = client.post(
        "/api/v1/ippt/snapshots",
        files=_multipart(_synthetic_snapshot("2026-09-11", rows_v2)),
    )
    assert second.status_code == 200
    assert second.json()["replaced"] is True

    observations = (
        (await db_session.execute(select(IpptStateObservation))).scalars().all()
    )
    assert len(observations) == 1
    assert observations[0].state == "ippt_not_attempted"

    # The spine row survived (idempotent identity), with a retried match.
    servicemen = (await db_session.execute(select(IpptServiceman))).scalars().all()
    assert len(servicemen) == 1


async def test_quarantine_and_duplicate_across_files(client_as, db_session):
    await _seed_active_roll(db_session, [])
    client = await client_as("super_admin")

    rows = {
        "ippt_failed": [
            "CPL   ,GOOD ROW,DK314,BN HQ,12,Fail,-,-,-",
            "CPL   ,BAD ROW,DK314,BN HQ,999,Fail,-,-,-",
            "CPL   ,GOOD ROW,DK314,BN HQ,12,Fail,-,-,-",  # duplicate person
        ],
    }
    response = client.post(
        "/api/v1/ippt/snapshots",
        files=_multipart(_synthetic_snapshot("2026-09-11", rows)),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["per_kind"]["ippt_failed"] == {"rows": 2, "quarantined": 1}
    assert payload["quarantined"] == 2  # + 1 duplicate-serviceman entry

    quarantine = client.get("/api/v1/ippt/quarantine").json()
    reasons = [entry["reason"] for entry in quarantine]
    assert any("outside [0, 366]" in reason for reason in reasons)
    assert any("more than one file" in reason for reason in reasons)

    dashboard = client.get("/api/v1/ippt/dashboard").json()
    assert dashboard["summary"]["quarantined"] == 2


# ============================================================================
# Read endpoints
# ============================================================================


async def test_dashboard_and_window_endpoints(client_as, db_session):
    await _seed_active_roll(db_session, ["DASHBOARD PERSON"])
    client = await client_as("super_admin")
    rows = {
        "ippt_failed": ["CPL   ,DASHBOARD PERSON,DK314,BN HQ,4,Fail,-,-,-"],
        "ippt_not_attempted": ["PTE   ,OTHER PERSON,DK314,BN HQ,300,No,Pending,-,-"],
    }
    client.post(
        "/api/v1/ippt/snapshots",
        files=_multipart(_synthetic_snapshot("2026-09-11", rows)),
    )

    dashboard = client.get("/api/v1/ippt/dashboard").json()
    assert dashboard["summary"]["total"] == 2
    by_name = {entry["full_name"]: entry for entry in dashboard["servicemen"]}
    urgent = by_name["DASHBOARD PERSON"]
    assert urgent["state"] == "failed_can_reattempt"
    # A 12-day window at the report date = 0 months to close → most urgent.
    assert urgent["tier"] == "tier_3"
    assert urgent["months_to_close"] == 0
    assert urgent["highlighted"] is True
    assert urgent["days_to_close"] is not None
    assert urgent["personnel_id"] is not None

    # The other person has a 300-day window: 9 months to close at the
    # report date → least-urgent tier, highlighted (not booked, no prep).
    other = by_name["OTHER PERSON"]
    assert other["ffi"] == "pending"
    assert other["tier"] == "tier_9"
    assert other["highlighted"] is True

    detail = client.get(f"/api/v1/ippt/window/{urgent['personnel_id']}").json()
    assert detail["serviceman"]["full_name"] == "DASHBOARD PERSON"
    assert detail["personnel"]["id"] == urgent["personnel_id"]
    assert len(detail["trajectory"]) == 1

    assert client.get("/api/v1/ippt/window/not-a-real-id").status_code == 404

    history = client.get("/api/v1/ippt/snapshots").json()
    assert len(history) == 1
    assert history[0]["report_date"] == "2026-09-11"
    assert len(history[0]["files"]) == 6

    # "Latest CAA": the report date of the last uploaded dataset.
    assert dashboard["summary"]["latest_report_date"] == "2026-09-11"


async def test_admin_reads_allowed_upload_not(client_as):
    admin = await client_as("admin")
    assert admin.get("/api/v1/ippt/dashboard").status_code == 200
    assert admin.get("/api/v1/ippt/snapshots").status_code == 200
    assert admin.get("/api/v1/ippt/quarantine").status_code == 200
    assert (
        admin.post(
            "/api/v1/ippt/snapshots",
            files=_multipart(_synthetic_snapshot("2026-09-11", {})),
        ).status_code
        == 403
    )


# ============================================================================
# Canonical fixture acceptance (skipped when /fixtures is absent)
# ============================================================================


async def test_canonical_fixtures_end_to_end(client_as, db_session):
    fixtures = _fixtures_dir()
    if not fixtures.exists():
        pytest.skip("canonical IPPT fixtures not present — /fixtures is gitignored")

    await _seed_active_roll(
        db_session,
        ["IMRAN AHMAD TADJOUDINE", "LEW EE KENT", "MUHAMMAD ROSFIRZAN BIN IRMAN"],
    )
    client = await client_as("super_admin")

    # The fixture directory may hold several monthly snapshots; each report
    # date must be uploaded as its own six-file submission, oldest first.
    by_date: dict = {}
    for path in sorted(fixtures.glob("*.csv")):
        kind, report_date = parse_filename(path.name)
        by_date.setdefault(report_date, {})[path.name] = path.read_bytes()

    sept_files = by_date[date(2026, 9, 11)]
    response = client.post("/api/v1/ippt/snapshots", files=_multipart(sept_files))
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 284
    assert payload["quarantined"] == 0
    assert payload["match"] == {"matched": 3, "unmatched": 281, "ambiguous": 0}

    if date(2026, 10, 8) in by_date:
        oct_files = by_date[date(2026, 10, 8)]
        oct_response = client.post(
            "/api/v1/ippt/snapshots", files=_multipart(oct_files)
        )
        assert oct_response.status_code == 200
        assert oct_response.json()["total_rows"] == 288

    dashboard = client.get("/api/v1/ippt/dashboard").json()
    # Tracked servicemen = the union of names across every ingested
    # snapshot (dropped names keep their last observation).
    all_names = set()
    for files in by_date.values():
        for name, content in files.items():
            for row in parse_report_file(name, content.decode()).rows:
                all_names.add(row.full_name.upper())
    assert dashboard["summary"]["total"] == len(all_names)
    # "Latest CAA": the report date of the last uploaded dataset.
    assert dashboard["summary"]["latest_report_date"] == str(max(by_date))
    assert dashboard["summary"]["report_dates"] == len(by_date)

    # LEW EE KENT: window close 12 on 2026-09-11 → ends 2026-09-23.
    # The latest snapshot (October) keeps him in the failed state with the
    # same window; the trajectory records both observations.
    kent = next(e for e in dashboard["servicemen"] if e["full_name"] == "LEW EE KENT")
    detail = client.get(f"/api/v1/ippt/window/{kent['personnel_id']}").json()
    assert {t["report_date"] for t in detail["trajectory"]} == {
        d.isoformat() for d in by_date
    }
    # The two-month trajectory shows the FAILED → mandatory NS FIT window
    # rollover from the spec (§2.1): September's failed window closed
    # 2026-09-23; October finds him at the start of mandatory NS FIT on a
    # new birthday-anchored window.
    september = next(
        t for t in detail["trajectory"] if t["report_date"] == "2026-09-11"
    )
    assert september["state"] == "failed_can_reattempt"
    assert september["window_end"] == "2026-09-23"
    october = (
        next(t for t in detail["trajectory"] if t["report_date"] == "2026-10-08")
        if date(2026, 10, 8) in by_date
        else None
    )
    if october is not None:
        assert october["state"] == "nsfit_not_started"
        assert kent["state"] == "nsfit_not_started"
        assert (
            kent["window_end"] == "2027-09-23"
        )  # report + 350 days; start 2026-09-24 = birthday anchor
        assert kent["window_end"] != september["window_end"]
    else:
        assert kent["state"] == "failed_can_reattempt"
        assert kent["window_end"] == "2026-09-23"
    assert json.dumps(detail)  # serializable


# ============================================================================
# Purge covers the IPPT tables
# ============================================================================


def test_purge_table_order_includes_ippt_children_first():
    names = [model.__tablename__ for model in PURGE_TABLES]
    for table in (
        "ippt_state_observations",
        "ippt_health_screenings",
        "ippt_quarantined_rows",
        "ippt_windows",
        "ippt_snapshots",
        "ippt_servicemen",
    ):
        assert table in names
    # Children must precede the servicemen spine, which must precede Personnel.
    assert names.index("ippt_state_observations") < names.index("ippt_servicemen")
    assert names.index("ippt_servicemen") < names.index("personnel")
