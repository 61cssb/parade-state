"""Discussion board models (issue 24).

An in-app board where admins and super-admins post ``requests`` / ``bugs``
items and discuss them in markdown comments. Super-admins triage each post
(status: Open / Duplicate / Accepted / Implemented) and may recategorize
it. Visible to admins only — never to regular users — and hidden entirely
while ``FEATURE_DISCUSSIONS`` is off.
"""

from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from parade_state.utils import utc_dt

from ..db import Base

if TYPE_CHECKING:
    from .access import User


class DiscussionPost(Base):
    """A single board post (a request or a bug report).

    ``edited_at`` flips from NULL on the author's first edit and then
    tracks the last edit — the display shows an "Edited" indicator with
    that timestamp. Full edit history is intentionally not kept.
    """

    __tablename__ = "discussion_posts"

    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    category: Mapped[str] = mapped_column(
        Enum("requests", "bugs", name="discussion_category"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        Enum("Open", "Duplicate", "Accepted", "Implemented",
             name="discussion_post_status"),
        default="Open",
        index=True,
    )
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    edited_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)

    # Relationships
    author: Mapped["User"] = relationship(
        foreign_keys=[author_id],
        primaryjoin="DiscussionPost.author_id == User.id",
    )
    comments: Mapped[list["DiscussionComment"]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="DiscussionComment.created_at",
    )

    def __repr__(self) -> str:
        return (
            f"<DiscussionPost(title={self.title!r}, "
            f"category={self.category!r}, status={self.status!r})>"
        )


class DiscussionComment(Base):
    """A markdown comment on a discussion post.

    Editing is restricted to the author (enforced server-side); like
    posts, only the last edit is recorded via ``edited_at``.
    """

    __tablename__ = "discussion_comments"

    post_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("discussion_posts.id", ondelete="CASCADE"),
        index=True,
    )
    author_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[utc_dt.datetime] = mapped_column(
        default=lambda: utc_dt.ensure_naive(utc_dt.utcnow())
    )
    edited_at: Mapped[utc_dt.datetime | None] = mapped_column(nullable=True)

    # Relationships
    post: Mapped["DiscussionPost"] = relationship(back_populates="comments")
    author: Mapped["User"] = relationship(
        foreign_keys=[author_id],
        primaryjoin="DiscussionComment.author_id == User.id",
    )

    def __repr__(self) -> str:
        return (
            f"<DiscussionComment(post_id={self.post_id!r}, "
            f"author_id={self.author_id!r})>"
        )
