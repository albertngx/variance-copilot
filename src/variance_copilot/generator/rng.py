"""Seeded random streams, one per generator module (SPEC §8.1)."""

from __future__ import annotations

import hashlib

import numpy as np


def _module_key(module: str) -> int:
    return int.from_bytes(hashlib.sha256(module.encode()).digest()[:8], "big")


def module_rng(seed: int, module: str) -> np.random.Generator:
    """The child stream for ``module``; it depends only on the seed and the module name."""
    sequence = np.random.SeedSequence(entropy=seed, spawn_key=(_module_key(module),))
    return np.random.Generator(np.random.PCG64(sequence))
