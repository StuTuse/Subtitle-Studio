"""简单文本预览框（ QDialog，带「复制」和「另存为」）。"""

from __future__ import annotations

import os

from qfluentwidgets import PushButton, PrimaryPushButton, SubtitleLabel, TextBrowser
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QVBoxLayout

from .theme import monospace


class TextPreviewDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None, filename: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 620)
        self._text = text
        self._filename = filename
        lay = QVBoxLayout(self)
        head = SubtitleLabel(title, self)
        lay.addWidget(head)
        self.view = TextBrowser(self)
        self.view.setPlainText(text or "（空）")
        self.view.setFont(monospace(9))
        lay.addWidget(self.view, 1)
        bar = QHBoxLayout()
        bar.addStretch(1)
        b_copy = PushButton("复制到剪贴板", self)
        b_copy.clicked.connect(self._copy)
        b_save = PrimaryPushButton("另存为…", self)
        b_save.clicked.connect(self._save)
        b_close = PushButton("关闭", self)
        b_close.clicked.connect(self.accept)
        bar.addWidget(b_copy)
        bar.addWidget(b_save)
        bar.addWidget(b_close)
        lay.addLayout(bar)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._text)

    def _save(self) -> None:
        fp, _ = QFileDialog.getSaveFileName(self, "保存预览内容", self._filename or "preview.txt",
                                            "文本文件 (*.txt);;所有文件 (*)")
        if fp:
            try:
                with open(fp, "w", encoding="utf-8", newline="") as f:
                    f.write(self._text)
            except OSError:
                pass
