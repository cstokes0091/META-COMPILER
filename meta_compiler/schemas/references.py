"""Shared value objects referenced across capability, contract, and skill schemas."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FindingRef(BaseModel):
    """A pointer into workspace-artifacts/wiki/findings/*.json.

    finding_id is the stable identifier `f"{citation_id}#{file_hash[:12]}"`
    minted by findings_loader. Paired with citation_id + seed_path so the
    reference can be resolved even if the finding file is reshuffled.
    """

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1)
    citation_id: str = Field(min_length=1)
    seed_path: str = Field(min_length=1)
    quote: str | None = Field(default=None, max_length=400)
    locator: dict[str, str | int] = Field(default_factory=dict)


class CitationRef(BaseModel):
    """A pointer into workspace-artifacts/wiki/citations/index.yaml."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(min_length=1)
