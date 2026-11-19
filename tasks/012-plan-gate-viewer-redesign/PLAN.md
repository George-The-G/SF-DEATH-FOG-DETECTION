# Plan — Task 012: Formal Plan Approval Gate & Task Viewer Layout Redesign

## Brainstorm — Goals, Decisions & Dead Ends

### Goals
1. **Formal Plan Approval Gate (Zero Accidental Jump-Starts)**:
   - Require explicit, recorded human approval of `PLAN.md` before `tools/task.py start` (worktree creation / implementation) can proceed.
   - Close all existing bypasses: `task.py new --status ready`, approving an empty/template `PLAN.md`, `task.py start --force` skipping `PRE_BUILD_GATES`, and agents self-approving.
2. **Task Viewer Plugin Layout Redesign (`tools/task_viewer/`)**:
   - Eliminate visual clutter and duplicate controls (PR shown 4×, lifecycle/beta shown 4×, branch 3×, 10–11 simultaneous buttons).
   - Add **`Main`** (Public) into a single **Unified 7-Node Top Lifecycle Sequence**, replacing the redundant two-bar stack (`renderStepper` + `renderMergeBar`).
   - Fix the broken **Open Convo** button so every visible control actually works reliably.
   - Optimize for Antigravity's narrow auxiliary side pane (`420px–680px`) as well as wide desktop.

### Design Review Findings (UI/UX Audit)
- **Duplicated controls removed**:
  - PR link (4× → 1× on the `Review` sequence node + contextual primary CTA when in `review`)
  - Lifecycle + Ship Track (2 stacked bars → 1 unified 7-node bar: `Scope ─ Plan(🔒) ─ Approved ─ Build ─ Review ─ Beta ─ Main`)
  - Branch / Worktree (3× → `WT` dot on `Build` node + collapsible `Details ▾`)
  - 10–11 simultaneous buttons → Stage-adaptive Action Bar (2–4 buttons max) + `Agent Brief ▾` menu + `⋯` utility overflow.
- **Root cause of broken "Open Convo" button**:
  - `sendConvoLinkToChat()` in `tools/task_viewer/index.html` called `window.sidecar.agent.sendMessage("Jump to linked conversation: [..](conversation://<id>)")`. Antigravity's sidecar SDK has **no API to switch the host's active conversation tab** — `sendMessage` injects a user chat message into the *current* agent (triggering an unwanted turn, where user-bubble `conversation://` links aren't clickable), and in standalone/iframe mode it silently copies markdown to clipboard and logs off-screen.
  - **Honest replacement**:
    - Antigravity linked convos: **Copy ID** (1-click raw UUID copy with inline `Copied ✓` button feedback) + **Brain / Artifacts** (new `server.py` action to open `~/.gemini/antigravity/brain/<conv_id>/` or preview its artifacts/summary inline).
    - Claude linked convos: **Resume in Terminal** (`launch_claude`) + **Copy ID**.
    - Prompt handoff buttons (`Copy Worker Brief`, `Copy Resume Prompt`, `Send Brief to Active Chat`, `Launch Fresh Claude`) move out of the Convo strip into a dedicated **`Agent Brief ▾`** menu.
- **Narrow side-pane (`<= 680px`) reclaim**:
  - Replace the stacked 240px `<aside>` with a 1-line **Task Switcher Bar** (`▼ #012 · slug [Plan]`) opening an overlay Wave drawer (~200px vertical space reclaimed so `PLAN.md` is visible above the fold).
  - Replace embedded `#action-log` with a dismissible top status toast.
  - Reorder tabs to workflow order: `PLAN.md` · `TASK.md` · `Mockups` · `Evidence` · `Handoff` (dimmed if absent).

### Proposed Formal Plan Approval Gate Mechanics
- **`TASK.md` frontmatter fields** (backward-compatible for legacy tasks already in `in-progress`/`beta`/`done`):
  - `plan_approved: false | true`
  - `plan_approved_at: "<ISO-8601 UTC>"`
  - `plan_approved_by: "human-ui" | "human-cli"`
  - `plan_approved_sha: "<12-char sha256 of PLAN.md>"`
- **Plan Readiness Check (`check_plan_readiness(task_dir, meta)`)**:
  - Verifies `PLAN.md` exists, has non-template content under `## Plan`, and `owned_paths` is non-empty.
  - Exposes structured checklist (`plan_readiness`) in `server.py` state for the Task Viewer Gate Callout.
  - Detects `plan_drifted` if `PLAN.md` hash changes after approval.
- **Hard CLI & UI Enforcement**:
  - `cmd_new`: rejects `--status ready` or `--status in-progress` (must start in `brainstorm` or `--quick` → `plan`).
  - `approve_task_gate` (`plan -> ready`): requires `check_plan_readiness().ready == True` and explicit `--approve-plan` (CLI) or `approve_plan` action (UI, with explicit confirmation / checklist check).
  - `cmd_start`: blocks if `status in PRE_BUILD_GATES` **or** `plan_approved` is not `True` — `--force` does **not** bypass the formal plan approval gate.
  - `cmd_verify`: fails if `plan_approved` is explicitly `False`.
  - `AGENTS.md` & `deathfog-task-workflow/SKILL.md`: explicitly forbid agents from self-approving (`--approve-plan` / `plan_approved: true`).

### Decisions Locked In
1. **Plan drift after approval:** `cmd_start` **blocks until re-approved** if `PLAN.md` SHA differs from `plan_approved_sha`; once already `in-progress`, UI & CLI show a non-blocking `Plan drifted` warning.
2. **Antigravity Convo button (0 LLM tokens):** Opens a **100% local inline preview modal** inside the Task Viewer (served by `server.py` reading `~/.gemini/antigravity/brain/<conv_id>/` artifacts & compact transcript metadata, plus an "Open Folder" button inside). Never sends chat messages or wakes an agent.
3. **Clickable HTML Mockup:** Build an interactive mockup in `tasks/012-plan-gate-viewer-redesign/mockups/` during the `plan` stage for visual sign-off before approving build.

### Proposed Test Cases (`tests/test_task_workflow.py` + Playwright Evidence)
1. **Formal Plan Gate (`tools/task.py`):**
   - `test_new_rejects_pre_approved_statuses` — `cmd_new` rejects `--status ready` / `in-progress`.
   - `test_plan_readiness_rejects_empty_or_template_plan` — `check_plan_readiness()` fails on untouched scaffold `PLAN.md` or empty `owned_paths`, passes once populated.
   - `test_approve_plan_requires_readiness_and_explicit_flag` — `plan -> ready` requires readiness + `--approve-plan` (CLI) or `approve_plan` (UI), stamping `plan_approved`, `plan_approved_at`, `plan_approved_by`, `plan_approved_sha`.
   - `test_start_blocks_unapproved_and_drifted_even_with_force` — `cmd_start(..., force=True)` still blocks if `plan_approved` is false OR `PLAN.md` drifted after approval.
   - `test_verify_fails_when_plan_unapproved` — `cmd_verify` fails if `plan_approved` is explicitly `False` (backward-compatible for legacy tasks).
2. **Server API & Zero-Token Convo Modal (`tools/task_viewer/server.py`):**
   - `test_viewer_state_exposes_plan_readiness_and_drift` — `build_state()` includes `plan_readiness`, `plan_approved`, `plan_drifted`.
   - `test_convo_preview_reads_local_brain_artifacts_safely` — `preview_conversation` reads local brain artifacts/summary, rejects path traversal (`../`), handles missing IDs cleanly.
   - `test_viewer_approve_plan_action` — `run_action({"action": "approve_plan", ...})` stamps `human-ui` and rejects unready plans.
3. **UI & Responsive Verification (`tools/task_viewer/index.html`):**
   - `test_viewer_html_unified_sequence_and_no_broken_convo_jump` — asserts `Main` in top sequence, no `sendConvoLinkToChat`, no raw `href="conversation://"`, no duplicate `renderMergeBar`.
   - Headless Playwright check at `460×800` (aux pane) and `1280×800` (desktop): zero console errors, 7-node sequence with `Main` visible, `PLAN.md` above the fold, inline Convo modal opens cleanly, screenshots saved to `evidence/`.

---

## Plan — Architecture, Owned Paths & Verification

### 1. Formal Plan Approval Gate (`tools/task.py`)
- **Frontmatter & Schema (`L63-80`, `L141-198`):**
  - Support optional formal approval keys in `TASK.md` frontmatter (ordered cleanly in `dump_task_md`):
    - `plan_approved: bool` (initialized to `False` in `cmd_new` at `L495-511`)
    - `plan_approved_at: str` (ISO-8601 UTC timestamp, e.g. `2026-11-19T10:30:00+00:00`)
    - `plan_approved_by: str` (`"human-ui"` | `"human-cli"`)
    - `plan_approved_sha: str` (first 12 hex chars of SHA-256 of normalized `PLAN.md`)
  - Helper `compute_plan_sha(task_dir: Path) -> str`: hashes `PLAN.md` UTF-8 text (LF-normalized) → `hashlib.sha256(...).hexdigest()[:12]` (or `""` if missing).
- **Plan Readiness & Drift Validator (`L1023-1048`, `@sec: handoff`):**
  - Add `check_plan_readiness(task_dir: Path, meta: dict) -> dict`:
    - `plan_exists`: `PLAN.md` exists and has `>= 120` chars.
    - `plan_has_spec`: `## Plan` section exists and contains real non-template content (not just scaffold placeholder lines like `- **Architecture:**`, `- **Owned Paths:**`, `*(To be finalized...)*`, or bare `TODO`).
    - `owned_paths_set`: `bool(meta.get("owned_paths"))`.
    - `plan_sha`: `compute_plan_sha(task_dir)`.
    - `plan_approved`: `bool(meta.get("plan_approved", False))`.
    - `plan_drifted`: `bool(plan_approved and meta.get("plan_approved_sha") and plan_sha != meta.get("plan_approved_sha"))`.
    - Returns `{"ready": bool, "checks": [...], "plan_sha": str, "plan_approved": bool, "plan_drifted": bool, "plan_approved_at": str, "plan_approved_by": str}`.
- **Hard Gate Enforcement:**
  - `cmd_new` (`L454-516`): reject `initial_status in ("ready", "in-progress", "review", "beta", "done")` with exit code `1` (must start in `backlog`, `brainstorm`, or `plan` via `--quick`). Initialize `plan_approved: False`, `plan_approved_at: ""`, `plan_approved_by: ""`, `plan_approved_sha: ""`.
  - `approve_task_gate(task_id, target_status=None, *, approve_plan=False, approved_by="human-cli")` (`L1029-1047`):
    - When `nxt == "ready"` (or re-approving a drifted `ready` plan):
      - Require `approve_plan=True`; otherwise return `{"ok": False, "error": "Formal plan approval required: pass --approve-plan (or click 'Approve Plan & Unlock Build' in Task Viewer)."}`.
      - Run `check_plan_readiness(tfile.parent, meta)`; if not `ready`, return `{"ok": False, "error": "PLAN.md is not ready for approval: ...", "plan_readiness": ...}`.
      - Stamp `plan_approved = True`, `plan_approved_at = utc_now`, `plan_approved_by = approved_by`, `plan_approved_sha = readiness["plan_sha"]`.
    - When `nxt in ("backlog", "brainstorm", "plan")`: reset `plan_approved = False` and clear approval stamp.
  - `cmd_approve` (`L1353-1357`) & CLI parser (`L1430-1433`): add `--approve-plan` flag and `--by` (default `"human-cli"`).
  - `cmd_start` (`L531-547`):
    - Block if `meta.get("status") in PRE_BUILD_GATES` **OR** `meta.get("plan_approved") is not True` (even if `--force` is passed! `--force` only overrides deps/paths/GPU collisions, never the formal plan approval gate).
    - Block if `plan_drifted` is `True` (`PLAN.md` modified since `plan_approved_sha`) until re-approved via `approve --approve-plan`.
    - Legacy compatibility: if a task is *already* `in-progress` with an active worktree, allow refreshing the brief (with a non-blocking drift warning if `plan_drifted`).
  - `cmd_verify` (`L651-676`):
    - Add check `checks["plan_approval"]`: fails (`ok: False`) if `meta.get("plan_approved") is False` (passes if `True` or omitted on legacy tasks created prior to Task 012).

### 2. Server State & Zero-Token Inline Convo Preview (`tools/task_viewer/server.py`)
- **State Enrichment (`build_state`, `L93-168`):**
  - Include `plan_readiness = task_cli.check_plan_readiness(tdir, meta)` on every task entry in `tasks_out[tid]`, plus top-level convenience fields `plan_approved`, `plan_drifted`, `plan_approved_at`, `plan_approved_by`, `plan_approved_sha`.
- **New Allowlisted Actions (`run_action`, `L172-302`):**
  - `approve_plan`: calls `task_cli.approve_task_gate(task_id, target_status="ready", approve_plan=True, approved_by="human-ui")`.
  - `reopen_plan`: calls `task_cli.approve_task_gate(task_id, target_status="plan")` (revokes approval cleanly).
  - `preview_conversation`: **0-token local reader** (`_load_convo_preview(conv_id)`):
    - Strictly validates `conv_id` against `^[A-Za-z0-9_-]{4,128}$` (rejects `..`, `/`, `\`).
    - Resolves `brain_dir = Path.home() / ".gemini" / "antigravity" / "brain" / conv_id` (overrideable via `brain_root` arg for unit tests).
    - Lists top-level `.md` artifacts (e.g. `implementation_plan.md`, `walkthrough.md`, `task.md`) with bounded content (max 16 KB each).
    - Reads tail of `.system_generated/logs/transcript.jsonl` (if present) to extract up to 8 recent compact `{role, timestamp, snippet}` turns (max 240 chars/turn).
    - Returns `{"ok": True, "conv_id": conv_id, "exists": bool, "brain_dir": str, "artifacts": [...], "recent_turns": [...]}`.
  - `open_convo_folder`: validates `conv_id` safely and opens `brain_dir` in OS file explorer (`os.startfile` / `open` / `xdg-open`).

### 3. Task Viewer Plugin Redesign (`tools/task_viewer/index.html`)
- **Unified 7-Node Top Sequence (replaces `renderStepper` + `renderMergeBar`, `L207-217`, `L487-517`):**
  - `Scope (brainstorm) ─ Plan (🔒) ─ Approved (ready) ─ Build (in-progress, WT dot) ─ Review (PR #N ↗) ─ Beta (Preview ↗) ─ Main (🌐 / merged_sha)`.
  - Compact flex connector lines + short labels below `520px` so all 7 nodes fit on **1 horizontal line** at `420px–680px`.
- **Formal Plan Approval Gate Callout:**
  - Shown when `status` is `brainstorm`, `plan`, or when `ready` with `plan_drifted === true`.
  - Displays live `plan_readiness.checks` (`✓ PLAN.md written`, `✓ Plan spec filled`, `✓ Owned paths`, `○ Human approval`).
  - Explicit 2-step confirmation (checkbox `"I have reviewed PLAN.md and approve build"` or confirm state) unlocking **`Approve Plan & Unlock Build`** (`action: "approve_plan"`).
- **Zero-Token Linked Convo Chip + Inline Preview Modal:**
  - Remove `sendConvoLinkToChat()` completely (zero `sidecar.agent.sendMessage` chat spam, zero raw `href="conversation://"`).
  - Antigravity convo: **`Preview`** (opens local inline modal showing artifacts + recent turns + Copy ID + Open Folder) + **`Copy ID`** (inline `Copied ✓` button state).
  - Claude convo: **`Resume in Terminal`** (`launch_claude`) + **`Copy ID`**.
  - Move `Copy Worker Brief`, `Copy Resume Prompt`, `Send Brief to Active Chat`, `Launch Fresh Claude` into a single **`Agent Brief ▾`** dropdown menu in the Action Bar.
- **Logical Grouping & Narrow Aux-Pane (`<= 680px`):**
  - Compact 38px header + dismissible top status toast (replaces off-screen `#action-log`).
  - At `<= 680px`: 1-line Task Switcher Bar (`▼ #012 · slug [Plan]`) opening an overlay Wave drawer (reclaims ~200px so `PLAN.md` is visible above the fold).
  - Stage-adaptive Action Bar (2–4 contextual controls max) + `⋯` overflow (`Open Folder`, `Copy Branch`, `Copy Beta URL`, `Reopen Plan`).
  - Single collapsible `Details ▾` strip (`Branch`, `Tier`, `Owned Paths`, `Beta Switch`, `Approved SHA`, `Last Agent`) — zero duplicate PR/Branch/Beta/Status pills.
  - Workflow tab order: `PLAN.md` · `TASK.md` · `Mockups (N)` · `Evidence (N)` · `Handoff` (dimmed when absent).
  - Trimmed sidebar cards: 2 lines + max 2 badges.
- **Fix 4-Second Mockup / DOM Refresh Bug (`refresh()` at `L307-329`, `L707`):**
  - **Root cause:** `setInterval(() => refresh(false), 4000)` unconditionally calls `renderDetail()` which replaces `el.innerHTML` every 4 seconds — destroying and reloading `<iframe class="mockup">`, collapsing menus/details, and resetting scroll even when `/api/state` hasn't changed.
  - **Fix:**
    1. Track `LAST_STATE_JSON` and skip `renderSidebar()` / `renderDetail()` on background polls (`!manual`) when the fetched state JSON is unchanged.
    2. Pause background `renderDetail()` while editing, typing in inputs, viewing the Convo Preview modal, or when a dropdown/approval checkbox is active.
    3. Keep `<iframe class="mockup">` in a persistent host element (or re-attach the existing iframe DOM node when `src` is unchanged) so interactive mockups never reload on state updates unless the user clicks Refresh or switches files.

### 4. Agent & Skill Guardrails (`AGENTS.md`, `.agents/skills/deathfog-task-workflow/SKILL.md`)
- Document the formal plan approval gate (`--approve-plan` / `plan_approved: true` / `plan_approved_sha`) and explicitly forbid agents from self-approving plans without direct human approval.

### 5. Owned Paths & Verification
- **Owned Paths:** `tools/task.py`, `tools/task_viewer/server.py`, `tools/task_viewer/index.html`, `tests/test_task_workflow.py`, `AGENTS.md`, `.agents/skills/deathfog-task-workflow/SKILL.md`.
- **Mockup:** `tasks/012-plan-gate-viewer-redesign/mockups/viewer_redesign_mockup.html` (viewable in Task Viewer → Task 012 → Mockups tab).
- **Verification:**
  1. `.\.venv\Scripts\pytest.exe tests/test_task_workflow.py` (all 9 new + existing tests green).
  2. `.\.venv\Scripts\python.exe tools/task.py verify 012 --skip-preview` (full `pytest` incl. `test_repo_hygiene.py` + `node --test tests_js/*.mjs`).
  3. Headless Playwright check of `tools/task_viewer/index.html` at `460×800` and `1280×800` saving evidence screenshots to `tasks/012-plan-gate-viewer-redesign/evidence/`.
- **Execution Tier:** `hydrogen` (`swarm(action="add", name="worker-012", model="Gemini 4 Hydrogen (Medium)", brief=<worker brief>)`).
