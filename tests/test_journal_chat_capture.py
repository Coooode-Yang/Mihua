from datetime import date
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app import ROOT, PetWindow, SpriteBank
from westie_agent.controller import AgentController
from westie_agent.journal_chat_smoke import run_chat_capture_smoke, sample_image
from westie_agent.journal_store import JournalStore


@pytest.fixture
def agent(tmp_path):
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    pet = PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False)
    controller = AgentController(pet, tmp_path)
    controller.personal.timer.stop()
    pet.chat.history.clear_conversation()
    yield controller
    controller.close();pet.timer.stop();pet.chat.close();pet.close();application.processEvents()


def test_images_render_persist_and_export_with_complete_chat(agent):
    result = run_chat_capture_smoke(lambda: PetWindow(SpriteBank(ROOT/'assets/chick-sheet.png'), use_tray=False))
    assert all(result.values())


@pytest.mark.parametrize('include_question', [False, True])
def test_answer_capture_keeps_its_own_question_image_without_neighbor_images(agent, include_question):
    panel = agent.pet.chat
    first = sample_image('#aa1111', 'first.png')
    second = sample_image('#1111aa', 'second.png')
    panel.add_message('你', '第一轮', attachments=(first,))
    panel.add_message('米花', '第一轮回答')
    panel.add_message('你', '第二轮', attachments=(second,))
    panel.add_message('米花', '第二轮回答')
    panel.history.capture_message(1, include_question=include_question)
    editor = agent.journal.editors[-1]
    editor.save()
    row = editor.saved
    assert len(row['attachments']) == 1
    assert agent.journal.store.asset(row['attachments'][0]).read_bytes() == first.data
    assert '第二轮' not in row['body']
    assert ('问题：第一轮' in row['body']) == include_question


def test_saved_question_keeps_image_bytes_and_clear_releases_chat_attachments(agent):
    panel = agent.pet.chat
    picture = sample_image('#118833', 'question.png')
    panel.add_message('你', '只有图片的问题', attachments=(picture,))
    panel.history.capture_message(0)
    editor = agent.journal.editors[-1]
    panel.history.clear_conversation()
    assert not panel.history.message_images
    editor.save()
    restarted = JournalStore(agent.base).get(editor.saved['id'])
    assert agent.journal.store.asset(restarted['attachments'][0]).read_bytes() == picture.data


def test_full_conversation_exceeds_old_six_image_and_text_limits_without_truncation(agent):
    panel = agent.pet.chat
    for index in range(7):
        image = sample_image(f'#{index+1:02x}8833', f'image-{index}.png')
        panel.add_message('你', f'第 {index} 轮问题', attachments=(image,))
        panel.add_message('米花', f'第 {index} 轮回答\n'+str(index)*10000)
    panel.history.capture_conversation()
    editor = agent.journal.editors[-1]
    assert len(editor.pending) == 7 and len(editor.body.toPlainText()) > 60000
    editor.save()
    assert editor.saved and len(editor.saved['attachments']) == 7
    assert editor.saved['sources'][0]['message_count'] == 14
    for index in range(7):
        assert str(index)*10000 in editor.saved['body']


def test_duplicate_images_share_asset_but_remain_at_each_turn_and_recovered_draft_works(agent):
    panel = agent.pet.chat
    picture = sample_image('#338833', 'repeated.png')
    for index in range(2):
        panel.add_message('你', f'问题 {index}', attachments=(picture,))
        panel.add_message('米花', f'回答 {index}')
    panel.history.capture_conversation()
    editor = agent.journal.editors[-1]
    assert len(editor.pending) == 1
    editor.stash_draft()
    draft = agent.journal.store.drafts()[0]
    restored = agent.journal.edit(seed=draft)
    panel.history.clear_conversation()
    restored.save()
    row = restored.saved
    assert len(row['attachments']) == 1 and row['body'].count('![对话配图]') == 2
    output = agent.journal.store.export(date.today().isoformat(), agent.base/'重复图片.md')
    assert output.read_text(encoding='utf-8').count('![对话配图]') == 2
    assert len(list((agent.base/'重复图片-assets').glob('*.png'))) == 1
    editor.saved = row


def test_remove_captured_images_also_removes_inline_references_and_staged_files(agent):
    panel = agent.pet.chat
    picture = sample_image('#bb8833', 'remove.png')
    panel.add_message('你', '要保留文字', attachments=(picture,))
    panel.history.capture_conversation()
    editor = agent.journal.editors[-1]
    staged = Path(editor.pending[0])
    editor.remove_images()
    assert not staged.exists() and not editor.pending and not editor.attachments
    assert 'assets/' not in editor.body.toPlainText() and '要保留文字' in editor.body.toPlainText()
    editor.save()
    assert editor.saved['attachments'] == [] and '![对话配图]' not in editor.saved['body']


def test_menu_capture_uses_activation_time_and_empty_conversation_action_is_disabled(agent):
    history = agent.pet.chat.history
    empty = history.conversation_menu()
    assert not next(a for a in empty.actions() if a.text() == '收藏截至目前的全部对话到科研日志').isEnabled()
    empty.deleteLater()
    agent.pet.chat.add_message('你', '打开菜单时的问题')
    menu = history.conversation_menu()
    agent.pet.chat.add_message('米花', '菜单打开后到达的回答')
    next(a for a in menu.actions() if a.text() == '收藏截至目前的全部对话到科研日志').trigger()
    editor = agent.journal.editors[-1]
    assert '菜单打开后到达的回答' in editor.body.toPlainText()
    menu.deleteLater()
