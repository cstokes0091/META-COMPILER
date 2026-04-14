---
name: core-scaffold
description: Use when generating deterministic scaffold outputs from a META-COMPILER
  Decision Log while preserving requirement IDs, citations, and scope boundaries.
---
# Skill: Core Scaffold Generation

Decision Log Version: v1
Project Type: hybrid

## Goal
Generate deterministic scaffolds from the Decision Log with explicit traceability.

## Inputs
- decision_log

## Required Behaviors
- Respect in-scope and out-of-scope boundaries.
- Propagate requirement IDs into generated outputs.
- Preserve citation IDs exactly as provided.
