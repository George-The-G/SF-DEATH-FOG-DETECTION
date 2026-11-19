# Plan — Task 010: Task Viewer Stage Gates, PR/Merge Tracker, Conversation Links & Token-Efficient Execution

## Brainstorm — Goals, Decisions & Dead Ends
- **Goals:**
  - Human-approved stage gates (`brainstorm → plan → ready → in-progress → review → beta`) + `--quick` fast-track for small tasks.
  - Plan-Heavy / Execute-Cheap workflow (`pro` plans → `flash`/`hydrogen` implements via `resume --worker` → compact `diff` review).
  - Primary + collapsible Antigravity/Claude conversation links (`conversations.json`) with 1-click jump & sidecar `sendMessage` fallback.
  - Prominent GitHub PR links + live merge tracker (`Branch → PR → Beta (v3-dev) → Public (main)`).
- **Decisions:**
  - `HANDOFF.md` is **optional & overwrite-only** (never auto-scaffolded, never an append log); live git/verify state provides 90% of resume context for free.
  - Track `exec_tier` (`flash` | `hydrogen` | `standard` | `pro`) in `TASK.md`; use `flash` for mechanical/UI/test tasks and `hydrogen` for multi-file/logic tasks.
  - Compute live merge state via `git merge-base --is-ancestor` against `v3-dev` and `main` (plus `merged_sha` recorded by `finish --merge`).
- **Rejected / Dead Ends:**
  - Running/append-only `HANDOFF.md` log (bloats context & wastes write tokens).

## Plan — Architecture, Owned Paths & Verification
- **Owned Paths:** `tools/task.py`, `tools/task_viewer/server.py`, `tools/task_viewer/index.html`, `tools/task_viewer/antigravity_sidecar.mjs`, `tests/test_task_workflow.py`, `AGENTS.md`, `.agents/skills/deathfog-task-workflow/SKILL.md`, `.agents/skills/deathfog-agent-brief/SKILL.md`.
- **Verification:** `.\.venv\Scripts\python.exe tools/task.py verify 010 --skip-preview`.
