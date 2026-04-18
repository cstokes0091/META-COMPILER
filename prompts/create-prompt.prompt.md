---
name: create-prompt
description: "Create a reusable prompt file (.prompt.md) for a common task."
argument-hint: "What task should this prompt help with?"
agent: agent
---
Related skill: `agent-customization`. Load and follow [prompts.md](../skills/agent-customization/references/prompts.md) for template and principles.

Guide the user to create a `.prompt.md`.

## Extract from Conversation
First, review the conversation history. If the user has been working on a repeatable task pattern, generalize that into a reusable prompt. Extract:
- The core task being performed repeatedly
- Any implicit inputs such as selected code, file type, or context
- The desired output format or style

## Clarify if Needed
If no clear pattern emerges from the conversation, clarify:
- What task should this prompt help with?
- Should it take arguments or use fixed context?
- Workspace-scoped or personal?

## Iterate
1. Draft the prompt and save it.
2. Identify the weakest or most ambiguous parts and ask about those.
3. Once finalized, summarize what the prompt does, suggest example invocations, and propose related customizations to create next.

Remember to follow the `agent-customization` guidance and keep the prompt focused on a single task.