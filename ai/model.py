"""Neural Geometric Adjustment Model.

A lightweight Multi-Layer Perceptron (MLP) residual nudger implemented
in pure NumPy for zero-overhead, sub-millisecond offline execution.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Tuple, Union
import numpy as np


class GeometricNudgerModel:
    """
    MLP Residual Nudger:
        Input: (N, 24) contextual layout features
        Hidden 1: (64) + ReLU
        Hidden 2: (64) + ReLU
        Output: (2) + Tanh * max_nudge_pct -> (dx_norm, dy_norm)
    """

    def __init__(
        self,
        input_dim: int = 24,
        hidden_dim: int = 64,
        output_dim: int = 2,
        max_nudge_pct: float = 0.04,  # max 4% canvas shift
        seed: int = 42,
    ) -> None:
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.max_nudge_pct = max_nudge_pct

        rng = np.random.RandomState(seed)
        # He (Kaiming) normal initialization
        self.W1 = (rng.randn(input_dim, hidden_dim) * np.sqrt(2.0 / input_dim)).astype(np.float32)
        self.b1 = np.zeros(hidden_dim, dtype=np.float32)

        self.W2 = (rng.randn(hidden_dim, hidden_dim) * np.sqrt(2.0 / hidden_dim)).astype(np.float32)
        self.b2 = np.zeros(hidden_dim, dtype=np.float32)

        # Output initialized small to start near-identity
        self.W3 = (rng.randn(hidden_dim, output_dim) * 0.01).astype(np.float32)
        self.b3 = np.zeros(output_dim, dtype=np.float32)

        # Feature normalization statistics
        self.mean = np.zeros(input_dim, dtype=np.float32)
        self.std = np.ones(input_dim, dtype=np.float32)

    def set_normalization(self, mean: np.ndarray, std: np.ndarray) -> None:
        self.mean = mean.astype(np.float32)
        self.std = np.where(std < 1e-6, 1.0, std).astype(np.float32)

    def forward(
        self, x: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Forward pass with cache for backpropagation.
        x: (N, input_dim)
        """
        # Standardize features
        x_norm = (x - self.mean) / self.std

        # Layer 1
        z1 = np.dot(x_norm, self.W1) + self.b1
        a1 = np.maximum(0.0, z1)  # ReLU

        # Layer 2
        z2 = np.dot(a1, self.W2) + self.b2
        a2 = np.maximum(0.0, z2)  # ReLU

        # Layer 3 (Output)
        z3 = np.dot(a2, self.W3) + self.b3
        # Tanh scaled by max_nudge_pct
        out = np.tanh(z3) * self.max_nudge_pct

        cache = (x_norm, z1, a1, z2, a2, z3)
        return out, cache

    def predict(self, x: np.ndarray) -> np.ndarray:
        """Inference forward pass returning predicted normalized offsets (N, 2)."""
        if x.shape[0] == 0:
            return np.zeros((0, self.output_dim), dtype=np.float32)
        out, _ = self.forward(x)
        return out

    def save(self, filepath: Union[str, Path]) -> None:
        """Save model weights and normalization stats to compressed .npz archive."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        np.savez_compressed(
            filepath,
            W1=self.W1,
            b1=self.b1,
            W2=self.W2,
            b2=self.b2,
            W3=self.W3,
            b3=self.b3,
            mean=self.mean,
            std=self.std,
            max_nudge_pct=np.array(self.max_nudge_pct, dtype=np.float32),
        )

    def load(self, filepath: Union[str, Path]) -> None:
        """Load model weights and normalization stats from .npz archive."""
        data = np.load(filepath)
        self.W1 = data["W1"]
        self.b1 = data["b1"]
        self.W2 = data["W2"]
        self.b2 = data["b2"]
        self.W3 = data["W3"]
        self.b3 = data["b3"]
        self.mean = data["mean"]
        self.std = data["std"]
        if "max_nudge_pct" in data:
            self.max_nudge_pct = float(data["max_nudge_pct"])
