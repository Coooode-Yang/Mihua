"""User-editable browser shortcuts. No credentials or implicit proxy changes."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

# Only used to remove the shipped examples when importing a legacy v1 file.
# A v2 list accepts any user-selected HTTP/HTTPS website, including these URLs.
LEGACY_EXAMPLE_URLS = frozenset({
    'https://one.bnuzh.edu.cn', 'https://jwxt.bnuzh.edu.cn/caslogin',
    'https://library.bnuzh.edu.cn', 'https://webvpn.bnu.edu.cn',
    'https://jwb.bnuzh.edu.cn', 'https://www.bnuzh.edu.cn',
})


def validated_url(value: str) -> str:
    if not isinstance(value,str) or not 1 <= len(value.strip()) <= 2048:
        raise ValueError('请输入长度不超过 2048 的 http:// 或 https:// 网址。')
    value=value.strip()
    try:
        parsed=urlsplit(value)
        port=parsed.port
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        if re.search(r'[\s\x00-\x1f\x7f\\]',value):
            raise ValueError()
        parsed.hostname.encode('idna')
    except (ValueError,UnicodeError):
        raise ValueError('仅支持有效的 HTTP/HTTPS 网页地址，不要包含用户名、密码、空格或本地文件路径。') from None
    return value


def default_catalog() -> dict:
    return {'schema_version':2, 'entries':[]}


def validate_catalog(data: dict) -> dict:
    if not isinstance(data,dict) or data.get('schema_version') not in {1,2}:
        raise ValueError('网页入口配置版本无效。')
    entries=data.get('entries')
    if not isinstance(entries,list) or len(entries)>30:
        raise ValueError('网页入口最多 30 个。')
    result=default_catalog()
    seen=set()
    for row in entries:
        if not isinstance(row,dict):raise ValueError('入口格式无效。')
        if data['schema_version']==1 and isinstance(row.get('url'),str) and row['url'].rstrip('/') in LEGACY_EXAMPLE_URLS:
            continue
        rid,name=row.get('id'),row.get('name')
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',rid) or rid in seen:
            raise ValueError('入口编号无效或重复。')
        if not isinstance(name,str) or not 1<=len(name.strip())<=60:
            raise ValueError('入口名称需为 1–60 字。')
        aliases=row.get('aliases',[])
        if not isinstance(aliases,list) or len(aliases)>10 or any(not isinstance(a,str) or not 1<=len(a)<=40 for a in aliases):
            raise ValueError('关键词最多 10 个，每个最多 40 字。')
        note=row.get('note','')
        if not isinstance(note,str) or len(note)>600:raise ValueError('说明最多 600 字。')
        seen.add(rid)
        result['entries'].append(dict(id=rid,name=name.strip(),url=validated_url(row.get('url','')),note=note,aliases=aliases))
    return result


class CampusCatalog:
    def __init__(self,base:Path):
        self.path=base/'data/campus.json'
        self.problem=''
        try:
            self.data=default_catalog()
            if self.path.exists():
                original=self.path.read_bytes()
                raw=json.loads(original.decode('utf-8'))
                self.data=validate_catalog(raw)
                if raw['schema_version']==1:
                    # Preserve the original before stripping school metadata/examples.
                    digest=hashlib.sha256(original).hexdigest()[:12]
                    backup=self.path.with_name(f'campus.before-web-shortcuts-v2-{digest}.json')
                    if not backup.exists():
                        with backup.open('xb') as stream:stream.write(original)
                    self.save(self.data)
        except (OSError,ValueError,TypeError):
            self.problem='网页入口配置未能读取或更新，请检查文件格式与目录权限；原文件未覆盖。'
            if not hasattr(self,'data'):self.data=default_catalog()

    def save(self,data):
        clean=validate_catalog(data)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(self.path)
        self.data=clean
        self.problem=''

    def entry(self,rid):
        return copy.deepcopy(next((e for e in self.data['entries'] if e['id']==rid),None))

    def match(self,text):
        if re.search(r'检查|检测|诊断|打不开|无法|故障|为什么',text):return None
        if not re.search(r'打开|带我去|进入|访问|启动|我要选课|查课表|查成绩',text):return None
        matches=[(len(key),e) for e in self.data['entries'] for key in [e['name'],*e['aliases']] if key.lower() in text.lower()]
        return copy.deepcopy(max(matches,key=lambda pair:pair[0])[1]) if matches else None


