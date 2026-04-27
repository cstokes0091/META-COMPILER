"""Capability graph: the post-dialogue compile output consumed by Stage 3 downstream stages."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .verification import VerificationType


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=240)
    when_to_use: list[str] = Field(default_factory=list)
    required_finding_ids: list[str] = Field(default_factory=list)
    io_contract_ref: str = Field(min_length=1)
    verification_type: VerificationType
    verification_hook_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    constraint_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    composes: list[str] = Field(default_factory=list)
    verification_required: bool = True
    phase: str | None = None
    objective: str | None = None
    implementation_steps: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    explicit_triggers: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    parallelizable: bool | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "Capability":
        # Every capability must point at >=1 REQ or CON. Pure floating
        # capabilities are an error — they can't be traced.
        if not self.requirement_ids and not self.constraint_ids:
            raise ValueError(
                f"capability {self.name!r}: must reference >=1 requirement_id "
                "or constraint_id"
            )
        # When verification is required (the default), the legacy invariants
        # all apply: triggers, findings, hooks, citations must be non-empty.
        if self.verification_required:
            for field, value in [
                ("when_to_use", self.when_to_use),
                ("required_finding_ids", self.required_finding_ids),
                ("verification_hook_ids", self.verification_hook_ids),
                ("citation_ids", self.citation_ids),
            ]:
                if not value:
                    raise ValueError(
                        f"capability {self.name!r}: {field} must be non-empty "
                        "when verification_required is True"
                    )
        return self


class CapabilityGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: str = Field(min_length=1)
    decision_log_version: int = Field(ge=1)
    project_type: str = Field(min_length=1)
    capabilities: list[Capability] = Field(min_length=1)
