"""Offline checks for the Kenney chick assets, interactions, and character text."""
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image
from PySide6.QtCore import Qt


def run_chick_smoke(factory, root):
    root = Path(root)
    metadata = json.loads((root/'assets/chick-assets.json').read_text(encoding='utf-8'))
    assert metadata['license']=='CC0-1.0'
    assert metadata['facing']=='left' and metadata['display_name']=='米花'
    for name, size in [('chick-sheet.png',(1152,768)), ('chick-search-sheet.png',(1536,1152)), ('chick-think-sheet.png',(1536,1152)), ('chick-sleep-sheet.png',(1536,1152))]:
        with Image.open(root/'assets'/name) as sheet:
            assert sheet.mode=='RGBA' and sheet.size==size
            for y in range(0,size[1],384):
                for x in range(0,size[0],384):
                    box = sheet.crop((x,y,x+384,y+384)).getbbox()
                    assert box and 0<box[0]<box[2]<384 and 0<box[1]<box[3]<384
    with Image.open(root/'assets/ui/chick-head.ico') as icon:
        assert {(16,16),(32,32),(48,48),(256,256)} <= icon.ico.sizes()
    from westie_agent.engine import PERSONAL_INSTRUCTIONS, INSTRUCTIONS
    assert all('小鸡' in text and '西高地' not in text for text in (PERSONAL_INSTRUCTIONS,INSTRUCTIONS))
    assert not list((root/'assets').glob('westie-*.png'))
    pet = factory()
    pet.timer.stop()
    try:
        assert len(pet.sprites.frames)==6 and len(pet.sprites.search_frames)==12
        assert len(pet.sprites.think_frames)==len(pet.sprites.sleep_frames)==12
        assert pet.pet_size==140
        assert pet.windowTitle()=='米花 · 桌面小鸡' and not pet.windowIcon().isNull()
        assert '叽' in pet.bubble and '汪' not in pet.bubble
        menu = pet.make_menu()
        assert any(a.text()=='小鸡大小' for a in menu.actions())
        assert not any('狗' in a.text() for a in menu.actions())
        images = {}
        for state in ('idle','pet','jump','curious','sleep','search'):
            pet.set_state(state)
            pet.render_frame(pet.state_started+.8)
            picture = pet._frame.toImage()
            assert picture.hasAlphaChannel() and picture.pixelColor(0,0).alpha()==0
            images[state]=hashlib.sha256(picture.constBits()).hexdigest()
        assert len(set(images.values()))==6
        for size in (140,190,240):
            pet.set_pet_size(size)
            pet.render_frame()
            assert pet.width()==size+24 and pet.height()==size+76
        pet.perform('pet')
        assert pet.state=='pet' and '尾巴' not in pet.bubble and '汪' not in pet.bubble
        pet.perform('sleep')
        assert pet.state=='sleep'
        pet.perform('wake')
        assert pet.state=='idle'
        return {'ok':True,'frozen':bool(getattr(sys,'frozen',False)),
                'character':'Kenney Cube Pets / chick','license':metadata['license'],
                'sprite_frames':6,'search_frames':12,'think_frames':12,'sleep_frames':12,'facing':'left','display_name':'米花','states_rendered':list(images),
                'pet_sizes':[140,190,240],'icon_sizes_ok':True,'character_prompt_ok':True,
                'old_dog_sheets_absent':True,'external_api_called':False}
    finally:
        pet.timer.stop()
        pet.chat.thinking_timer.stop()
        pet.chat.close()
        pet.close()
