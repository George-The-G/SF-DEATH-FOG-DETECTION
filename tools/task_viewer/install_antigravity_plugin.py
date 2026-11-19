#!/usr/bin/env python3
"""Install or update the Antigravity UI plugin wrapper for the Death Fog Task Viewer.

Writes `~/.gemini/config/plugins/deathfog-tasks/plugin.json` and
`sidecars/task_viewer/sidecar.json` pointing to this repository's portable
`tools/task_viewer/antigravity_sidecar.mjs`.

Usage:
    python tools/task_viewer/install_antigravity_plugin.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

VIEWER_DIR = Path(__file__).resolve().parent
REPO_ROOT = VIEWER_DIR.parent.parent


def install_plugin(plugin_root: Path | None = None) -> Path:
    """Write the Antigravity `deathfog-tasks` plugin manifest and sidecar config."""
    target = plugin_root or (Path.home() / ".gemini" / "config" / "plugins" / "deathfog-tasks")
    sidecar_dir = target / "sidecars" / "task_viewer"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    plugin_json = {
        "name": "deathfog-tasks",
        "description": "Live DAG task board, worktree auto-focus, plan/mockup/evidence viewer, and quick actions for SF-DEATH-FOG-DETECTION.",
    }
    (target / "plugin.json").write_text(json.dumps(plugin_json, indent=2) + "\n", encoding="utf-8")

    py_bin = REPO_ROOT / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")
    sidecar_json = {
        "command": "node",
        "args": [str((VIEWER_DIR / "antigravity_sidecar.mjs").resolve())],
        "env": {
            "DEATHFOG_REPO": str(REPO_ROOT.resolve()),
            "DEATHFOG_PYTHON": str(py_bin.resolve()) if py_bin.exists() else sys.executable,
        },
        "restart_policy": "always",
        "display_name": "Death Fog Tasks",
        "description": "Live DAG waves, active worktree auto-focus, PLAN/mockups/evidence viewer, and task actions for SF-DEATH-FOG-DETECTION.",
        "has_web_ui": True,
        "agent_permissions": {
            "allows_user_interactions": True,
        },
        "ui_config": {
            "display_name": "Death Fog Tasks",
            "views": [
                {
                    "path": "/",
                    "entrypoint": "SIDECAR_UI_ENTRYPOINT_AUX_PANE",
                    "title": "Death Fog Tasks",
                }
            ],
        },
    }
    (sidecar_dir / "sidecar.json").write_text(json.dumps(sidecar_json, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> int:
    """CLI entry point for installing the Antigravity UI plugin wrapper."""
    target = install_plugin()
    print(f"Installed Antigravity UI plugin at: {target}")
    print("Restart or toggle 'deathfog-tasks' in Antigravity Customizations to activate the side pane.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
