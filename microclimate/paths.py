"""Central data root resolution for microclimate simulation and live tools.

Under the self-contained layout (Option 1), all simulation data lives inside
`<repo_root>/data/` by default. Set `DEATHFOG_DATA` to override.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Return the root data directory (`DEATHFOG_DATA` or `<repo_root>/data`)."""
    env = os.environ.get("DEATHFOG_DATA")
    if env:
        return Path(env).expanduser()
    repo_data = REPO_ROOT / "data"
    try:
        if repo_data.exists() and any(
            (repo_data / sub).exists() for sub in ("library", "live", "grids", "raw")
        ):
            return repo_data
    except OSError:
        pass
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
