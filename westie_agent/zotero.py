"""Read Zotero metadata without changing its database or loading paper contents."""
from __future__ import annotations

import configparser
from contextlib import closing
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time


@dataclass(frozen=True)
class RecentPaper:
    title: str
    attachment_key: str
    item_key: str
    library_id: int
    group_id: int | None
    content_type: str
    last_read: float
    evidence: str
    page: int | None = None

    def open_url(self):
        if not re.fullmatch(r'[A-Z0-9]{8}', self.attachment_key):
            raise ValueError('Zotero 附件标识无效，请刷新文库。')
        library = f'groups/{int(self.group_id)}' if self.group_id else 'library'
        action = 'open-pdf' if self.content_type == 'application/pdf' else 'open'
        url = f'zotero://{action}/{library}/items/{self.attachment_key}'
        if self.content_type == 'application/pdf' and self.page is not None:
            url += f'?page={self.page}'
        return url


@dataclass
class ReadingSnapshot:
    directory: Path
    papers: list[RecentPaper]
    week_count: int
    month_count: int
    approximate_count: int


class ZoteroLibrary:
    def __init__(self, base, *, home=None, roaming=None, clock=time.time):
        self.config = Path(base)/'data/zotero.json'
        self.home = Path(home) if home else Path.home()
        self.roaming = Path(roaming) if roaming else Path(os.environ.get('APPDATA', self.home/'AppData/Roaming'))
        self.clock = clock

    def discover_directory(self):
        if self.config.is_file():
            try:
                setting = json.loads(self.config.read_text(encoding='utf-8'))
                value = setting['directory']
                if not isinstance(value, str) or not value:
                    raise ValueError()
                return Path(value).expanduser().resolve()
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError('文库路径设置无法读取，请重新选择文库或点击“自动查找”。') from None
        profile_root = self.roaming/'Zotero/Zotero'
        ini = profile_root/'profiles.ini'
        if ini.is_file():
            try:
                profiles = configparser.ConfigParser()
                profiles.read(ini, encoding='utf-8-sig')
                sections = sorted((s for s in profiles if s.startswith('Profile')),
                                  key=lambda s: profiles[s].get('Default') != '1')
                for section in sections:
                    row = profiles[section]
                    profile = Path(row['Path']) if row.get('IsRelative') == '0' else profile_root/row['Path']
                    prefs = profile/'prefs.js'
                    if not prefs.is_file() or prefs.stat().st_size > 2_000_000:
                        continue
                    text = prefs.read_text(encoding='utf-8')
                    if re.search(r'user_pref\("extensions\.zotero\.useDataDir",\s*true\)', text):
                        match = re.search(r'user_pref\("extensions\.zotero\.dataDir",\s*("(?:[^"\\]|\\.)*")\)', text)
                        if match:
                            path = Path(json.loads(match[1])).expanduser().resolve()
                            if (path/'zotero.sqlite').is_file():
                                return path
            except (OSError, ValueError, KeyError, configparser.Error):
                pass
        return (self.home/'Zotero').resolve()

    def select_directory(self, directory):
        path = Path(directory).expanduser().resolve()
        if not (path/'zotero.sqlite').is_file():
            raise ValueError('请选择包含 zotero.sqlite 的 Zotero 文库文件夹。')
        self.config.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.with_suffix('.json.tmp')
        try:
            temporary.write_text(json.dumps({'directory': str(path)}, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self.config)
        finally:
            temporary.unlink(missing_ok=True)

    def reset_directory(self):
        self.config.unlink(missing_ok=True)

    def resolve_pdf(self, paper, directory=None):
        """Resolve only the chosen attachment, with no downloads or DB writes."""
        directory = Path(directory).resolve() if directory is not None else self.discover_directory()
        if not re.fullmatch(r'[A-Z0-9]{8}', paper.attachment_key):
            raise ValueError('附件标识无效，请刷新研究页。')
        try:
            with closing(sqlite3.connect((directory/'zotero.sqlite').as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                db.execute('PRAGMA query_only=ON')
                row = db.execute('''SELECT a.path,a.linkMode,a.contentType FROM itemAttachments a
                    JOIN items i ON i.itemID=a.itemID WHERE i.key=? AND i.libraryID=?
                    AND NOT EXISTS(SELECT 1 FROM deletedItems x WHERE x.itemID=i.itemID OR x.itemID=a.parentItemID)''',
                    (paper.attachment_key,paper.library_id)).fetchone()
        except sqlite3.Error:
            raise ValueError('无法读取附件位置，请刷新文库后重试。') from None
        if not row or row[2] != 'application/pdf':
            raise ValueError('nature-reader 目前支持 Zotero 中的 PDF 附件，请选择对应的 PDF 文献。')
        value = row[0] or ''
        if value.startswith('storage:'):
            folder = (directory/'storage'/paper.attachment_key).resolve()
            path = (folder/value[len('storage:'):]).resolve()
            if not path.is_relative_to(folder):
                raise ValueError('附件路径无效，请在 Zotero 中重新定位附件。')
        elif row[1] == 2 and value.startswith('attachments:'):
            base = self.linked_attachment_base(directory)
            if base is None:
                raise ValueError('未找到链接附件的基准目录，请先在 Zotero 中确认附件能够打开。')
            path = (base/value[len('attachments:'):]).resolve()
            if not path.is_relative_to(base):
                raise ValueError('链接附件路径无效。')
        elif row[1] == 2 and Path(value).is_absolute():
            path = Path(value).resolve()
        else:
            raise ValueError('附件还未保存在本机，请先在 Zotero 中下载或定位 PDF 后重试。')
        if not path.is_file() or path.suffix.lower() != '.pdf':
            raise ValueError('找不到本机 PDF，请先在 Zotero 中下载或定位附件后重试。')
        return path

    def linked_attachment_base(self, directory):
        root = self.roaming/'Zotero/Zotero'
        profiles = configparser.ConfigParser()
        try:
            profiles.read(root/'profiles.ini',encoding='utf-8-sig')
            for section in profiles:
                if not section.startswith('Profile'): continue
                row = profiles[section]
                profile = Path(row['Path']) if row.get('IsRelative') == '0' else root/row['Path']
                text = (profile/'prefs.js').read_text(encoding='utf-8')
                def pref(name):
                    match = re.search(r'user_pref\("extensions\.zotero\.'+name+r'",\s*("(?:[^"\\]|\\.)*")\)',text)
                    return Path(json.loads(match[1])).expanduser().resolve() if match else None
                configured = pref('dataDir') or (self.home/'Zotero').resolve()
                if configured == directory.resolve():
                    return pref('baseAttachmentPath')
        except (OSError, ValueError, KeyError, configparser.Error):
            return None
        return None

    def recent(self, cancel=None):
        cancel = cancel or threading.Event()
        directory = self.discover_directory()
        database = directory/'zotero.sqlite'
        if not database.is_file():
            raise ValueError('未找到 Zotero 文库。请选择包含 zotero.sqlite 的文件夹。')
        papers = {}
        now = self.clock()
        try:
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                db.set_progress_handler(lambda: int(cancel.is_set()), 1000)
                columns = {row[1] for row in db.execute('PRAGMA table_info(itemAttachments)')}
                last_read = 'a.lastRead' if 'lastRead' in columns else 'NULL'
                rows = db.execute(f'''
                    SELECT i.key AS attachment_key, i.libraryID, l.type AS library_type,
                           a.parentItemID, a.contentType, {last_read} AS last_read,
                           p.key AS parent_key, g.groupID,
                           COALESCE(pt.value, at.value, '未命名文献') AS title
                    FROM itemAttachments a JOIN items i ON i.itemID=a.itemID
                    JOIN libraries l ON l.libraryID=i.libraryID
                    LEFT JOIN groups g ON g.libraryID=i.libraryID
                    LEFT JOIN items p ON p.itemID=a.parentItemID
                    LEFT JOIN (SELECT d.itemID,v.value FROM itemData d
                        JOIN fields f ON f.fieldID=d.fieldID JOIN itemDataValues v ON v.valueID=d.valueID
                        WHERE f.fieldName='title') pt ON pt.itemID=a.parentItemID
                    LEFT JOIN (SELECT d.itemID,v.value FROM itemData d
                        JOIN fields f ON f.fieldID=d.fieldID JOIN itemDataValues v ON v.valueID=d.valueID
                        WHERE f.fieldName='title') at ON at.itemID=a.itemID
                    WHERE a.contentType IN ('application/pdf','application/epub+zip','text/html','application/xhtml+xml')
                      AND l.type IN ('user','group')
                      AND NOT EXISTS (SELECT 1 FROM deletedItems x WHERE x.itemID=i.itemID OR x.itemID=p.itemID)
                ''').fetchall()
            for row in rows:
                if cancel.is_set():
                    return ReadingSnapshot(directory, [], 0, 0, 0)
                key = row['attachment_key']
                if not isinstance(key, str) or not re.fullmatch(r'[A-Z0-9]{8}', key):
                    continue
                if row['library_type'] == 'group' and not row['groupID']:
                    continue
                page, state_time = None, 0
                for name in ('.zotero-reader-state', '.zotero-pdf-state'):
                    state = directory/'storage'/key/name
                    if not state.resolve().is_relative_to(directory.resolve()):
                        continue
                    try:
                        if state.is_file() and state.stat().st_size < 65536:
                            data = json.loads(state.read_text(encoding='utf-8'))
                            index = data.get('pageIndex')
                            if type(index) is int and 0 <= index < 1_000_000:
                                page = index+1
                            state_time = state.stat().st_mtime
                            break
                    except (OSError, ValueError, AttributeError):
                        continue
                timestamp = row['last_read']
                explicit = isinstance(timestamp, (int, float)) and 0 < timestamp <= now+86400
                timestamp = float(timestamp) if explicit else state_time
                if not 0 < timestamp <= now+86400:
                    continue  # Added/modified metadata is not evidence of reading.
                paper = RecentPaper(str(row['title']), key, row['parent_key'] or key, row['libraryID'],
                                    row['groupID'], row['contentType'], timestamp,
                                    'Zotero 最近阅读' if explicit else '阅读位置更新时间', page)
                identity = (paper.library_id, paper.item_key)
                if identity not in papers or timestamp > papers[identity].last_read:
                    papers[identity] = paper
        except sqlite3.Error:
            raise ValueError('暂时无法读取 Zotero 文库，请稍后刷新；若更换过文库，请重新选择文件夹。') from None
        ordered = sorted(papers.values(), key=lambda p: p.last_read, reverse=True)
        return ReadingSnapshot(directory, ordered[:100],
                               sum(p.last_read >= now-7*86400 for p in ordered),
                               sum(p.last_read >= now-30*86400 for p in ordered),
                               sum(p.evidence != 'Zotero 最近阅读' for p in ordered))
