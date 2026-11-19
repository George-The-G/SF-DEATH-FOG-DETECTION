#!/usr/bin/env python3
"""Cross-agent PreToolUse guardrails (H1-H5) for Antigravity and Claude Code."""
from __future__ import annotations

import fnmatch
import json
import re
import shlex
import sys
from pathlib import Path

PROTECTED_BRANCHES = {"v3-dev", "main", "data", "live-archive"}
PRE_BUILD_STATUSES = {"backlog", "scope", "brainstorm", "plan"}
_WT_ADD_VALUE_FLAGS = {"-b", "-B", "--orphan", "--reason"}


def _main_root() -> Path:
    """Return the main repository root, resolving out of `_worktrees/wt_*` if needed."""
    root = Path(__file__).resolve().parent.parent.parent
    if root.parent.name == "_worktrees":
        return root.parent.parent
    return root


def _parse_frontmatter_owned_status(task_md: Path) -> tuple[str, list[str]]:
    """Extract `status` and `owned_paths` from `TASK.md` frontmatter without external deps."""
    try:
        text = task_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", []
    if not text.startswith("---"):
        return "", []
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", []
    status = ""
    owned: list[str] = []
    in_owned = False
    for raw in parts[1].splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if in_owned and s.startswith("- "):
            val = s[2:].strip().strip('"').strip("'")
            if val:
                owned.append(val)
            continue
        in_owned = False
        if ":" not in s:
            continue
        key, rest = s.split(":", 1)
        key, rest = key.strip(), rest.strip()
        if key == "status":
            status = rest.strip('"').strip("'")
        elif key == "owned_paths":
            if rest in ("", "[]"):
                in_owned = rest == ""
            elif rest.startswith("[") and rest.endswith("]"):
                owned.extend(p.strip().strip('"').strip("'") for p in rest[1:-1].split(",") if p.strip())
    return status, owned


def _split_segments(cmd: str) -> list[str]:
    """Split a shell command string on `;`, `&&`, `||`, and newlines."""
    return [seg.strip() for seg in re.split(r"&&|\|\||[;\r\n]+", cmd) if seg.strip()]


def _tokenize(seg: str) -> list[str]:
    """Tokenize a shell command segment cleanly with whitespace fallback."""
    try:
        return shlex.split(seg, posix=False)
    except ValueError:
        return seg.split()


def _first_positional_after_worktree_add(tokens: list[str]) -> str:
    """Return the target path argument after `git worktree add`, skipping flags."""
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in _WT_ADD_VALUE_FLAGS:
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        return tok.strip('"').strip("'")
    return ""


def _check_command(cmd: str, main_root: Path, cwd: Path) -> tuple[str, str] | None:
    """Evaluate H1-H4 guardrails against a shell command string."""
    if not cmd:
        return None
    for seg in _split_segments(cmd):
        tokens = _tokenize(seg)
        if not tokens:
            continue
        idx = 1 if tokens[0] == "&" and len(tokens) > 1 else 0
        exe = Path(tokens[idx].strip('"').strip("'")).name.lower()
        if exe in ("git", "git.exe"):
            rest = tokens[idx + 1 :]
            # Skip leading git global options like -C <dir> or -c <k=v>
            sub_i = 0
            while sub_i < len(rest):
                t = rest[sub_i]
                if t in ("-C", "-c", "--git-dir", "--work-tree"):
                    sub_i += 2
                elif t.startswith("-"):
                    sub_i += 1
                else:
                    break
            sub = rest[sub_i:]
            if not sub:
                continue
            verb = sub[0].lower()
            if verb == "clean" and any(re.match(r"^-[A-Za-z]*[xX]", t) for t in sub[1:]):
                return ("deny", "H1 blocked `git clean` with -x/-X (data/ and _archive/ live in repo root).")
            if verb == "push":
                has_force = any(
                    t in ("--force", "--force-with-lease") or re.match(r"^-[A-Za-z]*f[A-Za-z]*$", t)
                    for t in sub[1:]
                )
                if has_force:
                    for t in sub[1:]:
                        if t.startswith("-"):
                            continue
                        for part in t.split(":"):
                            if part.split("/")[-1] in PROTECTED_BRANCHES:
                                return ("deny", f"H2 blocked force-push targeting protected branch '{part}'.")
            if verb == "worktree" and len(sub) >= 2 and sub[1].lower() == "add":
                target = _first_positional_after_worktree_add(sub[2:])
                if target:
                    norm = target.replace("\\", "/")
                    resolved = (cwd / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
                    wt_base = (main_root / "_worktrees").resolve()
                    in_wt = norm.startswith("_worktrees/") or "/_worktrees/" in norm or wt_base in resolved.parents
                    if not in_wt:
                        return ("deny", f"H3 blocked `git worktree add` outside `_worktrees/`: {target}")
        if re.search(r"tools[/\\]task\.py\b", seg) and re.search(r"\bapprove\b", seg) and "--approve-plan" in seg:
            return ("force_ask", "H4 requires explicit human confirmation for `tools/task.py approve --approve-plan`.")
    return None


def _matches_owned(rel: str, owned_paths: list[str]) -> bool:
    """Return True if a repo-relative POSIX path matches any entry in `owned_paths`."""
    for o in owned_paths:
        norm = o.strip().rstrip("/")
        if rel == norm or rel.startswith(norm + "/") or fnmatch.fnmatch(rel, o):
            return True
    return False


def _find_prebuild_task_for_conv(conv_id: str, main_root: Path) -> tuple[str, str, str] | None:
    """Return `(tid, slug, status)` if `conv_id` is linked to a task in a pre-build gate."""
    troot = main_root / "tasks"
    if not conv_id or not troot.is_dir():
        return None
    for child in sorted(troot.iterdir()):
        cfile = child / "conversations.json"
        tfile = child / "TASK.md"
        if not child.is_dir() or not cfile.is_file() or not tfile.is_file():
            continue
        try:
            cdata = json.loads(cfile.read_text(encoding="utf-8"))
        except Exception:
            continue
        ids = {str(cdata.get("primary_id") or "")}
        ids.update(str(item.get("id") or "") for item in (cdata.get("items") or []) if isinstance(item, dict))
        if conv_id in ids:
            status, _ = _parse_frontmatter_owned_status(tfile)
            if status in PRE_BUILD_STATUSES:
                tid, _, slug = child.name.partition("-")
                return tid, slug, status
    return None


def _check_write(path_str: str, conv_id: str, main_root: Path, cwd: Path) -> tuple[str, str] | None:
    """Evaluate H5 guardrails (worktree owned_paths + main-checkout pre-build gate)."""
    if not path_str:
        return None
    root_res = main_root.resolve()
    raw_p = Path(path_str)
    target = (cwd / raw_p).resolve() if not raw_p.is_absolute() else raw_p.resolve()
    try:
        rel = target.relative_to(root_res)
    except ValueError:
        return None
    rel_posix = rel.as_posix()
    if rel_posix == "tasks" or rel_posix.startswith("tasks/"):
        return None
    if len(rel.parts) >= 2 and rel.parts[0] == "_worktrees" and rel.parts[1].startswith("wt_"):
        if len(rel.parts) == 2:
            return None
        wt_rel = Path(*rel.parts[2:]).as_posix()
        if wt_rel == "tasks" or wt_rel.startswith("tasks/"):
            return None
        suffix = rel.parts[1][3:]
        tfile = root_res / "tasks" / suffix / "TASK.md"
        if not tfile.is_file() and "-" in suffix:
            tid = suffix.split("-", 1)[0]
            matches = sorted((root_res / "tasks").glob(f"{tid}-*/TASK.md"))
            if matches:
                tfile = matches[0]
        if tfile.is_file():
            _, owned = _parse_frontmatter_owned_status(tfile)
            if owned and not _matches_owned(wt_rel, owned):
                return ("ask", f"H5: '{wt_rel}' is outside task owned_paths ({', '.join(owned)}).")
        return None
    hit = _find_prebuild_task_for_conv(conv_id, root_res)
    if hit:
        tid, slug, status = hit
        return ("deny", f"H5 pre-build gate: task {tid} ({slug}) is at '{status}'. Approve plan and start a worktree before editing repo code.")
    return None


def _format_response(platform: str, decision: str, reason: str = "") -> dict:
    """Format a PreToolUse hook decision into Antigravity or Claude Code JSON schema."""
    if platform == "claude":
        c_dec = "ask" if decision == "force_ask" else decision
        out: dict[str, object] = {"hookEventName": "PreToolUse", "permissionDecision": c_dec}
        if reason:
            out["permissionDecisionReason"] = reason
        return {"hookSpecificOutput": out}
    resp: dict[str, str] = {"decision": decision}
    if reason:
        resp["reason"] = reason
    return resp


def evaluate_payload(payload: dict, main_root: Path | None = None) -> tuple[str, dict]:
    """Evaluate a PreToolUse JSON payload from Antigravity or Claude Code."""
    is_claude = "tool_name" in payload or "hook_event_name" in payload
    platform = "claude" if is_claude else "antigravity"
    root = (main_root or _main_root()).resolve()
    cwd = Path(str(payload.get("cwd") or root))
    if is_claude:
        tool_name = str(payload.get("tool_name") or "")
        args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        conv_id = str(payload.get("session_id") or payload.get("conversation_id") or "")
    else:
        tc = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
        tool_name = str(tc.get("name") or "")
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        conv_id = str(payload.get("conversationId") or "")

    result: tuple[str, str] | None = None
    if tool_name in ("run_command", "Bash"):
        cmd = str(args.get("CommandLine") or args.get("command") or "")
        result = _check_command(cmd, root, cwd)
    elif tool_name in ("write_file", "edit_file", "Write", "Edit"):
        path_str = str(args.get("Path") or args.get("file_path") or args.get("path") or "")
        result = _check_write(path_str, conv_id, root, cwd)

    if result:
        return platform, _format_response(platform, result[0], result[1])
    return platform, _format_response(platform, "allow")


def main() -> int:
    """Read PreToolUse JSON from stdin and emit native decision JSON (fail open)."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        _, resp = evaluate_payload(payload)
    except Exception:
        resp = {"decision": "allow"}
    print(json.dumps(resp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
