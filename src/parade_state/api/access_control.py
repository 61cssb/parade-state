"""Access control management API endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parade_state.db import get_db_session
from parade_state.models import User, Deployment, DeploymentUserAccess, UserSubunitScope
from parade_state.models.schemas import (
    DeploymentUserAccessCreate,
    DeploymentUserAccessResponse,
    UserSubunitScopeCreate,
    UserSubunitScopeResponse,
    UserAccessListParams,
    UserSubunitScopeListParams,
)

router = APIRouter()


# ============================================================================
# Helper Functions
# ============================================================================


async def verify_deployment_access_or_admin(
    deployment_id: str,
    user_id: str,
    user_role: str,
    db: AsyncSession,
    allow_self_grant: bool = False,
) -> tuple[Deployment, bool]:
    """Verify user has access to deployment or is admin/super_admin.

    Returns (deployment, has_access) tuple.
    - Super admins always have access
    - Admins need explicit deployment access (unless allow_self_grant is True)
    - Regular users need explicit deployment access

    Args:
        deployment_id: Deployment to check access for
        user_id: User ID to check access for
        user_role: Role of the user (super_admin, admin, user)
        db: Database session
        allow_self_grant: If True, allow admins to access deployment for self-grant purposes
    """
    # Get deployment
    result = await db.execute(select(Deployment).where(Deployment.id == deployment_id))
    deployment = result.scalar_one_or_none()

    if not deployment:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Deployment not found",
        )

    # Super admins have full access
    if user_role == "super_admin":
        return deployment, True

    # Check for explicit deployment access
    access_result = await db.execute(
        select(DeploymentUserAccess).where(
            and_(
                DeploymentUserAccess.user_id == user_id,
                DeploymentUserAccess.deployment_id == deployment_id,
                DeploymentUserAccess.revoked_at.is_(None),
            )
        )
    )
    access = access_result.scalar_one_or_none()

    # Admins with explicit access
    if user_role == "admin" and access:
        return deployment, True

    # Allow self-grant for admins (for initial access setup)
    if user_role == "admin" and allow_self_grant:
        return deployment, True

    # Regular users need explicit access
    if access:
        return deployment, True

    # No access found
    return deployment, False


async def get_user_accessible_deployments(
    user_id: str,
    user_role: str,
    db: AsyncSession,
) -> list[Deployment]:
    """Get list of deployments user has access to.

    Super admins get all deployments.
    Admins and users get deployments they have explicit access to.
    """
    # Super admins get all deployments
    if user_role == "super_admin":
        result = await db.execute(select(Deployment))
        return list(result.scalars().all())

    # Get deployments with explicit access
    result = await db.execute(
        select(Deployment)
        .join(DeploymentUserAccess)
        .where(
            and_(
                DeploymentUserAccess.user_id == user_id,
                DeploymentUserAccess.revoked_at.is_(None),
            )
        )
    )
    return list(result.scalars().all())


def apply_subunit_scope_filter(query, model, user_id: str, deployment_id: str):
    """Apply subunit scope filtering to a query.

    Filters results to only show data within user's subunit scope.
    """
    # Get user's subunit scopes for this deployment
    # This would typically be done with a join, but for now we'll return the query
    # The actual filtering logic will be implemented in the calling functions
    return query


async def get_user_subunit_scopes(
    user_id: str,
    deployment_id: str,
    db: AsyncSession,
) -> list[UserSubunitScope]:
    """Get all subunit scopes for a user in a deployment."""
    result = await db.execute(
        select(UserSubunitScope).where(
            and_(
                UserSubunitScope.user_id == user_id,
                UserSubunitScope.deployment_id == deployment_id,
            )
        )
    )
    return list(result.scalars().all())


async def check_subunit_access(
    user_id: str,
    deployment_id: str,
    unit: str | None = None,
    sub_unit_1: str | None = None,
    sub_unit_2: str | None = None,
    sub_unit_3: str | None = None,
    db: AsyncSession | None = None,
) -> bool:
    """Check if user has access to specific subunit within deployment.

    Returns True if user has access to at least one scope that matches
    the provided unit hierarchy.
    """
    # If no database session provided, we can't check
    if not db:
        return True  # Optimistic default for now

    # Get user's subunit scopes
    scopes = await get_user_subunit_scopes(user_id, deployment_id, db)

    # If no scopes defined, user has access to all units (deployment-wide access)
    if not scopes:
        return True

    # Check if any scope matches the requested unit hierarchy
    for scope in scopes:
        # If scope has no restrictions, grant access
        if not scope.unit and not scope.sub_unit_1 and not scope.sub_unit_2 and not scope.sub_unit_3:
            return True

        # Check unit match
        if scope.unit and unit != scope.unit:
            continue

        # Check sub_unit_1 match
        if scope.sub_unit_1 and sub_unit_1 != scope.sub_unit_1:
            continue

        # Check sub_unit_2 match
        if scope.sub_unit_2 and sub_unit_2 != scope.sub_unit_2:
            continue

        # Check sub_unit_3 match
        if scope.sub_unit_3 and sub_unit_3 != scope.sub_unit_3:
            continue

        # All checks passed
        return True

    # No matching scope found
    return False


# ============================================================================
# Deployment User Access Endpoints
# ============================================================================


@router.post("/deployments/{deployment_id}/users/{user_id}/access", response_model=DeploymentUserAccessResponse)
async def grant_user_deployment_access(
    deployment_id: str,
    user_id: str,
    granted_by: str = Query(..., description="User ID granting the access"),
    user_role: str = Query(..., description="Role of user granting access"),
    db: AsyncSession = Depends(get_db_session),
):
    """Grant a user access to a deployment.

    Only admins and super admins can grant deployment access.
    """
    # Check permissions
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can grant deployment access",
        )

    # Verify deployment exists and granting user has access
    # Allow self-grant for initial access setup
    deployment, has_access = await verify_deployment_access_or_admin(
        deployment_id, granted_by, user_role, db, allow_self_grant=True
    )

    if not has_access:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this deployment",
        )

    # Verify target user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if access already exists (and is not revoked)
    existing_access = await db.execute(
        select(DeploymentUserAccess).where(
            and_(
                DeploymentUserAccess.user_id == user_id,
                DeploymentUserAccess.deployment_id == deployment_id,
                DeploymentUserAccess.revoked_at.is_(None),
            )
        )
    )
    existing = existing_access.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="User already has access to this deployment",
        )

    # Create access grant
    access = DeploymentUserAccess(
        user_id=user_id,
        deployment_id=deployment_id,
        granted_by=granted_by,
        granted_at=datetime.utcnow(),
    )

    db.add(access)
    await db.commit()
    await db.refresh(access)

    return access


@router.delete("/deployments/{deployment_id}/users/{user_id}/access")
async def revoke_user_deployment_access(
    deployment_id: str,
    user_id: str,
    revoked_by: str = Query(..., description="User ID revoking the access"),
    user_role: str = Query(..., description="Role of user revoking access"),
    db: AsyncSession = Depends(get_db_session),
):
    """Revoke a user's access to a deployment.

    Only admins and super admins can revoke deployment access.
    """
    # Check permissions
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can revoke deployment access",
        )

    # Verify deployment exists and revoking user has access
    # Allow admins to revoke even if they don't have access (for cleanup)
    deployment, has_access = await verify_deployment_access_or_admin(
        deployment_id, revoked_by, user_role, db, allow_self_grant=True
    )

    if not has_access:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this deployment",
        )

    # Get existing access
    access_result = await db.execute(
        select(DeploymentUserAccess).where(
            and_(
                DeploymentUserAccess.user_id == user_id,
                DeploymentUserAccess.deployment_id == deployment_id,
                DeploymentUserAccess.revoked_at.is_(None),
            )
        )
    )
    access = access_result.scalar_one_or_none()

    if not access:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User access not found",
        )

    # Revoke access
    access.revoked_at = datetime.utcnow()
    await db.commit()

    return {"message": "User access revoked successfully"}


@router.get("/users/{user_id}/deployments", response_model=list[DeploymentUserAccessResponse])
async def list_user_deployment_accesses(
    user_id: str,
    active_only: bool = Query(True, description="Show only active accesses"),
    requesting_user_id: str = Query(..., description="User ID making the request"),
    requesting_user_role: str = Query(..., description="Role of user making the request"),
    db: AsyncSession = Depends(get_db_session),
):
    """List all deployment accesses for a user.

    Users can only see their own deployment accesses.
    Admins and super admins can see all users' deployment accesses.
    """
    # Check permissions
    if requesting_user_id != user_id and requesting_user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You can only view your own deployment accesses",
        )

    # Verify user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get deployment accesses
    query = select(DeploymentUserAccess).where(
        DeploymentUserAccess.user_id == user_id
    )

    if active_only:
        query = query.where(DeploymentUserAccess.revoked_at.is_(None))

    result = await db.execute(query)
    accesses = result.scalars().all()

    return accesses


@router.get("/deployments/{deployment_id}/users", response_model=list[DeploymentUserAccessResponse])
async def list_deployment_users(
    deployment_id: str,
    active_only: bool = Query(True, description="Show only active accesses"),
    requesting_user_id: str = Query(..., description="User ID making the request"),
    requesting_user_role: str = Query(..., description="Role of user making the request"),
    db: AsyncSession = Depends(get_db_session),
):
    """List all users with access to a deployment.

    Only users with access to the deployment can see other users.
    """
    # Verify deployment exists and requesting user has access
    deployment, has_access = await verify_deployment_access_or_admin(
        deployment_id, requesting_user_id, requesting_user_role, db
    )

    if not has_access:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this deployment",
        )

    # Get deployment users
    query = select(DeploymentUserAccess).where(
        DeploymentUserAccess.deployment_id == deployment_id
    )

    if active_only:
        query = query.where(DeploymentUserAccess.revoked_at.is_(None))

    result = await db.execute(query)
    accesses = result.scalars().all()

    return accesses


# ============================================================================
# User Subunit Scope Endpoints
# ============================================================================


@router.post("/deployments/{deployment_id}/users/{user_id}/scopes", response_model=UserSubunitScopeResponse)
async def create_user_subunit_scope(
    deployment_id: str,
    user_id: str,
    scope: UserSubunitScopeCreate,
    created_by: str = Query(..., description="User ID creating the scope"),
    user_role: str = Query(..., description="Role of user creating scope"),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a subunit scope for a user within a deployment.

    Only admins and super admins can create subunit scopes.
    """
    # Check permissions
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can create subunit scopes",
        )

    # Verify deployment exists and creating user has access
    deployment, has_access = await verify_deployment_access_or_admin(
        deployment_id, created_by, user_role, db, allow_self_grant=True
    )

    if not has_access:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this deployment",
        )

    # Verify user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if scope already exists
    existing_scope = await db.execute(
        select(UserSubunitScope).where(
            and_(
                UserSubunitScope.user_id == user_id,
                UserSubunitScope.deployment_id == deployment_id,
                UserSubunitScope.unit == scope.unit,
                UserSubunitScope.sub_unit_1 == scope.sub_unit_1,
                UserSubunitScope.sub_unit_2 == scope.sub_unit_2,
                UserSubunitScope.sub_unit_3 == scope.sub_unit_3,
            )
        )
    )
    existing = existing_scope.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Subunit scope already exists for this user",
        )

    # Create scope
    new_scope = UserSubunitScope(
        user_id=user_id,
        deployment_id=deployment_id,
        unit=scope.unit,
        sub_unit_1=scope.sub_unit_1,
        sub_unit_2=scope.sub_unit_2,
        sub_unit_3=scope.sub_unit_3,
        created_by=created_by,
    )

    db.add(new_scope)
    await db.commit()
    await db.refresh(new_scope)

    return new_scope


@router.delete("/deployments/{deployment_id}/users/{user_id}/scopes/{scope_id}")
async def delete_user_subunit_scope(
    deployment_id: str,
    user_id: str,
    scope_id: str,
    deleted_by: str = Query(..., description="User ID deleting the scope"),
    user_role: str = Query(..., description="Role of user deleting scope"),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete a subunit scope for a user.

    Only admins and super admins can delete subunit scopes.
    """
    # Check permissions
    if user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete subunit scopes",
        )

    # Verify deployment exists and deleting user has access
    deployment, has_access = await verify_deployment_access_or_admin(
        deployment_id, deleted_by, user_role, db, allow_self_grant=True
    )

    if not has_access:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this deployment",
        )

    # Get scope
    scope_result = await db.execute(
        select(UserSubunitScope).where(
            and_(
                UserSubunitScope.id == scope_id,
                UserSubunitScope.user_id == user_id,
                UserSubunitScope.deployment_id == deployment_id,
            )
        )
    )
    scope = scope_result.scalar_one_or_none()

    if not scope:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Subunit scope not found",
        )

    # Delete scope
    await db.delete(scope)
    await db.commit()

    return {"message": "Subunit scope deleted successfully"}


@router.get("/deployments/{deployment_id}/users/{user_id}/scopes", response_model=list[UserSubunitScopeResponse])
async def list_user_subunit_scopes(
    deployment_id: str,
    user_id: str,
    requesting_user_id: str = Query(..., description="User ID making the request"),
    requesting_user_role: str = Query(..., description="Role of user making the request"),
    db: AsyncSession = Depends(get_db_session),
):
    """List all subunit scopes for a user within a deployment.

    Users can only see their own scopes.
    Admins and super admins can see all users' scopes.
    """
    # Check permissions
    if requesting_user_id != user_id and requesting_user_role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You can only view your own subunit scopes",
        )

    # Verify deployment exists and requesting user has access
    deployment, has_access = await verify_deployment_access_or_admin(
        deployment_id, requesting_user_id, requesting_user_role, db
    )

    if not has_access:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this deployment",
        )

    # Get scopes
    result = await db.execute(
        select(UserSubunitScope).where(
            and_(
                UserSubunitScope.user_id == user_id,
                UserSubunitScope.deployment_id == deployment_id,
            )
        )
    )
    scopes = result.scalars().all()

    return scopes
