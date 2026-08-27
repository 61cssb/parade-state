#!/usr/bin/env python
"""Visual/layout check harness: seeded SQLite app + authenticated headless browser.

WHAT THIS IS
    A single entry point for the local browser checks this project uses to
    verify HTML-view layout acceptance criteria (overlay behaviour, zero
    layout shift, mobile rendering, element placement). It:

      1. creates (or reuses) a throwaway SQLite database and migrates it,
      2. seeds it idempotently — one super-admin, one nominal roll with a
         label/remarks, five personnel,
      3. mints a real UserSession row so the browser can authenticate with
         a plain cookie (no Google OAuth dance), and starts uvicorn,
      4. drives system Chrome via Playwright, runs the requested checks and
         takes desktop/mobile screenshots.

WHY IT EXISTS (2026-08-20, issue 22 — Roll management placement)
    The acceptance criteria ("expanding overlays, no layout shift; mobile
    unaffected") need a real browser against a real running app, but the
    app's only login is Google OAuth, which headless checks can't do. The
    same throwaway seed + minted-session + uvicorn + Playwright setup was
    being hand-rewritten in /tmp every session. This script pins that flow.

USAGE
    # Layout check with expand/collapse + no-shift assertions:
    uv run --with playwright scripts/visual_check.py /nominal-roll \
        --click "details.mgmt-details summary" \
        --no-shift "div.card:has(input[name='search'])" \
        --screenshot /tmp/nr.png --mobile-screenshot /tmp/nr-mobile.png

    # Just get an authenticated local server to poke at (Ctrl-C to stop):
    uv run scripts/visual_check.py --serve-only --db local/dev.db --port 8931
    #   then set cookie session_token=<token> in your browser — the token
    #   picks the identity, so swapping it previews each role's view:
    #     visual-check-token        super-admin (everything)
    #     visual-check-admin-token  admin (Coy A / Platoon 1, Upload NR
    #                               hidden as on the dev trial)
    #     visual-check-user-token   plain user (no grants)

    # Prepare a local db without serving:
    uv run scripts/visual_check.py --db local/dev.db --fresh --no-serve

    (local/sqlite.sh is a thin wrapper over the last two.)

WHEN TO RETIRE
    Delete this when either (a) layout checks graduate into a real
    pytest-playwright suite with proper fixtures, or (b) the app grows a
    dev-only login bypass that makes the minted-session trick unnecessary.
    If it hasn't been used in a few development cycles (check git log),
    it is safe to drop — it creates nothing that outlives its process
    except the optional --db file.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uvicorn
    from sqlalchemy.ext.asyncio import AsyncSession

    from parade_state.models import User

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_TOKEN = "visual-check-token"
SEED_EMAIL = "visual-check@example.com"
ADMIN_TOKEN = "visual-check-admin-token"
ADMIN_EMAIL = "visual-check-admin@example.com"
USER_TOKEN = "visual-check-user-token"
USER_EMAIL = "visual-check-user@example.com"
CHROME_CANDIDATES = (
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)
DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
MOBILE_VIEWPORT = {"width": 390, "height": 844}
SETTLE_SECONDS = 0.2  # let layout settle after clicks


def database_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def migrate(db_path: Path) -> None:
    """Run alembic upgrade head against the check database."""
    env = {**os.environ, "DATABASE_URL": database_url(db_path)}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


async def seed(db_path: Path) -> None:
    """Seed the check database (idempotent per identity and per roll).

    Creates three identities so swapping the session cookie previews each
    role's view — auth resolves the live User row from the session, so the
    token alone decides who you are:

    - super-admin (SESSION_TOKEN): everything
    - admin (ADMIN_TOKEN): scoped to Coy A / Platoon 1 via a subunit grant
      (3 of the 5 seed personnel, odd indices)
    - plain user (USER_TOKEN): authenticated, no grants

    Also seeds the admin-trial feature-access row (upload_nr -> admin ->
    off, issue 37) so the admin preview matches the dev deployment; the
    matrix is fail-open, so an empty table would show admins Upload NR.

    Must be called with DATABASE_URL already set in the environment —
    parade_state reads it at import/startup time.
    """
    from sqlalchemy import func, select

    from parade_state.db import get_session_maker, init_database
    from parade_state.models import (
        FeatureAccess,
        NominalRoll,
        Personnel,
        User,
        UserSubunitAssignment,
    )
    from parade_state.utils import utc_dt

    init_database(database_url(db_path))
    maker = get_session_maker()
    assert maker is not None  # init_database just set it
    async with maker() as db:
        # Roll + personnel (idempotent via the seed csv_hash).
        roll = (
            await db.execute(
                select(NominalRoll).where(NominalRoll.csv_hash == "visual-check")
            )
        ).scalar_one_or_none()
        if roll is None:
            super_admin = await ensure_user(
                db, SEED_EMAIL, "Visual Check", "super_admin", SESSION_TOKEN
            )
            roll = NominalRoll(
                caa=utc_dt.date(2026, 9, 10),
                csv_hash="visual-check",
                personnel_count=5,
                uploaded_by=str(super_admin.id),
                label="Visual Check Roll",
                remarks="Keep dry.",
            )
            db.add(roll)
            await db.flush()

            ranks = ["PTE", "CPL", "LCP", "3SG", "ME4"]
            for index, rank in enumerate(ranks):
                db.add(
                    Personnel(
                        nominal_roll_id=str(roll.id),
                        pers_no=f"1000000{index + 1}",
                        rank=rank,
                        category="WOSE" if rank != "ME4" else "Officer",
                        full_name=f"Test Person {index + 1}",
                        unit="Coy A",
                        sub_unit_1=f"Platoon {index % 2 + 1}",
                        created_by=str(super_admin.id),
                    )
                )
            print(f"seed: roll CAA {roll.caa}, 5 personnel (Coy A, Platoon 1/2)")
        else:
            personnel_count = (
                await db.execute(
                    select(func.count())
                    .select_from(Personnel)
                    .where(Personnel.nominal_roll_id == str(roll.id))
                )
            ).scalar_one()
            print(
                f"seed: roll CAA {roll.caa} already present "
                f"({personnel_count} personnel)"
            )

        # Identities (idempotent per email; re-runs only add what's missing).
        super_admin = await ensure_user(
            db, SEED_EMAIL, "Visual Check", "super_admin", SESSION_TOKEN
        )
        await ensure_user(db, ADMIN_EMAIL, "Visual Check Admin", "admin", ADMIN_TOKEN)
        await ensure_user(db, USER_EMAIL, "Visual Check User", "user", USER_TOKEN)

        # Scope grant for the admin: exactly Coy A / Platoon 1. The grant is
        # added with the admin, so presence of the admin implies the grant.
        admin = (
            await db.execute(select(User).where(User.email == ADMIN_EMAIL))
        ).scalar_one()
        grant = await db.execute(
            select(UserSubunitAssignment).where(
                UserSubunitAssignment.user_id == str(admin.id)
            )
        )
        if grant.scalar_one_or_none() is None:
            db.add(
                UserSubunitAssignment(
                    user_id=str(admin.id),
                    nominal_roll_id=str(roll.id),
                    unit="Coy A",
                    sub_unit_1="Platoon 1",
                    created_by=str(super_admin.id),
                )
            )
            print("seed: admin grant Coy A / Platoon 1")

        # Feature-access matrix: the admin trial hides Upload NR from
        # plain admins (issue 37). The matrix fails open, so without this
        # row the local admin preview would show Upload NR.
        matrix_row = await db.execute(
            select(FeatureAccess).where(
                FeatureAccess.feature_key == "upload_nr",
                FeatureAccess.role == "admin",
            )
        )
        if matrix_row.scalar_one_or_none() is None:
            db.add(
                FeatureAccess(
                    feature_key="upload_nr",
                    role="admin",
                    enabled=False,
                )
            )
            print("seed: feature_access upload_nr -> admin -> off (trial)")

        await db.commit()
        print(
            f"seed: super-admin {SEED_EMAIL} ({SESSION_TOKEN}), "
            f"admin {ADMIN_EMAIL} ({ADMIN_TOKEN}, Coy A/Platoon 1, "
            f"no Upload NR), "
            f"user {USER_EMAIL} ({USER_TOKEN})"
        )


async def ensure_user(
    db: AsyncSession,
    email: str,
    name: str,
    role: str,
    token: str,
) -> User:
    """Create the user + a 365-day session if missing; return the user row."""
    from sqlalchemy import select

    from parade_state.models import User, UserSession
    from parade_state.utils import utc_dt

    existing = await db.execute(select(User).where(User.email == email))
    user = existing.scalar_one_or_none()
    if user is not None:
        return user

    user = User(email=email, name=name, role=role, status="active")
    db.add(user)
    await db.flush()
    db.add(
        UserSession(
            token=token,
            user_id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            expires_at=utc_dt.ensure_naive(
                utc_dt.utcnow() + utc_dt.timedelta(days=365)
            ),
        )
    )
    return user


def prepare_database(db_path: Path, fresh: bool) -> None:
    if fresh and db_path.exists():
        db_path.unlink()
        print(f"db: removed existing {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migrate(db_path)
    os.environ["DATABASE_URL"] = database_url(db_path)
    asyncio.run(seed(db_path))


def start_server(port: int) -> uvicorn.Server:
    """Start uvicorn in a daemon thread; returns the uvicorn.Server."""
    import uvicorn

    os.environ.setdefault("AUTH_COOKIE_SECURE", "false")  # local http checks
    config = uvicorn.Config(
        "parade_state.main:app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if server.should_exit:
            raise RuntimeError("uvicorn exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                if server.started:
                    return server
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"server did not start on port {port} within 20s")


def find_chrome(override: str | None) -> str | None:
    if override:
        return override
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None  # fall back to Playwright's bundled Chromium


async def run_checks(base_url: str, args: argparse.Namespace) -> None:
    from playwright.async_api import async_playwright

    chrome = find_chrome(args.chrome)
    if not chrome:
        print("chrome: no system Chrome found, using Playwright's bundled Chromium")

    async def one_pass(browser, viewport, screenshot: str | None) -> None:
        context = await browser.new_context(viewport=viewport)
        await context.add_cookies(
            [{"name": "session_token", "value": SESSION_TOKEN, "url": base_url}]
        )
        page = await context.new_page()
        await page.goto(base_url + args.path)
        await page.wait_for_timeout(int(SETTLE_SECONDS * 1000))

        content = await page.content()
        for text in args.expect or []:
            assert text in content, f"expected text not found: {text!r}"

        before = {
            selector: await page.locator(selector).bounding_box()
            for selector in args.no_shift or []
        }
        for selector, box in before.items():
            assert box is not None, f"no-shift selector not visible: {selector}"

        shot_taken = False
        if args.click:
            await page.locator(args.click).click()
            await page.wait_for_timeout(int(SETTLE_SECONDS * 1000))
            for selector, box in before.items():
                now = await page.locator(selector).bounding_box()
                assert now == box, f"layout shifted on expand: {selector}: {box} -> {now}"

            if screenshot and args.screenshot_expanded:
                await page.screenshot(path=screenshot, full_page=True)
                print(f"screenshot (expanded): {screenshot}")
                shot_taken = True

            # Collapse again and confirm the layout is restored.
            await page.locator(args.click).click()
            await page.wait_for_timeout(int(SETTLE_SECONDS * 1000))
            for selector, box in before.items():
                now = await page.locator(selector).bounding_box()
                assert now == box, f"layout shifted after collapse: {selector}: {box} -> {now}"

        if screenshot and not shot_taken:
            await page.screenshot(path=screenshot, full_page=True)
            print(f"screenshot: {screenshot}")
        await context.close()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=chrome, headless=True)
        await one_pass(browser, DESKTOP_VIEWPORT, args.screenshot)
        print("desktop OK")
        if args.mobile_screenshot or not args.screenshot:
            # Mobile pass always runs (cheap) unless desktop-only was implied
            # by giving only a desktop screenshot alongside --no-mobile.
            await one_pass(browser, MOBILE_VIEWPORT, args.mobile_screenshot)
            print("mobile OK")
        await browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seeded SQLite app + authenticated headless browser for visual checks"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="/nominal-roll",
        help="app path to open (default: %(default)s)",
    )
    parser.add_argument("--db", type=Path, help="database file (default: throwaway temp db)")
    parser.add_argument("--fresh", action="store_true", help="delete the db before migrating")
    parser.add_argument("--no-serve", action="store_true", help="prepare the db and exit")
    parser.add_argument("--serve-only", action="store_true", help="serve; skip browser checks")
    parser.add_argument("--port", type=int, default=0, help="port (default: pick a free one)")
    parser.add_argument("--keep-db", action="store_true", help="keep a temp db afterwards")
    parser.add_argument("--click", help="selector to click (expand), then click again (collapse)")
    parser.add_argument(
        "--no-shift",
        action="append",
        default=[],
        help="selector whose bounding box must not change across click/collapse (repeatable)",
    )
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        help="text that must be present in the page (repeatable)",
    )
    parser.add_argument("--screenshot", help="desktop full-page screenshot path")
    parser.add_argument("--mobile-screenshot", help="mobile full-page screenshot path")
    parser.add_argument(
        "--screenshot-expanded",
        action="store_true",
        help="with --click: screenshot the expanded state instead of the final state",
    )
    parser.add_argument("--chrome", help="Chrome executable (default: system Chrome)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    temp_db = args.db is None
    db_path = args.db or Path(tempfile.gettempdir()) / "parade-state-visual-check.db"

    try:
        prepare_database(db_path, fresh=args.fresh)
        if args.no_serve:
            print(f"db ready: {db_path}")
            return 0

        port = args.port or free_port()
        server = start_server(port)
        base_url = f"http://127.0.0.1:{port}"
        print(f"serving: {base_url}")
        print(f"  super-admin: session_token={SESSION_TOKEN}")
        print(f"  admin:       session_token={ADMIN_TOKEN} (Coy A / Platoon 1)")
        print(f"  user:        session_token={USER_TOKEN}")

        if args.serve_only:
            print("Ctrl-C to stop")
            stop = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            while not stop.is_set() and not server.should_exit:
                time.sleep(0.5)
            server.should_exit = True
            return 0

        asyncio.run(run_checks(base_url, args))
        print("all checks passed")
        return 0
    finally:
        if temp_db and not args.keep_db and db_path.exists():
            db_path.unlink()


if __name__ == "__main__":
    sys.exit(main())
