"""Personal workflows remain local without network diagnosis."""
from pathlib import Path
from tempfile import TemporaryDirectory


def run_offline_smoke(pet_factory):
    from .controller import AgentController
    with TemporaryDirectory(prefix='bai-offline-') as folder:
        pet = pet_factory()
        controller = AgentController(pet, Path(folder))
        def forbidden(*args, **kwargs):
            raise AssertionError('Local actions must not use credentials, models or probes')
        controller.credentials.load = forbidden
        controller.agent.reply = forbidden
        controller.runtime.ensure_ready = forbidden
        controller.bridge.functions = forbidden
        try:
            controller.submit('网络检查')
            controller.submit('网络守护')
            controller.submit('添加待办：离线整理笔记')
            controller.submit('陪我记个词')
            assert controller.personal.store.tasks()[0]['title'] == '离线整理笔记'
            assert controller.personal.growth.dialog is not None
            assert not hasattr(controller.personal.growth.dialog, 'tabs')
            assert not hasattr(controller, 'guardian')
            return {'ok': True, 'api_calls': 0, 'key_reads': 0, 'local_model_starts': 0,
                    'real_network_modified': False, 'personal_features_offline': True,
                    'removed_network_features_not_started': True}
        finally:
            controller.close()
            pet.timer.stop()
            pet.chat.close()
            pet.close()
