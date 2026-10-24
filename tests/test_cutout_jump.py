"""Jump regressions inspect exported pixels using synthetic RGBA artwork only."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageSequence


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cutout_jump.py"
SIZE = (112, 100)
FIXED_BOX = [46, 32, 64, 46]
LEG_BOXES = ([28, 59, 44, 84], [68, 59, 84, 84])
LEG_COLORS = (((210, 45, 61, 255), (255, 187, 37, 255)),
              ((35, 74, 210, 255), (41, 222, 184, 255)))
DURATIONS = [240, 80, 100, 60, 80, 100, 100, 60, 50, 80, 90, 300]


def source_image():
    image = Image.new("RGBA", SIZE)
    draw = ImageDraw.Draw(image)
    # Separate colored legs let tests measure the entire source silhouette,
    # without mistaking intended torso occlusion for changed leg pixels.
    draw.rectangle((45, 30, 65, 60), fill=(59, 92, 49, 255))
    for x, (base, detail) in zip((30, 70), LEG_COLORS):
        draw.rectangle((x, 59, x + 6, 80), fill=base)
        draw.rectangle((x, 80, x + 11, 83), fill=base)
        draw.rectangle((x + 1, 63, x + 3, 67), fill=detail)
        draw.rectangle((x + 7, 81, x + 10, 82), fill=detail)
    pixels = np.array(image)
    pixels[34:37, 48:51] = [227, 171, 49, 255]
    pixels[38:41, 54:57] = [143, 37, 101, 119]
    pixels[42:44, 60:62] = [0, 0, 0, 0]
    pixels[43, 61] = [117, 29, 101, 0]
    return Image.fromarray(pixels)


def configuration():
    stages = ["rest", "anticipation", "compression", "push_off", "ascending",
              "near_apex", "turning_down", "descent", "contact", "absorb", "recover", "rest"]
    body_y = [0, 2, 4, 0, None, None, None, None, 0, 3, 1, 0]
    samples = {4: 60, 5: 140, 6: 240, 7: 340}
    return {
        "schema_version": 1, "frame_count": 12, "ground_y": 83,
        "flight": {"height_px": 12, "duration_ms": 400},
        "body": {"clear_rects": [list(box) for box in LEG_BOXES],
                 "fixed_boxes": [FIXED_BOX.copy()]},
        "legs": [{"name": name, "box": list(box), "ankle": [x, 80]}
                 for name, box, x in zip(("left", "right"), LEG_BOXES, (33, 73))],
        "poses": [{"stage": stages[i], "body_y": body_y[i],
                   "flight_time_ms": samples.get(i), "leg_down_px": int(i in (5, 6)),
                   "leg_inset_px": int(i in (5, 6)), "duration_ms": DURATIONS[i]}
                  for i in range(12)],
        "preview": {"scale": 2, "background": [218, 218, 218]},
    }


def leg_patch(pixels, colors):
    mask = np.any(np.all(pixels[:, :, None, :] == np.array(colors)[None, None, :, :], axis=-1), axis=-1)
    ys, xs = np.where(mask)
    if not len(xs):
        raise AssertionError("A complete source leg disappeared from the export.")
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    isolated = np.zeros_like(pixels)
    isolated[mask] = pixels[mask]
    x0, y0, x1, y1 = box
    return isolated[y0:y1, x0:x1], box


class CutoutJumpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cutout-jump-tests-")
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic.png"
        self.original = source_image()
        self.original.save(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, config=None, name="output", success=True):
        config = configuration() if config is None else config
        path = self.root / f"{name}-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        out = self.root / name
        result = subprocess.run([sys.executable, str(SCRIPT), "--image", str(self.source),
                                 "--config", str(path), "--out", str(out)],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "Invalid operation unexpectedly succeeded.")
            self.assertTrue((result.stdout + result.stderr).strip())
        return out

    @staticmethod
    def read_frames(out):
        frames = []
        for path in sorted((out / "frames").glob("*.png")):
            with Image.open(path) as image:
                frames.append(np.array(image.convert("RGBA")))
        return frames

    @staticmethod
    def read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_complete_asymmetric_legs_and_shoes_keep_original_pixels(self):
        out = self.run_cli()
        original = np.array(self.original)
        for index, frame in enumerate(self.read_frames(out)):
            for colors in LEG_COLORS:
                expected, _ = leg_patch(original, colors)
                actual, _ = leg_patch(frame, colors)
                self.assertTrue(np.array_equal(actual, expected),
                                f"Frame {index + 1} changed the source leg or shoe.")

    def test_exported_feet_stay_planted_or_follow_the_timed_flight(self):
        config = configuration()
        out = self.run_cli(config)
        poses = self.read_json(out / "motion.json")["poses"]
        original = np.array(self.original)
        for index, (frame, pose) in enumerate(zip(self.read_frames(out), poses)):
            spec = config["poses"][index]
            t = spec["flight_time_ms"]
            expected_body_y = spec["body_y"] if t is None else -round(4 * 12 * (t / 400) * (1 - t / 400))
            self.assertEqual(pose["body_y"], expected_body_y)
            self.assertEqual(pose["airborne"], t is not None)
            for leg_index, (name, colors) in enumerate(zip(("left", "right"), LEG_COLORS)):
                _, box = leg_patch(frame, colors)
                _, source_box = leg_patch(original, colors)
                dy = 0 if t is None else expected_body_y + spec["leg_down_px"]
                dx = spec["leg_inset_px"] * (1 if leg_index == 0 else -1)
                self.assertEqual(box[0], source_box[0] + dx)
                self.assertEqual(box[3] - 1, config["ground_y"] + dy)
                self.assertEqual(pose["legs"][name]["sole_y"], box[3] - 1)
                if t is None:
                    self.assertEqual(box, source_box)
                else:
                    self.assertLess(box[3] - 1, config["ground_y"])
        # These samples are driven by actual playback durations, not just labels.
        self.assertEqual(sum(DURATIONS[3:8]), config["flight"]["duration_ms"])

    def test_body_rgba_landing_absorption_and_rest_loop(self):
        out = self.run_cli()
        frames = self.read_frames(out)
        poses = self.read_json(out / "motion.json")["poses"]
        x0, y0, x1, y1 = FIXED_BOX
        expected = np.array(self.original)[y0:y1, x0:x1]
        for frame, pose in zip(frames, poses):
            bob = pose["body_y"]
            self.assertTrue(np.array_equal(frame[y0+bob:y1+bob, x0:x1], expected))
        self.assertTrue(np.array_equal(frames[0], frames[-1]))
        self.assertEqual([p["body_y"] for p in poses[8:]], [0, 3, 1, 0])
        self.assertEqual([p["body_y"] for p in poses[:4]], [0, 2, 4, 0])
        self.assertFalse(np.array_equal(frames[8], frames[9]))
        self.assertFalse(np.array_equal(frames[9], frames[10]))

    def test_export_counts_gif_timing_and_review_status_are_truthful(self):
        out = self.run_cli()
        frames = self.read_frames(out)
        self.assertEqual(len(frames), 12)
        report = self.read_json(out / "validation.json")
        self.assertEqual(report["frame_count"], 12)
        self.assertEqual(report["unique_frame_count"], len({frame.tobytes() for frame in frames}))
        self.assertTrue(report["numeric_checks_passed"])
        self.assertEqual(report["status"], "needs_visual_review")
        with Image.open(out / "preview.gif") as gif:
            self.assertEqual(gif.info.get("loop"), 0)
            self.assertEqual(gif.n_frames, 12)
            durations = [frame.info["duration"] for frame in ImageSequence.Iterator(gif)]
        self.assertEqual(durations, DURATIONS)
        self.assertEqual(report["gif"]["durations_ms"], durations)
        self.assertEqual(report["gif"]["duration_ms"], sum(DURATIONS))
        for name in ("spritesheet.png", "contact-sheet.png", "motion.json", "run-config.json", "result.zip"):
            self.assertTrue((out / name).is_file(), name)

    def test_invalid_timing_and_clipped_flight_leave_no_partial_delivery(self):
        cases = [configuration(), configuration()]
        cases[0]["poses"][5]["flight_time_ms"] += 10
        cases[1]["flight"]["height_px"] = 60
        for index, config in enumerate(cases):
            with self.subTest(case=index):
                out = self.run_cli(config, name=f"invalid-{index}", success=False)
                self.assertFalse(out.exists() and any(out.iterdir()))

    def test_existing_output_is_not_overwritten(self):
        out = self.root / "existing"
        out.mkdir()
        sentinel = out / "approved-result.txt"
        sentinel.write_text("Keep the approved result.", encoding="utf-8")
        self.run_cli(name="existing", success=False)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "Keep the approved result.")
        self.assertEqual([p.name for p in out.iterdir()], [sentinel.name])


if __name__ == "__main__":
    unittest.main()
