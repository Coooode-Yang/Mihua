from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app import ROOT,PetWindow,SpriteBank
from westie_agent.controller import AgentController
from westie_agent.personal_store import FocusTimer,PersonalStore
from westie_agent.unified_smoke import run_unified_smoke


@pytest.fixture(scope='module')
def qt_app():
    instance=QApplication.instance() or QApplication([]);instance.setQuitOnLastWindowClosed(False);return instance


def test_actual_pet_click_main_navigation_and_fullscreen(qt_app):
    report=run_unified_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
    assert report['ok'] and not report['external_api_called']
    assert report['quiet_start_no_main_window_ok'] and report['shared_head_window_icon_ok']


def test_frontal_head_icon_supports_windows_small_and_large_sizes(qt_app):
    from PIL import Image
    from app import application_icon
    with Image.open(ROOT/'assets/ui/chick-head.png') as picture:
        assert picture.mode=='RGBA' and picture.width==picture.height
        assert picture.getpixel((0,0))[3]==0 and picture.getextrema()[3][1]==255
    icon=application_icon()
    assert not icon.isNull()
    assert {(16,16),(32,32),(256,256)}.issubset({(s.width(),s.height()) for s in icon.availableSizes()})


@pytest.fixture
def personal(qt_app,tmp_path):
    pet=PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False);agent=AgentController(pet,tmp_path)
    controller=agent.personal;controller.timer.stop();now=[1800000000.]
    controller.clock=lambda:now[0];controller.focus=FocusTimer(controller.store,controller.clock);controller.window.focus=controller.focus
    yield agent,controller,now,tmp_path
    agent.close();pet.timer.stop();pet.chat.close();pet.close();qt_app.processEvents()


def test_chat_command_starts_same_fullscreen_and_escape_does_not_pause(personal):
    agent,controller,now,base=personal
    agent.submit('开始1分钟专注');screen=controller.focus_screen
    assert screen.isFullScreen() and controller.focus.snapshot()['status']=='running'
    now[0]+=15;QTest.keyClick(screen,Qt.Key.Key_Escape)
    state=controller.focus.snapshot();assert state['remaining_s']==45 and state['status']=='running'
    assert FocusTimer(PersonalStore(base),lambda:now[0]).snapshot()['remaining_s']==45
    controller.window.fullscreen_button.click();assert len(controller.store.sessions())==1


def test_close_fullscreen_and_confirm_early_stop(personal,monkeypatch):
    agent,controller,now,_=personal
    controller.window.start_button.click();screen=controller.focus_screen
    now[0]+=10;screen.close();assert controller.focus.snapshot()['status']=='running'
    controller.show_focus_screen()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.No)
    screen.confirm_stop();assert controller.focus.snapshot()['status']=='running' and screen.isVisible()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    screen.confirm_stop();assert controller.focus.snapshot()['status']=='idle' and not screen.isVisible()
    assert controller.store.sessions()[0]['status']=='cancelled' and controller.store.today_stats(now[0])['count']==0


def test_timer_continues_with_main_window_closed_and_is_counted_once(personal):
    agent,controller,now,_=personal
    controller.window.start_button.click();controller.focus_screen.leave();controller.window.close()
    now[0]+=controller.focus.snapshot()['remaining_s'];controller.tick();controller.tick()
    assert controller.focus.snapshot()['status']=='finished'
    assert len(controller.store.sessions())==1 and controller.store.today_stats(now[0])['count']==1
    agent.pet.show_chat();assert controller.window.isVisible()


def test_chat_cannot_create_a_separate_window_and_tools_work_while_busy(personal):
    agent,controller,_,_=personal
    main=controller.window;main.show_page('chat');agent.pet.chat.set_busy(True)
    main.show_page('toolbox');QTest.qWait(20)
    assert agent.pet.chat.conversion_button.isEnabled() and main.toolbox_menu.isVisible()
    assert main.tabs.currentIndex()==3 and not agent.pet.chat.isWindow()
    main.toolbox_menu.hide()
    main.show_page('chat');assert not agent.pet.chat.input.isEnabled() and main.tabs.isEnabled()
    agent.pet.chat.set_busy(False);assert agent.pet.chat.input.isEnabled()


def test_toolbox_click_escape_and_keyboard_selection_preserve_current_page(personal):
    agent,controller,_,_=personal
    main=controller.window;main.show_page('daily');QTest.qWait(20)
    assert main.tabs.count()==4
    QTest.mouseClick(main.toolbox_button,Qt.MouseButton.LeftButton)
    assert main.toolbox_menu.isVisible() and main.toolbox_button.isChecked()
    assert main.tabs.currentIndex()==2
    anchor=main.toolbox_button.mapToGlobal(QPoint(0,main.toolbox_button.height()))
    assert main.toolbox_menu.pos()==anchor+QPoint(0,4)
    QTest.keyClick(main.toolbox_menu,Qt.Key.Key_Escape)
    assert not main.toolbox_menu.isVisible() and not main.toolbox_button.isChecked()
    QTest.mouseClick(main.toolbox_button,Qt.MouseButton.LeftButton)
    QTest.keyClick(main.toolbox_menu,Qt.Key.Key_Down)
    QTest.keyClick(main.toolbox_menu,Qt.Key.Key_Down)
    QTest.keyClick(main.toolbox_menu,Qt.Key.Key_Return)
    assert agent.conversion.window.isVisible()
    assert not main.toolbox_menu.isVisible() and main.tabs.currentIndex()==2


def test_toolbox_actions_use_existing_buttons(personal):
    agent,controller,_,_=personal
    main=controller.window;main.show_page('study')
    actions=main.toolbox_menu.actions()
    assert [a.text() for a in actions]==['网页快捷入口 · 带路小鸡','文件转换','模型设置']
    actions[0].trigger();assert agent.campus_dialog.isVisible()
    agent.campus_dialog.close()
    actions[1].trigger();assert agent.conversion.window.isVisible()
    agent.conversion.window.close()
    actions[2].trigger();assert agent.dialog.isVisible()
    assert main.tabs.currentIndex()==0
