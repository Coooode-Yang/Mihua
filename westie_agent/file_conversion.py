"""Local Filee CLI adapter. Originals and existing outputs are never replaced."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

FILEE_VERSION = '2.2.0'
FILEE_ARCHIVE_SHA256 = '3e181d39a298105bef381b3fea64e6a08ff4d93416196517a21c22d65b5e6ac7'


class ConversionCancelled(Exception):
    pass


@dataclass(frozen=True)
class Capability:
    ready: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConversionMode:
    key: str
    title: str
    target: str
    merge: bool = False


MODES = (
    ConversionMode('pdf', 'PDF 文档', 'pdf'),
    ConversionMode('merge_pdf', '合并为一个 PDF（按列表顺序）', 'pdf', True),
    ConversionMode('docx', 'Word 文档（DOCX）', 'docx'),
    ConversionMode('png', 'PNG 图片（PDF 会逐页导出）', 'png'),
    ConversionMode('jpg', 'JPG 图片（PDF 会逐页导出）', 'jpg'),
    ConversionMode('webp', 'WebP 图片', 'webp'),
    ConversionMode('txt', '纯文本（TXT）', 'txt'),
    ConversionMode('pptx', 'PowerPoint（PDF 每页作为图片）', 'pptx'),
    ConversionMode('xlsx', 'Excel 工作簿（XLSX）', 'xlsx'),
    ConversionMode('csv', 'CSV 表格', 'csv'),
    ConversionMode('epub', 'EPUB 电子书', 'epub'),
    ConversionMode('html', 'HTML 文档', 'html'),
)
MODE_BY_ID = {mode.key: mode for mode in MODES}
RASTER_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff', '.avif', '.heic', '.ico', '.jxl', '.jp2', '.tga', '.psd', '.psb'}


@dataclass
class ConversionResult:
    source: str
    state: str
    outputs: list[str] = field(default_factory=list)
    error: str = ''
    notice: str = ''


def available_modes(paths, capabilities):
    if not paths:
        return []
    result = []
    for mode in MODES:
        if mode.merge:
            possible = len(paths) > 1 and all(
                path.suffix.lower() == '.pdf' or 'pdf' in capabilities[path.suffix.lower()].ready for path in paths)
        else:
            possible = all(mode.target in capabilities[path.suffix.lower()].ready for path in paths)
            if mode.target not in {'pdf', 'png', 'jpg', 'webp'} and any(path.suffix.lower() in RASTER_EXTENSIONS for path in paths):
                possible = False  # Do not offer image-to-text as if it were OCR.
        if possible:
            result.append(mode)
    return result


def validate_inputs(paths):
    result = []
    seen = set()
    for value in paths:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError('请添加具体文件，暂不接受文件夹。')
        key = os.path.normcase(str(path))
        if key not in seen:
            result.append(path)
            seen.add(key)
    if not result:
        raise ValueError('请先添加文件。')
    if len(result) > 200:
        raise ValueError('一次最多添加 200 个文件，请分批转换。')
    return result


def publish_output(source: Path, directory: Path):
    """Reserve a fresh name before copying; remove only our own incomplete file."""
    for index in range(1, 100001):
        name = source.name if index == 1 else f'{source.stem} ({index}){source.suffix}'
        target = directory / name
        try:
            os.link(source, target)
            return str(target)
        except FileExistsError:
            continue
        except OSError:
            pass  # Some removable/network file systems need an ordinary copy.
        try:
            stream = target.open('xb')
        except FileExistsError:
            continue
        try:
            with stream, source.open('rb') as original:
                shutil.copyfileobj(original, stream, 1024*1024)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return str(target)
    raise ValueError('同名文件过多，请选择新的输出文件夹。')


class FileeEngine:
    def __init__(self, base: Path):
        self.directory = Path(base) / 'runtime' / 'filee' / FILEE_VERSION
        self.executable = self.directory / 'filee-cli.exe'
        self._capabilities = {}

    @property
    def ready(self):
        return self.executable.is_file()

    @staticmethod
    def _stop(process):
        if process.poll() is not None:
            return
        if os.name == 'nt':
            taskkill = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32/taskkill.exe'
            try:
                subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.kill()

    def _invoke(self, arguments, cancel, timeout=300):
        if not self.ready:
            raise ValueError('文件转换组件未就绪，请使用完整的米花发行包。')
        if cancel.is_set():
            raise ConversionCancelled()
        command = [str(self.executable), *map(str, arguments)]
        if len(subprocess.list2cmdline(command)) > 28000:
            raise ValueError('文件路径过长或数量太多，请分批转换。')
        with tempfile.TemporaryDirectory(prefix='bai-filee-settings-') as settings:
            environment = dict(os.environ)
            environment['FILEE_DATA_DIR'] = settings
            environment.pop('DEEPSEEK_API_KEY', None)
            process = subprocess.Popen(command, cwd=self.directory, env=environment,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            started = time.monotonic()
            try:
                while True:
                    if cancel.is_set():
                        raise ConversionCancelled()
                    if time.monotonic() - started > timeout:
                        raise ValueError('转换时间较长，已停止本次处理；可以拆成更小的文件后重试。')
                    try:
                        output, error = process.communicate(timeout=0.15)
                        return process.returncode, output.decode('utf-8-sig', errors='replace'), error.decode('utf-8-sig', errors='replace')
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    self._stop(process)
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.stdout.close()
                    process.stderr.close()

    def capability(self, extension, cancel):
        extension = extension.lower()
        if extension in self._capabilities:
            return self._capabilities[extension]
        if not re.fullmatch(r'\.[a-z0-9]{1,16}', extension):
            return Capability()
        code, output, _ = self._invoke(['formats', extension], cancel, timeout=30)
        ready, optional = set(), set()
        if code == 0:
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            # Pinned Filee 2.2 CLI: heading, installed targets, optional heading,
            # optional targets. Never offer an optional engine as ready.
            if len(lines) > 1 and not lines[1].startswith('('):
                ready = set(lines[1].split(', '))
            for index, line in enumerate(lines):
                if line.startswith('With optional engines') and index+1 < len(lines):
                    optional = set(lines[index+1].split(', '))
        result = Capability(frozenset(ready), frozenset(optional))
        self._capabilities[extension] = result
        return result

    def discover(self, paths, cancel):
        return {extension: self.capability(extension, cancel) for extension in dict.fromkeys(path.suffix.lower() for path in paths)}

    def _convert_group(self, paths, mode, directory, cancel):
        with tempfile.TemporaryDirectory(prefix='.bai-convert-', dir=directory) as folder:
            staging = Path(folder).resolve()
            arguments = ['convert', *paths]
            arguments += ['--preset', 'merge-pdf'] if mode.merge else ['--to', mode.target]
            arguments += ['--output', staging, '--json']
            code, output, error = self._invoke(arguments, cancel)
            if cancel.is_set():
                raise ConversionCancelled()
            try:
                data = json.loads(output)
                rows = data['files']
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError('转换引擎返回的文件清单无效。')
                sources = {os.path.normcase(str(path)) for path in paths}
                reported = {os.path.normcase(str(Path(row.get('source', '')).resolve())) for row in rows}
                if reported != sources or len(rows) != len(paths):
                    raise ValueError('转换引擎返回的源文件清单不一致。')
                if code != 0 or not rows or any(row.get('state') != 'done' for row in rows):
                    reason = next((r.get('error') for r in rows if r.get('error')), None)
                    raise ValueError(reason or '转换未完成，请检查源文件是否能正常打开。')
                files = list(dict.fromkeys(str(value) for row in rows for value in row.get('outputs', [])))
                checked = []
                allowed_extensions = {'.'+mode.target}
                if mode.target == 'jpg':
                    allowed_extensions.add('.jpeg')
                for value in files:
                    path = Path(value).resolve()
                    if (not path.is_relative_to(staging) or not path.is_file() or path.stat().st_size == 0
                            or path.suffix.lower() not in allowed_extensions):
                        raise ValueError('转换未生成完整结果，请重新另存源文件后再试。')
                    checked.append(path)
                if not checked:
                    raise ValueError('转换未生成结果文件。')
            except (json.JSONDecodeError, KeyError, TypeError):
                raise ValueError('转换引擎未返回有效结果，请确认源文件没有损坏。' + ('\n'+error.strip()[:240] if error.strip() else '')) from None
            published = []
            try:
                for path in checked:
                    if cancel.is_set():
                        raise ConversionCancelled()
                    published.append(publish_output(path, directory))
            except BaseException:
                # These names were exclusively reserved by this group.
                for name in published:
                    Path(name).unlink(missing_ok=True)
                raise
            fonts = sorted({font for row in rows for font in row.get('missingFonts', [])})
            notice = '原文件引用的字体未安装：' + '、'.join(fonts) if fonts else ''
            return [ConversionResult(str(path), 'done', published[:], notice=notice) for path in paths]

    def convert(self, paths, mode_key, output_directory, cancel, progress=lambda index, result: None):
        paths = validate_inputs(paths)
        mode = MODE_BY_ID.get(mode_key)
        if mode is None:
            raise ValueError('请选择有效的目标格式。')
        directory = Path(output_directory).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        capabilities = self.discover(paths, cancel)
        if mode not in available_modes(paths, capabilities):
            raise ValueError('当前文件组合暂不支持这个目标格式，请分别选择文件后再试。')
        groups = [paths] if mode.merge else [[path] for path in paths]
        results = []
        index = 0
        for group in groups:
            if cancel.is_set():
                converted = [ConversionResult(str(path), 'cancelled') for path in group]
            else:
                for offset, path in enumerate(group):
                    progress(index+offset, ConversionResult(str(path), 'running'))
                try:
                    converted = self._convert_group(group, mode, directory, cancel)
                except ConversionCancelled:
                    converted = [ConversionResult(str(path), 'cancelled') for path in group]
                except (OSError, ValueError) as exc:
                    converted = [ConversionResult(str(path), 'failed', error=str(exc)) for path in group]
            for result in converted:
                progress(index, result)
                results.append(result)
                index += 1
        return results
