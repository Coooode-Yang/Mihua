"""Confirm removal without opening sockets or altering existing imported tasks."""
from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
import json
import socketserver
import sqlite3
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QPushButton


def run_sync_removal_smoke(pet_factory, preview_dir=None):
    from .personal import PersonalController
    from .personal_store import PersonalStore
    with TemporaryDirectory(prefix='bai-sync-removed-') as folder:
        base = Path(folder)
        store = PersonalStore(base)
        identifier = store.save_task('保留之前导入的待办', '原有备注', '日常', 1)
        config = base / 'data/iphone-sync.json'
        config.write_text(json.dumps(dict(enabled=True, port=8765, token='legacy-test-token-' * 3,
                                         source='a' * 32, category='日常')), encoding='utf-8')
        original_config = config.read_bytes()
        with store.connect() as db:
            db.execute('CREATE TABLE iphone_task_links (source TEXT, remote_id TEXT, task_id TEXT, snapshot TEXT, PRIMARY KEY(source,remote_id))')
            db.execute('INSERT INTO iphone_task_links VALUES(?,?,?,?)', ('legacy', 'old-task', identifier, '{}'))
        def forbidden_bind(*args, **kwargs):
            raise AssertionError('Removed sync feature must not start a receiver')
        pet = pet_factory()
        controller = None
        try:
            with patch.object(socketserver.TCPServer, 'server_bind', forbidden_bind):
                controller = PersonalController(pet, base)
                controller.timer.stop()
                controller.show_page('daily')
                controller.tick()
                window = controller.window
                assert not hasattr(controller, 'iphone_sync') and not hasattr(controller, 'show_iphone_sync')
                assert not hasattr(window, 'iphone_sync_button')
                assert not any('iPhone' in b.text() for b in window.findChildren(QPushButton))
                assert controller.store.task(identifier)['title'] == '保留之前导入的待办'
                with controller.store.connect() as db:
                    assert db.execute('SELECT COUNT(*) FROM iphone_task_links').fetchone()[0] == 1
                assert config.read_bytes() == original_config
                for module in ('iphone_sync', 'iphone_sync_ui', 'iphone_sync_guide'):
                    assert importlib.util.find_spec('westie_agent.' + module) is None
                if preview_dir:
                    directory = Path(preview_dir)
                    directory.mkdir(parents=True, exist_ok=True)
                    QApplication.processEvents()
                    window.grab().save(str(directory / 'daily.png'))
                controller.close()
                controller = None
                assert config.read_bytes() == original_config
            return dict(ok=True, iphone_sync_removed=True, legacy_enabled_config_ignored=True,
                        no_receiver_started=True, imported_tasks_preserved=True,
                        removed_modules_absent=True, external_api_called=False,
                        real_user_data_accessed=False)
        finally:
            if controller:
                controller.close()
            pet.timer.stop()
            pet.chat.close()
            pet.close()
            QApplication.processEvents()
