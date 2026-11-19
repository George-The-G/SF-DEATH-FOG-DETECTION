"""Automated repository hygiene tests (enforces docs/AGENT_ERGONOMICS_PLAN.md).

Ensures newly added files, functions, and data folders stay indexable by
`tools/codemap.py` and follow the self-contained layout without locking the
codebase into fixed module names.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from microclimate.paths import REPO_ROOT, data_dir
from tools import codemap, task as task_cli

ALLOWED_ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "config.toml",
    "microclimate.toml",
    "pyproject.toml",
    "requirements-mc.txt",
    "routes.toml",
    "routes_drawn.json",
    "run.py",
}

KNOWN_DATA_SUBDIRS = {
    "grids",
    "library",
    "live",
    "raw",
    "refined",
    "scratch",
    "sky_inputs",
    "spikes",
    "backup",
}


class TestRepoHygiene(unittest.TestCase):
    def test_no_root_clutter(self):
        """Root directory must only contain core entry points and configs; docs go in docs/, experiments in spikes/."""
        root_files = {p.name for p in REPO_ROOT.iterdir() if p.is_file()}
        unexpected = root_files - ALLOWED_ROOT_FILES
        self.assertEqual(
            unexpected,
            set(),
            f"Unexpected files at repo root: {sorted(unexpected)}. Put docs in docs/, scripts in tools/ or spikes/.",
        )

    def test_large_files_have_section_markers(self):
        """Any Python or JS source file over 350 lines must have at least one `@sec:` or `---` section marker for codemap.py."""
        missing = []
        for p in codemap.iter_source_files():
            info = codemap.parse_python(p) if p.suffix == ".py" else codemap.parse_js(p)
            if info["lines"] > 350 and not info["sections"]:
                missing.append(f"{p.relative_to(REPO_ROOT).as_posix()} ({info['lines']}L)")
        self.assertEqual(
            missing,
            [],
            f"Files > 350 lines missing `# @sec:` or `// @sec:` section markers: {missing}",
        )

    def test_no_hardcoded_legacy_data_paths(self):
        """Production modules must use microclimate.paths.data_dir(), never Path.home() / 'deathfog-data'."""
        offenders = []
        for folder in ("microclimate", "deathfog", "site/v3/tools", "tools"):
            base = REPO_ROOT / folder
            if not base.exists():
                continue
            for p in base.rglob("*.py"):
                if p.name == "paths.py":
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                if 'Path.home() / "deathfog-data"' in text or "C:\\Users\\George\\deathfog-data" in text or "C:/Users/George/deathfog-data" in text:
                    offenders.append(p.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(
            offenders,
            [],
            f"Hardcoded legacy deathfog-data path found in {offenders}; use microclimate.paths.data_dir() instead.",
        )

    def test_data_folders_documented_in_manifest(self):
        """Any top-level folder created in data/ must be documented in data/MANIFEST.md (experiment outputs go in data/spikes/<topic>/)."""
        dd = data_dir()
        manifest = REPO_ROOT / "data" / "MANIFEST.md"
        if not dd.exists() or not manifest.exists():
            self.skipTest("data/ directory not present in this environment")
        manifest_text = manifest.read_text(encoding="utf-8")
        try:
            subdirs = [p.name for p in dd.iterdir() if p.is_dir()]
        except OSError:
            self.skipTest("data/ directory not readable in sandbox")
        unlisted = [
            name for name in subdirs
            if name not in KNOWN_DATA_SUBDIRS and f"data/{name}" not in manifest_text
        ]
        self.assertEqual(
            unlisted,
            [],
            f"Unlisted top-level data/ folder(s) {unlisted}. Put experiment data in data/spikes/<topic>/ or document in data/MANIFEST.md.",
        )

    def test_no_large_binaries_or_npz_tracked_in_git(self):
        """No .npz file or > 5 MB binary may be tracked in git."""
        try:
            out = subprocess.check_output(["git", "ls-files"], cwd=REPO_ROOT, text=True)
        except Exception:
            self.skipTest("git ls-files unavailable")
        bad_npz = []
        too_large = []
        max_bytes = 5 * 1024 * 1024
        for rel in out.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            if rel.endswith(".npz"):
                bad_npz.append(rel)
            p = REPO_ROOT / rel
            if p.exists() and p.is_file() and p.stat().st_size > max_bytes:
                too_large.append(f"{rel} ({p.stat().st_size / 1e6:.1f} MB)")
        self.assertEqual(bad_npz, [], f".npz files must live in data/, never tracked in git: {bad_npz}")
        self.assertEqual(too_large, [], f"Tracked files exceeding 5 MB limit: {too_large}")

    def test_no_vendor_specific_skill_dirs(self):
        """Skills must live in the generic `.agents/skills/` directory, never vendor-specific `.claude/skills/`."""
        self.assertFalse(
            (REPO_ROOT / ".claude" / "skills").exists(),
            "Vendor-specific `.claude/skills/` directory is forbidden; keep all skills in `.agents/skills/`.",
        )

    def test_task_packets_have_valid_frontmatter(self):
        """Every `tasks/<id>-<slug>/TASK.md` must have valid YAML frontmatter matching tools/task.py schema."""
        tdir = REPO_ROOT / "tasks"
        if not tdir.exists():
            return
        errors = []
        for child in sorted(tdir.iterdir()):
            if not child.is_dir():
                continue
            tfile = child / "TASK.md"
            if not tfile.is_file():
                errors.append(f"{child.name}: missing TASK.md")
                continue
            try:
                meta, _ = task_cli.parse_task_md(tfile)
                errors.extend(task_cli.validate_task_meta(meta, tfile))
            except Exception as exc:
                errors.append(f"{tfile}: {exc}")
        self.assertEqual(errors, [], f"Invalid task packet(s): {errors}")


if __name__ == "__main__":
    unittest.main()
