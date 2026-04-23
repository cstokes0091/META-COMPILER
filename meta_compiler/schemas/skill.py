"""SKILL.md frontmatter and skills/INDEX.md shapes."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .references import FindingRef


class SkillFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=240)
    triggers: list[str] = Field(min_length=1)
    required_finding_ids: list[str] = Field(min_length=1)
    contract_refs: list[str] = Field(min_length=1)
    verification_hooks: list[str] = Field(min_length=1)
    findings: list[FindingRef] = Field(min_length=1)


class SkillIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_name: str = Field(min_length=1)
    trigger_keywords: list[str] = Field(min_length=1)
    skill_path: str = Field(min_length=1)
    contract_refs: list[str] = Field(min_length=1)
    composes: list[str] = Field(default_factory=list)


class SkillIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: str = Field(min_length=1)
    decision_log_version: int = Field(ge=1)
    entries: list[SkillIndexEntry] = Field(min_length=1)
