#!/usr/bin/env python3
"""Build and run linux/amd64 offline; verify every emitted JSON and PNG on host."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from PIL import Image
from engine.contract import contract_problems
from engine.qa import audit


def verify(docker: str, output: Path, work: Path | None = None, qemu_guest_base: str | None = None) -> None:
    if not shutil.which(docker) and not Path(docker).is_file():raise SystemExit('Docker CLI is required; install Docker Desktop or use --docker /path/to/docker.')
    output.mkdir(parents=True,exist_ok=True)
    image='chameleon:verification'
    with (output/'docker-build.log').open('w') as log:
        subprocess.run([docker,'build','--platform','linux/amd64','-t',image,str(ROOT)],stdout=log,stderr=subprocess.STDOUT,check=True)
    with tempfile.TemporaryDirectory(prefix='chameleon-',dir=work) as tmp:
        tmp=Path(tmp);inputs,outputs=tmp/'input',tmp/'output';inputs.mkdir();outputs.mkdir()
        for task in (ROOT/'input').glob('*.json'):shutil.copyfile(task,inputs/task.name)
        poster=json.loads(next((ROOT/'input').glob('*level2*')).read_text());poster['target_canvas']={'width':2480,'height':3508}
        (inputs/'large_poster.json').write_text(json.dumps(poster))
        commands=[docker,'run','--rm','--platform','linux/amd64','--network','none','--read-only',
                  '--tmpfs','/tmp:rw,size=256m','-v',f'{inputs}:/app/input:ro','-v',f'{outputs}:/app/output']
        if qemu_guest_base:
            commands.extend(['-e',f'QEMU_GUEST_BASE={qemu_guest_base}'])
        commands.append(image)
        with (output/'docker-run.log').open('w') as log:subprocess.run(commands,stdout=log,stderr=subprocess.STDOUT,check=True)
        results=[]
        for path in sorted(inputs.glob('*.json')):
            task=json.loads(path.read_text());stem=path.stem+'_output';out=json.loads((outputs/(stem+'.json')).read_text())
            problems=contract_problems(task['source'],out,task['target_canvas'])
            if problems:raise RuntimeError(f'{path.name}: {problems}')
            with Image.open(outputs/(stem+'.png')) as im:
                if im.size!=tuple(task['target_canvas'][k] for k in ('width','height')):raise RuntimeError('PNG dimensions mismatch')
            results.append({'task':path.name,'canvas':out['canvas'],'audit':audit(task['source'],out)})
        dest=output/'docker-outputs'
        if dest.exists():shutil.rmtree(dest)
        shutil.copytree(outputs,dest)
    info=json.loads(subprocess.check_output([docker,'image','inspect',image]))[0]
    report={'passed':True,'platform':f'{info["Os"]}/{info["Architecture"]}','image_id':info['Id'],
            'network':'none','root_filesystem':'read-only','emulator_guest_base':qemu_guest_base,'cases':results}
    (output/'docker-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Docker verification passed: linux/amd64, no network, read-only image, 3 samples and a full-size poster.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker',default='docker')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts'/'verification')
    parser.add_argument('--work-dir',type=Path,help='For VM Docker daemons: a directory shared into the VM')
    parser.add_argument('--qemu-guest-base',help='Optional emulator-only address mapping; not needed on native amd64')
    args=parser.parse_args();verify(args.docker,args.output.resolve(),args.work_dir,args.qemu_guest_base)
