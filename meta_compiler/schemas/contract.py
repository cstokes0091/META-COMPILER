"""I/O contracts separated from skills. One contract may back multiple skills."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .references import FindingRef


ContractModality = Literal["document", "code", "data", "config", "artifact"]


class ContractIOField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    modality: ContractModality
    schema_ref: str | None = None


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    inputs: list[ContractIOField] = Field(min_length=1)
    outputs: list[ContractIOField] = Field(min_length=1)
    invariants: list[str] = Field(min_length=1)
    test_fixtures: list[str] = Field(default_factory=list)
    required_findings: list[FindingRef] = Field(min_length=1)


class ContractManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(min_length=1)
    path: str = Field(min_length=1)


class ContractManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: str = Field(min_length=1)
    decision_log_version: int = Field(ge=1)
    entries: list[ContractManifestEntry] = Field(min_length=1)
