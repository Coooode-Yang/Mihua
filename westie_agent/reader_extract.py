"""Local PDF extraction in an owned child process, separate from model calls."""
import json
from pathlib import Path
import re

CAPTION = re.compile(r'^(?:(?:Fig(?:ure)?\.?|Table)\s*(?:\d+|[IVXLC]+)(?![A-Za-z0-9])|[图表]\s*\d+(?!\d))',re.I)


def reading_order(boxes,width):
    narrow = [b for b in boxes if b['x1']-b['x0'] < width*.6]
    columns = sum(b['x0']<width*.4 for b in narrow)>=2 and sum(b['x0']>width*.45 for b in narrow)>=2
    if not columns: return sorted(boxes,key=lambda b:(round(b['top'],1),b['x0']))
    spans = sorted([b for b in boxes if b['x1']-b['x0']>=width*.65],key=lambda b:b['top'])
    pending = [b for b in boxes if b not in spans]
    result = []
    def ordered(values): return sorted(values,key=lambda b:(int(b['x0']>width*.45),b['top'],b['x0']))
    for span in spans:
        before = [b for b in pending if (b['top']+b['bottom'])/2<span['top']]
        result.extend(ordered(before)); result.append(span)
        pending = [b for b in pending if b not in before]
    result.extend(ordered(pending))
    return result


def clean_text(text):
    text = re.sub(r'([A-Za-z])-\n([a-z])',r'\1\2',text)
    return re.sub(r'\s+',' ',text).strip()


def is_equation(text):
    return len(text)<220 and bool(re.search(r'[=∑∫√∂]',text)) and len(re.findall(r'[A-Za-z]{4,}',text))<5


def extract_pdf(source,output):
    import pdfplumber
    from .reader_store import write_json
    source,output = Path(source),Path(output)
    assets = output/'assets'; (assets/'pages').mkdir(parents=True,exist_ok=True)
    blocks,figures,equations,pages,notes = [],[],[],[],[]
    counts = {'S':0,'C':0,'E':0,'F':0,'T':0}
    def next_id(kind):
        counts[kind]+=1
        return f'{kind}{counts[kind]:03}'
    try:
        pdf = pdfplumber.open(source,laparams={'all_texts':True,'line_margin':.3})
    except Exception:
        raise ValueError('PDF 无法打开，可能已损坏或需要密码。') from None
    with pdf:
        if len(pdf.pages)>300: raise ValueError('PDF 超过300页，请先拆分后分别解读。')
        for number,page in enumerate(pdf.pages,1):
            if page.width<=0 or page.height<=0: raise ValueError('PDF 页面尺寸无效。')
            resolution = min(144,2400/max(page.width,page.height)*72)
            rendered = page.to_image(resolution=resolution)
            image = rendered.original.convert('RGB')
            image_path = f'assets/pages/page-{number:03}.png'; image.save(output/image_path)
            bounds = rendered.bbox
            scale_x,scale_y = image.width/(bounds[2]-bounds[0]),image.height/(bounds[3]-bounds[1])
            def crop(bbox,category,identifier):
                x0,top,x1,bottom = bbox
                x0,top=max(bounds[0],x0-2),max(bounds[1],top-2)
                x1,bottom=min(bounds[2],x1+2),min(bounds[3],bottom+2)
                if x1<=x0 or bottom<=top:
                    notes.append(f'p.{number} {identifier}：该区域超出可见页边界，改用原页核对。')
                    return image_path
                target = f'assets/{category}/{identifier}.png'
                (output/target).parent.mkdir(parents=True,exist_ok=True)
                image.crop((round((x0-bounds[0])*scale_x),round((top-bounds[1])*scale_y),
                            round((x1-bounds[0])*scale_x),round((bottom-bounds[1])*scale_y))).save(output/target)
                return target
            boxes = page.objects.get('textboxhorizontal',[])+page.objects.get('textboxvertical',[])
            if not boxes and page.chars:
                text = page.extract_text() or ''
                boxes = [{'text':text,'x0':0,'x1':page.width,'top':0,'bottom':page.height}]
                notes.append(f'p.{number} 未能恢复段落边界，已按整页文本保留。')
            seen=set(); page_blocks=[]
            for box in reading_order(boxes,page.width):
                text=clean_text(box.get('text',''))
                signature=(text,round(box['x0']),round(box['top']))
                if not text or signature in seen: continue
                seen.add(signature)
                bbox=[float(box[k]) for k in ('x0','top','x1','bottom')]
                unreliable = text.count('�')>max(2,len(text)*.03) or '(cid:' in text
                kind='equation' if is_equation(text) else 'caption' if CAPTION.match(text) else 'paragraph'
                identifier=next_id('E' if kind=='equation' else 'C' if kind=='caption' else 'S')
                block={'id':identifier,'page':number,'type':kind,'order':len(blocks)+1,
                       'original_text':text,'translation':'','bbox':bbox,'confidence':'low' if unreliable else 'medium','refs':[]}
                if kind=='equation' or unreliable:
                    block['image_path']=crop(bbox,'equations' if kind=='equation' else 'uncertain',identifier)
                    block['confidence']='low'
                    if kind=='equation':
                        printed=re.search(r'\((\d+[A-Za-z]?)\)\s*$',text)
                        if printed:block['equation_number']=printed[1]
                        block['latex']=''; equations.append(dict(block))
                    notes.append(f'p.{number} {identifier}：'+('公式使用原图，未进行未经验证的转写。' if kind=='equation' else '文字编码不可靠，保留原图，未自动翻译。'))
                blocks.append(block);page_blocks.append(block)
            usable = [b for b in page_blocks if b['type']!='equation' and b['confidence']!='low']
            if sum(len(b['original_text']) for b in usable)<25:
                notes.append(f'p.{number}：可提取文字不足，可能是扫描页；未运行 OCR，需人工补充。')
            # Keep verifiable raster/table bounds; vector-only candidates are explicitly approximate.
            tables = page.find_tables() if len(page.edges)<8000 else []
            for caption in [b for b in page_blocks if b['type']=='caption']:
                text=caption['original_text']; table=bool(re.match(r'^(Table|表)',text,re.I))
                bbox=None; confidence='low'
                cx0,ctop,cx1,cbottom=caption['bbox']
                if table:
                    candidates=[t.bbox for t in tables if abs(t.bbox[1]-cbottom)<220 or abs(t.bbox[3]-ctop)<100]
                    if candidates:
                        bbox=min(candidates,key=lambda b:min(abs(b[1]-cbottom),abs(b[3]-ctop)));confidence='medium'
                else:
                    half = page.width/2
                    left,right=(0,page.width) if cx1-cx0>page.width*.6 else (0,half+12) if cx0<half else (half-12,page.width)
                    candidates=[]
                    for obj in page.images+page.curves+page.rects+page.lines:
                        if (obj['x0']>=left-8 and obj['x1']<=right+8 and obj['bottom']<=ctop+3
                                and obj['top']>=max(0,ctop-320) and obj['bottom']>ctop-300
                                and (obj['x1']-obj['x0']>2 or obj['bottom']-obj['top']>2)
                                and (obj['x1']-obj['x0'])*(obj['bottom']-obj['top'])<page.width*page.height*.8):
                            candidates.append(obj)
                    if candidates:
                        bbox=[min(c['x0'] for c in candidates),min(c['top'] for c in candidates),
                              max(c['x1'] for c in candidates),max(c['bottom'] for c in candidates)]
                identifier=next_id('T' if table else 'F')
                if bbox is None:
                    notes.append(f'p.{number} {identifier}：未能可靠裁切图表，请对照该页原图。')
                    continue
                target=crop(bbox,'tables' if table else 'figures',identifier)
                figure={'id':identifier,'page':number,'caption_id':caption['id'],'image_path':target,
                        'bbox':list(bbox),'confidence':confidence,'label':CAPTION.match(text).group(),
                        'placement_hint':'near_first_mention','placed_after':caption['id']}
                figures.append(figure)
                if confidence=='low': notes.append(f'p.{number} {identifier}：图表边界为自动估计，需对照原页核对。')
            pages.append({'page':number,'block_ids':[b['id'] for b in page_blocks],'image_path':image_path,
                          'text_available':bool(usable)})
            page.close(); image.close()
    for figure in figures:
        number=re.search(r'(\d+|[IVXLC]+)$',figure['label'],re.I).group()
        label=(r'(?:Table|表)' if figure['id'].startswith('T') else r'(?:Fig(?:ure)?\.?|图)')+r'\s*'+re.escape(number)+r'(?![A-Za-z0-9])'
        for block in blocks:
            if block['type']=='paragraph' and re.search(label,block['original_text'],re.I):
                figure['placed_after']=block['id'];block['refs'].append(figure['id']);break
    result={'paper':{'source_type':'pdf'},'blocks':blocks,'pages':pages,'figures':figures,'equations':equations,'glossary':[],'notes':notes}
    write_json(output/'source_map.json',result)
    write_json(output/'extraction-result.json',{'ok':True,'pages':len(pages),'source_format':'scanned-pdf' if not any(p['text_available'] for p in pages) else 'pdf-text'})
    return result
