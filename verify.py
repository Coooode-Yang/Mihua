"""Headless interaction checks; optionally render the actual widgets to a PNG.

    python verify.py
    python verify.py --preview preview.png

This checks Qt event handling, not real Windows compositor/multi-monitor input.
"""
import argparse
import json
import os
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import ROOT, PetWindow, SpriteBank


def mouse(widget, kind, local, global_point, button, buttons):
    event = QMouseEvent(kind, QPointF(local), QPointF(global_point), button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview")
    args = parser.parse_args()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    # The offscreen platform does not enumerate Windows system fonts itself.
    # Register existing local fonts for exported previews; no fonts are bundled.
    if os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisym.ttf"):
            if (fonts / name).exists():
                QFontDatabase.addApplicationFont(str(fonts / name))
    sprites = SpriteBank(ROOT / "assets" / "chick-sheet.png")
    pet = PetWindow(sprites, use_tray=False)
    pet.show()
    pet.timer.stop()
    app.processEvents()
    checks = []

    def check(name, condition):
        assert condition, name
        checks.append(name)

    check("six nonempty transparent poses", len(sprites.frames) == 6 and all(
        not f.mask().isNull() and f.toImage().pixelColor(0, 0).alpha() == 0 for f in sprites.frames))

    # A real click opens the chat; a drag must move the pet without opening it.
    point = QPoint(pet.width() // 2, 200)
    QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=point)
    check("single click opens chat", pet.chat.isVisible())
    pet.chat.hide()
    start = pet.pos()
    global_point = pet.mapToGlobal(point)
    delta = QPoint(-45, -25)
    mouse(pet, QEvent.Type.MouseButtonPress, point, global_point,
          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    mouse(pet, QEvent.Type.MouseMove, point + delta, global_point + delta,
          Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    mouse(pet, QEvent.Type.MouseButtonRelease, point, global_point + delta,
          Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    check("drag moves window without opening chat", pet.pos() != start and not pet.chat.isVisible())

    # Move across the head without pressing; this is distinct from dragging.
    pet._last_pet = 0
    head = pet.head_rect
    for p in [head.center().toPoint(), QPoint(int(head.right()-4),int(head.center().y())), QPoint(int(head.left()+4),int(head.center().y()))]:
        mouse(pet, QEvent.Type.MouseMove, p, pet.mapToGlobal(p),
              Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    check("head strokes trigger happy state", pet.state == "pet" and pet.pet_count > 0)

    # Exercise actual line-edit and Return handling rather than calling the parser.
    pet.show_chat()
    pet.chat.input.setText("睡觉")
    QTest.keyClick(pet.chat.input, Qt.Key.Key_Return)
    check("chat command enters sleep state", pet.state == "sleep")
    pet.chat.input.setText("醒醒")
    QTest.keyClick(pet.chat.input, Qt.Key.Key_Return)
    check("chat command wakes the pet", pet.state == "idle")
    pet.chat.input.setText("网络怎么恢复")
    QTest.keyClick(pet.chat.input, Qt.Key.Key_Return)
    check("network request truthfully reports unsupported capability",
          "还没接入网络诊断工具" in pet.chat.history.toPlainText())

    pet.chat.input.setText("<b>hello</b>")
    QTest.keyClick(pet.chat.input, Qt.Key.Key_Return)
    check("user markup is shown as plain text", "<b>hello</b>" in pet.chat.history.toPlainText())
    before = pet.chat.history.toPlainText()
    pet.chat.input.setText("   ")
    QTest.keyClick(pet.chat.input, Qt.Key.Key_Return)
    check("empty input is ignored", before == pet.chat.history.toPlainText())

    pet.set_state("jump", .06)
    QTest.qWait(100)
    pet.tick()
    check("temporary animation returns to idle", pet.state == "idle")
    pet.toggle_pause()
    check("pause stops animation timer", pet.paused and not pet.timer.isActive())
    pet.perform("pet")
    check("interaction resumes a paused pet", not pet.paused and pet.timer.isActive())
    pet.timer.stop()

    for size in (140, 190, 240):
        pet.set_pet_size(size)
        check(f"size {size} renders a frame", pet._frame.size() == pet.size())
    pet.set_pet_size(190)
    pet.set_agent_status("success")
    check("future Agent status hook updates the pet", pet.state == "pet")

    if args.preview:
        canvas = QPixmap(1120, 700)
        canvas.fill(QColor("#f4f0e9"))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#3f4b42"))
        painter.setFont(QFont("Microsoft YaHei", 28, QFont.Weight.Bold))
        painter.drawText(48, 66, "米花 · 桌面互动原型")
        painter.setFont(QFont("Microsoft YaHei", 11))
        painter.setPen(QColor("#7c837a"))
        painter.drawText(50, 99, "Kenney CC0 小鸡动作  /  本地运行  /  无需模型或 API Key")
        pet.chat.history.clear()
        pet.chat.add_message("米花", "叽！鼠标划过头顶，摸摸我吧。")
        pet.chat.add_message("你", "跳一下")
        pet.chat.add_message("米花", "看我跳一下！")
        pet.chat.resize(355, 450)
        pet.chat.show()
        app.processEvents()
        painter.drawPixmap(48, 142, pet.chat.grab())
        for x, state, bubble, label in [
            (430, "pet", "舒服～再摸摸！", "抚摸 · 开心振翅"),
            (735, "sleep", "呼……陪你安静待一会儿", "睡觉 · 呼吸起伏"),
        ]:
            pet.set_state(state)
            pet.say(bubble, 20)
            pet.render_frame(pet.state_started + .25)
            painter.drawPixmap(x, 205, pet._frame)
            painter.setPen(QColor("#5e6e60"))
            painter.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
            painter.drawText(QRectF(x, 547, 274, 35), Qt.AlignmentFlag.AlignCenter, label)
        painter.setFont(QFont("Microsoft YaHei", 10))
        painter.setPen(QColor("#7c837a"))
        painter.drawText(48, 650, "头顶划动：抚摸    左键按住：拖动    单击：聊天    右键：动作 / 大小 / 退出")
        painter.end()
        if not canvas.save(args.preview):
            raise RuntimeError("预览保存失败")
    print(json.dumps({"passed": len(checks), "checks": checks}, ensure_ascii=False, indent=2))
    pet.shutdown()


if __name__ == "__main__":
    main()
