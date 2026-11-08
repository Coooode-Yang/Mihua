"""Detached Windows installer and reversible directory swap.

The helper runs outside the application folder and waits for the actual GUI
process handle. It never overwrites the user's data with publisher defaults.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .updates import (EXECUTABLE, UpdateConfig, UpdateError, extract_package,
                      installed_version, verify_manifest, verify_package, version_tuple)


def lock_path(target: Path):
    digest = hashlib.sha256(os.path.normcase(str(target.resolve())).encode()).hexdigest()[:16]
    return target.parent / (".MihuaAgent-update-lock-" + digest + ".lock")


class InstallLock:
    def __init__(self, target):
        import msvcrt
        self.stream = lock_path(target).open("a+b")
        try:
            self.stream.seek(0, 2)
            if not self.stream.tell():
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except BaseException:
            self.stream.close()
            raise

    def close(self):
        self.stream.close()


def update_in_progress(target):
    if os.name != "nt" or not lock_path(target).exists():
        return False
    try:
        lock = InstallLock(target)
    except OSError:
        return True
    lock.close()
    return False


class WindowsParent:
    def __init__(self, pid: int, target: Path):
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            raise UpdateError("原程序已经退出，请重新启动后安装更新。")
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not self.kernel.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
            self.close()
            raise UpdateError("无法确认正在更新的程序。")
        if os.path.normcase(str(Path(buffer.value).resolve())) != os.path.normcase(str((target / EXECUTABLE).resolve())):
            self.close()
            raise UpdateError("更新目标与运行中的程序不一致。")

    def wait(self):
        if self.kernel.WaitForSingleObject(self.handle, 120000) != 0:
            raise UpdateError("等待原程序退出超时，旧版本未被替换。")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def journal(work, **values):
    path = work / "install-status.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def validate_layout(target: Path, work: Path):
    if (target.parent != work.parent or not work.name.startswith(".MihuaAgent-update-")
            or target == work or target.name.startswith(".MihuaAgent-update-")
            or not (target / EXECUTABLE).is_file() or not work.is_dir()):
        raise UpdateError("更新工作目录与安装目录不匹配。")
    for directory in (target, work, target / "data", target / "runtime"):
        if directory.exists() and (directory.is_symlink() or getattr(directory.stat(), "st_file_attributes", 0) & 0x400):
            raise UpdateError("自动更新不支持带有目录链接的安装位置，请手动升级。")


def install_staged(target: Path, work: Path, health_check):
    """Swap on the same volume, move data once, restore on any ordinary failure."""
    validate_layout(target, work)
    staged, previous = work / "staged" / "MihuaAgent", work / "previous"
    if not staged.is_dir() or previous.exists() or (staged / "data").exists():
        raise UpdateError("更新暂存目录无效，请重新下载。")
    if (target / "runtime").exists() and not (staged / "runtime").exists():
        # Preserve an existing optional runtime when the update does not ship it.
        preserve_runtime = True
    else:
        preserve_runtime = False
    moved_data = moved_runtime = swapped = False
    journal(work, state="installing", target=str(target))
    try:
        target.rename(previous)
        if (previous / "data").exists():
            (previous / "data").rename(staged / "data")
            moved_data = True
        if preserve_runtime:
            (previous / "runtime").rename(staged / "runtime")
            moved_runtime = True
        staged.rename(target)
        swapped = True
        health_check(target)
        journal(work, state="installed", target=str(target), backup=str(previous))
    except BaseException as exc:
        if previous.exists():
            location = target if swapped else staged
            try:
                if moved_data and (location / "data").exists():
                    (location / "data").rename(previous / "data")
                if moved_runtime and (location / "runtime").exists():
                    (location / "runtime").rename(previous / "runtime")
                if swapped:
                    target.rename(work / "failed")
                previous.rename(target)
            except OSError as recovery:
                journal(work, state="recovery_required", target=str(target), error=str(exc), recovery_error=str(recovery))
                raise UpdateError("自动恢复未完成，请按更新文档从 previous 目录恢复；用户数据仍保存在更新目录中。") from recovery
        journal(work, state="rolled_back", target=str(target), error=str(exc))
        raise


def check_health(target: Path, work: Path, expected: str):
    output = work / "health-check.json"
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["QT_QPA_PLATFORM"] = "offscreen"
    system32 = Path(env.get("WINDIR", "C:/Windows")) / "System32"
    env["PATH"] = os.pathsep.join(map(str, (system32, system32 / "WindowsPowerShell" / "v1.0")))
    process = subprocess.Popen([str(target / EXECUTABLE), "--update-self-test", "--self-test-output", str(output)],
                               cwd=target, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        status = process.wait(timeout=90)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise UpdateError("新版启动检查超时。") from exc
    if status != 0 or not output.exists():
        raise UpdateError("新版启动检查失败。")
    report = json.loads(output.read_text(encoding="utf-8"))
    if not report.get("ok") or not report.get("frozen") or report.get("version") != expected or not report.get("update_entry_ok"):
        raise UpdateError("新版启动检查未通过。")


def launch_helper(target: Path, work: Path) -> None:
    validate_layout(target, work)
    helper = work / "MihuaUpdater.exe"
    shutil.copy2(target / "MihuaUpdater.exe", helper)
    (work / "ready").unlink(missing_ok=True)
    process = subprocess.Popen([str(helper), "--target", str(target), "--work", str(work),
                                "--parent-pid", str(os.getpid())], cwd=work,
                               creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if (work / "ready").exists():
            return
        if process.poll() is not None:
            break
        time.sleep(0.1)
    # The parent stays running. If the helper is still waiting, stop it before
    # telling the GUI it is safe to keep using this version.
    if process.poll() is None:
        # PyInstaller onefile has a bootloader parent and a Python child.
        # Terminating only the bootloader can leave the installer alive.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
        process.wait(timeout=10)
    raise UpdateError("更新助手未能准备好，当前程序未退出；请重试或手动下载新版。")


def run_helper(target: Path, work: Path, parent_pid: int, config: UpdateConfig):
    target, work = target.resolve(), work.resolve()
    parent = lock = None
    ready = parent_exited = False
    try:
        validate_layout(target, work)
        lock = InstallLock(target)
        parent = WindowsParent(parent_pid, target)
        release = verify_manifest((work / "manifest.json").read_bytes(), config)
        if version_tuple(release.version) <= version_tuple(installed_version(target)) or UpdateConfig.load(target) != config:
            raise UpdateError("更新目标版本或来源已变化，请重新检查。")
        verify_package(work / "package.zip", release)
        # Re-extract in the helper after verification; never trust editable staging.
        shutil.rmtree(work / "staged", ignore_errors=True)
        extract_package(work / "package.zip", work / "staged", release, config)
        journal(work, state="ready", target=str(target))
        (work / "ready").write_text("ready", encoding="ascii")
        ready = True
        parent.wait()
        parent_exited = True
        parent.close()
        install_staged(target, work, lambda path: check_health(path, work, release.version))
        # Keep previous binaries for manual recovery, but release the big ZIP.
        (work / "package.zip").unlink(missing_ok=True)
    except Exception as exc:
        if work.is_dir():
            (work / "update-error.txt").write_text(str(exc), encoding="utf-8")
        if ready:
            ctypes.windll.user32.MessageBoxW(None, "更新没有完成，已尝试恢复旧版本。\n" + str(exc) + "\n更新记录：" + str(work), "米花软件更新", 0x10)
        if not parent_exited or not (target / EXECUTABLE).is_file():
            raise
        try:
            state = json.loads((work / 'install-status.json').read_text(encoding='utf-8'))['state']
        except (OSError, ValueError, KeyError):
            state = None
        if state != 'rolled_back':
            # A partial recovery can leave new binaries at target while data
            # remains in previous. Do not start a fresh, misleading data tree.
            raise
    finally:
        if parent:
            parent.close()
        if lock:
            lock.close()
    subprocess.Popen([str(target / EXECUTABLE)], cwd=target, creationflags=subprocess.DETACHED_PROCESS)
