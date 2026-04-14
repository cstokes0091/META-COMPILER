# [Agent Skills (SKILL.md)](https://code.visualstudio.com/docs/copilot/customization/agent-skills)

Folders of instructions, scripts, and resources that agents load on-demand for specialized tasks.

## Structure

```
.github/skills/<skill-name>/
|- SKILL.md
|- scripts/
|- references/
`- assets/
```

## Locations

| Path | Scope |
|------|-------|
| `.github/skills/<name>/` | Project |
| `.agents/skills/<name>/` | Project |
| `.claude/skills/<name>/` | Project |
| `~/.copilot/skills/<name>/` | Personal |

## SKILL.md Format

```yaml
---
name: skill-name
description: 'What and when to use. Max 1024 chars.'
argument-hint: 'Optional hint shown for slash invocation'
user-invocable: true
disable-model-invocation: false
---
```

## Template

```markdown
---
name: webapp-testing
description: 'Test web applications using Playwright. Use for verifying frontend, debugging UI, capturing screenshots.'
---

# Web Application Testing

## When to Use
- Verify frontend functionality
- Debug UI behavior

## Procedure
1. Start the web server
2. Run [test script](./scripts/test.js)
3. Review screenshots in `./screenshots/`
```

## Progressive Loading

1. **Discovery**: Agent reads `name` and `description`
2. **Instructions**: Loads `SKILL.md` body when relevant
3. **Resources**: Additional files load only when referenced

Keep file references one level deep from `SKILL.md`.

## Slash Command Behavior

Skills and prompt files both appear after typing `/` in chat.

## Core Principles

1. **Keyword-rich descriptions**
2. **Progressive loading**
3. **Relative paths**
4. **Self-contained procedures**

## Anti-patterns

- **Vague descriptions**
- **Monolithic SKILL.md**
- **Name mismatch**
- **Missing procedures**