# [Prompts (.prompt.md)](https://code.visualstudio.com/docs/copilot/customization/prompt-files)

Reusable task templates triggered on-demand in chat. Single focused task with parameterized inputs.

## Locations

| Path | Scope |
|------|-------|
| `.github/prompts/*.prompt.md` | Workspace |
| `<profile>/prompts/*.prompt.md` | User profile |

## Frontmatter

```yaml
---
description: "<recommended>"
name: "Prompt Name"
argument-hint: "Task..."
agent: "agent"
model: "GPT-5 (copilot)"
tools: [search, web]
---
```

## Template

```markdown
---
description: "Generate test cases for selected code"
agent: "agent"
---
Generate comprehensive test cases for the provided code:
- Include edge cases and error scenarios
- Follow existing test patterns in the codebase
- Use descriptive test names
```

**Context references**: Use Markdown links for files and `#tool:<name>` for tools.

## Invocation

- Type `/` in chat to select prompts and skills
- Use `Chat: Run Prompt...`
- Open the prompt file and use the play button

## Tool Priority

When both a prompt and a custom agent define tools, prompt tools win.

## When to Use

- Generate tests for specific code
- Create READMEs from specs
- Summarize metrics with parameters
- One-off generation tasks

## Core Principles

1. **Single task focus**: One prompt = one task
2. **Output examples**: Show expected format when structure matters
3. **Reuse over duplication**: Reference instructions instead of copying them

## Anti-patterns

- **Multi-task prompts**
- **Vague descriptions**
- **Over-tooling**