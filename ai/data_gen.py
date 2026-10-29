"""Synthetic Dataset Generation for Geometric Layout Nudger.

Generates training samples consisting of layout configurations with synthetic
spatial perturbations, paired with optimal target residual adjustments that
resolve collisions, restore breathing room margins, and preserve visual balance.
"""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np

from ai.features import extract_element_features, is_background_element
from tests.fuzz_designs import random_design



def compute_layout_penalty(
    elements: List[Dict[str, Any]],
    offsets: np.ndarray,
    W: float,
    H: float,
    desired_gap: float = 24.0,
    edge_margin: float = 16.0,
) -> float:
    """
    Differentiable/continuous penalty score for candidate offsets:
    offsets: (N, 2) normalized dx, dy in [-max_nudge, +max_nudge]
    Lower is better.
    """
    n = len(elements)
    if n == 0:
        return 0.0

    boxes = []
    for i, el in enumerate(elements):
        w = max(1.0, float(el.get("width", 1.0)))
        h = max(1.0, float(el.get("height", 1.0)))
        dx = float(offsets[i, 0]) * W
        dy = float(offsets[i, 1]) * H
        x = float(el.get("x", 0.0)) + dx
        y = float(el.get("y", 0.0)) + dy
        boxes.append((x, y, w, h))

    total_penalty = 0.0

    # 1. Edge boundary violations
    for x, y, w, h in boxes:
        # Off-canvas overflow
        if x < 0:
            total_penalty += 500.0 * (-x / W) ** 2
        if y < 0:
            total_penalty += 500.0 * (-y / H) ** 2
        if x + w > W:
            total_penalty += 500.0 * ((x + w - W) / W) ** 2
        if y + h > H:
            total_penalty += 500.0 * ((y + h - H) / H) ** 2

        # Breathing margin to edges
        if x < edge_margin:
            total_penalty += 20.0 * ((edge_margin - x) / edge_margin) ** 2
        if y < edge_margin:
            total_penalty += 20.0 * ((edge_margin - y) / edge_margin) ** 2
        if W - (x + w) < edge_margin:
            total_penalty += 20.0 * ((edge_margin - (W - (x + w))) / edge_margin) ** 2
        if H - (y + h) < edge_margin:
            total_penalty += 20.0 * ((edge_margin - (H - (y + h))) / edge_margin) ** 2

    # 2. Pairwise overlaps and tight spacing
    for i in range(n):
        xi, yi, wi, hi = boxes[i]
        for j in range(i + 1, n):
            xj, yj, wj, hj = boxes[j]

            # Intersection
            ix0 = max(xi, xj)
            iy0 = max(yi, yj)
            ix1 = min(xi + wi, xj + wj)
            iy1 = min(yi + hi, yj + hj)
            iw = max(0.0, ix1 - ix0)
            ih = max(0.0, iy1 - iy0)
            overlap_area = iw * ih

            if overlap_area > 0:
                # Heavy penalty for overlap
                total_penalty += 800.0 * (overlap_area / (wi * hi + 1e-4))
            else:
                # Breathing margin penalty if elements are horizontally or vertically aligned
                x_overlap = max(0.0, min(xi + wi, xj + wj) - max(xi, xj))
                if x_overlap > min(wi, wj) * 0.3:
                    # Vertical separation
                    if yj >= yi + hi:
                        gap = yj - (yi + hi)
                        if gap < desired_gap:
                            total_penalty += 35.0 * ((desired_gap - gap) / desired_gap) ** 2
                    elif yi >= yj + hj:
                        gap = yi - (yj + hj)
                        if gap < desired_gap:
                            total_penalty += 35.0 * ((desired_gap - gap) / desired_gap) ** 2

                y_overlap = max(0.0, min(yi + hi, yj + hj) - max(yi, yj))
                if y_overlap > min(hi, hj) * 0.3:
                    # Horizontal separation
                    if xj >= xi + wi:
                        gap = xj - (xi + wi)
                        if gap < desired_gap:
                            total_penalty += 35.0 * ((desired_gap - gap) / desired_gap) ** 2
                    elif xi >= xj + wj:
                        gap = xi - (xj + wj)
                        if gap < desired_gap:
                            total_penalty += 35.0 * ((desired_gap - gap) / desired_gap) ** 2

    # 3. Text optical centering penalty: gently guide text elements to optical center
    text_boxes = []
    for i, el in enumerate(elements):
        el_type = el.get("type", "")
        role = str(el.get("role", "")).lower()
        if el_type == "text" or "headline" in role or "subhead" in role or "cta" in role:
            x, y, w, h = boxes[i]
            align = el.get("text_align", "center")
            if align == "center" or "headline" in role or "subhead" in role:
                cx = x + w / 2.0
                total_penalty += 35.0 * ((cx - W / 2.0) / W) ** 2
            text_boxes.append((x, x + w))

    if text_boxes:
        overall_min_x = min(b[0] for b in text_boxes)
        overall_max_x = max(b[1] for b in text_boxes)
        overall_text_cx = (overall_min_x + overall_max_x) / 2.0
        if abs(overall_text_cx - W / 2.0) < 0.25 * W:
            total_penalty += 30.0 * ((overall_text_cx - W / 2.0) / W) ** 2

    # 4. Drift penalty: prefer small nudges over large displacements
    drift = np.sum(offsets ** 2) * 15.0
    total_penalty += drift

    return float(total_penalty)


def optimize_offsets_for_layout(
    elements: List[Dict[str, Any]],
    W: float,
    H: float,
    max_nudge: float = 0.04,
    iterations: int = 40,
) -> np.ndarray:
    """
    Find optimal offsets for elements via coordinate search / local gradient descent.
    Returns (N, 2) in normalized units [-max_nudge, max_nudge].
    """
    n = len(elements)
    if n == 0:
        return np.zeros((0, 2), dtype=np.float32)

    offsets = np.zeros((n, 2), dtype=np.float32)
    best_penalty = compute_layout_penalty(elements, offsets, W, H)

    # Multi-resolution step sizes
    step_sizes = [0.02, 0.01, 0.005, 0.002]
    for step in step_sizes:
        for _ in range(iterations // len(step_sizes)):
            improved = False
            for i in range(n):
                for axis in [0, 1]:  # dx, dy
                    for direction in [-1.0, 1.0]:
                        test_offsets = offsets.copy()
                        test_offsets[i, axis] = np.clip(
                            test_offsets[i, axis] + direction * step,
                            -max_nudge,
                            max_nudge,
                        )
                        pen = compute_layout_penalty(elements, test_offsets, W, H)
                        if pen < best_penalty - 1e-4:
                            best_penalty = pen
                            offsets = test_offsets
                            improved = True
            if not improved:
                break

    return offsets


def generate_synthetic_dataset(
    num_samples: int = 1500,
    seed: int = 42,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generate synthetic dataset of (X_features, Y_target_offsets).
    X shape: (TotalElements, 24)
    Y shape: (TotalElements, 2)
    """
    rng = random.Random(seed)
    np_rng = np.random.RandomState(seed)

    all_X: List[np.ndarray] = []
    all_Y: List[np.ndarray] = []

    canvases = [
        (1080, 1080), (1200, 628), (1080, 1920), (728, 90),
        (300, 250), (160, 600), (970, 250), (1280, 720),
    ]

    for idx in range(num_samples):
        # Generate random design
        design = random_design(seed=idx)
        cw = int(design["canvas"]["width"])
        ch = int(design["canvas"]["height"])
        W, H = float(cw), float(ch)

        elements = [copy.deepcopy(el) for el in design.get("elements", [])]
        active = [el for el in elements if not is_background_element(el, W, H)]
        if len(active) < 2:
            continue

        # Add synthetic perturbations (simulate tight spacing or slight overlaps)
        for el in active:
            perturb_x = (np_rng.rand() - 0.5) * 0.03 * W
            perturb_y = (np_rng.rand() - 0.5) * 0.03 * H
            el["x"] = float(el.get("x", 0.0)) + perturb_x
            el["y"] = float(el.get("y", 0.0)) + perturb_y

        # Extract features for current (perturbed) layout
        feats, active_els = extract_element_features(active, W, H)
        if len(active_els) == 0:
            continue

        # Compute optimal target offsets
        opt_offsets = optimize_offsets_for_layout(active_els, W, H)

        all_X.append(feats)
        all_Y.append(opt_offsets)

    X_mat = np.vstack(all_X)
    Y_mat = np.vstack(all_Y)
    return X_mat, Y_mat
