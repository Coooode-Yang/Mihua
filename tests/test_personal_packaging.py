import importlib.util
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def builder(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'packaging/build_release.py'
    spec = importlib.util.spec_from_file_location('bai_release_builder', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path / 'personal')
    module.ROOT.mkdir()
    return module


def test_builder_rejects_course_external_and_source_directories(builder, tmp_path):
    for path in (tmp_path / 'course/Release/v0.4.2', builder.ROOT, builder.ROOT / 'westie_agent'):
        with pytest.raises(RuntimeError):
            builder.validate_destination(path)
        assert not (path / 'MihuaAgent').exists()
    assert builder.validate_destination(builder.ROOT / 'Release/v0.5.0').is_relative_to(builder.ROOT)


def test_builder_refuses_to_clean_release_with_personal_data(builder):
    output = builder.ROOT / 'Release/v0.5.0'
    data = output / 'MihuaAgent/data'
    data.mkdir(parents=True)
    database = data / 'personal.sqlite3'
    database.write_bytes(b'keep my data')
    with pytest.raises(RuntimeError, match='个人数据'):
        builder.validate_destination(output)
    assert database.read_bytes() == b'keep my data'


def test_public_zip_excludes_database_key_logs_and_reports(builder, tmp_path):
    folder = tmp_path / 'package'
    for name in ('MihuaAgent.exe', 'data/settings.json', 'data/personal.sqlite3', 'data/personal.sqlite3-journal',
                 'data/api-key.dpapi.json', 'data/application.lock', 'data/vocabulary.json',
                 'data/logs/test.log', 'data/reports/private.html'):
        file = folder / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('test fixture', encoding='utf-8')
    archive = tmp_path / 'public.zip'
    builder.zip_tree(folder, archive, include_models=False)
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == {'MihuaAgent/MihuaAgent.exe', 'MihuaAgent/data/settings.json'}
