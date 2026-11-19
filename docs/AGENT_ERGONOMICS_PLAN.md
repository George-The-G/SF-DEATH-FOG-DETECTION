# AI Agent Navigation & Token-Efficiency Plan (`AGENT_ERGONOMICS_PLAN.md`)

Purpose: Make `SF-DEATH-FOG-DETECTION` fast, cheap on tokens, and drift-proof for AI coding agents without locking the codebase into a rigid structure.
Status: current (implemented 2026-11-18)
Last verified: 2026-11-18

---

## 1. Core Design Philosophy: Zero Drift, Zero Layout Lock-In

Every manual documentation table or static architecture map eventually drifts when code changes. Worse, stale documentation costs *more* tokens and causes *more* bugs than no documentation at all because an agent trusts the stale doc, writes the wrong code, fails a test, and has to re-read the real source.

**Rule of Thumb:**
> **The code (and its docstrings / C-style `///` comments) is the only thing humans and agents maintain.**
> Any index, symbol map, line number, or `.npz` schema must either be **extracted dynamically on the fly** or **auto-generated from the code during `pytest`**.

---

## 2. Drift-Safety Audit of All Proposed Improvements

Here is a candid review of every suggestion from the previous turn, graded on **Drift Risk** and **Layout Lock-In**, and adjusted so nothing can go stale or box you in:

| Proposal | Original Idea | Drift Risk / Lock-In Problem | Drift-Safe Revision (What We Will Actually Do) |
|---|---|---|---|
| **1. Code Map & Function Index** | Generate a static `docs/CODE_MAP.md` file with line numbers and function names. | **Medium Drift Risk if committed to git:** If someone edits a file without running the generator, `CODE_MAP.md` has wrong line numbers, and committing it creates noisy git diffs across branches. | **On-Demand CLI + Docstring/Comment Parser (`python tools/codemap.py [filter]`):** Parses Python AST docstrings and JS C-style `/** ... */` or `// @sec:` comments in <0.2s and prints a compact outline with live line numbers (or writes `data/codemap_cache.md` locally, gitignored). **0% drift, 0 git merge conflicts.** |
| **2. Large-File Navigation (`app.js`, `fog2.js`, `wetness.py`)** | Put a Table of Contents with line numbers at the top of large files. | **High Drift Risk:** Line numbers inside a file drift on the very next edit above them. | **C/C++-Style Section & Function Comments (`// @sec: <name>` and 1-line `///` / docstring summaries):** No line numbers in the file! Each major section and function gets a 1-line structured comment. `python tools/codemap.py site/v3/app.js` (or `grep "@sec:"`) extracts the exact current line numbers dynamically. |
| **3. Switch & Data-Contract Graph (Knowledge Graph alternative)** | Document feature switches (`fog3d`, `goes5`, `sky`) and `.npz` schemas in a markdown table. | **High Drift Risk if written by hand:** Adding a switch to `microclimate/live/config.py` or an array to `live_static.npz` makes a handwritten table stale immediately. | **Introspect Live Objects Directly in `tools/codemap.py --contracts`:** The script imports `microclimate.live.config` (`PROMOTED`, `BETA`) and inspects `data/live/*.npz` headers (`mmap_mode='r'`) + `site/v3/live*/status.json` keys on the fly. You can rename folders, add switches, or change arrays freely—the output updates automatically. |
| **4. Test Runner & Output Noise (`pyproject.toml` + JS tests)** | Add `pyproject.toml` (`pythonpath = ["."]`, `-q`) and silence 10 KB of debug `console.log` spam in `tests_js/test_mock.mjs`. | **Zero Drift Risk / Zero Lock-In.** | Keep as proposed. Saves ~3,000 tokens per test run and lets `pytest` run from any working directory without `PYTHONPATH` hacks. |
| **5. Local Preview & Verification (`tools/preview.py`)** | One script to serve `site/v3/` locally and take headless desktop/mobile screenshots + check console errors. | **Low Drift Risk** as long as URL parameters (`?beta=1`, `?night=...`) are passed through rather than hardcoded. | Keep as a thin wrapper around Python's `http.server` + Playwright that accepts any URL query string (`python tools/preview.py "?beta=1&sky=1"`) and dumps console/page errors + screenshots to `artifacts/`. |
| **6. Claude & Antigravity Skills (`deathfog-agent-brief`, `deathfog-ship`)** | Update stale paths (`Temp/wt_*`, `-deploy`, `~/deathfog-data`) and version in `.claude/skills/`. | **Low Drift Risk if skills reference commands instead of hardcoded folder trees.** | Keep skills under 40 lines each. Reference entry-point commands (`python tools/codemap.py`, `python -m pytest`, `gh workflow run collect.yml`) and [`AGENTS.md`](../AGENTS.md) rather than duplicating directory lists. |

---

## 3. Detailed Specification of the Drift-Safe System

### 3.1 C/C++-Style Comment Convention (Lightweight & Flexible)
Instead of enforcing rigid file splits or boilerplate headers, we use two simple conventions that any agent or human can read and write naturally:

1. **Section Dividers (for files > 300 lines like `site/v3/app.js`, `site/v3/fog2.js`, `microclimate/wetness.py`, `microclimate/live/forcing.py`):**
   - JavaScript: `// @sec: <short-name> — <one-line description>`
   - Python: `# @sec: <short-name> — <one-line description>`
2. **Concise Function / Shader Comment (1 line above or inside each function):**
   - Python: Standard 1-line docstring `"""One-line summary of inputs -> outputs."""`
   - JavaScript / GLSL: `/** One-line summary of purpose / uniforms. */` or `/// One-line summary` directly above `function foo(...)` or `export function foo(...)`.

*Why this doesn't lock you in:* You can move functions between files, rename files, or split `app.js` later without breaking anything. The comments travel with the functions.

---

### 3.2 Dynamic Code & Contract Inspector (`tools/codemap.py`)
A single zero-dependency Python script (~120 lines) that replaces static architecture docs and external knowledge graphs (like Graphify):

```powershell
# 1. Full repo overview (~1,200 tokens): every file, its 1-line docstring, and @sec markers with live line numbers
python tools/codemap.py

# 2. Drill into one file or folder (~200 tokens): every @sec, class, and function with exact [Lstart-Lend] and 1-line comment
python tools/codemap.py site/v3/app.js
python tools/codemap.py microclimate/live

# 3. Live data contracts & feature switches (~400 tokens):
#    - Reads microclimate.live.config.PROMOTED vs BETA directly
#    - Reads array keys, shapes, and dtypes from data/live/*.npz and data/library/*.npz (via header-only mmap, takes 5 ms)
python tools/codemap.py --contracts
```

**Why this beats a static `CODE_MAP.md` or Graphify:**
- **Always 100% accurate:** Line numbers are computed at the exact millisecond the agent runs the command.
- **Zero git noise:** No auto-generated markdown file dirtying `git status` or causing merge conflicts when two branches edit different lines of `app.js`.
- **Huge token savings:** Reading `python tools/codemap.py site/v3/app.js` costs ~250 tokens (vs. ~30,000 tokens to read `app.js`), and gives the agent the exact `StartLine` and `EndLine` for `read_file`.

---

### 3.3 Fast, Quiet Test & Preview Loop
1. **`pyproject.toml` at repo root:**
   ```toml
   [tool.pytest.ini_options]
   pythonpath = ["."]
   testpaths = ["tests", "tests_mc"]
   addopts = "-q"
   ```
2. **Quiet `tests_js/test_mock.mjs`:**
   - Gate the 150 lines of `console.log("J 659 potrero...")` behind `if (process.env.VERBOSE)` so `node --test tests_js/*.mjs` outputs 12 clean pass/fail lines instead of 10 KB of log spam.
3. **`tools/preview.py` for Live Viewer Previews:**
   - `python tools/preview.py --serve` — Starts a local HTTP server at `http://127.0.0.1:8000/site/v3/` for interactive browser previewing.
   - `python tools/preview.py --check "?beta=1"` — Boots a temporary local server, opens `?beta=1` and public `/` in headless Playwright (desktop + mobile 390x844), asserts **0 console/page errors and 0 failed network requests**, saves before/after screenshots to `artifacts/preview/`, and exits in ~4 seconds.

---

### 3.4 Updated Skills (`.claude/skills/` synced with `~/.claude/skills/synced/`)
We keep **two focused skills** in `.claude/skills/` (and sync them to your global Claude skills folder):
1. **`deathfog-agent-brief/SKILL.md`:**
   - Start every task with `python tools/codemap.py` (or `python tools/codemap.py <file>`) instead of blind full-file reads.
   - Work in the main checkout on a short-lived branch (`feat/...`, `fix/...`, `sim/...`); use `_worktrees/wt_<name>` **only** when running parallel agents, and remove the worktree on merge.
   - Check `data/library/runs/gpu.lock` before any GPU task.
2. **`deathfog-ship/SKILL.md`:**
   - **Local preview / verify:** `python -m pytest`, `node --test tests_js/*.mjs`, and `python tools/preview.py --check "?beta=1"`.
   - **Deploy to Beta:** Push to `origin/v3-dev`, trigger `gh workflow run collect.yml --ref main` from the main repo, and verify `v3/live_beta/status.json`.
   - **Promote to Main (Public):** Explicit user approval required in chat -> update `PROMOTED` in `microclimate/live/config.py` -> push & trigger `collect.yml` -> verify `v3/live/status.json`.

---

## 4. Enforcement: How We Guarantee New Files, Functions & Data Follow the Rules

Guidelines in `AGENTS.md` are necessary, but agents occasionally skip instructions when focused on a bug. To guarantee compliance **automatically without locking you into a rigid folder structure**, we enforce the rules in **`tests/test_repo_hygiene.py`** (which runs on every `pytest` run in <0.2s) plus an optional git `pre-commit` hook:

### 4.1 Code & Comment Hygiene Checks (`tests/test_repo_hygiene.py`)
1. **No Undocumented Public Functions or Giant Anonymous Blocks:**
   - **Python:** Any new top-level `def ` or `class ` (> 10 lines) in `microclimate/`, `deathfog/`, or `tools/` must have either a docstring or a preceding `# ` / `///` summary comment.
   - **JavaScript (`site/v3/*.js`, `edge/*/*.js`):** Any top-level `function ` or `export function ` (> 10 lines) must have a `///` or `/** ... */` comment above it, and any file over 350 lines must have at least one `// @sec:` section marker.
   - *Flexibility:* It does **not** care what folders or file names you use—only that functions in `.py`/`.js` files carry a 1-line comment so `tools/codemap.py` can index them.
2. **No Hardcoded User / Data Paths:**
   - Scans `.py` and `.js` files to forbid re-introducing `Path.home() / "deathfog-data"`, `~/deathfog-data`, or `C:\Users\George\deathfog-data` (enforcing `microclimate.paths.data_dir()`).
3. **No Root Clutter or Tracked Scratch Files:**
   - Fails if untracked/newly committed `.md`, `.log`, `.err`, `.npz`, or `.u8` files are added to the repository root (only `README.md`, `AGENTS.md`, `CLAUDE.md`, `run.py`, and config files are allowed at root; new `.md` docs must go under `docs/` or `spikes/`).
4. **No Large Binaries (> 5 MB) Committed to Git:**
   - Checks `git ls-files` sizes to ensure no `.npz`, `.tar`, `.zip`, or > 5 MB binary is ever staged or committed into git instead of `data/`.

### 4.2 Data Directory Hygiene (`data/MANIFEST.md` vs. `data/`)
- When `data/` exists locally, `test_repo_hygiene.py` checks top-level subfolders in `data/` (e.g., `library`, `live`, `raw`, `grids`, `refined`, `spikes`, `sky_inputs`, `scratch`).
- If an agent creates a brand-new top-level directory inside `data/` (e.g., `data/new_experiment_dump/`) that isn't listed in `data/MANIFEST.md`, `pytest` emits a clear failure:
  `AssertionError: Unlisted data folder 'data/new_experiment_dump' — put experiment outputs in 'data/spikes/<topic>/' or document the new folder in data/MANIFEST.md.`

---

## 5. Implementation Checklist

- [x] **Step 1 (Tests & Token Cleanup):** Add `pyproject.toml` and gate debug logs in `tests_js/test_mock.mjs` behind `process.env.VERBOSE`.
- [x] **Step 2 (`tools/codemap.py` + Section/Function Comments):** Build `tools/codemap.py` (supporting Python AST, JS functions/`///`/`@sec:` comments, and `--contracts` live `.npz`/switch introspection) and add `@sec:` + concise function comments to the top monolith files (`site/v3/app.js`, `site/v3/fog2.js`, `site/v3/ribbons2.js`, `microclimate/wetness.py`, `microclimate/live/forcing.py`, `microclimate/live/build.py`, `microclimate/live/outputs.py`, `microclimate/sun.py`, `microclimate/validate.py`, `deathfog/site.py`).
- [x] **Step 3 (Automated Enforcement — `tests/test_repo_hygiene.py`):** Add the fast (<0.2s) hygiene test suite enforcing function comments, `@sec:` markers on files >350 lines, no root clutter, no >5 MB git files, no hardcoded `~/deathfog-data` paths, and `data/MANIFEST.md` folder coverage.
- [x] **Step 4 (`tools/preview.py`):** Add the single-command local preview server and headless Playwright error/screenshot checker.
- [x] **Step 5 (Skills Sync):** Update `deathfog-agent-brief` and `deathfog-ship` in `.claude/skills/` and sync them to `~/.claude/skills/synced/` and [`AGENTS.md`](../AGENTS.md).
