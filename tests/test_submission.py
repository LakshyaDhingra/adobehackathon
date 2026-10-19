"""Release-critical checks beyond layout scoring: real artifacts and failures."""
from __future__ import annotations
import copy
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image
from engine.contract import contract_problems, validate_design
from engine.main import main, render_to_png
from engine.resize import resize
from engine.qa import audit
from renderer.converter.convert import convert_chameleon_to_crello
from engine.typography import get_font_manager
from studio.app import create_server

ROOT = Path(__file__).resolve().parent.parent
TASKS = [json.loads(p.read_text()) for p in sorted((ROOT/'input').glob('*.json'))]


class TestReleaseContract(unittest.TestCase):
    def test_all_official_outputs_obey_schema_and_content_contract(self):
        for task in TASKS:
            out=resize(task['source'],task['target_canvas'])
            self.assertEqual(contract_problems(task['source'],out,task['target_canvas']),[])
            self.assertEqual(audit(task['source'],out)['issues'],[])
        # Prevent the 5–6 px advanced-banner regression.
        advanced=resize(TASKS[2]['source'],TASKS[2]['target_canvas'])
        self.assertGreaterEqual(min(e['font_size'] for e in advanced['elements'] if e['type']=='text'),7)

    def test_actual_large_png_dimensions(self):
        target={'width':2480,'height':3508}
        out=resize(TASKS[0]['source'],target)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'poster.png'
            render_to_png(out,ROOT/'brand_kit/assets',path)
            with Image.open(path) as im:self.assertEqual(im.size,(2480,3508))

    def test_style_runs_are_end_exclusive_after_wrapping(self):
        element={'id':'headline','type':'text','x':0,'y':0,'width':70,'height':100,'content':'One two three',
                 'font_family':'Solstice Sans','font_size':20,'color':'rgba(255,255,255,1)',
                 'style_runs':[{'start':4,'end':7,'color':'rgba(255,0,0,1)'}]}
        converted,_=convert_chameleon_to_crello({'canvas':{'width':300,'height':300},'elements':[element]},get_font_manager())
        self.assertEqual(len(converted['text'][0]),len(element['content']))
        self.assertEqual(converted['text_color'][0][4:7],['rgba(255,0,0,1)']*3)
        self.assertEqual(converted['text_color'][0][7],element['color'])

    def test_rejects_duplicate_ids_nan_and_invalid_canvases(self):
        bad=copy.deepcopy(TASKS[0]['source']);bad['elements'].append(copy.deepcopy(bad['elements'][0]))
        with self.assertRaises(ValueError):validate_design(bad)
        bad=copy.deepcopy(TASKS[0]['source']);bad['elements'][0]['x']=float('nan')
        with self.assertRaises(ValueError):validate_design(bad)
        for target in [{'width':0,'height':200},{'width':100.5,'height':200},{'width':True,'height':200}]:
            with self.assertRaises(ValueError):resize(TASKS[0]['source'],target)

    def test_failed_task_does_not_stop_batch_or_leave_stale_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs,outputs=Path(tmp)/'input',Path(tmp)/'output';inputs.mkdir();outputs.mkdir()
            (inputs/'broken.json').write_text('{}')
            (inputs/'valid.json').write_text(json.dumps(TASKS[0]))
            (outputs/'broken_output.json').write_text('{}')
            (outputs/'broken_output.png').write_bytes(b'stale')
            self.assertEqual(main(inputs,outputs),1)
            self.assertFalse((outputs/'broken_output.json').exists())
            self.assertFalse((outputs/'broken_output.png').exists())
            self.assertTrue((outputs/'valid_output.png').exists())

    def test_renderer_failure_is_a_failed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs,outputs=Path(tmp)/'in',Path(tmp)/'out';inputs.mkdir()
            (inputs/'valid.json').write_text(json.dumps(TASKS[0]))
            with mock.patch('engine.main.render_to_png',side_effect=ImportError('renderer missing')):
                self.assertEqual(main(inputs,outputs),1)
            self.assertEqual(list(outputs.iterdir()),[])

    def test_rotated_fallback_and_contain_images_stay_in_bounds(self):
        source=copy.deepcopy(TASKS[0]['source'])
        image=next(e for e in source['elements'] if e['type']=='image')
        image.update(fit='contain',rotation=45,x=-100,y=-100)
        with mock.patch('engine.resize._LAYOUTS',{'vertical':mock.Mock(side_effect=RuntimeError('forced'))}):
            out=resize(source,{'width':80,'height':240})
        self.assertEqual(contract_problems(source,out,{'width':80,'height':240}),[])


class TestStudioHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=create_server(0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def request(self,path,payload,headers=None):
        data=json.dumps(payload).encode()
        return urllib.request.urlopen(urllib.request.Request(self.url+path,data=data,headers={'Content-Type':'application/json',**(headers or {})}))

    def test_studio_manifest_and_schema_are_served(self):
        with urllib.request.urlopen(self.url+'/manifest.json') as r:
            self.assertEqual(json.load(r)['entryPoints'][0]['documentSandbox'],'code.js')
        with urllib.request.urlopen(self.url+'/api/schema') as r:self.assertIn('$defs',json.load(r))

    def test_advanced_request_keeps_raw_content_and_reports_missing_asset(self):
        t=TASKS[2]
        with self.request('/api/resize',{'source':t['source'],'target_canvas':t['target_canvas']}) as response:
            r=json.load(response)
        self.assertEqual(contract_problems(t['source'],r['output'],t['target_canvas']),[])
        self.assertEqual(r['quality']['issues'],[])
        self.assertTrue(any('holiday' in w for w in r['warnings']))

    def test_batch_archive_contains_correct_sized_real_pngs_and_json(self):
        targets=[{'width':300,'height':250},{'width':728,'height':90}]
        with self.request('/api/batch',{'source':TASKS[0]['source'],'targets':targets}) as r:
            archive=zipfile.ZipFile(io.BytesIO(r.read()))
        self.assertIn('quality-report.json',archive.namelist())
        for i,target in enumerate(targets):
            stem=f'{i+1:02d}_{target["width"]}x{target["height"]}'
            out=json.loads(archive.read(stem+'.json'))
            self.assertEqual(contract_problems(TASKS[0]['source'],out,target),[])
            with Image.open(io.BytesIO(archive.read(stem+'.png'))) as im:self.assertEqual(im.size,tuple(target.values()))

    def test_invalid_input_and_cross_origin_requests_fail_without_killing_server(self):
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/resize',{})
        self.assertEqual(error.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as error:self.request('/api/resize',{}, {'Origin':'https://example.com'})
        self.assertEqual(error.exception.code,403)
        with urllib.request.urlopen(self.url+'/api/health') as r:self.assertEqual(json.load(r)['status'],'ok')

    def test_asset_paths_cannot_escape_upload_directory(self):
        t=TASKS[0]
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/resize',{'source':t['source'],'target_canvas':t['target_canvas'],'assets':{'../escape.png':'aGVsbG8='}})
        self.assertEqual(error.exception.code,400)


class TestTextFidelity(unittest.TestCase):
    def test_measured_mixed_styles_match_real_renderer_spans(self):
        from cr_renderer.schema import TextElement
        from cr_renderer import text_utils
        from engine.typography import measure_text_block
        e={'id':'styled','type':'text','x':0,'y':0,'width':210,'height':150,'content':'Regular bold words and more text',
           'font_family':'Solstice Sans','font_size':22,'color':'rgba(255,255,255,1)','letter_spacing':1.5,
           'style_runs':[{'start':8,'end':18,'font_weight':'bold','font_style':'italic','color':'rgba(255,0,0,1)'}]}
        fm=get_font_manager()
        out,_=convert_chameleon_to_crello({'canvas':{'width':300,'height':300},'elements':[e]},fm)
        real=TextElement(uuid='',type='textElement',width=e['width'],height=e['height'],text=out['text'][0],
            fontSize=e['font_size'],font=e['font_family'],lineHeight=1.2,textAlign='left',capitalize=False,letterSpacing=1.5,
            boldMap=text_utils.generate_map(out['font_bold'][0]),italicMap=text_utils.generate_map(out['font_italic'][0]),
            colorMap=text_utils.generate_map(out['text_color'][0]),lineMap=text_utils.generate_map(out['text_line'][0]))
        actual=max(sum(text_utils.make_text_blob(real,span,fm).width for span in line) for line in text_utils.make_text_spans(real))
        self.assertAlmostEqual(measure_text_block(e,22,e['width']).max_line_width,actual,places=4)

    def test_micro_banner_cards_do_not_spill_text_past_the_canvas(self):
        from fuzz_designs import random_design
        for seed in (9,13,15,26,39,54):
            source=random_design(seed)
            report=audit(source,resize(source,{'width':320,'height':50}))
            self.assertEqual(report['text_overflows'],[])
            self.assertFalse(any(i['code']=='text_ink_off_canvas' for i in report['issues']))
            self.assertFalse(any(i['code']=='label_off_shape' for i in report['issues']))


class TestRendererAssetFidelity(unittest.TestCase):
    def test_contain_preserves_semitransparent_asset_pixels(self):
        from renderer.converter.assets import _fit_image
        original=Image.new('RGBA',(2,2),(210,30,70,128))
        contained=_fit_image(original,4,2,'contain')
        self.assertEqual(contained.getpixel((1,0)),(210,30,70,128))
        self.assertEqual(contained.getpixel((0,0)),(0,0,0,0))
