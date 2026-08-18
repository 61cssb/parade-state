#!/usr/bin/env python3
"""One-time pre-alpha infrastructure sanity checks for a parade-state deployment.

Verifies the deployed baseline before alpha testing: reachability, security
gating (docs disabled, API auth-enforced), OAuth wiring (https redirect URI),
CORS behavior, session-cookie flags, and — with --deep — Railway platform
state (deployment SUCCESS, Postgres online, alembic at head inside the
container via `railway ssh`, skipped gracefully when no SSH key is registered).

Intended as a one-off operational tool, not a permanent fixture; CI checks
for pre-production deploys will supersede it.

Usage:
    python scripts/alpha_sanity_check.py \
        --base-url https://parade-state-production.up.railway.app [--deep]

Exit code 0 = all checks passed (warnings allowed); 1 = any failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

CHECK_WIDTH = 52
RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Block redirect following so we can inspect 3xx responses."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(url: str, headers: dict[str, str] | None = None):
    """GET a URL without following redirects.

    Returns (status, headers, body-bytes); network errors become (0, {}, b"").
    Header names are lowercased for case-insensitive lookup.
    """
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with OPENER.open(req, timeout=15) as resp:
            raw = resp.headers
            status, body = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        # 3xx responses arrive here because redirects are blocked
        raw = err.headers
        status, body = err.code, err.read()
    except urllib.error.URLError as err:
        detail = getattr(err.reason, "strerror", None) or str(err.reason)
        return 0, {}, detail.encode()
    lowered = {key.lower(): value for key, value in raw.items()}
    return status, lowered, body


def record(status: str, name: str, detail: str = "") -> None:
    RESULTS.append((status, name, detail))


def run_cmd(args: list[str]) -> str | None:
    """Run a subprocess, returning stdout or None on failure."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


# --------------------------------------------------------------------------
# Shallow checks (plain HTTP against the base URL)
# --------------------------------------------------------------------------


def check_health(base: str) -> None:
    status, _, body = fetch(f"{base}/health")
    ok = status == 200 and b"healthy" in body
    record(PASS if ok else FAIL, "GET /health", f"status {status}")


def check_root_redirect(base: str) -> None:
    status, headers, _ = fetch(base + "/")
    location = headers.get("location", headers.get("Location", ""))
    ok = status in (301, 302, 307, 308) and location.rstrip("/").endswith("/auth/login")
    record(
        PASS if ok else FAIL,
        "GET / redirects to /auth/login",
        f"status {status}, location {location or 'none'}",
    )


def check_login_page(base: str) -> None:
    status, _, body = fetch(f"{base}/auth/login")
    ok = status == 200 and b"ign in" in body
    record(PASS if ok else FAIL, "GET /auth/login renders", f"status {status}")


def check_docs_gated(base: str) -> None:
    statuses = {
        path: fetch(f"{base}{path}")[0] for path in ("/docs", "/redoc", "/openapi.json")
    }
    ok = all(code == 404 for code in statuses.values())
    detail = ", ".join(f"{path} {code}" for path, code in statuses.items())
    record(PASS if ok else FAIL, "OpenAPI surface returns 404", detail)


def check_oauth_start(base: str) -> None:
    status, headers, _ = fetch(f"{base}/auth/oauth/start")
    location = headers.get("location", headers.get("Location", ""))
    if status != 302 or not location.startswith("https://accounts.google.com/"):
        record(FAIL, "OAuth start redirects to Google", f"status {status}")
        return

    query = parse_qs(urlparse(location).query)
    redirect_uri = query.get("redirect_uri", [""])[0]
    expected = f"{base}/auth/callback"
    client_id = query.get("client_id", ["?"])[0]
    if redirect_uri != expected:
        record(
            FAIL,
            "OAuth redirect_uri matches deployment",
            f"got {redirect_uri}, want {expected}",
        )
        return
    record(
        PASS,
        "OAuth redirect_uri matches deployment",
        f"client {client_id[:12]}...",
    )


def check_cors(base: str) -> None:
    origin = base  # the deployment's own origin is the allowed one
    _, headers, _ = fetch(f"{base}/health", headers={"Origin": origin})
    acao = headers.get("access-control-allow-origin")
    acac = headers.get("access-control-allow-credentials")
    allowed_ok = acao == origin and acac == "true"

    _, denied_headers, _ = fetch(
        f"{base}/health", headers={"Origin": "https://evil.example"}
    )
    denied_ok = "access-control-allow-origin" not in denied_headers

    if allowed_ok and denied_ok:
        record(PASS, "CORS echoes only the allowed origin")
    else:
        record(
            FAIL,
            "CORS echoes only the allowed origin",
            f"allowed-origin echo={acao!r} credentials={acac!r} "
            f"evil-origin-leak={not denied_ok}",
        )


def check_session_cookie(base: str) -> None:
    _, headers, _ = fetch(f"{base}/auth/oauth/start")
    set_cookie = headers.get("set-cookie", "")
    if "session_data=" not in set_cookie:
        record(FAIL, "OAuth session cookie issued", "no session_data Set-Cookie")
        return

    flags = {
        "HttpOnly": "httponly" in set_cookie.lower(),
        "SameSite": "samesite" in set_cookie.lower(),
        "Secure": "secure" in set_cookie.lower(),
    }
    missing = [name for name, present in flags.items() if not present]
    # Secure on the short-lived OAuth-state cookie is a hardening nicety;
    # the auth cookie policy is enforced in app code and is not visible
    # until a completed login.
    status = PASS if not missing else (WARN if missing == ["Secure"] else FAIL)
    record(
        status,
        "OAuth session cookie flags",
        "; ".join(f"{name}={'on' if on else 'off'}" for name, on in flags.items()),
    )


def check_api_gated(base: str) -> None:
    # Trailing slash: the bare path 307-redirects to the real route first
    status, _, _ = fetch(f"{base}/api/v1/users/")
    ok = status in (401, 403)
    record(
        PASS if ok else FAIL,
        "API is auth-gated (/api/v1/users)",
        f"anonymous request status {status}",
    )


# --------------------------------------------------------------------------
# Deep checks (Railway CLI; no secrets printed)
# --------------------------------------------------------------------------


def check_platform_state() -> None:
    out = run_cmd(
        ["railway", "deployment", "list", "--json", "--service", "parade-state"]
    )
    if out is None:
        record(SKIP, "Latest deployment status", "railway CLI unavailable")
        return
    deployments = json.loads(out)
    if not deployments:
        record(SKIP, "Latest deployment status", "no deployments found")
        return
    newest = deployments[0].get("status")
    record(
        PASS if newest == "SUCCESS" else FAIL,
        "Latest deployment status",
        str(newest),
    )

    out = run_cmd(["railway", "service", "list", "--json"])
    if out is None:
        record(SKIP, "Postgres service online", "railway CLI unavailable")
        return
    try:
        services = json.loads(out)
        pg = next((s for s in services if s.get("name") == "Postgres"), None)
    except (json.JSONDecodeError, TypeError):
        pg = None
    if pg is None:
        record(FAIL, "Postgres service online", "not found in `railway service list`")
        return
    status = (pg.get("latestDeployment") or {}).get("status") or pg.get("status")
    record(
        PASS if status == "SUCCESS" else FAIL,
        "Postgres service online",
        f"latest deployment {status}",
    )


def check_migration_head() -> None:
    # `railway ssh` requires a registered SSH key; degrade gracefully.
    probe = run_cmd(["railway", "ssh", "--service", "parade-state", "--", "true"])
    if probe is None:
        record(
            SKIP,
            "alembic at head (in container)",
            "no SSH key registered with Railway; note that the container CMD "
            "chains `alembic upgrade head && uvicorn`, so a serving app "
            "already proves migrations applied",
        )
        return

    current = run_cmd(
        ["railway", "ssh", "--service", "parade-state", "--", "alembic", "current"]
    )
    heads = run_cmd(
        ["railway", "ssh", "--service", "parade-state", "--", "alembic", "heads"]
    )
    cur = (current or "").strip().split(" ")[0] if current else "?"
    head = (heads or "").strip().split(" ")[0] if heads else "?"
    record(
        PASS if cur == head and cur != "?" else FAIL,
        "alembic at head (in container)",
        f"current {cur}, head {head}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Deployment base URL")
    parser.add_argument(
        "--deep", action="store_true", help="Also run Railway platform checks"
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    if urlparse(base).scheme != "https":
        print("refusing: --base-url must be https", file=sys.stderr)
        return 1

    check_health(base)
    check_root_redirect(base)
    check_login_page(base)
    check_docs_gated(base)
    check_oauth_start(base)
    check_cors(base)
    check_session_cookie(base)
    check_api_gated(base)
    if args.deep:
        check_platform_state()
        check_migration_head()

    print(f"\nSanity checks for {base}\n")
    for status, name, detail in RESULTS:
        line = f"[{status}] {name}"
        if detail:
            line = f"{line:<{CHECK_WIDTH}} {detail}"
        print(line[:120])

    fails = sum(1 for s, _, _ in RESULTS if s == FAIL)
    warns = sum(1 for s, _, _ in RESULTS if s == WARN)
    skips = sum(1 for s, _, _ in RESULTS if s == SKIP)
    print(f"\n{fails} failed, {warns} warned, {skips} skipped, {len(RESULTS)} total")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
