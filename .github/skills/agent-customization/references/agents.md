# [Custom Agents (.agent.md)](https://code.visualstudio.com/docs/copilot/customization/custom-agents)

Custom personas with specific tools, instructions, and behaviors. Use for orchestrated workflows with role-based tool restrictions.

## Locations

| Path | Scope |
|------|-------|
| `.github/agents/*.agent.md` | Workspace |
| `<profile>/agents/*.agent.md` | User profile |

## Frontmatter

```yaml
---
description: "<required>"
name: "Agent Name"
tools: [search, web]
model: "Claude Sonnet 4"
argument-hint: "Task..."
agents: [agent1, agent2]
user-invocable: true
disable-model-invocation: false
handoffs: [...]
---
```

## Invocation Control

| Attribute | Default | Effect |
|-----------|---------|--------|
| `user-invocable: false` | `true` | Hide from agent picker, only accessible as subagent |
| `disable-model-invocation: true` | `false` | Prevent other agents from invoking as subagent |

## Model Fallback

```yaml
model: ['Claude Sonnet 4.5 (copilot)', 'GPT-5 (copilot)']
```

## Tools

**Special**: `[]` = no tools, omit = defaults.

### Tool Aliases

| Alias | Purpose |
|-------|---------|
| `execute` | Run shell commands |
| `read` | Read file contents |
| `edit` | Edit files |
| `search` | Search files or text |
| `agent` | Invoke custom agents as subagents |
| `web` | Fetch URLs and web search |
| `todo` | Manage task lists |

## Common Patterns

```yaml
tools: [read, search]
tools: [read, edit, search]
tools: []
```

## Template

```markdown
---
description: "{Use when... trigger phrases for subagent discovery}"
tools: [{minimal set of tool aliases}]
user-invocable: false
---
You are a specialist at {specific task}. Your job is to {clear purpose}.

## Constraints
- DO NOT {thing this agent should never do}
- ONLY {the one thing this agent does}

## Approach
1. {Step one}
2. {Step two}
3. {Step three}

## Output Format
{Exactly what this agent should return}
```

## Core Principles

1. **Single role**: One persona with focused responsibilities per agent
2. **Minimal tools**: Only include what the role needs
3. **Clear boundaries**: Define what the agent should not do
4. **Keyword-rich description**: Include trigger words so parent agents know when to delegate

## Anti-patterns

- **Swiss-army agents**: Too many tools, tries to do everything
- **Vague descriptions**: Doesn't guide delegation
- **Role confusion**: Description doesn't match body persona
- **Circular handoffs**: A -> B -> A without progress criteria