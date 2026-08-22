"""Discussions board API endpoints (issue 24).

Admins and super-admins create ``requests`` / ``bugs`` posts and comment
on them; super-admins additionally triage (category / status changes,
deletions). Regular users never reach this router — the
``require_admin_user_flexible`` dependency resolves identity from the
session token (never client-supplied user ids), which is what makes the
author-only edit rules enforceable server-side.

Super-admin triage is audit-logged; ordinary posting / commenting /
editing is not (board content is its own record).
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from parade_state.auth.admin_dependencies import require_admin_user_flexible
from parade_state.db import get_db_session
from parade_state.models import AuditLog, DiscussionComment, DiscussionPost, User
from parade_state.models.schemas import (
    DiscussionCommentCreate,
    DiscussionCommentResponse,
    DiscussionCommentUpdate,
    DiscussionPostCreate,
    DiscussionPostDetailResponse,
    DiscussionPostResponse,
    DiscussionPostTriage,
    DiscussionPostUpdate,
)
from parade_state.utils import utc_dt

router = APIRouter()

# Board list cap: flat newest-first view, filters only, no pagination.
LIST_LIMIT = 200


# ============================================================================
# Helpers
# ============================================================================


def _require_super_admin(user: User) -> None:
    """Authorize super_admin only."""
    if user.role != "super_admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only super admins can perform this action",
        )


def _require_author(user: User, author_id: str) -> None:
    """Authorize the original author only."""
    if str(user.id) != author_id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only the author can edit this content",
        )


def _now() -> utc_dt.datetime:
    """Naive-UTC now, matching the model column convention."""
    return utc_dt.ensure_naive(utc_dt.utcnow())


async def _get_post_or_404(db: AsyncSession, post_id: str) -> DiscussionPost:
    """Fetch a post with its comments loaded, 404 when absent."""
    post = (
        await db.execute(
            select(DiscussionPost)
            .options(selectinload(DiscussionPost.comments))
            .where(DiscussionPost.id == post_id)
        )
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Discussion post not found: {post_id}",
        )
    return post


async def _get_comment_or_404(db: AsyncSession, comment_id: str) -> DiscussionComment:
    """Fetch a comment, 404 when absent."""
    comment = (
        await db.execute(
            select(DiscussionComment).where(DiscussionComment.id == comment_id)
        )
    ).scalar_one_or_none()
    if comment is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Discussion comment not found: {comment_id}",
        )
    return comment


async def _author_names(
    db: AsyncSession, author_ids: set[str]
) -> dict[str, str | None]:
    """Display names keyed by user id (missing rows map to None)."""
    if not author_ids:
        return {}
    rows = (
        await db.execute(select(User.id, User.name).where(User.id.in_(author_ids)))
    ).all()
    return {str(uid): name for uid, name in rows}


def _to_comment_response(
    comment: DiscussionComment, author_name: str | None
) -> DiscussionCommentResponse:
    """Build a DiscussionCommentResponse from an ORM instance."""
    return DiscussionCommentResponse(
        id=str(comment.id),
        post_id=str(comment.post_id),
        author_id=str(comment.author_id),
        author_name=author_name,
        body=comment.body,
        created_at=comment.created_at,
        edited_at=comment.edited_at,
    )


async def _to_post_response(
    db: AsyncSession, post: DiscussionPost, *, with_comments: bool = False
) -> DiscussionPostResponse:
    """Build a post response, joining the author name and comment count."""
    author_ids = {str(post.author_id)}
    if with_comments:
        author_ids.update(str(c.author_id) for c in post.comments)
    name_by_id = await _author_names(db, author_ids)

    count = (
        await db.execute(
            select(func.count())
            .select_from(DiscussionComment)
            .where(DiscussionComment.post_id == str(post.id))
        )
    ).scalar_one()

    data = DiscussionPostResponse(
        id=str(post.id),
        title=post.title,
        body=post.body,
        author_id=str(post.author_id),
        author_name=name_by_id.get(str(post.author_id)),
        category=post.category,
        status=post.status,
        comment_count=count,
        created_at=post.created_at,
        edited_at=post.edited_at,
    )
    if with_comments:
        return DiscussionPostDetailResponse(
            **data.model_dump(),
            comments=[
                _to_comment_response(c, name_by_id.get(str(c.author_id)))
                for c in post.comments
            ],
        )
    return data


# ============================================================================
# Posts
# ============================================================================


@router.get("/posts", response_model=list[DiscussionPostResponse])
async def list_posts(
    category: str | None = None,
    status_filter: str | None = None,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> list[DiscussionPostResponse]:
    """List board posts, newest first, optionally filtered.

    A flat list capped at the 200 most recent posts (category and status
    filters narrow it); pagination is deliberately absent for the
    expected admin-only volume.
    """
    query = (
        select(DiscussionPost)
        .order_by(DiscussionPost.created_at.desc())
        .limit(LIST_LIMIT)
    )
    if category:
        query = query.where(DiscussionPost.category == category)
    if status_filter:
        query = query.where(DiscussionPost.status == status_filter)

    posts = (await db.execute(query)).scalars().all()
    return [await _to_post_response(db, post) for post in posts]


@router.post(
    "/posts",
    response_model=DiscussionPostResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_post(
    payload: DiscussionPostCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> DiscussionPostResponse:
    """Create a board post.

    Category is required (requests / bugs); every post starts as Open —
    status is set only through super-admin triage.
    """
    post = DiscussionPost(
        title=payload.title,
        body=payload.body,
        author_id=str(user.id),
        category=payload.category,
        status="Open",
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return await _to_post_response(db, post)


@router.get("/posts/{post_id}", response_model=DiscussionPostDetailResponse)
async def get_post(
    post_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> DiscussionPostDetailResponse:
    """Fetch a single post with its comments (oldest first)."""
    post = await _get_post_or_404(db, post_id)
    detail = await _to_post_response(db, post, with_comments=True)
    assert isinstance(detail, DiscussionPostDetailResponse)
    return detail


@router.patch("/posts/{post_id}", response_model=DiscussionPostResponse)
async def update_post(
    post_id: str,
    payload: DiscussionPostUpdate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> DiscussionPostResponse:
    """Edit a post's title/body — author only.

    Category and status are not editable here; they move only through
    the super-admin triage endpoint.
    """
    post = await _get_post_or_404(db, post_id)
    _require_author(user, str(post.author_id))

    if payload.title is not None:
        post.title = payload.title
    if payload.body is not None:
        post.body = payload.body
    post.edited_at = _now()

    await db.commit()
    await db.refresh(post)
    return await _to_post_response(db, post)


@router.patch("/posts/{post_id}/triage", response_model=DiscussionPostResponse)
async def triage_post(
    post_id: str,
    payload: DiscussionPostTriage,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> DiscussionPostResponse:
    """Change a post's category and/or status — super-admin only.

    The one board action that writes an audit row: triage decisions
    (accepting, marking duplicate, implementing) are the moderation
    trail worth keeping.
    """
    post = await _get_post_or_404(db, post_id)
    _require_super_admin(user)

    changes: list[str] = []
    if payload.category is not None and payload.category != post.category:
        changes.append(f"category: {post.category} -> {payload.category}")
        post.category = payload.category
    if payload.status is not None and payload.status != post.status:
        changes.append(f"status: {post.status} -> {payload.status}")
        post.status = payload.status

    if changes:
        db.add(
            AuditLog(
                user_id=str(user.id),
                entity_type="discussion_post",
                entity_id=str(post.id),
                action="update",
                description=(
                    f"Triaged discussion post {post.title!r} "
                    f"({'; '.join(changes)})"
                ),
            )
        )

    await db.commit()
    await db.refresh(post)
    return await _to_post_response(db, post)


@router.delete("/posts/{post_id}")
async def delete_post(
    post_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> dict:
    """Delete a post and its comments — super-admin only."""
    post = await _get_post_or_404(db, post_id)
    _require_super_admin(user)

    await db.delete(post)
    await db.commit()
    return {"detail": f"Discussion post {post_id} deleted"}


# ============================================================================
# Comments
# ============================================================================


@router.post(
    "/posts/{post_id}/comments",
    response_model=DiscussionCommentResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_comment(
    post_id: str,
    payload: DiscussionCommentCreate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> DiscussionCommentResponse:
    """Comment on a post — any admin or super-admin."""
    post = await _get_post_or_404(db, post_id)

    comment = DiscussionComment(
        post_id=str(post.id),
        author_id=str(user.id),
        body=payload.body,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return _to_comment_response(comment, user.name)


@router.patch("/comments/{comment_id}", response_model=DiscussionCommentResponse)
async def update_comment(
    comment_id: str,
    payload: DiscussionCommentUpdate,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> DiscussionCommentResponse:
    """Edit a comment's body — author only."""
    comment = await _get_comment_or_404(db, comment_id)
    _require_author(user, str(comment.author_id))

    comment.body = payload.body
    comment.edited_at = _now()

    await db.commit()
    await db.refresh(comment)
    return _to_comment_response(comment, user.name)


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: str,
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(require_admin_user_flexible),
) -> dict:
    """Delete a comment — super-admin only."""
    comment = await _get_comment_or_404(db, comment_id)
    _require_super_admin(user)

    await db.delete(comment)
    await db.commit()
    return {"detail": f"Discussion comment {comment_id} deleted"}
