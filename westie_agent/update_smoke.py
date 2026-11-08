"""Offline startup check for update binaries, menu wiring, and chick rendering."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from . import __version__
from .chick_smoke import run_chick_smoke
from .update_ui import UpdateController
from .updates import UpdateConfig


def run_update_smoke(factory, root, base):
    report = run_chick_smoke(factory, root)
    with TemporaryDirectory(prefix='mihua-update-smoke-') as folder:
        pet = factory()
        agent = SimpleNamespace(busy=False)
        updater = UpdateController(pet, Path(folder), agent, start_automatic=False)
        try:
            action = next(a for a in pet.make_menu().actions() if a.text() == '软件更新')
            action.trigger()
            assert updater.dialog.isVisible()
            assert updater.dialog.automatic.isChecked() and updater.dialog.auto_download.isChecked()
            assert not updater.timer.isActive() and not updater.startup_timer.isActive()
        finally:
            updater.close()
            pet.timer.stop()
            pet.chat.thinking_timer.stop()
            pet.chat.close()
            pet.close()
    config = UpdateConfig.load(Path(base))
    config.validate()
    if getattr(sys, 'frozen', False):
        assert config.enabled
        assert (Path(base) / 'MihuaUpdater.exe').is_file()
    report.update(version=__version__, update_entry_ok=True, updates_enabled=config.enabled,
                  startup_check=True, automatic_download=True, install_requires_confirmation=True,
                  external_api_called=False, github_requests=0)
    return report

