"""简单文本预览框（ QDialog，带「复制」和「另存为」）。"""

from __future__ import annotations

from qfluentwidgets import PushButton, PrimaryPushButton, SubtitleLabel, TextBrowser
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QMessageBox, QVBoxLayout

from ..core.i18n import S
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
        self.view.setPlainText(text or S("（空）", "(empty)"))
        self.view.setFont(monospace(9))
        lay.addWidget(self.view, 1)
        bar = QHBoxLayout()
        bar.addStretch(1)
        b_copy = PushButton(S("复制到剪贴板", "Copy to clipboard"), self)
        b_copy.clicked.connect(self._copy)
        b_save = PrimaryPushButton(S("另存为…", "Save as…"), self)
        b_save.clicked.connect(self._save)
        b_close = PushButton(S("关闭", "Close"), self)
        b_close.clicked.connect(self.accept)
        bar.addWidget(b_copy)
        bar.addWidget(b_save)
        bar.addWidget(b_close)
        lay.addLayout(bar)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._text)

    def _save(self) -> None:
        fp, _ = QFileDialog.getSaveFileName(
            self, S("保存预览内容", "Save preview content"),
            self._filename or "preview.txt",
            S("文本文件", "Text files") + " (*.txt);;" + S("所有文件", "All files") + " (*)")
        if fp:
            try:
                with open(fp, "w", encoding="utf-8", newline="") as f:
                    f.write(self._text)
            except OSError as e:
                # 静默失败比报错更糟：用户以为存好了。给一次明确提示。
                QMessageBox.warning(self, S("保存失败", "Save failed"),
                                    f"{type(e).__name__}: {e}")
