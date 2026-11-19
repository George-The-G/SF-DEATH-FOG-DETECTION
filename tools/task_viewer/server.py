#!/usr/bin/env python3
"""Portable Task Viewer web server for SF-DEATH-FOG-DETECTION.

Works standalone in any browser (`python tools/task_viewer/server.py`), inside
Cursor's Simple Browser, or docked inside Antigravity's side pane via the
`deathfog-tasks` UI plugin sidecar.

Usage:
    python tools/task_viewer/server.py [--port 8135] [--host 127.0.0.1]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

VIEWER_DIR = Path(__file__).resolve().parent
ROOT = VIEWER_DIR.parent.parent
sys.path.insert(0, str(ROOT))

from tools import task as task_cli  # noqa: E402

BETA_BASE_URL = "https://george-the-g.github.io/SF-DEATH-FOG-DETECTION/v3/"
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")


# @sec: helpers — Safe file readers, launcher detection, and command builders
def _read_text_safe(path: Path, max_bytes: int = 120_000) -> str:
    """Read UTF-8 text file safely up to `max_bytes`, returning empty string if missing."""
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except OSError:
        return ""


def _list_dir_files(directory: Path) -> list[str]:
    """Return sorted relative filenames inside `directory` (excluding `.gitkeep`)."""
    if not directory.is_dir():
        return []
    return sorted(
        p.name for p in directory.iterdir()
        if p.is_file() and p.name != ".gitkeep"
    )


_ROOT_DOC_ORDER = ("PLAN.md", "TASK.md", "HANDOFF.md", "conversations.json")
_TEXT_FILE_EXTS = {
    ".md", ".json", ".txt", ".html", ".htm", ".css", ".js", ".mjs",
    ".py", ".toml", ".yml", ".yaml", ".csv", ".log", ".svg",
}


def _list_task_files(task_dir: Path) -> list[dict]:
    """Return sorted task packet files up to depth 2 (root docs, mockups, evidence)."""
    if not task_dir.is_dir():
        return []
    entries: list[dict] = []

    def _entry(p: Path, rel_path: str, group: str) -> dict:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        return {
            "path": rel_path,
            "group": group,
            "name": p.name,
            "ext": p.suffix.lower(),
            "size": size,
        }

    root_files = [p for p in task_dir.iterdir() if p.is_file() and p.name != ".gitkeep"]
    root_rank = {name: idx for idx, name in enumerate(_ROOT_DOC_ORDER)}
    root_files.sort(key=lambda p: (root_rank.get(p.name, len(_ROOT_DOC_ORDER)), p.name.lower()))
    for p in root_files:
        entries.append(_entry(p, p.name, "root"))

    for group in ("mockups", "evidence"):
        sub = task_dir / group
        if sub.is_dir():
            for p in sorted((x for x in sub.iterdir() if x.is_file() and x.name != ".gitkeep"), key=lambda x: x.name.lower()):
                entries.append(_entry(p, f"{group}/{p.name}", group))

    return entries


def _read_task_file_safe(task_dir: Path, rel_path: str, max_bytes: int = 256_000) -> dict:
    """Safely read a UTF-8 text file or report binary metadata inside `task_dir`."""
    if not isinstance(rel_path, str) or not rel_path or rel_path.startswith("/") or "\\" in rel_path:
        return {"ok": False, "error": "Invalid file path."}
    parts = rel_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return {"ok": False, "error": "Path traversal is not allowed."}
    base = task_dir.resolve()
    candidate = (base / rel_path).resolve()
    if base not in candidate.parents:
        return {"ok": False, "error": "Resolved path escapes task directory."}
    if not candidate.is_file():
        return {"ok": False, "error": f"File not found: {rel_path}"}
    ext = candidate.suffix.lower()
    try:
        size = candidate.stat().st_size
    except OSError:
        size = 0
    if ext not in _TEXT_FILE_EXTS:
        return {"ok": True, "path": rel_path, "ext": ext, "size": size, "binary": True}
    try:
        raw = candidate.read_bytes()
        truncated = len(raw) > max_bytes
        text = raw[:max_bytes].decode("utf-8", errors="replace")
        return {
            "ok": True,
            "path": rel_path,
            "ext": ext,
            "size": size,
            "truncated": truncated,
            "content": text,
        }
    except OSError as exc:
        return {"ok": False, "error": f"Could not read file: {exc}"}


def detect_launchers() -> dict[str, str]:
    """Detect locally installed agent CLIs and terminal launchers on PATH."""
    return {
        name: (shutil.which(name) or "")
        for name in ("claude", "wt", "gemini", "agy")
    }


def build_claude_launch_cmd(
    cwd: Path,
    *,
    prompt_file: Path | None = None,
    session_id: str = "",
    launchers: dict[str, str] | None = None,
) -> list[str]:
    """Build a terminal launch command for Claude Code in `cwd` (resume prompt or session ID)."""
    bins = launchers if launchers is not None else detect_launchers()
    claude_bin = bins.get("claude") or "claude"
    if session_id:
        inner = f"& '{claude_bin}' --resume '{session_id}'"
    elif prompt_file is not None:
        pf = str(prompt_file).replace("'", "''")
        inner = f"$p = Get-Content -Raw -Encoding UTF8 '{pf}'; & '{claude_bin}' $p"
    else:
        inner = f"& '{claude_bin}'"

    if sys.platform == "win32":
        wt_bin = bins.get("wt")
        if wt_bin:
            return [wt_bin, "-d", str(cwd), "powershell", "-NoExit", "-Command", inner]
        return ["powershell", "-NoExit", "-Command", f"Set-Location '{cwd}'; {inner}"]
    if session_id:
        return [claude_bin, "--resume", session_id]
    return [claude_bin]


def _validate_conv_id(conv_id: str) -> str | None:
    """Validate an Antigravity/Claude conversation ID against safe path-segment characters."""
    s = str(conv_id or "").strip()
    return s if _CONV_ID_RE.match(s) else None


def _open_os_folder(directory: Path) -> None:
    """Open `directory` in the host OS file explorer."""
    if sys.platform == "win32":
        os.startfile(str(directory))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(directory)])
    else:
        subprocess.Popen(["xdg-open", str(directory)])


def _load_convo_preview(conv_id: str, *, brain_root: Path | None = None) -> dict:
    """Read local Antigravity brain artifacts and recent transcript turns with zero LLM tokens."""
    valid_id = _validate_conv_id(conv_id)
    if not valid_id:
        return {"ok": False, "error": f"Invalid conversation ID: {conv_id}"}

    root_dir = (brain_root if brain_root is not None else (Path.home() / ".gemini" / "antigravity" / "brain")).resolve()
    brain_dir = (root_dir / valid_id).resolve()
    if root_dir not in brain_dir.parents:
        return {"ok": False, "error": f"Invalid conversation path: {conv_id}"}

    if not brain_dir.is_dir():
        return {
            "ok": True,
            "conv_id": valid_id,
            "exists": False,
            "brain_dir": str(brain_dir),
            "artifacts": [],
            "recent_turns": [],
        }

    artifacts = []
    for p in sorted(brain_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".md":
            artifacts.append({
                "name": p.name,
                "size": p.stat().st_size,
                "content": _read_text_safe(p, max_bytes=16_384),
            })

    recent_turns = []
    transcript = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if transcript.is_file():
        try:
            lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            for ln in lines:
                if not ln.strip():
                    continue
                try:
                    obj = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                content = str(obj.get("content") or "").strip()
                if not content:
                    continue
                t_type = str(obj.get("type") or "")
                t_src = str(obj.get("source") or "")
                if t_type not in ("USER_INPUT", "PLANNER_RESPONSE") and t_src not in ("USER_EXPLICIT", "MODEL"):
                    continue
                is_user = "USER" in t_type or "USER" in t_src
                recent_turns.append({
                    "role": "User" if is_user else "Assistant",
                    "timestamp": str(obj.get("created_at") or ""),
                    "snippet": content[:240],
                })
            recent_turns = recent_turns[-8:]
        except OSError:
            recent_turns = []

    return {
        "ok": True,
        "conv_id": valid_id,
        "exists": True,
        "brain_dir": str(brain_dir),
        "artifacts": artifacts,
        "recent_turns": recent_turns,
    }


# @sec: state — Build live DAG, stage gates, handoff, conversations, and git state
def build_state() -> dict:
    """Collect all tasks, active worktrees, DAG waves, gates, handoffs, conversations, and evidence."""
    main_root = task_cli.MAIN_ROOT
    all_tasks = task_cli.load_all_tasks(main_root)
    wt_active = task_cli.active_worktree_tasks()

    merged: dict[str, tuple[dict, Path, Path, bool]] = {}
    for tid, (meta, tfile) in all_tasks.items():
        merged[tid] = (meta, tfile, main_root, False)
    for tid, (meta, wt_root) in wt_active.items():
        tfile = main_root / "tasks" / f"{meta['id']}-{meta['slug']}" / "TASK.md"
        if not tfile.is_file():
            tfile = wt_root / "tasks" / f"{meta['id']}-{meta['slug']}" / "TASK.md"
        merged[tid] = (meta, tfile, wt_root, True)

    waves_raw = task_cli.compute_waves({tid: (m, tf) for tid, (m, tf, _, _) in merged.items()})
    waves = [[str(m["id"]) for m in w] for w in waves_raw]

    tasks_out: dict[str, dict] = {}
    active_task_id: str | None = None
    for tid in sorted(merged):
        meta, tfile, checkout_root, is_wt = merged[tid]
        slug = meta.get("slug", "")
        tdir = tfile.parent
        verify_path = tdir / "evidence" / "verify.json"
        verify_data = None
        if verify_path.is_file():
            try:
                verify_data = json.loads(verify_path.read_text(encoding="utf-8"))
            except Exception:
                verify_data = None

        usage_path = tdir / "evidence" / "usage.json"
        usage_data = None
        if usage_path.is_file():
            try:
                usage_data = json.loads(usage_path.read_text(encoding="utf-8"))
            except Exception:
                usage_data = None

        handoff_path = tdir / "HANDOFF.md"
        handoff_md = _read_text_safe(handoff_path)
        handoff_meta: dict = {}
        if handoff_path.is_file():
            try:
                handoff_meta, _ = task_cli.parse_task_md(handoff_path)
            except Exception:
                handoff_meta = {}

        if active_task_id is None and (is_wt or meta.get("status") == "in-progress"):
            active_task_id = tid

        status_str = str(meta.get("status", ""))
        plan_readiness = task_cli.check_plan_readiness(tdir, meta)
        tasks_out[tid] = {
            **meta,
            "id": tid,
            "active_worktree": is_wt,
            "worktree_path": str(checkout_root) if is_wt else "",
            "task_dir": str(tdir),
            "task_md": _read_text_safe(tdir / "TASK.md"),
            "plan_md": _read_text_safe(tdir / "PLAN.md"),
            "handoff_md": handoff_md,
            "handoff_meta": handoff_meta,
            "conversations": task_cli.load_conversations(tdir),
            "merge_status": task_cli.merge_status(meta, main_root),
            "exec_tier": str(meta.get("exec_tier") or "hydrogen"),
            "git_summary": task_cli.git_summary(checkout_root) if is_wt else {},
            "next_gate": task_cli.next_gate_status(status_str),
            "is_pre_build_gate": status_str in task_cli.PRE_BUILD_GATES,
            "plan_readiness": plan_readiness,
            "plan_approved": plan_readiness["plan_approved"],
            "plan_drifted": plan_readiness["plan_drifted"],
            "plan_approved_at": plan_readiness["plan_approved_at"],
            "plan_approved_by": plan_readiness["plan_approved_by"],
            "plan_approved_sha": plan_readiness["plan_approved_sha"],
            "mockups": _list_dir_files(tdir / "mockups"),
            "evidence": _list_dir_files(tdir / "evidence"),
            "task_files": _list_task_files(tdir),
            "verify": verify_data,
            "usage": usage_data,
            "token_usage": str(
                meta.get("token_usage")
                or (usage_data.get("summary") if isinstance(usage_data, dict) else "")
                or ""
            ),
            "beta_url": f"{BETA_BASE_URL}?beta=1&feat={meta.get('switch', slug)}",
        }

    if active_task_id is None and tasks_out:
        active_task_id = waves[0][0] if waves and waves[0] else sorted(tasks_out)[0]

    return {
        "repo_root": str(main_root),
        "active_task_id": active_task_id,
        "available_launchers": detect_launchers(),
        "main_parity": task_cli.shared_main_drift(main_root),
        "waves": waves,
        "tasks": tasks_out,
    }


# @sec: actions — Safe allowlisted task mutations, gate approvals, and agent launchers
def run_action(action_or_payload: str | dict, task_id: str = "") -> dict:
    """Execute an allowlisted task action (gates, plan/handoff edits, conversations, resume, verify)."""
    if isinstance(action_or_payload, dict):
        payload = action_or_payload
        action = str(payload.get("action", ""))
        task_id = str(payload.get("task_id", ""))
    else:
        payload = {"action": action_or_payload, "task_id": task_id}
        action = str(action_or_payload)

    if action == "sync_main":
        return task_cli.sync_shared_to_main(push=bool(payload.get("push", True)))

    if action == "preview_conversation":
        return _load_convo_preview(str(payload.get("conv_id", "")))

    if action == "open_convo_folder":
        valid_id = _validate_conv_id(str(payload.get("conv_id", "")))
        if not valid_id:
            return {"ok": False, "error": "Invalid conversation ID."}
        brain_dir = Path.home() / ".gemini" / "antigravity" / "brain" / valid_id
        if not brain_dir.is_dir():
            return {"ok": False, "error": f"Brain folder not found: {brain_dir}"}
        _open_os_folder(brain_dir)
        return {"ok": True, "message": f"Opened {brain_dir}"}

    state = build_state()
    info = state["tasks"].get(task_id)
    if not info:
        return {"ok": False, "error": f"Unknown task id: {task_id}"}

    tdir = Path(info["task_dir"])
    checkout_root = Path(info["worktree_path"]) if info.get("worktree_path") else task_cli.MAIN_ROOT

    if action == "open_folder":
        if not tdir.exists():
            return {"ok": False, "error": f"Directory does not exist: {tdir}"}
        _open_os_folder(tdir)
        return {"ok": True, "message": f"Opened {tdir}"}

    if action == "open_beta":
        url = info["beta_url"]
        webbrowser.open(url)
        return {"ok": True, "message": f"Opened {url}"}

    if action == "approve_gate":
        target = str(payload.get("to") or "").strip() or None
        return task_cli.approve_task_gate(
            task_id,
            target_status=target,
            approve_plan=bool(payload.get("approve_plan", False)),
            approved_by="human-ui",
        )

    if action == "approve_plan":
        return task_cli.approve_task_gate(
            task_id,
            target_status="ready",
            approve_plan=True,
            approved_by="human-ui",
        )

    if action == "reopen_plan":
        return task_cli.approve_task_gate(task_id, target_status="plan")

    if action == "save_plan":
        content = str(payload.get("content", ""))
        (tdir / "PLAN.md").write_text(content, encoding="utf-8")
        return {"ok": True, "message": f"Saved PLAN.md ({len(content)} chars)"}

    if action == "save_handoff":
        content = str(payload.get("content", ""))
        task_cli.update_handoff(tdir, checkout_root, content=content)
        return {"ok": True, "message": f"Saved HANDOFF.md ({len(content)} chars)"}

    if action == "snapshot_handoff":
        gs = task_cli.git_summary(checkout_root)
        note = f"Snapshot from Task Viewer: HEAD {gs['head_sha']} ({gs['dirty_count']} dirty file(s))"
        task_cli.update_handoff(tdir, checkout_root, meta=info, state_note=note)
        return {"ok": True, "message": note}

    if action == "link_conversation":
        conv_id = str(payload.get("conv_id", "")).strip()
        tool = str(payload.get("tool", "antigravity")).strip() or "antigravity"
        label = str(payload.get("label", "")).strip()
        make_primary = bool(payload.get("make_primary", True))
        try:
            convs = task_cli.link_conversation(
                tdir,
                conv_id,
                tool=tool,
                label=label,
                stage=str(info.get("status", "")),
                make_primary=make_primary,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": f"Linked {tool} conversation {conv_id}", "conversations": convs}

    if action == "set_primary_conversation":
        conv_id = str(payload.get("conv_id", "")).strip()
        try:
            convs = task_cli.set_primary_conversation(tdir, conv_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": f"Primary conversation set to {conv_id}", "conversations": convs}

    if action == "resume_prompt":
        return task_cli.build_resume_prompt(task_id, worker=bool(payload.get("worker", False)))

    if action == "launch_claude":
        launchers = detect_launchers()
        if not launchers.get("claude"):
            return {"ok": False, "error": "Claude Code CLI (`claude`) not found on PATH."}
        session_id = str(payload.get("session_id", "")).strip()
        prompt_file: Path | None = None
        if not session_id:
            res = task_cli.build_resume_prompt(task_id)
            prompt_file = Path(tempfile.gettempdir()) / f"deathfog_resume_{task_id}.md"
            prompt_file.write_text(res["prompt"], encoding="utf-8")
        cmd = build_claude_launch_cmd(checkout_root, prompt_file=prompt_file, session_id=session_id, launchers=launchers)
        subprocess.Popen(cmd, cwd=str(checkout_root))
        return {
            "ok": True,
            "message": f"Launched Claude in {checkout_root}" + (f" (session {session_id})" if session_id else ""),
            "cmd": cmd,
        }

    if action == "start":
        res = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "task.py"), "start", task_id],
            cwd=task_cli.MAIN_ROOT,
            text=True,
            capture_output=True,
        )
        return {
            "ok": res.returncode == 0,
            "returncode": res.returncode,
            "output": (res.stdout + "\n" + res.stderr).strip(),
        }

    if action == "read_task_file":
        return _read_task_file_safe(tdir, str(payload.get("path", "")))

    if action == "verify":
        res = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "task.py"), "verify", task_id, "--skip-preview"],
            cwd=task_cli.MAIN_ROOT,
            text=True,
            capture_output=True,
        )
        return {
            "ok": res.returncode == 0,
            "returncode": res.returncode,
            "output": (res.stdout + "\n" + res.stderr).strip(),
        }

    return {"ok": False, "error": f"Unsupported action: {action}"}


def _resolve_task_file(task_id: str, kind: str, filename: str) -> Path | None:
    """Safely resolve a file inside a task's `mockups/` or `evidence/` directory."""
    if kind not in ("mockups", "evidence"):
        return None
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    state = build_state()
    info = state["tasks"].get(task_id)
    if not info:
        return None
    candidate = (Path(info["task_dir"]) / kind / filename).resolve()
    allowed_dir = (Path(info["task_dir"]) / kind).resolve()
    if allowed_dir not in candidate.parents or not candidate.is_file():
        return None
    return candidate


# @sec: http — Standalone HTTP server handler and CLI entry point
class TaskViewerHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving the Task Viewer UI, state API, files, and safe actions."""

    def log_message(self, fmt, *args):
        pass

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: dict, status: int = 200) -> None:
        self._send_bytes(json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            html = (VIEWER_DIR / "index.html").read_bytes()
            self._send_bytes(html, "text/html; charset=utf-8")
            return
        if path == "/api/state":
            qs = parse_qs(parsed.query)
            refresh_res = None
            if (qs.get("refresh") or [""])[0] == "1":
                refresh_res = task_cli.refresh_git_refs(task_cli.MAIN_ROOT)
            state = build_state()
            if refresh_res is not None:
                state["git_refresh"] = refresh_res
            self._send_json(state)
            return
        if path == "/api/file":
            qs = parse_qs(parsed.query)
            tid = (qs.get("id") or [""])[0]
            kind = (qs.get("kind") or [""])[0]
            name = (qs.get("name") or [""])[0]
            fpath = _resolve_task_file(tid, kind, name)
            if fpath is not None:
                ctype = mimetypes.guess_type(fpath.name)[0] or "application/octet-stream"
                self._send_bytes(fpath.read_bytes(), ctype)
                return
            self._send_json({"ok": False, "error": "File not found"}, status=404)
            return
        if path.startswith("/api/file/"):
            parts = [unquote(p) for p in path[len("/api/file/"):].split("/", 2)]
            if len(parts) == 3:
                fpath = _resolve_task_file(parts[0], parts[1], parts[2])
                if fpath is not None:
                    ctype = mimetypes.guess_type(fpath.name)[0] or "application/octet-stream"
                    self._send_bytes(fpath.read_bytes(), ctype)
                    return
            self._send_json({"ok": False, "error": "File not found"}, status=404)
            return
        self._send_json({"ok": False, "error": "Not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/action":
            self._send_json({"ok": False, "error": "Not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "Invalid JSON"}, status=400)
            return
        res = run_action(payload)
        self._send_json(res, status=200 if res.get("ok") else 400)


def main(argv: list[str]) -> int:
    """CLI entry point for standalone or sidecar-proxied Task Viewer server."""
    parser = argparse.ArgumentParser(description="Run the Death Fog Task Viewer web server.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8135, help="Bind port (default: 8135).")
    parser.add_argument("--state-json", action="store_true", help="Print current state JSON and exit.")
    parser.add_argument("--action-json", default="", help="Run JSON action payload and exit.")
    parser.add_argument("--action-stdin", action="store_true", help="Read JSON action payload from stdin and exit.")
    args = parser.parse_args(argv)

    if args.state_json:
        print(json.dumps(build_state()))
        return 0
    if args.action_stdin:
        raw = sys.stdin.read() or "{}"
        payload = json.loads(raw)
        print(json.dumps(run_action(payload)))
        return 0
    if args.action_json:
        payload = json.loads(args.action_json)
        print(json.dumps(run_action(payload)))
        return 0

    srv = ThreadingHTTPServer((args.host, args.port), TaskViewerHandler)
    print(f"Death Fog Task Viewer listening at http://{args.host}:{args.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
