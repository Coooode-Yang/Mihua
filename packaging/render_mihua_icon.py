"""Create a frontal app icon from Mihua's own editable Kenney model."""
from __future__ import annotations
import argparse
import base64
import io
from pathlib import Path

from PIL import Image,ImageDraw,ImageFont


def write_icons(source:Path,output:Path):
    from render_chick_assets import Chick
    chick=Chick(source)
    # Preserve the actual pet's faceted head, round eyes, beak and three tufts.
    chick.faces=[face for face in chick.faces if face[0]=='body']
    head=chick.render(yaw=0,pitch=6)
    head=head.crop(head.getbbox())
    head=head.resize((round(head.width*458/head.height),458),Image.Resampling.LANCZOS)
    output.mkdir(parents=True,exist_ok=True)
    aa=3
    canvas=Image.new('RGBA',(512*aa,512*aa))
    draw=ImageDraw.Draw(canvas)
    draw.rounded_rectangle((8*aa,8*aa,504*aa,504*aa),radius=110*aa,fill='#eadcbe')
    draw.rounded_rectangle((8*aa,8*aa,504*aa,494*aa),radius=110*aa,fill='#fff3dc')
    large=head.resize((head.width*aa,head.height*aa),Image.Resampling.LANCZOS)
    x,y=(512-head.width)//2,24
    canvas.alpha_composite(large,(x*aa,y*aa))
    canvas=canvas.resize((512,512),Image.Resampling.LANCZOS)
    canvas.save(output/'chick-head.png')
    canvas.save(output/'mihua-app-icon.png')
    canvas.save(output/'chick-head.ico',sizes=[(n,n) for n in (16,20,24,32,40,48,64,96,128,256)])
    buffer=io.BytesIO()
    head.save(buffer,format='PNG')
    encoded=base64.b64encode(buffer.getvalue()).decode('ascii')
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="512" height="512" viewBox="0 0 512 512">
<title>米花 · 原模型正面头像</title>
<desc>Original Kenney Cube Pets chick head, rendered from retained CC0 OBJ; cream rounded square.</desc>
<rect x="8" y="8" width="496" height="496" rx="110" fill="#eadcbe"/>
<rect x="8" y="8" width="496" height="486" rx="110" fill="#fff3dc"/>
<image x="{x}" y="{y}" width="{head.width}" height="{head.height}" xlink:href="data:image/png;base64,{encoded}"/>
</svg>'''
    (output/'mihua-app-icon.svg').write_text(svg,encoding='utf-8')
    preview=Image.new('RGB',(820,470),'#222526')
    draw=ImageDraw.Draw(preview)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
    small=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',14)
    large=canvas.resize((256,256),Image.Resampling.LANCZOS)
    preview.paste(large,(42,42),large)
    draw.text((48,335),'米花 · 原模型正面头像',font=font,fill='#f5f5ed')
    x=365
    for n in (16,20,24,32,48):
        tile=canvas.resize((n,n),Image.Resampling.LANCZOS)
        preview.paste(tile,(x,100-n//2),tile)
        draw.text((x-1,145),str(n),font=small,fill='#bdc5c5')
        x+=75
    for y,name in ((233,'米花'),(313,'卸载米花')):
        draw.rounded_rectangle((347,y-12,775,y+49),radius=8,fill='#292c2d')
        tile=canvas.resize((32,32),Image.Resampling.LANCZOS)
        preview.paste(tile,(365,y+2),tile)
        draw.text((414,y-1),name,font=font,fill='#fafaf6')
        draw.text((414,y+28),'最近添加',font=small,fill='#c4c8c7')
    preview.save(output/'mihua-icon-preview.png')
    return output/'chick-head.png'


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(write_icons(args.source,args.output))
