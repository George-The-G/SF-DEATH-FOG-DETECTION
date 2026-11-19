"""Unit tests for the self-contained task lifecycle & DAG wave scheduler (`tools/task.py`)."""
from __future__ import annotations

import argparse
import json
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
        self.assertIn("main_parity", state)
        self.assertIsInstance(state["tasks"], dict)
        if state["tasks"]:
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

    def test_shared_main_paths_and_no_raw_conversation_href(self):
        """Shared main sync manifest covers agent/tooling files and Task Viewer avoids broken Windows conversation:// hrefs."""
        for required in ("AGENTS.md", "tools/task.py", "tools/task_viewer", "tests/test_task_workflow.py"):
            self.assertIn(required, task.SHARED_MAIN_PATHS)
        html = (Path(task_viewer_server.VIEWER_DIR) / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('href="conversation://', html)
        self.assertNotIn("sendConvoLinkToChat", html)

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
                r_blocked = task.approve_task_gate("099")
                self.assertFalse(r_blocked["ok"])
                (tdir / "PLAN.md").write_text(
                    "# Plan — Task 099\n\n## Brainstorm — Goals, Decisions & Dead Ends\n- **Goals:** Test gating\n\n"
                    "## Plan — Architecture, Owned Paths & Verification\n- **Affected Symbols:** `tools/task.py:cmd_start`\n"
                    "- **Implementation Steps:**\n  1. Enforce formal plan gate and verify SHA.\n",
                    encoding="utf-8",
                )
                r2 = task.approve_task_gate("099", approve_plan=True, approved_by="human-cli")
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

    def _make_sample_task(self, root: Path, tid: str = "097", *, status: str = "plan", owns: list[str] | None = None, fill_plan: bool = False):
        """Create a temporary task packet for formal plan approval gate tests."""
        owns_list = ["tools/task.py"] if owns is None else owns
        meta = {
            "id": tid,
            "slug": "plan-gate-test",
            "title": "Plan Gate Test",
            "issue": None,
            "epic": None,
            "pr": None,
            "pr_url": "",
            "status": status,
            "plan_approved": False,
            "plan_approved_at": "",
            "plan_approved_by": "",
            "plan_approved_sha": "",
            "branch": f"feat/{tid}-plan-gate-test",
            "switch": "plan_gate_test",
            "depends_on": [],
            "owned_paths": owns_list,
            "gpu_required": False,
            "deliverables": [f"tasks/{tid}-plan-gate-test/PLAN.md"],
        }
        tdir = task.scaffold_task_dir(root, meta, problem="Verify formal plan approval gate.")
        if fill_plan:
            (tdir / "PLAN.md").write_text(
                f"# Plan — Task {tid}: Plan Gate Test\n\n"
                "## Brainstorm — Goals, Decisions & Dead Ends\n- **Goals:** Enforce plan gate\n\n"
                "## Plan — Architecture, Owned Paths & Verification\n"
                "- **Affected Symbols:** `tools/task.py:approve_task_gate`\n"
                "- **Implementation Steps:**\n  1. Require explicit human approval and SHA check.\n",
                encoding="utf-8",
            )
        return meta, tdir

    def test_new_rejects_pre_approved_statuses(self):
        """cmd_new rejects --status ready or in-progress so tasks cannot bypass plan gates."""
        for bad_status in ("ready", "in-progress"):
            ns = argparse.Namespace(
                title="Bypass Attempt",
                slug="bypass-attempt",
                epic=None,
                depends="",
                owns="tools/task.py",
                switch="",
                gpu=False,
                problem="Attempt",
                status=bad_status,
                tier="",
                quick=False,
                local=True,
            )
            self.assertEqual(task.cmd_new(ns), 1)

    def test_plan_readiness_rejects_empty_or_template_plan(self):
        """check_plan_readiness fails on untouched scaffold PLAN.md or empty owned_paths, and passes once populated."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            meta, tdir = self._make_sample_task(root, "096", owns=["tools/task.py"], fill_plan=False)
            rd_template = task.check_plan_readiness(tdir, meta)
            self.assertFalse(rd_template["ready"])

            meta_no_owns = {**meta, "owned_paths": []}
            (tdir / "PLAN.md").write_text(
                "# Plan — Task 096\n\n## Plan — Architecture, Owned Paths & Verification\n"
                "- **Affected Symbols:** `tools/task.py:cmd_start`\n"
                "- **Implementation Steps:**\n  1. Add readiness check and drift detection.\n",
                encoding="utf-8",
            )
            rd_no_owns = task.check_plan_readiness(tdir, meta_no_owns)
            self.assertFalse(rd_no_owns["ready"])

            rd_ready = task.check_plan_readiness(tdir, meta)
            self.assertTrue(rd_ready["ready"])

    def test_approve_plan_requires_readiness_and_explicit_flag(self):
        """plan -> ready requires readiness and explicit approve_plan=True, stamping approval metadata."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, tdir = self._make_sample_task(root, "095", status="plan", fill_plan=False)
            tfile = tdir / "TASK.md"

            def _find_live(_tid):
                m, _ = task.parse_task_md(tfile)
                return m, tfile, root

            with mock.patch.object(task, "find_task", side_effect=_find_live):
                r_no_flag = task.approve_task_gate("095")
                self.assertFalse(r_no_flag["ok"])

                r_unready = task.approve_task_gate("095", approve_plan=True)
                self.assertFalse(r_unready["ok"])

                (tdir / "PLAN.md").write_text(
                    "# Plan — Task 095\n\n## Plan — Architecture, Owned Paths & Verification\n"
                    "- **Affected Symbols:** `tools/task.py:cmd_approve`\n"
                    "- **Implementation Steps:**\n  1. Stamp plan_approved_sha on approval.\n",
                    encoding="utf-8",
                )
                r_ok = task.approve_task_gate("095", approve_plan=True, approved_by="human-cli")
                self.assertTrue(r_ok["ok"])
                self.assertEqual(r_ok["status"], "ready")
                saved_meta, _ = task.parse_task_md(tfile)
                self.assertTrue(saved_meta["plan_approved"])
                self.assertEqual(saved_meta["plan_approved_by"], "human-cli")
                self.assertTrue(saved_meta["plan_approved_sha"])
                self.assertTrue(saved_meta["plan_approved_at"])

    def test_start_blocks_unapproved_and_drifted_even_with_force(self):
        """cmd_start(..., force=True) blocks if plan_approved is false or PLAN.md drifted after approval."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, tdir = self._make_sample_task(root, "094", status="ready", fill_plan=True)
            tfile = tdir / "TASK.md"

            def _find_live(_tid):
                m, _ = task.parse_task_md(tfile)
                return m, tfile, root

            with mock.patch.object(task, "find_task", side_effect=_find_live), \
                 mock.patch.object(task, "WORKTREES_DIR", root / "_worktrees"):
                rc_unapproved = task.cmd_start(argparse.Namespace(id="094", base="v3-dev", force=True))
                self.assertEqual(rc_unapproved, 1)

                r_ok = task.approve_task_gate("094", target_status="ready", approve_plan=True, approved_by="human-cli")
                self.assertTrue(r_ok["ok"])

                # Simulate PLAN.md drift after approval
                (tdir / "PLAN.md").write_text(
                    (tdir / "PLAN.md").read_text(encoding="utf-8") + "\n- Post-approval edit\n",
                    encoding="utf-8",
                )
                rc_drifted = task.cmd_start(argparse.Namespace(id="094", base="v3-dev", force=True))
                self.assertEqual(rc_drifted, 1)

    def test_verify_fails_when_plan_unapproved(self):
        """cmd_verify fails if plan_approved is explicitly False while remaining compatible with legacy tasks."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            meta, tdir = self._make_sample_task(root, "093", status="in-progress", fill_plan=True)
            tfile = tdir / "TASK.md"
            fake_ok = argparse.Namespace(returncode=0, stdout="ok", stderr="")
            with mock.patch.object(task, "find_task", return_value=(meta, tfile, root)), \
                 mock.patch.object(task, "_check_scope", return_value=[]), \
                 mock.patch.object(task, "_run", return_value=fake_ok):
                rc = task.cmd_verify(argparse.Namespace(id="093", base="v3-dev", skip_preview=True, allow_extra=False))
                self.assertEqual(rc, 1)
                report = json.loads((tdir / "evidence" / "verify.json").read_text(encoding="utf-8"))
                self.assertFalse(report["checks"]["plan_approval"]["ok"])

                # Legacy task without plan_approved key passes plan_approval check
                legacy_meta = {k: v for k, v in meta.items() if k != "plan_approved"}
                with mock.patch.object(task, "find_task", return_value=(legacy_meta, tfile, root)):
                    rc_leg = task.cmd_verify(argparse.Namespace(id="093", base="v3-dev", skip_preview=True, allow_extra=False))
                    self.assertEqual(rc_leg, 0)

    def test_viewer_state_exposes_plan_readiness_and_drift(self):
        """build_state() exposes plan_readiness, plan_approved, and plan_drifted on every task."""
        state = task_viewer_server.build_state()
        self.assertTrue(state["tasks"])
        sample = next(iter(state["tasks"].values()))
        self.assertIn("plan_readiness", sample)
        self.assertIn("plan_approved", sample)
        self.assertIn("plan_drifted", sample)
        self.assertIn("checks", sample["plan_readiness"])

    def test_convo_preview_reads_local_brain_artifacts_safely(self):
        """_load_convo_preview reads local brain artifacts/turns, rejects traversal, and handles missing IDs."""
        bad = task_viewer_server._load_convo_preview("../etc/passwd")
        self.assertFalse(bad["ok"])
        with tempfile.TemporaryDirectory() as td:
            brain_root = Path(td)
            missing = task_viewer_server._load_convo_preview("abcd-1234", brain_root=brain_root)
            self.assertTrue(missing["ok"])
            self.assertFalse(missing["exists"])

            cdir = brain_root / "conv-012-test"
            logs = cdir / ".system_generated" / "logs"
            logs.mkdir(parents=True)
            (cdir / "implementation_plan.md").write_text("# Plan\n\nDetails here.", encoding="utf-8")
            (logs / "transcript.jsonl").write_text(
                json.dumps({"type": "USER_INPUT", "content": "Review the plan", "created_at": "2026-11-19T10:00:00Z"}) + "\n"
                + json.dumps({"type": "PLANNER_RESPONSE", "content": "Done!", "created_at": "2026-11-19T10:01:00Z"}) + "\n",
                encoding="utf-8",
            )
            res = task_viewer_server._load_convo_preview("conv-012-test", brain_root=brain_root)
            self.assertTrue(res["ok"])
            self.assertTrue(res["exists"])
            self.assertEqual(len(res["artifacts"]), 1)
            self.assertEqual(res["artifacts"][0]["name"], "implementation_plan.md")
            self.assertEqual(len(res["recent_turns"]), 2)
            self.assertEqual(res["recent_turns"][0]["role"], "User")

    def test_viewer_approve_plan_action(self):
        """run_action({'action': 'approve_plan', ...}) stamps human-ui and rejects unready plans."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            meta, tdir = self._make_sample_task(root, "092", status="plan", fill_plan=False)
            tfile = tdir / "TASK.md"

            def _find_live(_tid):
                m, _ = task.parse_task_md(tfile)
                return m, tfile, root

            fake_state = {
                "tasks": {
                    "092": {
                        **meta,
                        "task_dir": str(tdir),
                        "worktree_path": "",
                    }
                }
            }
            with mock.patch.object(task_viewer_server, "build_state", return_value=fake_state), \
                 mock.patch.object(task, "find_task", side_effect=_find_live):
                r_fail = task_viewer_server.run_action({"action": "approve_plan", "task_id": "092"})
                self.assertFalse(r_fail["ok"])

                (tdir / "PLAN.md").write_text(
                    "# Plan — Task 092\n\n## Plan — Architecture, Owned Paths & Verification\n"
                    "- **Affected Symbols:** `tools/task_viewer/server.py:run_action`\n"
                    "- **Implementation Steps:**\n  1. Add approve_plan action.\n",
                    encoding="utf-8",
                )
                r_ok = task_viewer_server.run_action({"action": "approve_plan", "task_id": "092"})
                self.assertTrue(r_ok["ok"])
                saved, _ = task.parse_task_md(tfile)
                self.assertTrue(saved["plan_approved"])
                self.assertEqual(saved["plan_approved_by"], "human-ui")

    def test_viewer_html_unified_sequence_and_no_broken_convo_jump(self):
        """Task Viewer HTML uses unified 7-node sequence ending in Main and removes broken convo jump/duplicate bars."""
        html = (Path(task_viewer_server.VIEWER_DIR) / "index.html").read_text(encoding="utf-8")
        self.assertIn("Main", html)
        self.assertNotIn("sendConvoLinkToChat", html)
        self.assertNotIn('href="conversation://', html)
        self.assertNotIn("renderMergeBar", html)
        self.assertIn("LAST_STATE_JSON", html)
        self.assertIn("preview_conversation", html)
        self.assertIn("Token Usage Breakdown", html)

    def test_collect_token_usage_aggregates_local_transcripts(self):
        """collect_token_usage sums PLANNER_RESPONSE tokens per model/convo, rejects traversal, and skips missing logs."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, tdir = self._make_sample_task(root, "091", status="in-progress", fill_plan=True)
            brain_root = root / "brain"

            task.link_conversation(tdir, "conv-neon-091", tool="antigravity", label="Neon planner")
            task.link_conversation(tdir, "conv-hydro-091", tool="antigravity", label="Hydrogen worker")
            task.link_conversation(tdir, "conv-missing-091", tool="antigravity", label="Missing log")
            task.link_conversation(tdir, "claude-sess-091", tool="claude", label="Claude session")
            # Inject an unsafe path-traversal ID directly into conversations.json
            cpath = tdir / "conversations.json"
            raw_conv = json.loads(cpath.read_text(encoding="utf-8"))
            raw_conv["items"].append({"id": "../evil-escape", "tool": "antigravity", "label": "Bad"})
            cpath.write_text(json.dumps(raw_conv), encoding="utf-8")

            neon_logs = brain_root / "conv-neon-091" / ".system_generated" / "logs"
            neon_logs.mkdir(parents=True)
            (neon_logs / "transcript.jsonl").write_text(
                "\n".join([
                    json.dumps({"type": "USER_INPUT", "content": "Scope it", "input_tokens": 99999}),
                    "{malformed json line",
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "model": "gemini-4-neon-medium",
                        "input_tokens": 1200,
                        "cache_read_tokens": 800,
                        "output_tokens": 150,
                    }),
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "model": "gemini-4-neon-medium",
                        "input_tokens": 1800,
                        "cache_read_tokens": 1200,
                        "output_tokens": 250,
                    }),
                ]) + "\n",
                encoding="utf-8",
            )

            hydro_logs = brain_root / "conv-hydro-091" / ".system_generated" / "logs"
            hydro_logs.mkdir(parents=True)
            (hydro_logs / "transcript.jsonl").write_text(
                json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "model": "gemini-4-hydrogen-medium",
                    "input_tokens": 5000,
                    "cache_read_tokens": 3000,
                    "output_tokens": 600,
                }) + "\n",
                encoding="utf-8",
            )

            usage = task.collect_token_usage(tdir, brain_root=brain_root)
            self.assertEqual(usage["convo_count"], 2)
            self.assertEqual(usage["totals"], {
                "input_tokens": 8000,
                "cache_read_tokens": 5000,
                "output_tokens": 1000,
                "turns": 3,
            })
            self.assertEqual(usage["by_model"]["gemini-4-neon-medium"], {
                "input_tokens": 3000,
                "cache_read_tokens": 2000,
                "output_tokens": 400,
                "turns": 2,
            })
            self.assertEqual(usage["by_model"]["gemini-4-hydrogen-medium"], {
                "input_tokens": 5000,
                "cache_read_tokens": 3000,
                "output_tokens": 600,
                "turns": 1,
            })
            self.assertEqual(set(usage["conversations"].keys()), {"conv-neon-091", "conv-hydro-091"})
            self.assertIn("in:8.0k", usage["summary"])
            self.assertIn("(2 convos)", usage["summary"])

            # Verify dump_task_md / parse_task_md round-trips token_usage cleanly
            tfile = tdir / "TASK.md"
            meta, body = task.parse_task_md(tfile)
            meta["token_usage"] = usage["summary"]
            tfile.write_text(task.dump_task_md(meta, body), encoding="utf-8")
            reloaded, _ = task.parse_task_md(tfile)
            self.assertEqual(reloaded["token_usage"], usage["summary"])

            # Verify cmd_finish writes evidence/usage.json and populates meta["token_usage"]
            (tdir / "evidence" / "verify.json").write_text(
                json.dumps({"ok": True, "git_sha": "abc123def456"}),
                encoding="utf-8",
            )
            fake_head = argparse.Namespace(returncode=0, stdout="abc123def456\n", stderr="")
            with mock.patch.object(task, "find_task", return_value=(meta, tfile, root)), \
                 mock.patch.object(task, "_run", return_value=fake_head), \
                 mock.patch.object(task, "collect_token_usage", return_value=usage):
                rc = task.cmd_finish(argparse.Namespace(
                    id="091", merge=False, allow_stale=False, deploy_beta=False, sync_main=False
                ))
            self.assertEqual(rc, 0)
            self.assertTrue((tdir / "evidence" / "usage.json").is_file())
            finished_meta, _ = task.parse_task_md(tfile)
            self.assertEqual(finished_meta["token_usage"], usage["summary"])

    def test_viewer_state_exposes_usage_json(self):
        """build_state() exposes evidence/usage.json as `usage` and populates `token_usage`."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            meta, tdir = self._make_sample_task(root, "090", status="beta", fill_plan=True)
            tfile = tdir / "TASK.md"
            usage_payload = {
                "convo_count": 1,
                "summary": "in:12.0k cache:8.0k out:1.5k (1 convo)",
                "totals": {"input_tokens": 12000, "cache_read_tokens": 8000, "output_tokens": 1500, "turns": 4},
                "by_model": {
                    "gemini-4-hydrogen-medium": {
                        "input_tokens": 12000,
                        "cache_read_tokens": 8000,
                        "output_tokens": 1500,
                        "turns": 4,
                    }
                },
                "conversations": {},
            }
            (tdir / "evidence" / "usage.json").write_text(json.dumps(usage_payload), encoding="utf-8")

            with mock.patch.object(task, "MAIN_ROOT", root), \
                 mock.patch.object(task, "load_all_tasks", return_value={"090": (meta, tfile)}), \
                 mock.patch.object(task, "active_worktree_tasks", return_value={}), \
                 mock.patch.object(task, "shared_main_drift", return_value={"ok": True, "drifted": []}), \
                 mock.patch.object(task, "merge_status", return_value={}), \
                 mock.patch.object(task_viewer_server, "detect_launchers", return_value={}):
                state = task_viewer_server.build_state()

            self.assertIn("090", state["tasks"])
            task_state = state["tasks"]["090"]
            self.assertEqual(task_state["usage"], usage_payload)
            self.assertEqual(task_state["token_usage"], usage_payload["summary"])


if __name__ == "__main__":
    unittest.main()
