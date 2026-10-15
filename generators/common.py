"""Shared helpers for the Vitalis synthetic-data generators."""
from __future__ import annotations

import zlib
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config(path: str | Path | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_rng(seed: int, stream: str) -> np.random.Generator:
    """Independent, reproducible RNG per named stream.

    Using separate streams means changing one generator never shifts the
    random numbers consumed by another.
    """
    return np.random.default_rng([int(seed), zlib.crc32(stream.encode("utf-8"))])
