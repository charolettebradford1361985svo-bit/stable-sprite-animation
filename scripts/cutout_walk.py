#!/usr/bin/env python3
"""Animate two complete source leg cutouts without scaling or mirroring them.

All configuration coordinates refer to the input image. Requires Pillow, numpy,
and OpenCV. Numeric validation does not establish that a walk looks natural.
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
from PIL import Image, ImageDraw, ImageSequence

__version__ = "0.2.0"


class ValidationError(ValueError):
    """Invalid input or a failed measurable rendering requirement."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def fields(value, required, optional, label):
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(required) <= value.keys(), f"{label} missing fields: {sorted(set(required) - value.keys())}")
    require(value.keys() <= set(required) | set(optional),
            f"{label} unknown fields: {sorted(value.keys() - set(required) - set(optional))}")


def number(value, label, *, integer=False, minimum=None, maximum=None):
    kind = isinstance(value, int) if integer else isinstance(value, (int, float))
    require(kind and not isinstance(value, bool), f"{label} must be {'an integer' if integer else 'a number'}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    require(finite, f"{label} must be finite")
    require(minimum is None or value >= minimum, f"{label} must be >= {minimum}")
    require(maximum is None or value <= maximum, f"{label} must be <= {maximum}")
    return value


def rect(value, size, label):
    require(isinstance(value, list) and len(value) == 4, f"{label} must be [left, top, right, bottom]")
    for item in value:
        number(item, label, integer=True)
    left, top, right, bottom = value
    require(0 <= left < right <= size[0] and 0 <= top < bottom <= size[1],
            f"{label} must be nonempty and inside the source image")
    return value[:]


def rects(value, size, label):
    require(isinstance(value, list), f"{label} must be an array")
    return [rect(item, size, f"{label}[{i}]") for i, item in enumerate(value)]


def point(value, size, label):
    require(isinstance(value, list) and len(value) == 2, f"{label} must be [x, y]")
    for item in value:
        number(item, label)
    require(0 <= value[0] < size[0] and 0 <= value[1] < size[1],
            f"{label} must be inside the source image")
    return value[:]


def validate_config(config, size):
    """Validate and return a normalized, path-free schema-version-1 config."""
    fields(config, {"schema_version", "frame_count", "durations_ms", "ground_y", "gait", "body", "legs"},
           {"action", "arms", "preview"}, "config")
    require(type(config["schema_version"]) is int and config["schema_version"] == 1, "schema_version must be 1")
    count = number(config["frame_count"], "frame_count", integer=True, minimum=4, maximum=64)
    durations = config["durations_ms"]
    require(isinstance(durations, list) and len(durations) == count, "durations_ms must have frame_count entries")
    for duration in durations:
        number(duration, "durations_ms entry", integer=True, minimum=10, maximum=655350)
        require(duration % 10 == 0, "durations_ms entries must be multiples of 10 ms")
    ground = number(config["ground_y"], "ground_y", integer=True, minimum=0, maximum=size[1] - 1)
    gait = config["gait"]
    fields(gait, {"stride_px", "lift_px", "stance_fraction", "leg_swing_degrees"}, {"direction"}, "gait")
    gait = dict(gait)
    number(gait["stride_px"], "gait.stride_px", minimum=0)
    require(gait["stride_px"] > 0, "gait.stride_px must be positive")
    number(gait["lift_px"], "gait.lift_px", minimum=0)
    number(gait["stance_fraction"], "gait.stance_fraction", minimum=0.5, maximum=0.75)
    number(gait["leg_swing_degrees"], "gait.leg_swing_degrees", minimum=0)
    gait.setdefault("direction", "right")
    require(gait["direction"] in ("right", "left"), "gait.direction must be right or left")
    body = config["body"]
    fields(body, {"clear_rects"}, {"offsets_y", "fixed_boxes"}, "body")
    offsets = body.get("offsets_y", [0] * count)
    require(isinstance(offsets, list) and len(offsets) == count, "body.offsets_y must have frame_count entries")
    for value in offsets:
        number(value, "body.offsets_y entry", integer=True)
    body = {"clear_rects": rects(body["clear_rects"], size, "body.clear_rects"),
            "offsets_y": offsets[:], "fixed_boxes": rects(body.get("fixed_boxes", []), size, "body.fixed_boxes")}
    legs = config["legs"]
    require(isinstance(legs, list) and len(legs) == 2, "legs must contain exactly two complete leg cutouts")
    arms = config.get("arms", [])
    require(isinstance(arms, list), "arms must be an array")
    normalized = {}
    for kind, items in (("legs", legs), ("arms", arms)):
        output, names = [], set()
        for i, item in enumerate(items):
            label = f"{kind}[{i}]"
            is_leg = kind == "legs"
            required = {"name", "box", "ankle", "phase"} if is_leg else {"name", "box", "pivot", "swing_degrees"}
            fields(item, required, {"exclude_rects"} if is_leg else {"phase", "exclude_rects"}, label)
            name = item["name"]
            require(isinstance(name, str) and bool(name.strip()) and name not in names, f"{label}.name must be nonempty and unique")
            names.add(name)
            anchor = "ankle" if is_leg else "pivot"
            phase = number(item.get("phase", 0), f"{label}.phase")
            if is_leg:
                require(phase in (0, 0.5), f"{label}.phase must be 0 or 0.5")
            entry = {"name": name, "box": rect(item["box"], size, f"{label}.box"),
                     anchor: point(item[anchor], size, f"{label}.{anchor}"), "phase": phase,
                     "exclude_rects": rects(item.get("exclude_rects", []), size, f"{label}.exclude_rects")}
            if not is_leg:
                entry["swing_degrees"] = number(item["swing_degrees"], f"{label}.swing_degrees", minimum=0)
            output.append(entry)
        normalized[kind] = output
    require({leg["phase"] for leg in normalized["legs"]} == {0, 0.5}, "legs must have phases 0 and 0.5")
    preview = config.get("preview", {})
    fields(preview, set(), {"box", "scale", "background"}, "preview")
    bg = preview.get("background", [218, 218, 218])
    require(isinstance(bg, list) and len(bg) == 3, "preview.background must be [r, g, b]")
    for channel in bg:
        number(channel, "preview.background channel", integer=True, minimum=0, maximum=255)
    preview = {"box": rect(preview.get("box", [0, 0, *size]), size, "preview.box"),
               "scale": number(preview.get("scale", 1), "preview.scale", integer=True, minimum=1),
               "background": bg[:]}
    result = {"schema_version": 1, "frame_count": count, "durations_ms": durations[:], "ground_y": ground,
              "gait": gait, "body": body, **normalized, "preview": preview}
    if "action" in config:
        require(isinstance(config["action"], str), "action must be a string")
        result["action"] = config["action"]
    return result


def motion(phase, gait):
    """Return periodic horizontal displacement, lift, and support state."""
    u = phase % 1
    stance, stride = gait["stance_fraction"], gait["stride_px"]
    direction = 1 if gait.get("direction", "right") == "right" else -1
    if u <= stance:
        return direction * stride * (1 - 2 * u / stance), 0.0, True
    t = (u - stance) / (1 - stance)
    return direction * -stride * math.cos(math.pi * t), gait["lift_px"] * math.sin(math.pi * t), False


def rotation(angle):
    r = math.radians(angle)
    return np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])


def transform(image, rotation_matrix, src_pivot, dst_pivot, size):
    matrix = np.column_stack([rotation_matrix, dst_pivot - rotation_matrix @ src_pivot])
    result = cv2.warpAffine(np.array(image), matrix.astype(np.float32), size,
                           flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    return Image.fromarray(result), matrix


def unbounded_pixels(image, matrix):
    """Rasterize into a tight expanded canvas to detect actual alpha clipping."""
    width, height = image.size
    corners = np.array([[0, 0], [width, 0], [0, height], [width, height]], float)
    transformed = corners @ matrix[:, :2].T + matrix[:, 2]
    require(np.all(np.isfinite(transformed)), "transform produced non-finite coordinates")
    origin = np.floor(transformed.min(axis=0)).astype(np.int64) - 2
    end = np.ceil(transformed.max(axis=0)).astype(np.int64) + 2
    extent = end - origin
    require(np.all(extent > 0) and np.all(extent <= 100000), "transform extent is invalid")
    shifted = matrix.copy()
    shifted[:, 2] -= origin
    pixels = cv2.warpAffine(np.array(image), shifted.astype(np.float32), tuple(map(int, extent)),
                            flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    return pixels, origin


def check_cutout_bounds(image, matrix, size, label):
    pixels, origin = unbounded_pixels(image, matrix)
    ys, xs = np.where(pixels[:, :, 3] > 0)
    require(len(xs) > 0, f"{label} has no visible pixels after transformation")
    require(xs.min() + origin[0] >= 0 and xs.max() + origin[0] < size[0]
            and ys.min() + origin[1] >= 0 and ys.max() + origin[1] < size[1],
            f"{label} visible content would be clipped by the canvas")


def cutout(source, definition):
    left, top, right, bottom = definition["box"]
    pixels = np.array(source.crop((left, top, right, bottom)))
    for x0, y0, x1, y1 in definition["exclude_rects"]:
        x0, y0, x1, y1 = max(x0, left), max(y0, top), min(x1, right), min(y1, bottom)
        if x0 < x1 and y0 < y1:
            pixels[y0 - top:y1 - top, x0 - left:x1 - left] = 0
    require(np.any(pixels[:, :, 3] >= 128), f"cutout {definition['name']} needs pixels with alpha >= 128")
    return Image.fromarray(pixels)


def render_frames(source, config):
    """Return (RGBA frames, pose records, numeric check flags).

    config is normalized with validate_config. Fixed boxes validate final RGBA;
    source hidden RGB is restored only where both source and result alpha are 0.
    """
    size, count, gait = source.size, config["frame_count"], config["gait"]
    source_pixels = np.array(source)
    body_pixels = source_pixels.copy()
    for left, top, right, bottom in config["body"]["clear_rects"]:
        body_pixels[top:bottom, left:right] = 0
    body = Image.fromarray(body_pixels)
    cutouts = {kind: [(item, cutout(source, item)) for item in config[kind]] for kind in ("legs", "arms")}
    frames, poses = [], []
    for index in range(count):
        phase, bob = index / count, config["body"]["offsets_y"][index]
        canvas = Image.new("RGBA", size)
        pose = {"frame": index + 1, "body_y": bob, "legs": {}, "arms": {}}
        for leg, image in cutouts["legs"]:
            dx, lift, planted = motion(phase + leg["phase"], gait)
            angle = -gait["leg_swing_degrees"] * dx / gait["stride_px"]
            r = rotation(angle)
            anchor = np.array(leg["ankle"], float)
            src_pivot = anchor - np.array(leg["box"][:2], float)
            target = anchor + np.array([dx, bob])
            layer, matrix = transform(image, r, src_pivot, target, size)
            # Use the original canvas rasterization when available, matching the
            # reference algorithm exactly. The expanded raster covers offscreen
            # initial placements before the foot is aligned to the ground.
            ys = np.where(np.array(layer)[:, :, 3] >= 128)[0]
            expanded, origin = unbounded_pixels(image, matrix)
            full_ys = np.where(expanded[:, :, 3] >= 128)[0]
            require(len(full_ys) > 0, f"leg {leg['name']} lost its opaque pixels")
            full_floor = int(full_ys.max() + origin[1])
            initial_floor = int(ys.max()) if len(ys) and 0 <= full_floor < size[1] else full_floor
            floor = config["ground_y"] - int(round(lift))
            target[1] += floor - initial_floor
            layer, matrix = transform(image, r, src_pivot, target, size)
            check_cutout_bounds(image, matrix, size, f"frame {index + 1} leg {leg['name']}")
            ys = np.where(np.array(layer)[:, :, 3] >= 128)[0]
            require(len(ys) > 0, f"frame {index + 1} leg {leg['name']} is not visible")
            actual_floor = int(ys.max())
            require(np.allclose(r.T @ r, np.eye(2), atol=1e-10, rtol=0)
                    and abs(np.linalg.det(r) - 1) < 1e-10, "leg transform must be rigid and unmirrored")
            require(actual_floor == floor, f"frame {index + 1} leg {leg['name']} sole alignment failed")
            require(actual_floor <= config["ground_y"], "leg penetrates the ground")
            require(not planted or actual_floor == config["ground_y"], "supporting foot is not on the ground")
            canvas.alpha_composite(layer)
            pose["legs"][leg["name"]] = {"phase": (phase + leg["phase"]) % 1, "dx": dx, "lift": lift,
                "planted": planted, "sole_y": actual_floor, "angle": angle, "matrix": matrix.tolist(),
                "scale": 1.0, "mirrored": False}
        require(any(item["planted"] for item in pose["legs"].values()), "frame has no supporting foot")
        for arm, image in cutouts["arms"]:
            angle = -arm["swing_degrees"] * motion(phase + arm["phase"], gait)[0] / gait["stride_px"]
            pivot = np.array(arm["pivot"], float)
            layer, matrix = transform(image, rotation(angle), pivot - np.array(arm["box"][:2], float),
                                      pivot + np.array([0, bob]), size)
            check_cutout_bounds(image, matrix, size, f"frame {index + 1} arm {arm['name']}")
            canvas.alpha_composite(layer)
            pose["arms"][arm["name"]] = {"angle": angle, "matrix": matrix.tolist()}
        ys = np.where(body_pixels[:, :, 3] > 0)[0]
        require(len(ys) == 0 or (ys.min() + bob >= 0 and ys.max() + bob < size[1]),
                f"frame {index + 1} body visible content would be clipped")
        body_layer = Image.new("RGBA", size)
        body_layer.paste(body, (0, bob))
        canvas.alpha_composite(body_layer)
        pixels = np.array(canvas)
        for left, top, right, bottom in config["body"]["fixed_boxes"]:
            require(0 <= top + bob < bottom + bob <= size[1], "fixed box would leave the canvas")
            actual = pixels[top + bob:bottom + bob, left:right]
            original = source_pixels[top:bottom, left:right]
            hidden = (actual[:, :, 3] == 0) & (original[:, :, 3] == 0)
            actual[hidden] = original[hidden]
            require(np.array_equal(actual, original),
                    f"frame {index + 1} fixed_boxes RGBA differs after undoing body translation")
        canvas = Image.fromarray(pixels)
        left, top, right, bottom = config["preview"]["box"]
        visible = pixels[:, :, 3] > 0
        visible[top:bottom, left:right] = False
        require(not visible.any(), f"frame {index + 1} preview.box would clip visible content")
        frames.append(canvas)
        poses.append(pose)
    # Check the function at the unexported Nth sample, rather than pretending the
    # last exported pose must equal the first. Also verify interior periodicity.
    for leg in config["legs"]:
        for phase in (0, 0.123, 0.317, 0.999):
            first = motion(phase + leg["phase"], gait)
            repeated = motion(phase + leg["phase"] + 1, gait)
            require(np.allclose(first[:2], repeated[:2], atol=1e-10, rtol=1e-10)
                    and first[2] == repeated[2], "motion function is not periodic")
    return frames, poses, {"rigid_leg_transforms": True, "support_contact": True,
        "no_ground_penetration": True, "continuous_support": True, "periodic_motion_function": True,
        "fixed_regions_rgba": True, "visible_content_not_clipped": True}


def background(frame, color):
    result = Image.new("RGBA", frame.size, tuple(color) + (255,))
    result.alpha_composite(frame)
    return result.convert("RGB")


def write_gif(frames, path, durations):
    strip = Image.new("RGB", (frames[0].width * len(frames), frames[0].height))
    for i, frame in enumerate(frames):
        strip.paste(frame, (i * frame.width, 0))
    palette = strip.quantize(colors=240, method=Image.Quantize.MEDIANCUT)
    quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    # Adjacent identical frames may be merged by Pillow, with their durations
    # added together. Do not introduce marker pixels to manufacture uniqueness.
    quantized[0].save(path, save_all=True, append_images=quantized[1:], duration=durations,
                      disposal=2, loop=0, optimize=False)
    with Image.open(path) as gif:
        actual_count = gif.n_frames
        require(1 <= actual_count <= len(frames), "GIF frame count is invalid")
        require(gif.info.get("loop") == 0, "GIF must loop indefinitely")
        actual = [frame.info.get("duration", 0) for frame in ImageSequence.Iterator(gif)]
        require(all(duration > 0 for duration in actual) and sum(actual) == sum(durations),
                "GIF total duration does not match durations_ms")
        if actual_count == len(frames):
            require(actual == durations, "GIF frame timing does not match durations_ms")
    return {"frame_count": actual_count, "duration_ms": sum(actual), "durations_ms": actual, "loop": 0}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(image_path, config_path, out_path):
    """Write an atomic output directory and return its validation report."""
    out_path = Path(out_path).absolute()
    require(not out_path.is_symlink(), "output directory must not be a symlink")
    require(not out_path.exists() or (out_path.is_dir() and not any(out_path.iterdir())),
            "output already exists and is not an empty directory")
    raw = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    with Image.open(image_path) as image:
        require(getattr(image, "n_frames", 1) == 1, "source must be a single image")
        source = image.convert("RGBA")
    alpha = np.array(source)[:, :, 3]
    require(np.any(alpha < 255), "source must have transparency; opaque images are not accepted")
    require(np.any(alpha >= 128), "source must contain pixels with alpha >= 128")
    config = validate_config(raw, source.size)
    frames, poses, checks = render_frames(source, config)
    unique = len({frame.tobytes() for frame in frames})
    warnings = []
    if unique < len(frames):
        warnings.append(f"Only {unique} unique RGBA frames out of {len(frames)}; no pixels were altered to manufacture uniqueness.")
    if not config["body"]["fixed_boxes"]:
        warnings.append("No fixed_boxes were declared; fixed-region identity was not independently sampled.")
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
        gif = write_gif(previews, temp / "preview.gif", config["durations_ms"])
        if gif["frame_count"] < len(frames):
            warnings.append(f"GIF contains {gif['frame_count']} stored frames; adjacent duplicates were merged with their timing preserved.")
        checks["gif_frame_count_timing_loop"] = True
        sheet = Image.new("RGBA", (source.width * len(frames), source.height))
        for i, frame in enumerate(frames):
            sheet.paste(frame, (i * source.width, 0))
        sheet.save(temp / "spritesheet.png")
        columns = min(4, len(frames))
        contact = Image.new("RGB", (source.width * columns, source.height * math.ceil(len(frames) / columns)),
                            tuple(preview["background"]))
        draw = ImageDraw.Draw(contact)
        for i, frame in enumerate(frames):
            x, y = (i % columns) * source.width, (i // columns) * source.height
            contact.paste(background(frame, preview["background"]), (x, y))
            draw.text((x + 10, y + 10), str(i + 1), fill=(25, 25, 25))
        contact.save(temp / "contact-sheet.png")
        motion_report = {"frame_count": len(frames), "durations_ms": config["durations_ms"],
                         "duration_ms": sum(config["durations_ms"]), "ground_y": config["ground_y"], "poses": poses}
        report = {"tool_version": __version__, "status": "needs_visual_review", "numeric_checks_passed": True,
                  "checks": checks, "frame_count": len(frames), "unique_frame_count": unique,
                  "fixed_box_count": len(config["body"]["fixed_boxes"]), "gif": gif, "warnings": warnings,
                  "visual_review": "pending", "natural_motion_approved": False}
        offsets = config["body"]["offsets_y"]
        half = len(offsets) // 2
        report["body_motion"] = {
            "adjacent_deltas_y": [offsets[(i + 1) % len(offsets)] - value for i, value in enumerate(offsets)],
            "wrap_delta_y": offsets[0] - offsets[-1],
            "half_cycle_symmetric": offsets[:half] == offsets[half:] if len(offsets) % 2 == 0 else None,
            "note": "Includes the last-to-first edge; inspect intended body motion, not only leg phase periodicity.",
        }
        write_json(temp / "motion.json", motion_report)
        write_json(temp / "validation.json", report)
        write_json(temp / "run-config.json", config)
        with zipfile.ZipFile(temp / "result.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(temp.rglob("*")):
                if file.is_file() and file.name != "result.zip":
                    archive.write(file, file.relative_to(temp).as_posix())
        # Recheck at commit time so an intervening writer cannot be overwritten.
        if out_path.exists():
            require(out_path.is_dir() and not out_path.is_symlink() and not any(out_path.iterdir()),
                    "output became nonempty while rendering")
            out_path.rmdir()
        os.replace(temp, out_path)
        return report
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        if had_empty_output and not out_path.exists():
            out_path.mkdir()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--image", required=True, type=Path, help="Transparent input image")
    parser.add_argument("--config", required=True, type=Path, help="Schema-version-1 JSON configuration")
    parser.add_argument("--out", required=True, type=Path, help="New or empty output directory")
    args = parser.parse_args(argv)
    try:
        report = run(args.image, args.config, args.out)
    except (ValidationError, OSError, ValueError, cv2.error) as error:
        print(f"cutout_walk: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "numeric_checks_passed": True,
                      "frame_count": report["frame_count"], "warnings": report["warnings"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
