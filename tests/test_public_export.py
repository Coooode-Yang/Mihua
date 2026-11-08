import importlib.util
from pathlib import Path


def auditor():
    path=Path(__file__).resolve().parents[1]/'packaging/audit_public.py'
    spec=importlib.util.spec_from_file_location('mihua_public_audit',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def licenses(root):
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md','assets/source/chick/License.txt','assets/skills/LICENSE.nature-skills.txt'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture',encoding='utf-8')


def test_audit_allows_synthetic_test_data_and_original_model(tmp_path):
    licenses(tmp_path)
    fixture=tmp_path/'tests/data/example.json';fixture.parent.mkdir(parents=True);fixture.write_text('{}')
    model=tmp_path/'assets/source/chick/animal-chick.obj';model.write_text('v 0 0 0')
    assert auditor().audit(tmp_path,[fixture,model])['ok']


def test_audit_rejects_personal_database_and_retired_artwork(tmp_path):
    licenses(tmp_path)
    database=tmp_path/'personal.sqlite3';database.write_bytes(b'fixture')
    image=tmp_path/'westie-sheet.png';image.write_bytes(b'fixture')
    report=auditor().audit(tmp_path,[database,image])
    assert {x['rule'] for x in report['findings']}=={'private-or-generated-file','retired-dog-artwork'}


def test_audit_reports_secret_location_without_value(tmp_path):
    licenses(tmp_path)
    source=tmp_path/'config.py'
    value='sk-'+''.join(chr(97+i%26) for i in range(35))
    source.write_text('key='+repr(value))
    report=auditor().audit(tmp_path,[source])
    assert report['findings']==[{'file':'config.py','rule':'provider-token','line':1}]
    assert value not in str(report)
