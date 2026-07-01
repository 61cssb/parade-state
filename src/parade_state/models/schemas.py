"""Pydantic schemas for API request/response validation."""

from typing import Literal

from pydantic import BaseModel, Field

from parade_state.utils import utc_dt

# ============================================================================
# Deployment Schemas
# ============================================================================


class DeploymentBase(BaseModel):
    """Base deployment schema."""

    name: str = Field(..., min_length=1, max_length=255)
    estab_id: str = Field(..., min_length=1)
    valid_from: utc_dt.datetime
    valid_until: utc_dt.datetime
    notes: str | None = None


class DeploymentCreate(DeploymentBase):
    """Schema for creating a deployment."""

    status: Literal["draft", "active", "inactive"] = "draft"
    scheduled_activation: utc_dt.datetime | None = None


class ExclusionCreate(BaseModel):
    """Schema for excluding a personnel from a deployment."""

    personnel_id: str = Field(..., min_length=1)


class DeploymentUpdate(BaseModel):
    """Schema for updating a deployment."""

    name: str | None = Field(None, min_length=1, max_length=255)
    status: (
        Literal["draft", "active", "inactive", "archived", "closed", "finalized"] | None
    ) = None
    valid_from: utc_dt.datetime | None = None
    valid_until: utc_dt.datetime | None = None
    scheduled_activation: utc_dt.datetime | None = None
    notes: str | None = None


class DeploymentResponse(DeploymentBase):
    """Schema for deployment response."""

    id: str
    status: str
    scheduled_activation: utc_dt.datetime | None
    personnel_count: int
    created_at: utc_dt.datetime
    created_by: str
    activated_at: utc_dt.datetime | None
    deactivated_at: utc_dt.datetime | None

    class Config:
        from_attributes = True


class DeploymentListParams(BaseModel):
    """Schema for deployment list query parameters."""

    status: str | None = None
    estab_id: str | None = None
    search: str | None = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class DeploymentStatusSessionInfo(BaseModel):
    """Schema for session information in deployment status."""

    status: Literal["open", "closed", "finalized"]
    present: int = 0
    absent: int = 0
    excused: int = 0
    unknown: int = 0
    total: int = 0


class DeploymentStatusUnitBreakdown(BaseModel):
    """Schema for unit-level breakdown in deployment status."""

    name: str
    total: int
    present: int
    absent: int
    excused: int
    unknown: int


class DeploymentStatusResponse(BaseModel):
    """Schema for deployment status response."""

    deployment_id: str
    deployment_name: str
    date: utc_dt.date
    deployment_status: Literal[
        "draft", "active", "inactive", "archived", "closed", "finalized"
    ]
    am_session: DeploymentStatusSessionInfo | None = None
    pm_session: DeploymentStatusSessionInfo | None = None
    units: list[DeploymentStatusUnitBreakdown]


# ============================================================================
# Deployment Personnel Override Schemas
# ============================================================================


class DeploymentPersonnelOverrideCreate(BaseModel):
    """Schema for creating a deployment personnel override."""

    personnel_id: str
    unit: str = Field(..., min_length=1)
    sub_unit_1: str | None = None
    sub_unit_2: str | None = None
    sub_unit_3: str | None = None


class DeploymentPersonnelOverrideResponse(BaseModel):
    """Schema for deployment personnel override response."""

    id: str
    deployment_id: str
    personnel_id: str
    unit: str
    sub_unit_1: str | None
    sub_unit_2: str | None
    sub_unit_3: str | None
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime

    class Config:
        from_attributes = True


# ============================================================================
# Deployment Notes Schemas
# ============================================================================


class DeploymentNotesCreate(BaseModel):
    """Schema for creating deployment notes."""

    personnel_id: str
    notes: str = Field(..., min_length=1)


class DeploymentNotesUpdate(BaseModel):
    """Schema for updating deployment notes."""

    notes: str = Field(..., min_length=1)


class DeploymentNotesResponse(BaseModel):
    """Schema for deployment notes response."""

    id: str
    deployment_id: str
    personnel_id: str
    notes: str
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime
    updated_by: str
    notes_version: int

    class Config:
        from_attributes = True


# ============================================================================
# Session Schemas
# ============================================================================


class SessionBase(BaseModel):
    """Base session schema."""

    deployment_id: str
    date: utc_dt.datetime
    session_type: Literal["AM", "PM"]


class SessionCreate(SessionBase):
    """Schema for creating a session."""

    status: Literal["open", "closed", "finalized"] = "open"


class SessionUpdate(BaseModel):
    """Schema for updating a session."""

    status: Literal["open", "closed", "finalized"] | None = None


class SessionResponse(SessionBase):
    """Schema for session response."""

    id: str
    status: str
    created_at: utc_dt.datetime
    created_by: str
    opened_at: utc_dt.datetime
    closed_at: utc_dt.datetime | None
    closed_by: str | None

    class Config:
        from_attributes = True


class SessionListParams(BaseModel):
    """Schema for session list query parameters."""

    deployment_id: str | None = None
    status: str | None = None
    date_from: utc_dt.datetime | None = None
    date_to: utc_dt.datetime | None = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


# ============================================================================
# Attendance Record Schemas
# ============================================================================


class AttendanceRecordCreate(BaseModel):
    """Schema for creating an attendance record."""

    session_id: str
    personnel_id: str
    status: Literal["present", "absent", "excused", "unknown"] = "absent"
    remarks: str | None = None


class AttendanceRecordUpdate(BaseModel):
    """Schema for updating an attendance record."""

    status: Literal["present", "absent", "excused", "unknown"] | None = None
    remarks: str | None = None


class AttendanceRecordResponse(BaseModel):
    """Schema for attendance record response."""

    id: str
    session_id: str
    personnel_id: str
    deployment_id: str
    status: str
    remarks: str | None
    notes_snapshot: str | None
    unit_snapshot: str | None
    sub_unit_1_snapshot: str | None
    sub_unit_2_snapshot: str | None
    sub_unit_3_snapshot: str | None
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime
    updated_by: str
    last_edit_at: utc_dt.datetime | None
    last_edit_by: str | None
    is_retroactive_edit: bool

    class Config:
        from_attributes = True


class AttendanceRecordBulkCreateItem(BaseModel):
    """Schema for a single attendance record in bulk create operation."""

    session_id: str
    personnel_id: str
    status: Literal["present", "absent", "excused", "unknown"] = "absent"
    remarks: str | None = None


class AttendanceRecordBulkCreate(BaseModel):
    """Schema for bulk creating attendance records."""

    attendance_records: list[AttendanceRecordBulkCreateItem]


class AttendanceRecordBulkUpdateItem(BaseModel):
    """Schema for a single attendance record in bulk update operation."""

    id: str
    status: Literal["present", "absent", "excused", "unknown"] | None = None
    remarks: str | None = None


class AttendanceRecordBulkUpdate(BaseModel):
    """Schema for bulk updating attendance records."""

    attendance_records: list[AttendanceRecordBulkUpdateItem]


class AttendanceListParams(BaseModel):
    """Schema for attendance list query parameters."""

    session_id: str | None = None
    deployment_id: str | None = None
    personnel_id: str | None = None
    status: str | None = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


# ============================================================================
# Personnel Schemas
# ============================================================================


class PersonnelBase(BaseModel):
    """Base personnel schema."""

    short_id: str = Field(..., min_length=1)
    rank: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    unit: str = Field(..., min_length=1)
    sub_unit_1: str | None = None
    sub_unit_2: str | None = None
    sub_unit_3: str | None = None


class PersonnelResponse(PersonnelBase):
    """Schema for personnel response."""

    id: str
    estab_id: str
    status: str
    created_at: utc_dt.datetime
    updated_at: utc_dt.datetime | None
    created_by: str
    updated_by: str | None

    class Config:
        from_attributes = True


class PersonnelUpdate(BaseModel):
    """Schema for updating personnel."""

    rank: str | None = Field(
        None, min_length=1, max_length=50, description="Personnel rank"
    )
    name: str | None = Field(
        None, min_length=1, max_length=255, description="Full name"
    )
    unit: str | None = Field(
        None, min_length=1, max_length=255, description="Unit assignment"
    )
    sub_unit_1: str | None = Field(None, max_length=255, description="Sub-unit level 1")
    sub_unit_2: str | None = Field(None, max_length=255, description="Sub-unit level 2")
    sub_unit_3: str | None = Field(None, max_length=255, description="Sub-unit level 3")
    status: str | None = Field(
        None,
        pattern="^(active|archived)$",
        description="Personnel status (active or archived)",
    )


class PersonnelListParams(BaseModel):
    """Schema for personnel list query parameters."""

    estab_id: str | None = None
    deployment_id: str | None = None
    unit: str | None = None
    sub_unit_1: str | None = None
    sub_unit_2: str | None = None
    sub_unit_3: str | None = None
    status: str | None = None
    search: str | None = None
    sort_by: str | None = Field(
        None,
        description="Sort field (name, rank, unit, status, created_at, updated_at)",
    )
    sort_order: str | None = Field(
        None,
        description="Sort order (asc, desc)",
    )
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class PersonnelResponseWithDeployment(PersonnelBase):
    """Schema for personnel response with deployment-specific assignments."""

    id: str
    estab_id: str
    status: str
    created_at: utc_dt.datetime
    updated_at: utc_dt.datetime | None
    created_by: str
    updated_by: str | None
    # Deployment-specific fields (included when deployment_id is provided)
    deployment_id: str | None = None
    has_override: bool = False
    deployment_notes: str | None = None

    class Config:
        from_attributes = True


class PersonnelAttendanceHistoryItem(BaseModel):
    """Schema for a single attendance record in personnel history."""

    id: str
    session_id: str
    session_date: utc_dt.datetime
    session_type: Literal["AM", "PM"]
    session_status: Literal["open", "closed", "finalized"]
    status: Literal["present", "absent", "excused", "unknown"]
    remarks: str | None
    created_at: utc_dt.datetime
    updated_at: utc_dt.datetime

    class Config:
        from_attributes = True


class PersonnelAttendanceHistoryStats(BaseModel):
    """Schema for attendance history statistics."""

    total_sessions: int
    present_count: int
    absent_count: int
    excused_count: int
    unknown_count: int
    attendance_rate: float  # Percentage of present + excused vs total


class PersonnelAttendanceHistoryResponse(BaseModel):
    """Schema for personnel attendance history response."""

    personnel_id: str
    deployment_id: str
    date_from: utc_dt.date | None
    date_to: utc_dt.date | None
    stats: PersonnelAttendanceHistoryStats
    attendance_records: list[PersonnelAttendanceHistoryItem]
    total_count: int


# ============================================================================
# Access Control Schemas
# ============================================================================


class DeploymentUserAccessCreate(BaseModel):
    """Schema for creating deployment user access."""

    # Empty for now - access is granted by user/deployment IDs
    pass


class DeploymentUserAccessResponse(BaseModel):
    """Schema for deployment user access response."""

    id: str
    user_id: str
    deployment_id: str
    granted_by: str
    granted_at: utc_dt.datetime
    revoked_at: utc_dt.datetime | None

    class Config:
        from_attributes = True


class UserSubunitScopeCreate(BaseModel):
    """Schema for creating user subunit scope."""

    unit: str | None = None
    sub_unit_1: str | None = None
    sub_unit_2: str | None = None
    sub_unit_3: str | None = None


class UserSubunitScopeResponse(BaseModel):
    """Schema for user subunit scope response."""

    id: str
    user_id: str
    deployment_id: str
    unit: str | None
    sub_unit_1: str | None
    sub_unit_2: str | None
    sub_unit_3: str | None
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime

    class Config:
        from_attributes = True


class UserAccessListParams(BaseModel):
    """Schema for user access list query parameters."""

    active_only: bool = True


class UserSubunitScopeListParams(BaseModel):
    """Schema for user subunit scope list query parameters."""

    deployment_id: str | None = None
    unit: str | None = None


# ============================================================================
# CSV Upload Schemas
# ============================================================================


class CsvUploadResponse(BaseModel):
    """Schema for CSV upload response."""

    id: str
    sha256_hash: str
    original_filename: str | None = None
    line_count: int
    detected_columns: list[str]
    status: str
    uploaded_at: utc_dt.datetime
    uploaded_by: str
    is_duplicate: bool = False

    class Config:
        from_attributes = True


class CsvUploadListItem(BaseModel):
    """Schema for CSV upload list item (metadata only, no raw_content)."""

    id: str
    sha256_hash: str
    original_filename: str | None = None
    line_count: int
    status: str
    uploaded_at: utc_dt.datetime
    uploaded_by: str
    estab_id: str | None = None
    mapping_confirmed_at: utc_dt.datetime | None = None
    diff_confirmed_at: utc_dt.datetime | None = None

    class Config:
        from_attributes = True


# ============================================================================
# Estab Schemas
# ============================================================================


class EstabListItem(BaseModel):
    """Schema for an Estab list item (summary view)."""

    id: str
    caa: utc_dt.date
    status: str
    personnel_count: int
    uploaded_at: utc_dt.datetime
    uploaded_by: str
    csv_hash: str
    # From the most recent linked CsvUpload (null until an upload is linked).
    original_filename: str | None = None

    class Config:
        from_attributes = True


class EstabResponse(EstabListItem):
    """Schema for a single Estab detail response."""

    notes: str | None = None
    confirmed_at: utc_dt.datetime | None = None
    confirmed_by: str | None = None
    created_at: utc_dt.datetime


class EstabUpdate(BaseModel):
    """Schema for updating an estab (status transitions + notes)."""

    status: Literal["confirmed", "draft"] | None = None
    notes: str | None = None


# ============================================================================
# Audit Log Schemas
# ============================================================================


class AuditLogListItem(BaseModel):
    """Schema for a single audit log entry in list responses."""

    id: str
    timestamp: utc_dt.datetime
    user_id: str | None = None
    user_name: str | None = None
    user_email: str | None = None
    entity_type: str
    entity_id: str
    action: str
    changes: str | None = None
    description: str
    ip_address: str | None = None

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    """Schema for paginated audit log list response."""

    items: list[AuditLogListItem]
    total: int
    limit: int
    offset: int
