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
        tfile = wt_root / "tasks" / f"{meta['id']}-{meta['slug']}" / "TASK.md"
        merged[tid] = (meta, tfile, wt_root, True)

    waves_raw = task_cli.compute_waves({tid: (m, tf) for tid, (m, tf, _, _) in merged.items()})
    waves = [[str(m["id"]) for m in w] for w in waves_raw]

    tasks_out: dict[str, dict] = {}
    active_task_id: str | None = None
    for tid in sorted(merged):
        meta, tfile, checkout_root, is_wt = merged[tid]
        slug = meta.get("slug", "")
        tdir = checkout_root / "tasks" / f"{tid}-{slug}"
        verify_path = tdir / "evidence" / "verify.json"
        verify_data = None
        if verify_path.is_file():
            try:
                verify_data = json.loads(verify_path.read_text(encoding="utf-8"))
            except Exception:
                verify_data = None

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
            "mockups": _list_dir_files(tdir / "mockups"),
            "evidence": _list_dir_files(tdir / "evidence"),
            "verify": verify_data,
            "beta_url": f"{BETA_BASE_URL}?beta=1&feat={meta.get('switch', slug)}",
        }

    if active_task_id is None and tasks_out:
        active_task_id = waves[0][0] if waves and waves[0] else sorted(tasks_out)[0]

    return {
        "repo_root": str(main_root),
        "active_task_id": active_task_id,
        "available_launchers": detect_launchers(),
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

    state = build_state()
    info = state["tasks"].get(task_id)
    if not info:
        return {"ok": False, "error": f"Unknown task id: {task_id}"}

    tdir = Path(info["task_dir"])
    checkout_root = Path(info["worktree_path"]) if info.get("worktree_path") else task_cli.MAIN_ROOT

    if action == "open_folder":
        if not tdir.exists():
            return {"ok": False, "error": f"Directory does not exist: {tdir}"}
        if sys.platform == "win32":
            os.startfile(str(tdir))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(tdir)])
        else:
            subprocess.Popen(["xdg-open", str(tdir)])
        return {"ok": True, "message": f"Opened {tdir}"}

    if action == "open_beta":
        url = info["beta_url"]
        webbrowser.open(url)
        return {"ok": True, "message": f"Opened {url}"}

    if action == "approve_gate":
        target = str(payload.get("to") or "").strip() or None
        return task_cli.approve_task_gate(task_id, target_status=target)

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
            self._send_json(build_state())
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
