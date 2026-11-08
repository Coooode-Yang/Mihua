"""Render Kenney's editable OBJ into deterministic transparent desktop sprites.

Pillow and NumPy are build-time dependencies. No image generation,
network service, Blender installation, or old character artwork is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import numpy as np


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def unit(a):
    n = math.sqrt(dot(a, a)) or 1
    return tuple(x/n for x in a)


def rotate(p, axis, angle, pivot=(0, 0, 0)):
    a = list(sub(p, pivot))
    i, j = {'x': (1, 2), 'y': (2, 0), 'z': (0, 1)}[axis]
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    a[i], a[j] = c*a[i]-s*a[j], s*a[i]+c*a[j]
    return tuple(a[k]+pivot[k] for k in range(3))


class Chick:
    def __init__(self, source):
        self.source = source
        self.vertices, self.uvs, self.faces = [], [], []
        group = ''
        self.texture = Image.open(source.parent/'Textures/colormap.png').convert('RGB')
        for line in source.read_text(encoding='utf-8-sig').splitlines():
            v = line.split()
            if not v:
                continue
            if v[0] == 'g':
                group = v[1]
            elif v[0] == 'v':
                self.vertices.append(tuple(map(float, v[1:4])))
            elif v[0] == 'vt':
                self.uvs.append(tuple(map(float, v[1:3])))
            elif v[0] == 'f':
                refs = [tuple(int(k)-1 for k in t.split('/')) for t in v[1:]]
                for i in range(1, len(refs)-1):
                    tri = (refs[0], refs[i], refs[i+1])
                    uv = tuple(sum(self.uvs[r[1]][k] for r in tri)/3 for k in (0, 1))
                    color = self.texture.getpixel((min(511, int(uv[0]*512)), min(511, int((1-uv[1])*512))))
                    self.faces.append((group, [r[0] for r in tri], color, [self.uvs[r[1]] for r in tri]))
        self.eye_centers = {}
        for side in (-1, 1):
            points = [self.vertices[i] for _, tri, color, _ in self.faces if max(color)<110
                      for i in tri if (1 if self.vertices[i][0]>=0 else -1)==side]
            if points:
                self.eye_centers[side] = tuple(sum(p[k] for p in points)/len(points) for k in range(3))

    def render(self, *, yaw=-22, pitch=16, flap=55, tilt=0, blink=False,
               sleep=0.0, breath=0.0, thinking=-1, search=-1):
        size, aa = 384, 3
        image = Image.new('RGBA', (size*aa, size*aa))
        draw = ImageDraw.Draw(image)
        az, el = map(math.radians, (yaw, pitch))
        view = (math.sin(az)*math.cos(el), math.sin(el), math.cos(az)*math.cos(el))
        right = (math.cos(az), 0, -math.sin(az))
        up = cross(view, right)
        light = unit((-0.7, 1.3, 1.8))
        scale = 178

        def project(p):
            return ((192+scale*dot(p, right))*aa, (345-scale*dot(p, up))*aa)

        def transform(p, group, color):
            if group=='pillow':
                return p
            x, y, z = p
            p = (x, y, z)
            if group.startswith('wing'):
                side = -1 if 'right' in group else 1
                pivot = (side*.55, .64, 0)
                folded = rotate(p, 'z', -side*(flap+sleep*17), pivot)
                if thinking>=0 or search>=0:
                    amount = min(1.0, max(0.0, (thinking if thinking>=0 else search)/3))
                    if side==-1:
                        raised = rotate(p, 'y', 130, pivot)
                        raised = rotate(raised, 'x', -12, pivot)
                        raised = (raised[0],raised[1],raised[2]+.34)
                        folded = tuple(a+(b-a)*amount for a,b in zip(folded,raised))
                p = folded
            if sleep:
                # Tuck the feet, fold wings and lower the body onto its haunches.
                crouch = (p[0]*.98, .08+(p[1]-.18)*(.78+breath*.018), p[2]+.06)
                if group.startswith('leg'):
                    crouch = (p[0],.035+p[1]*.28,p[2])
                else:
                    crouch = rotate(crouch,'x',8,(0,.30,0))
                p = tuple(a+(b-a)*sleep for a,b in zip(p,crouch))
            if tilt:
                p = rotate(p, 'z', tilt, (0, .65, 0))
            return p

        pixels = np.zeros((size*aa, size*aa, 4), dtype=np.uint8)
        depths = np.full((size*aa, size*aa), -np.inf)
        texture = np.asarray(self.texture)
        faces = [(g,[self.vertices[i] for i in indices],c,uv) for g,indices,c,uv in self.faces]
        if sleep>.05:
            # A small cream cushion makes the settled, tucked sleeping pose read
            # clearly even at the compact desktop size. It is native geometry.
            def pillow(latitude,longitude):
                a,b=math.radians(latitude),math.radians(longitude)
                return (.18+.58*sleep*math.cos(a)*math.cos(b),
                        .10+.10*sleep*math.sin(a),.43+.27*sleep*math.cos(a)*math.sin(b))
            for lat in range(-90,90,18):
                for lon in range(0,360,24):
                    q=[pillow(lat,lon),pillow(lat,lon+24),pillow(lat+18,lon+24),pillow(lat+18,lon)]
                    faces.extend([('pillow',[q[0],q[3],q[2]],(242,231,205),None),
                                  ('pillow',[q[0],q[2],q[1]],(242,231,205),None)])
        if sleep>.55 or blink:
            # Closed lids are curved geometry, not flattened white eyeballs.
            faces = [(g,p,c,uv) for g,p,c,uv in faces
                     if not (g=='body' and (max(c)<110 or (c[0]>210 and abs(c[0]-c[1])<2)))]
            for center in self.eye_centers.values():
                for j in range(16):
                    a,b = -1+j/8,-1+(j+1)/8
                    def lid(t,dy):
                        return (center[0]+t*.14,center[1]-.012+.05*t*t+dy,.644)
                    q=[lid(a,-.009),lid(b,-.009),lid(b,.009),lid(a,.009)]
                    faces.extend([('body',[q[0],q[1],q[2]],(67,56,47),None),
                                  ('body',[q[0],q[2],q[3]],(67,56,47),None)])
        for group, vertices, color, uvs in faces:
            points = [transform(p, group, color) for p in vertices]
            normal = unit(cross(sub(points[1], points[0]), sub(points[2], points[0])))
            if dot(normal, view)<=0:
                continue
            shade = .73+.27*max(0, dot(normal, light))
            screen = np.asarray([project(p) for p in points])
            lo = np.maximum(0, np.floor(screen.min(axis=0)).astype(int))
            hi = np.minimum(size*aa-1, np.ceil(screen.max(axis=0)).astype(int))
            if np.any(hi<lo):
                continue
            (x0,y0),(x1,y1),(x2,y2) = screen
            denom = (y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
            if abs(denom)<1e-8:
                continue
            xx, yy = np.meshgrid(np.arange(lo[0],hi[0]+1)+.5, np.arange(lo[1],hi[1]+1)+.5)
            w0 = ((y1-y2)*(xx-x2)+(x2-x1)*(yy-y2))/denom
            w1 = ((y2-y0)*(xx-x2)+(x0-x2)*(yy-y2))/denom
            w2 = 1-w0-w1
            z = sum(w*dot(p,view) for w,p in zip((w0,w1,w2),points))
            region = np.s_[lo[1]:hi[1]+1,lo[0]:hi[0]+1]
            visible = (w0>=-1e-8)&(w1>=-1e-8)&(w2>=-1e-8)&(z>depths[region])
            if uvs is None:
                rgb = np.broadcast_to((np.asarray(color)*shade).astype(np.uint8),(*xx.shape,3))
            else:
                uv = sum(w[...,None]*np.asarray(t) for w,t in zip((w0,w1,w2),uvs))
                tx = np.clip((uv[...,0]*512).astype(int),0,511)
                ty = np.clip(((1-uv[...,1])*512).astype(int),0,511)
                rgb = (texture[ty,tx]*shade).astype(np.uint8)
            pixels[region][visible,:3] = rgb[visible]
            pixels[region][visible,3] = 255
            depths[region][visible] = z[visible]
        image = Image.fromarray(pixels, 'RGBA')
        draw = ImageDraw.Draw(image)

        if search>=0:
            # Lift the glass to eye level, then trace a slow inspection arc.
            lift=min(1,search/3)
            phase=max(0,search-4)*math.pi/4
            cx,cy = 292-20*lift+5*math.sin(phase),222-53*lift+4*math.cos(phase)
            r = 29
            def pts(box):
                return tuple(round(n*aa) for n in box)
            draw.line(pts((cx-20, cy+22, cx-58, cy+81)), fill='#314b58', width=14*aa)
            draw.line(pts((cx-20, cy+22, cx-58, cy+81)), fill='#679da5', width=7*aa)
            draw.ellipse(pts((cx-r, cy-r, cx+r, cy+r)), outline='#314b58', width=8*aa)
            draw.ellipse(pts((cx-r+6, cy-r+6, cx+r-6, cy+r-6)), outline='#9bdce1', width=3*aa)
            draw.arc(pts((cx-20, cy-20, cx+20, cy+20)), 205, 264, fill='#e3fcff', width=4*aa)
        if thinking>=0:
            # An unobtrusive staggered ellipsis keeps thinking distinct from search.
            phase=max(0,thinking-4)/8*2*math.pi
            for j in range(3):
                pulse=(1+math.sin(phase-j*.9))/2
                cx,cy=258+j*22,73-pulse*5
                radius=4+2*pulse
                draw.ellipse(tuple(round(v*aa) for v in (cx-radius,cy-radius,cx+radius,cy+radius)),
                             fill=('#9eacac','#789697','#4d7377')[j])
        return image.resize((size,size),Image.Resampling.LANCZOS).transpose(Image.Transpose.FLIP_LEFT_RIGHT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    chick = Chick(args.source)
    poses = [chick.render(),chick.render(blink=True),chick.render(flap=5,tilt=-3),
             chick.render(flap=23,tilt=3),chick.render(yaw=-27,tilt=-7),chick.render(sleep=1,tilt=-14)]
    searches=[chick.render(search=i,yaw=-22+3*math.sin(max(0,i-4)*math.pi/4),tilt=-3) for i in range(12)]
    thoughts=[chick.render(thinking=i,yaw=-24+2*math.cos(max(0,i-4)*math.pi/4),tilt=-3-min(1,i/3)*4+math.sin(max(0,i-4)*math.pi/4)*2.5,
                           blink=(i==9)) for i in range(12)]
    sleeps=[chick.render(sleep=t,tilt=-14*t,blink=(t<.55)) for t in (0,.3,.65,1)]
    sleeps += [chick.render(sleep=1,tilt=-14,breath=math.sin(i*math.pi/4)) for i in range(8)]
    for name,frames,columns in [('chick-sheet.png',poses,3),('chick-search-sheet.png',searches,4),
                                ('chick-think-sheet.png',thoughts,4),('chick-sleep-sheet.png',sleeps,4)]:
        sheet = Image.new('RGBA',(384*columns,384*math.ceil(len(frames)/columns)))
        for i, frame in enumerate(frames):
            sheet.alpha_composite(frame, ((i%columns)*384, (i//columns)*384))
        sheet.save(output/name)
    ui = output/'ui'
    ui.mkdir(exist_ok=True)
    # Application icons use the same model's frontal head on a compact tile.
    from render_mihua_icon import write_icons
    write_icons(args.source, ui)
    overview = Image.new('RGB',(384*4,440*3),'#fffaf0')
    pen = ImageDraw.Draw(overview)
    samples=poses[:4]+[thoughts[i] for i in (0,3,6,9)]+[sleeps[2],sleeps[4],searches[4],searches[7]]
    labels=['Idle / faces left','Blink','Happy A','Happy B','Think / lift wing','Think / chin rest',
            'Think / head tilt','Think / blink','Sleep / settle','Sleep / tucked','Search / inspect A','Search / inspect B']
    font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 22)
    for i,frame in enumerate(samples):
        x,y=(i%4)*384,(i//4)*440
        overview.paste(frame, (x, y), frame)
        pen.text((x+24, y+390), labels[i], font=font, fill='#5a4933')
    overview.save(output/'chick-preview.png')
    for name,frames in [('mihua-thinking',thoughts[4:]),('mihua-sleep',sleeps[4:]),('mihua-search',searches[4:])]:
        gif=[]
        for frame in frames:
            background=Image.new('RGB',(384,384),'#fffaf0')
            background.paste(frame,mask=frame.getchannel('A'))
            gif.append(background)
        gif[0].save(output/(name+'.gif'),save_all=True,append_images=gif[1:],duration=230 if 'sleep' not in name else 520,loop=0)
    metadata = {'character': 'Kenney Cube Pets / animal-chick', 'license': 'CC0-1.0',
                'source_obj_sha256': hashlib.sha256(args.source.read_bytes()).hexdigest(),
                'renderer': 'packaging/render_chick_assets.py', 'size': [384, 384],
                'display_name':'米花','facing':'left','fixed_canvas':True,
                'icon_facing':'front','icon_shape':'rounded-square','icon_source':'original-model',
                'poses':['Idle','Blink','Happy A','Happy B','Curious','Sleep'],
                'search_frames':12,'think_frames':12,'sleep_frames':12,
                'animation_grid':[4,3],'intro_frames':4,'loop_frames':8,
                'pet_sizes':[140,190,240],'external_api_called':False}
    (output/'chick-assets.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output), 'vertices': len(chick.vertices), 'triangles': len(chick.faces),
                      'eyes': chick.eye_centers, 'bounds': [[min(p[k] for p in chick.vertices), max(p[k] for p in chick.vertices)] for k in range(3)]}))


if __name__ == '__main__':
    main()
