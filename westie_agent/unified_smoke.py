"""Real Qt navigation and fullscreen focus checks with isolated local data."""
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from .controller import AgentController
from .personal_store import FocusTimer
from .zotero import ZoteroLibrary


def run_unified_smoke(pet_factory):
    with TemporaryDirectory(prefix='bai-unified-smoke-') as folder:
        base=Path(folder);pet=pet_factory();agent=AgentController(pet,base);personal=agent.personal
        personal.timer.stop();personal.zotero_library=ZoteroLibrary(base,home=base/'home',roaming=base/'roaming')
        now=[1800000000.];personal.clock=lambda:now[0]
        personal.focus=FocusTimer(personal.store,personal.clock)
        main=personal.window;main.focus=personal.focus
        try:
            assert main.width() == main.minimumWidth() == 600
            from app import launch_desktop
            launch_desktop(pet);QTest.qWait(650)
            assert not main.isVisible() and not pet.chat.isWindow()
            assert not pet.windowIcon().isNull() and not main.windowIcon().isNull()
            assert main.windowIcon().cacheKey()==pet.windowIcon().cacheKey()
            QTest.mouseClick(pet,Qt.MouseButton.LeftButton,pos=QPoint(pet.width()//2,pet.height()-30))
            QTest.qWait(20)
            assert main.isVisible() and main.tabs.currentIndex()==0
            assert main.windowFlags() & Qt.WindowType.FramelessWindowHint
            bar = main.title_bar
            assert bar.isVisible() and bar.close_button.isVisible()
            assert bar.heading.text() == ''
            from .window_chrome import hit_test
            navigation = main.tabs.tabBar()
            tab_point = navigation.mapTo(main, navigation.tabRect(0).center())
            close_point = bar.close_button.mapTo(main, bar.close_button.rect().center())
            assert abs(tab_point.y() - close_point.y()) <= 10
            assert navigation.mapTo(main, QPoint(0, 0)).y() < 30
            assert hit_test(main, tab_point) == 1 and hit_test(main, close_point) == 1
            gap = QPoint((main.toolbox_button.mapTo(main, main.toolbox_button.rect().topRight()).x()
                          + bar.minimize.mapTo(main, QPoint(0, 0)).x()) // 2, close_point.y())
            assert hit_test(main, gap) == 2
            assert not hasattr(bar, 'maximize')
            assert not main.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
            QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(350, 24))
            assert not main.isMaximized()
            assert [main.tabs.tabText(i) for i in range(main.tabs.count())]==['学习','研究','日常','聊天']
            main.show_page('chat');assert pet.chat.isVisible() and pet.chat.window() is main
            pet.chat.input.setText('摸摸');pet.chat.send();assert pet.pet_count==1
            pet.chat.history.clear();pet.chat.add_message('你','模拟问题');pet.chat.add_message('米花','模拟回答',markdown=True)
            main.show_page('toolbox');QTest.qWait(20)
            assert main.toolbox_menu.isVisible() and main.tabs.currentIndex()==3
            assert [a.text() for a in main.toolbox_menu.actions()]==['网页快捷入口 · 带路小鸡','文件转换','模型设置']
            anchor=main.toolbox_button.mapToGlobal(QPoint(0,main.toolbox_button.height()))
            assert main.toolbox_menu.pos().y() >= anchor.y()
            QTest.keyClick(main.toolbox_menu,Qt.Key.Key_Escape)
            assert not main.toolbox_menu.isVisible() and not main.toolbox_button.isChecked()
            bar.close_button.click();pet.show_chat();assert personal.window is main and main.isVisible() and main.tabs.currentIndex()==3
            menu=pet.make_menu()
            interaction=next(a.menu() for a in menu.actions() if a.text()=='互动')
            assert [a.text() for a in interaction.actions()]==['摸摸','跳一下','睡觉 / 醒醒']
            assert not any(a.text() in {'摸摸','跳一下','睡觉 / 醒醒'} for a in menu.actions())
            next(a for a in menu.actions() if a.text()=='和米花说话').trigger();menu.deleteLater()
            assert main.tabs.currentIndex()==3 and '模拟回答' in pet.chat.history.toPlainText()
            main.show_page('study');main.focus_minutes.setValue(1);main.start_button.click();QTest.qWait(20)
            screen=personal.focus_screen
            assert screen.isFullScreen() and screen.isVisible() and not pet.isVisible(), (screen.windowState(),screen.isVisible(),pet.isVisible())
            assert screen.clock.text()=='01:00'
            now[0]+=12;screen.refresh();assert screen.clock.text()=='00:48'
            screen.pause_button.click();paused=personal.focus.snapshot()['remaining_s'];now[0]+=300;screen.refresh()
            assert screen.phase.text()=='已暂停' and personal.focus.snapshot()['remaining_s']==paused
            screen.pause_button.click();assert personal.focus.snapshot()['status']=='running'
            QTest.keyClick(screen,Qt.Key.Key_Escape);QTest.qWait(20)
            assert not screen.isVisible() and main.isVisible() and pet.isVisible()
            assert personal.focus.snapshot()['status']=='running' and len(personal.store.sessions())==1
            main.fullscreen_button.click();assert personal.focus_screen is screen and screen.isFullScreen()
            now[0]+=paused;personal.tick();screen.refresh()
            assert screen.clock.text()=='00:00' and screen.phase.text()=='专注已完成'
            assert personal.store.today_stats(now[0])['count']==1
            personal.tick();assert len(personal.store.sessions())==1
            screen.leave();main.show_page('research');QTest.qWait(40)
            forbidden=('平时只读标题和阅读记录','按服务商计费','整理结果已打开供你预览')
            assert not any(any(s in w.text() for s in forbidden) for w in main.research.findChildren(QLabel))
            agent.journal.show(all_dates=True);window=agent.journal.window
            assert window.browser.toPlainText()=='暂无记录' and window.status.isHidden()
            assert not any('按服务商计费' in w.text() for w in window.findChildren(QLabel))
            return {'ok':True,'external_api_called':False,'real_zotero_accessed':False,
                'integrated_title_bar_and_controls_ok':True,
                'default_width_matches_minimum_ok':True,'interaction_submenu_ok':True,
                'navigation_controls_same_row_ok':True,'header_gap_draggable_ok':True,
                'main_heading_removed_ok':True,
                'maximize_removed_ok':True,
                'quiet_start_no_main_window_ok':True,'shared_head_window_icon_ok':True,
                'pet_click_opens_single_main_window_ok':True,'chat_embedded_ok':True,
                'toolbox_popup_preserves_page_ok':True,'toolbox_three_actions_ok':True,
                'toolbox_below_button_and_escape_ok':True,
                'close_reopen_preserves_tab_and_chat_ok':True,'fullscreen_autostart_and_reentry_ok':True,
                'pause_resume_and_escape_preserve_timer_ok':True,'completion_counted_once_ok':True,
                'pet_hidden_during_fullscreen_ok':True,'marked_footer_text_removed_ok':True,
                'journal_empty_state_plain_text_ok':True}
        finally:
            agent.close();pet.timer.stop();pet.chat.close();pet.close();QApplication.processEvents()
