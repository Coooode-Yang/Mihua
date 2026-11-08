"""Compact Markdown for model replies, with literal user text kept separate."""
from __future__ import annotations

import re

from PySide6.QtGui import QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat


class ReplyDocument(QTextDocument):
    def loadResource(self, resource_type, url):
        # Formatting a reply must not read local files or fetch remote images.
        return None


def normalize_emphasis(text):
    """Tolerate model output like '**一句话： **' without changing code."""
    lines = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not marker[2].strip():
                fence = None
            lines.append(line)
            continue
        if fence:
            lines.append(line)
            continue
        def replace(match):
            if match['code'] is not None:
                return match[0]
            value = match['strong'].strip()
            if not value:
                return ''
            # CommonMark needs separation when punctuation such as a Chinese
            # colon/parenthesis touches the next word outside an emphasis span.
            before = ' ' if match.start() and not line[match.start()-1].isspace() else ''
            after = ' ' if match.end() < len(line) and not line[match.end()].isspace() else ''
            return before + '**' + value + '**' + after
        lines.append(re.sub(r'(?P<code>`+[^\n]*?`+)|\*\*(?P<strong>[^\n]+?)\*\*', replace, line))
    return ''.join(lines)


def reply_document(text, font):
    document = ReplyDocument()
    document.setDefaultFont(font)
    document.setIndentWidth(18)
    document.setMarkdown(normalize_emphasis(text), QTextDocument.MarkdownFeature.MarkdownNoHTML)
    images = []
    block = document.begin()
    while block.isValid():
        cursor = QTextCursor(block)
        block_format = block.blockFormat()
        heading = bool(block_format.headingLevel())
        block_format.setHeadingLevel(0)
        block_format.setTopMargin(5)
        block_format.setBottomMargin(5)
        block_format.setLineHeight(140, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.setBlockFormat(block_format)
        if heading:
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            char_format = QTextCharFormat()
            char_format.setFont(font)
            char_format.setProperty(QTextFormat.Property.FontSizeAdjustment, 0)
            char_format.setFontWeight(QFont.Weight.DemiBold)
            cursor.mergeCharFormat(char_format)
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                alt = fragment.charFormat().property(QTextFormat.Property.ImageAltText) or ''
                images.append((fragment.position(), fragment.length(), str(alt)))
            iterator += 1
        block = block.next()
    for position, length, alt in reversed(images):
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(alt, QTextCharFormat())
    return document
