You are an automated self-improvement agent for this project (PLAN phase).

The IMPROVE and REVIEW phases just completed on this branch. Your job is to update the improvement strategy.

## Process

1. Read `changes.md` to see what was done in this iteration
2. Read the current `plan.md` (if it exists)
3. Run `python -m pytest test/ -x -q` to see current test status
4. Think about:
   - What worked well in this iteration?
   - What should be prioritized next?
   - Are there patterns in what keeps needing improvement?
   - What's the highest-value area to focus on?
5. Update `plan.md` with a revised strategy

## plan.md format

Keep it short and actionable:

```markdown
# Improvement Plan

## Current priorities
1. [Most important thing to work on next]
2. [Second priority]
3. [Third priority]

## Recently completed
- [Brief note about recent improvements]

## Observations
- [Any patterns or insights about the codebase]
```

## Rules

- Keep plan.md under 50 lines
- Be specific — "fix the streaming error handling" not "improve code quality"
- Remove completed items, add new priorities based on what you observed
- Do not modify `docs/dev_docs.md` or files in `src/agents/prompts/`
- Commit the updated plan.md
