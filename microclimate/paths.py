"""Central data root resolution for microclimate simulation and live tools.

Under the self-contained layout (Option 1), all simulation data lives inside
`<repo_root>/data/` by default. Set `DEATHFOG_DATA` to override.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _has_data_subdirs(candidate: Path) -> bool:
    """Return True if candidate looks like a populated Death Fog data directory."""
    try:
        return candidate.exists() and any(
            (candidate / sub).exists() for sub in ("library", "live", "grids", "raw")
        )
    except OSError:
        return False


def main_repo_root() -> Path:
    """Return the main repository root even when executing inside `_worktrees/wt_*`."""
    if REPO_ROOT.parent.name == "_worktrees":
        parent_root = REPO_ROOT.parent.parent
        if (parent_root / ".git").exists() or (parent_root / "AGENTS.md").exists():
            return parent_root
    return REPO_ROOT


def data_dir() -> Path:
    """Return the root data directory (`DEATHFOG_DATA`, `<repo_root>/data`, or parent repo `data/` from worktrees)."""
    env = os.environ.get("DEATHFOG_DATA")
    if env:
        return Path(env).expanduser()
    repo_data = REPO_ROOT / "data"
    if _has_data_subdirs(repo_data):
        return repo_data
    main_root = main_repo_root()
    if main_root != REPO_ROOT:
        parent_data = main_root / "data"
        if _has_data_subdirs(parent_data):
            return parent_data
    try:
        legacy = Path.home() / "deathfog-data"
        if legacy.exists():
            return legacy
    except OSError:
        pass
    return repo_data


def library_dir() -> Path:
    return data_dir() / "library"


def live_dir() -> Path:
    return data_dir() / "live"


def grids_dir() -> Path:
    return data_dir() / "grids"


def raw_dir() -> Path:
    return data_dir() / "raw"


def scratch_dir() -> Path:
    return data_dir() / "scratch"
