"""字幕列表：多行文本可编辑表格 + 状态着色 + 右键菜单。"""

from __future__ import annotations

from typing import List

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QHeaderView, QMenu,
                             QStyledItemDelegate, QTableWidget, QTableWidgetItem,
                             QTextEdit)

from ..core.model import Cue, sec_to_ts
from ..core.i18n import S
from .theme import _crisp, is_dark, monospace, state_color, state_text, status_hex

COL_NO, COL_S, COL_E, COL_D, COL_STATE, COL_TEXT = range(6)


class _TextDelegate(QStyledItemDelegate):
    """多行编辑的文本列编辑器。"""

    def createEditor(self, parent, option, index):  # noqa: N802
        ed = QTextEdit(parent)
        ed.setAcceptRichText(False)
        ed.setLineWrapMode(QTextEdit.WidgetWidth)
        ed.setMinimumHeight(96)
        ed.setFont(index.data(Qt.FontRole) or QFont())
        ed.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return ed

    def setEditorData(self, editor, index):  # noqa: N802
        editor.setPlainText(index.data(Qt.EditRole) or "")
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, lambda: (editor.setFocus(),
                                      editor.selectAll(),
                                      editor.setFocus()))

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.toPlainText().rstrip(), Qt.EditRole)

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(400, max(34, option.fontMetrics.height() * 2))


class CueTable(QTableWidget):
    cue_changed = pyqtSignal(int, str)      # row, new text
    cue_selected = pyqtSignal(int)
    cue_activated = pyqtSignal(int)         # 双击/回车 -> 跳到该条
    request_action = pyqtSignal(str, list)  # action, rows

    def __init__(self, parent=None):
        super().__init__(0, 6, parent)
        self.setWindowTitle("")
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setIconSize(QSize(14, 14))
        self._mono = monospace(9)
        self._body = QFont()
        self._body.setPointSizeF(9.5)
        _crisp(self._body)
        self.setItemDelegateForColumn(COL_TEXT, _TextDelegate(self))

        self.setHorizontalHeaderLabels(["#", S("开始", "Start"), S("结束", "End"),
                                        S("时长", "Dur"), S("状态", "State"),
                                        S("字幕内容", "Subtitle text")])
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_TEXT, QHeaderView.Stretch)
        hh.setStretchLastSection(True)
        # 时间戳是 00:00:12.340 共 12 字符，字号 9pt 下 100px 够用
        for col, w in ((COL_NO, 44), (COL_S, 100), (COL_E, 100), (COL_D, 56), (COL_STATE, 72)):
            self.setColumnWidth(col, w)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(34)

        self.itemChanged.connect(self._on_item_changed)
        self.cellDoubleClicked.connect(self._on_double)
        self.currentCellChanged.connect(self._on_current)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

        self._suspend = False
        self._suppress_rows = set()

    # ------------------------------------------------------------ 渲染
    # 模块级样式缓存：(state, dark) → (QColor badge, QColor text-bg)。
    # 渲染热路径每行 2~3 次新建 dict+QColor+QBrush（5000 行即上万个临时
    # 对象），LLM 流式回填又走 mark_row_llm 逐条触发——统一查这里
    _style_cache: dict = {}

    @classmethod
    def _styles(cls, state: str, dark: bool):
        ck = (state, dark)
        v = cls._style_cache.get(ck)
        if v is None:
            v = (_state_badge(state, dark), state_color(state, dark))
            cls._style_cache[ck] = v
        return v

    def render(self, cues: List[Cue], select_row: int = -1) -> None:
        self._suspend = True
        prev_sel = self.currentRow()
        # 全量重建期间冻结视口重绘：clearContents + setItem 每格都会触发
        # dataChanged→重排/重绘，5k 行时零散重绘累计数百 ms。包一层开关
        # 让 Qt 把脏区合并成恢复时的一次性重绘（实测 5k 行 176→96ms）。
        self.setUpdatesEnabled(False)
        try:
            self.clearContents()
            self.setRowCount(len(cues))
            dark = is_dark()
            err_hex = status_hex("err")
            for r, c in enumerate(cues):
                no = QTableWidgetItem(str(r + 1))
                no.setFont(self._mono)
                no.setTextAlignment(Qt.AlignCenter)
                no.setFlags(no.flags() & ~Qt.ItemIsEditable)
                self.setItem(r, COL_NO, no)

                for col, t in ((COL_S, c.start), (COL_E, c.end)):
                    it = QTableWidgetItem(sec_to_ts(t, sep=".", millis=True))
                    it.setFont(self._mono)
                    it.setTextAlignment(Qt.AlignCenter)
                    self.setItem(r, col, it)

                d = QTableWidgetItem(f"{c.duration:.1f}s")
                d.setFont(self._mono)
                d.setTextAlignment(Qt.AlignCenter)
                d.setFlags(d.flags() & ~Qt.ItemIsEditable)
                warn = _duration_warn(c)
                if warn:
                    # 与导出页「导出前检查」同一套标准：编辑时就把问题亮出来，
                    # 别等用户点到导出页才发现"3 条语速过快"
                    d.setForeground(QBrush(QColor("#ff6b6b") if dark else QColor("#d13438")))
                    d.setToolTip(warn + "\n（与导出预检同一套标准）")
                self.setItem(r, COL_D, d)

                badge, bg = self._styles(c.state, dark)
                st = QTableWidgetItem(_state_text_fast(c.state))
                st.setTextAlignment(Qt.AlignCenter)
                st.setFlags(st.flags() & ~Qt.ItemIsEditable)
                st.setBackground(QBrush(badge))
                self.setItem(r, COL_STATE, st)

                tx = QTableWidgetItem(c.display_text)
                tx.setData(Qt.EditRole, c.display_text)
                tx.setFont(self._body)
                tx.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap)
                tx.setToolTip(_tip(c))
                if bg.alpha():
                    tx.setBackground(QBrush(bg))
                if c.state == "review":
                    # 待复查：暗色亮红 / 浅色深红，两种皮肤都保持高对比
                    tx.setForeground(QBrush(QColor(err_hex)))
                self.setItem(r, COL_TEXT, tx)

                h = max(34, min(150, 20 + 16 * (c.display_text.count("\n") + 1)
                                + 16 * _wrap_lines(c)))
                self.setRowHeight(r, h)
        finally:
            self.setUpdatesEnabled(True)
        self._suspend = False
        row = select_row if 0 <= select_row < len(cues) else prev_sel
        if 0 <= row < len(cues):
            self.selectRow(row)

    def update_row(self, row: int, cue: Cue) -> None:
        if not (0 <= row < self.rowCount()):
            return
        tx, st = self.item(row, COL_TEXT), self.item(row, COL_STATE)
        if tx is None or st is None:      # 行存在但单元格未建（异常路径防御）
            return
        self._suppress_rows.add(row)
        try:
            # 全等短路：undo 局部刷新会逐行调到这里，绝大多数行没变——
            # setData/setText 每格都发 dataChanged，5k 行无谓刷就是浪费
            if tx.text() != cue.display_text:
                tx.setData(Qt.EditRole, cue.display_text)
                tx.setText(cue.display_text)
            badge, _ = self._styles(cue.state, is_dark())
            st_txt = _state_text_fast(cue.state)
            if st.text() != st_txt:
                st.setText(st_txt)
            if st.background().color() != badge:
                st.setBackground(QBrush(badge))
            # 时间列：撤销平移/延长后 S/E/D 必须跟上（旧版只刷文本，
            # 撤销 shift 后表格时间显示是旧值，保存却用新值——显示与
            # 数据不一致）。值没变就不动。
            s_ts = sec_to_ts(cue.start, sep=".", millis=True)
            e_ts = sec_to_ts(cue.end, sep=".", millis=True)
            it_s, it_e = self.item(row, COL_S), self.item(row, COL_E)
            if it_s is not None and it_s.text() != s_ts:
                it_s.setText(s_ts)
            if it_e is not None and it_e.text() != e_ts:
                it_e.setText(e_ts)
            it_d = self.item(row, COL_D)
            if it_d is not None:
                d_txt = f"{cue.duration:.1f}s"
                if it_d.text() != d_txt:
                    it_d.setText(d_txt)
        finally:
            self._suppress_rows.discard(row)

    def mark_row_llm(self, row: int, text: str) -> None:
        """LLM 流式回填：只更新文本与状态底色，保持滚动位置。"""
        if not (0 <= row < self.rowCount()):
            return
        it = self.item(row, COL_TEXT)
        st = self.item(row, COL_STATE)
        if it is None or st is None:      # 行存在但单元格未建（异常路径防御）
            return
        badge, bg = self._styles("llm", is_dark())
        self._suppress_rows.add(row)
        try:
            if it.text() != text:
                it.setData(Qt.EditRole, text)
                it.setText(text)
            llm_txt = _state_text_fast("llm")
            if st.text() != llm_txt:
                st.setText(llm_txt)
            st.setBackground(QBrush(badge))
            it.setBackground(QBrush(bg))
        finally:
            self._suppress_rows.discard(row)

    def jump(self, row: int) -> None:
        if 0 <= row < self.rowCount():
            self.selectRow(row)
            self.scrollToItem(self.item(row, COL_TEXT), QAbstractItemView.PositionAtCenter)

    def selected_rows(self) -> List[int]:
        return sorted({i.row() for i in self.selectedIndexes()})

    # ------------------------------------------------------------ 事件
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._suspend or item.row() in self._suppress_rows:
            return
        r = item.row()
        if item.column() == COL_TEXT:
            new = item.data(Qt.EditRole)
            if new is None:
                new = item.text()
            self.cue_changed.emit(r, str(new))
        elif item.column() in (COL_S, COL_E):
            # 时间列：交给主界面解析（支持 12.3 / 00:00:12.340 两种写法）
            self.request_action.emit("set_time:%d:%d" % (r, item.column()), [r])

    def _on_double(self, row: int, col: int) -> None:
        if col != COL_TEXT:
            self.cue_activated.emit(row)

    def _on_current(self, row: int, *_a) -> None:
        if row >= 0:
            self.cue_selected.emit(row)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.state() != QTableWidget.EditingState:
            self.request_action.emit("delete", self.selected_rows())
            e.accept()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ControlModifier):
            if self.currentRow() >= 0:
                self.cue_activated.emit(self.currentRow())
                e.accept()
                return
        super().keyPressEvent(e)

    def _menu(self, pos) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        m = QMenu(self)
        m.addAction(S("跳到这条", "Jump to cue"), lambda: self.cue_activated.emit(rows[0]))
        m.addAction(S("从这条开始播放", "Play from here"), lambda: self.cue_activated.emit(rows[0]))
        m.addSeparator()
        m.addAction(S("播放选中片段", "Play selection"),
                    lambda: self.request_action.emit("play_range", rows))
        m.addAction(S("从头播放到末条结束", "Play to end"),
                    lambda: self.request_action.emit("play_selection", rows))
        m.addSeparator()
        m.addAction(S("在时间点拆分（空格处）", "Split at playhead (at space)") if len(rows) == 1
                    else S("拆分（按字数）", "Split (by length)"),
                    lambda: self.request_action.emit("split", rows))
        if len(rows) >= 2:
            m.addAction(S(f"合并选中的 {len(rows)} 条", f"Merge {len(rows)} selected"),
                        lambda: self.request_action.emit("merge", rows))
        m.addAction(S("在选中前插入空条目", "Insert empty before"),
                    lambda: self.request_action.emit("insert", rows))
        m.addSeparator()
        for label, act in ((S("标记为待复查", "Mark as review"), "review"),
                           (S("标记为已确认", "Mark as confirmed"), "confirmed"),
                           (S("恢复为原始识别文本", "Restore original text"), "revert"),
                           (S("清除该行标点", "Strip punctuation"), "strip_punct")):
            m.addAction(label, lambda a=act: self.request_action.emit(a, rows))
        m.addSeparator()
        # 智能断句/去重是文档级批处理：接通 model.split_long /
        # dedupe_repeats——功能早已实现却一直没有 UI 入口。
        m.addAction(S("智能断句（拆过长条目）", "Smart re-split long cues"),
                    lambda: self.request_action.emit("split_long", rows))
        m.addAction(S("删除连续重复句", "Delete repeated lines"),
                    lambda: self.request_action.emit("dedupe", rows))
        m.addSeparator()
        m.addAction(S("前移 0.10s", "Shift earlier 0.10s"),
                    lambda: self.request_action.emit("shift:-0.1", rows))
        m.addAction(S("后移 0.10s", "Shift later 0.10s"),
                    lambda: self.request_action.emit("shift:0.1", rows))
        m.addAction(S("延长 0.20s", "Extend 0.20s"),
                    lambda: self.request_action.emit("extend:0.2", rows))
        m.addAction(S("缩短 0.20s", "Shorten 0.20s"),
                    lambda: self.request_action.emit("extend:-0.2", rows))
        m.addSeparator()
        m.addAction(S("复制文本", "Copy text"), lambda: self.request_action.emit("copy", rows))
        m.addAction(S("删除", "Delete"), lambda: self.request_action.emit("delete", rows))
        m.exec_(self.mapToGlobal(pos))


# ---------------------------------------------------------------- helpers
_state_text_cache: dict = {}


def _state_text_fast(state: str) -> str:
    """state_text 的按主题缓存版：render 热路径每行一次 dict+函数查找，
    5k 行 x 每秒多次全量重染时是可感的固定开销。主题切换走
    invalidate_theme_cache 时顺带清空（下方由 render 侧的 dark 比对保证
    缓存键正确——键含 dark，切主题天然失效）。"""
    ck = (state, is_dark())
    v = _state_text_cache.get(ck)
    if v is None:
        v = state_text(state)
        _state_text_cache[ck] = v
    return v


def _duration_warn(c: Cue) -> str:
    """时长列警示文案：与 export_page 预检（>28 字 / <0.5s / >9 字每秒）、
    本表自身的 >8s 过长红字共用同一套判定，返回空串表示没有问题。"""
    if c.duration > 8:
        return S(f"时长 {c.duration:.1f}s，超过 8s——考虑拆分",
                 f"Duration {c.duration:.1f}s, over 8s — consider splitting")
    if c.duration < 0.5:
        return S(f"时长 {c.duration:.1f}s，不足 0.5s——播放时会一闪而过",
                 f"Duration {c.duration:.1f}s, under 0.5s — will flash by")
    n = len(c.display_text.replace("\n", ""))
    if c.duration > 0 and n / c.duration > 9:
        return S(f"约 {n / c.duration:.1f} 字/秒，超过 9 字/秒——观众跟不上",
                 f"~{n / c.duration:.1f} chars/sec, over 9 — too fast to read")
    return ""


def _state_badge(state: str, dark: bool) -> QColor:
    # 暗色徽章提高亮度与饱和度，默认灰字（#e6e6e6）放上去才读得清
    return {
        "asr": QColor("#4a4a4a") if dark else QColor("#dfe6ec"),
        "llm": QColor("#1f7a4d") if dark else QColor("#b7e6c8"),
        "edited": QColor("#93702a") if dark else QColor("#f6e2a8"),
        "review": QColor("#a83232") if dark else QColor("#f8c9c9"),
        "confirmed": QColor("#2f66a3") if dark else QColor("#c5ddf5"),
    }.get(state, QColor(Qt.transparent))


_tip_cache: dict = {}


def _tip(c: Cue) -> str:
    # 悬浮提示只依赖 (start,end,confidence,state,text,original_text)：
    # 全量重染时 5k 行重复构造（17.6ms）毫无必要。键用这些字段的元组，
    # 编辑该行时键自然变化；缓存上限 2 万条防极端文档内存膨胀。
    key = (c.start, c.end, c.confidence, c.state, c.text, c.original_text)
    v = _tip_cache.get(key)
    if v is None:
        bits = [f"{sec_to_ts(c.start)} → {sec_to_ts(c.end)}"]
        if c.confidence is not None:
            bits.append(S(f"置信度 {c.confidence:.2f}", f"Confidence {c.confidence:.2f}"))
        if c.state == "review":
            bits.append(S("⚠ 待复查", "⚠ Review"))
        if c.is_changed():
            bits.append(S("原文：", "Original:")
                        + (c.original_text or "").replace("\n", " / "))
        v = "\n".join(bits)
        if len(_tip_cache) >= 20000:
            _tip_cache.clear()
        _tip_cache[key] = v
    return v


def _wrap_lines(c: Cue) -> int:
    n = 0
    for ln in c.display_text.split("\n"):
        n += max(1, (len(ln) + 29) // 30)
    return n - 1
