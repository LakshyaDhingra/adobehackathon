#!/usr/bin/env python3
"""Create a short MP4 walkthrough from actual Chameleon outputs, without mock results.
Requires Pillow (runtime) and imageio-ffmpeg (optional authoring dependency).
"""
from __future__ import annotations
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from PIL import Image,ImageDraw,ImageFont
from engine.resize import resize
from engine.qa import audit
from renderer.converter.convert import render_design_to_png

W,H=1280,720
BG='#faf9f6';INK='#302534';MUTED='#87768e';PURPLE='#6748a3'
FONTS=ROOT/'brand_kit/fonts'


def font(size,bold=False):return ImageFont.truetype(str(FONTS/('SolsticeSans-Bold.ttf' if bold else 'SolsticeSans-Regular.ttf')),size)


def base(kicker,title,subtitle):
    im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im)
    d.rounded_rectangle((42,26,77,61),radius=10,fill=PURPLE);d.text((51,27),'c',font=font(26,True),fill='white')
    d.text((89,29),'chameleon',font=font(23,True),fill=INK)
    d.text((43,86),kicker,font=font(12,True),fill=MUTED)
    d.text((40,111),title,font=font(35,True),fill=INK)
    d.text((43,163),subtitle,font=font(16),fill=MUTED)
    d.line((40,670,1240,670),fill='#e5dfe8',width=1)
    d.text((43,686),'ADOBE UNIVERSITY HACKATHON 2026',font=font(10),fill=MUTED)
    d.text((906,686),'Actual engine outputs · local checks',font=font(10),fill=MUTED)
    return im


def card(im,art,box,label):
    x,y,w,h=box;d=ImageDraw.Draw(im)
    d.rounded_rectangle((x,y,x+w,y+h),radius=12,fill='white',outline='#e6e1e8',width=1)
    d.text((x+16,y+13),label,font=font(12,True),fill=MUTED)
    thumb=art.convert('RGB');thumb.thumbnail((w-30,h-65),Image.Resampling.LANCZOS)
    im.paste(thumb,(int(x+(w-thumb.width)/2),int(y+43+(h-58-thumb.height)/2)))


def make_frames(source,advanced,assets,outdir):
    previews={}
    for label,target in [('Post',(1080,1080)),('Story',(1080,1920)),('Banner',(1600,400)),('Thumbnail',(1280,720))]:
        o=resize(source,dict(zip(('width','height'),target)))
        p=outdir/(label+'.png');render_design_to_png(o,assets,p)
        with Image.open(p) as image:previews[label]=image.copy()
    p=outdir/'source.png';render_design_to_png(source,assets,p)
    with Image.open(p) as image:previews['Source']=image.copy()
    a=resize(advanced,{'width':728,'height':90})
    p=outdir/'advanced.png';render_design_to_png(a,assets,p)
    with Image.open(p) as image:previews['Advanced']=image.copy()
    frames=[]
    im=base('ONE FINISHED DESIGN','Keep the idea. Change the canvas.','The original elements are the starting point. Every word and asset stays.')
    card(im,previews['Source'],(430,215,420,425),'ORIGINAL · 1080 × 1080');frames.append((im,5))
    im=base('REFLOW, ACROSS SHAPES','The same campaign, arranged again.','Real brand fonts, preserved image proportions, and grouped buttons that travel together.')
    card(im,previews['Post'],(40,220,350,420),'POST · 1080 × 1080')
    card(im,previews['Story'],(410,220,260,420),'STORY · 1080 × 1920')
    card(im,previews['Thumbnail'],(690,220,550,420),'THUMBNAIL · 1280 × 720');frames.append((im,8))
    im=base('A DIFFERENT SHAPE, A DIFFERENT COMPOSITION','Make room for the message.','Product art and copy become columns. The headline keeps its hierarchy.')
    card(im,previews['Banner'],(50,225,1180,380),'BANNER · 1600 × 400');frames.append((im,6))
    im=base('THE ADVANCED CASE','17 elements. A very tight canvas.','728 × 90 · Original content preserved · No local collision or overflow flags')
    # Show at native dimensions to avoid disguising the real readability trade-off.
    d=ImageDraw.Draw(im);d.rounded_rectangle((50,240,1230,475),radius=12,fill='white',outline='#e6e1e8')
    im.paste(previews['Advanced'].convert('RGB'),(276,305))
    d.text((70,500),'Every text element is at least 7 px. Small text still deserves a human review.',font=font(16),fill=INK)
    d.text((70,535),'The missing holiday badge is intentional in the starter; it remains a reported placeholder.',font=font(13),fill=MUTED)
    frames.append((im,7))
    im=base('FROM ONE DESIGN TO A CAMPAIGN','Inspect. Choose. Export.','A local studio, four-format campaign downloads, and an Adobe Express add-on panel.')
    d=ImageDraw.Draw(im)
    for i,(title,body) in enumerate([('Compare','Original and reflow side by side'),('Choose','Automatic or directed layout'),('Export','PNG + editable JSON + campaign ZIP')]):
        x=55+i*405;d.rounded_rectangle((x,270,x+360,500),radius=14,fill='white',outline='#e6e1e8');d.text((x+22,295),f'0{i+1}',font=font(15),fill=PURPLE);d.text((x+22,345),title,font=font(27,True),fill=INK)
        words=body.split();lines=[];line=''
        for word in words:
            if len(line+' '+word)>29:lines.append(line);line=word
            else:line=(line+' '+word).strip()
        lines.append(line)
        for j,line in enumerate(lines):d.text((x+22,402+24*j),line,font=font(14),fill=MUTED)
    d.text((55,558),'Express inserts the result as an image on a new page. Live host acceptance is a separate check.',font=font(13),fill=MUTED)
    frames.append((im,6));return frames


def main():
    try:import imageio_ffmpeg
    except ImportError:raise SystemExit('Install the optional imageio-ffmpeg package to create the video.')
    dest=ROOT/'artifacts';dest.mkdir(exist_ok=True)
    core=json.loads((ROOT/'input/chameleon_sample_level2_core.json').read_text())['source']
    advanced=json.loads((ROOT/'input/chameleon_sample_level3_advanced.json').read_text())['source']
    with tempfile.TemporaryDirectory() as temp:
        frames=make_frames(core,advanced,ROOT/'brand_kit/assets',Path(temp))
        command=[imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-f','rawvideo','-vcodec','rawvideo','-pix_fmt','rgb24',
                 '-s',f'{W}x{H}','-r','24','-i','-','-an','-c:v','libx264','-pix_fmt','yuv420p','-crf','21','-movflags','+faststart',str(dest/'chameleon-demo.mp4')]
        process=subprocess.Popen(command,stdin=subprocess.PIPE)
        previous=None
        for im,seconds in frames:
            for frame in range(seconds*24):
                fraction=min(1,frame/10)
                result=Image.blend(previous,im,fraction) if previous is not None and fraction<1 else im
                process.stdin.write(result.tobytes())
            previous=im
        process.stdin.close()
        if process.wait():raise RuntimeError('Video encoding failed')
        frames[1][0].save(dest/'demo-preview.png')
    print(dest/'chameleon-demo.mp4')

if __name__=='__main__':main()
