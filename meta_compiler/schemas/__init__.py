"""Typed schemas for the post-dialogue pipeline (capabilities → contracts → skills).

Producers (stage modules) construct and validate via these models and serialize
with .model_dump() → YAML. Hooks intentionally do NOT import Pydantic — they
duplicate the required-field subset so .github/hooks/bin/meta_hook.py remains
stdlib-only (its docstring contract).
"""
from __future__ import annotations

from .capability import Capability, CapabilityGraph
from .contract import (
    Contract,
    ContractIOField,
    ContractManifest,
    ContractManifestEntry,
    ContractModality,
)
from .references import CitationRef, FindingRef
from .skill import SkillFrontmatter, SkillIndex, SkillIndexEntry
from .verification import VerificationHook, VerificationType

__all__ = [
    "Capability",
    "CapabilityGraph",
    "CitationRef",
    "Contract",
    "ContractIOField",
    "ContractManifest",
    "ContractManifestEntry",
    "ContractModality",
    "FindingRef",
    "SkillFrontmatter",
    "SkillIndex",
    "SkillIndexEntry",
    "VerificationHook",
    "VerificationType",
]
