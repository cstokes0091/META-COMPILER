# [Workspace Instructions](https://code.visualstudio.com/docs/copilot/customization/custom-instructions)

Guidelines that automatically apply to all chat requests across your entire workspace.

## File Types (Choose One)

| File | Location | Purpose |
|------|----------|---------|
| `copilot-instructions.md` | `.github/` | Project-wide standards |
| `AGENTS.md` | Root or subfolders | Open standard, monorepo hierarchy support |

Use **only one** unless you have a deliberate reason to layer them.

## AGENTS.md Hierarchy

For monorepos, the closest file in the directory tree takes precedence.

## Template

```markdown
# Project Guidelines

## Code Style
{Language and formatting preferences}

## Architecture
{Major components and boundaries}

## Build and Test
{Commands to install, build, test}

## Conventions
{Patterns that differ from common practice}
```

## When to Use

- General coding standards that apply everywhere
- Team preferences shared through version control
- Project-wide requirements

## Core Principles

1. **Minimal by default**
2. **Concise and actionable**
3. **Link, don't embed**
4. **Keep current**