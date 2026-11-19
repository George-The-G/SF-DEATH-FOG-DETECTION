"""Unit tests for the self-contained task lifecycle & DAG wave scheduler (`tools/task.py`)."""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import task
from tools.task_viewer import install_antigravity_plugin, server as task_viewer_server


class TestTaskWorkflow(unittest.TestCase):
    def test_frontmatter_roundtrip_and_validation(self):
        """TASK.md YAML frontmatter parses, validates, and serializes cleanly without external deps."""
        meta = {
            "id": "042",
            "slug": "grid-artifact-fix",
            "title": "Fix 320m column-skip grid artifact",
            "issue": 42,
            "epic": 40,
            "pr": 99,
            "pr_url": "https://github.com/George-The-G/SF-DEATH-FOG-DETECTION/pull/99",
            "status": "review",
            "branch": "feat/042-grid-artifact-fix",
            "switch": "col_top_clamp",
            "depends_on": ["041"],
            "owned_paths": ["site/v3/fog2.js", "tests_js/test_fog2.mjs"],
            "gpu_required": False,
            "deliverables": ["tasks/042-grid-artifact-fix/PLAN.md", "site/v3/fog2.js"],
        }
        body = "# Task 042\n\nDetails here.\n"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "TASK.md"
            p.write_text(task.dump_task_md(meta, body), encoding="utf-8")
            parsed, parsed_body = task.parse_task_md(p)
            self.assertEqual(task.validate_task_meta(parsed, p), [])
            self.assertEqual(parsed["id"], "042")
            self.assertEqual(parsed["issue"], 42)
            self.assertEqual(parsed["pr"], 99)
            self.assertEqual(parsed["pr_url"], "https://github.com/George-The-G/SF-DEATH-FOG-DETECTION/pull/99")
            self.assertEqual(parsed["gpu_required"], False)
            self.assertEqual(parsed["owned_paths"], ["site/v3/fog2.js", "tests_js/test_fog2.mjs"])
            self.assertIn("Details here.", parsed_body)

    def test_dag_parallel_waves_respect_deps_paths_and_gpu(self):
        """Parallel waves serialize overlapping owned_paths, GPU locks, and explicit depends_on."""
        tasks = {
            "001": (
                {"id": "001", "slug": "a", "status": "ready", "depends_on": [], "owned_paths": ["site/v3/fog2.js"], "gpu_required": False},
                Path("tasks/001-a/TASK.md"),
            ),
            "002": (
                {"id": "002", "slug": "b", "status": "ready", "depends_on": [], "owned_paths": ["microclimate/wetness.py"], "gpu_required": False},
                Path("tasks/002-b/TASK.md"),
            ),
            "003": (
                {"id": "003", "slug": "c", "status": "ready", "depends_on": [], "owned_paths": ["site/v3/fog2.js"], "gpu_required": False},
                Path("tasks/003-c/TASK.md"),
            ),
            "004": (
                {"id": "004", "slug": "d", "status": "ready", "depends_on": ["001", "002"], "owned_paths": ["site/v3/app.js"], "gpu_required": False},
                Path("tasks/004-d/TASK.md"),
            ),
            "005": (
                {"id": "005", "slug": "gpu1", "status": "ready", "depends_on": [], "owned_paths": ["microclimate/wind.py"], "gpu_required": True},
                Path("tasks/005-gpu1/TASK.md"),
            ),
            "006": (
                {"id": "006", "slug": "gpu2", "status": "ready", "depends_on": [], "owned_paths": ["microclimate/tracer.py"], "gpu_required": True},
                Path("tasks/006-gpu2/TASK.md"),
            ),
        }
        waves = task.compute_waves(tasks)
        wave_ids = [[m["id"] for m in w] for w in waves]
        # Wave 1 can run 001 (fog2.js), 002 (wetness.py), and 005 (GPU #1) in parallel
        self.assertEqual(wave_ids[0], ["001", "002", "005"])
        # Wave 2 runs 003 (fog2.js), 004 (depends on 001+002), and 006 (GPU #2) in parallel
        self.assertEqual(wave_ids[1], ["003", "004", "006"])

    def test_task_viewer_state_and_plugin_installer(self):
        """Task viewer state builder and Antigravity plugin installer produce valid JSON."""
        state = task_viewer_server.build_state()
        self.assertIn("waves", state)
        self.assertIn("tasks", state)
        self.assertIn("available_launchers", state)
        self.assertGreaterEqual(len(state["tasks"]), 4)
        sample = next(iter(state["tasks"].values()))
        self.assertIn("conversations", sample)
        self.assertIn("merge_status", sample)
        self.assertIn("exec_tier", sample)
        self.assertIn("handoff_md", sample)
        self.assertIn("next_gate", sample)
        with tempfile.TemporaryDirectory() as td:
            out = install_antigravity_plugin.install_plugin(Path(td) / "deathfog-tasks")
            self.assertTrue((out / "plugin.json").is_file())
            self.assertTrue((out / "sidecars" / "task_viewer" / "sidecar.json").is_file())

    def test_stage_gates_block_start_until_approved(self):
        """Tasks in brainstorm/plan gates cannot be started until approved to ready."""
        self.assertEqual(task.next_gate_status("brainstorm"), "plan")
        self.assertEqual(task.next_gate_status("plan"), "ready")
        self.assertIsNone(task.next_gate_status("ready"))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            meta = {
                "id": "099",
                "slug": "gate-demo",
                "title": "Gate Demo",
                "issue": None,
                "epic": None,
                "pr": None,
                "pr_url": "",
                "status": "brainstorm",
                "branch": "feat/099-gate-demo",
                "switch": "gate_demo",
                "depends_on": [],
                "owned_paths": ["tools/task.py"],
                "gpu_required": False,
                "deliverables": ["tasks/099-gate-demo/PLAN.md"],
            }
            tdir = task.scaffold_task_dir(root, meta, problem="Test gating")
            self.assertFalse((tdir / "HANDOFF.md").exists())
            self.assertIn("Brainstorm", (tdir / "PLAN.md").read_text(encoding="utf-8"))

            tfile = tdir / "TASK.md"
            with mock.patch.object(task, "find_task", return_value=(dict(meta), tfile, root)):
                rc = task.cmd_start(argparse.Namespace(id="099", base="v3-dev", force=False))
                self.assertEqual(rc, 1)

            def _find_live(_tid):
                m, _ = task.parse_task_md(tfile)
                return m, tfile, root

            with mock.patch.object(task, "find_task", side_effect=_find_live):
                r1 = task.approve_task_gate("099")
                self.assertTrue(r1["ok"])
                self.assertEqual(r1["status"], "plan")
                r2 = task.approve_task_gate("099")
                self.assertTrue(r2["ok"])
                self.assertEqual(r2["status"], "ready")

    def test_conversations_handoff_and_resume_prompt(self):
        """Primary + collapsible conversation links, HANDOFF.md checkpoints, and resume prompt work end-to-end."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            meta = {
                "id": "098",
                "slug": "convo-demo",
                "title": "Convo Demo",
                "status": "plan",
                "branch": "feat/098-convo-demo",
                "switch": "convo_demo",
                "depends_on": [],
                "owned_paths": ["tools/task.py"],
                "gpu_required": False,
                "deliverables": ["tasks/098-convo-demo/PLAN.md"],
            }
            tdir = task.scaffold_task_dir(root, meta, problem="Test convo links")
            c1 = task.link_conversation(tdir, "ag-uuid-1111", tool="antigravity", label="Brainstorm", stage="brainstorm")
            self.assertEqual(c1["primary"]["id"], "ag-uuid-1111")
            self.assertEqual(len(c1["history"]), 0)

            c2 = task.link_conversation(tdir, "cl-sess-2222", tool="claude", label="Build", stage="in-progress")
            self.assertEqual(c2["primary"]["id"], "cl-sess-2222")
            self.assertEqual([h["id"] for h in c2["history"]], ["ag-uuid-1111"])

            c3 = task.set_primary_conversation(tdir, "ag-uuid-1111")
            self.assertEqual(c3["primary"]["id"], "ag-uuid-1111")
            self.assertEqual([h["id"] for h in c3["history"]], ["cl-sess-2222"])

            task.update_handoff(tdir, root, meta=meta, agent="gemini", model="Gemini 4", state_note="First note")
            task.update_handoff(tdir, root, meta=meta, agent="gemini", model="Gemini 4", state_note="Plan drafted")
            hmeta, hbody = task.parse_task_md(tdir / "HANDOFF.md")
            self.assertEqual(hmeta["last_agent"], "gemini")
            self.assertIn("Plan drafted", hbody)
            self.assertNotIn("First note", hbody)  # overwrite-only, no append log

            with mock.patch.object(task, "find_task", return_value=(meta, tdir / "TASK.md", root)):
                res = task.build_resume_prompt("098")
                self.assertTrue(res["ok"])
                self.assertIn("STOP AT GATE", res["prompt"])
                self.assertIn("ag-uuid-1111", res["prompt"])
                self.assertIn("Plan drafted", res["prompt"])

                worker = task.build_resume_prompt("098", worker=True)
                self.assertTrue(worker["worker"])
                self.assertIn("Worker Brief", worker["prompt"])
                self.assertLess(len(worker["prompt"]), len(res["prompt"]) + 200)

            ms = task.merge_status({**meta, "status": "beta", "pr": 42, "pr_url": "https://example.com/pr/42", "merged_into": ["v3-dev"]}, root)
            self.assertTrue(ms["in_beta"])
            self.assertEqual(ms["pr"], 42)
            self.assertEqual(ms["pr_url"], "https://example.com/pr/42")

    def test_claude_launch_command_builder(self):
        """Claude terminal launch command supports both prompt-file and --resume session modes."""
        cwd = Path("C:/repo/_worktrees/wt_010")
        bins = {"claude": "C:/bin/claude.exe", "wt": "C:/bin/wt.exe"}
        cmd_sess = task_viewer_server.build_claude_launch_cmd(cwd, session_id="sess-123", launchers=bins)
        self.assertIn("--resume", " ".join(cmd_sess))
        self.assertIn("sess-123", " ".join(cmd_sess))
        cmd_prompt = task_viewer_server.build_claude_launch_cmd(cwd, prompt_file=Path("C:/tmp/p.md"), launchers=bins)
        self.assertIn("p.md", " ".join(cmd_prompt))


if __name__ == "__main__":
    unittest.main()
