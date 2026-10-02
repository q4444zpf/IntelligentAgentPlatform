from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


ControlExecutionOutcome = Literal["succeeded", "failed", "unknown"]
ControlCommandStatus = Literal[
    "pending_approval", "approved", "rejected", "expired", "executing",
    "awaiting_feedback", "succeeded", "failed", "unknown", "deviation",
    "rollback_pending", "rolled_back",
]


class ControlPolicyCreateRequest(BaseModel):
    asset_type: str = Field(min_length=1, max_length=80)
    command_type: str = Field(min_length=1, max_length=80)
    parameter_schema: dict[str, Any]
    max_command_lifetime_seconds: int = Field(gt=0, le=31_536_000)
    approval_mode: Literal["single", "dual"] = "single"
    deviation_thresholds: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ControlPolicyInfo(ControlPolicyCreateRequest):
    model_config = ConfigDict(from_attributes=True)
    id: str
    unit_id: str
    project_id: str
    owner_id: str
    created_at: datetime
    updated_at: datetime


class ControlCommandProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str
    target: dict[str, Any]
    parameters: dict[str, Any]
    simulation_artifact_id: str
    valid_from: datetime
    valid_until: datetime
    rollback_plan: dict[str, Any] = Field(default_factory=dict)
    expected_state: dict[str, Any] | None = None
    idempotency_key: str = Field(min_length=1, max_length=180)
    run_id: str | None = None
    team_version_id: str | None = None


class ControlFeedbackRequest(BaseModel):
    actual_state: dict[str, Any]
    observed_at: datetime | None = None


class ControlCommandInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    unit_id: str
    project_id: str
    policy_id: str
    requester_id: str
    run_id: str | None
    team_version_id: str | None
    policy_digest: str
    deviation_thresholds_snapshot: dict[str, Any]
    feedback_observed_at: datetime | None
    target: dict[str, Any]
    parameters: dict[str, Any]
    parameter_digest: str
    simulation_artifact_id: str
    simulation_digest: str
    valid_from: datetime
    valid_until: datetime
    required_approvals: int
    rollback_plan: dict[str, Any]
    expected_state: dict[str, Any] | None
    actual_state: dict[str, Any] | None
    deviation_result: dict[str, Any] | None
    external_command_ref: str | None
    idempotency_key: str
    status: ControlCommandStatus
