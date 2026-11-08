"""Real native-engine checks using authored fixtures and temporary user data."""
import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import threading
import time

from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from .file_conversion import FILEE_VERSION, FileeEngine
from .file_conversion_ui import ConversionDialog


def run_conversion_smoke(base, assets):
    engine = FileeEngine(base)
    assert engine.ready
    report = {'engine_version': FILEE_VERSION, 'external_api_called': False}
    cancel = threading.Event()
    with TemporaryDirectory(prefix='bai-conversion-smoke-') as directory:
        work = Path(directory)
        paths = []
        for source, name in [('sample.docx', '中文 文稿.docx'), ('sample.pptx', '中文 汇报.pptx'),
                             ('image-1.png', '图片 一.png'), ('image-2.png', '图片 二.png')]:
            path = work/name
            shutil.copy2(Path(assets)/'conversion-samples'/source, path)
            paths.append(path)
        hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        output = work/'结果 文件夹'
        results = engine.convert(paths[:3], 'pdf', output, cancel)
        assert all(r.state == 'done' and Path(r.outputs[0]).read_bytes().startswith(b'%PDF-') for r in results)
        report['docx_pptx_image_to_pdf_ok'] = True
        word_pdf, ppt_pdf = Path(results[0].outputs[0]), Path(results[1].outputs[0])
        pages = engine.convert([ppt_pdf], 'png', work/'分页', cancel)
        assert pages[0].state == 'done' and len(pages[0].outputs) == 2
        assert all(not QImage(p).isNull() for p in pages[0].outputs)
        report['pdf_to_images_two_pages_ok'] = True
        editable = engine.convert([word_pdf], 'docx', work/'Word', cancel)
        assert editable[0].state == 'done' and Path(editable[0].outputs[0]).read_bytes().startswith(b'PK')
        report['pdf_to_docx_ok'] = True
        merged = engine.convert(paths[2:][::-1], 'merge_pdf', work/'合并', cancel)
        assert all(r.state == 'done' for r in merged) and merged[0].outputs == merged[1].outputs
        merged_pages = engine.convert([Path(merged[0].outputs[0])], 'png', work/'合并分页', cancel)
        assert merged_pages[0].state == 'done' and len(merged_pages[0].outputs) == 2
        # Original fixtures use different backgrounds. Verify the requested order.
        for name, expected in zip(merged_pages[0].outputs, paths[2:][::-1]):
            actual, original = QImage(name), QImage(str(expected))
            a = actual.pixelColor(actual.width()//2, 10)
            b = original.pixelColor(original.width()//2, 10)
            assert max(abs(a.red()-b.red()), abs(a.green()-b.green()), abs(a.blue()-b.blue())) < 12
        report['merge_order_two_pages_ok'] = True
        webp = engine.convert([paths[2]], 'webp', work/'图片', cancel)
        assert webp[0].state == 'done' and not QImage(webp[0].outputs[0]).isNull()
        report['image_to_webp_ok'] = True
        old_outputs = {Path(r.outputs[0]): hashlib.sha256(Path(r.outputs[0]).read_bytes()).hexdigest() for r in results}
        repeated = engine.convert(paths[:1], 'pdf', output, cancel)
        assert repeated[0].state == 'done' and repeated[0].outputs != results[0].outputs
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == value for p, value in old_outputs.items())
        bad = work/'损坏.png'; bad.write_bytes(b'invalid image fixture')
        mixed = engine.convert([bad, paths[2]], 'pdf', work/'批量', cancel)
        assert [r.state for r in mixed] == ['failed', 'done']
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == value for p, value in hashes.items())
        report['no_overwrite_originals_unchanged_partial_failure_ok'] = True
        stopped = threading.Event(); stopped.set()
        cancelled = engine.convert(paths[2:], 'pdf', work/'取消', stopped)
        assert all(r.state == 'cancelled' for r in cancelled)
        report['cancel_ok'] = True
        window = ConversionDialog(engine)
        try:
            window.add_files(paths[:2])
            deadline = time.monotonic()+60
            while (window.busy or window.worker is not None) and time.monotonic() < deadline:
                QTest.qWait(15)
            assert not window.busy and window.target.currentData() == 'pdf' and window.start_button.isEnabled()
            window.start_button.click()
            while (window.busy or window.worker is not None) and time.monotonic() < deadline:
                QTest.qWait(15)
            assert not window.busy and '已完成 2' in window.status.text() and window.open_button.isEnabled()
            report['background_ui_chinese_paths_ok'] = True
        finally:
            window.shutdown()
    report['ok'] = True
    return report
