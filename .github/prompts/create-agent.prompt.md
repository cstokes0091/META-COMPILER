---
name: create-agent
description: "Create a custom agent (.agent.md) for a specific job."
argument-hint: "What job should this agent do and how?"
agent: agent
---
Related skill: `agent-customization`. Load and follow [agents.md](../skills/agent-customization/references/agents.md) for template and principles.

Guide the user to create an `.agent.md`.

## Extract from Conversation
First, review the conversation history. If the user has been using the agent in a specialized way, generalize that into a custom agent. Extract:
- The specialized role or persona being assumed
- Tool preferences, including which tools to use and avoid
- The domain or job scope

## Clarify if Needed
If no clear specialization emerges from the conversation, clarify:
- What job should this agent do?
- When should it be picked over the default agent?
- Which tools should it use or avoid?

Explore the codebase using subagents if you need more context.

## Iterate
1. Draft the agent and save it.
2. Identify the weakest or most ambiguous parts and ask about those.
3. Once finalized, summarize what the agent does, suggest example prompts to try it, and propose related customizations to create next.

Remember to follow the `agent-customization` guidance and keep the agent tightly scoped.