"""Feature-access matrix save API (issue 37).

Super-admins configure, per feature, whether the ``admin`` role can see
and use it. The Settings card POSTs the full matrix (one item per
configured feature); the endpoint upserts the ``feature_access`` rows
and audit-logs the change. Effective visibility = ``FEATURE_*`` env flag
AND matrix entry — the env kill switch outranks the matrix, and absent
rows mean enabled (fail-open). See :mod:`parade_state.feature_access`.
"""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.auth.dependencies import require_super_admin_user
from parade_state.db import get_db_session
from parade_state.feature_access import (
    FEATURE_KEY_LABELS,
    MATRIX_FEATURE_KEYS,
    MATRIX_FEATURES,
    feature_access_map,
)
from parade_state.models.access import FeatureAccess, User
from parade_state.models.audit import AuditLog
from parade_state.utils import utc_dt

#: The only role the matrix currently configures (issue 37; viewer tiers
#: are #29, out of scope). Fixed server-side — the payload carries no
#: role, so a crafted request cannot touch super_admin visibility.
MATRIX_ROLE = "admin"

router = APIRouter()


class FeatureAccessItem(BaseModel):
    """One feature toggle from the Settings card."""

    feature_key: str
    enabled: bool

    @field_validator("feature_key")
    @classmethod
    def key_must_be_known(cls, value: str) -> str:
        if value not in MATRIX_FEATURE_KEYS:
            raise ValueError(
                f"Unknown feature key {value!r}; expected one of: "
                f"{', '.join(sorted(MATRIX_FEATURE_KEYS))}"
            )
        return value


class FeatureAccessUpdate(BaseModel):
    """Full-matrix save payload from the Settings card."""

    items: list[FeatureAccessItem]


class FeatureAccessResponse(BaseModel):
    """The effective matrix for the configured role after the save."""

    role: str
    features: dict[str, bool]


@router.post("/feature-access", response_model=FeatureAccessResponse)
async def save_feature_access(
    payload: FeatureAccessUpdate,
    current_user: User = Depends(require_super_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Upsert the admin feature-access matrix (super admin only).

    The payload carries every feature key (the card submits the whole
    matrix), so each row is created or updated to the posted value —
    deterministic regardless of prior state. Only value changes are
    audit-logged.
    """
    posted = {item.feature_key: item.enabled for item in payload.items}

    rows = (
        (
            await db.execute(
                select(FeatureAccess).where(FeatureAccess.role == MATRIX_ROLE)
            )
        )
        .scalars()
        .all()
    )
    by_key = {row.feature_key: row for row in rows}

    now = utc_dt.ensure_naive(utc_dt.utcnow())
    changes: dict[str, dict[str, bool]] = {}
    for feature in MATRIX_FEATURES:
        new_value = posted.get(feature.key, True)
        row = by_key.get(feature.key)
        old_value = bool(row.enabled) if row is not None else True
        if row is None:
            db.add(
                FeatureAccess(
                    feature_key=feature.key,
                    role=MATRIX_ROLE,
                    enabled=new_value,
                    created_at=now,
                    updated_at=now,
                )
            )
        elif bool(row.enabled) != new_value:
            row.enabled = new_value
            row.updated_at = now
        else:
            continue  # unchanged — skip from the audit trail
        if old_value != new_value:
            changes[feature.key] = {"from": old_value, "to": new_value}

    if changes:
        summary = ", ".join(
            f"{FEATURE_KEY_LABELS.get(key, key)} "
            f"{'→ on' if change['to'] else '→ off'}"
            for key, change in sorted(changes.items())
        )
        db.add(
            AuditLog(
                user_id=str(current_user.id),
                entity_type="feature_access",
                entity_id=MATRIX_ROLE,
                action="update",
                changes=json.dumps(changes),
                description=(
                    f"Updated feature access for role '{MATRIX_ROLE}': {summary}"
                ),
            )
        )

    await db.commit()

    matrix = await feature_access_map(db)
    return FeatureAccessResponse(
        role=MATRIX_ROLE,
        features={
            feature.key: matrix.get(MATRIX_ROLE, {}).get(feature.key, True)
            for feature in MATRIX_FEATURES
        },
    )
