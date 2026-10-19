"""Seeded random design generator for robustness testing.

The hidden scoring set contains designs the team has never seen (arbitrary
numbers of text boxes and images, awkward copy, missing assets, rotated pieces,
un-grouped buttons...). This builds that kind of design from the brand-kit
assets so the engine can be exercised far beyond the three shipped samples.

It is deliberately *not* tuned to any specific layout - it composes random
elements at random-but-plausible positions on random source canvases.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

SOURCE_CANVASES: List[Tuple[int, int]] = [
    (1080, 1080), (1080, 1350), (1080, 1920), (1280, 720), (1200, 628),
    (800, 800), (1000, 1500), (1920, 1080), (1080, 1080), (1080, 1350),
]

# (asset_id, native aspect ratio w/h)
HERO_ASSETS = [("solstice_bottle.png", 0.65), ("solstice_bottle.png", 1.0)]
LOGO_ASSET = ("solstice_logo_lockup.png", 3.2)
BG_ASSET = "solstice_bg_gradient.png"
MISSING_ASSET = "solstice_holiday_badge_2025.png"

HEADLINES = [
    "Chase the Light",
    "Summer Sale",
    "Introducing the All-New Limited-Batch Solstice Chase the Light Botanical Sparkling Collection",
    "Golden Hour, Bottled",
    "Supercalifragilisticexpialidocious Refreshment Experience",
    "50% OFF",
    "Taste the season",
]
SUBHEADS = [
    "Botanical sparkling infusions, crafted for golden hour.",
    "Small-batch botanical infusions blended with wild honey, blood orange zest, and a whisper of smoked rosemary.",
    "Free shipping on orders over $40",
    "New flavors every season.",
    "Limited run. While supplies last.",
]
LABELS = ["Shop the Summer Line", "Buy now", "LIMITED EDITION", "AWARD WINNING 2026", "SMALL BATCH", "Learn more", "New"]
LEGAL = "Please drink responsibly. Botanical Sparkling Co., 2026. Void where prohibited. See solstice.com/terms for full details."

WHITE = "rgba(255,255,255,1)"
ORANGE = "rgba(232,106,58,1)"
GOLD = "rgba(244,180,60,1)"
DARK = "rgba(35,27,41,1)"


def _text(eid: str, x: float, y: float, w: float, h: float, content: str, fs: float, z: int,
          rng: random.Random, serif: bool = False, bold: bool = False, color: str = WHITE,
          align: str = "center", group: str | None = None) -> Dict[str, Any]:
    el: Dict[str, Any] = {
        "id": eid, "type": "text", "x": x, "y": y, "width": w, "height": h, "z_index": z,
        "content": content, "font_family": "Solstice Serif" if serif else "Solstice Sans",
        "font_size": fs, "font_weight": "bold" if bold else "regular", "color": color,
        "text_align": align,
    }
    if group:
        el["group_id"] = group
    if serif and rng.random() < 0.4 and " " in content:
        cut = content.index(" ", len(content) // 2) if " " in content[len(content) // 2:] else len(content) // 2
        el["style_runs"] = [{"start": cut, "end": len(content), "color": GOLD}]
    return el


def _button(prefix: str, cx: float, y: float, w: float, h: float, label: str, fs: float,
            z: int, grouped: bool, fill: str, rng: random.Random) -> List[Dict[str, Any]]:
    gid = prefix if grouped else None
    shape: Dict[str, Any] = {
        "id": f"{prefix}_bg", "type": "shape", "shape_type": "rectangle", "x": cx - w / 2, "y": y,
        "width": w, "height": h, "z_index": z, "fill_color": fill, "corner_radius": h / 2,
    }
    if gid:
        shape["group_id"] = gid
    text = _text(f"{prefix}_label", cx - w / 2, y + (h - fs * 1.4) / 2, w, fs * 1.4, label, fs, z + 2,
                 rng, bold=True, group=gid)
    return [shape, text]


def random_design(seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    W, H = rng.choice(SOURCE_CANVASES)
    els: List[Dict[str, Any]] = []
    z = 0

    if rng.random() < 0.9:
        els.append({"id": "bg", "type": "image", "x": 0, "y": 0, "width": W, "height": H,
                    "z_index": z, "asset_id": BG_ASSET, "fit": "cover"})
    z += 1
    if rng.random() < 0.25:  # full-canvas tint sitting above the background
        els.append({"id": "scrim", "type": "shape", "shape_type": "rectangle", "x": 0, "y": 0,
                    "width": W, "height": H, "z_index": z, "fill_color": "rgba(20,10,30,0.35)"})
    z += 1
    for k in range(rng.choice([0, 0, 1, 2, 3])):
        d = rng.uniform(0.18, 0.5) * min(W, H)
        els.append({
            "id": f"deco{k}", "type": "shape", "shape_type": "ellipse",
            "x": rng.choice([-d * 0.3, W - d * 0.7, rng.uniform(0, W - d)]),
            "y": rng.choice([-d * 0.3, H - d * 0.7, rng.uniform(0, H - d)]),
            "width": d, "height": d, "z_index": z, "fill_color": "rgba(255,255,255,0.12)",
        })
        z += 1

    m = 0.06 * min(W, H)
    y = m
    wide = W / H > 1.6
    if rng.random() < 0.8:
        lh = 0.09 * H if not wide else 0.2 * H
        lw = lh * LOGO_ASSET[1]
        els.append({"id": "logo", "type": "image", "x": (W - lw) / 2 if not wide else m, "y": y,
                    "width": lw, "height": lh, "z_index": 5, "asset_id": LOGO_ASSET[0], "fit": "contain"})
        y += lh + m * 0.6

    n_hero = rng.choice([0, 1, 1, 1, 2, 3])
    for k in range(n_hero):
        asset, ar = rng.choice(HERO_ASSETS)
        hh = (0.28 if n_hero == 1 else 0.2) * H if not wide else 0.7 * H
        hw = hh * ar
        x = (W - hw) / 2 if n_hero == 1 else m + k * (hw + m * 0.5)
        els.append({"id": f"product{k}", "type": "image", "x": x, "y": y, "width": hw, "height": hh,
                    "z_index": 3, "asset_id": asset, "fit": "contain"})
        if not wide and k == n_hero - 1:
            y += hh + m * 0.6

    align = "left" if rng.random() < 0.25 else "center"
    headline = rng.choice(HEADLINES)
    hfs = rng.choice([0.04, 0.05, 0.065]) * min(W, H) * (1.4 if len(headline) < 25 else 1.0)
    head = _text("headline", m, y, W - 2 * m, hfs * 3.2, headline, hfs, 5, rng, serif=True, bold=True, align=align)
    if rng.random() < 0.2:  # "drop shadow": same words, nudged, darker, underneath
        shadow = dict(head, id="headline_shadow", x=head["x"] + 3, y=head["y"] + 3, z_index=4,
                      color="rgba(0,0,0,0.6)")
        shadow.pop("style_runs", None)
        els.append(shadow)
    els.append(head)
    y += hfs * 3.0

    for k in range(rng.choice([0, 1, 1, 2, 3])):
        sfs = max(10.0, hfs * rng.uniform(0.35, 0.55))
        els.append(_text(f"sub{k}", m * 1.5, y, W - 3 * m, sfs * 3.4, rng.choice(SUBHEADS), sfs, 5, rng, align=align))
        y += sfs * 3.0

    if rng.random() < 0.25:  # a card: panel + title + body (+ icon)
        cw, ch = 0.7 * W, 0.16 * H
        cx0 = (W - cw) / 2
        els.append({"id": "card_bg", "type": "shape", "shape_type": "rectangle", "x": cx0, "y": y,
                    "width": cw, "height": ch, "z_index": 3, "fill_color": "rgba(255,255,255,0.92)",
                    "corner_radius": 18, "group_id": "card"})
        els.append(_text("card_title", cx0 + cw * 0.05, y + ch * 0.12, cw * 0.9, ch * 0.3, "Free shipping", ch * 0.2,
                         5, rng, bold=True, color=DARK, align="left", group="card"))
        els.append(_text("card_body", cx0 + cw * 0.05, y + ch * 0.5, cw * 0.9, ch * 0.4, "On every order over $40, all summer long.",
                         ch * 0.13, 5, rng, color=DARK, align="left", group="card"))
        y += ch + m * 0.5

    if rng.random() < 0.6:
        n = rng.choice([2, 3, 4])
        bw = (W - 2 * m - (n - 1) * m * 0.4) / n
        bh = 0.045 * H
        for k in range(n):
            els += _button(f"badge{k}", m + bw / 2 + k * (bw + m * 0.4), y, bw, bh, rng.choice(LABELS),
                           bh * 0.3, 3, True, rng.choice([ORANGE, DARK]), rng)
        y += bh + m * 0.5

    if rng.random() < 0.85:
        bw, bh = 0.4 * W, 0.075 * H
        els += _button("cta", W / 2, y, bw, bh, rng.choice(LABELS[:2] + LABELS[5:6]),
                       bh * 0.3, 3, rng.random() < 0.6, ORANGE, rng)

    if rng.random() < 0.4:
        rw, rh = 0.28 * W, 0.05 * H
        r = {"id": "ribbon_bg", "type": "shape", "shape_type": "rectangle", "x": W - rw - m * 0.4, "y": m * 0.5,
             "width": rw, "height": rh, "z_index": 4, "fill_color": "rgba(220,60,50,1)", "rotation": -12,
             "group_id": "ribbon"}
        els += [r, _text("ribbon_label", r["x"], r["y"] + rh * 0.2, rw, rh * 0.6, "SUNSET SALE", rh * 0.35, 6,
                         rng, bold=True, group="ribbon") | {"rotation": -12}]

    if rng.random() < 0.15:
        tw, th = 0.18 * W, 0.05 * H
        els.append(_text("sticker", W - tw - m * 0.5, m * 1.6, tw, th, "NEW!", th * 0.7, 6, rng, bold=True,
                         color=GOLD) | {"rotation": -14})

    if rng.random() < 0.3:
        els.append({"id": "holiday_watermark", "type": "image", "x": W - 180 - m, "y": H - 80 - m * 0.4,
                    "width": 180, "height": 80, "z_index": 2, "asset_id": MISSING_ASSET})
    if rng.random() < 0.5:
        lfs = max(9.0, 0.011 * min(W, H) * 1.6)
        els.append(_text("legal", m, H - m - lfs * 1.6, W - 2 * m, lfs * 1.6, LEGAL, lfs, 6, rng,
                         color="rgba(255,255,255,0.7)"))

    # If the flow ran past the canvas, compress it vertically so the *source*
    # is a valid, non-overlapping design (fonts shrink with the squeeze).
    flow = [e for e in els if e["id"] != "bg" and not e["id"].startswith("deco")
            and e["id"] not in ("legal", "holiday_watermark")]
    extent = max((float(e["y"]) + float(e["height"]) for e in flow), default=0.0)
    limit = H - 2.2 * m
    if extent > limit:
        k = limit / extent
        for e in flow:
            e["y"] = float(e["y"]) * k
            e["height"] = float(e["height"]) * k
            if e["type"] == "text":
                e["font_size"] = max(9.0, float(e["font_size"]) * k)
            if e["type"] == "image":  # keep pictures proportional
                ar = float(e["width"]) / max(1e-6, float(e["height"]) / k)
                e["width"] = float(e["height"]) * ar
                e["x"] = float(e["x"])

    # Structural variety from a separate stream, so the base seeds keep their
    # shape: hero beside the copy on landscape sources, and a corner logo.
    vary = random.Random(seed * 7919 + 17)
    if W / H > 1.2 and vary.random() < 0.5:
        heroes = [e for e in els if e["id"].startswith("product")]
        if heroes:
            col = W * 0.5
            right = vary.random() < 0.6
            copy_x0 = m if right else col + m * 0.5
            hero_x0 = col + m * 0.5 if right else m
            hh = min(0.7 * H, (col - 1.5 * m) / max(0.1, sum(float(e["width"]) / float(e["height"]) for e in heroes)))
            x = hero_x0
            for e in heroes:
                ar = float(e["width"]) / float(e["height"])
                e.update(x=x, y=(H - hh) / 2, width=hh * ar, height=hh)
                x += hh * ar + m * 0.3
            def in_copy(e: Dict[str, Any]) -> bool:
                if str(e["id"]).startswith(("badge", "cta", "card")):
                    return True
                return e["type"] == "text" and e["id"] != "legal" and not e.get("rotation")
            for e in els:
                if in_copy(e):  # squeeze the copy flow into its half
                    e["x"] = copy_x0 + (float(e["x"]) - m) * 0.5
                    e["width"] = float(e["width"]) * 0.5
    if vary.random() < 0.3:
        for e in els:
            if e["id"] == "logo":
                e["x"] = m * 0.8

    # Clamp every authored element into the source canvas so the *source* is valid.
    for e in els:
        if e["id"].startswith("deco") or e["id"] == "bg":
            continue
        e["width"] = min(float(e["width"]), W)
        e["height"] = min(float(e["height"]), H)
        e["x"] = max(0.0, min(W - float(e["width"]), float(e["x"])))
        e["y"] = max(0.0, min(H - float(e["height"]), float(e["y"])))
    for e in els:
        for k in ("x", "y", "width", "height"):
            e[k] = round(float(e[k]), 2)
    return {"canvas": {"width": W, "height": H}, "elements": els}
