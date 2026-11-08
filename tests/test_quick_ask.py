import json

import httpx2
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from app import PetWindow, ROOT, SpriteBank
from westie_agent.bridge import ToolBridge
from westie_agent.engine import LocalAgent
from westie_agent.quick_ask import GlobalShortcut
from westie_agent.quick_ask_smoke import run_quick_ask_smoke
from westie_agent.settings import Settings
from westie_agent.web_lookup import LookupUnavailable, SearchEvidence, SearchSource, lookup


def test_shared_chat_clipboard_context_and_minimum_window():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    assert run_quick_ask_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))['ok']


@pytest.mark.parametrize('sequence', ['Q', 'Ctrl+F1', 'Meta+Q', 'Ctrl+K, Ctrl+Q'])
def test_shortcut_rejects_unsupported_or_unmodified_keys(sequence):
    with pytest.raises(ValueError):
        GlobalShortcut.chord(QKeySequence(sequence))


def test_search_sources_bounded_deduplicated_and_only_fixed_endpoint():
    requests = []
    feed = '''<rss><channel>
        <item><title>MEO &amp; satellites</title><link>https://example.org/orbit</link><description>&lt;b&gt;Middle orbit&lt;/b&gt;</description></item>
        <item><title>Duplicate</title><link>https://example.org/orbit</link><description>duplicate</description></item>
        <item><title>Unsafe</title><link>file:///C:/secret</link><description>do not open</description></item>
    </channel></rss>'''
    def response(request):
        requests.append(request)
        return httpx2.Response(200, text=feed)
    evidence = lookup('MEO satellite', transport=httpx2.MockTransport(response))
    assert len(requests) == 1 and requests[0].url.host == 'www.bing.com'
    assert requests[0].url.params['q'] == 'MEO satellite' and len(evidence.sources) == 1
    assert evidence.sources[0].snippet == 'Middle orbit'
    assert 'https://example.org/orbit' in evidence.footer() and 'secret' not in evidence.footer()


@pytest.mark.parametrize('status,body', [(200, '<html>captcha</html>'), (403, 'blocked'), (200, '<rss><channel/></rss>'), (302, '')])
def test_failed_search_never_claims_verification(status, body):
    with pytest.raises(LookupUnavailable):
        lookup('test', transport=httpx2.MockTransport(lambda r: httpx2.Response(status, text=body)))


def test_model_receives_search_evidence_and_keeps_it_for_followup():
    requests = []
    def response(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json={'id': 'quick-test', 'object': 'chat.completion', 'created': 1,
            'model': 'deepseek-flash', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': '中地球轨道。'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
    settings = Settings()
    agent = LocalAgent(settings, ToolBridge(settings), allow_network=False)
    evidence = SearchEvidence('MEO', (SearchSource('Actual result', 'https://example.org/orbit', 'retrieved orbital evidence'),), '2026-11-02 UTC')
    first = agent.reply('MEO', settings.api_base_url, lambda _: None, api_key='fake-test-key',
                        api_transport=httpx2.MockTransport(response), quick=True, web_evidence=evidence)
    agent.reply('和 LEO 的区别？', settings.api_base_url, lambda _: None, api_key='fake-test-key',
                api_transport=httpx2.MockTransport(response), quick=True)
    assert 'https://example.org/orbit' in first.text and '检索来源' in first.text
    assert 'retrieved orbital evidence' in json.dumps(requests[1], ensure_ascii=False)
    assert '约150字' in json.dumps(requests[0], ensure_ascii=False)


def test_only_web_links_can_be_opened(monkeypatch):
    from app import ChatPanel
    application = QApplication.instance() or QApplication([])
    panel = ChatPanel()
    opened = []
    monkeypatch.setattr('app.QDesktopServices.openUrl', lambda url: opened.append(url.toString()))
    try:
        for value in ('file:///C:/secret', 'javascript:alert(1)', 'https://user:password@example.org/', 'https://example.org/valid'):
            panel.open_source(QUrl(value))
        assert opened == ['https://example.org/valid']
    finally:
        panel.close();panel.deleteLater();application.processEvents()
