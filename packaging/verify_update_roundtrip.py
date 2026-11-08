"""Verify actual Windows installer and frozen updater using synthetic data only."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from westie_agent.credentials import SecretStore
from westie_agent.settings import Settings, DEEPSEEK_URL
from westie_agent.updates import UpdateConfig, extract_package, verify_manifest, verify_package


def process_ids(executable):
    path = str(executable.resolve()).replace("'", "''")
    command = "Get-Process -Name MihuaAgent -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq '" + path + "' } | Select-Object -ExpandProperty Id"
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return [int(n) for n in result.stdout.split() if n.isdigit()]


def request_exit(pids):
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
    user.EnumWindows.argtypes = [callback, W.LPARAM]
    threads = set()
    def visit(handle, _):
        pid = W.DWORD()
        thread = user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value in pids:
            threads.add(thread)
        return True
    user.EnumWindows(callback(visit), 0)
    if not threads:
        # The Qt offscreen plugin has a message queue but no EnumWindows HWND.
        # Enumerate only threads belonging to the exact synthetic test PIDs.
        class ThreadEntry(ctypes.Structure):
            _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('thread', W.DWORD),
                        ('owner', W.DWORD), ('priority', W.LONG), ('delta', W.LONG), ('flags', W.DWORD)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
        kernel.Thread32First.argtypes = kernel.Thread32Next.argtypes = [W.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise RuntimeError('Could not enumerate synthetic test threads')
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            more = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.owner in pids:
                    threads.add(entry.thread)
                more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
    delivered = [thread for thread in threads if user.PostThreadMessageW(thread, 0x12, 0, 0)]
    if not delivered:
        raise RuntimeError('Could not signal synthetic test application message queue')


def wait_file(path, process, timeout=120):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('Frozen helper did not become ready; inspect ' + str(path.parent))
        time.sleep(.2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/update-roundtrip.json')
    args = parser.parse_args()
    release_dir = args.release_dir.resolve()
    original = release_dir / 'MihuaAgent'
    config = UpdateConfig.load(original)
    manifest = (release_dir / 'publish/latest.json').read_bytes()
    release = verify_manifest(manifest, config)
    archive = release_dir / 'MihuaAgent-api-win64.zip'
    verify_package(archive, release)
    sandbox = Path(tempfile.mkdtemp(prefix='Mihua-update-e2e-')).resolve()
    target = sandbox / '米花 测试安装'
    work = sandbox / '.MihuaAgent-update-roundtrip'
    report = {'ok': False, 'version': release.version, 'github_requests': 0, 'paid_api_calls': 0}
    process = helper = None
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONUTF8='1')
    for name in ('PYTHONHOME', 'PYTHONPATH', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY'):
        env.pop(name, None)
    try:
        # NSIS /D= and _?= must be unquoted final command-line tails.
        subprocess.run('"' + str(args.installer.resolve()) + '" /S /TESTMODE /D=' + str(target),
                       check=True, timeout=180)
        assert (target / '米花.exe').is_file() and (target / '卸载米花.exe').is_file()
        info = json.loads((target / 'build-info.json').read_text(encoding='utf-8'))
        # Use the current frozen application with older metadata to exercise the
        # real process handoff; no actual old installation is used or modified.
        info['version'] = '0.5.27'
        (target / 'build-info.json').write_text(json.dumps(info), encoding='utf-8')
        Settings(tools_mode='mock').save(target)
        SecretStore(target).save(DEEPSEEK_URL, 'mihua-test-only-not-a-provider-key')
        (target / 'data/update-preferences.json').write_text('{"automatic":false}', encoding='utf-8')
        (target / 'data/notes').mkdir(exist_ok=True)
        (target / 'data/notes/keep.txt').write_text('保留的合成笔记', encoding='utf-8')
        (target / 'data/models').mkdir(exist_ok=True)
        (target / 'data/models/test.gguf').write_bytes(b'synthetic-local-model-marker')
        preserve = {str(p.relative_to(target)): p.read_bytes() for p in (target / 'data').rglob('*') if p.is_file()}
        (target / 'unknown-user-file.txt').write_bytes(b'keep unknown file in backup')
        work.mkdir()
        shutil.copy2(archive, work / 'package.zip')
        (work / 'manifest.json').write_bytes(manifest)
        extract_package(work / 'package.zip', work / 'staged', release, config)
        process = subprocess.Popen([str(target / 'MihuaAgent.exe'), '--no-tray', '--quit-after', '240000'],
                                   cwd=target, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(3)
        if process.poll() is not None:
            raise RuntimeError('Synthetic installed application failed to start')
        shutil.copy2(target / 'MihuaUpdater.exe', work / 'MihuaUpdater.exe')
        helper = subprocess.Popen([str(work / 'MihuaUpdater.exe'), '--target', str(target),
                                   '--work', str(work), '--parent-pid', str(process.pid)],
                                  cwd=work, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        wait_file(work / 'ready', helper)
        request_exit({process.pid})
        assert process.wait(timeout=30) == 0
        assert helper.wait(timeout=180) == 0
        state = json.loads((work / 'install-status.json').read_text(encoding='utf-8'))
        assert state['state'] == 'installed', state
        for name, content in preserve.items():
            assert (target / name).read_bytes() == content, 'Synthetic data changed: ' + name
        assert SecretStore(target).load(DEEPSEEK_URL) == 'mihua-test-only-not-a-provider-key'
        assert (work / 'previous/unknown-user-file.txt').read_bytes() == b'keep unknown file in backup'
        assert json.loads((target / 'build-info.json').read_text(encoding='utf-8'))['version'] == release.version
        health = json.loads((work / 'health-check.json').read_text(encoding='utf-8'))
        assert health['ok'] and health['update_entry_ok'] and health['frozen']
        time.sleep(3)
        restarted = process_ids(target / 'MihuaAgent.exe')
        assert restarted, 'Updated application did not restart'
        request_exit(set(restarted))
        deadline = time.monotonic() + 30
        while process_ids(target / 'MihuaAgent.exe') and time.monotonic() < deadline:
            time.sleep(.3)
        assert not process_ids(target / 'MihuaAgent.exe')
        # The new uninstall manifest removes the helper and app, preserving data
        # and unknown root files, without touching the real registry/shortcuts.
        (target / 'unknown-after-update.txt').write_bytes(b'keep')
        subprocess.run('"' + str(target / '卸载米花.exe') + '" /S /TESTMODE _?=' + str(target),
                       cwd=sandbox, check=True, timeout=90)
        assert not (target / 'MihuaAgent.exe').exists() and not (target / 'MihuaUpdater.exe').exists()
        assert (target / 'data/notes/keep.txt').read_bytes() == preserve['data/notes/keep.txt']
        assert (target / 'unknown-after-update.txt').read_bytes() == b'keep'
        report.update(ok=True, installer_test_mode=True, frozen_parent_identity_verified=True,
                      helper_signature_rechecked=True, graceful_exit=True, startup_health_check=health,
                      preserved_user_files=len(preserve), encrypted_key_decrypts=True,
                      backup_created=True, updated_application_restarted=True,
                      uninstall_preserves_data_and_unknown_files=True)
    except BaseException as exc:
        report.update(error=str(exc), sandbox=str(sandbox))
        raise
    finally:
        if helper and helper.poll() is None:
            # Only this unique test helper tree may be terminated during cleanup.
            subprocess.run(['taskkill', '/PID', str(helper.pid), '/T', '/F'], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        for pid in process_ids(target / 'MihuaAgent.exe'):
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        # Verify the resolved absolute target is the unique temporary test root.
        if report['ok'] and sandbox.parent == Path(tempfile.gettempdir()).resolve() and sandbox.name.startswith('Mihua-update-e2e-'):
            shutil.rmtree(sandbox)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
