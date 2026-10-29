#!/usr/bin/env python3
"""Bundle explicit source directories, including new files not yet committed."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent.parent
DEST=ROOT/'artifacts';DEST.mkdir(exist_ok=True)
INCLUDE=['engine','renderer','brand_kit','input','studio','express-addon/src','tests','scripts','docs','.github',
         'ai','README.md','FAQ.md','RULES.md','Dockerfile','.dockerignore','.gitignore','requirements.txt','requirements.lock',
         'chameleon_design_schema_v0.9.json','express-addon/package.json','express-addon/package-lock.json','express-addon/README.md']


def package():
    files=[]
    for name in INCLUDE:
        path=ROOT/name
        files.extend([p for p in path.rglob('*') if p.is_file()] if path.is_dir() else [path])
    files=sorted(set(p for p in files if '__pycache__' not in p.parts and not p.name.endswith('.pyc')))
    for p in files:
        if not p.is_file():raise RuntimeError(f'Missing submission file: {p}')
    manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with zipfile.ZipFile(DEST/'chameleon-submission.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in files:z.write(p,p.relative_to(ROOT))
        z.writestr('output/.gitkeep','')
        z.writestr('submission-files.json',json.dumps(manifest,indent=2)+'\n')
        for folder in ['verification']:
            path=DEST/folder
            if path.is_dir():
                for p in path.rglob('*'):
                    if p.is_file() and p.suffix in ('.json','.log','.md'):z.write(p,'evidence/'+str(p.relative_to(DEST)))
        if (DEST/'chameleon-demo.mp4').is_file():z.write(DEST/'chameleon-demo.mp4','demo/chameleon-demo.mp4')
    with zipfile.ZipFile(DEST/'chameleon-express-addon.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in (ROOT/'express-addon/src').iterdir():
            if p.is_file():z.write(p,p.name)
    print(f'Created {DEST/"chameleon-submission.zip"} ({len(files)} source files)')
    print(f'Created {DEST/"chameleon-express-addon.zip"}')

if __name__=='__main__':package()
