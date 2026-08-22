"""Discussions board behavior (issue 24).

Covers the board's behavioral contract rather than implementation
details: who can post / comment / edit / triage / delete, that identity
comes from the session (author-only edits are enforced server-side),
and that user-authored markdown can never carry executable HTML.

Flag-off coverage (styled 404s for every role) lives in
test_feature_flags.py alongside the other flag-gated features.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.session import create_user_session
from parade_state.models import AuditLog, User
from parade_state.utils import markdown
from parade_state.utils.cookies import AUTH_COOKIE_NAME

# ============================================================================
# Helpers
# ============================================================================


async def _make_user(
    db_session: AsyncSession, email: str, name: str, role: str
) -> User:
    """Create an active user with the given role."""
    user = User(email=email, name=name, status="active", role=role)
    db_session.add(user)
    await db_session.commit()
    return user


async def _sign_in(
    client: TestClient, db_session: AsyncSession, user: User
) -> None:
    """Create a session for ``user`` and set the auth cookie on ``client``."""
    session = await create_user_session(
        db_session,
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
    )
    await db_session.commit()
    client.cookies.set(AUTH_COOKIE_NAME, session.token)


def _post_payload(**overrides) -> dict:
    """A minimal valid post-create payload."""
    payload = {
        "title": "Export attendance as CSV",
        "body": "Please add a **CSV export** on the attendance page.",
        "category": "requests",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
async def actors(db_session: AsyncSession):
    """The three roles the board distinguishes, plus a regular user."""
    return {
        "author": await _make_user(db_session, "author@example.com", "Author Admin", "admin"),
        "other": await _make_user(db_session, "other@example.com", "Other Admin", "admin"),
        "super": await _make_user(db_session, "sa@example.com", "Super Admin", "super_admin"),
        "user": await _make_user(db_session, "user@example.com", "Plain User", "user"),
    }


@pytest.fixture
async def author_post(client: TestClient, db_session: AsyncSession, actors):
    """A post authored by the ``author`` admin (signed in as them)."""
    await _sign_in(client, db_session, actors["author"])
    response = client.post("/api/v1/discussions/posts", json=_post_payload())
    assert response.status_code == 201, response.text
    return response.json()


# ============================================================================
# Markdown sanitization
# ============================================================================


def test_markdown_escapes_raw_html():
    """Raw HTML in a body renders as inert text, never executable markup."""
    html = markdown.render_markdown("<script>alert(1)</script> plain")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_markdown_neutralizes_unsafe_link_schemes():
    """javascript:/vbscript:/data: links become inert # anchors."""
    html = markdown.render_markdown(
        "[a](javascript:alert(1)) [b](JAVASCRIPT:alert(2)) "
        "[c](vbscript:x) [d](data:text/html,x)"
    )
    assert "javascript:" not in html
    assert "vbscript:" not in html
    assert "data:" not in html
    assert html.count('href="#"') == 4


def test_markdown_keeps_safe_content():
    """Ordinary markdown survives sanitization."""
    html = markdown.render_markdown(
        "**bold** [ok](https://example.com)\n\n```\ncode\n```"
    )
    assert "<strong>bold</strong>" in html
    assert 'href="https://example.com"' in html
    assert "<pre><code>code" in html


async def test_post_page_renders_sanitized_markdown(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """The detail page escapes a script-laced body and keeps safe markdown."""
    await _sign_in(client, db_session, actors["author"])
    response = client.patch(
        f"/api/v1/discussions/posts/{author_post['id']}",
        json={"body": "<script>alert(1)</script> stays **bold**"},
    )
    assert response.status_code == 200

    page = client.get(f"/admin/discussions/posts/{author_post['id']}")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text
    assert "<strong>bold</strong>" in page.text


# ============================================================================
# Post creation + validation
# ============================================================================


async def test_category_required_and_valid(
    client: TestClient, db_session: AsyncSession, actors
):
    """Category is mandatory and limited to requests / bugs."""
    await _sign_in(client, db_session, actors["author"])

    missing = _post_payload()
    del missing["category"]
    assert client.post("/api/v1/discussions/posts", json=missing).status_code == 422

    invalid = _post_payload(category="ideas")
    assert client.post("/api/v1/discussions/posts", json=invalid).status_code == 422

    ok = client.post("/api/v1/discussions/posts", json=_post_payload(category="bugs"))
    assert ok.status_code == 201
    assert ok.json()["category"] == "bugs"
    assert ok.json()["status"] == "Open"  # triage starts closed to authors


async def test_list_newest_first_with_filters(
    client: TestClient, db_session: AsyncSession, actors
):
    """The board lists newest first and honors category/status filters."""
    await _sign_in(client, db_session, actors["author"])
    first = client.post(
        "/api/v1/discussions/posts", json=_post_payload(title="Older request")
    ).json()
    second = client.post(
        "/api/v1/discussions/posts",
        json=_post_payload(title="Newer bug", category="bugs"),
    ).json()

    board = client.get("/api/v1/discussions/posts")
    assert board.status_code == 200
    titles = [p["title"] for p in board.json()]
    assert titles.index("Newer bug") < titles.index("Older request")

    bugs_only = client.get("/api/v1/discussions/posts", params={"category": "bugs"})
    assert [p["id"] for p in bugs_only.json()] == [second["id"]]

    requests_only = client.get(
        "/api/v1/discussions/posts", params={"category": "requests"}
    )
    assert [p["id"] for p in requests_only.json()] == [first["id"]]

    open_only = client.get(
        "/api/v1/discussions/posts", params={"status": "Open"}
    )
    assert len(open_only.json()) == 2


# ============================================================================
# Author-only edits (server-side, session identity)
# ============================================================================


async def test_author_can_edit_own_post(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """An author's edit updates the body and flips the Edited indicator."""
    await _sign_in(client, db_session, actors["author"])
    assert author_post["edited_at"] is None

    response = client.patch(
        f"/api/v1/discussions/posts/{author_post['id']}",
        json={"title": "Export attendance as CSV (revised)", "body": "Updated body"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Export attendance as CSV (revised)"
    assert body["edited_at"] is not None
    assert body["category"] == "requests"  # author edit never moves triage fields


@pytest.mark.parametrize("actor", ["other", "super"])
async def test_non_author_cannot_edit_post(
    client: TestClient, db_session: AsyncSession, actors, author_post, actor
):
    """Only the author edits a post — even a super-admin gets 403."""
    await _sign_in(client, db_session, actors[actor])
    response = client.patch(
        f"/api/v1/discussions/posts/{author_post['id']}", json={"body": "hijack"}
    )
    assert response.status_code == 403


# ============================================================================
# Triage + delete: super-admin only, audit-logged
# ============================================================================


async def test_admin_cannot_triage_or_delete(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """Plain admins get 403 on category/status changes and deletions."""
    await _sign_in(client, db_session, actors["other"])
    post_id = author_post["id"]

    triage = client.patch(
        f"/api/v1/discussions/posts/{post_id}/triage", json={"status": "Accepted"}
    )
    assert triage.status_code == 403

    delete = client.delete(f"/api/v1/discussions/posts/{post_id}")
    assert delete.status_code == 403


async def test_super_admin_triage_writes_audit_row(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """Super-admin triage moves category/status and lands in the audit log."""
    await _sign_in(client, db_session, actors["super"])
    response = client.patch(
        f"/api/v1/discussions/posts/{author_post['id']}",
        json={"category": "bugs", "status": "Accepted"},
    )
    # Author-only endpoint: super-admin triage must use the triage route.
    assert response.status_code == 403

    triage = client.patch(
        f"/api/v1/discussions/posts/{author_post['id']}/triage",
        json={"category": "bugs", "status": "Accepted"},
    )
    assert triage.status_code == 200
    assert triage.json()["category"] == "bugs"
    assert triage.json()["status"] == "Accepted"

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "discussion_post",
                    AuditLog.entity_id == author_post["id"],
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].action == "update"
    assert "category: requests -> bugs" in rows[0].description
    assert "status: Open -> Accepted" in rows[0].description


async def test_triage_no_change_writes_no_audit_row(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """A triage call that changes nothing leaves no audit noise."""
    await _sign_in(client, db_session, actors["super"])
    response = client.patch(
        f"/api/v1/discussions/posts/{author_post['id']}/triage",
        json={"category": "requests", "status": "Open"},
    )
    assert response.status_code == 200

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.entity_id == author_post["id"])
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_super_admin_delete_post_cascades_comments(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """Super-admin delete removes the post and its comments."""
    from parade_state.models import DiscussionComment

    post_id = author_post["id"]
    await _sign_in(client, db_session, actors["other"])
    comment = client.post(
        f"/api/v1/discussions/posts/{post_id}/comments", json={"body": "a note"}
    )
    assert comment.status_code == 201

    await _sign_in(client, db_session, actors["super"])
    assert client.delete(f"/api/v1/discussions/posts/{post_id}").status_code == 200

    remaining = (
        await db_session.execute(
            select(DiscussionComment).where(DiscussionComment.post_id == post_id)
        )
    ).scalars().all()
    assert remaining == []

    assert client.get(f"/api/v1/discussions/posts/{post_id}").status_code == 404


# ============================================================================
# Comments
# ============================================================================


async def test_comment_lifecycle_author_only_edits(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """Anyone on the board comments; only the comment's author edits it."""
    post_id = author_post["id"]

    await _sign_in(client, db_session, actors["other"])
    comment = client.post(
        f"/api/v1/discussions/posts/{post_id}/comments",
        json={"body": "Seconded — `csv` export would help."},
    )
    assert comment.status_code == 201
    assert comment.json()["edited_at"] is None
    comment_id = comment.json()["id"]

    # The post's author is not the comment's author — edit gets 403.
    await _sign_in(client, db_session, actors["author"])
    hijack = client.patch(
        f"/api/v1/discussions/comments/{comment_id}", json={"body": "hijack"}
    )
    assert hijack.status_code == 403

    await _sign_in(client, db_session, actors["other"])
    edit = client.patch(
        f"/api/v1/discussions/comments/{comment_id}", json={"body": "Edited note"}
    )
    assert edit.status_code == 200
    assert edit.json()["edited_at"] is not None

    detail = client.get(f"/api/v1/discussions/posts/{post_id}")
    assert detail.status_code == 200
    assert detail.json()["comment_count"] == 1
    assert detail.json()["comments"][0]["body"] == "Edited note"


async def test_comment_delete_super_admin_only(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """Comment deletion is super-admin only; the author keeps the record."""
    await _sign_in(client, db_session, actors["other"])
    comment = client.post(
        f"/api/v1/discussions/posts/{author_post['id']}/comments",
        json={"body": "to be moderated"},
    )
    comment_id = comment.json()["id"]

    own_delete = client.delete(f"/api/v1/discussions/comments/{comment_id}")
    assert own_delete.status_code == 403

    await _sign_in(client, db_session, actors["super"])
    assert (
        client.delete(f"/api/v1/discussions/comments/{comment_id}").status_code == 200
    )


# ============================================================================
# Role gating: the board is invisible to users and anonymous callers
# ============================================================================


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/discussions/posts", "GET"),
        ("/api/v1/discussions/posts", "POST"),
        ("/api/v1/discussions/posts/some-id", "GET"),
    ],
)
async def test_regular_user_denied(
    client: TestClient, db_session: AsyncSession, actors, path, method
):
    """Regular users never reach the board API (403); anonymous gets 401."""
    response = client.request(method, path, json=_post_payload())
    assert response.status_code == 401  # anonymous: no session

    await _sign_in(client, db_session, actors["user"])
    response = client.request(method, path, json=_post_payload())
    assert response.status_code == 403


async def test_pages_require_admin(
    client: TestClient, db_session: AsyncSession, actors
):
    """Board pages redirect anonymous callers and 403 regular users."""
    anonymous = client.get("/admin/discussions", follow_redirects=False)
    assert anonymous.status_code == 302

    await _sign_in(client, db_session, actors["user"])
    assert client.get("/admin/discussions").status_code == 403


async def test_board_pages_render(
    client: TestClient, db_session: AsyncSession, actors, author_post
):
    """Both pages render the board content for admins."""
    await _sign_in(client, db_session, actors["author"])

    board = client.get("/admin/discussions")
    assert board.status_code == 200
    assert "Export attendance as CSV" in board.text
    assert "requests" in board.text

    detail = client.get(f"/admin/discussions/posts/{author_post['id']}")
    assert detail.status_code == 200
    assert "<strong>CSV export</strong>" in detail.text

    missing = client.get("/admin/discussions/posts/nonexistent")
    assert missing.status_code == 404
