---
description: Use when implementing or editing scaffold deliverables generated from
  a META-COMPILER Decision Log. Enforces Stage 3 contract, scope boundaries, and traceability.
name: execution-instructions
applyTo:
- code/**
- report/**
- tests/**
- references/**
- requirements/**
---
# EXECUTION_INSTRUCTIONS

Decision Log Version: v1
Project Type: hybrid

## Stage 3 Contract
- Read only Decision Log artifacts.
- Preserve constraints and scope decisions.
- Emit traceable outputs with requirement and citation anchors.

## Requirement IDs
- REQ-001: Decision log must be schema-valid and citation-traceable.
- REQ-002: Scaffold generator must consume Decision Log only.
