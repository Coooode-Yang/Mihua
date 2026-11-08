import json
from pathlib import Path
import subprocess
import threading

import pytest

from westie_agent import file_conversion as fc


@pytest.fixture
def engine(tmp_path):
    result = fc.FileeEngine(tmp_path)
    result.directory.mkdir(parents=True)
    result.executable.write_bytes(b'fake CLI')
    result._capabilities['.png'] = fc.Capability(frozenset({'pdf', 'webp', 'docx'}))
    return result


def inputs(tmp_path, *names):
    files = [tmp_path/name for name in names]
    for path in files:
        path.write_bytes(b'original')
    return files


def fake_cli(arguments, cancel, **kwargs):
    staging = Path(arguments[arguments.index('--output')+1])
    sources = arguments[1:next(i for i, v in enumerate(arguments) if str(v).startswith('--'))]
    output = staging/'result.pdf'
    output.write_bytes(b'%PDF-fake fixture')
    return 0, json.dumps({'files': [{'source': str(p), 'state': 'done', 'outputs': [str(output)]} for p in sources]}), ''


def test_capabilities_exclude_optional_engines(engine, monkeypatch):
    monkeypatch.setattr(engine, '_invoke', lambda *a, **k: (0,
        'Available output formats for .docx:\npdf, txt, html\nWith optional engines installed:\npng, jpg\n', ''))
    result = engine.capability('.DOCX', threading.Event())
    assert result.ready == {'pdf', 'txt', 'html'} and result.optional == {'png', 'jpg'}
    assert engine.capability('.docx', threading.Event()) is result
    assert not engine.capability('.bad / arg', threading.Event()).ready


def test_common_modes_and_images_do_not_offer_ocr():
    caps = {'.png': fc.Capability(frozenset({'pdf', 'docx', 'webp'})),
            '.docx': fc.Capability(frozenset({'pdf', 'txt'})), '.pdf': fc.Capability(frozenset({'png'}))}
    assert [m.key for m in fc.available_modes([Path('a.png')], caps)] == ['pdf', 'webp']
    assert [m.key for m in fc.available_modes([Path('a.png'), Path('b.docx')], caps)] == ['pdf', 'merge_pdf']
    assert [m.key for m in fc.available_modes([Path('a.pdf'), Path('b.pdf')], caps)] == ['merge_pdf', 'png']
    assert not fc.available_modes([], caps)


def test_input_validation_dedup_and_limits(tmp_path):
    paths = inputs(tmp_path, '中文 含空格.png')
    assert fc.validate_inputs(paths*2) == paths
    with pytest.raises(ValueError):
        fc.validate_inputs([tmp_path])
    with pytest.raises(ValueError):
        fc.validate_inputs([])
    with pytest.raises(OSError):
        fc.validate_inputs([tmp_path/'missing'])
    with pytest.raises(ValueError, match='200'):
        fc.validate_inputs(inputs(tmp_path, *(f'{i}.txt' for i in range(201))))


def test_original_and_existing_output_are_preserved(engine, tmp_path, monkeypatch):
    paths = inputs(tmp_path, 'image.png')
    output = tmp_path/'output'; output.mkdir()
    existing = output/'result.pdf'; existing.write_bytes(b'existing')
    monkeypatch.setattr(engine, '_invoke', fake_cli)
    result = engine.convert(paths, 'pdf', output, threading.Event())
    assert result[0].state == 'done' and Path(result[0].outputs[0]).name == 'result (2).pdf'
    assert paths[0].read_bytes() == b'original' and existing.read_bytes() == b'existing'
    assert not list(output.glob('.bai-convert-*'))


@pytest.mark.parametrize('fault', ['json', 'empty', 'escape', 'extension', 'source', 'failed', 'exit', 'no-output'])
def test_engine_rejects_invalid_or_untrusted_result(engine, tmp_path, monkeypatch, fault):
    paths = inputs(tmp_path, 'image.png')
    output = tmp_path/'output'
    outside = tmp_path/'outside.pdf'; outside.write_bytes(b'existing')
    def invoke(arguments, cancel):
        code, text, error = fake_cli(arguments, cancel)
        data = json.loads(text); row = data['files'][0]
        if fault == 'json': return 0, 'not JSON', ''
        if fault == 'empty': Path(row['outputs'][0]).write_bytes(b'')
        if fault == 'escape': row['outputs'] = [str(outside)]
        if fault == 'extension':
            bad = Path(row['outputs'][0]).with_suffix('.exe'); bad.write_bytes(b'bad'); row['outputs'] = [str(bad)]
        if fault == 'source': row['source'] = str(outside)
        if fault == 'failed': row['state'] = 'failed'; row['error'] = '损坏文件'
        if fault == 'exit': code = 1
        if fault == 'no-output': row['outputs'] = []
        return code, json.dumps(data), error
    monkeypatch.setattr(engine, '_invoke', invoke)
    result = engine.convert(paths, 'pdf', output, threading.Event())
    assert result[0].state == 'failed' and not result[0].outputs
    assert not list(output.iterdir()) and outside.read_bytes() == b'existing'


def test_batch_failure_does_not_stop_next_and_cancel_keeps_completed(engine, tmp_path, monkeypatch):
    paths = inputs(tmp_path, 'bad.png', 'good.png', 'last.png')
    def invoke(arguments, cancel):
        if Path(arguments[1]).name == 'bad.png': raise ValueError('损坏文件')
        return fake_cli(arguments, cancel)
    monkeypatch.setattr(engine, '_invoke', invoke)
    cancel = threading.Event()
    def progress(index, result):
        if index == 1 and result.state == 'done': cancel.set()
    results = engine.convert(paths, 'pdf', tmp_path/'output', cancel, progress)
    assert [r.state for r in results] == ['failed', 'done', 'cancelled']
    assert Path(results[1].outputs[0]).is_file()


def test_merge_uses_list_order_and_reports_one_shared_output(engine, tmp_path, monkeypatch):
    paths = inputs(tmp_path, 'second.png', 'first.png')
    calls = []
    def invoke(args, cancel):
        calls.append(args)
        return fake_cli(args, cancel)
    monkeypatch.setattr(engine, '_invoke', invoke)
    results = engine.convert(paths, 'merge_pdf', tmp_path/'output', threading.Event())
    assert calls[0][1:3] == paths and '--preset' in calls[0]
    assert results[0].outputs == results[1].outputs and len(results[0].outputs) == 1


def test_publish_fallback_cleans_only_its_reserved_file(tmp_path, monkeypatch):
    source = inputs(tmp_path, 'result.pdf')[0]
    out = tmp_path/'out'; out.mkdir()
    existing = out/'result.pdf'; existing.write_bytes(b'preserve')
    monkeypatch.setattr(fc.os, 'link', lambda *a: (_ for _ in ()).throw(OSError('no hard links')))
    monkeypatch.setattr(fc.shutil, 'copyfileobj', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError): fc.publish_output(source, out)
    assert list(out.iterdir()) == [existing] and existing.read_bytes() == b'preserve'


def test_invoke_isolates_settings_and_cancels_only_owned_process(engine, monkeypatch):
    cancel = threading.Event(); seen = {}
    class Process:
        returncode = None
        def __init__(self, command, **kwargs):
            seen.update(kwargs); seen['command'] = command
            assert Path(kwargs['env']['FILEE_DATA_DIR']).is_dir()
            assert 'DEEPSEEK_API_KEY' not in kwargs['env']
            assert 'shell' not in kwargs
        def communicate(self, timeout):
            if self.returncode is not None: return b'', b''
            cancel.set()
            raise subprocess.TimeoutExpired('fake', timeout)
        def poll(self): return self.returncode
    def stop(process):
        seen['stopped'] = True; process.returncode = -1
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-secret')
    monkeypatch.setattr(fc.subprocess, 'Popen', Process)
    monkeypatch.setattr(engine, '_stop', stop)
    with pytest.raises(fc.ConversionCancelled): engine._invoke(['formats', '.pdf'], cancel)
    assert seen['stopped'] and seen['command'][0] == str(engine.executable)
    assert not Path(seen['env']['FILEE_DATA_DIR']).exists()
