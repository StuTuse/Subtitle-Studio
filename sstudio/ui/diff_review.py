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

from ..core.i18n import S
from .theme import CARD_MARGINS


class DiffReviewDialog(QDialog):
    """逐条 diff 复查。entries: [{id, row, original, fixed}]。

    exec_() 返回后用 result_actions() 取每条的决定：
      "accept" | "reject" | "skip"
    """

    def __init__(self, entries: List[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle(S("逐条复查纠错结果", "Review fixes one by one"))
        self.resize(860, 640)
        self.setModal(True)
        self._entries = entries
        self._actions: Dict[str, str] = {}
        self._cur = -1
        self._MARKS = {"accept": S("✓ 采纳", "✓ Accept"),
                       "reject": S("✕ 拒绝", "✕ Reject"),
                       "skip": S("— 跳过", "— Skip")}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(*CARD_MARGINS)
        n = len(entries)
        self._head = StrongBodyLabel(
            S(f"共 {n} 条改动：逐条核对后采纳或拒绝",
              f"{n} changes: review each, accept or reject"), self)
        lay.addWidget(self._head)
        self._hint = CaptionLabel(
            S("采纳 = 保留修正文本；拒绝 = 恢复原始识别文本。"
              "不确定的可以先跳过（保持修正），稍后在编辑页用状态色核对。",
              "Accept = keep the fixed text; Reject = restore the original. "
              "Unsure? Skip (keep the fix) and check state colors in the editor later."), self)
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
        o_lab = QLabel(S("原文：", "Original:"), detail)
        o_lab.setStyleSheet("color:#c75050;font-weight:600;")
        n_lab = QLabel(S("修正：", "Fixed:"), detail)
        n_lab.setStyleSheet("color:#2f9e5f;font-weight:600;")
        dv.addWidget(o_lab)
        dv.addWidget(self._orig_view)
        dv.addWidget(n_lab)
        dv.addWidget(self._new_view)
        lay.addWidget(detail)

        bar = QHBoxLayout()
        self.btn_prev = PushButton(FIF.LEFT_ARROW, S("上一条", "Previous"), self)
        self.btn_accept = PrimaryPushButton(FIF.ACCEPT, S("采纳（保留修正）", "Accept (keep fix)"), self)
        self.btn_reject = PushButton(FIF.CLOSE, S("拒绝（恢复原文）", "Reject (restore)"), self)
        self.btn_next = PushButton(S("下一条", "Next"), self)
        self.btn_accept.clicked.connect(lambda: self._decide("accept"))
        self.btn_reject.clicked.connect(lambda: self._decide("reject"))
        self.btn_prev.clicked.connect(self._prev)
        self.btn_next.clicked.connect(self._next)
        bar.addWidget(self.btn_prev)
        bar.addWidget(self.btn_accept)
        bar.addWidget(self.btn_reject)
        bar.addWidget(self.btn_next)
        bar.addStretch(1)
        self.btn_all_yes = PushButton(S("全部采纳", "Accept all"), self)
        self.btn_all_no = PushButton(S("全部拒绝", "Reject all"), self)
        self.btn_all_yes.clicked.connect(lambda: self._decide_all("accept"))
        self.btn_all_no.clicked.connect(lambda: self._decide_all("reject"))
        bar.addWidget(self.btn_all_yes)
        bar.addWidget(self.btn_all_no)
        b_done = PrimaryPushButton(S("完成", "Done"), self)
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
            mark = self._MARKS.get(act, "")
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
            mark = self._MARKS.get(act, "")
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
        self._orig_view.setText(e["original"] or S("（空）", "(empty)"))
        self._new_view.setText(e["fixed"] or S("（空）", "(empty)"))

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
            self._head.setText(S(
                f"共 {len(self._entries)} 条改动：已全部浏览，点「完成」应用",
                f"{len(self._entries)} changes: all reviewed, press Done to apply"))
