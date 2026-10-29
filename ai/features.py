"""Feature extraction for the Neural Geometric Nudger.

Converts design elements and spatial layout context into normalized,
fixed-dimensional feature vectors suitable for MLP / neural inference.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

ROLE_NAMES = [
    "headline",
    "subhead",
    "cta",
    "logo",
    "hero",
    "badge",
    "legal",
    "other",
]
NUM_ROLES = len(ROLE_NAMES)
FEATURE_DIM = 4 + NUM_ROLES + 4 + 4 + 2 + 2  # 24 dimensions


def infer_role_index(el: Dict[str, Any], ad: Optional[Any] = None) -> int:
    """Infer the 0-indexed role for an element."""
    eid = el.get("id", "")
    el_type = el.get("type", "")

    if ad is not None:
        if getattr(ad, "headline", None) and ad.headline.get("id") == eid:
            return 0
        if getattr(ad, "subhead", None) and ad.subhead.get("id") == eid:
            return 1
        if getattr(ad, "cta_unit", None) and any(m.get("id") == eid for m in ad.cta_unit.elements):
            return 2
        if getattr(ad, "logo", None) and ad.logo.get("id") == eid:
            return 3
        if getattr(ad, "hero", None) and ad.hero.get("id") == eid:
            return 4
        if getattr(ad, "badge_rows", None):
            for row in ad.badge_rows:
                for u in row:
                    if any(m.get("id") == eid for m in u.elements):
                        return 5
        if getattr(ad, "legal", None) and any(l.get("id") == eid for l in ad.legal):
            return 6

    # Heuristic fallback if ad is not provided
    role_hint = str(el.get("role", "")).lower()
    content = str(el.get("content", "")).lower()

    if "headline" in role_hint or "title" in role_hint:
        return 0
    if "subhead" in role_hint or "description" in role_hint or "body" in role_hint:
        return 1
    if "cta" in role_hint or "button" in role_hint or "shop" in content or "buy" in content:
        return 2
    if "logo" in role_hint or "brand" in role_hint:
        return 3
    if "hero" in role_hint or (el_type == "image" and float(el.get("width", 0)) > 200):
        return 4
    if "badge" in role_hint:
        return 5
    if "legal" in role_hint or "terms" in content or "rights reserved" in content:
        return 6
    return 7


def is_background_element(el: Dict[str, Any], W: float, H: float) -> bool:
    """Check if an element is a full-bleed canvas background."""
    w = float(el.get("width", 0))
    h = float(el.get("height", 0))
    return w >= 0.95 * W and h >= 0.95 * H


def extract_element_features(
    elements: List[Dict[str, Any]],
    W: float,
    H: float,
    ad: Optional[Any] = None,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """
    Extract a (N, FEATURE_DIM) feature matrix for all non-background elements.

    Returns:
        features: ndarray of shape (N, 24)
        active_elements: list of the N elements that correspond to each row
    """
    active_elements = [el for el in elements if not is_background_element(el, W, H)]
    n = len(active_elements)
    if n == 0:
        return np.zeros((0, FEATURE_DIM), dtype=np.float32), []

    features = np.zeros((n, FEATURE_DIM), dtype=np.float32)

    boxes = []
    roles = []
    for el in active_elements:
        x = float(el.get("x", 0.0))
        y = float(el.get("y", 0.0))
        w = max(1.0, float(el.get("width", 1.0)))
        h = max(1.0, float(el.get("height", 1.0)))
        boxes.append((x, y, w, h))
        roles.append(infer_role_index(el, ad))

    ar = W / max(1.0, H)
    log_area = math.log10(max(1.0, W * H))

    for i in range(n):
        xi, yi, wi, hi = boxes[i]
        cxi = xi + wi / 2.0
        cyi = yi + hi / 2.0
        r_i = roles[i]

        # 1. Normalized geometry (4)
        norm_geom = [xi / W, yi / H, wi / W, hi / H]

        # 2. One-hot role encoding (NUM_ROLES = 8)
        role_onehot = [0.0] * NUM_ROLES
        if 0 <= r_i < NUM_ROLES:
            role_onehot[r_i] = 1.0

        # 3. Margins to canvas boundaries (4)
        left_m = xi / W
        right_m = max(0.0, (W - (xi + wi)) / W)
        top_m = yi / H
        bottom_m = max(0.0, (H - (yi + hi)) / H)
        canvas_margins = [left_m, right_m, top_m, bottom_m]

        # 4. Proximity to nearest neighboring element in 4 directions (4)
        min_dist_up = top_m
        min_dist_down = bottom_m
        min_dist_left = left_m
        min_dist_right = right_m

        for j in range(n):
            if i == j:
                continue
            xj, yj, wj, hj = boxes[j]
            # Vertical neighbors
            # Overlap in x range
            x_overlap = max(0.0, min(xi + wi, xj + wj) - max(xi, xj))
            if x_overlap > 0:
                if yj >= yi + hi:  # neighbor is below
                    gap_down = max(0.0, (yj - (yi + hi)) / H)
                    min_dist_down = min(min_dist_down, gap_down)
                elif yi >= yj + hj:  # neighbor is above
                    gap_up = max(0.0, (yi - (yj + hj)) / H)
                    min_dist_up = min(min_dist_up, gap_up)

            # Horizontal neighbors
            # Overlap in y range
            y_overlap = max(0.0, min(yi + hi, yj + hj) - max(yi, yj))
            if y_overlap > 0:
                if xj >= xi + wi:  # neighbor is to the right
                    gap_right = max(0.0, (xj - (xi + wi)) / W)
                    min_dist_right = min(min_dist_right, gap_right)
                elif xi >= xj + wj:  # neighbor is to the left
                    gap_left = max(0.0, (xi - (xj + wj)) / W)
                    min_dist_left = min(min_dist_left, gap_left)

        neighbor_gaps = [min_dist_up, min_dist_down, min_dist_left, min_dist_right]

        # 5. Global context: aspect ratio and canvas scale (2)
        global_ctx = [ar / 5.0, log_area / 7.0]

        # 6. Center offset from canvas center (2)
        center_offset = [(cxi - W / 2.0) / W, (cyi - H / 2.0) / H]

        vec = norm_geom + role_onehot + canvas_margins + neighbor_gaps + global_ctx + center_offset
        features[i] = np.array(vec, dtype=np.float32)

    return features, active_elements
