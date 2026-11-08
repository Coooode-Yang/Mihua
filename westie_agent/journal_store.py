"""Local daily research notes, separate from tasks and the Zotero database."""
from contextlib import contextmanager
from datetime import date, datetime
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
from urllib.parse import quote, urlsplit
import uuid

KINDS = {'reading': '文献笔记', 'meeting': '组会与讨论', 'discovery': '科研见闻', 'review': 'AI 回顾'}
MAX_CHAT_IMAGES = 200


def conversation_source(sources):
    return any(s.get('type') == 'chat' and s.get('scope') == 'conversation' for s in sources if isinstance(s, dict))


def image_limit(sources):
    return MAX_CHAT_IMAGES if conversation_source(sources) else 6


def valid_day(value):
    try:
        if date.fromisoformat(value).isoformat() != value: raise ValueError()
    except (ValueError, TypeError): raise ValueError('请选择有效的日志日期。') from None
    return value


def safe_url(value):
    if not isinstance(value, str) or len(value) > 4096 or re.search(r'[\s\x00-\x1f<>]', value): return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
            return value
        if re.fullmatch(r'zotero://(?:open-pdf|open)/(?:library|groups/[1-9]\d*)/items/[A-Z0-9]{8}(?:\?page=[1-9]\d*)?', value):
            return value
    except ValueError: pass
    return ''


def md_literal(value):
    return re.sub(r'([\\`*_{}\[\]<>#|])', r'\\\1', str(value)).replace('\n', ' ')


class JournalStore:
    def __init__(self, base):
        self.base = Path(base).resolve()
        self.root = self.base/'data/research-journal'
        self.database = self.root/'journal.sqlite'

    def save_draft(self, identifier, data):
        from .reader_store import write_json
        if not re.fullmatch(r'[0-9a-f]{32}',identifier): raise ValueError('草稿标识无效。')
        target=self.root/'drafts'/f'{identifier}.json';target.parent.mkdir(parents=True,exist_ok=True)
        write_json(target,data)

    def drafts(self):
        result=[]
        for path in sorted((self.root/'drafts').glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            if not re.fullmatch(r'[0-9a-f]{32}',path.stem):continue
            try:
                data=json.loads(path.read_text(encoding='utf-8'));valid_day(data['day'])
                if isinstance(data.get('body'),str):result.append(dict(data,_draft_id=path.stem))
            except (OSError,ValueError,KeyError,TypeError):continue
        return result

    def remove_draft(self,identifier):
        if not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('草稿标识无效。')
        (self.root/'drafts'/f'{identifier}.json').unlink(missing_ok=True)
        self.clear_draft_images(identifier)

    def clear_draft_images(self,identifier):
        if not re.fullmatch(r'[0-9a-f]{32}',identifier):raise ValueError('草稿标识无效。')
        for path in (self.root/'drafts').glob(f'{identifier}-*.png'):
            if re.fullmatch(identifier+r'-[0-9a-f]{32}\.png',path.name):path.unlink(missing_ok=True)

    def stage_chat_image(self, identifier, data):
        """Keep explicitly captured image bytes with the draft until Save."""
        from PIL import Image
        if not re.fullmatch(r'[0-9a-f]{32}', identifier):
            raise ValueError('草稿标识无效。')
        if not isinstance(data, bytes) or not data or len(data) > 15*1024*1024:
            raise ValueError('对话图片无法保存，单张图片需在 15 MB 以内。')
        with Image.open(BytesIO(data)) as image:
            if image.format != 'PNG' or image.width*image.height > 40_000_000:
                raise ValueError('对话图片格式或尺寸无法保存。')
            image.verify()
        path = self.root/'drafts'/f'{identifier}-{uuid.uuid4().hex}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('xb') as stream:
                stream.write(data)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return path, 'assets/'+hashlib.sha256(data).hexdigest()+'.png'

    @contextmanager
    def connect(self, write=False):
        if write: self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database if write else self.database.as_uri()+'?mode=ro', uri=not write, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            if write:
                connection.executescript('''
                    CREATE TABLE IF NOT EXISTS entries (
                        id TEXT PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
                        title TEXT NOT NULL, body TEXT NOT NULL, reflection TEXT NOT NULL,
                        tags TEXT NOT NULL, sources TEXT NOT NULL, attachments TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS entries_day ON entries(day, deleted, created_at);
                ''')
            yield connection
            if write: connection.commit()
        except BaseException:
            if write: connection.rollback()
            raise
        finally: connection.close()

    @staticmethod
    def decode(row):
        entry = dict(row)
        for key in ('tags', 'sources', 'attachments'): entry[key] = json.loads(entry[key])
        return entry

    def entries(self, day=None, *, query='', kind='', deleted=False, start=None, end=None):
        if day: valid_day(day)
        if start: valid_day(start)
        if end: valid_day(end)
        if not self.database.exists(): return []
        clauses = ['deleted=?']; args = [int(deleted)]
        for column, op, value in (('day', '=', day), ('kind', '=', kind), ('day', '>=', start), ('day', '<=', end)):
            if value: clauses.append(f'{column}{op}?'); args.append(value)
        if query.strip():
            # instr treats %, _, backslashes and quotes literally.
            clauses.append('instr(lower(title||char(10)||body||char(10)||reflection||char(10)||tags),lower(?))>0')
            args.append(query.strip())
        with self.connect() as db:
            return [self.decode(row) for row in db.execute('SELECT * FROM entries WHERE '+' AND '.join(clauses)+
                    ' ORDER BY day DESC, created_at ASC, id ASC', args)]

    def get(self, identifier):
        if not self.database.exists(): return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM entries WHERE id=?', (identifier,)).fetchone()
            return self.decode(row) if row else None

    def save(self, *, day, kind, title='', body='', reflection='', tags=(), sources=(), attachments=(), identifier=None, expected_updated_at=None):
        valid_day(day)
        if kind not in KINDS: raise ValueError('请选择日志类型。')
        title, body, reflection = title.strip(), body.strip(), reflection.strip()
        if not body: raise ValueError('写一点内容再保存吧。')
        sources = list(sources)
        body_limit = 1_000_000 if conversation_source(sources) else 60000
        if len(title)>200 or len(body)>body_limit or len(reflection)>6000: raise ValueError('内容过长，请拆成几条记录保存。')
        tags = list(dict.fromkeys(str(t).strip().lstrip('#') for t in tags if str(t).strip()))
        if len(tags)>12 or any(len(t)>40 for t in tags): raise ValueError('标签最多 12 个，每个最多 40 字。')
        if len(sources)>100 or len(json.dumps(sources, ensure_ascii=False))>100000: raise ValueError('来源过多，请拆分记录。')
        for source in sources:
            if not isinstance(source, dict): raise ValueError('来源格式无效。')
            if source.get('url') and not safe_url(source['url']): raise ValueError('来源链接需使用 HTTP、HTTPS 或有效的 Zotero 文献链接。')
        attachments = list(attachments)
        if len(attachments)>image_limit(sources): raise ValueError(f'每条记录最多添加 {image_limit(sources)} 张图片。')
        for attachment in attachments: self.asset(attachment)
        previous = self.get(identifier) if identifier else None
        if identifier and (not previous or previous['deleted']): raise ValueError('这条记录已被移走，请刷新后重试。')
        if previous and expected_updated_at is not None and previous['updated_at']!=expected_updated_at:
            raise ValueError('记录已在其他窗口更新。请点击“另存一条”保留本次内容，或重新打开原记录。')
        stamp = datetime.now().astimezone().isoformat(timespec='microseconds')
        entry = dict(id=identifier or uuid.uuid4().hex, day=day, kind=kind, title=title or body.splitlines()[0][:60],
                     body=body, reflection=reflection, tags=tags, sources=sources, attachments=attachments,
                     created_at=previous['created_at'] if previous else stamp, updated_at=stamp, deleted=0)
        with self.connect(write=True) as db:
            db.execute('INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', tuple(
                json.dumps(entry[k],ensure_ascii=False) if k in {'tags','sources','attachments'} else entry[k]
                for k in ('id','day','kind','title','body','reflection','tags','sources','attachments','created_at','updated_at','deleted')))
        return entry

    def trash(self, identifier, deleted=True):
        if not self.database.exists(): return
        with self.connect(write=True) as db: db.execute('UPDATE entries SET deleted=? WHERE id=?', (int(deleted),identifier))

    def asset(self, relative):
        if not isinstance(relative,str) or not re.fullmatch(r'assets/[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)',relative):
            raise ValueError('日志图片路径无效。')
        path = (self.root/relative).resolve()
        if not path.is_relative_to((self.root/'assets').resolve()) or not path.is_file(): raise ValueError('日志图片不存在。')
        return path

    def import_image(self, path):
        from PIL import Image
        path = Path(path)
        if path.stat().st_size>15*1024*1024: raise ValueError('每张图片请控制在 15 MB 以内。')
        if path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}: raise ValueError('支持 PNG、JPG、WEBP 图片。')
        with Image.open(path) as image:
            if image.width*image.height>40_000_000: raise ValueError('图片尺寸过大，请先缩小后添加。')
            if image.format not in {'PNG','JPEG','WEBP'}: raise ValueError('图片内容与格式不符。')
            image.verify()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = 'assets/'+digest+path.suffix.lower()
        target = self.root/relative; target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            with path.open('rb') as original, target.open('xb') as stream: shutil.copyfileobj(original,stream)
        return relative

    def reader_path(self, relative):
        if not isinstance(relative,str): raise ValueError('解读路径无效。')
        path = (self.base/relative).resolve()
        if not path.is_relative_to((self.base/'data/paper-readers').resolve()) or path.name!='paper.md' or not path.is_file():
            raise ValueError('原解读文件已移动或不可用，可回到研究页重新查看。')
        return path

    def markdown(self, day, *, preview=False, asset_prefix='assets', entries=None):
        valid_day(day)
        rows = self.entries(day) if entries is None else entries
        single=preview and len(rows)==1
        lines = [] if single else [f'# 科研日志 · {day}', '']
        if not rows: lines += ['今天还没有记录。随时留下一点想法。','']
        for entry in rows:
            inline_images = set()
            def inline_image(match):
                relative = match[2]
                if relative not in entry['attachments']:
                    return match[0]
                inline_images.add(relative)
                return match[1] + asset_prefix+'/'+Path(relative).name + match[3]
            body = re.sub(r'(!\[[^\]\n]*\]\()<?(assets/[0-9a-f]{64}\.(?:png|jpg|jpeg|webp))>?(\))', inline_image, entry['body'])
            clock = datetime.fromisoformat(entry['created_at']).strftime('%H:%M')
            if single:
                lines += [f'# {md_literal(entry["title"])}','',f'{day} {clock} · {KINDS[entry["kind"]]}','',body,'']
            else:
                lines += [f'## {clock} · {md_literal(KINDS[entry["kind"]])} · {md_literal(entry["title"])}', '', body, '']
            if entry['reflection']: lines += ['**为什么值得留下 / 我的想法**', '',entry['reflection'],'']
            if entry['tags']: lines += ['标签：'+' · '.join(md_literal(t) for t in entry['tags']),'']
            for image in entry['attachments']:
                if image in inline_images:
                    continue
                lines += [f'![日志图片]({asset_prefix}/{Path(image).name})','']
            for index, source in enumerate(entry['sources']):
                title = md_literal(source.get('title','来源'))
                url = safe_url(source.get('url',''))
                if url: lines += [f'来源：[{title}](<{url}>)','']
                else: lines += [f'来源：{title}','']
                if source.get('reader'):
                    try: path = self.reader_path(source['reader'])
                    except ValueError: continue
                    link = f'bai-reader:/{entry["id"]}/{index}' if preview else path.as_uri()
                    lines += [f'[查看收藏时的文献解读](<{link}>)','']
                if source.get('entry_id'):
                    if preview and source.get('snapshot'):
                        lines += [f'[查看整理时的原始记录](bai-note:/{entry["id"]}/{index})','']
                    else:
                        lines += [f'原记录编号：{source["entry_id"]}（{md_literal(source.get("day",""))}）','']
                        snapshot=source.get('snapshot')
                        if isinstance(snapshot,dict):
                            lines += ['### 整理时的原始记录','',snapshot.get('body',''),'']
                            if snapshot.get('reflection'):lines += ['我的想法：'+snapshot['reflection'],'']
        return '\n'.join(lines)

    def export(self, day, destination):
        path = Path(destination)
        if path.suffix.lower()!='.md': raise ValueError('请选择 .md 文件。')
        if path.exists(): raise ValueError('文件已存在，请换一个文件名以保留旧导出。')
        rows = self.entries(day); prefix=path.stem+'-assets'
        text = self.markdown(day, asset_prefix=quote(prefix), entries=rows)
        for row in rows:
            for relative in row['attachments']:
                source = self.asset(relative); target=path.parent/prefix/source.name
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(source.read_bytes()).digest():
                        raise ValueError('导出目录存在不同的同名附件，请选择其他文件名。')
                else:
                    with source.open('rb') as original,target.open('xb') as stream: shutil.copyfileobj(original,stream)
        with path.open('x',encoding='utf-8') as output: output.write(text)
        return path
