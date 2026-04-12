# AGENTS.md

## 1. Mission & Priorities
**Role of the agent in this repository:**
- <DESCRIBE THE AGENT’S ROLE>

**Decision priority order:**
- <E.G. correctness > security > maintainability > performance > speed>

**Global constraints or goals:**
- <NON-OBVIOUS CONSTRAINTS, IF ANY>

## 2. Executable Commands (Ground Truth)
All commands listed here must work.

- Install / setup:
  - `<COMMAND>`
- Dev server:
  - `<COMMAND>`
- Lint:
  - `<COMMAND>`
- Format:
  - `<COMMAND>`
- Type check:
  - `<COMMAND>`
- Unit tests:
  - `<COMMAND>`
- Integration / e2e tests:
  - `<COMMAND>` or `N/A`

## 3. Repository Map
**High-level structure:**
- `<PATH>` — <WHAT LIVES HERE>
- `<PATH>` — <WHAT LIVES HERE>

**Entry points:**
- Backend: `<FILE / PATH>`
- Frontend: `<FILE / PATH>`
- CLI / Worker / Service: `<FILE / PATH>` or `N/A`

**Key configuration locations:**
- `<FILE / PATH>` — <PURPOSE>

## 4. Definition of Done
For any change, the following must hold:
- [ ] <REQUIRED TESTS ADDED OR UPDATED>
- [ ] <REQUIRED CHECKS RUN>
- [ ] <DOCS UPDATED IF BEHAVIOR CHANGED>
- [ ] <SUMMARY / NOTES EXPECTATION>

## 5. Code Style & Conventions (Repo-Specific)
Only list conventions that are easy to get wrong.

- Language(s) + version(s):
  - `<LANGUAGE@VERSION>`
- Formatter:
  - `<TOOL + CONFIG + COMMAND>`
- Naming conventions:
  - `<RULE>`
- Error handling pattern:
  - `<RULE / PATTERN>`
- Logging rules:
  - `<WHAT TO LOG / WHAT NOT TO LOG>`

## 6. Boundaries & Guardrails
The agent must **not**:
- <FORBIDDEN ACTION>
- <FORBIDDEN ACTION>
- <FORBIDDEN ACTION>

When unsure:
- Prefer the smallest possible change
- Leave a TODO with context rather than guessing

## 7. Security & Privacy Constraints
If applicable:
- Sensitive data locations:
  - `<PATH>`
- Redaction / handling rules:
  - `<RULE>`
- Approved crypto / storage patterns:
  - `<PATTERN>`
- Threat model notes:
  - `<ASSUMPTION>`

If not applicable, explicitly state: `N/A`.

## 8. Common Pitfalls & Couplings
Things that are easy to break:
- If you touch `<X>`, you must also update `<Y>`
- Do not import `<FORBIDDEN IMPORT>`; use `<ALTERNATIVE>`
- <OTHER COMMON MISTAKE>

## 9. Examples & Canonical Patterns (Optional)
Only include if useful.

### Example: <TASK NAME>
- Files to edit:
  - `<FILE>`
- Tests to add:
  - `<FILE>`
- Commands to run:
  - `<COMMAND>`

## 10. Pull Requests & Branching
Default branch: main

When a PR is requested, create a branch agent/<branch_name> and create a PR from there using gh
