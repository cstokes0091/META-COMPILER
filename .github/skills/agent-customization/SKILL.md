---
name: agent-customization
description: '**WORKFLOW SKILL** - Create, update, review, fix, or debug VS Code agent customization files (.instructions.md, .prompt.md, .agent.md, SKILL.md, copilot-instructions.md, AGENTS.md). USE FOR: saving coding preferences; troubleshooting why instructions or skills are ignored; configuring applyTo patterns; defining tool restrictions; creating custom agent modes or specialized workflows; packaging domain knowledge; fixing YAML frontmatter syntax. DO NOT USE FOR: general coding questions; runtime debugging or error diagnosis; MCP server configuration; VS Code extension development.'
---

# Agent Customization

## Decision Flow

| Primitive | When to Use |
|-----------|-------------|
| Workspace Instructions | Always-on, applies everywhere in the project |
| File Instructions | Explicit via `applyTo` patterns, or on-demand via `description` |
| MCP | Integrates external systems, APIs, or data |
| Hooks | Deterministic shell commands at agent lifecycle points |
| Custom Agents | Subagents for context isolation, or multi-stage workflows with tool restrictions |
| Prompts | Single focused task with parameterized inputs |
| Skills | On-demand workflow with bundled assets |

## Quick Reference

Consult the reference docs for templates, frontmatter options, asset organization, anti-patterns, and creation checklists.

| Type | File | Location | Reference |
|------|------|----------|-----------|
| Workspace Instructions | `copilot-instructions.md`, `AGENTS.md` | `.github/` or root | [Link](./references/workspace-instructions.md) |
| File Instructions | `*.instructions.md` | `.github/instructions/` | [Link](./references/instructions.md) |
| Prompts | `*.prompt.md` | `.github/prompts/` | [Link](./references/prompts.md) |
| Hooks | `*.json` | `.github/hooks/` | [Link](./references/hooks.md) |
| Custom Agents | `*.agent.md` | `.github/agents/` | [Link](./references/agents.md) |
| Skills | `SKILL.md` | `.github/skills/<name>/` | [Link](./references/skills.md) |

## Creation Process

If you need to explore or validate patterns in the codebase, use a read-only subagent.

Follow these steps when creating any customization file.

### 1. Determine Scope

Ask whether the customization belongs in the shared workspace or in a personal profile. META-COMPILER uses workspace-scoped `.github/` assets by default.

### 2. Choose the Right Primitive

Use the decision flow above to select the right file type based on the user's need.

### 3. Create the File

Create the file directly at the appropriate path:
- Use the location tables in each reference file
- Include required frontmatter
- Keep the body focused on the single responsibility of the artifact

### 4. Validate

After creating:
- Confirm the file is in the correct location
- Verify frontmatter syntax between `---` markers
- Check that `description` is present and meaningful
- Ensure linked references use repo-local relative paths

## Edge Cases

**Instructions vs Skill?** Most work or always-on guidance -> Instructions. Specific reusable workflow -> Skill.

**Skill vs Prompt?** Multi-step workflow with bundled assets -> Skill. Single focused task with inputs -> Prompt.

**Skill vs Custom Agent?** Same capabilities for all steps -> Skill. Need context isolation or tool restrictions -> Custom Agent.

**Hooks vs Instructions?** Instructions guide behavior. Hooks enforce behavior deterministically.

## Common Pitfalls

**Description is the discovery surface.** The `description` field is how the agent decides whether to load a skill, instruction, or agent. Include concrete trigger phrases.

**YAML frontmatter silent failures.** Unescaped colons, tabs, or name mismatches can cause the file to be ignored. Quote descriptions that contain punctuation-heavy phrases.

**`applyTo: "**"` burns context.** Use narrow globs unless the instruction truly applies everywhere.