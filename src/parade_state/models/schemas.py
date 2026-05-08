"""Pydantic schemas for API request/response validation."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ============================================================================
# Deployment Schemas
# ============================================================================


class DeploymentBase(BaseModel):
    """Base deployment schema."""

    name: str = Field(..., min_length=1, max_length=255)
    estab_id: str = Field(..., min_length=1)
    valid_from: datetime
    valid_until: datetime
    notes: str | None = None


class DeploymentCreate(DeploymentBase):
    """Schema for creating a deployment."""

    status: Literal["draft", "active", "inactive"] = "draft"
    scheduled_activation: datetime | None = None


class DeploymentUpdate(BaseModel):
    """Schema for updating a deployment."""

    name: str | None = Field(None, min_length=1, max_length=255)
    status: Literal["draft", "active", "inactive", "archived", "closed", "finalized"] | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    scheduled_activation: datetime | None = None
    notes: str | None = None


class DeploymentResponse(DeploymentBase):
    """Schema for deployment response."""

    id: str
    status: str
    scheduled_activation: datetime | None
    personnel_count: int
    created_at: datetime
    created_by: str
    activated_at: datetime | None
    deactivated_at: datetime | None

    class Config:
        from_attributes = True


class DeploymentListParams(BaseModel):
    """Schema for deployment list query parameters."""

    status: str | None = None
    estab_id: str | None = None
    search: str | None = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


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
    created_at: datetime
    created_by: str
    updated_at: datetime

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
    created_at: datetime
    created_by: str
    updated_at: datetime
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
    date: datetime
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
    created_at: datetime
    created_by: str
    opened_at: datetime
    closed_at: datetime | None
    closed_by: str | None

    class Config:
        from_attributes = True


class SessionListParams(BaseModel):
    """Schema for session list query parameters."""

    deployment_id: str | None = None
    status: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
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
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str
    last_edit_at: datetime | None
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

    service_number: str = Field(..., min_length=1)
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
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PersonnelUpdate(BaseModel):
    """Schema for updating personnel."""

    rank: str | None = Field(None, min_length=1)
    name: str | None = Field(None, min_length=1)
    unit: str | None = Field(None, min_length=1)
    sub_unit_1: str | None = None
    sub_unit_2: str | None = None
    sub_unit_3: str | None = None
    status: str | None = None


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
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class PersonnelResponseWithDeployment(PersonnelBase):
    """Schema for personnel response with deployment-specific assignments."""

    id: str
    estab_id: str
    status: str
    created_at: datetime
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
    session_date: datetime
    session_type: Literal["AM", "PM"]
    session_status: Literal["open", "closed", "finalized"]
    status: Literal["present", "absent", "excused", "unknown"]
    remarks: str | None
    created_at: datetime
    updated_at: datetime

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
    date_from: date | None
    date_to: date | None
    stats: PersonnelAttendanceHistoryStats
    attendance_records: list[PersonnelAttendanceHistoryItem]
    total_count: int
