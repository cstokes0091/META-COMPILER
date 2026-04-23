"""Capability graph: the post-dialogue compile output consumed by Stage 3 downstream stages."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .verification import VerificationType


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=240)
    when_to_use: list[str] = Field(min_length=1)
    required_finding_ids: list[str] = Field(min_length=1)
    io_contract_ref: str = Field(min_length=1)
    verification_type: VerificationType
    verification_hook_ids: list[str] = Field(min_length=1)
    requirement_ids: list[str] = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)
    composes: list[str] = Field(default_factory=list)


class CapabilityGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: str = Field(min_length=1)
    decision_log_version: int = Field(ge=1)
    project_type: str = Field(min_length=1)
    capabilities: list[Capability] = Field(min_length=1)
