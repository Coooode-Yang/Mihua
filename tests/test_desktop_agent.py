import threading

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app import ROOT, PetWindow, SpriteBank
from westie_agent.controller import AgentController
from westie_agent.engine import AgentReply


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_model_work_does_not_block_pet_interaction(qt_app, tmp_path):
    pet = PetWindow(SpriteBank(ROOT / "assets/chick-sheet.png"), use_tray=False)
    assert pet.pet_size == 140
    controller = AgentController(pet, tmp_path)
    release = threading.Event()
    entered = threading.Event()
    class Runtime:
        url = "http://127.0.0.1:1"
        api_key = "local-test"
        def ensure_ready(self, *a):
            pass
        def close(self):
            pass
    class Engine:
        history = []
        def reply(self, text, *a, **k):
            entered.set()
            assert release.wait(3)
            return AgentReply("本地测试回答", [], [], "real", False)
    controller.runtime = Runtime()
    controller.settings.model_backend='local'
    controller.settings.model='qwen3:4b'
    controller.agent = Engine()
    try:
        pet.handle_text("我不想睡觉，请解释校园网认证流程")
        assert entered.wait(1) and controller.busy
        assert pet.state == "search" and pet.agent_busy
        assert "正在思考" in pet.chat.status.text()
        controller.progress("检查网络连接状态…")
        assert "正在思考" in pet.chat.status.text() and "检查网络" in pet.chat.status.text()
        pet.perform("pet")
        qt_app.processEvents()
        assert pet.pet_count == 1 and not pet.chat.input.isEnabled()
        release.set()
        for _ in range(100):
            QTest.qWait(10)
            if not controller.busy:
                break
        assert not controller.busy and pet.chat.input.isEnabled()
        assert not pet.agent_busy and not pet.chat.thinking_timer.isActive()
        assert pet.state == "idle" and pet.chat.status.isHidden()
        assert "本地测试回答" in pet.chat.history.toPlainText()
    finally:
        release.set()
        controller.close()
        pet.timer.stop()
        pet.chat.close()
        pet.close()


def test_busy_thinking_animates_and_stops_on_failure(qt_app, tmp_path):
    pet = PetWindow(SpriteBank(ROOT / "assets/chick-sheet.png"), use_tray=False)
    controller = AgentController(pet, tmp_path)
    try:
        pet.set_agent_status("checking")
        pet.chat.set_busy(True)
        before = pet.chat.status.text()
        QTest.qWait(520)
        assert "正在思考" in pet.chat.status.text() and pet.chat.status.text() != before
        controller.failed("本次检查未完成")
        assert pet.state == "idle" and not pet.agent_busy
        assert pet.chat.status.isHidden() and not pet.chat.thinking_timer.isActive()
    finally:
        controller.close()
        pet.timer.stop()
        pet.chat.close()
        pet.close()


def test_removed_network_commands_do_not_call_tools_or_keys(qt_app,tmp_path,monkeypatch):
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False)
    controller=AgentController(pet,tmp_path)
    def forbidden(*a,**k): pytest.fail('removed features must not do work')
    monkeypatch.setattr(controller.credentials,'load',forbidden)
    monkeypatch.setattr(controller.bridge,'functions',forbidden)
    monkeypatch.setattr(controller,'work',forbidden)
    try:
        for text in ('网络检查','网络守护','开启网络守护','导出排障报告'):
            controller.submit(text)
        assert not controller.busy and not controller.agent.allow_network
        assert '已移除' in pet.chat.history.toPlainText()
    finally:
        controller.close();pet.timer.stop();pet.chat.close();pet.close()
