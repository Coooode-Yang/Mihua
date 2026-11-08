"""Native vector accessories, rendered with the pet and the wardrobe preview."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QPolygonF

from .growth import ITEMS


def draw_outfits(painter, target, equipped, sleeping=False, reading=False):
    painter.save()
    painter.translate(target.x(), target.y())
    painter.scale(target.width()/384, target.height()/384)
    painter.setPen(QPen(QColor('#51483f'), 3))
    # Normalized anchors match the shared floor of every sprite pose.
    for slot, item in equipped.items():
        if item not in ITEMS:
            continue
        color = QColor(ITEMS[item][3])
        painter.setBrush(color)
        if slot == 'neck':
            y = 265 if sleeping else 247
            if item == 'bow':
                painter.drawPolygon(QPolygonF([QPointF(130,y-14), QPointF(172,y), QPointF(130,y+16)]))
                painter.drawPolygon(QPolygonF([QPointF(211,y-14), QPointF(172,y), QPointF(211,y+16)]))
                painter.drawEllipse(QPointF(172,y), 9, 9)
            else:
                painter.drawRoundedRect(QRectF(110,y-8,128,20), 9, 9)
                if item == 'bell':
                    painter.setBrush(QColor('#eed287'))
                    painter.drawEllipse(QPointF(174,y+16), 13, 13)
                    painter.drawLine(QPointF(166,y+19), QPointF(182,y+19))
                else:
                    painter.drawRoundedRect(QRectF(190,y,29,62), 6, 6)
                    painter.setPen(QPen(QColor('#f4dfcd'), 3))
                    for x in (135,162,190,216):
                        painter.drawLine(QPointF(x,y-6), QPointF(x,y+10))
                    for dy in (23,40,55):
                        painter.drawLine(QPointF(192,y+dy), QPointF(217,y+dy))
        elif slot == 'head':
            y = 85 if sleeping else 49
            if item == 'beret':
                painter.drawEllipse(QRectF(95,y-11,132,40))
                painter.drawRoundedRect(QRectF(118,y+17,93,12), 5, 5)
                painter.drawLine(QPointF(160,y-10), QPointF(166,y-23))
            else:
                painter.drawRoundedRect(QRectF(120,y+6,90,27), 5, 5)
                painter.drawPolygon(QPolygonF([QPointF(75,y+2),QPointF(166,y-26),QPointF(256,y+2),QPointF(166,y+31)]))
                painter.setPen(QPen(QColor('#e7c26f'), 5))
                painter.drawLine(QPointF(246,y+4), QPointF(246,y+51))
                painter.drawEllipse(QPointF(246,y+52), 5, 7)
        elif slot == 'badge':
            painter.drawEllipse(QPointF(137,292), 18, 18)
            painter.setPen(QColor('#fff9df'))
            painter.drawText(QRectF(120,275,34,34), Qt.AlignmentFlag.AlignCenter, '★')
        elif slot == 'ground':
            if item == 'star':
                painter.drawRoundedRect(QRectF(291,340,64,20), 6, 6)
                painter.drawLine(QPointF(323,340),QPointF(323,300))
                star = QPainterPath()
                import math
                for i in range(10):
                    a = -math.pi/2+i*math.pi/5
                    radius = 28 if i % 2 == 0 else 13
                    p = QPointF(323+math.cos(a)*radius, 285+math.sin(a)*radius)
                    star.moveTo(p) if i == 0 else star.lineTo(p)
                star.closeSubpath()
                painter.drawPath(star)
            else:
                draw_book(painter, 270, 325)
    if reading:
        draw_book(painter, 125, 330)
    painter.restore()


def draw_book(painter, x, y):
    painter.setPen(QPen(QColor('#536f67'), 3))
    painter.setBrush(QColor('#fcf4dd'))
    painter.drawRoundedRect(QRectF(x,y,80,40), 4, 4)
    painter.drawLine(QPointF(x+40,y+2), QPointF(x+40,y+38))
    painter.setPen(QPen(QColor('#c0bcaa'), 2))
    for dy in (12,21,30):
        painter.drawLine(QPointF(x+8,y+dy), QPointF(x+30,y+dy))
        painter.drawLine(QPointF(x+50,y+dy), QPointF(x+72,y+dy))
