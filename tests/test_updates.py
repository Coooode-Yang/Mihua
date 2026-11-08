import base64
import hashlib
import io
import json
import stat
import threading
import zipfile
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from westie_agent import updates
from westie_agent.update_install import install_staged, validate_layout, InstallLock, update_in_progress, launch_helper
from westie_agent.updates import (GitHubUpdater, UpdateConfig, UpdateError, UpdateCancelled,
                                 canonical_json, extract_package, verify_manifest,
                                 verify_package, version_tuple)


@pytest.fixture
def signing():
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    return key, UpdateConfig('example/MihuaAgent-Releases', public)


def signed(signing, package=b'package', **changes):
    key, config = signing
    payload = dict(schema=1, repository=config.repository, variant='api', platform='windows-x64',
                   version='0.5.1', asset='MihuaAgent-api-win64.zip',
                   sha256=hashlib.sha256(package).hexdigest(), size=len(package), notes='保留数据并升级。')
    payload.update(changes)
    return canonical_json(dict(manifest=payload, signature=base64.b64encode(key.sign(canonical_json(payload))).decode()))


def package_bytes(config, extra=None, version='0.5.1'):
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, 'w') as archive:
        files = {'MihuaAgent/MihuaAgent.exe': b'new-exe', 'MihuaAgent/MihuaUpdater.exe': b'helper',
                 'MihuaAgent/_internal/runtime.dll': b'runtime',
                 'MihuaAgent/update-config.json': canonical_json(asdict(config)),
                 'MihuaAgent/build-info.json': canonical_json({'version': version}),
                 'MihuaAgent/data/settings.json': b'publisher-defaults'}
        files.update(extra or {})
        for name, content in files.items():
            info = zipfile.ZipInfo('placeholder')
            info.filename = info.orig_filename = name
            archive.writestr(info, content)
    return memory.getvalue()


def test_signed_check_and_numeric_versions(signing):
    raw = signed(signing, version='0.10.0')
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw)))
    try:
        updater = GitHubUpdater(signing[1], '0.9.9', client=client)
        assert updater.check().version == '0.10.0'
        updater.current = '0.10.0'
        assert updater.check() is None
        updater.current = '1.0.0'
        assert updater.check() is None
    finally:
        client.close()


@pytest.mark.parametrize('value', ['v1.0.0', '1.2', '01.2.3', '1.2.3-beta', None, [1, 2, 3]])
def test_invalid_version(value):
    with pytest.raises(UpdateError):
        version_tuple(value)


def test_modified_manifest_and_wrong_key_rejected(signing):
    value = json.loads(signed(signing))
    value['manifest']['version'] = '9.0.0'
    with pytest.raises(UpdateError, match='签名'):
        verify_manifest(canonical_json(value), signing[1])
    other = Ed25519PrivateKey.generate()
    other_config = UpdateConfig(signing[1].repository, base64.b64encode(other.public_key().public_bytes_raw()).decode())
    with pytest.raises(UpdateError, match='签名'):
        verify_manifest(signed(signing), other_config)


@pytest.mark.parametrize('changes', [{'repository': 'other/repo'}, {'variant': 'offline'}, {'platform': 'linux-x64'},
                                     {'asset': '../evil.zip'}, {'size': -1}, {'size': True}, {'sha256': 'invalid'}])
def test_signed_but_incompatible_manifest_rejected(signing, changes):
    with pytest.raises(UpdateError):
        verify_manifest(signed(signing, **changes), signing[1])


def test_disabled_updates_do_not_access_network():
    def forbidden(request):
        pytest.fail('Disabled updater must not access network')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(UpdateError, match='尚未配置'):
            GitHubUpdater(UpdateConfig(), '0.4.2', client=client).check()


@pytest.mark.parametrize('location', ['http://github.com/example/MihuaAgent-Releases/releases/x',
                                       'https://attacker.example/package.zip',
                                       'https://github.com/other/repo/releases/x'])
def test_redirect_cannot_escape_trusted_origins(signing, location):
    requests = []
    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={'Location': location})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(UpdateError):
            GitHubUpdater(signing[1], '0.4.2', client=client).check()
    assert len(requests) == 1


def test_github_asset_redirect_and_full_download(signing, tmp_path):
    config = signing[1]
    content = package_bytes(config)
    release = verify_manifest(signed(signing, content), config)
    urls = []
    def handle(request):
        urls.append(str(request.url))
        if request.url.host == 'github.com':
            return httpx.Response(302, headers={'Location': 'https://release-assets.githubusercontent.com/asset'})
        return httpx.Response(200, content=content)
    base = tmp_path / 'Installed'
    base.mkdir()
    progress = []
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        work = GitHubUpdater(config, '0.4.2', client=client).download(release, base, progress.append)
    assert len(urls) == 2 and progress[-1] == 100
    assert not (work / 'staged/MihuaAgent/data').exists()
    assert (work / 'staged/MihuaAgent/MihuaAgent.exe').read_bytes() == b'new-exe'


@pytest.mark.parametrize('bad_content', [b'corrupt', b'p', b'package-too-long'])
def test_corrupted_download_cleans_staging(signing, tmp_path, bad_content):
    release = verify_manifest(signed(signing), signing[1])
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=bad_content))) as client:
        with pytest.raises(UpdateError):
            GitHubUpdater(signing[1], '0.4.2', client=client).download(release, tmp_path / 'Installed')
    assert not list(tmp_path.glob('.MihuaAgent-update-*'))


def test_cancelled_download_leaves_installed_data_untouched(signing, tmp_path):
    base = tmp_path / 'Installed'
    (base / 'data').mkdir(parents=True)
    (base / 'data/key').write_bytes(b'user-secret')
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(UpdateCancelled):
        GitHubUpdater(signing[1], '0.4.2', cancel=cancel).download(verify_manifest(signed(signing), signing[1]), base)
    assert (base / 'data/key').read_bytes() == b'user-secret'
    assert not list(tmp_path.glob('.MihuaAgent-update-*'))


@pytest.mark.parametrize('path', ['MihuaAgent/../../escape.exe', '/MihuaAgent/file',
                                 'MihuaAgent/data/../../escape', 'MihuaAgent/C:evil',
                                 'MihuaAgent/_internal/CON.dll', 'MihuaAgent/_internal/file. ',
                                 'MihuaAgent\\escape', 'MihuaAgent/_internal/RUNTIME.dll'])
def test_unsafe_archive_rejected_before_extraction(signing, tmp_path, path):
    content = package_bytes(signing[1], {path: b'evil'})
    archive = tmp_path / 'package.zip'
    archive.write_bytes(content)
    release = verify_manifest(signed(signing, content), signing[1])
    with pytest.raises(UpdateError):
        extract_package(archive, tmp_path / 'stage', release, signing[1])
    assert not (tmp_path / 'stage').exists()


def test_archive_symlink_rejected(signing, tmp_path):
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        info = zipfile.ZipInfo('MihuaAgent/link')
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, '../../elsewhere')
    with pytest.raises(UpdateError, match='链接'):
        extract_package(archive, tmp_path / 'stage', verify_manifest(signed(signing), signing[1]), signing[1])


def test_archive_cannot_replace_update_trust(signing, tmp_path):
    content = package_bytes(UpdateConfig())
    archive = tmp_path / 'package.zip'
    archive.write_bytes(content)
    with pytest.raises(UpdateError, match='公钥'):
        extract_package(archive, tmp_path / 'stage', verify_manifest(signed(signing, content), signing[1]), signing[1])


def transaction_dirs(tmp_path):
    target = tmp_path / '米花 程序'
    (target / 'data/models').mkdir(parents=True)
    (target / 'runtime').mkdir()
    (target / 'MihuaAgent.exe').write_bytes(b'old')
    (target / 'data/settings.json').write_bytes(b'my-settings')
    (target / 'data/api-key.dpapi.json').write_bytes(b'my-encrypted-key')
    (target / 'data/models/model.gguf').write_bytes(b'my-model')
    (target / 'runtime/server.exe').write_bytes(b'my-runtime')
    work = tmp_path / '.MihuaAgent-update-test'
    staged = work / 'staged/MihuaAgent'
    staged.mkdir(parents=True)
    (staged / 'MihuaAgent.exe').write_bytes(b'new')
    return target, work


def assert_user_data(target):
    assert (target / 'data/settings.json').read_bytes() == b'my-settings'
    assert (target / 'data/api-key.dpapi.json').read_bytes() == b'my-encrypted-key'
    assert (target / 'data/models/model.gguf').read_bytes() == b'my-model'
    assert (target / 'runtime/server.exe').read_bytes() == b'my-runtime'


def test_transaction_keeps_data_keys_models_runtime_and_old_binaries(tmp_path):
    target, work = transaction_dirs(tmp_path)
    checked = []
    install_staged(target, work, lambda path: checked.append(path))
    assert checked == [target]
    assert (target / 'MihuaAgent.exe').read_bytes() == b'new'
    assert (work / 'previous/MihuaAgent.exe').read_bytes() == b'old'
    assert_user_data(target)
    assert json.loads((work / 'install-status.json').read_text())['state'] == 'installed'


def test_failed_health_check_restores_old_program_and_all_data(tmp_path):
    target, work = transaction_dirs(tmp_path)
    def fail(path):
        assert_user_data(path)
        raise UpdateError('simulated unhealthy new version')
    with pytest.raises(UpdateError):
        install_staged(target, work, fail)
    assert (target / 'MihuaAgent.exe').read_bytes() == b'old'
    assert_user_data(target)
    assert json.loads((work / 'install-status.json').read_text())['state'] == 'rolled_back'


def test_locked_old_program_fails_without_touching_data(tmp_path, monkeypatch):
    target, work = transaction_dirs(tmp_path)
    rename = Path.rename
    def locked(path, destination):
        if path == target:
            raise PermissionError('file in use')
        return rename(path, destination)
    monkeypatch.setattr(Path, 'rename', locked)
    with pytest.raises(PermissionError):
        install_staged(target, work, lambda _: pytest.fail('health check must not run'))
    assert_user_data(target)
    assert (target / 'MihuaAgent.exe').read_bytes() == b'old'


def test_failure_during_data_handoff_restores_original_directory(tmp_path, monkeypatch):
    target, work = transaction_dirs(tmp_path)
    rename = Path.rename
    def fail(path, destination):
        if path == work / 'previous/runtime':
            raise PermissionError('runtime locked')
        return rename(path, destination)
    monkeypatch.setattr(Path, 'rename', fail)
    with pytest.raises(PermissionError):
        install_staged(target, work, lambda _: None)
    assert_user_data(target)
    assert (target / 'MihuaAgent.exe').read_bytes() == b'old'


def test_install_rejects_workspace_outside_installation_parent(tmp_path):
    target, work = transaction_dirs(tmp_path)
    with pytest.raises(UpdateError):
        validate_layout(target, work / 'nested')


def test_install_lock_blocks_second_start(tmp_path):
    target, _ = transaction_dirs(tmp_path)
    lock = InstallLock(target)
    try:
        assert update_in_progress(target)
        with pytest.raises(OSError):
            InstallLock(target)
    finally:
        lock.close()
    assert not update_in_progress(target)


def test_release_tool_generates_verifiable_manifest(signing, tmp_path):
    import importlib.util
    from cryptography.hazmat.primitives import serialization
    spec = importlib.util.spec_from_file_location('westie_release_tool', Path(__file__).resolve().parents[1] / 'packaging/release_updates.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    key_path = tmp_path / 'private.pem'
    key_path.write_bytes(signing[0].private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    package = tmp_path / 'MihuaAgent-api-win64.zip'
    package.write_bytes(package_bytes(signing[1]))
    result = module.prepare(package, key_path, tmp_path / 'publish', '本次改进')
    release = verify_manifest(result.read_bytes(), signing[1])
    assert release.version == '0.5.1' and release.notes == '本次改进'
    verify_package(package, release)


def test_handoff_timeout_stops_entire_onefile_process_tree(tmp_path, monkeypatch):
    from westie_agent import update_install
    target, work = transaction_dirs(tmp_path)
    (target / 'MihuaUpdater.exe').write_bytes(b'helper')
    process = type('Process', (), {'pid': 43210, 'poll': lambda self: None,
                                   'wait': lambda self, timeout: 1})()
    monkeypatch.setattr(update_install.subprocess, 'Popen', lambda *a, **k: process)
    captured = []
    monkeypatch.setattr(update_install.subprocess, 'run', lambda args, **k: captured.append(args))
    ticks = iter([0, 121])
    monkeypatch.setattr(update_install.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(UpdateError, match='助手未能准备'):
        launch_helper(target, work)
    assert captured == [['taskkill', '/PID', '43210', '/T', '/F']]
    assert (target / 'MihuaAgent.exe').read_bytes() == b'old'
    assert_user_data(target)
