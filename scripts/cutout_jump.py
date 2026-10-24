#!/usr/bin/env python3
"""Render one rest-to-rest hop using intact source legs and an explicit timeline.

This is the approved small-jump cutout method, not a pose generator. Agents must
choose source-specific cutouts and re-plan the timeline for other frame counts.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

import cv2
import numpy as np
from PIL import Image, ImageDraw

from cutout_walk import (ValidationError, background, check_cutout_bounds, cutout,
                         fields, number, point, rect, rects, require, rotation,
                         transform, write_gif, write_json)

__version__ = "0.3.0"


def validate_config(config, size):
    fields(config, {"schema_version", "frame_count", "ground_y", "flight", "body", "legs", "poses"},
           {"action", "arms", "preview"}, "config")
    require(type(config["schema_version"]) is int and config["schema_version"] == 1, "schema_version must be 1")
    count = number(config["frame_count"], "frame_count", integer=True, minimum=8, maximum=64)
    ground = number(config["ground_y"], "ground_y", integer=True, minimum=0, maximum=size[1] - 1)
    flight = config["flight"]
    fields(flight, {"height_px", "duration_ms"}, set(), "flight")
    height = number(flight["height_px"], "flight.height_px", minimum=0)
    require(height > 0, "flight.height_px must be positive")
    flight_ms = number(flight["duration_ms"], "flight.duration_ms", integer=True, minimum=10)
    body = config["body"]
    fields(body, {"clear_rects"}, {"fixed_boxes"}, "body")
    body = {"clear_rects": rects(body["clear_rects"], size, "body.clear_rects"),
            "fixed_boxes": rects(body.get("fixed_boxes", []), size, "body.fixed_boxes")}
    poses = config["poses"]
    require(isinstance(poses, list) and len(poses) == count, "poses must have frame_count entries")
    normalized_poses, air = [], []
    for i, pose in enumerate(poses):
        label = f"poses[{i}]"
        fields(pose, {"stage", "body_y", "flight_time_ms", "leg_down_px", "leg_inset_px", "duration_ms"}, set(), label)
        require(isinstance(pose["stage"], str) and bool(pose["stage"].strip()), f"{label}.stage must be nonempty")
        duration = number(pose["duration_ms"], f"{label}.duration_ms", integer=True, minimum=10, maximum=655350)
        require(duration % 10 == 0, "pose durations must be multiples of 10 ms")
        for key in ("leg_down_px", "leg_inset_px"):
            number(pose[key], f"{label}.{key}", integer=True, minimum=0)
        if pose["flight_time_ms"] is None:
            number(pose["body_y"], f"{label}.body_y", integer=True, minimum=0)
            require(pose["leg_down_px"] == pose["leg_inset_px"] == 0, "grounded legs must stay at their source positions")
        else:
            require(pose["body_y"] is None, "airborne body_y must be null; it is calculated from the parabola")
            t = number(pose["flight_time_ms"], f"{label}.flight_time_ms", integer=True, minimum=1)
            require(t < flight_ms, "airborne sample must lie inside the flight duration")
            air.append(i)
        normalized_poses.append(dict(pose))
    require(len(air) >= 2 and air == list(range(air[0], air[-1] + 1)), "provide one contiguous ascending/descending airborne interval")
    push, contact = air[0] - 1, air[-1] + 1
    require(push >= 2 and contact <= count - 3, "reserve rest, anticipation, push-off, contact, absorption, and final rest poses")
    require(poses[0]["stage"] == poses[-1]["stage"] == "rest" and poses[0]["body_y"] == poses[-1]["body_y"] == 0,
            "first and last poses must be the same rest position")
    require(poses[push]["stage"] == "push_off" and poses[push]["body_y"] == 0, "airborne interval must follow a grounded push_off at body_y 0")
    require(poses[contact]["stage"] == "contact" and poses[contact]["body_y"] == 0, "airborne interval must end with contact at body_y 0")
    require(max(p["body_y"] for p in poses[1:push]) > 0, "provide positive pre-jump compression before push_off")
    recovery = [p["body_y"] for p in poses[contact + 1:]]
    require(recovery[0] > 0 and all(a >= b for a, b in zip(recovery, recovery[1:])),
            "contact must lead to absorption followed by decreasing compression toward rest")
    elapsed = poses[push]["duration_ms"]
    for i in air:
        require(poses[i]["flight_time_ms"] == elapsed, "flight sample times must match the exported pose durations from push_off")
        elapsed += poses[i]["duration_ms"]
    require(elapsed == flight_ms, "flight.duration_ms must equal playback time from push_off to contact")
    require(poses[air[0]]["flight_time_ms"] < flight_ms / 2 < poses[air[-1]]["flight_time_ms"],
            "flight must include both ascending and descending samples")

    legs, arms = config["legs"], config.get("arms", [])
    require(isinstance(legs, list) and len(legs) == 2, "legs must contain two complete leg cutouts in left-to-right order")
    require(isinstance(arms, list), "arms must be an array")
    parts = {}
    for kind, entries in (("legs", legs), ("arms", arms)):
        output, names = [], set()
        for i, entry in enumerate(entries):
            label = f"{kind}[{i}]"
            anchor = "ankle" if kind == "legs" else "pivot"
            required = {"name", "box", anchor} | ({"angles_degrees"} if kind == "arms" else set())
            fields(entry, required, {"exclude_rects"}, label)
            name = entry["name"]
            require(isinstance(name, str) and bool(name.strip()) and name not in names, f"{label}.name must be nonempty and unique")
            names.add(name)
            item = {"name": name, "box": rect(entry["box"], size, f"{label}.box"),
                    anchor: point(entry[anchor], size, f"{label}.{anchor}"),
                    "exclude_rects": rects(entry.get("exclude_rects", []), size, f"{label}.exclude_rects")}
            if kind == "arms":
                angles = entry["angles_degrees"]
                require(isinstance(angles, list) and len(angles) == count, f"{label}.angles_degrees must have frame_count entries")
                item["angles_degrees"] = [number(a, f"{label}.angle") for a in angles]
                require(angles[0] == angles[-1], "arms must return to their starting angle")
            output.append(item)
        parts[kind] = output
    require(parts["legs"][0]["ankle"][0] < parts["legs"][1]["ankle"][0], "legs must be ordered by their source x coordinate")
    preview = config.get("preview", {})
    fields(preview, set(), {"box", "scale", "background"}, "preview")
    color = preview.get("background", [218, 218, 218])
    require(isinstance(color, list) and len(color) == 3, "preview.background must be [r, g, b]")
    for c in color:
        number(c, "preview.background channel", integer=True, minimum=0, maximum=255)
    result = {"schema_version": 1, "frame_count": count, "ground_y": ground,
              "flight": {"height_px": height, "duration_ms": flight_ms}, "body": body,
              **parts, "poses": normalized_poses,
              "preview": {"box": rect(preview.get("box", [0, 0, *size]), size, "preview.box"),
                          "scale": number(preview.get("scale", 1), "preview.scale", integer=True, minimum=1),
                          "background": color[:]}}
    if "action" in config:
        require(isinstance(config["action"], str), "action must be a string")
        result["action"] = config["action"]
    return result


def render_frames(source, config):
    """Return frames, observed motion, and measurable checks; no visual approval."""
    size = source.size
    original = np.array(source)
    body_pixels = original.copy()
    for left, top, right, bottom in config["body"]["clear_rects"]:
        body_pixels[top:bottom, left:right] = 0
    body = Image.fromarray(body_pixels)
    parts = {kind: [(item, cutout(source, item)) for item in config[kind]] for kind in ("legs", "arms")}
    frames, poses = [], []
    for i, spec in enumerate(config["poses"]):
        airborne = spec["flight_time_ms"] is not None
        if airborne:
            u = spec["flight_time_ms"] / config["flight"]["duration_ms"]
            bob = -round(4 * config["flight"]["height_px"] * u * (1 - u))
        else:
            bob = spec["body_y"]
        canvas = Image.new("RGBA", size)
        pose = {"frame": i + 1, "stage": spec["stage"], "body_y": bob, "airborne": airborne,
                "duration_ms": spec["duration_ms"], "flight_time_ms": spec["flight_time_ms"], "legs": {}, "arms": {}}
        for side, (leg, image) in enumerate(parts["legs"]):
            inset = spec["leg_inset_px"] * (1 if side == 0 else -1)
            leg_y = bob + spec["leg_down_px"] if airborne else 0
            anchor = np.array(leg["ankle"], float)
            layer, matrix = transform(image, np.eye(2), anchor - np.array(leg["box"][:2], float),
                                      anchor + np.array([inset, leg_y]), size)
            check_cutout_bounds(image, matrix, size, f"frame {i + 1} leg {leg['name']}")
            pixels = np.array(layer)
            sole = int(np.where(pixels[:, :, 3] >= 128)[0].max())
            require(sole == config["ground_y"] + leg_y, "source legs must meet ground_y without reshaping or repositioning")
            require(sole < config["ground_y"] if airborne else sole == config["ground_y"], "airborne feet must clear the ground; grounded feet must be planted")
            dx, dy = (int(round(matrix[0, 2])), int(round(matrix[1, 2])))
            require(0 <= dx <= size[0] - image.width and 0 <= dy <= size[1] - image.height, "complete leg crop would leave the canvas")
            require(np.array_equal(pixels[dy:dy + image.height, dx:dx + image.width], np.array(image)), "whole leg pixels changed during integer translation")
            canvas.alpha_composite(layer)
            pose["legs"][leg["name"]] = {"sole_y": sole, "horizontal_inset": inset, "translation_y": leg_y,
                "matrix": matrix.tolist(), "source_scale": 1, "source_rotation": 0}
        for arm, image in parts["arms"]:
            pivot = np.array(arm["pivot"], float)
            angle = arm["angles_degrees"][i]
            layer, matrix = transform(image, rotation(angle), pivot - np.array(arm["box"][:2], float),
                                      pivot + np.array([0, bob]), size)
            check_cutout_bounds(image, matrix, size, f"frame {i + 1} arm {arm['name']}")
            canvas.alpha_composite(layer)
            pose["arms"][arm["name"]] = {"angle": angle, "matrix": matrix.tolist()}
        body_ys = np.where(body_pixels[:, :, 3] > 0)[0]
        require(len(body_ys) == 0 or (body_ys.min() + bob >= 0 and body_ys.max() + bob < size[1]),
                f"frame {i + 1} body visible content would be clipped")
        upper = Image.new("RGBA", size)
        upper.paste(body, (0, bob))
        canvas.alpha_composite(upper)
        pixels = np.array(canvas)
        for left, top, right, bottom in config["body"]["fixed_boxes"]:
            require(0 <= top + bob < bottom + bob <= size[1], "fixed box would leave the canvas")
            actual = pixels[top + bob:bottom + bob, left:right]
            expected = original[top:bottom, left:right]
            hidden = (actual[:, :, 3] == 0) & (expected[:, :, 3] == 0)
            actual[hidden] = expected[hidden]
            require(np.array_equal(actual, expected), f"frame {i + 1} fixed-region RGBA changed in body coordinates")
        left, top, right, bottom = config["preview"]["box"]
        outside = pixels[:, :, 3] > 0
        outside[top:bottom, left:right] = False
        require(not outside.any(), f"frame {i + 1} preview.box would clip visible content")
        frames.append(Image.fromarray(pixels))
        poses.append(pose)
    require(np.array_equal(np.array(frames[0]), np.array(frames[-1])), "rest poses differ across the loop boundary")
    return frames, poses, {"original_whole_legs_and_shoes": True, "legs_are_integer_translations_only": True,
        "source_pixel_checks": True, "grounded_feet_do_not_slide_or_sink": True, "airborne_feet_clear_ground": True,
        "fixed_regions_rgba_in_body_coordinates": True, "visible_content_not_clipped": True,
        "flight_sample_times_match_playback": True, "landing_has_absorb_and_recovery": True, "first_last_identical": True}


def run(image_path, config_path, out_path):
    out_path = Path(out_path).absolute()
    require(not out_path.is_symlink(), "output directory must not be a symlink")
    require(not out_path.exists() or (out_path.is_dir() and not any(out_path.iterdir())), "output already exists and is not empty")
    raw = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    with Image.open(image_path) as image:
        require(getattr(image, "n_frames", 1) == 1, "source must be a single image")
        source = image.convert("RGBA")
    alpha = np.array(source)[:, :, 3]
    require(np.any(alpha < 255) and np.any(alpha >= 128), "source must contain visible content and real transparency")
    config = validate_config(raw, source.size)
    frames, poses, checks = render_frames(source, config)
    durations = [p["duration_ms"] for p in poses]
    unique = len({frame.tobytes() for frame in frames})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{out_path.name}-", dir=out_path.parent))
    had_empty_output = out_path.exists()
    try:
        (temp / "frames").mkdir()
        for i, frame in enumerate(frames):
            frame.save(temp / "frames" / f"{i + 1:02}.png")
        preview = config["preview"]
        previews = []
        for frame in frames:
            crop = frame.crop(preview["box"])
            previews.append(background(crop, preview["background"]).resize(
                (crop.width * preview["scale"], crop.height * preview["scale"]), Image.Resampling.NEAREST))
        gif = write_gif(previews, temp / "preview.gif", durations)
        checks["gif_frame_count_timing_loop"] = True
        sheet = Image.new("RGBA", (source.width * len(frames), source.height))
        columns = min(4, len(frames))
        contact = Image.new("RGB", (source.width * columns, source.height * math.ceil(len(frames) / columns)), tuple(preview["background"]))
        draw = ImageDraw.Draw(contact)
        for i, frame in enumerate(frames):
            sheet.paste(frame, (i * source.width, 0))
            x, y = (i % columns) * source.width, (i // columns) * source.height
            contact.paste(background(frame, preview["background"]), (x, y))
            draw.text((x + 7, y + 8), f"{i + 1} {poses[i]['stage']}", fill=(25, 25, 25))
        sheet.save(temp / "spritesheet.png")
        contact.save(temp / "contact-sheet.png")
        warnings = [f"{unique} unique RGBA images among {len(frames)} PNG frames; rest endpoints intentionally match."]
        if not config["body"]["fixed_boxes"]:
            warnings.append("No fixed_boxes declared; fixed-region identity was not independently sampled.")
        if gif["frame_count"] < len(frames):
            warnings.append("Adjacent identical GIF images were merged with their duration preserved.")
        report = {"tool_version": __version__, "status": "needs_visual_review", "numeric_checks_passed": True,
                  "checks": checks, "frame_count": len(frames), "unique_frame_count": unique, "gif": gif,
                  "warnings": warnings, "visual_review": "pending", "natural_motion_approved": False}
        write_json(temp / "motion.json", {"frame_count": len(frames), "durations_ms": durations, "duration_ms": sum(durations),
                                          "ground_y": config["ground_y"], "flight": config["flight"], "poses": poses})
        write_json(temp / "validation.json", report)
        write_json(temp / "run-config.json", config)
        with zipfile.ZipFile(temp / "result.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(temp.rglob("*")):
                if file.is_file() and file.name != "result.zip":
                    archive.write(file, file.relative_to(temp).as_posix())
        if out_path.exists():
            require(out_path.is_dir() and not out_path.is_symlink() and not any(out_path.iterdir()), "output became nonempty while rendering")
            out_path.rmdir()
        os.replace(temp, out_path)
        return report
    except Exception:
        # Only remove the temporary directory this invocation allocated.
        if temp.resolve().parent == out_path.parent.resolve() and temp.name.startswith(f".{out_path.name}-"):
            shutil.rmtree(temp, ignore_errors=True)
        if had_empty_output and not out_path.exists():
            out_path.mkdir()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args.image, args.config, args.out)
    except (ValidationError, OSError, ValueError, cv2.error) as error:
        print(f"cutout_jump: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "numeric_checks_passed": True,
                      "frame_count": report["frame_count"], "unique_frame_count": report["unique_frame_count"], "gif": report["gif"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
