# Plan — Task 013: Zero-Token Task Token Usage Metadata on Finish

## Brainstorm — Goals, Decisions & Dead Ends
- **Goal:** Automatically capture exact per-model token usage (`input_tokens`, `cache_read_tokens`, `output_tokens`, `turns`) for each finished task at **zero LLM token cost** by aggregating local Antigravity `transcript.jsonl` logs for all conversations linked in `conversations.json`, and display the summary in the Task Viewer `Details ▾` strip.
- **Decisions:**
  - Read `~/.gemini/antigravity/brain/<conv_id>/.system_generated/logs/transcript.jsonl` purely in Python (`tools/task.py`) — zero API calls, zero LLM tokens.
  - Store detailed per-model and per-conversation breakdown in `tasks/<id>-<slug>/evidence/usage.json` and a compact 1-line `token_usage` summary string (e.g. `"in:142k cache:98k out:11k (2 convos)"`) in `TASK.md` frontmatter (staged alongside `TASK.md` & `verify.json` inside `cmd_finish`).
  - Gracefully skip missing/non-Antigravity transcripts (never fail `cmd_finish` if a transcript is absent or on CI).

## Plan — Architecture, Owned Paths & Verification

### 1. Local Transcript Aggregator & Finish Hook (`tools/task.py`)
- **New helper `collect_token_usage(task_dir: Path, *, brain_root: Path | None = None) -> dict`** (in `@sec: handoff`, near `L1209-1276`):
  - Reads `load_conversations(task_dir)` (`primary` + `history`), filters `tool == "antigravity"` and validates `conv_id` against `^[A-Za-z0-9_-]{4,128}$`.
  - For each unique `conv_id`, scans `(brain_root or Path.home() / ".gemini" / "antigravity" / "brain") / conv_id / ".system_generated" / "logs" / "transcript.jsonl"` line-by-line (skipping malformed lines safely).
  - Sums `input_tokens`, `cache_read_tokens`, `output_tokens`, and `turns` for `type == "PLANNER_RESPONSE"`, both overall (`totals`) and grouped by `model` (`by_model`) and `conv_id` (`conversations`).
  - Produces a concise formatted `summary` string (e.g. `"in:45.2k cache:31.0k out:4.1k · 2 convos"`).
- **Hook into `cmd_finish` (`L873-955`):**
  - Before staging `TASK.md` and `verify.json`, call `usage = collect_token_usage(tdir)`.
  - If `usage["totals"]["turns"] > 0`, write `tasks/<id>-<slug>/evidence/usage.json`, set `meta["token_usage"] = usage["summary"]` in `TASK.md`, and include `usage.json` in the `git add` list before committing/merging.
- **Frontmatter order (`dump_task_md`, `L142-186`):**
  - Preserve optional `token_usage` field cleanly when present.

### 2. Task Viewer Exposure (`tools/task_viewer/server.py`, `tools/task_viewer/index.html`)
- **`tools/task_viewer/server.py` (`build_state`, `L182-264`):**
  - Load `evidence/usage.json` (if present) as `usage` on each task dict in `tasks_out[tid]` (fallback to `meta.get("token_usage", "")`).
- **`tools/task_viewer/index.html`:**
  - Show `Token Usage` row inside the collapsible `Details ▾` panel when `t.token_usage` or `t.usage` is present (with per-model breakdown in the tooltip/title attribute) and render `usage.json` cleanly in the `Evidence` tab alongside `verify.json`.

### 3. Tests (`tests/test_task_workflow.py`)
- `test_collect_token_usage_aggregates_local_transcripts`: writes mock `transcript.jsonl` files for 2 linked Antigravity convos (Neon + Hydrogen) into a temp `brain_root`, verifies exact per-model and total sums (`input_tokens`, `cache_read_tokens`, `output_tokens`, `turns`), path-traversal rejection, and graceful handling of missing transcripts.
- `test_viewer_state_exposes_usage_json`: verifies `build_state()` surfaces `usage` when `evidence/usage.json` exists.

### 4. Owned Paths & Verification
- **Owned Paths:** `tools/task.py`, `tools/task_viewer/server.py`, `tools/task_viewer/index.html`, `tests/test_task_workflow.py`.
- **Execution Tier:** `flash` (`invoke_subagent(TypeName="self", Model="flash", ...)` once formally approved).
- **Verification:** `.\.venv\Scripts\pytest.exe tests/test_task_workflow.py tests/test_repo_hygiene.py` and `.\.venv\Scripts\python.exe tools/task.py verify 013 --skip-preview`.
