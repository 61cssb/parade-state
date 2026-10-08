"""Seed a fresh E2E database: super-admin session + attendance-active roll.

Invoked as a subprocess by tests/e2e/conftest.py so the seeding runs in a
clean interpreter against the test database:

    python tests/e2e/seed_helper.py sqlite+aiosqlite:///<path>

The nominal roll is built from ``fixtures/ippt/`` when present (every
report name becomes a roll row, so name matching exercises the full
spine); otherwise a small deterministic roster is used instead. Roll
insertion is direct ORM seeding — the CSV upload pipeline has its own
integration coverage.
"""

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "fixtures" / "ippt"

SESSION_TOKEN = "ippt-e2e-token"
SEED_EMAIL = "ippt-e2e@example.com"

FALLBACK_ROSTER = [
    ("CPL", "E2E FALLBACK PERSON", "BN HQ"),
    ("PTE", "E2E SECOND PERSON", "MEDICAL COY"),
]


def roster_from_fixtures() -> list[tuple[str, str, str]]:
    """(rank, full_name, sub_unit) per unique name across the six reports."""
    from parade_state.utils.ippt_reports import parse_report_file

    roster: dict[str, tuple[str, str, str]] = {}
    for path in sorted(FIXTURES.glob("*.csv")):
        parsed = parse_report_file(path.name, path.read_text())
        for row in parsed.rows:
            roster.setdefault(
                row.full_name.upper(), (row.rank, row.full_name, row.sub_unit)
            )
    return list(roster.values())


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: seed_helper.py <database_url>", file=sys.stderr)
        return 2
    database_url = sys.argv[1]

    from parade_state.db import get_session_maker, init_database
    from parade_state.models import NominalRoll, Personnel, User, UserSession
    from parade_state.utils import utc_dt

    init_database(database_url)
    maker = get_session_maker()
    roster = roster_from_fixtures() or FALLBACK_ROSTER

    async def seed() -> None:
        async with maker() as db:
            async with db.begin():
                super_admin = User(
                    email=SEED_EMAIL,
                    name="IPPT E2E",
                    role="super_admin",
                    status="active",
                )
                db.add(super_admin)
                await db.flush()
                db.add(
                    UserSession(
                        token=SESSION_TOKEN,
                        user_id=str(super_admin.id),
                        email=super_admin.email,
                        name=super_admin.name,
                        role=super_admin.role,
                        expires_at=utc_dt.ensure_naive(
                            utc_dt.add_timedelta(utc_dt.utcnow(), days=365)
                        ),
                    )
                )
                roll = NominalRoll(
                    caa=date(2026, 9, 10),
                    csv_hash="ippt-e2e-seed",
                    personnel_count=len(roster),
                    uploaded_by=str(super_admin.id),
                    label="IPPT E2E Roll",
                )
                db.add(roll)
                await db.flush()
                for index, (rank, full_name, sub_unit) in enumerate(roster):
                    db.add(
                        Personnel(
                            nominal_roll_id=str(roll.id),
                            pers_no=f"7000000{index:02d}",
                            rank=rank,
                            category="Officer"
                            if rank.startswith(("CPT", "LTA", "MAJ", "3WO"))
                            else "WOSE",
                            full_name=full_name,
                            unit="DK314",
                            sub_unit_1=sub_unit,
                            created_by=str(super_admin.id),
                        )
                    )
                roll.attendance_active = True
        print(
            f"seeded: super-admin session {SESSION_TOKEN}, roll with {len(roster)} personnel"
        )

    import asyncio

    asyncio.run(seed())
    return 0


if __name__ == "__main__":
    sys.exit(main())
