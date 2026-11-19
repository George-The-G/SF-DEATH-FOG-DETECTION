"""Unit tests for the self-contained task lifecycle & DAG wave scheduler (`tools/task.py`)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools import task


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


if __name__ == "__main__":
    unittest.main()
