"""Pydantic schemas for API request/response validation."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from parade_state.utils import utc_dt

from .personnel import CALLUP_STATUSES

AttendanceStatus = Literal[
    "present",
    "absent",
    "time_off",
    "mc",
    "yet_to_inpro",
    "outpro",
    "reporting_sick",
    "late",
    "att_out",
]

# ============================================================================
# Grouping Schemas
# ============================================================================


class GroupingBase(BaseModel):
    """Base grouping schema.

    ``valid_from`` / ``valid_until`` are optional to support adhoc groupings
    (``mode="adhoc"``) that don't have a fixed validity window. Standard
    groupings require them — enforced at the API layer.
    """

    name: str = Field(..., min_length=1, max_length=255)
    nominal_roll_id: str = Field(..., min_length=1)
    valid_from: utc_dt.datetime | None = None
    valid_until: utc_dt.datetime | None = None
    notes: str | None = None


class GroupingCreate(GroupingBase):
    """Schema for creating a grouping."""

    mode: Literal["standard", "adhoc", "vehicle"] = "standard"
    status: Literal["draft", "active", "inactive"] = "draft"
    scheduled_activation: utc_dt.datetime | None = None


class ExclusionCreate(BaseModel):
    """Schema for excluding a personnel from a grouping."""

    personnel_id: str = Field(..., min_length=1)


class GroupingUpdate(BaseModel):
    """Schema for updating a grouping."""

    name: str | None = Field(None, min_length=1, max_length=255)
    status: (
        Literal["draft", "active", "inactive", "archived", "closed", "finalized"] | None
    ) = None
    valid_from: utc_dt.datetime | None = None
    valid_until: utc_dt.datetime | None = None
    scheduled_activation: utc_dt.datetime | None = None
    notes: str | None = None


class GroupingResponse(GroupingBase):
    """Schema for grouping response."""

    id: str
    mode: str
    status: str
    scheduled_activation: utc_dt.datetime | None
    personnel_count: int
    created_at: utc_dt.datetime
    created_by: str
    activated_at: utc_dt.datetime | None
    deactivated_at: utc_dt.datetime | None

    class Config:
        from_attributes = True


class GroupingListParams(BaseModel):
    """Schema for grouping list query parameters."""

    status: str | None = None
    nominal_roll_id: str | None = None
    search: str | None = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


class GroupingStatusSessionInfo(BaseModel):
    """Schema for session information in grouping status."""

    status: Literal["open", "closed", "finalized"]
    present: int = 0
    absent: int = 0
    total: int = 0


class GroupingStatusUnitBreakdown(BaseModel):
    """Schema for unit-level breakdown in grouping status."""

    name: str
    total: int
    present: int
    absent: int


class GroupingStatusResponse(BaseModel):
    """Schema for grouping status response."""

    grouping_id: str
    grouping_name: str
    date: utc_dt.date
    grouping_status: Literal[
        "draft", "active", "inactive", "archived", "closed", "finalized"
    ]
    am_session: GroupingStatusSessionInfo | None = None
    pm_session: GroupingStatusSessionInfo | None = None
    units: list[GroupingStatusUnitBreakdown]


# ============================================================================
# Grouping Personnel Override Schemas
# ============================================================================


class GroupingPersonnelOverrideCreate(BaseModel):
    """Schema for creating a grouping personnel override."""

    personnel_id: str
    unit: str = Field(..., min_length=1)
    sub_unit_1: str | None = None
    sub_unit_2: str | None = None
    sub_unit_3: str | None = None
    checkbox: bool = False
    remarks: str | None = None


class GroupingPersonnelOverrideResponse(BaseModel):
    """Schema for grouping personnel override response."""

    id: str
    grouping_id: str
    personnel_id: str
    unit: str
    sub_unit_1: str | None
    sub_unit_2: str | None
    sub_unit_3: str | None
    checkbox: bool
    remarks: str | None
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime

    class Config:
        from_attributes = True


# ============================================================================
# Grouping Notes Schemas
# ============================================================================


class GroupingNotesCreate(BaseModel):
    """Schema for creating grouping notes."""

    personnel_id: str
    notes: str = Field(..., min_length=1)


class GroupingNotesUpdate(BaseModel):
    """Schema for updating grouping notes."""

    notes: str = Field(..., min_length=1)


class GroupingNotesResponse(BaseModel):
    """Schema for grouping notes response."""

    id: str
    grouping_id: str
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

    grouping_id: str
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

    grouping_id: str | None = None
    status: str | None = None
    date_from: utc_dt.datetime | None = None
    date_to: utc_dt.datetime | None = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)


# ============================================================================
# Attendance Schemas
# ============================================================================
#
# Attendance is NR/Tagging-scoped with hardcoded AM/PM slots. One record per
# (personnel, date) carries status + remarks for both AM and PM.
# Sessions are no longer user-managed (see /api/v1/sessions 410 stub).


class AttendanceUpsert(BaseModel):
    """Schema for a single per-person AM/PM attendance entry in a bulk upsert."""

    personnel_id: str
    date: utc_dt.date
    status_am: AttendanceStatus = "absent"
    remarks_am: str | None = None
    status_pm: AttendanceStatus = "absent"
    remarks_pm: str | None = None


class AttendanceResponse(BaseModel):
    """Schema for an attendance row response."""

    id: str
    personnel_id: str
    nominal_roll_id: str
    date: utc_dt.date
    status_am: str
    remarks_am: str | None
    status_pm: str
    remarks_pm: str | None
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


class AttendanceBulkUpsert(BaseModel):
    """Schema for bulk upserting attendance rows for a roster."""

    nominal_roll_id: str
    records: list[AttendanceUpsert]


class CopyRemarksResponse(BaseModel):
    """Schema for the copy-remarks endpoint result."""

    nominal_roll_id: str
    date: utc_dt.date
    slot: Literal["am", "pm"]
    updated: int
    skipped: int


# ============================================================================
# Personnel Schemas
# ============================================================================


class PersonnelBase(BaseModel):
    """Base personnel schema."""

    pers_no: str | None = Field(None, min_length=1)
    rank: str = Field(..., min_length=1)
    category: Literal["Officer", "WOSE"] = Field(
        ..., description="Operational corps, inferred from rank"
    )
    name: str = Field(..., min_length=1)
    unit: str = Field(..., min_length=1)
    sub_unit_1: str | None = None
    sub_unit_2: str | None = None
    sub_unit_3: str | None = None


class PersonnelResponse(PersonnelBase):
    """Schema for personnel response."""

    id: str
    nominal_roll_id: str
    status: str
    callup_status: str
    remarks: str | None
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
    callup_status: str | None = Field(
        None,
        description=f"Callup decision status (one of: {', '.join(CALLUP_STATUSES)})",
    )
    remarks: str | None = Field(
        None, max_length=2000, description="Per-person remarks (empty clears)"
    )

    @field_validator("callup_status")
    @classmethod
    def _callup_status_must_be_known(cls, v: str | None) -> str | None:
        if v is not None and v not in CALLUP_STATUSES:
            raise ValueError(
                f"callup_status must be one of: {', '.join(CALLUP_STATUSES)}"
            )
        return v

    @field_validator("remarks")
    @classmethod
    def _remarks_normalized(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


class PersonnelListParams(BaseModel):
    """Schema for personnel list query parameters."""

    nominal_roll_id: str | None = None
    grouping_id: str | None = None
    unit: str | None = None
    sub_unit_1: str | None = None
    sub_unit_2: str | None = None
    sub_unit_3: str | None = None
    status: str | None = None
    category: Literal["Officer", "WOSE"] | None = None
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


class PersonnelResponseWithGrouping(PersonnelBase):
    """Schema for personnel response with grouping-specific assignments."""

    id: str
    nominal_roll_id: str
    status: str
    callup_status: str
    remarks: str | None
    created_at: utc_dt.datetime
    updated_at: utc_dt.datetime | None
    created_by: str
    updated_by: str | None
    # Grouping-specific fields (included when grouping_id is provided)
    grouping_id: str | None = None
    has_override: bool = False
    grouping_notes: str | None = None

    class Config:
        from_attributes = True


class PersonnelAttendanceHistoryItem(BaseModel):
    """Schema for a single attendance row in personnel history."""

    id: str
    nominal_roll_id: str
    date: utc_dt.date
    status_am: str
    remarks_am: str | None
    status_pm: str
    remarks_pm: str | None
    created_at: utc_dt.datetime
    updated_at: utc_dt.datetime

    class Config:
        from_attributes = True


class PersonnelAttendanceHistoryStats(BaseModel):
    """Schema for attendance history statistics.

    AM and PM slots are counted independently toward totals (so one day with
    both slots present contributes 2 to ``total_slots``).
    """

    total_slots: int
    present_count: int
    absent_count: int
    attendance_rate: float  # Percentage of present-like slots vs total


class PersonnelAttendanceHistoryResponse(BaseModel):
    """Schema for personnel attendance history response."""

    personnel_id: str
    nominal_roll_id: str | None
    date_from: utc_dt.date | None
    date_to: utc_dt.date | None
    stats: PersonnelAttendanceHistoryStats
    attendance_records: list[PersonnelAttendanceHistoryItem]
    total_count: int


# ============================================================================
# Access Control Schemas
# ============================================================================


class GroupingUserAccessCreate(BaseModel):
    """Schema for creating grouping user access."""

    # Empty for now - access is granted by user/grouping IDs
    pass


class GroupingUserAccessResponse(BaseModel):
    """Schema for grouping user access response."""

    id: str
    user_id: str
    grouping_id: str
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
    grouping_id: str
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

    grouping_id: str | None = None
    unit: str | None = None


class UserSubunitAssignmentCreate(BaseModel):
    """Schema for granting a user attendance rights on one NR sub_unit_1."""

    sub_unit_1: str = Field(..., min_length=1)


class UserSubunitAssignmentResponse(BaseModel):
    """Schema for a user_subunit_assignment row."""

    id: str
    user_id: str
    nominal_roll_id: str
    sub_unit_1: str
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime

    class Config:
        from_attributes = True


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
    # Present only when the caller opted into auto-processing on the
    # upload endpoint: the created-NominalRoll pipeline result on
    # success, or the processing failure reason in ``process_error``.
    # The upload itself is always stored either way.
    process_result: "CsvUploadProcessResponse | None" = None
    process_error: str | None = None

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
    nominal_roll_id: str | None = None
    mapping_confirmed_at: utc_dt.datetime | None = None
    diff_confirmed_at: utc_dt.datetime | None = None

    class Config:
        from_attributes = True


class CsvUploadProcessRequest(BaseModel):
    """Schema for processing a stored CsvUpload into a NominalRoll.

    ``source_nominal_roll_id`` (optional): when set, copy the source NR's
    tagging entries into the new NR's auto-created tagging by matching
    personnel across NRs on ``pers_no``. Personnel in the source tagging
    with no pers_no match in the new NR are surfaced in the response.
    """

    source_nominal_roll_id: str | None = Field(None, min_length=1)
    created_by: str = Field(..., min_length=1)


class CsvUploadProcessUnmatchedItem(BaseModel):
    """Schema for an unmatched personnel row surfaced during tagging import."""

    pers_no: str
    name: str | None = None


class CsvUploadProcessResponse(BaseModel):
    """Schema for the process response — created NR plus ingestion diagnostics."""

    nominal_roll_id: str
    personnel_inserted: int
    rows_skipped: int
    tagging_entries_imported: int = 0
    unmatched: list[CsvUploadProcessUnmatchedItem] = []


# Resolve CsvUploadResponse's forward reference to the process result
# schema defined above.
CsvUploadResponse.model_rebuild()


# ============================================================================
# Nominal Roll Schemas
# ============================================================================


class NominalRollListItem(BaseModel):
    """Schema for a Nominal Roll list item (summary view)."""

    id: str
    caa: utc_dt.date
    attendance_active: bool = False
    personnel_count: int
    uploaded_at: utc_dt.datetime
    uploaded_by: str
    csv_hash: str
    # From the most recent linked CsvUpload (null until an upload is linked).
    original_filename: str | None = None
    label: str | None = None
    remarks: str | None = None

    class Config:
        from_attributes = True


class NominalRollResponse(NominalRollListItem):
    """Schema for a single Nominal Roll detail response."""

    notes: str | None = None
    attendance_activated_at: utc_dt.datetime | None = None
    attendance_activated_by: str | None = None
    created_at: utc_dt.datetime


class NominalRollUpdate(BaseModel):
    """Schema for updating a nominal roll (notes, label, remarks)."""

    notes: str | None = None
    label: str | None = Field(None, max_length=100)
    remarks: str | None = None

    @field_validator("label")
    @classmethod
    def _label_must_be_nonempty(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("label must not be empty or whitespace")
        return stripped


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


# ============================================================================
# Deferment Schemas
# ============================================================================

DefermentReasonLiteral = Literal[
    "Honeymoon",
    "Work",
    "Full-time studies",
    "Other",
    "Medical Grounds",
    "Examination",
    "New employment",
    "Special employment",
    "Compassionate",
    "Childbirth",
    "Part-time studies",
    "Newly Established Business (Local)",
]

DefermentStatusLiteral = Literal[
    "Approved",
    "Withdrawn",
    "Rejected",
    "To Resubmit",
    "Time off arrangement",
    "Pending action",
    "Not called up",
    "Do not call up",
]


class DefermentCreate(BaseModel):
    """Schema for creating a deferment.

    ``rank_name`` and ``sub_unit`` are snapshotted server-side from the linked
    personnel record; clients only send ``personnel_id`` plus the request fields.
    """

    personnel_id: str = Field(..., min_length=1)
    reason: DefermentReasonLiteral
    remarks: str | None = None
    oc_updates: str | None = None


class DefermentUpdate(BaseModel):
    """Schema for updating a deferment."""

    reason: DefermentReasonLiteral | None = None
    status: DefermentStatusLiteral | None = None
    remarks: str | None = None
    oc_updates: str | None = None


class DefermentResponse(BaseModel):
    """Schema for deferment API responses."""

    id: str
    personnel_id: str
    nominal_roll_id: str | None = None
    rank_name: str
    sub_unit: str | None = None
    reason: str
    status: str
    remarks: str | None = None
    oc_updates: str | None = None
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime | None = None
    updated_by: str | None = None

    class Config:
        from_attributes = True


# ============================================================================
# Tagging Schemas
# ============================================================================


class TaggingEntryInput(BaseModel):
    """Client-supplied person → subunit remap.

    ``personnel_id`` must belong to the parent tagging's nominal roll
    (server-enforced). ``from_*`` is optional — if omitted, the server
    snapshots the linked personnel's canonical subunit at create time.
    At least ``to_unit`` must be supplied.
    """

    personnel_id: str = Field(..., min_length=1)
    from_unit: str | None = Field(None, max_length=255)
    from_sub_unit_1: str | None = Field(None, max_length=255)
    from_sub_unit_2: str | None = Field(None, max_length=255)
    from_sub_unit_3: str | None = Field(None, max_length=255)
    to_unit: str = Field(..., min_length=1, max_length=255)
    to_sub_unit_1: str | None = Field(None, max_length=255)
    to_sub_unit_2: str | None = Field(None, max_length=255)
    to_sub_unit_3: str | None = Field(None, max_length=255)


class TaggingEntryResponse(BaseModel):
    """Schema for tagging entry responses."""

    id: str
    tagging_id: str
    personnel_id: str
    personnel_pers_no: str | None = None
    personnel_label: str | None = None
    from_unit: str | None
    from_sub_unit_1: str | None
    from_sub_unit_2: str | None
    from_sub_unit_3: str | None
    to_unit: str
    to_sub_unit_1: str | None
    to_sub_unit_2: str | None
    to_sub_unit_3: str | None

    class Config:
        from_attributes = True


class TaggingCreate(BaseModel):
    """Schema for creating a tagging.

    Under the 1:1 model, taggings are auto-created on NR ingestion and this
    schema is rarely used directly. ``label`` is optional and informational.
    """

    label: str | None = Field(None, max_length=100)
    nominal_roll_id: str = Field(..., min_length=1)
    remarks: str | None = None
    entries: list[TaggingEntryInput] = Field(default_factory=list)


class TaggingUpdate(BaseModel):
    """Schema for updating a tagging.

    If ``entries`` is provided, the tagging's entries are full-replaced.
    Omit ``entries`` to leave the existing entries untouched while updating
    label/remarks.
    """

    label: str | None = Field(None, max_length=100)
    remarks: str | None = None
    entries: list[TaggingEntryInput] | None = None


class TaggingListItem(BaseModel):
    """Schema for a tagging summary in list responses (no entries)."""

    id: str
    label: str | None = None
    nominal_roll_id: str
    remarks: str | None = None
    entry_count: int = 0
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime | None = None
    updated_by: str | None = None

    class Config:
        from_attributes = True


class TaggingResponse(BaseModel):
    """Schema for a single tagging detail response (with entries)."""

    id: str
    label: str | None = None
    nominal_roll_id: str
    remarks: str | None = None
    entries: list[TaggingEntryResponse] = []
    created_at: utc_dt.datetime
    created_by: str
    updated_at: utc_dt.datetime | None = None
    updated_by: str | None = None

    class Config:
        from_attributes = True


class TaggingCloneCreate(BaseModel):
    """Schema for merging a source tagging's entries into a target NR's tagging."""

    target_nominal_roll_id: str = Field(..., min_length=1)


class TaggingCloneUnmatchedItem(BaseModel):
    """Schema for an unmatched personnel row surfaced by clone."""

    pers_no: str
    name: str | None = None


class TaggingCloneResponse(BaseModel):
    """Schema for clone response — target tagging plus clone diagnostics."""

    tagging: TaggingResponse
    source_count: int
    matched_count: int
    unmatched: list[TaggingCloneUnmatchedItem]
