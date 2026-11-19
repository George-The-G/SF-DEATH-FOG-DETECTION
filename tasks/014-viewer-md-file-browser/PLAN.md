# Plan — Task 014: Rendered Markdown Viewer & Right-Hand Task File Browser

## Brainstorm — Goals, Decisions & Dead Ends
- **Goals:**
  1. **Rendered Markdown Viewer (0 external deps, 0 LLM tokens):** Render `PLAN.md`, `TASK.md`, `HANDOFF.md`, and any `.md` file inside `tasks/<id>-<slug>/` as clean formatted HTML (headings, bold/italic, inline & fenced code, lists, `- [x]` / `- [ ]` checkboxes, tables, blockquotes, links) with a **Rendered / Raw / Edit** toggle.
  2. **Simultaneous Right-Hand Task File Browser:** Display a collapsible right sidebar inside the document workspace listing all files in `tasks/<id>-<slug>/` (`PLAN.md`, `TASK.md`, `HANDOFF.md`, `conversations.json`, `mockups/*`, `evidence/*`) so the user can browse and open any task file while keeping the file tree visible on the right.
  3. **Clearer Start Worktree UX:** Clarify that **Start Worktree** creates the isolated git branch/worktree, and show a 1-click **"Send Worker Brief to Active Chat"** CTA right in the post-start toast so kicking off implementation is effortless.
- **Decisions Locked In:**
  - Replace duplicate top doc tabs (`PLAN.md`, `TASK.md`, `Mockups`, `Evidence`, `Handoff`) with a single clean **open-file toolbar** (`PLAN.md · Rendered | Raw | Edit | Files ▸`) paired with the **Right-Hand Task File Browser** (zero duplicate navigation; `PLAN.md` opens by default).
  - Side-by-side split (`[Document Viewer | 210px Task Files]`) when wide (`> 760px`), collapsible right drawer via `Files` toggle when narrow (`<= 760px`).
  - Use existing `/api/action` (`read_task_file`) for reading text/md/json files in `tasks/<id>-<slug>/` and existing `/api/file?id=...&kind=mockups|evidence&name=...` for HTML mockups and images — works identically in Antigravity sidecar (`antigravity_sidecar.mjs`) and standalone `server.py` without touching the sidecar adapter.

## Plan — Architecture, Owned Paths & Verification

### 1. Safe Task File Tree & Reader (`tools/task_viewer/server.py`)
- **Helper `_list_task_files(task_dir: Path) -> list[dict]`** (in `@sec: helpers`, near `L48-55`):
  - Walks `task_dir` up to depth 2 (`PLAN.md`, `TASK.md`, `HANDOFF.md`, `conversations.json`, `mockups/*`, `evidence/*`), excluding `.gitkeep`.
  - Returns sorted entries `[{"path": "PLAN.md", "group": "root"|"mockups"|"evidence", "name": "PLAN.md", "ext": ".md", "size": int}]` with `PLAN.md`, `TASK.md`, `HANDOFF.md` ordered first in `root`.
- **Helper `_read_task_file_safe(task_dir: Path, rel_path: str, max_bytes: int = 256_000) -> dict`** (in `@sec: helpers`):
  - Rejects empty paths, leading `/`, `..`, backslashes, or any path whose `.resolve()` is not inside `task_dir.resolve()`.
  - Returns `{"ok": True, "path": rel_path, "ext": ext, "size": size, "content": text}` for UTF-8 text files (`.md`, `.json`, `.txt`, `.html`, `.css`, `.js`, `.py`) or `{"ok": True, "path": rel_path, "ext": ext, "size": size, "binary": True}` for images/binaries.
- **State (`build_state`, `L182-278`):**
  - Include `"task_files": _list_task_files(tdir)` on every task entry in `tasks_out[tid]`.
- **Action (`run_action`, `L282-436`):**
  - Add `action == "read_task_file"` accepting `task_id` + `path`, delegating to `_read_task_file_safe(tdir, rel_path)`.

### 2. Rendered Markdown Viewer + Right-Hand File Browser (`tools/task_viewer/index.html`)
- **Zero-Dependency Safe Markdown Renderer (`renderMarkdown(md)`):**
  - Escapes raw HTML first (`esc()`), then cleanly converts:
    - Fenced code blocks (` ```lang ... ``` `)
    - ATX headings (`#`–`####`)
    - Blockquotes (`> ...`)
    - Markdown tables (`| a | b |` + `|---|---|`)
    - Unordered/ordered lists + task checkboxes (`- [x]`, `- [ ]`)
    - Inline `` `code` ``, `**bold**`, `*italic*`, and safe `[label](https://...)` links
  - Styled with clean dark-mode `.md-body` typography (proper spacing, muted borders, scrollable code blocks & tables).
- **Document Workspace Split Layout (replaces `.tabs` bar):**
  - Track `ACTIVE_FILE` (default `'PLAN.md'`), `MD_MODE` (`'rendered' | 'raw'`), `FILES_OPEN` (default `true` on `> 760px`, `false` on `<= 760px`), and `FILE_CACHE` (keyed by `${task_id}:${path}`).
  - Left column: **Open-File Toolbar** showing current filename + contextual controls:
    - For `.md` files: `Rendered` / `Raw` pill toggle, plus `Edit` (for `PLAN.md` / `HANDOFF.md`) and `Snapshot Git` (for `HANDOFF.md`).
    - For `evidence/verify.json`: renders Token Usage Breakdown card (when `t.usage` exists) + formatted JSON.
    - For `.html` under `mockups/`: uses the existing persistent `<iframe class="mockup">` host (zero 4-second flicker!).
    - For `.png/.jpg/.jpeg/.webp/.svg` under `evidence/` or `mockups/`: renders responsive `<img>`.
    - Right side of toolbar: `Files` toggle button showing file count badge.
  - Right column (`.file-browser`, `210px` wide when wide, slim overlay drawer when `<= 760px`):
    - Grouped sections: **Task Docs** (`PLAN.md`, `TASK.md`, `HANDOFF.md`, `conversations.json`), **Mockups**, **Evidence**.
    - Highlights `ACTIVE_FILE`, shows subtle file-type icon + size, clicking opens immediately (using `t.plan_md` / `t.task_md` / `t.handoff_md` from state for instant 0-latency switches, or `read_task_file` for other text/json files).
- **Post-Start Worktree Toast CTA:**
  - When `runAction('start')` succeeds for a new worktree, show a rich toast: `"Worktree ready — send brief to agent to begin implementation"` with an inline **"Send Worker Brief to Active Chat"** (or **"Copy Worker Brief"**) button right inside the toast!

### 3. Tests (`tests/test_task_workflow.py`)
- `test_list_and_read_task_files_safely`: tests `_list_task_files()` ordering/grouping, `_read_task_file_safe()` for `.md`/`.json`/binary, and path-traversal rejection (`../`, `..\\`, absolute paths).
- `test_viewer_state_exposes_task_files_and_read_action`: tests `build_state()` includes `task_files` and `run_action({"action": "read_task_file", ...})` returns file content safely.
- `test_viewer_html_has_markdown_renderer_and_file_browser`: asserts `index.html` contains `renderMarkdown`, `.file-browser`, `read_task_file`, and the post-start worker brief CTA.

### 4. Owned Paths & Verification
- **Owned Paths:** `tools/task_viewer/server.py`, `tools/task_viewer/index.html`, `tests/test_task_workflow.py`.
- **Mockup:** `tasks/014-viewer-md-file-browser/mockups/md_browser_mockup.html`.
- **Execution Tier:** `hydrogen` (`swarm(action="add", name="worker-014", model="Gemini 4 Hydrogen (Medium)", brief=<worker brief>)`).
- **Verification:** `.\.venv\Scripts\pytest.exe tests/test_task_workflow.py tests/test_repo_hygiene.py` and `.\.venv\Scripts\python.exe tools/task.py verify 014 --skip-preview`.
