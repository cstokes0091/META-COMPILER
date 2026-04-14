---
description: Use when mapping scaffold outputs to requirement IDs, citation IDs, and
  Decision Log constraints.
name: decision-trace-instructions
applyTo:
- code/**
- report/**
- tests/**
- references/**
- requirements/**
---
# DECISION_TRACE_INSTRUCTIONS

## Workflow
1. Read conventions and architecture from Decision Log.
2. Map outputs to requirement IDs.
3. Attach citation IDs to claims and constraints.

## Guardrails
- Do not infer uncaptured design decisions.
- Escalate missing information to Stage 2 instead of improvising.
