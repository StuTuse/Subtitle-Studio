# -*- coding: utf-8 -*-
"""LLM 纠错结果逐条复查：原文 → 修正 逐条对比，按条采纳/拒绝。

与「查看修改对比」纯文本只读预览互补：这里给出的是**可操作**的
复查视图——每条改动一行，可单独采纳（保留修正）、拒绝（回滚原文），
也支持一键全收/全拒。按 cue 稳定 id 定位，纠错后用户增删/排序导致
的行号漂移不会错位。

关闭时统一走「未处理的改动保持现状」：拒绝=回滚原文，跳过=保留修正。
"""

from __future__ import annotations

from typing import Dict, List

from qfluentwidgets import (BodyLabel, CaptionLabel, FluentIcon as FIF, PrimaryPushButton,
                            PushButton, StrongBodyLabel)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QSizePolicy, QVBoxLayout, QWidget)

from .theme import CARD_MARGINS


class DiffReviewDialog(QDialog):
    """逐条 diff 复查。entries: [{id, row, original, fixed}]。

    exec_() 返回后用 result_actions() 取每条的决定：
      "accept" | "reject" | "skip"
    """

    def __init__(self, entries: List[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("逐条复查纠错结果")
        self.resize(860, 640)
        self.setModal(True)
        self._entries = entries
        self._actions: Dict[str, str] = {}
        self._cur = -1

        lay = QVBoxLayout(self)
        lay.setContentsMargins(*CARD_MARGINS)
        n = len(entries)
        self._head = StrongBodyLabel(f"共 {n} 条改动：逐条核对后采纳或拒绝", self)
        lay.addWidget(self._head)
        self._hint = CaptionLabel(
            "采纳 = 保留修正文本；拒绝 = 恢复原始识别文本。"
            "不确定的可以先跳过（保持修正），稍后在编辑页用状态色核对。", self)
        self._hint.setWordWrap(True)
        self._hint.setMinimumWidth(0)
        self._hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self._hint)

        self.list = QListWidget(self)
        self.list.setUniformItemSizes(False)
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(self._on_row)
        lay.addWidget(self.list, 1)

        # 当前条的原文/修正双行预览（列表里放不下全文）
        self._orig_view = BodyLabel("", self)
        self._orig_view.setWordWrap(True)
        self._new_view = BodyLabel("", self)
        self._new_view.setWordWrap(True)
        detail = QWidget(self)
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(8, 8, 8, 8)
        dv.setSpacing(4)
        o_lab = QLabel("原文：", detail)
        o_lab.setStyleSheet("color:#c75050;font-weight:600;")
        n_lab = QLabel("修正：", detail)
        n_lab.setStyleSheet("color:#2f9e5f;font-weight:600;")
        dv.addWidget(o_lab)
        dv.addWidget(self._orig_view)
        dv.addWidget(n_lab)
        dv.addWidget(self._new_view)
        lay.addWidget(detail)

        bar = QHBoxLayout()
        self.btn_prev = PushButton(FIF.LEFT_ARROW, "上一条", self)
        self.btn_accept = PrimaryPushButton(FIF.ACCEPT, "采纳（保留修正）", self)
        self.btn_reject = PushButton(FIF.CLOSE, "拒绝（恢复原文）", self)
        self.btn_next = PushButton("下一条", self)
        self.btn_accept.clicked.connect(lambda: self._decide("accept"))
        self.btn_reject.clicked.connect(lambda: self._decide("reject"))
        self.btn_prev.clicked.connect(self._prev)
        self.btn_next.clicked.connect(self._next)
        bar.addWidget(self.btn_prev)
        bar.addWidget(self.btn_accept)
        bar.addWidget(self.btn_reject)
        bar.addWidget(self.btn_next)
        bar.addStretch(1)
        b_all_yes = PushButton("全部采纳", self)
        b_all_no = PushButton("全部拒绝", self)
        b_all_yes.clicked.connect(lambda: self._decide_all("accept"))
        b_all_no.clicked.connect(lambda: self._decide_all("reject"))
        bar.addWidget(b_all_yes)
        bar.addWidget(b_all_no)
        b_done = PrimaryPushButton("完成", self)
        b_done.clicked.connect(self.accept)
        bar.addWidget(b_done)
        lay.addLayout(bar)

        self._fill()
        if entries:
            self.list.setCurrentRow(0)

    # ------------------------------------------------------------ 数据
    @staticmethod
    def collect(doc) -> List[dict]:
        """从文档收集全部改动条目（text≠original 且 original 非空）。"""
        out: List[dict] = []
        for row, c in enumerate(doc.cues):
            if c.original_text and c.is_changed():
                out.append({"id": c.id, "row": row,
                            "original": c.original_text, "fixed": c.text})
        return out

    def result_actions(self) -> Dict[str, str]:
        return dict(self._actions)

    # ------------------------------------------------------------ 内部
    def _fill(self) -> None:
        from .theme import monospace
        self.list.clear()
        for i, e in enumerate(self._entries):
            act = self._actions.get(e["id"])
            mark = {"accept": "✓ 采纳", "reject": "✕ 拒绝", "skip": "— 跳过"}.get(act, "")
            head = f"[{e['row'] + 1}] {e['original']}"
            tail = f"→ {e['fixed']}"
            label = head + "\n   " + tail + (("    " + mark) if mark else "")
            it = QListWidgetItem(label)
            it.setFont(monospace(9))
            if act == "accept":
                it.setForeground(QColor("#2f9e5f"))
            elif act == "reject":
                it.setForeground(QColor("#c75050"))
            self.list.addItem(it)

    def _refresh_marks(self) -> None:
        """只刷新已决策的着色标记，不重建列表（保持滚动位置）。"""
        for i, e in enumerate(self._entries):
            act = self._actions.get(e["id"])
            it = self.list.item(i)
            if it is None:
                continue
            mark = {"accept": "✓ 采纳", "reject": "✕ 拒绝", "skip": "— 跳过"}.get(act, "")
            text = it.text()
            base = text.split("    ")[0]
            it.setText(base + (("    " + mark) if mark else ""))
            if act == "accept":
                it.setForeground(QColor("#2f9e5f"))
            elif act == "reject":
                it.setForeground(QColor("#c75050"))
            else:
                it.setForeground(self.palette().color(self.foregroundRole()))

    def _on_row(self, row: int) -> None:
        self._cur = row
        if not (0 <= row < len(self._entries)):
            self._orig_view.setText("")
            self._new_view.setText("")
            return
        e = self._entries[row]
        self._orig_view.setText(e["original"] or "（空）")
        self._new_view.setText(e["fixed"] or "（空）")

    def _decide(self, action: str) -> None:
        if not (0 <= self._cur < len(self._entries)):
            return
        e = self._entries[self._cur]
        self._actions[e["id"]] = action
        self._refresh_marks()
        self._next()

    def _decide_all(self, action: str) -> None:
        for e in self._entries:
            self._actions[e["id"]] = action
        self._refresh_marks()

    def _prev(self) -> None:
        if self._cur > 0:
            self.list.setCurrentRow(self._cur - 1)

    def _next(self) -> None:
        if self._cur + 1 < len(self._entries):
            self.list.setCurrentRow(self._cur + 1)
        else:
            # 最后一条决策完不自动关：用户可能想回头改主意，手动点「完成」
            self._head.setText(f"共 {len(self._entries)} 条改动：已全部浏览，点「完成」应用")
