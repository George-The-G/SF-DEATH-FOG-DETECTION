import tomllib
from pathlib import Path


def load(path="microclimate.toml"):
    with open(Path(path), "rb") as f:
        return tomllib.load(f)
