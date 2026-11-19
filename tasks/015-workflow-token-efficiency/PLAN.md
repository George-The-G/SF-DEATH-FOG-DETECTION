# Task 015: Workflow Token Efficiency Improvements

**Status:** `brainstorm` → ready for `plan`  
**Owner / Scope:** `tools/task.py`, `.agents/skills/deathfog-task-workflow/SKILL.md`, `.agents/skills/deathfog-agent-brief/SKILL.md`, `AGENTS.md`, `tests/test_task_workflow.py`

---

## 1. Baseline Evidence (Tasks 012–014 Transcript Audit)

From `833e5d95-bb3f-4a18-a7b7-0d33c9985261` (`gemini-4-neon-medium`, 65 turns across Tasks 012–014):
- **Totals:** `858,297` uncached input · `6,651,097` cache read · `65,135` output (`16.6k → 165.3k` context).
- **Task 014 coordinator vs. worker:** Coordinator spent **194.0k in / 1.73M cache** (12 turns) just scoping/delegating/merging, while the Hydrogen worker spent **85.6k in / 763.5k cache** (15 turns) doing the actual implementation & tests.
- **Cold-cache TTL misses:** **75.2%** (`645,145`) of all uncached `input_tokens` occurred on just **5 cache-miss turns** (`cache_read_tokens == 0`) after ~2–7 minute pauses (`2m08s`, `3m51s`, `6m34s`, `6m44s`, `6m56s`).
- **Worker test chatter (`flash_013`):** 8 sequential single-command turns (`T15–T22`) running tests one by one.

---

## Plan

### 1. Conversation Boundary & Ask-First `/compact` Prompts (`SKILL.md`, `AGENTS.md`, `tools/task.py`)
- **Post-`finish` prompt:** Update `SKILL.md`, `AGENTS.md`, and `cmd_finish` (`tools/task.py` L961–L966) so the agent **always asks the user** whether to run `/compact` or start a fresh chat when a task finishes and more tasks are queued.
- **Pre-pause `/compact` prompt:** On threads >60k tokens, **ask the user** before stepping away for a long human review (e.g., mockup/plan review) if they want to `/compact` first, since pauses >2m risk a full cold-cache re-read. *(Agents never compact silently; they always ask.)*

### 2. Multi-Agent `swarm` (Large Features) vs. `invoke_subagent` (Fast Path) (`SKILL.md`, `AGENTS.md`)
- **Large features & parallel work (`swarm` stays first-class):**
  - Use `swarm` whenever a task benefits from **multiple collaborating agents** (e.g., a `Hydrogen` backend worker + `flash` UI/mockup/test helper on the shared board, with `workspace: "share"` for concurrent edits), when pinning `Hydrogen`/`Sonnet` by name, or across **Wave 1 parallel DAG tasks** in separate worktrees.
  - **Swarm turn hygiene:** Batch `swarm(title)` + `swarm(add)` in one turn (avoid calling `swarm(models)` when the model name is already documented in `SKILL.md`), and batch `swarm(kill)` with review/finish turns.
- **Single-worker `flash` fast path (`invoke_subagent`):**
  - Use `invoke_subagent(TypeName="self", Model="flash", ...)` for standalone `flash` tasks and standalone mockups (saves `swarm(title)` + `read_board` + `swarm(kill)` = 3–4 coordinator turns). If the same `flash` agent will also implement the task after plan approval, reuse it via `send_message(Recipient=<conv_id>, ...)` to preserve its mockup context at zero swarm overhead.

### 3. Cheap, Ready-to-Use Mockups via `flash` (`SKILL.md`, `AGENTS.md`)
- When a UI mockup is needed during `brainstorm`/`plan`, the coordinator writes a concise layout/behavior spec in `PLAN.md` and delegates `tasks/<id>-<slug>/mockups/<name>.html` to `invoke_subagent(TypeName="self", Model="flash", ...)` (or a `flash` swarm member if collaborating live with another worker after `start`).
- Keeps ~5k–10k HTML generation tokens off Neon and out of the coordinator's permanent context while producing a ready-to-use mockup that both the user and implementation worker can inspect directly.

### 4. Deduplicate `tools/task.py` Plumbing & Worker Briefs (`tools/task.py`, `SKILL.md`, `deathfog-agent-brief/SKILL.md`)
- **`cmd_start` (`tools/task.py` L650–L666, `cli` L1747–L1751):** Replace the ad-hoc brief printout with a 1-line startup header + `build_resume_prompt(tid, worker=True)["prompt"]` (no duplicate outlines/metadata), plus a `--no-brief` flag to opt out. Eliminates the separate `task.py resume <id> --worker` turn.
- **Worker brief batching rule (`build_resume_prompt` L1549–L1554):** Add explicit instruction to batch independent `read_file` calls and combine targeted test + `verify` runs cleanly (avoiding 6–8 sequential `run_command` turns).
- **Skip duplicate `verify`/`pytest` on coordinator:** In `SKILL.md` & `AGENTS.md`, when a worker already ran `task.py verify <id>` and `evidence/verify.json` has `"ok": true` matching `HEAD`, the coordinator reviews `task.py diff <id>` + `verify.json` instead of re-running tests.
- **Zero-extra-cost `PLAN.md` handoff:** Coordinator copies only the `[Lstart-Lend]` ranges and symbol names it *already* read while drafting `PLAN.md` (no extra Neon reads).

### 5. Tests (`tests/test_task_workflow.py`)
- Add unit tests verifying:
  - `cmd_start` emits the canonical `--worker` brief by default (without duplicating outlines) and omits it when `--no-brief` is passed.
  - `cmd_finish` prints the ask-user `/compact` / new-chat reminder.
  - `build_resume_prompt(..., worker=True)` includes the batching rule.
