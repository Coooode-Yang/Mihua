"""Signed GitHub Releases updates. No credentials or paid model calls are used."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import socket
import ssl
import stat
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import __version__ as VERSION

MAX_MANIFEST = 128 * 1024
MAX_PACKAGE = 2 * 1024**3 - 1
MAX_EXPANDED = 8 * 1024**3
MAX_FILES = 40000
EXECUTABLE = "MihuaAgent.exe"


class UpdateError(ValueError):
    pass


class UpdateCancelled(UpdateError):
    pass


class UpdateConnectionError(UpdateError):
    def __init__(self, reason, status=None):
        self.reason, self.status = reason, status
        self.retryable = reason in {'timeout', 'connection'} or (reason == 'http' and status is not None and 500 <= status < 600)
        messages = {
            'timeout': '连接 GitHub 超时，请稍后重试，或检查系统代理是否正常。',
            'dns': '无法解析 GitHub 地址，请检查网络或 DNS 设置后重试。',
            'tls': 'GitHub 的 HTTPS 证书校验失败，请检查系统时间和证书设置。',
            'proxy': '无法连接更新所用的系统代理，请检查代理是否已启动。',
            'connection': '与 GitHub 的连接中断，请检查网络或系统代理后重试。',
        }
        if reason == 'http':
            text = f'GitHub 更新请求未成功（HTTP {status}），请稍后重试。'
        else:
            text = messages.get(reason, '更新连接失败，请检查网络后重试。')
        super().__init__(text)


def connection_error(exc):
    chain=[]
    item=exc
    while item is not None and len(chain)<10:
        chain.append(item)
        item=item.__cause__
    if isinstance(exc, httpx.HTTPStatusError):
        return UpdateConnectionError('http', exc.response.status_code)
    if any(isinstance(e, httpx.ProxyError) for e in chain):
        return UpdateConnectionError('proxy')
    if any(isinstance(e, ssl.SSLError) or 'CERTIFICATE_VERIFY_FAILED' in str(e) for e in chain):
        return UpdateConnectionError('tls')
    if any(isinstance(e, socket.gaierror) or 'getaddrinfo failed' in str(e).lower() for e in chain):
        return UpdateConnectionError('dns')
    if isinstance(exc, httpx.TimeoutException):
        return UpdateConnectionError('timeout')
    return UpdateConnectionError('connection')


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})", value):
        raise UpdateError("版本号需要使用 major.minor.patch 格式。")
    return tuple(map(int, value.split(".")))


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_json(raw: bytes):
    if len(raw) > MAX_MANIFEST:
        raise UpdateError("更新信息文件过大。")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise UpdateError("更新信息包含重复字段。")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(UpdateError("更新信息格式无效。")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise UpdateError("更新信息格式无效。") from exc


@dataclass(frozen=True)
class UpdateConfig:
    repository: str = ""
    public_key: str = ""
    variant: str = "api"
    manifest_asset: str = "latest.json"

    def validate(self):
        if not isinstance(self.repository, str) or not isinstance(self.public_key, str):
            raise UpdateError("更新配置格式无效。")
        if self.repository and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", self.repository):
            raise UpdateError("GitHub 仓库应使用 账号/仓库名 格式。")
        if self.variant not in {"api", "offline"} or self.manifest_asset != ("latest.json" if self.variant == "api" else "latest-offline.json"):
            raise UpdateError("更新通道配置无效。")
        if self.public_key:
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(self.public_key, validate=True))
            except (ValueError, TypeError) as exc:
                raise UpdateError("更新公钥无效。") from exc
        if bool(self.repository) != bool(self.public_key):
            raise UpdateError("更新仓库和验证公钥需要一起配置。")
        return self

    @property
    def enabled(self):
        return bool(self.repository and self.public_key)

    @property
    def manifest_url(self):
        return f"https://github.com/{self.repository}/releases/latest/download/{self.manifest_asset}"

    @classmethod
    def load(cls, base: Path):
        path = base / "update-config.json"
        if not path.exists():
            return cls()
        try:
            value = read_json(path.read_bytes())
            if not isinstance(value, dict):
                raise UpdateError("更新配置格式无效。")
            return cls(**value).validate()
        except (OSError, TypeError) as exc:
            raise UpdateError("无法读取更新配置。") from exc


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int
    notes: str
    envelope: dict


def verify_manifest(raw: bytes, config: UpdateConfig) -> Release:
    config.validate()
    if not config.enabled:
        raise UpdateError("发布者尚未配置更新仓库和验证公钥。")
    envelope = read_json(raw)
    if not isinstance(envelope, dict):
        raise UpdateError("更新信息格式无效。")
    try:
        payload = envelope["manifest"]
        signature = base64.b64decode(envelope["signature"], validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(config.public_key, validate=True))
        key.verify(signature, canonical_json(payload))
    except (InvalidSignature, KeyError, TypeError, ValueError) as exc:
        raise UpdateError("更新信息签名验证失败，已停止更新。") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1 or payload.get("repository") != config.repository or payload.get("variant") != config.variant or payload.get("platform") != "windows-x64":
        raise UpdateError("更新包与本程序的仓库、平台或版本通道不匹配。")
    version = payload.get("version")
    version_tuple(version)
    name = payload.get("asset")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}\.zip", name):
        raise UpdateError("更新包文件名无效。")
    digest, size, notes = payload.get("sha256"), payload.get("size"), payload.get("notes", "")
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest) or type(size) is not int or not 0 < size <= MAX_PACKAGE:
        raise UpdateError("更新包的大小或校验信息无效。")
    if not isinstance(notes, str) or len(notes) > 16000:
        raise UpdateError("更新说明格式无效。")
    # Derive the URL rather than accepting an arbitrary executable URL.
    url = f"https://github.com/{config.repository}/releases/download/v{version}/{quote(name)}"
    return Release(version, url, digest, size, notes, envelope)


def installed_version(base: Path) -> str:
    path = base / "build-info.json"
    if not path.exists():
        return VERSION
    try:
        value = read_json(path.read_bytes())["version"]
        version_tuple(value)
        return value
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise UpdateError("无法确定当前版本，已停止自动更新。") from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_package(path: Path, release: Release):
    if not path.is_file() or path.stat().st_size != release.size or file_sha256(path) != release.sha256:
        raise UpdateError("更新包大小或 SHA-256 校验失败，已停止安装。")


def _allowed_url(url: str, repository: str):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise UpdateError("更新地址必须使用可信 HTTPS 连接。")
    if parsed.hostname == "github.com":
        if not unquote(parsed.path).startswith(f"/{repository}/releases/"):
            raise UpdateError("更新地址不属于配置的仓库。")
    elif parsed.hostname not in {"release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}:
        raise UpdateError("更新下载被重定向到未知站点。")


class GitHubUpdater:
    def __init__(self, config: UpdateConfig, current: str, cancel=None, client=None):
        self.config = config.validate()
        self.current = current
        version_tuple(current)
        self.cancel = cancel or threading.Event()
        self.client = client

    def _fetch(self, url, limit, write, progress=lambda _: None):
        started = time.monotonic()
        client = None
        try:
            if self.cancel.is_set():
                raise UpdateCancelled("更新已取消。")
            client = self.client or httpx.Client(timeout=httpx.Timeout(15, connect=8), follow_redirects=False,
                                               headers={"User-Agent": "MihuaAgent-Updater/" + VERSION})
            for _ in range(6):
                _allowed_url(url, self.config.repository)
                if self.cancel.is_set():
                    raise UpdateCancelled("更新已取消。")
                with client.stream("GET", url, follow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise UpdateError("更新地址缺少跳转目标。")
                        url = str(response.url.join(location))
                        continue
                    if response.status_code == 404:
                        raise UpdateError("尚未找到正式更新版本，请稍后重试。")
                    response.raise_for_status()
                    count = 0
                    for block in response.iter_bytes(256 * 1024):
                        if self.cancel.is_set():
                            raise UpdateCancelled("更新已取消。")
                        if time.monotonic() - started > 1800:
                            raise UpdateError("更新下载超时，请稍后重试。")
                        count += len(block)
                        if count > limit:
                            raise UpdateError("下载内容超过声明大小，已停止。")
                        write(block)
                        progress(count)
                    return
            raise UpdateError("更新地址跳转次数过多。")
        except httpx.HTTPError as exc:
            raise connection_error(exc) from exc
        finally:
            if self.client is None and client is not None:
                client.close()

    def check(self):
        if not self.config.enabled:
            raise UpdateError("发布者尚未配置更新仓库，当前版本仍可正常使用。")
        for attempt in range(3):
            data = bytearray()
            try:
                self._fetch(self.config.manifest_url, MAX_MANIFEST, data.extend)
                break
            except UpdateConnectionError as exc:
                self.wait_for_retry(exc, attempt)
        release = verify_manifest(bytes(data), self.config)
        return release if version_tuple(release.version) > version_tuple(self.current) else None

    def wait_for_retry(self, error, attempt):
        if not error.retryable or attempt >= 2:
            raise error
        if self.cancel.wait(.4 * (attempt + 1)):
            raise UpdateCancelled("更新已取消。")

    def download(self, release: Release, base: Path, progress=lambda _: None) -> Path:
        base = base.resolve()
        # Use a sibling so application/data directory moves stay on one volume.
        work = Path(tempfile.mkdtemp(prefix=".MihuaAgent-update-", dir=base.parent))
        try:
            if shutil.disk_usage(base.parent).free < release.size * 2 + 256 * 1024**2:
                raise UpdateError("磁盘空间不足，无法下载和展开更新。")
            archive = work / "package.zip"
            for attempt in range(3):
                try:
                    with archive.open("xb" if attempt == 0 else "wb") as stream:
                        self._fetch(release.url, release.size, stream.write,
                                    lambda count: progress(min(99, int(count * 100 / release.size))))
                    break
                except UpdateConnectionError as exc:
                    self.wait_for_retry(exc, attempt)
            verify_package(archive, release)
            (work / "manifest.json").write_bytes(canonical_json(release.envelope))
            extract_package(archive, work / "staged", release, self.config, self.cancel)
            progress(100)
            return work
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise


def extract_package(archive: Path, output: Path, release: Release, config: UpdateConfig, cancel=None) -> Path:
    """Validate ALL entries before writing anything; never extract publisher data."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            total = 0
            seen = set()
            if len(entries) > MAX_FILES:
                raise UpdateError("更新包文件数量过多。")
            for info in entries:
                # ZipInfo normalizes backslashes on Windows and truncates NUL;
                # validate its original header name before that normalization.
                name = info.orig_filename
                parts = tuple(name.rstrip('/').split('/'))
                mode = info.external_attr >> 16
                if (len(parts) < 2 and not info.is_dir() or not parts or parts[0] != "MihuaAgent"
                        or "\\" in name or name.startswith("/") or info.flag_bits & 1
                        or any(p in {"", ".", ".."} or p.endswith((".", " ")) or ":" in p
                               or any(ord(c) < 32 for c in p)
                               or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", p) for p in parts)
                        or stat.S_ISLNK(mode) or (mode & 0o170000) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise UpdateError("更新包包含不安全的文件路径或链接。")
                normalized = "/".join(parts).casefold()
                if normalized in seen:
                    raise UpdateError("更新包包含重复文件。")
                seen.add(normalized)
                total += info.file_size
                if total > MAX_EXPANDED:
                    raise UpdateError("更新包解压后体积过大。")
            files = {i.orig_filename.rstrip('/').casefold() for i in entries if not i.is_dir()}
            for info in entries:
                parts = info.orig_filename.rstrip('/').split('/')
                if any('/'.join(parts[:n]).casefold() in files for n in range(1, len(parts))):
                    raise UpdateError("更新包包含冲突的文件和目录。")
            if shutil.disk_usage(output.parent).free < total + 128 * 1024**2:
                raise UpdateError("磁盘空间不足，无法展开更新。")
            for info in entries:
                if cancel and cancel.is_set():
                    raise UpdateCancelled("更新已取消。")
                parts = PurePosixPath(info.filename).parts
                if len(parts) > 1 and parts[1].casefold() == "data":
                    continue
                destination = output.joinpath(*parts)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(info) as source, destination.open("xb") as target:
                        shutil.copyfileobj(source, target, 1024 * 1024)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise UpdateError("更新包无法解压。") from exc
    package = output / "MihuaAgent"
    if not (package / EXECUTABLE).is_file() or not (package / "_internal").is_dir() or not (package / "MihuaUpdater.exe").is_file():
        raise UpdateError("更新包缺少程序、依赖或更新助手。")
    if installed_version(package) != release.version or not (package / "build-info.json").is_file():
        raise UpdateError("更新包内版本与发布信息不一致。")
    if UpdateConfig.load(package) != config:
        raise UpdateError("更新包改变了更新来源或公钥，已停止安装。")
    return package
