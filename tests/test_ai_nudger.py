"""Unit and benchmark tests for the Neural Geometric Nudger."""

from __future__ import annotations

import copy
import time
import unittest
import numpy as np

from ai.features import extract_element_features, is_background_element
from ai.infer import apply_geometric_nudges, get_model
from ai.model import GeometricNudgerModel


class TestNeuralGeometricNudger(unittest.TestCase):
    def setUp(self) -> None:
        self.canvas_w = 1080.0
        self.canvas_h = 1080.0
        self.sample_elements = [
            {
                "id": "bg",
                "type": "image",
                "x": 0.0,
                "y": 0.0,
                "width": 1080.0,
                "height": 1080.0,
            },
            {
                "id": "headline",
                "type": "text",
                "content": "Chase the Light",
                "role": "headline",
                "x": 100.0,
                "y": 200.0,
                "width": 880.0,
                "height": 120.0,
                "font_size": 48,
            },
            {
                "id": "copy",
                "type": "text",
                "content": "Crafted for golden hour.",
                "role": "subhead",
                "x": 100.0,
                "y": 330.0,
                "width": 880.0,
                "height": 60.0,
                "font_size": 24,
            },
            {
                "id": "cta_btn",
                "type": "shape",
                "shape_type": "rectangle",
                "role": "cta",
                "x": 390.0,
                "y": 400.0,  # only 10px below copy - very tight breathing room!
                "width": 300.0,
                "height": 70.0,
            },
        ]

    def test_feature_extraction(self) -> None:
        features, active = extract_element_features(
            self.sample_elements, self.canvas_w, self.canvas_h
        )
        # Background should be filtered out
        self.assertEqual(len(active), 3)
        self.assertEqual(features.shape, (3, 24))
        # All feature values must be finite numbers
        self.assertTrue(np.all(np.isfinite(features)))

    def test_model_forward_and_latency(self) -> None:
        model = GeometricNudgerModel(input_dim=24, hidden_dim=64, output_dim=2)
        dummy_x = np.random.randn(6, 24).astype(np.float32)

        # Warm up
        _ = model.predict(dummy_x)

        # Benchmark 100 inferences
        start = time.perf_counter()
        for _ in range(100):
            preds = model.predict(dummy_x)
        elapsed_ms = (time.perf_counter() - start) * 10.0  # ms per inference

        self.assertEqual(preds.shape, (6, 2))
        self.assertLess(elapsed_ms, 5.0, f"Inference took {elapsed_ms:.2f} ms; expected < 5 ms")

    def test_nudger_preserves_hard_constraints(self) -> None:
        elements = copy.deepcopy(self.sample_elements)
        # Apply nudges
        nudged = apply_geometric_nudges(elements, self.canvas_w, self.canvas_h)

        self.assertEqual(len(nudged), len(self.sample_elements))
        for el in nudged:
            x = float(el["x"])
            y = float(el["y"])
            w = float(el["width"])
            h = float(el["height"])
            self.assertGreaterEqual(x, 0.0)
            self.assertGreaterEqual(y, 0.0)
            self.assertLessEqual(x + w, self.canvas_w + 1e-2)
            self.assertLessEqual(y + h, self.canvas_h + 1e-2)

    def test_graceful_fallback_on_missing_weights(self) -> None:
        elements = copy.deepcopy(self.sample_elements)
        # Pass non-existent path
        result = apply_geometric_nudges(
            elements,
            self.canvas_w,
            self.canvas_h,
            weights_path="/tmp/non_existent_weights.npz",
        )
        # Should return elements intact without raising
        self.assertEqual(len(result), len(elements))
        self.assertEqual(result[1]["y"], elements[1]["y"])


if __name__ == "__main__":
    unittest.main()
