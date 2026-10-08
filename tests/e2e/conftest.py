"""E2E fixtures: fresh seeded database + real uvicorn + Playwright browser.

Restore-to-start guarantee: every test gets a brand-new database (alembic
migrations + the deterministic seed in ``seed_helper.py``) and its own
server process on a free port, so tests are freely repeatable — each run
starts from the identical, freshly-migrated state (there is nothing to
restore because the starting state is rebuilt from scratch).

Run with::

    uv run --with playwright pytest tests/e2e -m e2e

The whole directory skips itself when Playwright is not importable (it is
deliberately not a dev dependency; visual_check.py pioneered the
``uv run --with playwright`` invocation), or when no Chrome/Chromium can
be launched, so plain ``pytest tests`` stays green on machines without
browser tooling.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SESSION_TOKEN = "ippt-e2e-token"
CHROME_CANDIDATES = (
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)
SERVER_START_TIMEOUT = 30


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _find_chrome() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None  # fall back to Playwright's bundled Chromium


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    chrome = _find_chrome()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=chrome, headless=True)
        except Exception as exc:  # noqa: BLE001 — skip, not fail
            pytest.skip(f"no usable Chrome/Chromium for E2E: {exc}")
        yield browser
        browser.close()


@pytest.fixture
def e2e_server(tmp_path):
    """Fresh migrated+seeded database with the app serving it on a free port."""
    db_path = tmp_path / "e2e.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"

    def _run(cmd: list[str]) -> None:
        result = subprocess.run(
            cmd, cwd=REPO_ROOT, env={**os.environ, "DATABASE_URL": database_url}
        )
        if result.returncode != 0:
            raise RuntimeError(f"E2E setup step failed: {' '.join(cmd)}")

    _run([sys.executable, "-m", "alembic", "upgrade", "head"])
    _run(
        [
            sys.executable,
            str(REPO_ROOT / "tests" / "e2e" / "seed_helper.py"),
            database_url,
        ]
    )

    port = _free_port()
    server_env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "FEATURE_IPPT": "true",
        "ENVIRONMENT": "development",
        "AUTH_COOKIE_SECURE": "false",
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "parade_state.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=server_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + SERVER_START_TIMEOUT
    import urllib.request

    while time.monotonic() < deadline:
        if server.poll() is not None:
            output = (
                server.stdout.read().decode(errors="replace") if server.stdout else ""
            )
            raise RuntimeError(f"E2E server exited during startup:\n{output[-2000:]}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.2)
    else:
        server.terminate()
        raise RuntimeError("E2E server did not become healthy in time")

    yield base_url

    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()


@pytest.fixture
def page(browser, e2e_server):
    """Authenticated browser page (super-admin via the seeded session cookie)."""

    context = browser.new_context()
    context.add_cookies(
        [
            {
                "name": "session_token",
                "value": SESSION_TOKEN,
                "url": e2e_server,
            }
        ]
    )
    yield context.new_page()
    context.close()
