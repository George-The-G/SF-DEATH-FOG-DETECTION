#!/usr/bin/env python3
"""Self-contained task lifecycle, DAG wave scheduler, and verification gate.

Manages isolated git worktrees (`_worktrees/wt_<id>-<slug>`), self-contained task
packets (`tasks/<id>-<slug>/`), GitHub Issue sync, and conflict-free parallel waves.

Usage:
    python tools/task.py list
    python tools/task.py dag
    python tools/task.py new --title "..." --slug my-feat --owns site/v3/fog2.js
    python tools/task.py start <id>
    python tools/task.py verify <id>
    python tools/task.py finish <id> [--merge] [--deploy-beta]
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from microclimate.paths import data_dir, main_repo_root  # noqa: E402
except ImportError:
    def main_repo_root() -> Path:
        """Fallback main repository root resolver when `microclimate/` is absent (e.g. on `main`)."""
        if ROOT.parent.name == "_worktrees":
            return ROOT.parent.parent
        return ROOT

    def data_dir() -> Path:
        """Fallback data directory resolver when `microclimate/` is absent."""
        return main_repo_root() / "data"

from tools import codemap  # noqa: E402

MAIN_ROOT = main_repo_root()
TASKS_DIR = ROOT / "tasks"
WORKTREES_DIR = MAIN_ROOT / "_worktrees"

SHARED_MAIN_PATHS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".agents/skills/deathfog-agent-brief/SKILL.md",
    ".agents/skills/deathfog-task-workflow/SKILL.md",
    ".agents/skills/deathfog-ship/SKILL.md",
    "tools/codemap.py",
    "tools/task.py",
    "tools/task_viewer",
    "tests/test_task_workflow.py",
)
DEPLOY_TRIGGER_PREFIXES = ("site/v3/", "microclimate/", "live_ci/", "deathfog/", "run.py")

REQUIRED_FIELDS = (
    "id",
    "slug",
    "title",
    "status",
    "branch",
    "switch",
    "depends_on",
    "owned_paths",
    "gpu_required",
    "deliverables",
)
VALID_STATUSES = ("backlog", "brainstorm", "plan", "ready", "in-progress", "review", "beta", "done")
VALID_EXEC_TIERS = ("flash", "hydrogen", "standard", "pro")
PRE_BUILD_GATES = {"backlog", "brainstorm", "plan"}
GATE_NEXT = {"backlog": "brainstorm", "brainstorm": "plan", "plan": "ready"}
DONE_STATUSES = {"beta", "done"}


# @sec: frontmatter — Zero-dependency YAML frontmatter parser and serializer for TASK.md
def _parse_scalar(val: str):
    """Parse a simple YAML scalar (string, int, bool, empty list, or inline list)."""
    s = val.strip()
    if s in ("[]", ""):
        return [] if s == "[]" else ""
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "none", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


def parse_task_md(path: Path) -> tuple[dict, str]:
    """Parse YAML frontmatter and markdown body from a `TASK.md` file."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, flags=re.S)
    if not m:
        raise ValueError(f"{path} is missing YAML frontmatter delimited by ---")
    raw_yaml, body = m.group(1), m.group(2)
    meta: dict = {}
    cur_list_key: str | None = None
    for raw_line in raw_yaml.splitlines():
        line = raw_line.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s*-\s+", line):
            if cur_list_key is None:
                raise ValueError(f"List item without key in {path}: {raw_line}")
            item = re.sub(r"^\s*-\s+", "", line)
            meta[cur_list_key].append(_parse_scalar(item))
            continue
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if rest == "":
            meta[key] = []
            cur_list_key = key
        else:
            meta[key] = _parse_scalar(rest)
            cur_list_key = None
    if "id" in meta and meta["id"] is not None:
        meta["id"] = str(meta["id"]).zfill(3) if str(meta["id"]).isdigit() else str(meta["id"])
    return meta, body


def dump_task_md(meta: dict, body: str) -> str:
    """Serialize frontmatter dict and markdown body back to `TASK.md` format."""
    lines = ["---"]
    ordered_keys = [
        "id",
        "slug",
        "title",
        "issue",
        "epic",
        "pr",
        "pr_url",
        "status",
        "plan_approved",
        "plan_approved_at",
        "plan_approved_by",
        "plan_approved_sha",
        "branch",
        "switch",
        "depends_on",
        "owned_paths",
        "gpu_required",
        "deliverables",
    ]
    keys = [k for k in ordered_keys if k in meta] + [k for k in meta if k not in ordered_keys]
    for k in keys:
        v = meta[k]
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f'  - "{item}"')
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, int):
            lines.append(f"{k}: {v}")
        else:
            escaped = str(v).replace('"', '\\"')
            lines.append(f'{k}: "{escaped}"')
    lines.append("---")
    body_clean = body.lstrip("\r\n")
    return "\n".join(lines) + "\n\n" + body_clean + ("" if body_clean.endswith("\n") else "\n")


def validate_task_meta(meta: dict, path: Path) -> list[str]:
    """Return a list of schema validation errors for a parsed `TASK.md`."""
    errs: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in meta:
            errs.append(f"{path}: missing required frontmatter field '{field}'")
    status = meta.get("status")
    if status and status not in VALID_STATUSES:
        errs.append(f"{path}: invalid status '{status}' (expected one of {VALID_STATUSES})")
    for list_field in ("depends_on", "owned_paths", "deliverables"):
        if list_field in meta and not isinstance(meta[list_field], list):
            errs.append(f"{path}: '{list_field}' must be a list")
    if "gpu_required" in meta and not isinstance(meta["gpu_required"], bool):
        errs.append(f"{path}: 'gpu_required' must be true or false")
    if "plan_approved" in meta and not isinstance(meta["plan_approved"], bool):
        errs.append(f"{path}: 'plan_approved' must be true or false")
    return errs


# @sec: discovery — Discover tasks across main repo and active worktrees
def _run(cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run a subprocess capturing stdout/stderr as UTF-8 text."""
    return subprocess.run(cmd, cwd=cwd or ROOT, text=True, capture_output=True, check=check)


def load_all_tasks(root: Path = ROOT) -> dict[str, tuple[dict, Path]]:
    """Load all `tasks/*/TASK.md` files under `root`, keyed by normalized task ID."""
    tasks: dict[str, tuple[dict, Path]] = {}
    tdir = root / "tasks"
    if not tdir.exists():
        return tasks
    for child in sorted(tdir.iterdir()):
        tfile = child / "TASK.md"
        if child.is_dir() and tfile.is_file():
            meta, _ = parse_task_md(tfile)
            tasks[str(meta["id"])] = (meta, tfile)
    return tasks


def active_worktree_tasks() -> dict[str, tuple[dict, Path]]:
    """Return tasks currently checked out inside `_worktrees/wt_*`."""
    found: dict[str, tuple[dict, Path]] = {}
    if not WORKTREES_DIR.exists():
        return found
    for wt in sorted(WORKTREES_DIR.iterdir()):
        if not wt.is_dir() or not wt.name.startswith("wt_"):
            continue
        suffix = wt.name[3:]
        tfile = wt / "tasks" / suffix / "TASK.md"
        if tfile.is_file():
            try:
                meta, _ = parse_task_md(tfile)
                found[str(meta["id"])] = (meta, wt)
            except Exception:
                pass
    return found


def find_task(task_id: str) -> tuple[dict, Path, Path]:
    """Locate a task by ID (or numeric shorthand) and return `(meta, task_md_path, checkout_root)`."""
    norm = str(task_id).zfill(3) if str(task_id).isdigit() else str(task_id)
    # Prefer active worktree if one exists for this task
    for tid, (meta, wt_root) in active_worktree_tasks().items():
        if tid == norm or str(meta.get("issue", "")) == str(task_id):
            tfile = wt_root / "tasks" / f"{meta['id']}-{meta['slug']}" / "TASK.md"
            return meta, tfile, wt_root
    # Fall back to current checkout
    for tid, (meta, tfile) in load_all_tasks(ROOT).items():
        if tid == norm or str(meta.get("issue", "")) == str(task_id):
            return meta, tfile, ROOT
    # Fall back to main repo root if invoked from another worktree
    if MAIN_ROOT != ROOT:
        for tid, (meta, tfile) in load_all_tasks(MAIN_ROOT).items():
            if tid == norm or str(meta.get("issue", "")) == str(task_id):
                return meta, tfile, MAIN_ROOT
    raise SystemExit(f"Task '{task_id}' not found in tasks/ or _worktrees/.")


def _paths_overlap(paths_a: list[str], paths_b: list[str]) -> bool:
    """Return True if two `owned_paths` lists overlap (exact, prefix, or glob match)."""
    for a in paths_a:
        na = a.rstrip("/")
        for b in paths_b:
            nb = b.rstrip("/")
            if (
                na == nb
                or na.startswith(nb + "/")
                or nb.startswith(na + "/")
                or fnmatch.fnmatch(na, nb)
                or fnmatch.fnmatch(nb, na)
            ):
                return True
    return False


# @sec: dag — Topological wave scheduler with path-ownership and GPU serialization
def _norm_id(val) -> str:
    """Normalize numeric task IDs to 3-digit zero-padded strings."""
    s = str(val)
    return s.zfill(3) if s.isdigit() else s


def compute_waves(tasks: dict[str, tuple[dict, Path]]) -> list[list[dict]]:
    """Group unfinished tasks into conflict-free parallel execution waves."""
    done_ids = {tid for tid, (m, _) in tasks.items() if m.get("status") in DONE_STATUSES}
    remaining = {tid: m for tid, (m, _) in tasks.items() if tid not in done_ids}
    waves: list[list[dict]] = []
    satisfied = set(done_ids)

    while remaining:
        ready = sorted(
            [
                m
                for m in remaining.values()
                if all(_norm_id(dep) in satisfied for dep in m.get("depends_on", []))
            ],
            key=lambda m: (0 if m.get("status") == "in-progress" else 1, str(m["id"])),
        )
        if not ready:
            # Break cycles or external deps by reporting remaining as blocked
            waves.append(sorted(remaining.values(), key=lambda m: str(m["id"])))
            break

        wave: list[dict] = []
        wave_paths: list[str] = []
        wave_has_gpu = False
        for m in ready:
            if m.get("gpu_required") and wave_has_gpu:
                continue
            if _paths_overlap(m.get("owned_paths", []), wave_paths):
                continue
            wave.append(m)
            wave_paths.extend(m.get("owned_paths", []))
            if m.get("gpu_required"):
                wave_has_gpu = True

        if not wave:
            wave = [ready[0]]
        waves.append(wave)
        for m in wave:
            tid = str(m["id"])
            satisfied.add(tid)
            remaining.pop(tid, None)
    return waves


def cmd_list(_args: argparse.Namespace) -> int:
    """Print a compact summary table of all tasks."""
    tasks = load_all_tasks(MAIN_ROOT)
    wt_active = active_worktree_tasks()
    for tid, (m, wt) in wt_active.items():
        tasks[tid] = (m, wt / "tasks" / f"{m['id']}-{m['slug']}" / "TASK.md")
    if not tasks:
        print("No tasks found in tasks/. Create one with: python tools/task.py new --title \"...\" --slug <slug>")
        return 0
    print(f"{'ID':<5} {'STATUS':<12} {'EPIC':<6} {'PR':<6} {'GPU':<4} {'SLUG':<28} {'OWNED PATHS'}")
    print("-" * 98)
    for tid in sorted(tasks):
        m, _ = tasks[tid]
        epic = f"#{m['epic']}" if m.get("epic") else "-"
        pr_str = f"#{m['pr']}" if m.get("pr") else "-"
        gpu = "yes" if m.get("gpu_required") else "no"
        owns = ", ".join(m.get("owned_paths", [])) or "-"
        wt_mark = "*" if tid in wt_active else " "
        print(f"{tid:<4}{wt_mark} {m.get('status','?'):<12} {epic:<6} {pr_str:<6} {gpu:<4} {m.get('slug',''):<28} {owns}")
    if wt_active:
        print("\n(* = active worktree in _worktrees/)")
    return 0


def cmd_dag(_args: argparse.Namespace) -> int:
    """Compute and print conflict-free parallel execution waves."""
    tasks = load_all_tasks(MAIN_ROOT)
    for tid, (m, wt) in active_worktree_tasks().items():
        tasks[tid] = (m, wt / "tasks" / f"{m['id']}-{m['slug']}" / "TASK.md")
    waves = compute_waves(tasks)
    if not waves:
        print("All tasks are in 'beta' or 'done'. Nothing pending.")
        return 0
    for idx, wave in enumerate(waves, 1):
        label = "Ready to run in parallel now" if idx == 1 else f"Blocked by Wave {idx - 1} or shared path/GPU"
        print(f"WAVE {idx} ({label}):")
        for m in wave:
            deps = ", ".join(str(d) for d in m.get("depends_on", [])) or "none"
            owns = ", ".join(m.get("owned_paths", [])) or "none"
            res = "GPU" if m.get("gpu_required") else "CPU"
            print(f"  [{m['id']}] {m['slug']:<26} ({m['status']}, {res}) | owns: {owns} | deps: {deps}")
    return 0


# @sec: scaffold — Create new tasks, epics, and self-contained task packets
def _next_local_id() -> str:
    """Compute the next sequential 3-digit task ID from existing `tasks/` folders."""
    nums = [0]
    for tid in load_all_tasks(MAIN_ROOT):
        if tid.isdigit():
            nums.append(int(tid))
    for tid in active_worktree_tasks():
        if tid.isdigit():
            nums.append(int(tid))
    return str(max(nums) + 1).zfill(3)


def _create_github_issue(title: str, body: str, labels: list[str]) -> int | None:
    """Create a GitHub Issue via `gh` and return its integer number, or None if offline."""
    cmd = ["gh", "issue", "create", "--title", title, "--body", body]
    for lbl in labels:
        cmd.extend(["--label", lbl])
    res = _run(cmd, cwd=MAIN_ROOT)
    if res.returncode != 0:
        return None
    m = re.search(r"/issues/(\d+)", res.stdout)
    return int(m.group(1)) if m else None


def _append_to_epic_issue(epic_num: int, task_issue: int, title: str) -> None:
    """Append a markdown checklist item `- [ ] #<task_issue> <title>` to a GitHub Epic issue."""
    view = _run(["gh", "issue", "view", str(epic_num), "--json", "body"], cwd=MAIN_ROOT)
    if view.returncode != 0:
        return
    try:
        cur_body = json.loads(view.stdout).get("body", "")
    except json.JSONDecodeError:
        return
    entry = f"- [ ] #{task_issue} {title}"
    if entry in cur_body:
        return
    new_body = (cur_body.rstrip() + "\n" + entry + "\n") if cur_body.strip() else (entry + "\n")
    _run(["gh", "issue", "edit", str(epic_num), "--body", new_body], cwd=MAIN_ROOT)


def scaffold_task_dir(base_root: Path, meta: dict, problem: str = "") -> Path:
    """Create `tasks/<id>-<slug>/` with `TASK.md`, `PLAN.md`, `mockups/`, and `evidence/`."""
    tdir = base_root / "tasks" / f"{meta['id']}-{meta['slug']}"
    (tdir / "mockups").mkdir(parents=True, exist_ok=True)
    (tdir / "evidence").mkdir(parents=True, exist_ok=True)
    (tdir / "mockups" / ".gitkeep").touch(exist_ok=True)
    (tdir / "evidence" / ".gitkeep").touch(exist_ok=True)

    task_md = tdir / "TASK.md"
    if not task_md.exists():
        body = (
            f"# Task {meta['id']}: {meta['title']}\n\n"
            f"## Problem & Goal\n{problem or 'Describe the problem and desired outcome.'}\n\n"
            f"## Scope\n- **In scope:** {', '.join(meta.get('owned_paths', [])) or 'TBD'}\n"
            f"- **Out of scope:** Unrelated modules outside `owned_paths`.\n\n"
            f"## Acceptance Criteria\n"
            f"- [ ] Technical plan written in `PLAN.md`\n"
            f"- [ ] Implementation stays strictly within `owned_paths`\n"
            f"- [ ] `python tools/task.py verify {meta['id']}` passes with `ok: true`\n"
        )
        task_md.write_text(dump_task_md(meta, body), encoding="utf-8")

    plan_md = tdir / "PLAN.md"
    if not plan_md.exists():
        plan_md.write_text(
            f"# Plan — Task {meta['id']}: {meta['title']}\n\n"
            f"## Brainstorm — Goals, Decisions & Dead Ends\n"
            f"- **Goals:** {problem or 'TBD'}\n"
            f"- **Decisions:** TBD\n"
            f"- **Rejected / Dead Ends:** None yet\n"
            f"- **Open Questions:** TBD\n\n"
            f"## Plan — Architecture, Owned Paths & Verification\n"
            f"- **Affected Symbols (`python tools/codemap.py`):** TBD\n"
            f"- **Implementation Steps:**\n  1. TBD\n"
            f"- **Switch:** `{meta['switch']}` (preview via `?beta=1&feat={meta['switch']}`)\n"
            f"- **Gate:** `python tools/task.py verify {meta['id']}`\n",
            encoding="utf-8",
        )
    return tdir


def cmd_new(args: argparse.Namespace) -> int:
    """Create a new task packet (and optionally a linked GitHub Issue)."""
    slug = re.sub(r"[^a-z0-9-]+", "-", args.slug.lower()).strip("-")
    owns = [p.strip() for p in (args.owns or "").split(",") if p.strip()]
    deps = [d.strip().zfill(3) if d.strip().isdigit() else d.strip() for d in (args.depends or "").split(",") if d.strip()]
    switch = args.switch or slug.replace("-", "_")

    quick = bool(getattr(args, "quick", False))
    initial_status = getattr(args, "status", "") or ("plan" if quick else "brainstorm")
    if initial_status in ("ready", "in-progress", "review", "beta", "done"):
        print(
            f"Rejected: initial status '{initial_status}' is not allowed. "
            "New tasks must start in 'backlog', 'brainstorm', or 'plan' (via --quick) "
            "and pass the formal plan approval gate before build."
        )
        return 1
    exec_tier = getattr(args, "tier", "") or ("flash" if quick else "hydrogen")
    issue_num: int | None = None
    tid = _next_local_id()
    if not args.local:
        draft_meta = {
            "id": tid,
            "slug": slug,
            "title": args.title,
            "issue": None,
            "epic": args.epic,
            "status": initial_status,
            "branch": f"feat/{tid}-{slug}",
            "switch": switch,
            "depends_on": deps,
            "owned_paths": owns,
            "gpu_required": bool(args.gpu),
            "deliverables": [],
        }
        issue_num = _create_github_issue(
            f"[Task] {args.title}",
            dump_task_md(draft_meta, args.problem or "Scoped via `tools/task.py new`."),
            ["task", f"status:{initial_status}"],
        )
        if issue_num is not None:
            tid = str(issue_num).zfill(3)
            if args.epic:
                _append_to_epic_issue(args.epic, issue_num, args.title)
        else:
            print("Note: GitHub CLI offline or unauthenticated; created local task ID instead.")

    task_rel = f"tasks/{tid}-{slug}"
    deliverables = [f"{task_rel}/PLAN.md", f"{task_rel}/evidence/verify.json"] + owns
    meta = {
        "id": tid,
        "slug": slug,
        "title": args.title,
        "issue": issue_num,
        "epic": args.epic,
        "pr": None,
        "pr_url": "",
        "status": initial_status,
        "plan_approved": False,
        "plan_approved_at": "",
        "plan_approved_by": "",
        "plan_approved_sha": "",
        "exec_tier": exec_tier,
        "branch": f"feat/{tid}-{slug}",
        "switch": switch,
        "depends_on": deps,
        "owned_paths": owns,
        "gpu_required": bool(args.gpu),
        "deliverables": deliverables,
    }
    tdir = scaffold_task_dir(ROOT, meta, problem=args.problem or "")
    print(f"Created task {tid}: {tdir.relative_to(ROOT).as_posix()}")
    if issue_num:
        print(f"GitHub Issue: #{issue_num}")
    return 0


def cmd_epic(args: argparse.Namespace) -> int:
    """Create a GitHub Epic issue to group multiple tasks."""
    body = f"# Epic: {args.title}\n\n{args.summary or ''}\n\n## Tasks\n"
    issue_num = _create_github_issue(f"[Epic] {args.title}", body, ["epic"])
    if issue_num is None:
        print("Could not reach GitHub via `gh issue create`. Re-run with network access to create the Epic issue.")
        return 1
    print(f"Created GitHub Epic #{issue_num}: {args.title}")
    return 0


# @sec: worktree — Start a task in an isolated worktree and emit a compact codemap brief
def cmd_start(args: argparse.Namespace) -> int:
    """Create branch + `_worktrees/wt_<id>-<slug>`, check collisions, and print a token-saving agent brief."""
    meta, tfile, _ = find_task(args.id)
    tid = str(meta["id"])
    slug = meta["slug"]
    branch = meta["branch"]

    # Enforce formal human plan approval gate (brainstorm -> plan -> ready, never bypassed by --force)
    wt_path = WORKTREES_DIR / f"wt_{tid}-{slug}"
    already_active = meta.get("status") == "in-progress" and wt_path.exists()
    readiness = check_plan_readiness(tfile.parent, meta)
    if not already_active:
        if meta.get("status") in PRE_BUILD_GATES:
            nxt = next_gate_status(str(meta.get("status", ""))) or "ready"
            print(
                f"Blocked: task {tid} is at the '{meta.get('status')}' gate. "
                f"Advance to '{nxt}' (and 'ready' via `python tools/task.py approve {tid} --approve-plan` "
                "or the Task Viewer) before starting a worktree. (--force cannot bypass plan approval.)"
            )
            return 1
        if meta.get("plan_approved") is not True:
            print(
                f"Blocked: task {tid} does not have formal plan approval (`plan_approved: true`). "
                f"Run `python tools/task.py approve {tid} --to ready --approve-plan` or approve in Task Viewer. "
                "(--force cannot bypass plan approval.)"
            )
            return 1
        if readiness["plan_drifted"]:
            print(
                f"Blocked: task {tid} PLAN.md drifted since approval "
                f"(approved {readiness['plan_approved_sha']}, current {readiness['plan_sha']}). "
                f"Re-approve via `python tools/task.py approve {tid} --to ready --approve-plan` or Task Viewer. "
                "(--force cannot bypass plan approval.)"
            )
            return 1
    elif readiness["plan_drifted"]:
        print(
            f"Warning: task {tid} PLAN.md drifted since approval "
            f"(approved {readiness['plan_approved_sha']}, current {readiness['plan_sha']})."
        )

    # Check dependencies
    all_tasks = load_all_tasks(MAIN_ROOT)
    unmet = [
        d for d in meta.get("depends_on", [])
        if (all_tasks.get(str(d).zfill(3) if str(d).isdigit() else str(d), ({}, Path()))[0].get("status") not in DONE_STATUSES)
    ]
    if unmet and not args.force:
        print(f"Blocked: task {tid} depends on unfinished task(s): {unmet}. Use --force to override.")
        return 1

    # Check owned_paths collision with other active worktrees
    for other_id, (other_m, other_wt) in active_worktree_tasks().items():
        if other_id == tid:
            continue
        if _paths_overlap(meta.get("owned_paths", []), other_m.get("owned_paths", [])):
            print(f"Path collision with active worktree {other_wt.name} (task {other_id}): {other_m.get('owned_paths')}")
            if not args.force:
                return 1
        if meta.get("gpu_required") and other_m.get("gpu_required"):
            print(f"GPU lock collision with active GPU task {other_id} ({other_wt.name}).")
            if not args.force:
                return 1

    wt_path = WORKTREES_DIR / f"wt_{tid}-{slug}"
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    if not wt_path.exists():
        base_ref = args.base
        branch_exists = _run(["git", "show-ref", "--verify", f"refs/heads/{branch}"], cwd=MAIN_ROOT).returncode == 0
        if branch_exists:
            res = _run(["git", "worktree", "add", str(wt_path), branch], cwd=MAIN_ROOT)
        else:
            res = _run(["git", "worktree", "add", "-b", branch, str(wt_path), base_ref], cwd=MAIN_ROOT)
        if res.returncode != 0:
            print(res.stderr or res.stdout)
            return res.returncode

    # Ensure task packet exists inside the worktree, carrying over pre-build plan/convo/mockups
    meta["status"] = "in-progress"
    wt_tdir = scaffold_task_dir(wt_path, meta)
    src_tdir = tfile.parent
    if src_tdir.resolve() != wt_tdir.resolve():
        for fname in ("PLAN.md", "HANDOFF.md", "conversations.json"):
            src_f = src_tdir / fname
            if src_f.is_file():
                shutil.copy2(src_f, wt_tdir / fname)
        src_mockups = src_tdir / "mockups"
        if src_mockups.is_dir():
            for mf in src_mockups.iterdir():
                if mf.is_file() and mf.name != ".gitkeep":
                    shutil.copy2(mf, wt_tdir / "mockups" / mf.name)
    wt_task_md = wt_tdir / "TASK.md"
    _, body = parse_task_md(tfile) if tfile.exists() else (meta, "")
    wt_task_md.write_text(dump_task_md(meta, body), encoding="utf-8")

    # Update GitHub issue label if linked and online
    if meta.get("issue"):
        _run(["gh", "issue", "edit", str(meta["issue"]), "--add-label", "status:in-progress", "--remove-label", "status:ready"], cwd=MAIN_ROOT)

    # Emit compact agent brief with live codemap lines for owned_paths
    print(f"=== TASK BRIEF: {tid}-{slug} ===")
    print(f"Worktree : {wt_path}")
    print(f"Branch   : {branch}")
    print(f"Switch   : {meta['switch']} (viewer: ?beta=1&feat={meta['switch']})")
    print(f"Owns     : {', '.join(meta.get('owned_paths', [])) or '(none)'}")
    print(f"Packet   : tasks/{tid}-{slug}/ (edit PLAN.md, mockups/, evidence/)")
    print(f"Verify   : .\\.venv\\Scripts\\python.exe tools/task.py verify {tid}")
    print("\n--- Owned File Outlines (tools/codemap.py) ---")
    for rel in meta.get("owned_paths", []):
        p = wt_path / rel
        if p.is_file() and p.suffix in (".py", ".js", ".mjs"):
            info = codemap.parse_python(p) if p.suffix == ".py" else codemap.parse_js(p)
            print(f"{rel} ({info['lines']}L): {info['doc']}")
            for ln, sec in info["sections"]:
                print(f"  @sec L{ln}: {sec}")
    return 0


# @sec: verify — Hard verification gate (deliverables, scope, pytest, node, Playwright)
def _check_scope(wt_root: Path, tid: str, slug: str, owned_paths: list[str], base_ref: str) -> list[str]:
    """Return any modified or untracked files outside `owned_paths` and `tasks/<id>-<slug>/`."""
    allowed_prefix = f"tasks/{tid}-{slug}/"
    changed: set[str] = set()
    diff = _run(["git", "diff", "--name-only", f"{base_ref}...HEAD"], cwd=wt_root)
    if diff.returncode == 0:
        changed.update(line.strip() for line in diff.stdout.splitlines() if line.strip())
    status = _run(["git", "status", "--porcelain"], cwd=wt_root)
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            if len(line) > 3:
                path_part = line[3:].split(" -> ")[-1].strip().strip('"')
                changed.add(path_part)
    out_of_scope = []
    for rel in sorted(changed):
        if rel.startswith(allowed_prefix):
            continue
        if not any(
            rel == o.rstrip("/") or rel.startswith(o.rstrip("/") + "/") or fnmatch.fnmatch(rel, o)
            for o in owned_paths
        ):
            out_of_scope.append(rel)
    return out_of_scope


def cmd_verify(args: argparse.Namespace) -> int:
    """Run all hard verification gates for a task and write `evidence/verify.json`."""
    meta, tfile, wt_root = find_task(args.id)
    tid, slug = str(meta["id"]), meta["slug"]
    ev_dir = wt_root / "tasks" / f"{tid}-{slug}" / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)

    checks: dict[str, dict] = {}
    ok = True

    # 1. Schema check
    schema_errs = validate_task_meta(meta, tfile)
    checks["schema"] = {"ok": len(schema_errs) == 0, "errors": schema_errs}
    ok = ok and checks["schema"]["ok"]

    # 1b. Formal plan approval check (backward-compatible for legacy tasks without plan_approved)
    if "plan_approved" not in meta:
        checks["plan_approval"] = {"ok": True, "legacy": True}
    elif meta.get("plan_approved") is True:
        readiness = check_plan_readiness(tfile.parent, meta)
        checks["plan_approval"] = {
            "ok": True,
            "approved_by": str(meta.get("plan_approved_by") or ""),
            "approved_sha": str(meta.get("plan_approved_sha") or ""),
            "drifted": readiness["plan_drifted"],
        }
    else:
        checks["plan_approval"] = {
            "ok": False,
            "error": "Plan has not been formally approved (plan_approved is false).",
        }
    ok = ok and checks["plan_approval"]["ok"]

    # 2. Deliverables check (verify.json is written at the end)
    missing_deliv = []
    for rel in meta.get("deliverables", []):
        if rel.endswith("evidence/verify.json"):
            continue
        p = wt_root / rel
        if not p.exists() or (p.is_file() and p.stat().st_size == 0):
            missing_deliv.append(rel)
    checks["deliverables"] = {"ok": len(missing_deliv) == 0, "missing": missing_deliv}
    ok = ok and checks["deliverables"]["ok"]

    # 3. Scope check
    if not args.allow_extra and meta.get("owned_paths"):
        extra = _check_scope(wt_root, tid, slug, meta["owned_paths"], args.base)
        checks["scope"] = {"ok": len(extra) == 0, "out_of_scope": extra}
        ok = ok and checks["scope"]["ok"]
    else:
        checks["scope"] = {"ok": True, "skipped": True}

    # 4. Python + repo hygiene tests
    py_res = _run([sys.executable, "-m", "pytest"], cwd=wt_root)
    checks["pytest"] = {
        "ok": py_res.returncode == 0,
        "returncode": py_res.returncode,
        "tail": (py_res.stdout + "\n" + py_res.stderr).strip().splitlines()[-8:],
    }
    ok = ok and checks["pytest"]["ok"]

    # 5. JS tests
    js_files = sorted(str(p.relative_to(wt_root).as_posix()) for p in (wt_root / "tests_js").glob("*.mjs"))
    if js_files:
        js_res = _run(["node", "--test", *js_files], cwd=wt_root)
        checks["node_test"] = {
            "ok": js_res.returncode == 0,
            "returncode": js_res.returncode,
            "tail": (js_res.stdout + "\n" + js_res.stderr).strip().splitlines()[-8:],
        }
        ok = ok and checks["node_test"]["ok"]

    # 6. Headless Playwright preview check when site/v3/ is owned
    touches_v3 = any(p.startswith("site/v3/") for p in meta.get("owned_paths", []))
    if touches_v3 and not args.skip_preview:
        query = f"?beta=1&feat={meta['switch']}&night=2026-10-01"
        p_desk = _run([sys.executable, "tools/preview.py", "--check", query, "--out", str(ev_dir)], cwd=wt_root)
        p_phone = _run([sys.executable, "tools/preview.py", "--check", query, "--phone", "--out", str(ev_dir)], cwd=wt_root)
        checks["preview"] = {
            "ok": p_desk.returncode == 0 and p_phone.returncode == 0,
            "query": query,
            "desktop_rc": p_desk.returncode,
            "phone_rc": p_phone.returncode,
        }
        ok = ok and checks["preview"]["ok"]

    sha = _run(["git", "rev-parse", "HEAD"], cwd=wt_root).stdout.strip()
    report = {
        "task_id": tid,
        "slug": slug,
        "git_sha": sha,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ok": ok,
        "checks": checks,
    }
    out_file = ev_dir / "verify.json"
    out_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


# @sec: pr — Open a GitHub Pull Request for a task and record pr / pr_url back into TASK.md
def cmd_pr(args: argparse.Namespace) -> int:
    """Push task branch, create/find GitHub PR against `--base`, and record `pr` & `pr_url` in `TASK.md`."""
    meta, tfile, wt_root = find_task(args.id)
    tid, slug, branch = str(meta["id"]), meta["slug"], meta["branch"]
    vfile = wt_root / "tasks" / f"{tid}-{slug}" / "evidence" / "verify.json"

    if not args.draft:
        if not vfile.is_file():
            print(f"Cannot open PR for task {tid}: missing {vfile}. Run `python tools/task.py verify {tid}` first (or pass --draft).")
            return 1
        report = json.loads(vfile.read_text(encoding="utf-8"))
        if not report.get("ok"):
            print(f"Cannot open PR for task {tid}: verify.json has ok=false.")
            return 1

    push = _run(["git", "push", "-u", "origin", branch], cwd=wt_root)
    if push.returncode != 0:
        print(push.stderr or push.stdout)
        return push.returncode

    # Check if a PR already exists for this branch
    view = _run(["gh", "pr", "view", branch, "--json", "number,url"], cwd=wt_root)
    pr_num: int | None = None
    pr_url: str = ""
    if view.returncode == 0 and view.stdout.strip():
        data = json.loads(view.stdout)
        pr_num = data.get("number")
        pr_url = data.get("url", "")
    else:
        issue_line = f"Closes #{meta['issue']}\n" if meta.get("issue") else ""
        epic_line = f"Part of Epic #{meta['epic']}\n" if meta.get("epic") else ""
        body = (
            f"## Task {tid}: {meta['title']}\n\n"
            f"{issue_line}{epic_line}\n"
            f"- **Task packet:** `tasks/{tid}-{slug}/TASK.md` & `tasks/{tid}-{slug}/PLAN.md`\n"
            f"- **Verification evidence:** `tasks/{tid}-{slug}/evidence/verify.json`\n"
            f"- **Beta preview switch:** `?beta=1&feat={meta['switch']}`\n"
            f"- **Owned paths:** {', '.join(f'`{p}`' for p in meta.get('owned_paths', [])) or '(none)'}\n"
        )
        cmd = [
            "gh", "pr", "create",
            "--base", args.base,
            "--head", branch,
            "--title", f"task({tid}): {meta['title']}",
            "--body", body,
        ]
        if args.draft:
            cmd.append("--draft")
        created = _run(cmd, cwd=wt_root)
        if created.returncode != 0:
            print(created.stderr or created.stdout)
            return created.returncode
        pr_url = created.stdout.strip().splitlines()[-1].strip()
        m = re.search(r"/pull/(\d+)", pr_url)
        pr_num = int(m.group(1)) if m else None

    # Record PR correlation back into TASK.md and push
    meta["pr"] = pr_num
    meta["pr_url"] = pr_url
    meta["status"] = "review"
    _, body_md = parse_task_md(tfile)
    tfile.write_text(dump_task_md(meta, body_md), encoding="utf-8")

    rel_task = str(tfile.relative_to(wt_root).as_posix())
    _run(["git", "add", rel_task], cwd=wt_root)
    commit = _run(["git", "commit", "-m", f"task({tid}): link PR #{pr_num} in TASK.md"], cwd=wt_root)
    if commit.returncode == 0:
        _run(["git", "push", "origin", branch], cwd=wt_root)

    if meta.get("issue") and pr_num:
        _run(["gh", "issue", "comment", str(meta["issue"]), "--body", f"Opened PR #{pr_num}: {pr_url}"], cwd=MAIN_ROOT)
        _run(["gh", "issue", "edit", str(meta["issue"]), "--add-label", "status:review", "--remove-label", "status:in-progress"], cwd=MAIN_ROOT)

    print(f"PR #{pr_num}: {pr_url} (recorded in {rel_task})")
    return 0


# @sec: finish — Merge verified branch into v3-dev, remove worktree, and sync GitHub
def cmd_finish(args: argparse.Namespace) -> int:
    """Validate passing `verify.json`, optionally merge into `v3-dev`, clean worktree, and sync GitHub."""
    meta, tfile, wt_root = find_task(args.id)
    tid, slug, branch = str(meta["id"]), meta["slug"], meta["branch"]
    vfile = wt_root / "tasks" / f"{tid}-{slug}" / "evidence" / "verify.json"
    if not vfile.is_file():
        print(f"Cannot finish task {tid}: missing {vfile}. Run `python tools/task.py verify {tid}` first.")
        return 1
    report = json.loads(vfile.read_text(encoding="utf-8"))
    head_sha = _run(["git", "rev-parse", "HEAD"], cwd=wt_root).stdout.strip()
    if not report.get("ok"):
        print(f"Cannot finish task {tid}: verify.json has ok=false.")
        return 1
    if report.get("git_sha") != head_sha and not args.allow_stale:
        print(f"Cannot finish task {tid}: verify.json SHA ({report.get('git_sha')}) != HEAD ({head_sha}). Re-run verify after committing.")
        return 1

    # Update TASK.md status (and record merge metadata when merging to v3-dev)
    new_status = "beta" if args.merge else "review"
    meta["status"] = new_status
    if args.merge:
        meta["merged_sha"] = head_sha[:12]
        merged_into = list(meta.get("merged_into") or [])
        if "v3-dev" not in merged_into:
            merged_into.append("v3-dev")
        meta["merged_into"] = merged_into
    _, body = parse_task_md(tfile)
    tfile.write_text(dump_task_md(meta, body), encoding="utf-8")

    if args.merge:
        if wt_root != MAIN_ROOT:
            dirty = _run(["git", "status", "--porcelain"], cwd=wt_root).stdout.strip()
            if dirty:
                rel_packet = f"tasks/{tid}-{slug}"
                _run(["git", "add", rel_packet], cwd=wt_root, check=True)
                _run(["git", "commit", "-m", f"task({tid}): mark {slug} status={new_status} and stage evidence"], cwd=wt_root)
        # Clean any stale untracked duplicate task packet in MAIN_ROOT before merge
        main_tdir = MAIN_ROOT / "tasks" / f"{tid}-{slug}"
        if wt_root != MAIN_ROOT and main_tdir.is_dir():
            tracked = _run(["git", "ls-files", f"tasks/{tid}-{slug}"], cwd=MAIN_ROOT).stdout.strip()
            if not tracked:
                shutil.rmtree(main_tdir)
        res = _run(["git", "merge", "--no-ff", branch, "-m", f"Merge {branch} (task {tid})"], cwd=MAIN_ROOT)
        if res.returncode != 0:
            print(res.stderr or res.stdout)
            return res.returncode
        if wt_root != MAIN_ROOT and wt_root.exists():
            rm_res = _run(["git", "worktree", "remove", str(wt_root)], cwd=MAIN_ROOT)
            if rm_res.returncode != 0:
                print(f"Warning: merged {branch} into v3-dev, but could not remove {wt_root.name}: {(rm_res.stderr or rm_res.stdout).strip()}")
            else:
                _run(["git", "branch", "-d", branch], cwd=MAIN_ROOT)
                print(f"Merged {branch} into v3-dev and removed worktree {wt_root.name}.")

    if meta.get("issue"):
        comment = (
            f"Task `{tid}-{slug}` verified (`{head_sha[:8]}`) -> status `{new_status}`.\n"
            f"Beta preview flag: `?beta=1&feat={meta['switch']}`"
        )
        _run(["gh", "issue", "comment", str(meta["issue"]), "--body", comment], cwd=MAIN_ROOT)
        _run(["gh", "issue", "edit", str(meta["issue"]), "--add-label", f"status:{new_status}", "--remove-label", "status:in-progress"], cwd=MAIN_ROOT)

    if args.deploy_beta:
        _run(["git", "push", "origin", "v3-dev"], cwd=MAIN_ROOT, check=True)
        owned = meta.get("owned_paths", [])
        needs_site_deploy = bool(getattr(args, "force_deploy", False)) or any(
            str(p).startswith(DEPLOY_TRIGGER_PREFIXES) for p in owned
        )
        if needs_site_deploy:
            wf = _run(["gh", "workflow", "run", "collect.yml", "--ref", "main"], cwd=MAIN_ROOT)
            if wf.returncode != 0:
                print("Warning: failed to trigger collect.yml:", wf.stderr.strip())
            else:
                print("Triggered GitHub Actions Beta deploy (`collect.yml` on `main`).")
        else:
            print("Pushed v3-dev; skipped collect.yml (tooling/docs-only task — pass --force-deploy to override).")

    if getattr(args, "sync_main", False):
        sync_res = sync_shared_to_main(push=True)
        print(sync_res.get("message") or sync_res.get("error"))
        if not sync_res.get("ok"):
            return 1
    return 0


def shared_main_drift(main_root: Path = MAIN_ROOT) -> dict:
    """Return shared agent/tooling file drift between `origin/main` and `v3-dev`."""
    main_ref = ""
    for cand in ("origin/main", "main"):
        if _run(["git", "rev-parse", "--verify", cand], cwd=main_root).returncode == 0:
            main_ref = cand
            break
    if not main_ref:
        return {"ok": False, "in_sync": True, "drifted": [], "main_ref": "", "error": "No main ref found"}
    res = _run(["git", "diff", "--name-only", main_ref, "v3-dev", "--", *SHARED_MAIN_PATHS], cwd=main_root)
    if res.returncode != 0:
        return {"ok": False, "in_sync": False, "drifted": [], "main_ref": main_ref, "error": res.stderr.strip()}
    drifted = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    return {"ok": True, "in_sync": len(drifted) == 0, "drifted": drifted, "main_ref": main_ref}


def sync_shared_to_main(*, push: bool = False, check_only: bool = False, main_root: Path = MAIN_ROOT) -> dict:
    """Sync `SHARED_MAIN_PATHS` from `v3-dev` onto `origin/main`, run isolated tests, and optionally push."""
    if not check_only:
        fetch_res = _run(["git", "fetch", "origin", "main"], cwd=main_root)
        if fetch_res.returncode != 0:
            return {"ok": False, "error": f"Failed to fetch origin/main: {(fetch_res.stderr or fetch_res.stdout).strip()}"}

    drift = shared_main_drift(main_root)
    if not drift.get("ok"):
        return drift
    if drift["in_sync"]:
        return {"ok": True, "in_sync": True, "drifted": [], "message": "Shared agent/tooling files are already in sync between v3-dev and main."}
    if check_only:
        return {
            "ok": True,
            "in_sync": False,
            "drifted": drift["drifted"],
            "message": f"{len(drift['drifted'])} shared file(s) differ between v3-dev and {drift['main_ref']}: {', '.join(drift['drifted'])}",
        }

    wt_sync = main_root / "_worktrees" / "wt_sync_main"
    wt_sync.parent.mkdir(parents=True, exist_ok=True)
    if wt_sync.exists():
        _run(["git", "worktree", "remove", "--force", str(wt_sync)], cwd=main_root)

    add_res = _run(["git", "worktree", "add", "--detach", str(wt_sync), "origin/main"], cwd=main_root)
    if add_res.returncode != 0:
        return {"ok": False, "error": f"Failed to create sync worktree: {(add_res.stderr or add_res.stdout).strip()}"}

    try:
        co_res = _run(["git", "checkout", "v3-dev", "--", *SHARED_MAIN_PATHS], cwd=wt_sync)
        if co_res.returncode != 0:
            return {"ok": False, "error": f"Failed to checkout shared files: {(co_res.stderr or co_res.stdout).strip()}"}

        # Run truly isolated checks inside wt_sync so v3-dev's microclimate/ cannot leak onto sys.path
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        py = sys.executable
        for label, cmd in (
            ("pytest", [py, "-m", "pytest", "-q", "tests/test_task_workflow.py"]),
            ("task_list", [py, "tools/task.py", "list"]),
            ("viewer_state", [py, "tools/task_viewer/server.py", "--state-json"]),
        ):
            chk = subprocess.run(cmd, cwd=wt_sync, env=env, text=True, capture_output=True)
            if chk.returncode != 0:
                return {
                    "ok": False,
                    "error": f"Isolated main check '{label}' failed (rc={chk.returncode}): {(chk.stderr or chk.stdout).strip()[-600:]}",
                }

        status = _run(["git", "status", "--porcelain"], cwd=wt_sync).stdout.strip()
        if not status:
            return {"ok": True, "in_sync": True, "drifted": [], "message": "Nothing to commit; main already matches v3-dev."}

        cm_res = _run(["git", "commit", "-m", "Sync shared agent & task-viewer tooling from v3-dev"], cwd=wt_sync)
        if cm_res.returncode != 0:
            return {"ok": False, "error": f"Commit failed: {(cm_res.stderr or cm_res.stdout).strip()}"}

        sha = _run(["git", "rev-parse", "--short", "HEAD"], cwd=wt_sync).stdout.strip()
        if push:
            ps_res = _run(["git", "push", "origin", "HEAD:main"], cwd=wt_sync)
            if ps_res.returncode != 0:
                return {"ok": False, "error": f"Push to origin/main failed: {(ps_res.stderr or ps_res.stdout).strip()}"}
            return {
                "ok": True,
                "in_sync": True,
                "drifted": [],
                "commit": sha,
                "message": f"Synced {len(drift['drifted'])} shared file(s) to origin/main ({sha}) with isolated tests passing.",
            }
        return {
            "ok": True,
            "in_sync": False,
            "drifted": drift["drifted"],
            "commit": sha,
            "message": f"Verified isolated commit {sha} on main (dry run; pass --push to push origin/main).",
        }
    finally:
        _run(["git", "worktree", "remove", "--force", str(wt_sync)], cwd=main_root)


def cmd_sync_main(args: argparse.Namespace) -> int:
    """Check or sync shared agent/tooling files from `v3-dev` onto `main` with isolated tests."""
    res = sync_shared_to_main(push=bool(args.push), check_only=bool(args.check))
    print(res.get("message") or res.get("error"))
    if not res.get("ok"):
        return 1
    if args.check and not res.get("in_sync"):
        return 2
    return 0


def cmd_sync(_args: argparse.Namespace) -> int:
    """Cache open GitHub task/epic issues into `data/tasks_index.json` for offline DAG inspection."""
    res = _run(
        ["gh", "issue", "list", "--state", "all", "--limit", "100", "--json", "number,title,state,labels,updatedAt"],
        cwd=MAIN_ROOT,
    )
    if res.returncode != 0:
        print("GitHub sync failed (offline or unauthenticated):", res.stderr.strip())
        return 1
    cache_path = data_dir() / "tasks_index.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(res.stdout, encoding="utf-8")
    items = json.loads(res.stdout)
    print(f"Synced {len(items)} GitHub issue(s) to {cache_path}")
    return 0


# @sec: handoff — Stage gate approval, HANDOFF.md checkpoints, conversation links, and resume prompts
def compute_plan_sha(task_dir: Path) -> str:
    """Compute a 12-char SHA-256 hash of LF-normalized `PLAN.md`, or empty string if missing."""
    plan_path = task_dir / "PLAN.md"
    if not plan_path.is_file():
        return ""
    text = plan_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _is_plan_template_line(line: str) -> bool:
    """Return True if a `PLAN.md` line is an untouched scaffold placeholder."""
    s = line.strip()
    if not s or s.startswith("<!--"):
        return True
    if s in ("TBD", "TODO", "*(To be finalized...)*"):
        return True
    if re.match(r"^-\s+\*\*(Switch|Gate):\*\*", s, re.I):
        return True
    if re.match(r"^-\s+\*\*(Implementation Steps|Architecture|Owned Paths):\*\*\s*(TBD|TODO|\*\(To be finalized.*\)\*)?$", s, re.I):
        return True
    if re.match(r"^-\s+\*\*Affected Symbols.*:\*\*\s*(TBD|TODO)?$", s, re.I):
        return True
    if re.match(r"^(?:\d+\.|-)\s*(TBD|TODO)$", s, re.I):
        return True
    return False


def check_plan_readiness(task_dir: Path, meta: dict) -> dict:
    """Validate `PLAN.md` readiness, owned_paths, and post-approval drift against `plan_approved_sha`."""
    plan_path = task_dir / "PLAN.md"
    text = plan_path.read_text(encoding="utf-8", errors="replace") if plan_path.is_file() else ""
    plan_exists = plan_path.is_file() and len(text.strip()) >= 120

    sec_match = re.search(r"^##\s+Plan\b[^\n]*\n(.*)$", text, flags=re.M | re.S)
    plan_has_spec = False
    if sec_match:
        sec_body = re.split(r"^##\s+", sec_match.group(1), maxsplit=1, flags=re.M)[0]
        meaningful = [ln.strip() for ln in sec_body.splitlines() if not _is_plan_template_line(ln)]
        plan_has_spec = len("\n".join(meaningful)) >= 20

    owns = meta.get("owned_paths") or []
    owned_paths_set = bool(owns)
    plan_sha = compute_plan_sha(task_dir)
    plan_approved = bool(meta.get("plan_approved", False))
    approved_sha = str(meta.get("plan_approved_sha") or "")
    plan_drifted = bool(plan_approved and approved_sha and plan_sha != approved_sha)

    checks = [
        {"id": "plan_exists", "label": "PLAN.md written", "ok": plan_exists},
        {"id": "plan_has_spec", "label": "Plan spec filled", "ok": plan_has_spec},
        {"id": "owned_paths_set", "label": f"Owned paths ({len(owns)})", "ok": owned_paths_set},
        {"id": "human_approved", "label": "Human approval", "ok": plan_approved and not plan_drifted},
    ]
    ready = plan_exists and plan_has_spec and owned_paths_set
    return {
        "ready": ready,
        "checks": checks,
        "plan_sha": plan_sha,
        "plan_approved": plan_approved,
        "plan_drifted": plan_drifted,
        "plan_approved_at": str(meta.get("plan_approved_at") or ""),
        "plan_approved_by": str(meta.get("plan_approved_by") or ""),
        "plan_approved_sha": approved_sha,
    }


def next_gate_status(status: str) -> str | None:
    """Return the next pre-build gate status (`backlog -> brainstorm -> plan -> ready`), or None."""
    return GATE_NEXT.get(str(status))


def approve_task_gate(
    task_id: str,
    target_status: str | None = None,
    *,
    approve_plan: bool = False,
    approved_by: str = "human-cli",
) -> dict:
    """Advance a task's stage gate in `TASK.md` and return a result summary dict."""
    meta, tfile, _ = find_task(task_id)
    prev = str(meta.get("status", ""))
    nxt = target_status or next_gate_status(prev)
    if not nxt:
        return {"ok": False, "error": f"Task {meta['id']} is already at '{prev}'; specify --to <status> explicitly."}
    if nxt not in VALID_STATUSES:
        return {"ok": False, "error": f"Invalid target status '{nxt}'. Valid: {', '.join(VALID_STATUSES)}"}

    _, body = parse_task_md(tfile)
    if nxt == "ready":
        if not approve_plan:
            return {
                "ok": False,
                "error": "Formal plan approval required: pass --approve-plan (or click 'Approve Plan & Unlock Build' in Task Viewer).",
            }
        readiness = check_plan_readiness(tfile.parent, meta)
        if not readiness["ready"]:
            missing = [c["label"] for c in readiness["checks"] if c["id"] != "human_approved" and not c["ok"]]
            return {
                "ok": False,
                "error": f"PLAN.md is not ready for approval: {', '.join(missing)}",
                "plan_readiness": readiness,
            }
        meta["plan_approved"] = True
        meta["plan_approved_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        meta["plan_approved_by"] = approved_by or "human-cli"
        meta["plan_approved_sha"] = readiness["plan_sha"]
    elif nxt in ("backlog", "brainstorm", "plan"):
        meta["plan_approved"] = False
        meta["plan_approved_at"] = ""
        meta["plan_approved_by"] = ""
        meta["plan_approved_sha"] = ""

    meta["status"] = nxt
    tfile.write_text(dump_task_md(meta, body), encoding="utf-8")
    return {
        "ok": True,
        "id": str(meta["id"]),
        "previous_status": prev,
        "status": nxt,
        "plan_approved": bool(meta.get("plan_approved", False)),
        "plan_approved_sha": str(meta.get("plan_approved_sha") or ""),
        "message": f"Task {meta['id']} advanced: {prev} -> {nxt}",
    }


def load_conversations(task_dir: Path) -> dict:
    """Load `conversations.json` from `task_dir` and return primary + collapsible history."""
    cpath = task_dir / "conversations.json"
    data = {"primary_id": "", "items": []}
    if cpath.is_file():
        try:
            raw = json.loads(cpath.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data["primary_id"] = str(raw.get("primary_id") or "")
                items = raw.get("items")
                if isinstance(items, list):
                    data["items"] = [it for it in items if isinstance(it, dict) and it.get("id")]
        except Exception:
            pass
    items = data["items"]
    primary = next((it for it in items if it.get("id") == data["primary_id"]), None)
    if primary is None and items:
        primary = items[0]
        data["primary_id"] = str(primary.get("id", ""))
    history = [it for it in items if primary is None or it.get("id") != primary.get("id")]
    return {"primary_id": data["primary_id"], "primary": primary, "history": history, "items": items}


def _save_conversations(task_dir: Path, primary_id: str, items: list[dict]) -> dict:
    """Persist `conversations.json` inside `task_dir` and return normalized conversation state."""
    task_dir.mkdir(parents=True, exist_ok=True)
    payload = {"primary_id": primary_id, "items": items}
    (task_dir / "conversations.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return load_conversations(task_dir)


def link_conversation(
    task_dir: Path,
    conv_id: str,
    tool: str = "antigravity",
    label: str = "",
    stage: str = "",
    make_primary: bool = True,
) -> dict:
    """Link an Antigravity or Claude conversation ID to a task (primary or history)."""
    cid = conv_id.strip()
    if not cid:
        raise ValueError("Conversation ID must not be empty.")
    tool_norm = tool.strip().lower() or "antigravity"
    if tool_norm not in ("antigravity", "claude", "gemini", "other"):
        raise ValueError(f"Unsupported tool '{tool_norm}'. Use antigravity, claude, gemini, or other.")
    cur = load_conversations(task_dir)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = [it for it in cur["items"] if it.get("id") != cid]
    entry = {
        "id": cid,
        "tool": tool_norm,
        "label": label.strip() or f"{tool_norm.capitalize()} session",
        "stage": stage.strip(),
        "updated_at": now,
    }
    items.insert(0, entry)
    primary_id = cid if (make_primary or not cur["primary_id"]) else cur["primary_id"]
    return _save_conversations(task_dir, primary_id, items)


def set_primary_conversation(task_dir: Path, conv_id: str) -> dict:
    """Promote an already-linked conversation ID in `conversations.json` to primary."""
    cid = conv_id.strip()
    cur = load_conversations(task_dir)
    if not any(it.get("id") == cid for it in cur["items"]):
        raise ValueError(f"Conversation '{cid}' is not linked to this task.")
    return _save_conversations(task_dir, cid, cur["items"])


def git_summary(checkout_root: Path) -> dict:
    """Return lightweight git HEAD, dirty count, status, diff stat, and recent commits."""
    if not checkout_root.exists():
        return {"head_sha": "", "head_subject": "", "dirty_count": 0, "status_short": "", "diff_stat": "", "recent_commits": ""}
    head_res = _run(["git", "log", "-1", "--format=%h%x1f%s"], cwd=checkout_root)
    head_sha, head_subject = "", ""
    if head_res.returncode == 0 and "\x1f" in head_res.stdout:
        head_sha, head_subject = head_res.stdout.strip().split("\x1f", 1)
    st_res = _run(["git", "status", "--short"], cwd=checkout_root)
    st_lines = [ln for ln in st_res.stdout.splitlines() if ln.strip()] if st_res.returncode == 0 else []
    diff_res = _run(["git", "diff", "--stat"], cwd=checkout_root)
    log_res = _run(["git", "log", "-n", "5", "--oneline"], cwd=checkout_root)
    return {
        "head_sha": head_sha,
        "head_subject": head_subject,
        "dirty_count": len(st_lines),
        "status_short": "\n".join(st_lines[:25]),
        "diff_stat": diff_res.stdout.strip()[:1200] if diff_res.returncode == 0 else "",
        "recent_commits": log_res.stdout.strip() if log_res.returncode == 0 else "",
    }


def _is_ancestor(ref: str, target: str, cwd: Path) -> bool:
    """Return True if git commit/ref `ref` is an ancestor of `target` in `cwd`."""
    if not ref:
        return False
    res = _run(["git", "merge-base", "--is-ancestor", ref, target], cwd=cwd)
    return res.returncode == 0


def merge_status(meta: dict, main_root: Path = MAIN_ROOT) -> dict:
    """Compute live PR and merge-destination status (Branch -> PR -> Beta v3-dev -> Public main)."""
    branch = str(meta.get("branch", ""))
    pr = meta.get("pr")
    pr_url = str(meta.get("pr_url") or "")
    status = str(meta.get("status", ""))
    merged_sha = str(meta.get("merged_sha") or "")
    recorded = [str(x) for x in (meta.get("merged_into") or []) if str(x)]

    check_ref = merged_sha or branch
    in_beta = ("v3-dev" in recorded) or (status in ("beta", "done")) or _is_ancestor(check_ref, "v3-dev", main_root)
    in_public = ("main" in recorded) or (status == "done") or _is_ancestor(check_ref, "main", main_root)
    merged_into = list(dict.fromkeys(recorded + (["v3-dev"] if in_beta else []) + (["main"] if in_public else [])))
    return {
        "branch": branch,
        "pr": pr,
        "pr_url": pr_url,
        "merged_sha": merged_sha,
        "in_beta": in_beta,
        "in_public": in_public,
        "merged_into": merged_into,
    }


def update_handoff(
    task_dir: Path,
    checkout_root: Path,
    *,
    meta: dict | None = None,
    agent: str = "",
    model: str = "",
    state_note: str = "",
    decisions_note: str = "",
    next_note: str = "",
    content: str | None = None,
) -> Path:
    """Overwrite optional `HANDOFF.md` sticky note (no append log) with compact current state."""
    hpath = task_dir / "HANDOFF.md"
    if content is not None:
        hpath.write_text(content, encoding="utf-8")
        return hpath

    gs = git_summary(checkout_root)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tid = (meta or {}).get("id", task_dir.name.split("-", 1)[0])
    title = (meta or {}).get("title", task_dir.name)
    stage = (meta or {}).get("status", "brainstorm")

    hmeta = {"updated_at": now, "last_agent": "", "last_model": "", "last_commit": ""}
    if hpath.is_file():
        old_meta, _ = parse_task_md(hpath)
        hmeta.update({k: v for k, v in old_meta.items() if k in hmeta})
    hmeta["updated_at"] = now
    if agent:
        hmeta["last_agent"] = agent
    if model:
        hmeta["last_model"] = model
    if gs["head_sha"]:
        hmeta["last_commit"] = gs["head_sha"]

    hbody = (
        f"# Handoff (Optional Sticky Note) — Task {tid}: {title}\n\n"
        f"- **Stage:** `{stage}` | **HEAD:** `{gs['head_sha'] or 'none'}` ({gs['dirty_count']} dirty)\n"
        f"- **State:** {state_note or 'See PLAN.md and git status.'}\n"
        f"- **Dead Ends / Gotchas:** {decisions_note or 'None.'}\n"
        f"- **Next:** {next_note or 'Continue from PLAN.md.'}\n"
    )
    hpath.write_text(dump_task_md(hmeta, hbody), encoding="utf-8")
    return hpath


def build_resume_prompt(task_id: str, *, worker: bool = False) -> dict:
    """Assemble a compact cold-boot resume prompt (or lean `--worker` execution brief for cheap/fast agents)."""
    meta, tfile, checkout_root = find_task(task_id)
    tid = str(meta["id"])
    slug = str(meta["slug"])
    tdir = tfile.parent
    is_wt = WORKTREES_DIR in checkout_root.parents
    gs = git_summary(checkout_root)
    convs = load_conversations(tdir)
    tier = str(meta.get("exec_tier") or "hydrogen")
    delegation_tool = (
        'invoke_subagent(TypeName="self", Model="flash")'
        if tier == "flash"
        else 'swarm(action="add", model="Gemini 4 Hydrogen (Medium)")'
        if tier == "hydrogen"
        else 'swarm(action="add", model="Claude Sonnet 5.5 (Medium)")'
    )

    handoff_text = (tdir / "HANDOFF.md").read_text(encoding="utf-8", errors="replace").strip() if (tdir / "HANDOFF.md").is_file() else ""
    verify_path = tdir / "evidence" / "verify.json"
    verify_line = "unverified"
    if verify_path.is_file():
        try:
            v = json.loads(verify_path.read_text(encoding="utf-8"))
            verify_line = f"ok={v.get('ok')} (sha={v.get('git_sha', '')[:7]})"
        except Exception:
            verify_line = "verify.json unreadable"

    codemap_lines: list[str] = []
    for rel in meta.get("owned_paths", [])[:8]:
        p = checkout_root / rel
        if p.is_file() and p.suffix in (".py", ".js", ".mjs"):
            info = codemap.parse_python(p) if p.suffix == ".py" else codemap.parse_js(p)
            secs = ", ".join(f"L{ln}:{sec.split('—')[0].strip()}" for ln, sec in info["sections"][:5])
            codemap_lines.append(f"- `{rel}` ({info['lines']}L)" + (f" [{secs}]" if secs else ""))

    stage = str(meta.get("status", ""))
    if worker:
        sections = [
            f"# Worker Brief — Task {tid}: {meta.get('title', slug)} (Tier: {tier})",
            f"- **Worktree:** `{checkout_root}` | **Branch:** `{meta.get('branch', '')}` | **Switch:** `?beta=1&feat={meta.get('switch', slug)}`",
            f"- **Plan:** Read `tasks/{tid}-{slug}/PLAN.md` and implement strictly within `owned_paths`.",
            f"- **Owned Paths:** {', '.join(meta.get('owned_paths', [])) or '(none)'}",
        ]
        if codemap_lines:
            sections.append("## Target Symbols (read only needed line ranges)\n" + "\n".join(codemap_lines))
        if handoff_text:
            sections.append("## Sticky Note (`HANDOFF.md`)\n" + handoff_text[:1200])
        sections.append(
            f"## Rules & Finish\n"
            f"1. Follow `AGENTS.md`. Do NOT edit files outside `owned_paths` or read unrelated monoliths.\n"
            f"2. Iterate with targeted tests first; then run `python tools/task.py verify {tid}` once before reporting.\n"
            f"3. Do NOT write a running handoff log. Report concise diff summary, test counts, and any risks."
        )
    else:
        gate_note = (
            f"STOP AT GATE: task is in `{stage}` — do NOT edit repo code or start a worktree until the user gives explicit go-ahead."
            if stage in PRE_BUILD_GATES
            else f"Active stage `{stage}` — edit strictly within `owned_paths` inside `{checkout_root}`."
        )
        sections = [
            f"# Resume Task {tid}: {meta.get('title', slug)}",
            f"- **Stage:** `{stage}` ({gate_note}) | **Exec Tier:** `{tier}`",
            f"- **Checkout:** `{checkout_root}` ({'worktree' if is_wt else 'main repo'}) | **Branch:** `{meta.get('branch', '')}` | **Switch:** `?beta=1&feat={meta.get('switch', slug)}`",
            f"- **Packet:** `tasks/{tid}-{slug}/` (`TASK.md`, `PLAN.md`)",
            f"- **Owned Paths:** {', '.join(meta.get('owned_paths', [])) or '(none)'}",
            f"- **Git:** HEAD `{gs['head_sha']}` {gs['head_subject']} | dirty: {gs['dirty_count']} | Verify: {verify_line}",
        ]
        if convs["primary"]:
            p = convs["primary"]
            sections.append(f"- **Primary Convo:** `{p['tool']}` `{p['id']}` ({p.get('label', '')})")
        if handoff_text:
            sections.append("\n## HANDOFF.md (Sticky Note)\n" + handoff_text[:1500])
        if gs["status_short"]:
            sections.append("\n## Working Tree (`git status --short`)\n```\n" + gs["status_short"] + "\n```")
        if gs["recent_commits"]:
            sections.append("\n## Recent Commits\n```\n" + gs["recent_commits"] + "\n```")
        if codemap_lines:
            sections.append("\n## Owned File Outlines\n" + "\n".join(codemap_lines))
        sections.append(
            f"\n## Standing Reminder\n"
            f"1. Read `tasks/{tid}-{slug}/PLAN.md` (and `AGENTS.md`). Respect stage gates (`brainstorm -> plan -> ready -> in-progress`).\n"
            f"2. `HANDOFF.md` is optional & overwrite-only — only write it if pausing mid-task with non-obvious dead ends."
        )

    prompt = "\n".join(sections).strip() + "\n"
    return {
        "ok": True,
        "id": tid,
        "stage": stage,
        "exec_tier": tier,
        "recommended_delegation": delegation_tool,
        "worker": worker,
        "checkout_root": str(checkout_root),
        "active_worktree": is_wt,
        "prompt": prompt,
        "conversations": convs,
    }


def cmd_diff(args: argparse.Namespace) -> int:
    """Print a compact diff summary (`--stat` + bounded patch + verify status) for low-token code review."""
    meta, tfile, checkout_root = find_task(args.id)
    tid = str(meta["id"])
    base = args.base
    stat = _run(["git", "diff", "--stat", f"{base}...HEAD"], cwd=checkout_root)
    wt_stat = _run(["git", "diff", "--stat"], cwd=checkout_root)
    vpath = tfile.parent / "evidence" / "verify.json"
    v_ok = "unverified"
    if vpath.is_file():
        try:
            v = json.loads(vpath.read_text(encoding="utf-8"))
            v_ok = f"ok={v.get('ok')} sha={str(v.get('git_sha', ''))[:7]}"
        except Exception:
            v_ok = "unreadable"
    print(f"=== REVIEW DIFF: Task {tid} ({meta.get('slug')}) | Verify: {v_ok} ===")
    if stat.stdout.strip():
        print("-- Committed vs " + base + " --")
        print(stat.stdout.strip())
    if wt_stat.stdout.strip():
        print("-- Uncommitted --")
        print(wt_stat.stdout.strip())
    if not args.stat_only:
        patch = _run(["git", "diff", f"{base}"], cwd=checkout_root)
        lines = patch.stdout.splitlines()
        limit = max(20, int(args.max_lines))
        print("\n".join(lines[:limit]))
        if len(lines) > limit:
            print(f"\n... truncated ({len(lines) - limit} more lines; pass --max-lines to expand)")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Advance a task's human-approved stage gate (`brainstorm -> plan -> ready`)."""
    res = approve_task_gate(
        args.id,
        target_status=args.to or None,
        approve_plan=bool(getattr(args, "approve_plan", False)),
        approved_by=getattr(args, "by", "human-cli") or "human-cli",
    )
    print(res.get("message") or res.get("error"))
    return 0 if res.get("ok") else 1


def cmd_link(args: argparse.Namespace) -> int:
    """Link an Antigravity or Claude conversation ID to a task packet."""
    meta, tfile, _ = find_task(args.id)
    res = link_conversation(
        tfile.parent,
        args.conv,
        tool=args.tool,
        label=args.label,
        stage=str(meta.get("status", "")),
        make_primary=not args.secondary,
    )
    print(f"Linked {args.tool} conversation {args.conv} to task {meta['id']} (primary={res['primary_id']}, total={len(res['items'])})")
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    """Update `HANDOFF.md` with current commit, agent metadata, and checkpoint notes."""
    meta, tfile, checkout_root = find_task(args.id)
    hpath = update_handoff(
        tfile.parent,
        checkout_root,
        meta=meta,
        agent=args.agent,
        model=args.model,
        state_note=args.state,
        decisions_note=args.decisions,
        next_note=args.next,
    )
    print(f"Updated handoff: {hpath}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Print a compact model-agnostic cold-boot resume prompt (or `--worker` execution brief)."""
    res = build_resume_prompt(args.id, worker=bool(getattr(args, "worker", False)))
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(res["prompt"])
    return 0


# @sec: cli — Argument parser entry point
def main(argv: list[str]) -> int:
    """CLI entry point for `tools/task.py`."""
    parser = argparse.ArgumentParser(description="Self-contained task & parallel worktree workflow CLI.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all tasks and their status/owned_paths.")
    sub.add_parser("dag", help="Compute conflict-free parallel execution waves.")
    sub.add_parser("sync", help="Sync GitHub issues to local data/tasks_index.json cache.")

    p_epic = sub.add_parser("epic", help="Create a GitHub Epic issue.")
    p_epic.add_argument("--title", required=True)
    p_epic.add_argument("--summary", default="")

    p_new = sub.add_parser("new", help="Create a new task packet (+ GitHub issue unless --local).")
    p_new.add_argument("--title", required=True)
    p_new.add_argument("--slug", required=True)
    p_new.add_argument("--epic", type=int, default=None)
    p_new.add_argument("--depends", default="", help="Comma-separated prerequisite task IDs.")
    p_new.add_argument("--owns", default="", help="Comma-separated owned file/folder paths.")
    p_new.add_argument("--switch", default="", help="Feature flag name (default: slug with underscores).")
    p_new.add_argument("--gpu", action="store_true", help="Mark task as requiring the local GPU lock.")
    p_new.add_argument("--problem", default="", help="Short problem/goal statement.")
    p_new.add_argument("--status", default="", choices=("",) + VALID_STATUSES, help="Initial gate status (default: brainstorm, or plan with --quick).")
    p_new.add_argument("--tier", default="", choices=("",) + VALID_EXEC_TIERS, help="Recommended execution model tier (flash|hydrogen|standard|pro).")
    p_new.add_argument("--quick", action="store_true", help="Fast-track small task (starts at plan gate with exec_tier=flash).")
    p_new.add_argument("--local", action="store_true", help="Skip GitHub issue creation (offline/draft mode).")

    p_approve = sub.add_parser("approve", help="Advance a task's stage gate (brainstorm -> plan -> ready).")
    p_approve.add_argument("id")
    p_approve.add_argument("--to", default="", choices=("",) + VALID_STATUSES, help="Explicit target gate status.")
    p_approve.add_argument("--approve-plan", action="store_true", help="Formally approve PLAN.md when advancing to 'ready'.")
    p_approve.add_argument("--by", default="human-cli", help="Approver label recorded in TASK.md (default: human-cli).")

    p_link = sub.add_parser("link", help="Link an Antigravity or Claude conversation ID to a task.")
    p_link.add_argument("id")
    p_link.add_argument("--conv", required=True, help="Conversation or session ID.")
    p_link.add_argument("--tool", default="antigravity", choices=("antigravity", "claude", "gemini", "other"))
    p_link.add_argument("--label", default="", help="Short label (e.g. 'Brainstorm', 'Build').")
    p_link.add_argument("--secondary", action="store_true", help="Add to history without replacing primary.")

    p_ckpt = sub.add_parser("checkpoint", help="Update HANDOFF.md with current commit and checkpoint notes.")
    p_ckpt.add_argument("id")
    p_ckpt.add_argument("--agent", default="", help="Agent tool name (e.g. gemini, claude).")
    p_ckpt.add_argument("--model", default="", help="Model name.")
    p_ckpt.add_argument("--state", default="", help="Current state note.")
    p_ckpt.add_argument("--decisions", default="", help="Decision or dead-end note.")
    p_ckpt.add_argument("--next", default="", help="Next step note.")

    p_resume = sub.add_parser("resume", help="Print a compact cold-boot resume prompt (or --worker brief).")
    p_resume.add_argument("id")
    p_resume.add_argument("--worker", action="store_true", help="Emit lean execution brief for a fast/cheap worker agent.")
    p_resume.add_argument("--json", action="store_true", help="Output structured JSON instead of plain markdown.")

    p_diff = sub.add_parser("diff", help="Print a compact diff summary + verify status for low-token review.")
    p_diff.add_argument("id")
    p_diff.add_argument("--base", default="v3-dev", help="Base git ref (default: v3-dev).")
    p_diff.add_argument("--stat-only", action="store_true", help="Show only --stat summary.")
    p_diff.add_argument("--max-lines", type=int, default=200, help="Max patch lines to print (default: 200).")

    p_start = sub.add_parser("start", help="Start a task in an isolated `_worktrees/wt_<id>-<slug>` worktree.")
    p_start.add_argument("id")
    p_start.add_argument("--base", default="v3-dev", help="Base git ref for the feature branch (default: v3-dev).")
    p_start.add_argument("--force", action="store_true", help="Override dependency or path-overlap guardrails.")

    p_verify = sub.add_parser("verify", help="Run deliverables, scope, pytest, JS tests, and Playwright checks.")
    p_verify.add_argument("id")
    p_verify.add_argument("--base", default="v3-dev", help="Base ref for scope diff check.")
    p_verify.add_argument("--skip-preview", action="store_true", help="Skip headless Playwright check.")
    p_verify.add_argument("--allow-extra", action="store_true", help="Allow modified files outside owned_paths.")

    p_pr = sub.add_parser("pr", help="Push branch, open GitHub PR against v3-dev, and record pr/pr_url in TASK.md.")
    p_pr.add_argument("id")
    p_pr.add_argument("--base", default="v3-dev", help="Base branch for the PR (default: v3-dev).")
    p_pr.add_argument("--draft", action="store_true", help="Open as a draft PR (skips verify.json gate).")

    p_finish = sub.add_parser("finish", help="Verify evidence, merge into v3-dev, remove worktree, and sync GitHub.")
    p_finish.add_argument("id")
    p_finish.add_argument("--merge", action="store_true", help="Merge feature branch into v3-dev and remove worktree.")
    p_finish.add_argument("--deploy-beta", action="store_true", help="Push v3-dev and trigger collect.yml (skipped for tooling-only tasks unless --force-deploy).")
    p_finish.add_argument("--force-deploy", action="store_true", help="Trigger collect.yml even for tooling/docs-only tasks.")
    p_finish.add_argument("--sync-main", action="store_true", help="Also sync shared agent/tooling files to origin/main with isolated tests.")
    p_finish.add_argument("--allow-stale", action="store_true", help="Allow verify.json from an earlier commit SHA.")

    p_sync_main = sub.add_parser("sync-main", help="Check or sync shared agent/tooling files from v3-dev to origin/main.")
    p_sync_main.add_argument("--check", action="store_true", help="Check drift only (exits 2 if drifted).")
    p_sync_main.add_argument("--push", action="store_true", help="Push synced commit to origin/main after isolated tests pass.")

    args = parser.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "dag": cmd_dag,
        "sync": cmd_sync,
        "sync-main": cmd_sync_main,
        "epic": cmd_epic,
        "new": cmd_new,
        "approve": cmd_approve,
        "link": cmd_link,
        "checkpoint": cmd_checkpoint,
        "resume": cmd_resume,
        "diff": cmd_diff,
        "start": cmd_start,
        "verify": cmd_verify,
        "pr": cmd_pr,
        "finish": cmd_finish,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
