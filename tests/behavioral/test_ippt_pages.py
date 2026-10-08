"""Behavioral tests: IPPT pages (dashboard, window, upload).

Page-level acceptance: GET the page, assert the HTML renders the right
sections for the caller's role, then drive the upload API and confirm
the pages reflect the ingested state. Flag-off 404s are covered in
tests/integration/test_ippt_api.py.
"""

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.session import create_user_session
from parade_state.models import NominalRoll, Personnel, User
from parade_state.utils.cookies import AUTH_COOKIE_NAME

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


async def _sign_in(client: TestClient, db_session: AsyncSession, user: User) -> None:
    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )
    await db_session.commit()
    client.cookies.set(AUTH_COOKIE_NAME, session.token)


async def _make_user(db_session: AsyncSession, role: str, email: str) -> User:
    user = User(email=email, name=email.split("@")[0], role=role, status="active")
    db_session.add(user)
    await db_session.commit()
    return user


def _upload_files(
    rows: dict[str, list[str]],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    return _upload_files_for_date("20260911", rows)


def _upload_files_for_date(
    d: str,
    rows: dict[str, list[str]],
) -> list[tuple[str, tuple[str, bytes, str]]]:

    def build(header: str, kind: str) -> bytes:
        return ("\n".join([header, *rows.get(kind, [])]) + "\n").encode()

    names = {
        f"IPPT_COMPLETED_{d}.csv": (FAMILY_A_HEADER, "ippt_completed"),
        f"IPPT_FAILED_{d}.csv": (FAMILY_B_FAILED_HEADER, "ippt_failed"),
        f"IPPT_NOT_ATTEMPTED_{d}.csv": (FAMILY_C_HEADER, "ippt_not_attempted"),
        f"NSFIT_COMPLETED_{d}.csv": (FAMILY_A_HEADER, "nsfit_completed"),
        f"NSFIT_IN_PROGRESS_{d}.csv": (FAMILY_B_NSFIT_HEADER, "nsfit_in_progress"),
        f"NSFIT_NOT_STARTED_{d}.csv": (FAMILY_C_HEADER, "nsfit_not_started"),
    }
    return [
        ("files", (name, build(header, kind), "text/csv"))
        for name, (header, kind) in names.items()
    ]


async def _seed_roll_with_match(db_session: AsyncSession) -> None:
    admin = await _make_user(db_session, "super_admin", "seed-ippt@example.com")
    roll = NominalRoll(
        caa=date(2026, 1, 1),
        csv_hash="ippt-behavioral",
        personnel_count=1,
        uploaded_by=str(admin.id),
    )
    db_session.add(roll)
    await db_session.flush()
    db_session.add(
        Personnel(
            nominal_roll_id=str(roll.id),
            pers_no="80000001",
            rank="CPL",
            category="WOSE",
            full_name="MATCHED PERSON",
            unit="DK314",
            sub_unit_1="BN HQ",
            created_by=str(admin.id),
        )
    )
    roll.attendance_active = True
    await db_session.commit()


class TestIpptPages:
    async def test_dashboard_empty_state(
        self, client: TestClient, db_session: AsyncSession
    ):
        sa = await _make_user(db_session, "super_admin", "sa-ippt@example.com")
        await _sign_in(client, db_session, sa)

        response = client.get("/ippt/dashboard")
        assert response.status_code == 200
        body = response.text
        assert "IPPT Monitoring" in body
        assert 'href="/ippt/dashboard"' in body  # nav entry
        # The three colour-coded tabs always render; the section shows the
        # active (default: most urgent) tier only.
        assert "3 months before window close" in body
        assert "6 months before window close" in body
        assert "9 months before window close" in body
        assert "#dc2626" in body and "#ea580c" in body and "#ca8a04" in body
        # Data-quality status line below the intro, not its own box.
        assert "Latest CAA" in body
        assert "quarantined rows" in body
        assert "2026" not in body  # no data yet: the date shows a dash
        assert "Snapshot history" in body

    async def test_upload_page_super_admin_only(
        self, client: TestClient, db_session: AsyncSession
    ):
        sa = await _make_user(db_session, "super_admin", "sa-upload@example.com")
        admin = await _make_user(db_session, "admin", "admin-ippt@example.com")

        await _sign_in(client, db_session, sa)
        page = client.get("/ippt/upload")
        assert page.status_code == 200
        assert "Upload the six reports" in page.text
        assert "Quarantined rows" in page.text

        await _sign_in(client, db_session, admin)
        forbidden = client.get("/ippt/upload")
        assert forbidden.status_code == 403
        assert (
            "no access" in forbidden.text.lower() or "No Permission" in forbidden.text
        )

    async def test_pages_render_ingested_state(
        self, client: TestClient, db_session: AsyncSession
    ):
        await _seed_roll_with_match(db_session)
        sa = await _make_user(db_session, "super_admin", "sa-flow@example.com")
        await _sign_in(client, db_session, sa)

        rows = {
            "ippt_failed": ["CPL   ,MATCHED PERSON,DK314,BN HQ,4,Fail,-,-,-"],
            "ippt_not_attempted": [
                "PTE   ,UNMATCHED PERSON,DK314,BN HQ,300,Yes,Pending,-,-"
            ],
        }
        response = client.post("/api/v1/ippt/snapshots", files=_upload_files(rows))
        assert response.status_code == 200

        dashboard = client.get("/ippt/dashboard")
        assert dashboard.status_code == 200
        body = dashboard.text
        # The 4-day-window person renders on the default (most urgent) tab.
        assert "MATCHED PERSON" in body
        assert "not in nominal roll" in body  # match-quality badge
        assert "most urgent" in body  # default tab = 3 months before close
        assert "Snapshot history" in body
        assert "2026-09-11" in body

        # The ?tier= filter switches the active section: the 300-day-window
        # person (9 months to close, unbooked) sits in the least-urgent
        # tier, not on the default tab.
        assert "UNMATCHED PERSON" not in body
        tier9_view = client.get("/ippt/dashboard?tier=tier_9")
        assert tier9_view.status_code == 200
        assert "least urgent" in tier9_view.text
        assert "UNMATCHED PERSON" in tier9_view.text
        # Unknown values fall back to the default (most urgent) tab.
        fallback = client.get("/ippt/dashboard?tier=bogus")
        assert "most urgent" in fallback.text

        # A second snapshot months later: MATCHED PERSON (absent, window
        # closed) gains a staleness badge and a dimmed row. UNMATCHED PERSON
        # rolled onto mandatory NS FIT — their latest observation (Family B)
        # carries no FFI, so the displayed Pending screening is stamped with
        # its September vintage.
        rows_oct = {
            "nsfit_in_progress": [
                "CPL   ,UNMATCHED PERSON,DK314,MEDICAL COY,240,3,-,-,-",
            ],
        }
        response = client.post(
            "/api/v1/ippt/snapshots",
            files=_upload_files_for_date("20261012", rows_oct),
        )
        assert response.status_code == 200

        dashboard = client.get("/ippt/dashboard")
        body = dashboard.text
        assert "last seen 2026-09-11" in body  # staleness badge
        assert "opacity: 0.55" in body  # dimmed stale row
        tier6_view = client.get("/ippt/dashboard?tier=tier_6")
        assert tier6_view.status_code == 200
        assert "UNMATCHED PERSON" in tier6_view.text  # mandatory FIT chased

        # The window page links from the dashboard and renders the trajectory.
        window_page = client.get("/ippt/window/00000000-0000-0000-0000-000000000000")
        assert window_page.status_code == 404  # unknown personnel id
        assert "No IPPT monitoring data" in window_page.text

        # A matched person's page: find the personnel id via the dashboard API.
        detail_payload = client.get("/api/v1/ippt/dashboard").json()
        matched = next(
            e
            for e in detail_payload["servicemen"]
            if e["full_name"] == "MATCHED PERSON"
        )
        page = client.get(f"/ippt/window/{matched['personnel_id']}")
        assert page.status_code == 200
        assert "MATCHED PERSON" in page.text
        assert "Trajectory" in page.text
        assert "Attempted — failed (can re-attempt)" in page.text
        assert "Health screening (FFI)" in page.text

    async def test_upload_page_lists_history_and_quarantine(
        self, client: TestClient, db_session: AsyncSession
    ):
        await _seed_roll_with_match(db_session)
        sa = await _make_user(db_session, "super_admin", "sa-quar@example.com")
        await _sign_in(client, db_session, sa)

        rows = {
            "ippt_failed": [
                "CPL   ,MATCHED PERSON,DK314,BN HQ,4,Fail,-,-,-",
                "CPL   ,BAD CLOSE,DK314,BN HQ,999,Fail,-,-,-",
            ],
        }
        assert (
            client.post("/api/v1/ippt/snapshots", files=_upload_files(rows)).status_code
            == 200
        )

        page = client.get("/ippt/upload")
        assert page.status_code == 200
        assert "Ingested snapshots" in page.text
        assert "2026-09-11" in page.text
        assert "Quarantined rows" in page.text
        assert "outside [0, 366]" in page.text
