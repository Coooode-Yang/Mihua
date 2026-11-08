"""Vendor a pinned, reviewable subset of upstream nature-reader resources."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'c1a8e7bbcf86d82a268dc4a9980d36082b31fb2f'


def main():
    checkout = ROOT/'build/nature-skills-reference'
    actual = subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    if actual != COMMIT:
        raise RuntimeError('Upstream revision differs from the reviewed version')
    destination = ROOT/'assets/skills'
    shutil.copytree(checkout/'skills/nature-reader',destination/'nature-reader',dirs_exist_ok=True)
    shared = destination/'nature-shared/core'
    shared.mkdir(parents=True,exist_ok=True)
    shutil.copy2(checkout/'skills/nature-shared/core/terminology-ledger.md',shared/'terminology-ledger.md')
    shutil.copy2(checkout/'LICENSE',destination/'LICENSE.nature-skills.txt')
    (destination/'NOTICE.Bai.md').write_text(
        '# Nature-reader integration\n\nUpstream: https://github.com/Yuan1z0825/nature-skills\n\n'
        'Revision: '+COMMIT+'\n\nNature-reader 2.1.1 and the shared terminology ledger are retained under Apache-2.0. '
        'The math validator is vendored with attribution in westie_agent/reader_math.py. '
        'Bai adds local PDF extraction, model request orchestration, source-bound Markdown versions and Qt viewing. '
        'This is an adapted integration, not an endorsement by upstream.\n',encoding='utf-8')
    files = {p.relative_to(destination).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
             for p in destination.rglob('*') if p.is_file() and p.name!='nature-reader-manifest.json'}
    (destination/'nature-reader-manifest.json').write_text(json.dumps({
        'source':'https://github.com/Yuan1z0825/nature-skills','commit':COMMIT,
        'skill':'nature-reader','version':'2.1.1','files':files,
        'integration':'Original guidance retained; Bai adds local PDF extraction, bounded model requests and Qt viewing.',
    },ensure_ascii=False,indent=2),encoding='utf-8')
    # Freeze the reviewed deterministic validator as a regular bundled module.
    source = checkout/'skills/nature-reader/scripts/validate_reader_math.py'
    header = '# Vendored unmodified from Yuan1z0825/nature-skills, commit '+COMMIT+'\n# Apache-2.0; see assets/skills/LICENSE.nature-skills.txt.\n'
    (ROOT/'westie_agent/reader_math.py').write_text(header+source.read_text(encoding='utf-8'),encoding='utf-8')
    print(str(destination))


if __name__=='__main__': main()
