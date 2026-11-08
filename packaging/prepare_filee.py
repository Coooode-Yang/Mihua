"""Prepare the pinned official portable engine; no installer or system changes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from westie_agent.file_conversion import FILEE_ARCHIVE_SHA256, FILEE_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    destination = (ROOT/'runtime/filee'/FILEE_VERSION).resolve()
    assert destination.is_relative_to(ROOT/'runtime')
    archive = args.archive or ROOT/'build'/f'Filee-{FILEE_VERSION}-win-Portable.zip'
    archive.parent.mkdir(parents=True, exist_ok=True)
    url = f'https://github.com/KnifeLemon/Filee/releases/download/v{FILEE_VERSION}/Filee-{FILEE_VERSION}-win-Portable.zip'
    if not archive.exists():
        request = urllib.request.Request(url, headers={'User-Agent': 'BaiAgent-build'})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open('wb') as stream:
            while block := response.read(1024*1024):
                stream.write(block)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != FILEE_ARCHIVE_SHA256:
        raise RuntimeError('Filee archive SHA256 mismatch; no engine was installed.')
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            if not (destination/entry.filename).resolve().is_relative_to(destination):
                raise RuntimeError('Unexpected archive path')
        zipped.extractall(destination)
    for upstream, local in (('LICENSE', 'LICENSE.Filee.txt'), ('THIRD-PARTY-NOTICES.md', 'THIRD-PARTY-NOTICES.Filee.md')):
        request = urllib.request.Request(f'https://raw.githubusercontent.com/KnifeLemon/Filee/v{FILEE_VERSION}/{upstream}', headers={'User-Agent': 'BaiAgent-build'})
        with urllib.request.urlopen(request, timeout=30) as response:
            (destination/local).write_bytes(response.read())
    executable = destination/'filee-cli.exe'
    if not executable.is_file():
        raise RuntimeError('Official CLI was not found in the archive')
    (destination/'bai-engine-manifest.json').write_text(json.dumps({
        'version': FILEE_VERSION, 'archive_sha256': FILEE_ARCHIVE_SHA256,
        'cli_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
        'source': f'https://github.com/KnifeLemon/Filee/releases/tag/v{FILEE_VERSION}',
    }, indent=2), encoding='utf-8')
    print(str(destination))


if __name__ == '__main__':
    main()
