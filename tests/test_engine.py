"""Test and benchmark suite for the CHAMELEON layout engine.

Run from the repo root:

    python -m unittest discover -s tests -v

Everything is graded by ``engine.qa.audit`` - an auditor that is independent of
the layout code (it re-measures text with the renderer's own Skia metrics and
re-derives roles from the *source* design).  The "hard" rules from RULES.md
(ids preserved, nothing off-canvas, pictures in proportion, brand fields
untouched) must hold on EVERY case; "soft" quality (collisions, readable type)
must hold wherever the canvas is physically big enough to allow it.
"""

from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path
from unittest import mock

from engine import resize as resize_mod
from engine.analyzer import analyze_design
from engine.layouts import LayoutDoesNotFit, layout_proportional
from engine.main import render_to_png, self_check
from engine.qa import audit, ink_rect
from engine.resize import resize
from engine.typography import readable_floor
from engine.visible import visible_insets, visible_rect
from fuzz_designs import random_design

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = REPO_ROOT / "input"

# Real-world formats (name, width, height), in the spirit of the scoring catalogue.
CATALOGUE = [
    ("Instagram Post", 1080, 1080),
    ("Instagram Portrait", 1080, 1350),
    ("Instagram Story", 1080, 1920),
    ("Facebook Post", 1200, 630),
    ("Facebook Cover", 820, 312),
    ("X Post", 1600, 900),
    ("X Header", 1500, 500),
    ("LinkedIn Banner", 1584, 396),
    ("YouTube Thumbnail", 1280, 720),
    ("YouTube Channel Art", 2560, 1440),
    ("Pinterest Pin", 1000, 1500),
    ("Web Leaderboard", 728, 90),
    ("Medium Rectangle", 300, 250),
    ("Wide Skyscraper", 160, 600),
    ("Half Page", 300, 600),
    ("Billboard", 970, 250),
    ("Mobile Banner", 320, 50),
    ("Email Header", 600, 200),
    ("A4 Poster @300dpi", 2480, 3508),
    ("Presentation 16:9", 1920, 1080),
]


def is_roomy(w: int, h: int) -> bool:
    """Canvases large enough that overlap-free, readable layouts are always possible
    (the 17-element Level 3 design physically cannot be set at 7px+ in 300x250)."""
    return min(w, h) >= 250 and w * h >= 120_000 and w / h <= 4.5 and h / w <= 4.0


def hard_failures(report: dict) -> list:
    """Violations of the non-negotiable rules."""
    bad = []
    if not report["id_match"]:
        bad.append("ids")
    if report["off_canvas"]:
        bad.append(("off_canvas", report["off_canvas"]))
    if report["max_image_ar_error_pct"] > 5.0:
        bad.append(("image_aspect", report["max_image_ar_error_pct"]))
    bad += [i for i in report["issues"] if i["code"] in ("brand_changed", "image_distorted")]
    return bad


def load_tasks() -> list:
    return [json.loads(p.read_text()) for p in sorted(INPUT_DIR.glob("*.json"))]


class TestOfficialSamples(unittest.TestCase):
    """The three shipped tasks, at the exact targets they specify."""

    def test_each_level_passes_the_audit(self) -> None:
        for task in load_tasks():
            with self.subTest(task=task["task_id"]):
                out = resize(task["source"], task["target_canvas"])
                self.assertEqual(self_check(task["source"], out), [])
                rep = audit(task["source"], out)
                self.assertEqual(hard_failures(rep), [])
                self.assertEqual(rep["collisions"], [])
                self.assertEqual(rep["text_overflows"], [])
                # Level 3 squeezes 17 elements into 728x90, so only tiny text may remain.
                self.assertTrue(
                    all(i["code"] == "text_too_small" for i in rep["issues"]),
                    [i for i in rep["issues"] if i["code"] != "text_too_small"],
                )
                self.assertGreaterEqual(rep["score"], 60.0)

    def test_levels_one_and_two_are_flawless(self) -> None:
        for task in load_tasks()[:2]:
            out = resize(task["source"], task["target_canvas"])
            self.assertEqual(audit(task["source"], out)["issues"], [], task["task_id"])

    def test_output_renders_to_a_real_png(self) -> None:
        import tempfile

        task = load_tasks()[1]
        out = resize(task["source"], task["target_canvas"])
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "out.png"
            render_to_png(out, REPO_ROOT / "brand_kit" / "assets", png)
            from PIL import Image

            with Image.open(png) as image:
                self.assertEqual(image.size, (task["target_canvas"]["width"], task["target_canvas"]["height"]))


class TestCatalogue(unittest.TestCase):
    def test_samples_across_the_catalogue(self) -> None:
        for task in load_tasks():
            src = task["source"]
            for name, w, h in CATALOGUE:
                with self.subTest(task=task["task_id"], canvas=name):
                    out = resize(src, {"width": w, "height": h})
                    rep = audit(src, out)
                    self.assertEqual(hard_failures(rep), [])
                    self.assertEqual(self_check(src, out), [])
                    if is_roomy(w, h):
                        self.assertEqual(rep["collisions"], [])
                        self.assertEqual(rep["text_overflows"], [])
                        self.assertGreaterEqual(rep["score"], 85.0, rep["issues"])

    def test_type_scales_with_the_canvas(self) -> None:
        """A poster-sized canvas must get poster-sized type (the old engine capped
        every font at a fixed pixel size)."""
        task = load_tasks()[1]
        src = task["source"]
        small = resize(src, {"width": 1080, "height": 1350})
        big = resize(src, {"width": 2480, "height": 3508})
        head = lambda d: max(e["font_size"] for e in d["elements"] if e["type"] == "text")  # noqa: E731
        self.assertGreater(head(big), 1.8 * head(small))

    def test_roundtrip_wide_to_tall(self) -> None:
        for task in load_tasks():
            wide = resize(task["source"], {"width": 1600, "height": 400})
            for target in ({"width": 1080, "height": 1920}, {"width": 1080, "height": 1080}):
                with self.subTest(task=task["task_id"], target=target):
                    back = resize(wide, target)
                    rep = audit(wide, back)
                    self.assertEqual(hard_failures(rep), [])
                    self.assertEqual(rep["collisions"], [])


class TestRandomDesigns(unittest.TestCase):
    """Seeded random designs: arbitrary counts of text/images, groups, tilted
    stickers, drop-shadow text, cards, missing assets, left-aligned copy..."""

    SEEDS = range(40)
    TARGETS = [c for c in CATALOGUE if c[0] in (
        "Instagram Post", "Instagram Story", "Facebook Post", "X Header", "YouTube Thumbnail",
        "Pinterest Pin", "Web Leaderboard", "Medium Rectangle", "Wide Skyscraper", "Billboard",
        "A4 Poster @300dpi", "Mobile Banner",
    )]

    def test_hard_rules_hold_everywhere_and_roomy_canvases_are_clean(self) -> None:
        scores = []
        for seed in self.SEEDS:
            src = random_design(seed)
            for name, w, h in self.TARGETS:
                with self.subTest(seed=seed, canvas=name):
                    out = resize(src, {"width": w, "height": h})
                    rep = audit(src, out)
                    self.assertEqual(hard_failures(rep), [])
                    if is_roomy(w, h):
                        self.assertEqual(rep["collisions"], [], rep["issues"])
                        self.assertEqual(rep["text_overflows"], [], rep["issues"])
                        scores.append(rep["score"])
        self.assertGreaterEqual(sum(scores) / len(scores), 98.0)

    def test_no_collisions_even_on_cramped_banners(self) -> None:
        bad = 0
        total = 0
        for seed in self.SEEDS:
            src = random_design(seed)
            for w, h in ((728, 90), (320, 50), (300, 250), (160, 600)):
                rep = audit(src, resize(src, {"width": w, "height": h}))
                total += 1
                bad += bool(rep["collisions"])
        self.assertLessEqual(bad / total, 0.02, f"{bad}/{total} cramped layouts still collide")


class TestEngineContract(unittest.TestCase):
    def setUp(self) -> None:
        self.task = load_tasks()[2]

    def test_deterministic(self) -> None:
        a = resize(self.task["source"], {"width": 970, "height": 250})
        b = resize(self.task["source"], {"width": 970, "height": 250})
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_does_not_mutate_the_input(self) -> None:
        before = copy.deepcopy(self.task["source"])
        resize(self.task["source"], {"width": 1080, "height": 1920})
        self.assertEqual(before, self.task["source"])

    def test_output_is_plain_json_with_integer_canvas(self) -> None:
        out = resize(self.task["source"], {"width": 1080.0, "height": 1920.0})
        self.assertEqual(out["canvas"], {"width": 1080, "height": 1920})
        json.dumps(out)  # must serialise

    def test_missing_asset_is_kept_visible_and_untouched(self) -> None:
        """The engine must not hide or drop content: the intentionally missing
        asset keeps its source opacity and stays a valid, in-bounds element."""
        src = self.task["source"]
        out = resize(src, {"width": 1080, "height": 1080})
        wm_src = next(e for e in src["elements"] if e["id"] == "holiday_watermark")
        wm_out = next(e for e in out["elements"] if e["id"] == "holiday_watermark")
        self.assertEqual(wm_out.get("opacity"), wm_src.get("opacity"))
        self.assertGreater(wm_out["width"], 4)
        self.assertGreater(wm_out["height"], 4)

    def test_unknown_element_keys_are_preserved(self) -> None:
        """e.g. ``capitalize`` is honoured by the renderer but is not in the schema."""
        src = copy.deepcopy(self.task["source"])
        for e in src["elements"]:
            if e["id"] == "headline":
                e["capitalize"] = True
        out = resize(src, {"width": 1080, "height": 1080})
        head = next(e for e in out["elements"] if e["id"] == "headline")
        self.assertTrue(head["capitalize"])

    def test_z_order_and_element_order_are_preserved(self) -> None:
        out = resize(self.task["source"], {"width": 1080, "height": 1080})
        self.assertEqual([e["id"] for e in out["elements"]], [e["id"] for e in self.task["source"]["elements"]])
        for s, o in zip(self.task["source"]["elements"], out["elements"]):
            self.assertEqual(s.get("z_index"), o.get("z_index"))

    def test_falls_back_to_a_valid_layout_if_a_layout_crashes(self) -> None:
        boom = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.dict(resize_mod._LAYOUTS, {"vertical": boom, "split": boom, "banner": boom}):
            out = resize(self.task["source"], {"width": 1080, "height": 1080})
        rep = audit(self.task["source"], out)
        self.assertEqual(hard_failures(rep), [])

    def test_tolerates_sparse_elements(self) -> None:
        """Optional fields (rotation, opacity, z_index, group_id...) may all be absent."""
        src = {
            "canvas": {"width": 800, "height": 800},
            "elements": [
                {"id": "t", "type": "text", "x": 100, "y": 300, "width": 600, "height": 80,
                 "content": "Hello", "font_family": "Solstice Sans", "font_size": 40, "color": "rgba(255,255,255,1)"},
                {"id": "s", "type": "shape", "x": 50, "y": 50, "width": 100, "height": 40,
                 "shape_type": "rectangle", "fill_color": "rgba(200,0,0,1)"},
            ],
        }
        for w, h in ((1080, 1920), (728, 90), (500, 500)):
            out = resize(src, {"width": w, "height": h})
            self.assertEqual(hard_failures(audit(src, out)), [])


class TestAnalyzerRegressions(unittest.TestCase):
    """Bugs found by fuzzing that must stay fixed."""

    def _design(self, extra: list) -> dict:
        base = [
            {"id": "bg", "type": "image", "x": 0, "y": 0, "width": 1080, "height": 1080, "z_index": 0,
             "asset_id": "solstice_bg_gradient.png", "fit": "cover"},
            {"id": "headline", "type": "text", "x": 65, "y": 448, "width": 950, "height": 140, "z_index": 5,
             "content": "Golden Hour, Bottled", "font_family": "Solstice Serif", "font_size": 80,
             "font_weight": "bold", "color": "rgba(255,255,255,1)", "text_align": "center"},
            {"id": "sub", "type": "text", "x": 97, "y": 640, "width": 886, "height": 60, "z_index": 5,
             "content": "Limited run. While supplies last.", "font_family": "Solstice Sans", "font_size": 30,
             "color": "rgba(255,255,255,1)", "text_align": "center"},
        ]
        return {"canvas": {"width": 1080, "height": 1080}, "elements": base + extra}

    def test_faint_circle_behind_headline_is_not_a_button(self) -> None:
        circle = {"id": "halo", "type": "shape", "shape_type": "ellipse", "x": 426, "y": 464, "width": 224,
                  "height": 224, "z_index": 4, "fill_color": "rgba(255,255,255,0.12)"}
        ad = analyze_design(self._design([circle]))
        self.assertEqual(ad.headline["id"], "headline")
        self.assertIsNone(ad.cta_unit)

    def test_lone_product_image_is_hero_not_logo(self) -> None:
        product = {"id": "product", "type": "image", "x": 417, "y": 65, "width": 246, "height": 378,
                   "z_index": 3, "asset_id": "solstice_bottle.png", "fit": "contain"}
        ad = analyze_design(self._design([product]))
        self.assertIsNone(ad.logo)
        self.assertEqual([h["id"] for h in ad.hero_images], ["product"])

    def test_full_canvas_tint_is_background_not_a_corner_blob(self) -> None:
        scrim = {"id": "scrim", "type": "shape", "shape_type": "rectangle", "x": 0, "y": 0, "width": 1080,
                 "height": 1080, "z_index": 1, "fill_color": "rgba(20,10,30,0.35)"}
        src = self._design([scrim])
        out = resize(src, {"width": 1080, "height": 1920})
        s = next(e for e in out["elements"] if e["id"] == "scrim")
        self.assertEqual((s["x"], s["y"], s["width"], s["height"]), (0, 0, 1080, 1920))

    def test_drop_shadow_text_moves_with_its_primary(self) -> None:
        head = self._design([])["elements"][1]
        shadow = dict(head, id="headline_shadow", x=head["x"] + 3, y=head["y"] + 3, z_index=4,
                      color="rgba(0,0,0,0.6)")
        src = self._design([shadow])
        out = resize(src, {"width": 1080, "height": 1350})
        p = next(e for e in out["elements"] if e["id"] == "headline")
        s = next(e for e in out["elements"] if e["id"] == "headline_shadow")
        self.assertLess(abs(s["x"] - p["x"]), 0.2 * p["font_size"] + 1)
        self.assertLess(abs(s["y"] - p["y"]), 0.2 * p["font_size"] + 1)
        self.assertEqual(s["font_size"], p["font_size"])
        self.assertEqual(audit(src, out)["collisions"], [])

    def test_two_overlays_on_the_same_corner_do_not_overlap(self) -> None:
        def ribbon(rid: str, y: float) -> list:
            return [
                {"id": f"{rid}_bg", "type": "shape", "shape_type": "rectangle", "x": 760, "y": y, "width": 280,
                 "height": 54, "z_index": 4, "fill_color": "rgba(220,60,50,1)", "rotation": -12, "group_id": rid},
                {"id": f"{rid}_lbl", "type": "text", "x": 760, "y": y + 10, "width": 280, "height": 34,
                 "z_index": 6, "content": "SALE", "font_family": "Solstice Sans", "font_size": 20,
                 "font_weight": "bold", "color": "rgba(255,255,255,1)", "text_align": "center",
                 "rotation": -12, "group_id": rid},
            ]
        src = self._design(ribbon("r1", 30) + ribbon("r2", 100))
        for w, h in ((1080, 1080), (1080, 1920), (1280, 720)):
            with self.subTest(canvas=(w, h)):
                rep = audit(src, resize(src, {"width": w, "height": h}))
                self.assertEqual(rep["collisions"], [])


class TestAuditorItself(unittest.TestCase):
    """The auditor must actually catch the failures it exists to catch."""

    def setUp(self) -> None:
        self.task = load_tasks()[1]

    def test_naive_stretch_is_penalised(self) -> None:
        src = self.task["source"]
        sx, sy = 1600 / 1080, 400 / 1080
        stretched = {"canvas": {"width": 1600, "height": 400}, "elements": []}
        for e in src["elements"]:
            e = dict(e)
            e["x"], e["y"], e["width"], e["height"] = e["x"] * sx, e["y"] * sy, e["width"] * sx, e["height"] * sy
            if e["type"] == "text":
                e["font_size"] = max(1, e["font_size"] * min(sx, sy))
            stretched["elements"].append(e)
        good = audit(src, resize(src, {"width": 1600, "height": 400}))
        bad = audit(src, stretched)
        self.assertGreater(good["score"], bad["score"] + 20)
        self.assertTrue(bad["max_image_ar_error_pct"] > 5 or bad["collisions"] or bad["text_overflows"])

    def test_detects_overlap_offcanvas_and_id_loss(self) -> None:
        src = self.task["source"]
        out = resize(src, {"width": 1080, "height": 1080})
        broken = copy.deepcopy(out)
        a, b = [e for e in broken["elements"] if e["type"] == "text" and not e.get("group_id")][:2]
        b["x"], b["y"] = a["x"], a["y"]
        self.assertTrue(audit(src, broken)["collisions"])
        broken = copy.deepcopy(out)
        broken["elements"][3]["x"] = 5000
        self.assertTrue(audit(src, broken)["off_canvas"])
        broken = copy.deepcopy(out)
        broken["elements"].pop()
        self.assertFalse(audit(src, broken)["id_match"])
        broken = copy.deepcopy(out)
        next(e for e in broken["elements"] if e["type"] == "text")["font_family"] = "Comic Sans"
        self.assertTrue(any(i["code"] == "brand_changed" for i in audit(src, broken)["issues"]))

    def test_readable_floor_is_sane(self) -> None:
        self.assertGreaterEqual(readable_floor(300, 250), 7)
        self.assertGreater(readable_floor(2480, 3508), readable_floor(1080, 1080))


def _task(level: str) -> dict:
    return json.loads((INPUT_DIR / f"chameleon_sample_{level}.json").read_text())["source"]


def _by_id(design: dict) -> dict:
    return {e["id"]: e for e in design["elements"]}


class TestDesignQuality(unittest.TestCase):
    """Composition properties beyond the hard rules: visible-pixel sizing,
    fidelity on near-same formats, hierarchy and readability on small canvases."""

    def test_transparent_asset_padding_is_not_content(self) -> None:
        src = _task("level3_advanced")
        bottle = _by_id(src)["product"]
        l, t, r, b = visible_insets(bottle)
        self.assertGreater(l + r, 0.4)  # the starter bottle is mostly transparent
        x0, y0, x1, y1 = ink_rect(bottle)
        self.assertLess((x1 - x0) * (y1 - y0), 0.5 * bottle["width"] * bottle["height"])

    def test_hero_and_logo_use_their_visible_size(self) -> None:
        src = _task("level3_advanced")
        out = _by_id(resize(src, {"width": 1200, "height": 630}))
        _, y0, _, y1 = visible_rect(out["product"])
        self.assertGreater(y1 - y0, 0.45 * 630)  # the product, not its padding, owns the hero column
        out = _by_id(resize(_task("level1_warmup"), {"width": 728, "height": 90}))
        _, y0, _, y1 = visible_rect(out["logo"])
        self.assertGreater(y1 - y0, 0.25 * 90)

    def test_same_format_keeps_the_designers_composition(self) -> None:
        src = _task("level2_core")
        out = _by_id(resize(src, {"width": 1080, "height": 1080}))
        for e in src["elements"]:
            if e["id"].startswith("deco"):
                continue  # bleeding circles must be pulled on-canvas
            o = out[e["id"]]
            self.assertLess(abs(o["x"] - e["x"]) + abs(o["y"] - e["y"]), 2.0, e["id"])
            self.assertEqual(o.get("font_size"), e.get("font_size"), e["id"])

    def test_proportional_candidate_declines_large_shape_changes(self) -> None:
        with self.assertRaises(LayoutDoesNotFit):
            layout_proportional(analyze_design(_task("level2_core")), 1584, 396)

    def test_labels_never_outshout_the_headline(self) -> None:
        src = random_design(21)  # one unbreakable word holds this headline back
        for w, h in [(1080, 1350), (1080, 1920), (2480, 3508)]:
            out = _by_id(resize(src, {"width": w, "height": h}))
            head = out["headline"]["font_size"]
            for eid, el in out.items():
                if eid.endswith("_label"):
                    self.assertLessEqual(el["font_size"], max(head, readable_floor(w, h)), (w, h, eid))

    def test_small_canvases_hold_every_text_at_the_floor(self) -> None:
        src = _task("level3_advanced")
        for w, h in [(160, 600), (300, 600), (970, 250)]:
            out = resize(src, {"width": w, "height": h})
            sizes = [e["font_size"] for e in out["elements"] if e["type"] == "text"]
            self.assertGreaterEqual(min(sizes), 7, (w, h))
            self.assertEqual(audit(src, out)["collisions"], [], (w, h))

    def test_spare_room_grows_content_instead_of_padding(self) -> None:
        src = _task("level2_core")
        out = _by_id(resize(src, {"width": 1080, "height": 1920}))
        src_k = math.sqrt(1080 * 1920 / (1080 * 1080))
        self.assertGreater(out["headline"]["font_size"], 1.3 * 56 * src_k * 0.95)

    def test_wide_ribbon_trims_padding_instead_of_shrinking_label(self) -> None:
        src = random_design(9)
        for w, h in [(1080, 1080), (820, 312), (1080, 1920)]:
            out = resize(src, {"width": w, "height": h})
            rep = audit(src, out)
            self.assertEqual(rep["issues"], [], (w, h, rep["issues"]))

    def test_multiline_headline_avoids_single_word_widow(self) -> None:
        from engine.typography import measure_text_block

        src = _task("level2_core")
        out = _by_id(resize(src, {"width": 1080, "height": 1920}))
        head = out["headline"]
        m = measure_text_block(head, head["font_size"], head["width"])
        if m.num_lines >= 2:
            self.assertGreaterEqual(len(m.lines[-1].split()), 2, m.lines)


if __name__ == "__main__":
    unittest.main()
