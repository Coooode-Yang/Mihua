"""Audit public source candidates without printing possible secret values.

Uses Git's tracked and unignored file lists where available. This is a limited
rule check, not a proof that all sensitive or copyrighted content is absent.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
BLOCKED_DIRECTORIES={'.git','.venv','venv','__pycache__','.pytest_cache','build','dist','Release','runtime'}
BLOCKED_SUFFIXES={'.exe','.dll','.pyd','.pem','.key','.pfx','.p12','.sqlite','.sqlite3','.db','.gguf','.log','.zip','.7z'}
TEXT_SUFFIXES={'.py','.md','.txt','.json','.toml','.yaml','.yml','.bat','.ps1','.c','.nsi','.svg','.example'}
RULES={
    'private-key-header':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    'provider-token':re.compile(r'(?<![A-Za-z0-9])(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{28,}'),
    'user-home-path':re.compile(r'(?i)[A-Z]:[\\/](?:Users|home)[\\/]'),
}


def public_files(root:Path):
    try:
        result=subprocess.run(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=root,
                              capture_output=True,check=True)
        names=sorted(set(result.stdout.decode('utf-8').split('\0'))-{''})
        return [root/name for name in names if (root/name).exists() or (root/name).is_symlink()]
    except (OSError,subprocess.CalledProcessError):
        result=[]
        for path in root.rglob('*'):
            relative=path.relative_to(root)
            if any(part in BLOCKED_DIRECTORIES for part in relative.parts):
                continue
            if path.is_file() or path.is_symlink():
                result.append(path)
        return sorted(result)


def audit(root:Path,files=None):
    root=root.resolve()
    files=list(public_files(root) if files is None else files)
    findings=[]
    for path in files:
        relative=path.relative_to(root).as_posix()
        parts=Path(relative).parts
        def flag(rule,line=None):
            findings.append({'file':relative,'rule':rule,**({'line':line} if line else {})})
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            flag('linked-or-external-path');continue
        if (any(part in BLOCKED_DIRECTORIES for part in parts) or parts[0]=='data'
                or parts[:2]==('Tools','data') or path.suffix.lower() in BLOCKED_SUFFIXES
                or (path.name.startswith('.env') and path.name!='.env.example')):
            flag('private-or-generated-file');continue
        if re.fullmatch(r'westie-.*\.(?:png|gif|ico)',path.name,re.I):
            flag('retired-dog-artwork')
        if path.stat().st_size>=100*1024**2:
            flag('github-file-size-limit')
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in ('LICENSE','.gitignore','.gitattributes'):
            try: text=path.read_text(encoding='utf-8-sig')
            except UnicodeError: flag('text-encoding');continue
            for number,line in enumerate(text.splitlines(),1):
                for name,pattern in RULES.items():
                    if pattern.search(line):flag(name,number)
    # Preserve the reviewed upstream resource boundary and its license hashes.
    manifest=root/'assets/skills/nature-reader-manifest.json'
    if manifest.is_file():
        value=json.loads(manifest.read_text(encoding='utf-8'))
        for name,digest in value['files'].items():
            item=(manifest.parent/name).resolve()
            if not item.is_relative_to(manifest.parent.resolve()) or not item.is_file() or hashlib.sha256(item.read_bytes()).hexdigest()!=digest:
                findings.append({'file':'assets/skills/'+name,'rule':'upstream-resource-mismatch'})
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md','assets/source/chick/License.txt','assets/skills/LICENSE.nature-skills.txt'):
        if not (root/name).is_file():findings.append({'file':name,'rule':'missing-license-or-notice'})
    return {'ok':not findings,'checked_files':len(files),'findings':findings,
            'scope':'current tracked and unignored source candidates; limited pattern and provenance checks'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=audit(ROOT)
    content=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(content,encoding='utf-8')
    print(content)
    raise SystemExit(0 if result['ok'] else 1)
