"""Pinned portable llama.cpp + verified Qwen3 GGUF. No Python/Ollama install."""
from __future__ import annotations

import hashlib
import concurrent.futures
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .runtime import Cancelled, SetupRequired, WindowsJob, safe_extract, LOCAL_OPENER

LLAMA_VERSION = "b12003"
LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_VERSION}/llama-{LLAMA_VERSION}-bin-win-vulkan-x64.zip"
LLAMA_HASH = "057817bae95e91506872b6506a5264e932049d659fd93a49817174e72754aed4"
LLAMA_BYTES = 37497934
MODELS = {
    "qwen3:4b": ("7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5", 2497280256),
    "qwen3:1.7b": ("3d0b790534fe4b79525fc3692950408dca41171676ed7e21db57af5c65ef6ab6", 1359279776),
}
LICENSE_HASH = "d18a5cc71b84bc4af394a31116bd3932b42241de70c77d2b76d69a314ec8aa12"
REGISTRY = "https://registry.ollama.ai/v2/library/qwen3/blobs/sha256:"
MODEL_URLS = {
    "qwen3:4b": "https://modelscope.cn/models/Qwen/Qwen3-4B-GGUF/resolve/8cca206c792af04f8d452368bd30b43ec18007f1/Qwen3-4B-Q4_K_M.gguf",
    "qwen3:1.7b": REGISTRY + MODELS["qwen3:1.7b"][0],
}


def parallel_model_download(url, target, digest, size, progress, cancel, workers=8):
    """Bounded ranged transfers; retain completed pieces and verify final SHA256."""
    marker = target.with_suffix(target.suffix + ".verified.json")
    if target.exists() or marker.exists():
        return verified_download(url, target, digest, size, progress, cancel)
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": "WestieAgent/0.2"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            ranged = response.status == 206 and response.headers.get("Content-Range", "").endswith("/" + str(size))
    except urllib.error.HTTPError:
        ranged = False
    if not ranged:
        return verified_download(url, target, digest, size, progress, cancel)
    target.parent.mkdir(parents=True, exist_ok=True)
    pieces = target.parent / (target.name + "." + digest[:8] + ".pieces")
    pieces.mkdir(exist_ok=True)
    block = 32 * 1024**2
    ranges = [(i, start, min(size, start + block) - start) for i, start in enumerate(range(0, size, block))]
    # Reuse complete blocks from an earlier sequential download.
    partial = target.with_suffix(target.suffix + "." + digest[:8] + ".part")
    if partial.exists():
        with partial.open("rb") as old:
            for i, start, length in ranges:
                if start + length > partial.stat().st_size:
                    break
                piece = pieces / f"{i:04}.bin"
                if not piece.exists():
                    old.seek(start)
                    piece.write_bytes(old.read(length))
    amounts = {i: length if (pieces / f"{i:04}.bin").exists() and (pieces / f"{i:04}.bin").stat().st_size == length else 0
               for i, _, length in ranges}
    lock = threading.Lock()
    failed = threading.Event()
    def fetch(item):
        i, start, length = item
        if amounts[i] == length:
            return
        piece = pieces / f"{i:04}.bin"
        for attempt in range(3):
            try:
                headers = {"Range": f"bytes={start}-{start + length - 1}", "User-Agent": "WestieAgent/0.2"}
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as response:
                    expected_range = f"bytes {start}-{start + length - 1}/{size}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected_range:
                        raise OSError("分片下载范围不匹配")
                    with piece.open("wb") as output:
                        amount = 0
                        while True:
                            if cancel.is_set() or failed.is_set():
                                raise Cancelled()
                            chunk = response.read(256 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                            amount += len(chunk)
                            with lock:
                                amounts[i] = amount
                    if amount != length:
                        raise OSError("分片下载未完成")
                return
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    failed.set()
                    raise SetupRequired("模型下载中断，点击重试可继续已完成的分片。")
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="model-download") as pool:
        pending = {pool.submit(fetch, item) for item in ranges}
        while pending:
            done, pending = concurrent.futures.wait(pending, timeout=1, return_when=concurrent.futures.FIRST_EXCEPTION)
            for future in done:
                future.result()
            with lock:
                amount = sum(amounts.values())
            progress(f"下载模型：{amount / size:.0%}（{amount / 1024**2:.0f}/{size / 1024**2:.0f} MB）")
            if cancel.is_set():
                failed.set()
                raise Cancelled()
    progress("正在合并并校验本地模型…")
    assembling = target.with_suffix(target.suffix + ".assembling")
    sha = hashlib.sha256()
    with assembling.open("wb") as output:
        for i, _, _ in ranges:
            with (pieces / f"{i:04}.bin").open("rb") as part:
                for chunk in iter(lambda: part.read(4 * 1024**2), b""):
                    if cancel.is_set():
                        raise Cancelled()
                    output.write(chunk)
                    sha.update(chunk)
    if sha.hexdigest() != digest or assembling.stat().st_size != size:
        # Only remove this downloader's named segments, never arbitrary files.
        for i, _, _ in ranges:
            (pieces / f"{i:04}.bin").unlink(missing_ok=True)
        assembling.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
        raise SetupRequired("模型校验失败，损坏的下载分片已清理，请重新下载。")
    assembling.replace(target)
    marker.write_text(json.dumps({"sha256": digest, "size": size}), encoding="utf-8")
    for i, _, _ in ranges:
        (pieces / f"{i:04}.bin").unlink()
    pieces.rmdir()
    partial.unlink(missing_ok=True)


def verified_download(url: str, target: Path, digest: str, size: int,
                      progress: Callable[[str], None], cancel: threading.Event):
    marker = target.with_suffix(target.suffix + ".verified.json")
    if target.exists() and marker.exists():
        info = json.loads(marker.read_text(encoding="utf-8"))
        if info.get("sha256") == digest and target.stat().st_size == size:
            return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    if not target.exists():
        for attempt in range(3):
            received = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": "WestieAgent/0.2"}
            if received:
                headers["Range"] = f"bytes={received}-"
            try:
                try:
                    response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25)
                except urllib.error.HTTPError as exc:
                    # Some download gateways do not support HTTP Range.
                    if not received or exc.code not in (400, 416, 501):
                        raise
                    received = 0
                    response = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "WestieAgent/0.2"}), timeout=25)
                with response:
                    if received and response.status != 206:
                        received = 0
                    last = 0.0
                    with partial.open("ab" if received else "wb") as output:
                        while True:
                            if cancel.is_set():
                                raise Cancelled()
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                            received += len(chunk)
                            if time.monotonic() - last >= 1:
                                progress(f"下载中：{received / size:.0%}（{received / 1024**2:.0f}/{size / 1024**2:.0f} MB）")
                                last = time.monotonic()
                if received != size:
                    raise OSError("下载尚未完整")
                partial.replace(target)
                break
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise SetupRequired("下载失败，请检查网络后重试；支持断点续传的服务器会保留已下载进度。")
    progress("正在校验文件完整性…")
    sha = hashlib.sha256()
    with target.open("rb") as data:
        for chunk in iter(lambda: data.read(4 * 1024**2), b""):
            if cancel.is_set():
                raise Cancelled()
            sha.update(chunk)
    if sha.hexdigest() != digest or target.stat().st_size != size:
        target.unlink()
        raise SetupRequired("文件校验失败，请重新下载。")
    marker.write_text(json.dumps({"sha256": digest, "size": size}), encoding="utf-8")


class PortableRuntime:
    def __init__(self, base: Path):
        self.base = base
        self.process = None
        self.job = None
        self.log = None
        self.url = ""
        self.active_model = ""
        self.api_key = ""
        self._lock = threading.RLock()

    def model_path(self, model: str) -> Path:
        if model not in MODELS:
            raise ValueError("不支持的模型")
        return self.base / "data" / "models" / (model.replace(":", "-") + ".gguf")

    def has_model(self, model: str) -> bool:
        path = self.model_path(model)
        marker = path.with_suffix(".gguf.verified.json")
        return path.is_file() and path.stat().st_size == MODELS[model][1] and marker.is_file()

    def ready_on_disk(self, model: str) -> bool:
        return (self.base / "runtime" / "llama" / "llama-server.exe").is_file() and self.has_model(model)

    def prepare(self, model: str, progress: Callable[[str], None], cancel: threading.Event):
        if self.ready_on_disk(model):
            progress("本地模型和运行组件均已就绪。")
            return
        self.base.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.base).free < 2 * MODELS[model][1] + 350 * 1024**2:
            raise SetupRequired("磁盘空间不足，请把整个文件夹移到空间更充足的位置。")
        runtime_dir = self.base / "runtime" / "llama"
        runtime_marker = runtime_dir / "verified-runtime.json"
        if not (runtime_dir / "llama-server.exe").exists() or not runtime_marker.exists():
            runtime_zip = self.base / "data" / "cache" / (LLAMA_VERSION + "-vulkan.zip")
            progress("准备便携运行组件（约 36 MB）…")
            verified_download(LLAMA_URL, runtime_zip, LLAMA_HASH, LLAMA_BYTES, progress, cancel)
            if cancel.is_set():
                raise Cancelled()
            self.close()
            safe_extract(runtime_zip, runtime_dir)
            runtime_marker.write_text(json.dumps({"version": LLAMA_VERSION, "sha256": LLAMA_HASH}), encoding="utf-8")
        model_hash, model_size = MODELS[model]
        progress(f"准备 {model} 模型…")
        parallel_model_download(MODEL_URLS[model], self.model_path(model), model_hash, model_size, progress, cancel)
        license_path = self.base / "licenses" / "Qwen3-APACHE-2.0.txt"
        verified_download(REGISTRY + LICENSE_HASH, license_path, LICENSE_HASH, 11338, progress, cancel)
        progress("本地模型准备完成，现在可以离线使用。")

    def start(self, model: str, context_size: int = 8192, cancel: threading.Event | None = None, *, _cpu_only=False):
        cancel = cancel or threading.Event()
        with self._lock:
            if self.process and self.process.poll() is None and self.active_model == model:
                return
            self.close()
            if not self.ready_on_disk(model):
                raise SetupRequired("请先点击「设置」→「准备本地模型」。")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            executable = self.base / "runtime" / "llama" / "llama-server.exe"
            logs = self.base / "data" / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            self.log = (logs / "model-runtime.log").open("ab")
            log_offset = self.log.tell()
            if _cpu_only:
                self.log.write(b'\nWestie: GPU allocation failed; retrying once with CPU inference.\n')
                self.log.flush()
            command = [str(executable), "--model", str(self.model_path(model)),
                       "--alias", model, "--host", "127.0.0.1", "--port", str(port),
                       "--ctx-size", str(context_size), "--parallel", "1", "--jinja",
                       "--reasoning", "off", "--reasoning-budget", "0", "--n-gpu-layers", "0" if _cpu_only else "99",
                       "--threads", str(max(1, min(8, (os.cpu_count() or 4) // 2))),
                       "--sleep-idle-seconds", "120"]
            if _cpu_only:
                command += ['--device', 'none', '--no-op-offload', '--no-kv-offload',
                            '--no-repack', '--batch-size', '128', '--ubatch-size', '128']
            self.api_key = secrets.token_urlsafe(32)
            self.process = subprocess.Popen(command, cwd=executable.parent,
                                            env=dict(os.environ, LLAMA_API_KEY=self.api_key),
                                            stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            process = self.process
            self.job = WindowsJob(process)
            self.url = f"http://127.0.0.1:{port}"
            self.active_model = model
        for _ in range(600):
            if cancel.is_set():
                self.close()
                raise Cancelled()
            if process.poll() is not None:
                self.log.flush()
                with (logs / "model-runtime.log").open('rb') as failure_log:
                    failure_log.seek(log_offset)
                    failure = failure_log.read()[-65536:].lower()
                self.close()
                if not _cpu_only and any(marker in failure for marker in (
                        b'erroroutofdevicememory', b'failed to allocate vulkan',
                        b'unable to allocate vulkan', b'device memory allocation')):
                    return self.start(model, context_size, cancel, _cpu_only=True)
                raise SetupRequired("本地模型启动失败，请查看 data/logs/model-runtime.log。")
            try:
                request = urllib.request.Request(self.url + "/health", headers={"Authorization": "Bearer " + self.api_key})
                with LOCAL_OPENER.open(request, timeout=.5) as response:
                    if json.load(response).get("status") == "ok":
                        return
            except (OSError, ValueError):
                cancel.wait(.2)
        self.close()
        raise SetupRequired("模型加载超时，请关闭占用内存的程序后重试。")

    def ensure_ready(self, model: str, cancel: threading.Event, context_size: int = 8192):
        self.start(model, context_size, cancel)

    def close(self):
        with self._lock:
            process, self.process = self.process, None
            if self.job and self.job.handle:
                self.job.close()
            elif process and process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                else:
                    process.terminate()
            if process:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            if self.log:
                self.log.close()
                self.log = None
            self.url, self.active_model, self.api_key = "", "", ""
