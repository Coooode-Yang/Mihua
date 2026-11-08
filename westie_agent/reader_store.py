"""Versioned, document-bound Markdown artifacts; no writes into Zotero."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ReaderArtifact:
    directory: Path
    metadata: dict

    @property
    def markdown(self): return self.directory/'paper.md'


class ReaderStore:
    def __init__(self, base):
        self.root = (Path(base)/'data/paper-readers').resolve()

    def document_root(self, library_directory, paper):
        if not re.fullmatch(r'[A-Z0-9]{8}',paper.item_key) or type(paper.library_id) is not int or paper.library_id<1:
            raise ValueError('文献标识无效，请刷新研究页。')
        fingerprint = hashlib.sha256(os.path.normcase(str(Path(library_directory).resolve())).encode('utf-8')).hexdigest()[:20]
        directory = self.root/fingerprint/f'{paper.library_id}-{paper.item_key}'
        if not directory.resolve().is_relative_to(self.root):
            raise ValueError('解读保存目录无效。')
        return directory

    def versions(self, library_directory, paper):
        root = self.document_root(library_directory,paper)
        result = []
        if not root.is_dir(): return result
        for directory in sorted(root.iterdir(),reverse=True):
            if not directory.is_dir() or not directory.resolve().is_relative_to(self.root): continue
            try:
                metadata = json.loads((directory/'reader.json').read_text(encoding='utf-8'))
                if (not isinstance(metadata,dict) or metadata.get('item_key') != paper.item_key
                        or metadata.get('library_id') != paper.library_id or not (directory/'paper.md').is_file()): continue
                result.append(ReaderArtifact(directory,metadata))
            except (OSError,ValueError,TypeError): continue
        return result

    def latest(self, library_directory, paper):
        versions = self.versions(library_directory,paper)
        return versions[0] if versions else None

    def publish(self, staging, library_directory, paper, metadata):
        destination = self.document_root(library_directory,paper)
        destination.mkdir(parents=True,exist_ok=True)
        run = datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        write_json(Path(staging)/'reader.json',dict(metadata,library_id=paper.library_id,item_key=paper.item_key,
                                                  attachment_key=paper.attachment_key,title=paper.title))
        os.rename(staging,destination/run)
        return ReaderArtifact(destination/run,dict(metadata,library_id=paper.library_id,item_key=paper.item_key,
                                                  attachment_key=paper.attachment_key,title=paper.title))
