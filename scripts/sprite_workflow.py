#!/usr/bin/env python3
"""Prepare reference-conditioned sprite sheets and export registered animation frames.

This program does not generate images or infer anatomy. An image-capable agent
supplies the generated sheet, motion regions, registration anchor and loop plan.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import zipfile

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
except ImportError as exc:
    raise SystemExit("Missing dependency. Install this skill's requirements.txt in a Python environment.") from exc

VERSION = "0.1.0"
MAX_SHEET_PIXELS = 64_000_000
REGISTRATION_MIN_SCORE = 0.55
PROBE_MIN_SCORE = 0.55
BACKGROUND = (218, 218, 218)


class WorkflowError(ValueError):
    pass


def check(condition, message):
    if not condition:
        raise WorkflowError(message)


def integer(value, name, minimum=None, maximum=None):
    check(isinstance(value, int) and not isinstance(value, bool), f"{name} must be an integer.")
    if minimum is not None:
        check(value >= minimum, f"{name} must be at least {minimum}.")
    if maximum is not None:
        check(value <= maximum, f"{name} must be at most {maximum}.")
    return value


def finite_number(value, name, minimum=0):
    check(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
          f"{name} must be a finite number.")
    check(value >= minimum, f"{name} must be at least {minimum}.")
    return float(value)


def rectangle(value, size, name):
    check(isinstance(value, list) and len(value) == 4, f"{name} must be [left, top, right, bottom].")
    left, top, right, bottom = [integer(v, name) for v in value]
    check(0 <= left < right <= size[0] and 0 <= top < bottom <= size[1],
          f"{name} must be a nonempty rectangle inside frame_size; right/bottom are exclusive.")
    return (left, top, right, bottom)


def read_rgba(path):
    with Image.open(path) as image:
        check(image.width * image.height <= MAX_SHEET_PIXELS, "Image exceeds the 64-million-pixel processing limit.")
        return image.convert("RGBA")


def json_write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_destination(path):
    check(not path.is_symlink(), "Output destination cannot be a symbolic link.")
    if path.exists():
        check(path.is_dir() and not any(path.iterdir()), f"Output already exists and is not empty: {path}")


@contextmanager
def atomic_output(destination):
    """Publish a complete directory; clean only a newly created sibling temp dir."""
    destination = destination.absolute()
    check_destination(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = destination.parent.resolve()
    destination = parent / destination.name
    with tempfile.TemporaryDirectory(prefix=".sprite-build-", dir=parent) as temporary:
        staging = Path(temporary).resolve()
        # Both the eventual directory move and recursive temp cleanup are confined
        # to this explicitly selected parent and the newly created temp directory.
        check(staging.parent == parent and staging.name.startswith(".sprite-build-"), "Unsafe staging directory.")
        yield staging
        check_destination(destination)
        if destination.exists():
            destination.rmdir()  # Empty directory only; never recursively delete user output.
        check(destination.parent.resolve() == parent, "Output parent changed during processing.")
        os.replace(staging, destination)


def to_gray(image):
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[:, :, 3:4] / 255.0
    rgb = rgba[:, :, :3] * alpha + np.array(BACKGROUND) * (1 - alpha)
    return cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY)


def on_background(image):
    background = Image.new("RGBA", image.size, (*BACKGROUND, 255))
    background.alpha_composite(image)
    return background.convert("RGB")


def make_sheet(frames, columns, rows, labels=False, transparent=False):
    width, height = frames[0].size
    sheet = Image.new("RGBA" if transparent else "RGB", (width * columns, height * rows),
                      (0, 0, 0, 0) if transparent else BACKGROUND)
    for index, frame in enumerate(frames):
        xy = ((index % columns) * width, (index // columns) * height)
        if transparent:
            sheet.paste(frame, xy)  # Preserve RGBA, including transparent RGB values.
        else:
            sheet.paste(on_background(frame), xy)
            if labels:
                ImageDraw.Draw(sheet).text((xy[0] + 6, xy[1] + 6), str(index + 1), fill=(45, 45, 45))
    return sheet


def prompt_for(config):
    count, columns, rows = config["frame_count"], config["columns"], config["rows"]
    width, height = config["frame_size"]
    loop = config["loop"]["mode"]
    loop_instruction = {
        "rest": "Start at a relaxed rest pose; perform the action once, then visibly recover to that same rest pose over the final frames. The last two cells are settled recovery poses, without residual effects.",
        "cycle": "Make one complete periodic action. The motion phase after the last cell must continue naturally into the first cell; do not add an artificial freeze at the end.",
        "none": "Show the requested one-shot action in chronological order; the final pose should be a deliberate end state.",
    }[loop]
    return ("Create one coherent production sprite-animation sheet by editing the supplied reference canvas.\n"
            f"Action: {config['action']}\n"
            f"Exactly {count} animation frames, {columns} columns by {rows} rows, in reading order. "
            f"Canvas {width*columns}x{height*rows}; equal {width}x{height} cells. "
            f"Leave the final {columns*rows-count} unused cells fully transparent.\n"
            "Use the same supplied character, facing direction, proportions, outfit, palette and pixel style in every cell. "
            "Keep a fixed camera and common anchor. Animate only the body parts required by the action. "
            "Keep stationary parts in exactly the same positions; preserve joint connections and mouth/limb anatomy. "
            "All effects must remain inside their own cells.\n"
            f"{loop_instruction}\n"
            "True transparent background, sharp pixel clusters, no captions, lettering, numbered labels, grid lines, borders, scenery or checkerboard. "
            "Generate all frames together.\n"
            "The agent may append a numbered frame-by-frame action plan before invoking its image editing tool.\n")


def prepare(args):
    count = integer(args.frames, "frames", 2, 64)
    action = args.action.strip()
    check(bool(action), "action cannot be empty.")
    fps = finite_number(args.fps, "fps", 0.01)
    check(fps <= 100, "fps must be <=100 because GIF timing uses 10ms increments.")
    columns = args.columns if args.columns is not None else math.ceil(math.sqrt(count))
    columns = integer(columns, "columns", 1, count)
    rows = math.ceil(count / columns)
    source = read_rgba(args.image)
    alpha_min, alpha_max = source.getchannel("A").getextrema()
    check(alpha_max > 0, "Input image is empty.")
    check(alpha_min < 255, "Input has no transparency. Remove its background and inspect the cutout before prepare.")
    if args.padding is None:
        horizontal = max(16, math.ceil(source.width * .20))
        vertical = max(16, math.ceil(source.height * .20))
        padding = [horizontal, vertical, horizontal, vertical]
    else:
        padding = [integer(v, "padding", 0) for v in args.padding]
    left, top, right, bottom = padding
    size = (source.width + left + right, source.height + top + bottom)
    check(size[0] * size[1] * columns * rows <= MAX_SHEET_PIXELS,
          "Reference canvas is too large. Use an explicitly chosen smaller working resolution or fewer frames.")
    mode = args.loop or ("rest" if count >= 4 else "cycle")
    check(mode != "rest" or count >= 4, "rest loops need at least 4 frames; use cycle for shorter loops.")
    ticks = [int(round(i * 100 / fps)) for i in range(count + 1)]
    durations = [(ticks[i + 1] - ticks[i]) * 10 for i in range(count)]
    check(min(durations) >= 10, "fps produces durations shorter than GIF can represent.")
    config = {
        "schema_version": 1, "action": action, "frame_count": count,
        "columns": columns, "rows": rows, "frame_size": list(size),
        "source_offset": [left, top], "fps": fps, "durations_ms": durations,
        "registration_box": None, "motion_regions": [],
        "loop": {"mode": mode, "rest_frame": None}, "seam_probes": [], "frame_beats": [],
    }
    base = Image.new("RGBA", size)
    base.paste(source, (left, top))  # Exact copy, not alpha compositing that changes hidden RGB.
    canvas = Image.new("RGBA", (size[0] * columns, size[1] * rows))
    for index in range(count):
        canvas.paste(base, ((index % columns) * size[0], (index // columns) * size[1]))
    with atomic_output(args.out) as staging:
        source.save(staging / "source.png")
        base.save(staging / "seed-frame.png")
        canvas.save(staging / "reference-canvas.png")
        (staging / "prompt.txt").write_text(prompt_for(config), encoding="utf-8")
        json_write(staging / "run.json", config)
    return {"run": str(args.out), "status": "needs_agent_regions_and_image_generation",
            "frame_count": count, "frame_size": list(size), "columns": columns, "rows": rows}


def validate_config(config, seed):
    check(isinstance(config, dict), "run.json must contain a JSON object.")
    check(config.get("schema_version") == 1, "Unsupported schema_version; expected 1.")
    check(isinstance(config.get("action"), str) and bool(config["action"].strip()), "action must be nonempty text.")
    check(finite_number(config.get("fps"), "fps", .01) <= 100, "fps must be <=100.")
    count = integer(config.get("frame_count"), "frame_count", 2, 64)
    columns = integer(config.get("columns"), "columns", 1, count)
    rows = integer(config.get("rows"), "rows", 1, count)
    check(rows == math.ceil(count / columns), "rows must equal ceil(frame_count / columns).")
    size = config.get("frame_size")
    check(isinstance(size, list) and len(size) == 2, "frame_size must be [width,height].")
    size = tuple(integer(v, "frame_size", 1) for v in size)
    check(size == seed.size, "frame_size does not match seed-frame.png.")
    check(size[0] * size[1] * rows * columns <= MAX_SHEET_PIXELS, "Output sheet exceeds the processing limit.")
    offset = config.get("source_offset")
    check(isinstance(offset, list) and len(offset) == 2, "source_offset must be [left,top].")
    for v in offset:
        integer(v, "source_offset", 0)
    check(offset[0] < size[0] and offset[1] < size[1], "source_offset must be inside frame_size.")
    durations = config.get("durations_ms")
    check(isinstance(durations, list) and len(durations) == count, "durations_ms needs one duration per frame.")
    for v in durations:
        integer(v, "durations_ms", 10, 600_000)
        check(v % 10 == 0, "GIF durations_ms must be multiples of 10ms; do not silently truncate timing.")
    box = rectangle(config.get("registration_box"), size, "registration_box")
    regions = config.get("motion_regions")
    check(isinstance(regions, list) and len(regions) > 0,
          "motion_regions is empty. An agent must inspect the character and mark the action's moving regions.")
    mask = Image.new("L", size)
    for region in regions:
        check(isinstance(region, list) and len(region) >= 3, "Each motion region needs at least three [x,y] points.")
        points = []
        for point in region:
            check(isinstance(point, list) and len(point) == 2, "Motion points must be [x,y].")
            x, y = [integer(v, "motion point") for v in point]
            check(0 <= x < size[0] and 0 <= y < size[1], "Motion point lies outside the padded frame.")
            points.append((x, y))
        check(abs(cv2.contourArea(np.array(points, dtype=np.int32))) >= 1,
              "Motion polygons must enclose a nonzero area.")
        ImageDraw.Draw(mask).polygon(points, fill=255)
    dynamic = np.array(mask) > 0
    left, top, right, bottom = box
    check(not dynamic[top:bottom, left:right].any(),
          "registration_box intersects a moving region. Select a fixed textured anchor; fully moving characters need another registration strategy.")
    template_image = seed.crop(box)
    check(np.any(np.array(template_image)[:, :, 3] > 0), "registration_box contains no visible anchor pixels.")
    template = to_gray(template_image)
    check(float(template.std()) >= 3, "registration_box is too flat for reliable matching; choose a textured fixed anchor.")
    loop = config.get("loop")
    check(isinstance(loop, dict), "loop must be an object.")
    check(loop.get("mode") in ("rest", "cycle", "none"), "loop.mode must be rest, cycle or none.")
    if loop["mode"] == "rest":
        check(count >= 4, "rest loops need at least four frames.")
        integer(loop.get("rest_frame"), "loop.rest_frame (1-based)", 1, count)
    probes = config.get("seam_probes", [])
    check(isinstance(probes, list), "seam_probes must be a list.")
    for probe in probes:
        check(isinstance(probe, dict), "Each seam probe must be an object.")
        check(isinstance(probe.get("name"), str) and bool(probe["name"].strip()), "Seam probe requires a name.")
        rectangle(probe.get("box"), size, "seam probe box")
        finite_number(probe.get("max_shift_px", 1.5), "max_shift_px", 0)
    beats = config.get("frame_beats", [])
    check(isinstance(beats, list) and (not beats or len(beats) == count), "frame_beats must be empty or have one entry per frame.")
    check(all(isinstance(beat, str) for beat in beats), "frame_beats entries must be text.")
    return dynamic, template, box


def find_template(gray, template, ratios):
    best = (-1.0, 0.0, (0, 0))
    for ratio in ratios:
        width = round(template.shape[1] * float(ratio))
        height = round(template.shape[0] * float(ratio))
        if width < 3 or height < 3 or width > gray.shape[1] or height > gray.shape[0]:
            continue
        scaled = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA if ratio < 1 else cv2.INTER_NEAREST)
        if float(scaled.std()) < 2:
            continue
        result = cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED)
        _, score, _, xy = cv2.minMaxLoc(result)
        if np.isfinite(score) and score > best[0]:
            best = (float(score), float(ratio), (int(xy[0]), int(xy[1])))
    return best


def locate_probe(reference, image, box, radius):
    left, top, right, bottom = box
    template = reference[top:bottom, left:right]
    if min(template.shape) < 2 or float(template.std()) < 3:
        return None, "low_texture"
    x0, y0 = max(0, left-radius), max(0, top-radius)
    x1, y1 = min(image.shape[1], right+radius), min(image.shape[0], bottom+radius)
    search = image[y0:y1, x0:x1]
    if min(search.shape) < min(template.shape):
        return None, "insufficient_search_area"
    result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, xy = cv2.minMaxLoc(result)
    if not np.isfinite(score) or score < PROBE_MIN_SCORE:
        return None, "low_correlation"
    return (xy[0]+x0-left, xy[1]+y0-top, float(score)), None


def evaluate_seam(config, frames, dynamic):
    mode, count = config["loop"]["mode"], len(frames)
    if mode == "none":
        return {"status": "not_applicable", "checks": [], "note": "One-shot animation; no loop requirement."}
    probes = config.get("seam_probes", [])
    if not probes:
        return {"status": "unverified", "checks": [],
                "note": "No stable-feature probes configured. Matching endpoint hashes do not prove a smooth loop."}
    reference = to_gray(frames[0])
    transitions = [(count-2, count-1), (count-1, 0), (0, 1)]
    if mode == "rest":
        # A duplicated idle hold can hide the real transition into / out of rest.
        # Inspect only the edges of those contiguous holds, not every action step.
        first_pixels = np.array(frames[0])
        last_pixels = np.array(frames[-1])
        trailing_start = count - 1
        while trailing_start > 0 and np.array_equal(np.array(frames[trailing_start-1]), last_pixels):
            trailing_start -= 1
        if trailing_start > 0:
            transitions.append((trailing_start-1, trailing_start))
        leading_end = 0
        while leading_end + 1 < count and np.array_equal(np.array(frames[leading_end+1]), first_pixels):
            leading_end += 1
        if leading_end + 1 < count:
            transitions.append((leading_end, leading_end+1))
    transitions = list(dict.fromkeys(transitions))
    gray = {i: to_gray(frames[i]) for pair in transitions for i in pair}
    checks = []
    for first, second in transitions:
        for probe in probes:
            maximum = float(probe.get("max_shift_px", 1.5))
            radius = max(6, math.ceil(maximum * 4) + 2)
            result = {"from": first+1, "to": second+1, "probe": probe["name"], "max_shift_px": maximum}
            left, top, right, bottom = probe["box"]
            if not dynamic[top:bottom, left:right].any():
                result.update(status="unverified", dx=None, dy=None, correlation=None,
                              reason="probe_only_covers_copied_static_pixels")
                checks.append(result)
                continue
            a, why_a = locate_probe(reference, gray[first], tuple(probe["box"]), radius)
            b, why_b = locate_probe(reference, gray[second], tuple(probe["box"]), radius)
            if a is None or b is None:
                result.update(status="unverified", dx=None, dy=None, correlation=None,
                              reason=why_a or why_b)
            else:
                dx, dy = b[0]-a[0], b[1]-a[1]
                result.update(dx=int(dx), dy=int(dy), correlation=round(min(a[2], b[2]), 6),
                              status="verified" if math.hypot(dx, dy) <= maximum else "needs_review")
            checks.append(result)
    status = "needs_review" if any(c["status"] == "needs_review" for c in checks) else (
        "unverified" if any(c["status"] == "unverified" for c in checks) else "verified")
    return {"status": status, "checks": checks,
            "note": "Probes assess configured stable features only, including actual entry/exit edges of duplicated rest holds. Visually inspect action phase, pose, shape and disappearing effects across the loop."}


def save_gif(frames, contact, destination, durations, repeat=True):
    palette = contact.quantize(colors=240, method=Image.Quantize.MEDIANCUT)
    images = [on_background(f).quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    options = {"loop": 0} if repeat else {}
    images[0].save(destination, save_all=True, append_images=images[1:], duration=durations,
                   disposal=2, optimize=False, **options)
    with Image.open(destination) as saved:
        count = saved.n_frames
        loop_metadata = saved.info.get("loop")
        total = 0
        for i in range(count):
            saved.seek(i)
            total += saved.info.get("duration", 0)
    check(total == sum(durations), "Encoded GIF timing does not match requested durations.")
    check((repeat and loop_metadata == 0) or (not repeat and loop_metadata is None),
          "Encoded GIF repeat metadata does not match loop.mode.")
    return count, total, loop_metadata


def finalize(args):
    config = json.loads((args.run / "run.json").read_text(encoding="utf-8"))
    seed = read_rgba(args.run / "seed-frame.png")
    dynamic, template, anchor = validate_config(config, seed)
    destination = args.out if args.out is not None else args.run / "output"
    check_destination(destination)
    generated = read_rgba(args.sheet)
    alpha = np.array(generated)[:, :, 3]
    check(int(alpha.max()) >= 128, "Generated sheet is empty.")
    check(int(alpha.min()) < 255, "Generated sheet has no real transparency; remove the background before finalize.")
    count, columns, rows = config["frame_count"], config["columns"], config["rows"]
    width, height = seed.size
    boxes, slots = [], []
    for i in range(columns * rows):
        row, col = divmod(i, columns)
        box = (round(col*generated.width/columns), round(row*generated.height/rows),
               round((col+1)*generated.width/columns), round((row+1)*generated.height/rows))
        check(box[2] > box[0] and box[3] > box[1], "Generated sheet is too small for the configured grid.")
        slot = generated.crop(box)
        visible = int((np.array(slot)[:, :, 3] >= 128).sum())
        if i >= count:
            check(visible <= max(8, int(slot.width*slot.height*.001)),
                  f"Unused cell {i+1} contains visible content; check the generated frame count/grid.")
        else:
            check(visible > 0, f"Generated cell {i+1} is blank.")
            slots.append(slot)
            boxes.append(list(box))
    grayscale = [to_gray(slot) for slot in slots]
    nominal = math.sqrt(generated.width/(columns*width) * generated.height/(rows*height))
    ratios = nominal * np.linspace(.55, 1.6, 55)
    # Explicitly include 1:1 and nominal scale for exact synthetic / source copies.
    ratios = np.unique(np.concatenate([ratios, [1.0, nominal]]))
    estimates = [find_template(gray, template, ratios) for gray in grayscale]
    reliable = [ratio for score, ratio, _ in estimates if score >= REGISTRATION_MIN_SCORE]
    check(bool(reliable), "Registration failed: no reliable fixed-anchor match. Inspect the generated layout and anchor.")
    shared = float(np.median(reliable))
    candidates = np.unique(np.concatenate([shared*np.linspace(.98, 1.02, 17), [shared, nominal, 1.0]]))
    selected = sorted(set([0, count//3, (2*count)//3, count-1]))
    scores = [float(np.median([find_template(grayscale[i], template, [ratio])[0] for i in selected])) for ratio in candidates]
    shared = float(candidates[int(np.argmax(scores))])
    scale = 1/shared
    base = np.array(seed)
    frames, registered, registrations = [], [], []
    for i, slot in enumerate(slots):
        score, _, xy = find_template(grayscale[i], template, [shared])
        check(score >= REGISTRATION_MIN_SCORE,
              f"Frame {i+1} registration score {score:.3f} is too low; inspect its anchor/scale before exporting.")
        tx, ty = anchor[0]-xy[0]*scale, anchor[1]-xy[1]*scale
        pixels = cv2.warpAffine(np.array(slot), np.float32([[scale,0,tx],[0,scale,ty]]),
                               (width,height), flags=cv2.INTER_NEAREST,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=(0,0,0,0))
        pixels[pixels[:, :, 3] < 128] = 0
        pixels[pixels[:, :, 3] >= 128, 3] = 255
        frame = base.copy()
        frame[dynamic] = pixels[dynamic]
        # Keep all effect islands. Tiny dots may be intentional sneeze/spark particles.
        registered.append(Image.fromarray(pixels))
        frames.append(Image.fromarray(frame))
        registrations.append({"frame": i+1, "source_box": boxes[i],
                              "coarse_ratio": estimates[i][1], "score": round(score,6),
                              "translation": [round(tx,5),round(ty,5)]})
    source_order = list(range(1,count+1))
    if config["loop"]["mode"] == "rest":
        resting = config["loop"]["rest_frame"]
        idle = frames[resting-1].copy()
        frames[0], frames[-1] = idle.copy(), idle.copy()
        source_order[0] = source_order[-1] = resting
    arrays = [np.array(frame) for frame in frames]
    static = [int(np.any(a != base, axis=2)[~dynamic].sum()) for a in arrays]
    check(max(static) == 0, "Static pixel invariant failed.")
    hashes = [hashlib.sha256(a.tobytes()).hexdigest() for a in arrays]
    endpoints = hashes[0] == hashes[-1]
    if config["loop"]["mode"] == "rest":
        check(endpoints, "Rest loop endpoints do not match.")
    adjacent = []
    pairs = [(i,i+1) for i in range(count-1)]
    if config["loop"]["mode"] != "none":
        pairs.append((count-1,0))
    for a,b in pairs:
        # Ignore RGB under fully transparent pixels for perceptual change metrics.
        visible = (arrays[a][:,:,3] > 0) | (arrays[b][:,:,3] > 0)
        changed = np.any(arrays[a] != arrays[b], axis=2) & visible
        delta = np.abs(arrays[a].astype(np.int16)-arrays[b].astype(np.int16))
        adjacent.append({"from":a+1, "to":b+1, "changed_pixels":int(changed.sum()),
                         "mean_absolute_difference":round(float(delta[visible].mean()) if visible.any() else 0,6)})
    seam = evaluate_seam(config, frames, dynamic)
    warnings = []
    if len(set(hashes)) < 2:
        warnings.append("All output frames are identical; the requested action was not demonstrated.")
    if seam["status"] == "unverified":
        warnings.append("Loop seam remains unverified; configure useful probes and inspect the animation.")
    if seam["status"] == "needs_review":
        warnings.append("A loop transition exceeds the configured stable-feature displacement threshold.")
    warnings.extend(f"Frame {r['frame']}: marginal registration score {r['score']:.3f}." for r in registrations if r["score"] < .65)
    report = {
        "pipeline_version": VERSION,
        "status": "needs_review" if warnings else "needs_visual_review",
        "frame_count": count, "unique_frame_count": len(set(hashes)), "frame_size": list(seed.size),
        "loop_mode": config["loop"]["mode"], "common_raw_to_output_scale": scale,
        "first_last_identical": endpoints, "static_pixel_differences": static,
        "registrations": registrations, "adjacent_changes": adjacent, "seam": seam,
        "warnings": warnings, "durations_ms": config["durations_ms"],
        "alpha_policy": "Generated moving pixels use alpha threshold 128; fixed RGBA pixels remain byte-identical to seed-frame.png.",
        "preview_background": list(BACKGROUND),
        "visual_review_required": True,
        "note": "Zero fixed-region differences and endpoint hashes do not certify anatomy, pose timing or all facial details.",
    }
    contact = make_sheet(frames, columns, rows, labels=True)
    atlas = make_sheet(frames, columns, rows, transparent=True)
    with atomic_output(destination) as staging:
        (staging / "frames").mkdir()
        (staging / "registered").mkdir()
        digits = max(2, len(str(count)))
        for i,(frame,raw) in enumerate(zip(frames,registered),1):
            frame.save(staging/"frames"/f"{i:0{digits}d}.png")
            raw.save(staging/"registered"/f"{i:0{digits}d}.png")
        atlas.save(staging/"spritesheet.png")
        contact.save(staging/"contact-sheet.png")
        make_sheet(registered, columns, rows, labels=True).save(staging/"registered-contact-sheet.png")
        Image.fromarray((dynamic*255).astype(np.uint8)).save(staging/"motion-mask.png")
        encoded, total, loop_metadata = save_gif(frames, contact, staging/"preview.gif", config["durations_ms"],
                                                repeat=config["loop"]["mode"] != "none")
        report["gif_encoded_frames"], report["gif_duration_ms"] = encoded, total
        report["gif_loop"] = loop_metadata
        json_write(staging/"validation.json", report)
        json_write(staging/"source-order.json", {"source_frame_order": source_order, "one_based": True,
                   "note": "Rest loops reuse a generated resting pose; adjacent duplicates may become a longer GIF hold."})
        config_keys = ("schema_version", "action", "frame_count", "columns", "rows", "frame_size", "source_offset",
                       "fps", "durations_ms", "registration_box", "motion_regions", "frame_beats")
        portable_config = {key: config[key] for key in config_keys if key in config}
        portable_config["loop"] = {key: config["loop"][key] for key in ("mode", "rest_frame") if key in config["loop"]}
        portable_config["seam_probes"] = [{key: probe[key] for key in ("name", "box", "max_shift_px") if key in probe}
                                          for probe in config.get("seam_probes", [])]
        manifest = {"pipeline_version":VERSION, "config":portable_config,
                    "source_sha256":hashlib.sha256(np.array(seed).tobytes()).hexdigest(),
                    "generated_sheet_sha256":hashlib.sha256(args.sheet.read_bytes()).hexdigest(),
                    "artifacts":{"frames":"frames/", "gif":"preview.gif", "sheet":"spritesheet.png", "report":"validation.json"}}
        json_write(staging/"manifest.json", manifest)
        prompt_path = args.run/"prompt.txt"
        (staging/"generation-prompt.txt").write_text(prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else prompt_for(config),encoding="utf-8")
        with zipfile.ZipFile(staging/"result.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted((staging/"frames").glob("*.png")):
                archive.write(path, path.relative_to(staging).as_posix())
            for name in ("preview.gif","spritesheet.png","contact-sheet.png","validation.json","manifest.json","source-order.json","generation-prompt.txt"):
                archive.write(staging/name, name)
        with zipfile.ZipFile(staging/"result.zip") as archive:
            check(archive.testzip() is None, "Output ZIP verification failed.")
    return {"output":str(destination), "status":report["status"], "frame_count":count,
            "unique_frame_count":report["unique_frame_count"], "gif_encoded_frames":encoded,
            "static_max_pixel_difference":max(static), "seam_status":seam["status"], "warnings":warnings}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="Prepare a reference canvas and agent-editable run.json.")
    p.add_argument("--image", type=Path, required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--frames", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--columns", type=int)
    p.add_argument("--padding", type=int, nargs=4, metavar=("LEFT","TOP","RIGHT","BOTTOM"))
    p.add_argument("--fps", type=float, default=8)
    p.add_argument("--loop", choices=("rest","cycle","none"))
    p.set_defaults(handler=prepare)
    p = commands.add_parser("finalize", help="Register, lock fixed pixels, inspect loop probes, export PNG/GIF/ZIP.")
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--sheet", type=Path, required=True)
    p.add_argument("--out", type=Path)
    p.set_defaults(handler=finalize)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        print(json.dumps(args.handler(args), ensure_ascii=False, indent=2))
        return 0
    except (WorkflowError, OSError, ValueError, cv2.error) as exc:
        print(json.dumps({"error":str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
