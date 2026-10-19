"""Local Chameleon studio and Adobe Express add-on backend. No cloud inference."""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import mimetypes
import shutil
import ssl
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from engine.analyzer import analyze_design
from engine.contract import contract_problems, validate_canvas, validate_design
from engine.qa import audit
from engine.resize import resize
from renderer.converter.convert import render_design_to_png

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'brand_kit' / 'assets'
PUBLIC = ROOT / 'express-addon' / 'src'
RENDER_LOCK = threading.RLock()
MAX_BODY = 24 * 1024 * 1024
MAX_PIXELS = 20_000_000


def _render(design: dict, assets: Path) -> tuple[str, list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'design.png'
        warnings = render_design_to_png(design, assets, path, fonts_dir=ROOT / 'brand_kit' / 'fonts')
        return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode(), warnings


def _validate_request(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError('Request must be a JSON object')
    validate_design(payload['source'])
    validate_canvas(payload['target_canvas'])
    for canvas in (payload['source']['canvas'], payload['target_canvas']):
        if max(canvas.values()) > 8192 or canvas['width'] * canvas['height'] > MAX_PIXELS:
            raise ValueError('Studio supports up to 8192 px per side and 20 megapixels')
    if len(payload['source']['elements']) > 300:
        raise ValueError('Studio supports up to 300 elements per design')
    if sum(len(e.get('content', '')) for e in payload['source']['elements']) > 50000:
        raise ValueError('Design contains too much text for an interactive preview')
    if not isinstance(payload.get('assets', {}), dict):
        raise ValueError('Assets must be a map of filenames to base64 image data')


def _load_assets(uploads: dict, directory: Path) -> None:
    for p in ASSETS.iterdir():
        if p.is_file():
            shutil.copyfile(p, directory / p.name)
    for name, encoded in uploads.items():
        if not isinstance(name, str) or name != Path(name).name or name in ('.', '..') or '\\' in name:
            raise ValueError('Asset filenames cannot contain paths')
        if not isinstance(encoded, str):
            raise ValueError('Asset data must be base64')
        data = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ('PNG', 'JPEG', 'WEBP') or im.width * im.height > MAX_PIXELS:
                raise ValueError('Assets must be PNG, JPEG or WebP under 20 megapixels')
            im.verify()
        (directory / name).write_bytes(data)


def process_resize(payload: dict) -> dict:
    _validate_request(payload)
    source, target = payload['source'], payload['target_canvas']
    with tempfile.TemporaryDirectory() as tmp, RENDER_LOCK:
        assets = Path(tmp)
        _load_assets(payload.get('assets', {}), assets)
        start = time.perf_counter()
        output = resize(source, target, mode=payload.get('mode', 'auto'), assets_dir=assets)
        elapsed = (time.perf_counter() - start) * 1000
        problems = contract_problems(source, output, target)
        if problems:
            raise ValueError('Output contract failed: ' + '; '.join(problems))
        png, warnings = _render(output, assets)
        source_png, source_warnings = _render(source, assets)
        ad = analyze_design(source, assets)
        roles = {}
        for name, elements in [('background', ad.backgrounds), ('image', ad.hero_images), ('footer', ad.watermarks),
                               ('body', ad.subheads), ('legal', ad.legal_texts), ('decoration', ad.decoratives)]:
            roles.update({e['id']: name for e in elements})
        for name, e in [('headline', ad.headline), ('logo', ad.logo), ('call to action', ad.cta_text)]:
            if e:
                roles[e['id']] = name
        return {'output': output, 'quality': audit(source, output, assets), 'problems': problems,
                'elapsed_ms': round(elapsed, 1), 'output_png': png, 'source_png': source_png,
                'warnings': list(dict.fromkeys(warnings + source_warnings)), 'roles': roles}


class StudioHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logging.getLogger('studio').info(fmt, *args)

    def _send(self, code: int, body: bytes, kind='application/json', headers=None):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, allow_nan=False).encode())

    def do_GET(self):
        path = urlsplit(self.path).path
        manifest = json.loads((PUBLIC / 'manifest.json').read_text())
        addon_id = manifest['testId']
        if getattr(self.server, 'addon_mode', False):
            scheme = 'https' if isinstance(self.connection, ssl.SSLSocket) else 'http'
            base = f"{scheme}://{self.headers.get('Host')}"
            if path == '/':
                return self._json(200, {'addOns': [{
                    'addonId': addon_id, 'versionString': manifest['version'],
                    'supportedLanguages': ['en-US'], 'supportedApps': ['Express'],
                    'downloadUrl': f'{base}/{addon_id}/manifest.json',
                    'addon': {'localizedMetadata': {'name': manifest['name']}},
                    'entryPoints': [{**e, 'discoverable': True} for e in manifest['entryPoints']]
                }]})
            if path == f'/{addon_id}':
                resources = [f'{base}/{addon_id}/{p.name}' for p in PUBLIC.iterdir() if p.is_file()]
                return self._json(200, {'resources': resources})
        if path.startswith(f'/{addon_id}/'):
            path = path[len(addon_id)+1:]
        if path == '/studio':
            path = '/index.html'
        if path == '/api/health':
            return self._json(200, {'status': 'ok', 'engine': 'chameleon', 'scoring_mode': 'preserve-all-content'})
        if path == '/api/tasks':
            return self._json(200, [json.loads(p.read_text()) for p in sorted((ROOT / 'input').glob('*.json'))])
        if path == '/api/schema':
            return self._send(200, (ROOT / 'chameleon_design_schema_v0.9.json').read_bytes())
        # Serve a fixed allowlist. No arbitrary filesystem access through URLs.
        filename = {'/': 'index.html', '/index.html': 'index.html', '/manifest.json': 'manifest.json',
                    '/app.js': 'app.js', '/styles.css': 'styles.css', '/code.js': 'code.js',
                    '/host.js': 'host.js'}.get(path)
        if filename:
            file = PUBLIC / filename
            return self._send(200, file.read_bytes(), mimetypes.guess_type(filename)[0] or 'application/octet-stream')
        self._json(404, {'error': 'Not found'})

    def do_POST(self):
        try:
            # Same-origin UI requests only. The Express panel is served from
            # this server too, so no permissive cross-origin API is necessary.
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                return self._json(403, {'error': 'Use the studio from this server origin'})
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                return self._json(415, {'error': 'Content-Type must be application/json'})
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > MAX_BODY:
                return self._json(413, {'error': 'Request must be between 1 byte and 24 MB'})
            payload = json.loads(self.rfile.read(length))
            path = urlsplit(self.path).path
            if path == '/api/resize':
                return self._json(200, process_resize(payload))
            if path == '/api/batch':
                targets = payload.get('targets', [])
                if not isinstance(targets, list) or not 1 <= len(targets) <= 12:
                    raise ValueError('Choose between 1 and 12 canvases')
                archive = io.BytesIO()
                reports = []
                with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                    for i, target in enumerate(targets):
                        result = process_resize({**payload, 'target_canvas': target})
                        stem = f'{i+1:02d}_{target["width"]}x{target["height"]}'
                        z.writestr(stem + '.json', json.dumps(result['output'], indent=2))
                        z.writestr(stem + '.png', base64.b64decode(result['output_png'].split(',')[1]))
                        reports.append({'canvas': target, 'local_quality': result['quality'], 'warnings': result['warnings']})
                    z.writestr('quality-report.json', json.dumps(reports, indent=2))
                return self._send(200, archive.getvalue(), 'application/zip', {'Content-Disposition': 'attachment; filename="chameleon-campaign.zip"'})
            return self._json(404, {'error': 'Not found'})
        except (ValueError, TypeError, KeyError, OSError) as exc:
            self._json(400, {'error': str(exc)})
        except Exception:
            logging.exception('Resize request failed')
            self._json(500, {'error': 'The preview could not be generated. Check the server log.'})


def create_server(port=5050):
    return ThreadingHTTPServer(('127.0.0.1', port), StudioHandler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5050)
    parser.add_argument('--cert', type=Path)
    parser.add_argument('--key', type=Path)
    parser.add_argument('--addon', action='store_true', help='Serve Adobe developer discovery at /; studio remains at /studio')
    args = parser.parse_args()
    if bool(args.cert) != bool(args.key):
        parser.error('--cert and --key must be supplied together')
    server = create_server(args.port)
    server.addon_mode = args.addon
    scheme = 'http'
    if args.cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(args.cert), str(args.key))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = 'https'
    print(f'Chameleon studio: {scheme}://localhost:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
