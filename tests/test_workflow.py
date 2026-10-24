"""Behavioral CLI regressions using small synthetic image fixtures only.

Run from any directory: python -m unittest discover -s tests -v
No model, network connection, or user artwork is needed.
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


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'sprite_workflow.py'
PADDING = (12, 16, 20, 24)
ANCHOR = [36, 56, 80, 91]
MOTION = [[[28, 20], [96, 20], [96, 51], [28, 51]]]
PROBE = {'name': 'fixed patterned feature', 'box': [80, 26, 92, 38], 'max_shift_px': 1.5}


def synthetic_source() -> Image.Image:
    image = Image.new('RGBA', (96, 80))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 4, 83, 34), fill=(78, 122, 56, 255))
    draw.rectangle((24, 35, 67, 67), fill=(32, 44, 54, 255))
    draw.rectangle((30, 66, 39, 78), fill=(20, 24, 31, 255))
    draw.rectangle((51, 66, 61, 78), fill=(20, 24, 31, 255))
    draw.rectangle((5, 44, 19, 50), fill=(111, 176, 203, 97))
    draw.rectangle((42, 40, 49, 45), fill=(220, 207, 93, 255))
    array = np.array(image)
    rng = np.random.default_rng(721)
    array[46:58, 30:44, :3] = rng.integers(25, 220, (12, 14, 3), dtype=np.uint8)
    array[46:58, 30:44, 3] = 255
    array[0, 0] = [117, 29, 101, 0]  # Hidden RGB must not be silently rewritten.
    feature = np.zeros((12, 12, 4), dtype=np.uint8)
    feature[:, :] = (78, 122, 56, 255)
    feature[2:10, 2:5] = (15, 26, 21, 255)
    feature[3:7, 7:10] = (244, 237, 199, 255)
    feature[8:10, 8:11] = (46, 60, 31, 255)
    array[10:22, 68:80] = feature
    return Image.fromarray(array)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sprite-workflow-tests-')
        self.root = Path(self.temp.name)
        self.source = self.root / 'private-character.png'
        self.original = synthetic_source()
        self.original.save(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                text=True, capture_output=True, encoding='utf-8')
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, 'Invalid operation unexpectedly succeeded.')
            self.assertTrue((result.stdout + result.stderr).strip())
            self.assertNotIn('Traceback (most recent call last)', result.stdout + result.stderr)
        return result

    def prepare(self, frames=6, loop='rest', name='run'):
        run = self.root / name
        self.cli('prepare', '--image', self.source, '--action', 'blink, then return to rest',
                 '--frames', frames, '--out', run, '--padding', *PADDING, '--fps', 8, '--loop', loop)
        config = json.loads((run/'run.json').read_text(encoding='utf-8'))
        config['registration_box'] = ANCHOR.copy()
        config['motion_regions'] = MOTION
        config['loop'] = {'mode': loop, 'rest_frame': frames-1 if loop == 'rest' else None}
        config['seam_probes'] = [dict(PROBE)]
        self.write_config(run, config)
        return run, config

    @staticmethod
    def write_config(run, config):
        (run/'run.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    def make_sheet(self, run, config, scale=1.0, shifts=True, probe_shift_frames=(), fill_unused=False):
        seed = Image.open(run/'seed-frame.png').convert('RGBA')
        width, height = seed.size
        cell_size = (round(width*scale), round(height*scale))
        sheet = Image.new('RGBA', (cell_size[0]*config['columns'], cell_size[1]*config['rows']))
        for i in range(config['frame_count']):
            frame = seed.copy()
            draw = ImageDraw.Draw(frame)
            # Generation deliberately disagrees with the original in a static part.
            draw.rectangle((17, 60, 31, 66), fill=(220, 73, 31, 255))
            # Distinct generated appearance, including the selected rest frame.
            draw.rectangle((50, 39, 67, 45), fill=(36+17*i, 47, 138, 255))
            if i+1 in probe_shift_frames:
                patch = frame.crop(PROBE['box'])
                draw.rectangle((80, 26, 95, 37), fill=(78, 122, 56, 255))
                frame.paste(patch, (84, 26))
            resized = frame.resize(cell_size, Image.Resampling.NEAREST)
            slot = Image.new('RGBA', cell_size)
            dx, dy = (((i % 3)-1)*2, (i % 2)*2) if shifts else (0, 0)
            slot.paste(resized, (dx, dy))
            sheet.paste(slot, ((i % config['columns'])*cell_size[0], (i//config['columns'])*cell_size[1]))
        if fill_unused:
            index = config['frame_count']
            self.assertLess(index, config['columns']*config['rows'])
            sheet.paste(seed.resize(cell_size, Image.Resampling.NEAREST),
                        ((index % config['columns'])*cell_size[0], (index//config['columns'])*cell_size[1]))
        path = run / 'generated-test-sheet.png'
        sheet.save(path)
        return path

    def finalize(self, run, sheet, out=None, success=True):
        out = out or run/'output'
        self.cli('finalize', '--run', run, '--sheet', sheet, '--out', out, success=success)
        if not success:
            self.assertFalse(out.exists() and any(out.iterdir()), 'Failed operation left partial outputs.')
            return None
        report = json.loads((out/'validation.json').read_text(encoding='utf-8'))
        return out, report

    def assert_static_exact(self, run, out):
        base = np.array(Image.open(run/'seed-frame.png').convert('RGBA'))
        region = Image.new('L', (base.shape[1], base.shape[0]))
        for polygon in MOTION:
            ImageDraw.Draw(region).polygon([tuple(p) for p in polygon], fill=255)
        static = np.array(region) == 0
        for path in sorted((out/'frames').glob('*.png')):
            frame = np.array(Image.open(path).convert('RGBA'))
            self.assertTrue(np.array_equal(frame[static], base[static]), path.name)
        # The fixture exercises semitransparent static pixels, not just opaque rectangles.
        self.assertTrue(np.any((base[:, :, 3][static] > 0) & (base[:, :, 3][static] < 255)))

    def test_prepare_non_square_source_exact_rgba_and_arbitrary_counts(self):
        for count in (6, 7, 12):
            with self.subTest(frame_count=count):
                run, config = self.prepare(count, name=f'run-{count}')
                saved = np.array(Image.open(run/'source.png').convert('RGBA'))
                self.assertTrue(np.array_equal(saved, np.array(self.original)))
                seed = Image.open(run/'seed-frame.png').convert('RGBA')
                self.assertEqual(seed.size, (128, 120))
                cropped = np.array(seed.crop((12, 16, 108, 96)))
                self.assertTrue(np.array_equal(cropped, np.array(self.original)))
                self.assertEqual(config['frame_count'], count)
                self.assertGreaterEqual(config['columns']*config['rows'], count)
                with Image.open(run/'reference-canvas.png') as canvas:
                    self.assertEqual(canvas.size, (128*config['columns'], 120*config['rows']))
                self.assertTrue((run/'prompt.txt').read_text(encoding='utf-8').strip())

    def test_prepare_rejects_missing_input_and_existing_nonempty_directory(self):
        missing = self.root/'missing-output'
        self.cli('prepare','--image',self.root/'missing.png','--action','blink','--frames',6,'--out',missing,success=False)
        self.assertFalse(missing.exists() and any(missing.iterdir()))
        run, _ = self.prepare()
        marker = run/'keep.txt'
        marker.write_text('existing content',encoding='utf-8')
        self.cli('prepare','--image',self.source,'--action','blink','--frames',6,'--out',run,success=False)
        self.assertEqual(marker.read_text(encoding='utf-8'),'existing content')

    def test_invalid_config_rejected_before_outputs(self):
        cases = [
            ('missing-anchor', {'registration_box': None}),
            ('missing-motion', {'motion_regions': []}),
            ('outside-anchor', {'registration_box': [0, 0, 900, 900]}),
            ('outside-motion', {'motion_regions': [[[0, 0], [999, 0], [999, 999]]]}),
            ('overlap', {'registration_box': [35, 24, 60, 40]}),
            ('low-texture', {'registration_box': [0, 0, 10, 10]}),
            ('bad-rest', {'loop': {'mode': 'rest', 'rest_frame': 100}}),
        ]
        for name, edit in cases:
            with self.subTest(case=name):
                run, config = self.prepare(name=name)
                sheet = self.make_sheet(run, config)
                config.update(edit)
                self.write_config(run, config)
                self.finalize(run, sheet, success=False)

    def test_blank_and_opaque_sheet_rejected(self):
        for kind in ('blank', 'opaque'):
            with self.subTest(kind=kind):
                run, config = self.prepare(name=kind)
                sheet = self.make_sheet(run, config)
                with Image.open(sheet) as loaded:
                    image = loaded.copy()
                if kind == 'blank':
                    image = Image.new('RGBA', image.size)
                else:
                    image.putalpha(255)
                image.save(sheet)
                self.finalize(run, sheet, success=False)

    def test_unused_grid_cell_with_extra_character_rejected(self):
        run, config = self.prepare(7)
        sheet = self.make_sheet(run, config, fill_unused=True)
        self.finalize(run, sheet, success=False)

    def test_prime_frame_count_exports_exact_count_and_keeps_unused_cells_empty(self):
        run, config = self.prepare(7, loop='cycle')
        sheet = self.make_sheet(run, config, shifts=False)
        out, report = self.finalize(run, sheet)
        self.assertEqual(report['frame_count'], 7)
        self.assertEqual(len(list((out/'frames').glob('*.png'))), 7)
        with Image.open(out/'spritesheet.png') as exported:
            for index in range(7, config['columns']*config['rows']):
                left=(index % config['columns'])*128
                top=(index // config['columns'])*120
                self.assertIsNone(exported.crop((left,top,left+128,top+120)).getchannel('A').getbbox())

    def test_common_scale_registration_and_fixed_rgba_preservation(self):
        run, config = self.prepare()
        sheet = self.make_sheet(run, config, scale=1.25)
        out, report = self.finalize(run, sheet)
        self.assertEqual(len(list((out/'frames').glob('*.png'))),6)
        self.assertAlmostEqual(report['common_raw_to_output_scale'], 0.8, delta=0.07)
        self.assertEqual(len(report['registrations']),6)
        self.assertEqual(max(report['static_pixel_differences']),0)
        self.assert_static_exact(run,out)

    def test_rest_uses_generated_pose_without_external_base_snap_and_keeps_timing(self):
        run, config = self.prepare()
        sheet = self.make_sheet(run, config, shifts=False)
        out, report = self.finalize(run, sheet)
        frames = [np.array(Image.open(p).convert('RGBA')) for p in sorted((out/'frames').glob('*.png'))]
        seed = np.array(Image.open(run/'seed-frame.png').convert('RGBA'))
        self.assertTrue(np.array_equal(frames[0],frames[-1]))
        self.assertTrue(np.array_equal(frames[0],frames[4]))
        self.assertGreater(np.any(frames[0] != seed,axis=2).sum(),20)
        for i in range(1,5):
            registered = np.array(Image.open(out/'registered'/f'{i+1:02d}.png').convert('RGBA'))
            # Central dynamic block avoids mask-boundary/threshold ambiguity.
            self.assertTrue(np.array_equal(frames[i][40:44,52:65],registered[40:44,52:65]))
        with Image.open(out/'preview.gif') as gif:
            duration=0
            dynamic=Image.new('L',gif.size)
            ImageDraw.Draw(dynamic).polygon([tuple(p) for p in MOTION[0]],fill=255)
            static=np.array(dynamic)==0
            first_static=None
            for i in range(gif.n_frames):
                gif.seek(i)
                duration+=gif.info.get('duration',0)
                rgb=np.array(gif.convert('RGB'))
                if first_static is None:
                    first_static=rgb[static].copy()
                else:
                    self.assertTrue(np.array_equal(rgb[static],first_static),
                                    'GIF palette/disposal changed fixed-region pixels.')
            self.assertEqual(duration,sum(config['durations_ms']))
            self.assertEqual(gif.n_frames,report['gif_encoded_frames'])
        self.assertEqual(report['gif_duration_ms'],sum(config['durations_ms']))
        self.assert_static_exact(run,out)

    def test_cycle_preserves_both_generated_endpoints(self):
        run, config = self.prepare(loop='cycle')
        sheet = self.make_sheet(run, config, shifts=False)
        out, report = self.finalize(run, sheet)
        first=np.array(Image.open(out/'frames'/'01.png'))
        last=np.array(Image.open(out/'frames'/'06.png'))
        self.assertFalse(np.array_equal(first,last))
        for index in (1,6):
            final=np.array(Image.open(out/'frames'/f'{index:02d}.png'))
            registered=np.array(Image.open(out/'registered'/f'{index:02d}.png'))
            self.assertTrue(np.array_equal(final[40:44,52:65],registered[40:44,52:65]))

    def test_none_mode_does_not_encode_a_repeating_gif(self):
        run, config = self.prepare(loop='none')
        sheet = self.make_sheet(run, config, shifts=False)
        out, report = self.finalize(run, sheet)
        self.assertEqual(report['seam']['status'], 'not_applicable')
        self.assertIsNone(report['gif_loop'])
        with Image.open(out/'preview.gif') as gif:
            self.assertNotIn('loop', gif.info)
        first=np.array(Image.open(out/'frames'/'01.png'))
        last=np.array(Image.open(out/'frames'/'06.png'))
        self.assertFalse(np.array_equal(first,last))

    def test_loop_probes_catch_jump_even_when_first_and_last_are_identical(self):
        for shifted_frame in (2,5):
            with self.subTest(shifted_frame=shifted_frame):
                run, config = self.prepare(name=f'jump-{shifted_frame}')
                config['loop']['rest_frame']=3
                self.write_config(run,config)
                sheet=self.make_sheet(run,config,shifts=False,probe_shift_frames=(shifted_frame,))
                out,report=self.finalize(run,sheet)
                self.assertTrue(report['first_last_identical'])
                self.assertEqual(report['seam']['status'],'needs_review',report['seam'])
                self.assertTrue(any(c['status']=='needs_review' for c in report['seam']['checks']))

    def test_rest_hold_boundaries_expose_hidden_jump_without_policing_mid_action(self):
        # Identical holds surround the action: [rest, rest, rest, 4, 5, 6, 7,
        # rest, rest, rest]. The old 9->10, 10->1, 1->2 checks all look smooth.
        cases = [('head', 4, (3, 4)), ('tail', 7, (7, 8)), ('middle', 5, None)]
        for name, shifted_frame, expected_edge in cases:
            with self.subTest(hold_boundary=name):
                run, config = self.prepare(10, name=f'hold-{name}')
                sheet_path = self.make_sheet(run, config, shifts=False,
                                             probe_shift_frames=(shifted_frame,))
                with Image.open(sheet_path) as opened:
                    sheet = opened.copy()
                width, height = config['frame_size']
                rest_index = config['loop']['rest_frame'] - 1
                left=(rest_index % config['columns'])*width
                top=(rest_index // config['columns'])*height
                rest=sheet.crop((left,top,left+width,top+height))
                for frame_number in (2,3,8):
                    index=frame_number-1
                    sheet.paste(rest,((index % config['columns'])*width,
                                      (index // config['columns'])*height))
                sheet.save(sheet_path)
                out, report = self.finalize(run, sheet_path)
                frames = [np.array(Image.open(out/'frames'/f'{i:02d}.png')) for i in range(1,11)]
                for index in (1,2,7,8,9):
                    self.assertTrue(np.array_equal(frames[0],frames[index]))
                checks={(check['from'],check['to']):check for check in report['seam']['checks']}
                for old_edge in ((9,10),(10,1),(1,2)):
                    self.assertEqual(checks[old_edge]['status'],'verified',checks)
                if expected_edge is not None:
                    self.assertEqual(report['seam']['status'],'needs_review',report['seam'])
                    self.assertIn(expected_edge,checks)
                    self.assertEqual(checks[expected_edge]['status'],'needs_review',checks)
                    self.assertGreaterEqual(abs(checks[expected_edge]['dx']),3)
                else:
                    # A purposeful feature movement inside the action is not a loop seam.
                    self.assertEqual(report['seam']['status'],'verified',report['seam'])
                    self.assertNotIn((4,5),checks)
                    self.assertNotIn((5,6),checks)

    def test_no_probe_and_textureless_probe_cannot_claim_verified_seam(self):
        cases = [('none', []),
                 ('flat', [{'name':'flat green','box':[31,22,36,27],'max_shift_px':1.5}]),
                 ('copied-static', [{'name':'copied torso','box':ANCHOR,'max_shift_px':1.5}])]
        for name, probes in cases:
            with self.subTest(probes=name):
                run,config=self.prepare(name=name)
                config['seam_probes']=probes
                self.write_config(run,config)
                sheet=self.make_sheet(run,config,shifts=False)
                _,report=self.finalize(run,sheet)
                self.assertEqual(report['seam']['status'],'unverified',report['seam'])

    def test_result_archive_is_readable_and_does_not_include_private_inputs(self):
        run,config=self.prepare()
        sheet=self.make_sheet(run,config,shifts=False)
        out,report=self.finalize(run,sheet)
        with ZipFile(out/'result.zip') as archive:
            self.assertIsNone(archive.testzip())
            names=archive.namelist()
            self.assertEqual(sum(n.startswith('frames/') and n.endswith('.png') for n in names),6)
            for name in names:
                self.assertFalse(name.startswith(('/', '\\')))
                self.assertNotIn(':',name)
                self.assertNotIn('..',Path(name).parts)
                self.assertNotIn('raw',name.lower())
                self.assertNotIn('source.png',name.lower())
                self.assertNotIn('private-character',name)
                if name.endswith(('.json','.txt','.md')):
                    content=archive.read(name).decode('utf-8')
                    self.assertNotIn(str(self.root),content)
                    if name.endswith('.json'):
                        pending=[json.loads(content)]
                        while pending:
                            value=pending.pop()
                            if isinstance(value,dict):
                                pending.extend(value.values())
                            elif isinstance(value,list):
                                pending.extend(value)
                            elif isinstance(value,str):
                                self.assertNotIn(str(self.root),value)
                                self.assertNotIn('private-character.png',value)


if __name__ == '__main__':
    unittest.main()
