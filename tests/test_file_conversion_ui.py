from pathlib import Path
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from westie_agent.file_conversion import Capability, ConversionCancelled, ConversionResult
from westie_agent.file_conversion_ui import ConversionDialog


class Engine:
    ready = True
    cancel_discovery = False
    def discover(self, paths, cancel):
        if self.cancel_discovery: raise ConversionCancelled()
        return {p.suffix: Capability(frozenset({'pdf', 'png', 'docx'})) for p in paths}
    def convert(self, paths, mode, output, cancel, progress):
        out = Path(output); out.mkdir(parents=True, exist_ok=True)
        result = []
        for i, path in enumerate(paths):
            progress(i, ConversionResult(str(path), 'running'))
            target = out/(path.stem+'.pdf'); target.write_bytes(b'%PDF-fixture')
            row = ConversionResult(str(path), 'done', [str(target)])
            progress(i, row); result.append(row)
        return result


def wait(dialog):
    deadline = time.monotonic()+5
    while (dialog.busy or dialog.worker is not None) and time.monotonic() < deadline:
        QTest.qWait(10)
    assert not dialog.busy and dialog.worker is None


@pytest.fixture
def dialog():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = ConversionDialog(Engine()); window.show()
    yield window
    window.shutdown(); app.processEvents()


def test_batch_controls_order_and_open_result(dialog, tmp_path, monkeypatch):
    paths = [tmp_path/'一.png', tmp_path/'二.png']
    for p in paths: p.write_bytes(b'fixture')
    dialog.add_files(paths); wait(dialog)
    assert dialog.start_button.isEnabled() and dialog.target.findData('docx') == -1
    dialog.files.setCurrentItem(dialog.files.topLevelItem(1))
    dialog.move_selected(-1)
    assert dialog.paths() == paths[::-1]
    dialog.start_button.click(); wait(dialog)
    assert '已完成 2' in dialog.status.text() and dialog.open_button.isEnabled()
    opened = []
    monkeypatch.setattr('westie_agent.file_conversion_ui.QDesktopServices.openUrl', lambda url: opened.append(url.toLocalFile()))
    dialog.open_button.click()
    assert Path(opened[0]).is_file()
    dialog.clear_button.click()
    assert not dialog.paths() and not dialog.start_button.isEnabled()


def test_cancelled_discovery_can_be_retried(dialog, tmp_path):
    path = tmp_path/'doc.pdf'; path.write_bytes(b'fixture')
    dialog.engine.cancel_discovery = True
    dialog.add_files([path]); wait(dialog)
    assert dialog.start_button.text() == '重新识别' and dialog.start_button.isEnabled()
    dialog.engine.cancel_discovery = False
    dialog.start_button.click(); wait(dialog)
    dialog.target.setCurrentIndex(dialog.target.findData('docx'))
    assert '扫描件' in dialog.mode_hint.text() and not dialog.mode_hint.isHidden()


def test_missing_engine_does_not_enable_conversion():
    app = QApplication.instance() or QApplication([])
    engine = Engine(); engine.ready = False
    window = ConversionDialog(engine)
    assert '未就绪' in window.status.text() and not window.start_button.isEnabled()
    window.shutdown()


def test_toolbox_and_local_command_work_while_ai_busy(tmp_path):
    from app import PetWindow, SpriteBank, ROOT
    from westie_agent.controller import AgentController
    app = QApplication.instance() or QApplication([])
    pet = PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False)
    controller = AgentController(pet, tmp_path)
    controller.personal.timer.stop()
    try:
        controller.busy = True
        pet.chat.conversion_button.click()
        assert controller.conversion.window.isVisible()
        controller.conversion.window.close()
        controller.submit('文件转换')
        assert controller.conversion.window.isVisible()
        assert not list(tmp_path.glob('*key*'))
    finally:
        controller.close(); pet.timer.stop(); pet.chat.close(); pet.close(); app.processEvents()
