# [File-Specific Instructions (.instructions.md)](https://code.visualstudio.com/docs/copilot/customization/custom-instructions)

Guidelines loaded on-demand when relevant to the current task, or explicitly when files match a pattern.

## Locations

| Path | Scope |
|------|-------|
| `.github/instructions/*.instructions.md` | Workspace |
| `<profile>/instructions/*.instructions.md` | User profile |

## Frontmatter

```yaml
---
description: "<required>"
name: "Instruction Name"
applyTo: "**/*.ts"
---
```

## Discovery Modes

| Mode | Trigger | Use Case |
|------|---------|----------|
| **On-demand** (`description`) | Agent detects task relevance | Task-based instructions |
| **Explicit** (`applyTo`) | Files matching glob in context | File-type-specific standards |
| **Manual** | Add context manually | Ad-hoc attachment |

## Template

```markdown
---
description: "Use when writing database migrations, schema changes, or data transformations. Covers safety checks and rollback patterns."
---
# Migration Guidelines

- Always create reversible migrations
- Test rollback before merging
- Never drop columns in the same release as code removal
```

## Explicit File Matching

```yaml
applyTo: "**"
applyTo: "**/*.py"
applyTo: ["src/**", "lib/**"]
```

## Core Principles

1. **Keyword-rich descriptions**: Include trigger words for on-demand discovery
2. **One concern per file**: Separate testing, styling, API work
3. **Concise and actionable**: Keep the content tight
4. **Show, don't tell**: Brief examples over long explanations

## Anti-patterns

- **Vague descriptions**
- **Overly broad applyTo**
- **Duplicating docs**
- **Mixing concerns**