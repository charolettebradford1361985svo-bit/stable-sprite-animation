"""Cutout-walk regressions using synthetic, asymmetric RGBA artwork only.

These tests inspect exported pixels and public motion data; they need neither a
model nor user artwork, network access, or a machine-specific Python path.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from PIL import Image, ImageDraw


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'cutout_walk.py'
SIZE = (112, 96)
FIXED_BOX = [46, 21, 64, 34]
LEG_BOXES = ([28, 44, 44, 72], [68, 44, 84, 72])
PALETTES = (
    ((210, 45, 61, 255), (255, 103, 27, 255), (250, 187, 37, 255), (190, 21, 110, 255)),
    ((35, 74, 210, 255), (41, 171, 239, 255), (88, 222, 184, 255), (93, 39, 194, 255)),
)


def synthetic_source() -> Image.Image:
    image = Image.new('RGBA', SIZE)
    draw = ImageDraw.Draw(image)
    # Keep this fixture's torso away from moving legs so whole-cutout assertions
    # measure shape preservation without confusing intended torso occlusion.
    draw.rectangle((45, 18, 61, 44), fill=(59, 92, 49, 255))
    for index, origin_x in enumerate((30, 70)):
        base, upper, shin, shoe = PALETTES[index]
        draw.rectangle((origin_x, 44, origin_x + 5, 68), fill=base)
        # A forward toe and stepped sole expose mirroring and scale changes.
        draw.rectangle((origin_x, 68, origin_x + 9, 71), fill=base)
        draw.rectangle((origin_x + 10, 70, origin_x + 11, 71), fill=base)
        draw.rectangle((origin_x + 1, 48, origin_x + 3, 50), fill=upper)
        draw.rectangle((origin_x + 1, 58, origin_x + 3, 60), fill=shin)
        draw.rectangle((origin_x + 6, 69, origin_x + 8, 71), fill=shoe)
    array = np.array(image)
    # Fixed-area checks must exercise more than a flat opaque rectangle.
    array[22:26, 47:51] = [227, 171, 49, 255]
    array[26:30, 53:57] = [143, 37, 101, 119]
    array[30:33, 59:63] = [0, 0, 0, 0]
    array[31, 60] = [117, 29, 101, 0]  # Declared fixed pixels include hidden RGB.
    return Image.fromarray(array)


def configuration(count: int = 8) -> dict:
    return {
        'schema_version': 1,
        'frame_count': count,
        'durations_ms': [80 + 10 * (index % 3) for index in range(count)],
        'ground_y': 71,
        'gait': {'stride_px': 8, 'lift_px': 5, 'stance_fraction': 0.6,
                 'leg_swing_degrees': 0, 'direction': 'right'},
        'body': {'clear_rects': [list(box) for box in LEG_BOXES],
                 'offsets_y': [0] * count, 'fixed_boxes': [FIXED_BOX.copy()]},
        'legs': [
            {'name': 'rear', 'box': list(LEG_BOXES[0]), 'ankle': [33, 68], 'phase': 0},
            {'name': 'front', 'box': list(LEG_BOXES[1]), 'ankle': [73, 68], 'phase': 0.5},
        ],
        'preview': {'scale': 2, 'background': [218, 218, 218]},
    }


def color_mask(array: np.ndarray, colors) -> np.ndarray:
    return np.any(np.all(array[:, :, None, :] == np.asarray(colors)[None, None, :, :], axis=-1), axis=-1)


def colored_patch(array: np.ndarray, colors) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    mask = color_mask(array, colors)
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise AssertionError('The entire source leg disappeared from the exported frame.')
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    isolated = np.zeros_like(array)
    isolated[mask] = array[mask]
    left, top, right, bottom = box
    return isolated[top:bottom, left:right], box


class CutoutWalkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cutout-walk-tests-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'private-source-character.png'
        self.original = synthetic_source()
        self.original.save(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, config=None, *, name='output', success=True, image=None):
        config = configuration() if config is None else config
        config_path = self.root / f'{name}-input.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')
        out = self.root / name
        result = subprocess.run(
            [sys.executable, str(SCRIPT), '--image', str(image or self.source),
             '--config', str(config_path), '--out', str(out)],
            text=True, capture_output=True, encoding='utf-8', timeout=30,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, 'Invalid operation unexpectedly succeeded.')
            self.assertTrue((result.stdout + result.stderr).strip(), 'Failure must explain its cause.')
            self.assertNotIn('Traceback (most recent call last)', result.stdout + result.stderr)
        return out

    @staticmethod
    def read_json(path):
        return json.loads(path.read_text(encoding='utf-8'))

    @staticmethod
    def read_frames(out):
        return [np.array(Image.open(path).convert('RGBA'))
                for path in sorted((out / 'frames').glob('*.png'))]

    def assert_no_partial_output(self, out):
        self.assertFalse(out.exists() and any(out.iterdir()), 'Failure left partial delivery files.')

    def test_frame_counts_timing_and_delivery_archive(self):
        for count in (12, 8, 7):
            with self.subTest(frame_count=count):
                config = configuration(count)
                out = self.run_cli(config, name=f'export-{count}')
                frame_paths = sorted((out / 'frames').glob('*.png'))
                self.assertEqual([path.name for path in frame_paths],
                                 [f'{index:02d}.png' for index in range(1, count + 1)])
                frames = self.read_frames(out)
                for frame in frames:
                    self.assertEqual(frame.shape, (SIZE[1], SIZE[0], 4))
                    self.assertTrue(np.any(frame[:, :, 3] == 0))
                    self.assertTrue(np.any(frame[:, :, 3] == 255))
                # A periodic walk samples [0, 1), without inserting a duplicate end pose.
                self.assertFalse(np.array_equal(frames[0], frames[-1]))
                for filename in ('preview.gif', 'spritesheet.png', 'contact-sheet.png',
                                 'motion.json', 'validation.json', 'run-config.json', 'result.zip'):
                    self.assertTrue((out / filename).is_file(), filename)
                validation = self.read_json(out / 'validation.json')
                self.assertTrue(validation['numeric_checks_passed'])
                self.assertEqual(validation['status'], 'needs_visual_review')
                motion = self.read_json(out / 'motion.json')
                self.assertEqual(motion['frame_count'], count)
                self.assertEqual(motion['durations_ms'], config['durations_ms'])
                self.assertEqual(motion['duration_ms'], sum(config['durations_ms']))
                self.assertEqual(len(motion['poses']), count)
                with Image.open(out / 'preview.gif') as gif:
                    self.assertEqual(gif.info.get('loop'), 0)
                    duration = 0
                    for index in range(gif.n_frames):
                        gif.seek(index)
                        duration += gif.info['duration']
                    self.assertEqual(duration, sum(config['durations_ms']))
                with ZipFile(out / 'result.zip') as archive:
                    names = archive.namelist()
                    pngs = [name for name in names if name.startswith('frames/') and name.endswith('.png')]
                    self.assertEqual(len(pngs), count)
                    self.assertNotIn(self.source.name, names)
                    self.assertFalse(any(Path(name).is_absolute() or '..' in Path(name).parts for name in names))
                    for name in names:
                        self.assertNotIn('source', Path(name).stem.lower())
                        if name.endswith(('.json', '.txt', '.md')):
                            text = archive.read(name).decode('utf-8')
                            for private_value in (str(self.root), self.root.as_posix(), self.source.name):
                                self.assertNotIn(private_value, text)

    def test_original_whole_leg_and_asymmetric_shoe_pixels_survive_without_scaling(self):
        config = configuration(12)
        original = np.array(self.original)
        for direction in ('right', 'left'):
            with self.subTest(direction=direction):
                config['gait']['direction'] = direction
                out = self.run_cli(config, name=f'leg-shapes-{direction}')
                frames = self.read_frames(out)
                for frame_index, frame in enumerate(frames):
                    for leg_index, palette in enumerate(PALETTES):
                        expected, _ = colored_patch(original, palette)
                        actual, _ = colored_patch(frame, palette)
                        self.assertTrue(np.array_equal(actual, expected),
                                        f'{direction}, frame {frame_index + 1}, leg {leg_index}: source leg changed')

    def test_half_cycle_phase_and_actual_support_pixels_touch_ground(self):
        config = configuration(12)
        out = self.run_cli(config)
        poses = self.read_json(out / 'motion.json')['poses']
        frames = self.read_frames(out)
        for pose, frame in zip(poses, frames):
            rear, front = (pose['legs'][name] for name in ('rear', 'front'))
            self.assertAlmostEqual((front['phase'] - rear['phase']) % 1, 0.5)
            for index, leg in enumerate((rear, front)):
                _, box = colored_patch(frame, PALETTES[index])
                actual_sole_y = box[3] - 1
                self.assertEqual(actual_sole_y, leg['sole_y'])
                if leg['planted']:
                    self.assertEqual(actual_sole_y, config['ground_y'])
                    self.assertEqual(leg['lift'], 0)
                else:
                    self.assertLessEqual(actual_sole_y, config['ground_y'])
        # At quarter cycles, different source shoes supply support, observed in pixels.
        for frame_index, planted, lifted in ((3, 0, 1), (9, 1, 0)):
            _, planted_box = colored_patch(frames[frame_index], PALETTES[planted])
            _, lifted_box = colored_patch(frames[frame_index], PALETTES[lifted])
            self.assertEqual(planted_box[3] - 1, config['ground_y'])
            self.assertLess(lifted_box[3] - 1, config['ground_y'])

    def test_rigid_rotation_matrices_match_rendered_source_landmarks(self):
        config = configuration(8)
        config['gait']['leg_swing_degrees'] = 11
        out = self.run_cli(config)
        poses = self.read_json(out / 'motion.json')['poses']
        original = np.array(self.original)
        observed_rotation = False
        for pose, frame in zip(poses, self.read_frames(out)):
            for leg_index, name in enumerate(('rear', 'front')):
                leg = pose['legs'][name]
                matrix = np.asarray(leg['matrix'], dtype=float)
                self.assertEqual(matrix.shape, (2, 3))
                rotation = matrix[:, :2]
                np.testing.assert_allclose(rotation.T @ rotation, np.eye(2), atol=1e-6)
                self.assertAlmostEqual(np.linalg.det(rotation), 1, places=6)
                observed_rotation |= abs(rotation[0, 1]) > 1e-3
                for marker in PALETTES[leg_index][1:]:
                    source_y, source_x = np.nonzero(np.all(original == marker, axis=-1))
                    frame_y, frame_x = np.nonzero(np.all(frame == marker, axis=-1))
                    self.assertGreater(len(frame_x), 0, 'Source landmark was lost.')
                    left, top = LEG_BOXES[leg_index][:2]
                    expected = matrix @ np.array([source_x.mean() - left, source_y.mean() - top, 1])
                    actual = np.array([frame_x.mean(), frame_y.mean()])
                    self.assertLess(np.linalg.norm(actual - expected), 1.25,
                                    f'{name}: frame pixels do not follow the reported rigid transform')
        self.assertTrue(observed_rotation, 'Nonzero swing must rotate at least one rendered leg.')

    def test_integer_body_offsets_preserve_fixed_rgba_in_body_coordinates(self):
        config = configuration(8)
        offsets = [0, -1, -2, -1, 0, 1, 2, 1]
        config['body']['offsets_y'] = offsets
        out = self.run_cli(config)
        poses = self.read_json(out / 'motion.json')['poses']
        left, top, right, bottom = FIXED_BOX
        original = np.array(self.original)[top:bottom, left:right]
        self.assertTrue(np.any((original[:, :, 3] > 0) & (original[:, :, 3] < 255)))
        for offset, pose, frame in zip(offsets, poses, self.read_frames(out)):
            self.assertEqual(pose['body_y'], offset)
            np.testing.assert_array_equal(frame[top + offset:bottom + offset, left:right], original)

    def test_optional_body_offsets_default_to_zero(self):
        config = configuration(7)
        del config['body']['offsets_y']
        out = self.run_cli(config)
        self.assertEqual([pose['body_y'] for pose in self.read_json(out / 'motion.json')['poses']], [0] * 7)

    def test_identical_quantized_poses_keep_timing_without_inventing_pixel_motion(self):
        config = configuration(8)
        config['gait'].update(stride_px=0.01, lift_px=0, leg_swing_degrees=0)
        out = self.run_cli(config)
        frames = self.read_frames(out)
        self.assertEqual(len(frames), 8)
        self.assertTrue(all(np.array_equal(frame, frames[0]) for frame in frames[1:]))
        report = self.read_json(out / 'validation.json')
        self.assertEqual(report['frame_count'], 8)
        self.assertEqual(report['unique_frame_count'], 1)
        with Image.open(out / 'preview.gif') as gif:
            self.assertEqual(report['gif']['frame_count'], gif.n_frames)
            duration = 0
            for index in range(gif.n_frames):
                gif.seek(index)
                duration += gif.info['duration']
            self.assertEqual(duration, sum(config['durations_ms']))
            self.assertEqual(gif.info.get('loop'), 0)

    def test_invalid_configuration_fails_before_creating_delivery_files(self):
        cases = []

        def add(name, mutate):
            config = configuration()
            mutate(config)
            cases.append((name, config))

        add('outside-leg-box', lambda c: c['legs'][0].update(box=[-1, 44, 44, 72]))
        add('outside-fixed-box', lambda c: c['body'].update(fixed_boxes=[[0, 0, 113, 96]]))
        add('outside-preview', lambda c: c['preview'].update(box=[0, 0, 113, 96]))
        add('clipped-preview', lambda c: c['preview'].update(box=[40, 18, 68, 45]))
        add('duration-count', lambda c: c.update(durations_ms=[80]))
        add('duration-zero', lambda c: c['durations_ms'].__setitem__(0, 0))
        add('duration-not-centiseconds', lambda c: c['durations_ms'].__setitem__(0, 83))
        add('duplicate-leg-name', lambda c: c['legs'][1].update(name='rear'))
        add('same-leg-phase', lambda c: c['legs'][1].update(phase=0))
        add('fractional-body-offset', lambda c: c['body']['offsets_y'].__setitem__(1, 0.5))
        add('too-few-frames', lambda c: c.update(frame_count=3, durations_ms=[80] * 3))
        for name, config in cases:
            with self.subTest(case=name):
                out = self.run_cli(config, name=name, success=False)
                self.assert_no_partial_output(out)

    def test_opaque_input_and_motion_clipping_fail_atomically(self):
        for mode in ('RGB', 'RGBA'):
            with self.subTest(input_mode=mode):
                opaque = self.root / f'opaque-{mode}.png'
                solid = self.original.convert('RGB').convert(mode)
                solid.save(opaque)
                out = self.run_cli(name=f'opaque-{mode}', image=opaque, success=False)
                self.assert_no_partial_output(out)
        for name, mutate in (
            ('excessive-stride', lambda c: c['gait'].update(stride_px=400)),
            ('excessive-lift', lambda c: c['gait'].update(lift_px=160)),
            ('excessive-body-shift', lambda c: c['body'].update(offsets_y=[120] * 8)),
        ):
            with self.subTest(case=name):
                config = configuration()
                mutate(config)
                out = self.run_cli(config, name=name, success=False)
                self.assert_no_partial_output(out)

    def test_existing_nonempty_output_is_never_modified(self):
        out = self.run_cli()
        marker = out / 'keep-user-content.txt'
        marker.write_bytes(b'approved output must survive\x00\xff')
        before = {path.relative_to(out).as_posix(): path.read_bytes()
                  for path in out.rglob('*') if path.is_file()}
        self.run_cli(name='output', success=False)
        after = {path.relative_to(out).as_posix(): path.read_bytes()
                 for path in out.rglob('*') if path.is_file()}
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
