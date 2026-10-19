#!/usr/bin/env python3
"""Verify samples/catalogue and write an honest, machine-readable release report."""
from __future__ import annotations
import argparse
import json
import logging
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from engine.contract import contract_problems
from engine.main import main
from engine.qa import audit
from engine.resize import resize

CATALOGUE=[(1080,1080),(1080,1350),(1080,1920),(1200,630),(820,312),(1600,900),(1500,500),
           (1584,396),(1280,720),(2560,1440),(1000,1500),(728,90),(300,250),(160,600),
           (300,600),(970,250),(320,50),(600,200),(2480,3508),(1920,1080)]


def verify(output_dir: Path, seeds: int = 40) -> dict:
    logging.getLogger('engine.resize').setLevel(logging.CRITICAL)
    output_dir.mkdir(parents=True,exist_ok=True)
    if main(ROOT/'input',output_dir/'samples'):
        raise RuntimeError('Official sample pipeline failed')
    sys.path.insert(0,str(ROOT/'tests'))
    from fuzz_designs import random_design
    tasks=[json.loads(p.read_text()) for p in sorted((ROOT/'input').glob('*.json'))]
    sources=[(t.get('task_id','sample'),t['source']) for t in tasks]
    sources.extend((f'random-{i}',random_design(i)) for i in range(seeds))
    hard,quality,latencies,scores=[],[],[],[]
    for name,source in sources:
        for w,h in CATALOGUE:
            t=time.perf_counter();out=resize(source,{'width':w,'height':h});latencies.append((time.perf_counter()-t)*1000)
            problems=contract_problems(source,out,{'width':w,'height':h})
            if problems:hard.append({'design':name,'canvas':[w,h],'problems':problems})
            report=audit(source,out);scores.append(report['score'])
            if report['issues']:quality.append({'design':name,'canvas':[w,h],'local_score':report['score'],'issues':report['issues']})
    sample_reports=[]
    for task in tasks:
        out=resize(task['source'],task['target_canvas'])
        sample_reports.append({'task':task['task_id'],'target':task['target_canvas'],'audit':audit(task['source'],out),
            'minimum_text_px':min((e['font_size'] for e in out['elements'] if e['type']=='text'),default=None)})
    sorted_latency=sorted(latencies)
    report={'system':platform.platform(),'python':platform.python_version(),'cases':len(latencies),'random_seeds':seeds,
        'hard_contract_failures':hard,'quality_issue_cases':len(quality),'quality_issues':quality,
        'median_local_score':statistics.median(scores),'local_score_at_least_90':sum(s>=90 for s in scores),
        'latency_ms':{'median':round(statistics.median(latencies),2),'p95':round(sorted_latency[int(.95*(len(latencies)-1))],2),'max':round(max(latencies),2)},
        'official_samples':sample_reports,
        'limitations':['Local quality scores are not official judge scores.','Arbitrary copy-heavy micro canvases can require text below comfortable reading sizes.','Adobe Express host acceptance must be verified in a signed-in developer session.']}
    (output_dir/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('quality_issues','official_samples')},indent=2))
    if hard:raise RuntimeError(f'{len(hard)} hard contract failures')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts'/'verification')
    parser.add_argument('--seeds',type=int,default=40)
    args=parser.parse_args()
    verify(args.output,args.seeds)
