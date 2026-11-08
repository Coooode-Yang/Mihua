from datetime import datetime,timedelta

import httpx2
import pytest
from PySide6.QtWidgets import QApplication,QPushButton
from PySide6.QtTest import QTest

from app import ROOT,PetWindow,SpriteBank
from westie_agent.bridge import ToolBridge
from westie_agent.controller import AgentController
from westie_agent.engine import LocalAgent
from westie_agent.offline import disconnected_snapshot,recent_offline_observation
from westie_agent.offline_smoke import run_offline_smoke
from westie_agent.settings import Settings


@pytest.fixture(scope='module')
def qt_app():return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('data,expected',[
    ({'selected_interface':{'status':'disconnected'}},True),
    ({'selected_interface':{'status':'down'}},True),
    ({'selected_interface':{'status':'unknown'}},False),
    ({'selected_interface':{'status':'connected'}},False),
    ({'selected_interface':{'status':'disconnected'},'candidate_interfaces':[{'status':'connected'}]},False),
    ({'candidate_interfaces':[]},True),({},False),
])
def test_local_gate_only_skips_api_on_positive_offline_evidence(data,expected):
    assert disconnected_snapshot({'success':True,'data':data}) is expected
    assert not disconnected_snapshot({'success':False,'data':data})
    assert not disconnected_snapshot({'success':True,'error':{'code':'PERMISSION_DENIED'},'data':data})


def test_cached_offline_state_must_be_recent():
    def ob(age):return {'state':'disconnected','checked_at':(datetime.now().astimezone()-timedelta(seconds=age)).isoformat()}
    assert recent_offline_observation(ob(5))
    assert not recent_offline_observation(ob(90))
    assert not recent_offline_observation(ob(-60))
    assert not recent_offline_observation({'state':'unknown'})


def test_actual_controller_offline_and_basic_paths_do_not_read_key_or_call_model(qt_app):
    result=run_offline_smoke(lambda:PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'),use_tray=False))
    assert result['ok'] and result['api_calls']==0 and result['key_reads']==0
