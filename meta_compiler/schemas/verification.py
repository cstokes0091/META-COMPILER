"""Verification hooks emitted by Stage 3 as pytest stubs for Stage 4 to flesh out."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .references import FindingRef


class VerificationType(str, Enum):
    unit_test = "unit_test"
    numerical = "numerical"
    regression = "regression"
    contract_fixture = "contract_fixture"
    static_lint = "static_lint"
    human_review = "human_review"


class VerificationHook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook_id: str = Field(min_length=1)
    verification_type: VerificationType
    entrypoint: str = Field(min_length=1)
    contract_ref: str | None = None
    findings: list[FindingRef] = Field(default_factory=list)
