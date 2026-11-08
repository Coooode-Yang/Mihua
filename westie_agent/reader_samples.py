"""Small authored PDFs for developer checks; contains no user's paper data."""
from pathlib import Path
import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPageSize, QPainter, QPdfWriter, QPen


def create_sample_pdf(path,scanned=False):
    fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    for name in ('times.ttf','timesbd.ttf','arial.ttf'):
        if (fonts/name).is_file():QFontDatabase.addApplicationFont(str(fonts/name))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    writer=QPdfWriter(str(path));writer.setResolution(72);writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setTitle('Synthetic reader fixture - not a published paper')
    painter=QPainter(writer)
    def text(x,y,width,height,value,size=11,bold=False):
        font=QFont('Times New Roman',size);font.setBold(bold);painter.setFont(font)
        painter.setPen(QColor('#202820'))
        painter.drawText(QRectF(x,y,width,height),Qt.TextFlag.TextWordWrap,value)
    try:
        if scanned:
            image=QImage(900,1100,QImage.Format.Format_RGB32);image.fill(QColor('white'))
            brush=QPainter(image);brush.setFont(QFont('Arial',24));brush.drawText(QRectF(50,50,800,500),Qt.TextFlag.TextWordWrap,
                'An image-only test page. There is no selectable text layer.');brush.end()
            painter.drawImage(QRectF(0,0,530,650),image)
            return path
        text(30,25,490,45,'A Synthetic Study of Signal Gain',20,True)
        text(30,78,490,42,'This authored sample tests bilingual reading. It is not a real research publication. We preserve source locations and report uncertainty.')
        paragraph=('We measure signal gain in a controlled experiment. The input power is 2 watts. '
                   'The output power is 6 watts. The result supports a gain of 3 for this setup. '
                   'This observation does not establish performance under other conditions. ')
        text(30,143,225,150,paragraph)
        text(285,143,225,150,'The method compares the input and output under the same conditions. '
             'Figure 1 shows the measured signal. Table 1 records the two power measurements. '
             'The equation defines the ratio. No additional experiments are claimed.')
        painter.setPen(QPen(QColor('#567a62'),2))
        painter.drawLine(45,390,230,390);painter.drawLine(45,310,45,390)
        painter.drawLine(45,380,95,354);painter.drawLine(95,354,150,345);painter.drawLine(150,345,225,315)
        text(30,410,225,38,'Fig. 1. Signal measured in the authored example.',10)
        text(295,310,210,35,'G = P_out / P_in',14)
        text(285,365,225,28,'Table 1. Power measurements.',10)
        painter.setPen(QPen(QColor('#555555'),.8))
        for y in (400,429,458,487):painter.drawLine(285,y,510,y)
        for x in (285,400,510):painter.drawLine(x,400,x,487)
        for x,y,value in [(292,403,'Quantity'),(408,403,'Watts'),(292,432,'Input'),(408,432,'2'),(292,461,'Output'),(408,461,'6')]:
            text(x,y,100,22,value,10)
        text(30,535,480,80,'The sample is designed to exercise figure crops, table crops, equation images and stable page anchors. '
             'Readers should inspect the original evidence before drawing conclusions.')
        writer.newPage()
        text(30,30,490,35,'Methods and limitations',18,True)
        text(30,95,225,180,paragraph+paragraph)
        text(285,95,225,160,'The evidence is limited to an illustrative example. All text and graphics were authored for testing. '
             'This page checks that the reader does not stop after the first page. '
             'The original source file must remain unchanged after processing.')
        text(30,320,490,80,'Data availability: This synthetic sample contains no personal data. '
             'Code availability: The extraction and validation workflow is part of the desktop application. '
             'Competing interests: This is a software test fixture.')
    finally:
        painter.end()
    return path
