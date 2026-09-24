"""字幕编辑主界面：播放器 + 时间轴 + 字幕表 + 工具条 + 编辑区。"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from qfluentwidgets import (Action, BodyLabel, CaptionLabel, CommandBar, FluentIcon as FIF,
                            IndeterminateProgressBar, InfoBar, InfoBarPosition, LineEdit,
                            PrimaryPushButton, PrimaryToolButton, ProgressBar, PushButton,
                            SearchLineEdit,
                            Slider, StrongBodyLabel, ToolButton)
from PyQt5.QtCore import Qt, QObject, QEvent, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QComboBox, QDialog,
                             QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                             QPlainTextEdit, QShortcut, QSizePolicy, QSplitter,
                             QTextEdit, QVBoxLayout, QWidget, QApplication)

from ..core import formats
from ..core.config import Config
from ..core.model import Cue, CueDocument, normalize_cues, sec_to_ts, ts_to_sec
from .cue_table import COL_S, CueTable
from .player import PlayerWidget
from .theme import err_span, human_time, is_dark
from .timeline import Timeline


class EditorInterface(QWidget):
    doc_changed = pyqtSignal()
    say = pyqtSignal(str, int)

    def __init__(self, cfg: Config, main):
        super().__init__()
        self.setObjectName("editor")
        self.setWindowTitle("字幕编辑")
        self.cfg = cfg
        self.main = main
        self.doc: Optional[CueDocument] = None
        self._undo: List[dict] = []
        self._redo: List[dict] = []
        self._follow = True
        self._editing_row = -1
        # 编辑缓冲归属：缓冲里装的是哪一行的内容、是否带未落盘的用户输入。
        # 程序化换行（播放跟随/选中切换）覆盖缓冲前必须查这两个标志，
        # 否则「新行原文+我打的字」会被写进别的行（丢输入类 P0）
        self._edit_buf_row = -1
        self._edit_buf_dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 6)
        root.setSpacing(6)

        # ------------------------------------------------------- 工具条
        self.bar = CommandBar(self)
        self.bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        def act(icon, text, slot):
            a = Action(icon, text, self)
            a.triggered.connect(slot)
            return a

        self.bar.addActions([
            act(FIF.VIDEO, "导入视频", lambda: self.main.open_media_dialog()),
            act(FIF.PLAY, "开始转写", lambda: self.main.start_transcribe()),
            act(FIF.BROOM, "AI 纠错", lambda: self.main.goto_fix()),
            act(FIF.SAVE, "导出", lambda: self.main.switch_to("export")),
        ])
        self.bar.addSeparator()
        self.bar.addActions([
            act(FIF.FOLDER, "打开工程", self._open_project),
            act(FIF.SAVE_AS, "保存工程", lambda: self.main.save_project()),
            act(FIF.ADD, "导入字幕", self._import_subtitle),
            act(FIF.REMOVE, "撤销", self.undo),
            act(FIF.SYNC, "重做", self.redo),
        ])
        self.bar.addSeparator()
        self.btn_filter = act(FIF.SEARCH, "筛选", self._toggle_filter)
        self.bar.addAction(self.btn_filter)
        root.addWidget(self.bar)

        # -------------------------------------------------- 筛选行（默认隐藏）
        self.filter_row = QWidget(self)
        fr = QHBoxLayout(self.filter_row)
        fr.setContentsMargins(0, 0, 0, 0)
        self.search = SearchLineEdit(self.filter_row)
        self.search.setPlaceholderText("搜索字幕文本 / 输入 #数字 跳到第 N 条 / 支持正则")
        self.search.setMaximumWidth(420)
        # 每 keystroke 全表 setRowHidden + 正则匹配：5000 条时中文输入法
        # 连续上字明显掉帧。180ms 防抖，末键才真正过滤
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._filter)
        self.search.textChanged.connect(
            lambda _t: self._search_timer.start())
        fr.addWidget(self.search)
        self.chk_only_problem = PushButton(self.filter_row)
        self.chk_only_problem.setCheckable(True)
        self.chk_only_problem.setText("只看待复查/过长/过快")
        self.chk_only_problem.clicked.connect(self._filter)
        fr.addWidget(self.chk_only_problem)
        self.btn_replace = PushButton("替换…", self.filter_row)
        self.btn_replace.setToolTip("按当前搜索词批量替换（支持正则），可撤销")
        self.btn_replace.clicked.connect(self._replace_dialog)
        fr.addWidget(self.btn_replace)
        fr.addStretch(1)
        self.stat_label = CaptionLabel("—", self.filter_row)
        fr.addWidget(self.stat_label)
        self.filter_row.setVisible(False)
        root.addWidget(self.filter_row)

        # ---------------------------------------------- 导入/转写流程面板
        # 空态时占住主区域：导入 → 开始转写 → 进度，全部在这一条流程里。
        self.hero = QFrame(self)
        self.hero.setObjectName("heroPanel")
        self.hero.setStyleSheet(
            "#heroPanel{background:palette(base);border:1px dashed palette(mid);"
            "border-radius:10px;}")
        hv = QVBoxLayout(self.hero)
        hv.setContentsMargins(24, 18, 24, 18)
        hv.setSpacing(8)
        self.hero_title = StrongBodyLabel("拖入视频，或选择一个文件开始", self.hero)
        from .theme import hero_font
        self.hero_title.setFont(hero_font())
        hv.addWidget(self.hero_title, 0, Qt.AlignHCenter)
        self.hero_sub = CaptionLabel(
            "导入后点「开始转写」：自动提取音频 → 本地 Whisper 识别，全程离线", self.hero)
        hv.addWidget(self.hero_sub, 0, Qt.AlignHCenter)
        brow = QHBoxLayout()
        brow.setSpacing(10)
        brow.addStretch(1)
        self.btn_import2 = PushButton(" 选择视频", self.hero)
        self.btn_import2.clicked.connect(lambda: self.main.open_media_dialog())
        brow.addWidget(self.btn_import2)
        self.btn_start = PrimaryPushButton(" 开始转写", self.hero, FIF.PLAY)
        self.btn_start.setMinimumHeight(34)
        self.btn_start.setMinimumWidth(150)
        self.btn_start.setEnabled(False)
        self.btn_start.setToolTip("提取音频并开始识别（需先在上方或此处导入视频）")
        self.btn_start.clicked.connect(lambda: self.main.start_transcribe())
        brow.addWidget(self.btn_start)
        brow.addStretch(1)
        hv.addLayout(brow)
        prow = QHBoxLayout()
        prow.setSpacing(10)
        self.hero_bar = ProgressBar(self.hero)
        self.hero_bar.setRange(0, 1000)
        self.hero_bar.setValue(0)
        self.hero_bar.setTextVisible(False)
        self.hero_bar.setFixedWidth(360)
        self.hero_bar.setVisible(False)
        prow.addWidget(self.hero_bar)
        self.hero_pct = CaptionLabel("", self.hero)
        self.hero_pct.setMinimumWidth(42)
        self.hero_pct.setVisible(False)
        prow.addWidget(self.hero_pct)
        self.btn_cancel = PushButton("取消", self.hero)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(lambda: self.main.cancel_transcribe())
        prow.addWidget(self.btn_cancel)
        prow.addStretch(1)
        ph = QHBoxLayout()
        ph.addStretch(1); ph.addLayout(prow); ph.addStretch(1)
        hv.addLayout(ph)
        self.hero_status = CaptionLabel("", self.hero)
        hv.addWidget(self.hero_status, 0, Qt.AlignHCenter)
        self.hero.setMinimumHeight(150)
        root.addWidget(self.hero)
        self._flow = "empty"

        # ------------------------------ 最近工程（空态时显示，回头客直达）
        # cfg.recent_files 一直被认真维护却从没有 UI 读它：改字幕的用户
        # 只能去资源管理器考古。这里放一条可点链接行，仅在空态出现。
        self.recent_row = QWidget(self)
        rr = QHBoxLayout(self.recent_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(6)
        self.recent_label = CaptionLabel("最近：", self.recent_row)
        rr.addWidget(self.recent_label)
        self.recent_links = [CaptionLabel("", self.recent_row) for _ in range(4)]
        for lb in self.recent_links:
            lb.setVisible(False)
            lb.setCursor(Qt.PointingHandCursor)
            lb.linkActivated.connect(lambda _u, w=lb: self._open_recent(w._recent_path))
            rr.addWidget(lb)
        rr.addStretch(1)
        self.recent_row.setVisible(False)
        root.addWidget(self.recent_row)

        # -------------------------------------------------- 播放器 + 字幕表
        # 排版参考 Subtitle Edit：上带「播放器 | 修改区」并排（时间轴/控件条
        # 全宽压在下面），下带字幕表独占整行。列表横竖都 ≥ 半屏：
        # 竖向约 62%，横向 100% 宽。编辑区不再和列表抢横向空间。
        split = QSplitter(Qt.Vertical, self)
        split.setHandleWidth(5)   # 上下两区贴紧：手柄细一点
        root.addWidget(split, 1)
        self.split = split
        split.setVisible(False)   # 空态先让位给流程面板，转完再上场

        top = QWidget(split)
        tl = QGridLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(3)          # 播放器→时间轴→播放控件：贴紧，别散

        # 上带左右分栏：播放器吃左边，修改区吃右边
        hsplit = QSplitter(Qt.Horizontal, top)
        hsplit.setHandleWidth(6)
        self.player = PlayerWidget(hsplit)
        hsplit.addWidget(self.player)

        edit_box = QWidget(hsplit)
        eb = QVBoxLayout(edit_box)
        eb.setContentsMargins(4, 0, 0, 0)
        eb.setSpacing(5)
        self.cur_row = CaptionLabel("未选中", edit_box)
        self.cur_row.setWordWrap(True)
        eb.addWidget(self.cur_row)
        # 小进度条：当前选中的是第几条 / 一共多少条，改字幕时一眼看到进度
        prrow = QHBoxLayout()
        prrow.setContentsMargins(0, 0, 0, 0)
        prrow.setSpacing(6)
        self.pos_bar = ProgressBar(edit_box)
        self.pos_bar.setRange(0, 100)
        self.pos_bar.setValue(0)
        self.pos_bar.setTextVisible(False)
        self.pos_bar.setFixedHeight(8)
        self.pos_bar.setToolTip("当前字幕在全部字幕中的位置")
        prrow.addWidget(self.pos_bar, 1)
        self.pos_label = CaptionLabel("0 / 0", edit_box)
        self.pos_label.setMinimumWidth(58)
        prrow.addWidget(self.pos_label)
        eb.addLayout(prrow)
        eb.addWidget(BodyLabel("修改内容（Enter 保存并跳下一条）", edit_box))
        self.edit_area = QPlainTextEdit(edit_box)
        self.edit_area.setPlaceholderText(
            "选中一条字幕后，这里会显示它的文本。\n"
            "直接改错别字，Enter 保存并跳到下一条；\n"
            "Shift+Enter 才是换行。（双击表格里的文字也能就地编辑）")
        from .theme import edit_font
        self.edit_area.setFont(edit_font())   # CJK 小字全 hinting，暗浅底都更实
        self.edit_area.setTabChangesFocus(True)
        self.edit_area.setMinimumWidth(220)
        self.edit_area.setMinimumHeight(48)
        eb.addWidget(self.edit_area, 1)

        er = QHBoxLayout()
        er.setSpacing(6)
        self.btn_save_edit = PrimaryPushButton(FIF.SAVE, "保存并下一条", edit_box)
        self.btn_save_edit.clicked.connect(self._apply_inline)
        er.addWidget(self.btn_save_edit)
        b_del = PushButton(FIF.DELETE, "删除", edit_box)
        b_del.setToolTip("删除选中条目（Del 键同样可用）")
        b_del.clicked.connect(lambda: self._act("delete"))
        er.addWidget(b_del)
        er.addStretch(1)
        eb.addLayout(er)
        er2 = QHBoxLayout()
        er2.setSpacing(6)
        for label, slot, tip in (
                ("F2 拆分", lambda: self._act("split"), "在播放头位置把选中字幕一分为二"),
                ("F3 合并", lambda: self._act("merge"), "把选中的多条合成一条"),
                ("−0.1s", lambda: self._act("shift:-0.1"), "整条时间轴前移"),
                ("+0.1s", lambda: self._act("shift:0.1"), "整条时间轴后移"),
                ("消空隙", lambda: self._act("close_gaps"),
                 "连续说话时字幕之间若有小空隙，播放会闪断：把不超过阈值的前一条"
                 "结束时间拉齐到后一条开始。句末标点（。！？）结尾、或换了说话人"
                 "的空隙会保留。阈值在 设置→其它 调。Ctrl+Z 撤销。")):
            b = PushButton(edit_box)
            b.setText(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            er2.addWidget(b)
        er2.addStretch(1)
        eb.addLayout(er2)
        hsplit.addWidget(edit_box)
        hsplit.setCollapsible(0, False)
        hsplit.setCollapsible(1, False)
        # 播放器侧定住，富余宽度全给修改区
        hsplit.setStretchFactor(0, 0)
        hsplit.setStretchFactor(1, 1)
        if cfg.editor_hsplit:
            hsplit.setSizes(cfg.editor_hsplit)
        else:
            hsplit.setSizes([640, 480])
        self.hsplit = hsplit
        tl.addWidget(hsplit, 0, 0, 1, 3)

        self.timeline = Timeline(top)
        tl.addWidget(self.timeline, 1, 0, 1, 3)

        transport = QWidget(top)
        th = QHBoxLayout(transport)
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(4)
        self.b_prev = ToolButton(FIF.LEFT_ARROW, transport)
        self.b_prev.setToolTip("上一条 ←")
        self.b_play = PrimaryToolButton(FIF.PLAY, transport)
        self.b_play.setToolTip("播放/暂停 空格")
        self.b_next = ToolButton(FIF.RIGHT_ARROW, transport)
        self.b_next.setToolTip("下一条 →")
        self.b_rewind = ToolButton(transport)
        self.b_rewind.setText("−5s")
        self.b_forward = ToolButton(transport)
        self.b_forward.setText("+5s")
        self.b_speed = ToolButton(transport)
        self.b_speed.setText("1.0x")
        self.b_speed.setToolTip("播放速度（点击切换）")
        self.b_mute = ToolButton(FIF.VOLUME, transport)
        self.vol = Slider(Qt.Horizontal, transport)
        self.vol.setRange(0, 100)
        self.vol.setValue(cfg.player_volume)
        self.vol.setFixedWidth(90)
        self.time_label = BodyLabel("0:00.00 / 0:00.00", transport)
        self.b_loop_a = ToolButton(transport)
        self.b_loop_a.setText("A")
        self.b_loop_a.setToolTip("循环起点")
        self.b_loop_b = ToolButton(transport)
        self.b_loop_b.setText("B")
        self.b_loop_b.setToolTip("循环终点")
        self.b_loop_clear = ToolButton(transport)
        self.b_loop_clear.setText("A-B")
        self.b_loop_clear.setToolTip("取消循环")
        self.follow_btn = ToolButton(transport)
        self.follow_btn.setCheckable(True)
        self.follow_btn.setChecked(True)
        self.follow_btn.setText("跟随")
        self.follow_btn.setToolTip("播放时自动滚动到当前字幕")
        for w in (self.b_prev, self.b_play, self.b_next, self.b_rewind, self.b_forward,
                  self.time_label, self.b_speed, self.vol, self.b_mute,
                  self.b_loop_a, self.b_loop_b, self.b_loop_clear, self.follow_btn):
            th.addWidget(w)
        th.addStretch(1)
        tl.addWidget(transport, 2, 0, 1, 3)
        split.addWidget(top)

        bottom = QWidget(split)
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(3)          # 紧贴上方的播放控件

        # 字幕表独占下带整行：宽度即整页宽，编辑框挪到右上不再挤它
        self.table = CueTable(bottom)
        bl.addWidget(self.table, 1)
        split.addWidget(bottom)
        # 上下比例：字幕列表是主战场 → 播放器区压到最小可看高度，
        # 列表区吃掉其余全部空间（实测约 62%，稳定过半屏）。
        split.setSizes([480, 800])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        # ---------------------------------------------------- 状态条
        self.status = BodyLabel("就绪。拖入一个视频开始。", self)
        self.status.setObjectName("editorStatus")
        root.addWidget(self.status)

        self._connect()
        self._shortcuts()
        QTimer.singleShot(0, self._refresh_enabled)

    # ------------------------------------------------------------ 信号连接
    def _connect(self) -> None:
        self.b_play.clicked.connect(self.player.toggle)
        self.b_prev.clicked.connect(lambda: self._step(-1))
        self.b_next.clicked.connect(lambda: self._step(1))
        self.b_rewind.clicked.connect(lambda: self.player.nudge(-5))
        self.b_forward.clicked.connect(lambda: self.player.nudge(5))
        self.b_speed.clicked.connect(lambda: self.b_speed.setText(f"{self.player.cycle_speed():.2f}x"))
        self.b_mute.clicked.connect(lambda: self.vol.setValue(self.player.toggle_mute()))
        self.vol.valueChanged.connect(self.player.set_volume)
        self.b_loop_a.clicked.connect(lambda: (self.player.set_loop_a(), self._say("已设循环起点 A")))
        self.b_loop_b.clicked.connect(lambda: (self.player.set_loop_b(), self._say("已设循环终点 B")))
        self.b_loop_clear.clicked.connect(lambda: (self.player.clear_loop(), self._say("已取消循环")))
        self.follow_btn.toggled.connect(lambda v: setattr(self, "_follow", bool(v)))
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.durationChanged.connect(self._on_media_state)
        self.player.stateChanged.connect(self._on_media_state)
        self.player.error.connect(lambda m: self._say(m, 6000))
        self.timeline.seek_requested.connect(self.player.seek)
        self.timeline.cue_clicked.connect(self._select_row)
        self.timeline.cue_range.connect(self._select_range)
        self.table.cue_changed.connect(self._on_text_changed)
        self.table.cue_selected.connect(self._on_select)
        self.table.cue_activated.connect(self._jump_to)
        self.table.request_action.connect(self._act)
        # 主题切换（light/dark/auto）后重染表格状态色、重绘时间轴
        self._last_dark = None
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(1500)   # 400ms 纯浪费：切主题 1.5s 内跟上无感
        self._theme_timer.timeout.connect(self._watch_theme)
        self._theme_timer.start()
        # Enter 保存并下一条；Shift+Enter 换行（在 eventFilter 里拦截）
        self.edit_area.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.edit_area:
            if (event.type() == QEvent.KeyPress and event.key() in
                    (Qt.Key_Return, Qt.Key_Enter)
                    and not (event.modifiers() & Qt.ShiftModifier)):
                self._apply_inline()
                return True
            if event.type() == QEvent.FocusOut:
                # 离开编辑区先落盘：否则改了字直接点别处，改动会被无声丢掉。
                # 两边都去掉首尾换行再比：toPlainText() 常带一个尾随 \n，
                # 直接比会让"没动过的行"每次都判定有改动、反复走保存路径。
                cur = (self.doc.cues[self._editing_row].display_text
                       if self.doc and 0 <= self._editing_row < len(self.doc.cues)
                       else None)
                if self.edit_area.toPlainText().strip("\n") != (
                        cur.strip("\n") if cur is not None else None):
                    self._apply_inline_silent()
        return False

    def _apply_inline_silent(self) -> None:
        """失焦保存：只写回，不跳下一条。"""
        row = self._editing_row
        if not self.doc or row < 0 or row >= len(self.doc.cues):
            return
        self._on_text_changed(row, self.edit_area.toPlainText().strip("\n"))
        self._edit_buf_row = row        # 已落盘：缓冲现在干净地属于这一行
        self._edit_buf_dirty = False

    def _shortcuts(self) -> None:
        # 组合键类：不会与打字冲突，直接做 QShortcut
        combo = [(QKeySequence("Ctrl+Z"), self.undo),
                 (QKeySequence("Ctrl+Y"), self.redo),
                 (QKeySequence("Ctrl+S"), lambda: self.main.save_project()),
                 (QKeySequence("Ctrl+O"), lambda: self.main.open_media_dialog()),
                 (QKeySequence("Ctrl+I"), self._import_subtitle),
                 (QKeySequence("Ctrl+G"), lambda: self.main.start_transcribe()),
                 (QKeySequence("Ctrl+D"), lambda: self._act("delete")),
                 (QKeySequence("Ctrl+F"), self._focus_search),
                 (QKeySequence("F2"), lambda: self._act("split")),
                 (QKeySequence("F3"), lambda: self._act("merge")),
                 ]
        for seq, fn in combo:
            s = QShortcut(seq, self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(fn)
        # 裸键（空格/方向键/逗号句号）：只在非文本输入控件里生效，避免吃掉打字
        self._key_filter = _TransportKeyFilter(self.player, self)
        self._key_filter.install()

    def transport_key(self, key: int, mods) -> bool:
        """返回 True 表示这个按键已被播放器消费。"""
        shift = bool(mods & Qt.ShiftModifier)
        if key == Qt.Key_Space:
            self.player.toggle()
            return True
        if key == Qt.Key_Left:
            self.player.nudge(-1 if shift else -5)
            return True
        if key == Qt.Key_Right:
            self.player.nudge(1 if shift else 5)
            return True
        if key in (Qt.Key_Comma,):
            self.player.nudge(-1 / 30.0)
            return True
        if key in (Qt.Key_Period,):
            self.player.nudge(1 / 30.0)
            return True
        if key == Qt.Key_BracketLeft:
            self._nudge_sel(-0.1)
            return True
        if key == Qt.Key_BracketRight:
            self._nudge_sel(0.1)
            return True
        return False

    # ------------------------------------------------------------ 文档挂载
    # ------------------------------------------------------------ 导入/转写流程
    def _set_flow(self, state: str, msg: str = "") -> None:
        """empty=没导入；ready=已导入待转写；busy=转写中；done=已有字幕。"""
        self._flow = state
        busy = state == "busy"
        ready = state == "ready"
        self.btn_start.setEnabled(ready)
        self.btn_start.setText(" 正在转写…" if busy else " 开始转写")
        self.btn_import2.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.hero_bar.setVisible(busy)
        self.hero_pct.setVisible(busy)
        if state == "empty":
            self.hero_title.setText("拖入视频，或选择一个文件开始")
            self.hero_sub.setText("导入后点「开始转写」：自动提取音频 → 本地 Whisper 识别，全程离线")
            self.hero_pct.setText("")
            self.hero_bar.setRange(0, 1000); self.hero_bar.setValue(0)
            self._refresh_recent()
        elif state == "ready":
            self.hero_title.setText(msg or "视频已就绪")
            self.hero_sub.setText("点「开始转写」：自动提取音频 → 本地 Whisper 识别（全程离线）")
            self.recent_row.setVisible(False)
        elif state == "busy":
            self.hero_title.setText("正在转写…")
            self.hero_sub.setText(msg or "提取音频 → 识别中，请稍候")
            self.recent_row.setVisible(False)
        self.hero.setVisible(state != "done")

    def _refresh_recent(self) -> None:
        """空态下展示最近打开的工程/媒体；失效路径直接跳过不占位。"""
        import os as _os
        recents = [p for p in (self.cfg.recent_files or []) if _os.path.isfile(p)][:4]
        any_link = False
        for i, lb in enumerate(self.recent_links):
            if i < len(recents):
                p = recents[i]
                lb._recent_path = p
                name = _os.path.basename(p)
                lb.setText(f'<a href="#" style="text-decoration:none">{name}</a>')
                lb.setToolTip(p)
                lb.setVisible(True)
                any_link = True
            else:
                lb.setVisible(False)
        self.recent_row.setVisible(any_link)

    def _open_recent(self, path: str) -> None:
        if not path:
            return
        self.main._load_any(path)   # ssp 走工程，其余按扩展名导入

    def set_flow_progress(self, msg: str, pct: float) -> None:
        """由主窗转写进度驱动 hero 读条。pct<0 表示不确定阶段。"""
        if self._flow != "busy":
            return
        if pct is not None and pct >= 0:
            self.hero_bar.setRange(0, 1000)
            self.hero_bar.setValue(int(max(0.0, min(1.0, pct)) * 1000))
            self.hero_pct.setText(f"{pct * 100:.0f}%")
        else:
            self.hero_bar.setRange(0, 0)      # 忙碌指示
            self.hero_pct.setText("…")
        self.hero_sub.setText(msg)

    def _watch_theme(self) -> None:
        """轮询 qfluentwidgets 主题（它切主题不发 Qt 信号）。变了就重染表格。"""
        try:
            d = is_dark()
        except Exception:
            return
        if self._last_dark is None:
            self._last_dark = d
            return
        if d != self._last_dark:
            self._last_dark = d
            # auto 模式跟随系统明暗切换：调色板也要跟着换
            from .theme import _apply_app_palette
            try:
                _apply_app_palette(d)
            except Exception:
                pass
            if self.doc is not None:
                self.table.render(self.doc.cues, self.table.currentRow())
            self.table.viewport().update()

    def set_document(self, doc: Optional[CueDocument], reset_history: bool = True) -> None:
        self.doc = doc
        self._last_dark = None      # 空态期间主题可能已变过：强制下一拍重染一次
        if reset_history:
            self._undo, self._redo = [], []
        self.table.render(doc.cues if doc else [])
        self.timeline.set_document(doc)
        if doc is not None and doc.source_video and os.path.isfile(doc.source_video):
            self.player.load(doc.source_video)
            self.player.set_volume(self.cfg.player_volume)
        self._refresh_enabled()
        self._filter(self.search.text())
        self.update_status()
        self._update_pos_bar(self.table.currentRow())
        # 流程面板：有字幕→让位；只有视频→ready；什么都没有→empty
        # 注意：CueDocument 定义了 __len__，空字幕的 doc 布尔值是 False，
        # 必须用 is not None 判空。
        if doc is not None and doc.cues:
            self._set_flow("done")
        elif doc is not None and doc.source_video:
            self._set_flow("ready", f"已导入：{os.path.basename(doc.source_video)}")
        else:
            self._set_flow("empty")
        self.split.setVisible(self._flow == "done")
        if self._flow == "done":
            # 隐藏期间的 setSizes 会在"首次显示"时被 Qt 按 sizeHint 重新分配；
            # 载入真实视频后 QVideoWidget 的 sizeHint 会变成视频原始分辨率，
            # 把上半区撑大、列表被挤小。显示后下一拍强制重新分配一次。
            QTimer.singleShot(0, self._apply_split_ratio)

    def _apply_split_ratio(self) -> None:
        """上带（播放器|修改区+时间轴）压到约 300px，字幕表吃掉其余（≥62%）。"""
        if not self.split.isVisible():
            return
        h = max(self.split.height(), 600)
        top_min = self.split.widget(0).minimumSizeHint().height()
        t = max(min(int(h * 0.38), 320), top_min, 200)
        self.split.setSizes([t, max(h - t, 400)])

    def current(self) -> Optional[CueDocument]:
        return self.doc

    def push_undo(self) -> None:
        if not self.doc:
            return
        self._undo.append(self.doc.snapshot())
        del self._undo[:-60]
        self._redo.clear()

    # ------------------------------------------------------------ 表格动作
    def _on_text_changed(self, row: int, text: str) -> None:
        if not self.doc or not (0 <= row < len(self.doc.cues)):
            return
        cue = self.doc.cues[row]
        if cue.display_text == text:
            return
        self.push_undo()
        cue.text = text
        if cue.state not in ("review",):
            cue.state = "edited"
        self.table.update_row(row, cue)
        self.timeline.update()
        self.update_status()
        self.main.mark_dirty()
        self._edit_buf_dirty = False    # 该行已消费：缓冲重新干净

    def _update_pos_bar(self, row: int) -> None:
        """位置进度条：当前第几条 / 总条数，改到哪儿一眼可见。"""
        total = len(self.doc.cues) if self.doc else 0
        if total <= 0 or row < 0 or row >= total:
            self.pos_bar.setValue(0)
            self.pos_label.setText(f"— / {total}" if total else "0 / 0")
            return
        self.pos_bar.setValue(int((row + 1) * 100 / total))
        self.pos_label.setText(f"{row + 1} / {total}")

    def _on_select(self, row: int) -> None:
        self._editing_row = row
        self._update_pos_bar(row)
        if self.doc and 0 <= row < len(self.doc.cues):
            c = self.doc.cues[row]
            self.cur_row.setText(f"第 {row + 1} 条 · {human_time(c.start)} → {human_time(c.end)}"
                                 f" · {c.duration:.2f}s")
            # 编辑中不要把用户正在打的字冲掉（点击别行时由 focusOut 先落盘）。
            # 缓冲归属追踪：缓冲 dirty（有未落盘的输入）且焦点还在编辑框时，
            # 程序化换行绝不能覆盖它——否则「新行原文+我打的字」会被写进别的行
            if not self.edit_area.hasFocus() or (
                    not self._edit_buf_dirty and self._edit_buf_row == row):
                self.edit_area.setPlainText(c.display_text)
                self._edit_buf_row = row
                self._edit_buf_dirty = False
            if self._follow:
                self.timeline._sel = row
                self.timeline.update()

    def _apply_inline(self) -> None:
        row = self._editing_row
        if not self.doc or row < 0 or row >= len(self.doc.cues):
            return
        raw = self.edit_area.toPlainText().strip("\n")
        self._on_text_changed(row, raw)
        nxt = row + 1
        self._select_row(nxt)
        # 焦点还在编辑区 → _on_select 的防冲守卫会跳过刷新，
        # 这里显式把下一条装进来，让用户连续改。
        if self.doc and 0 <= nxt < len(self.doc.cues):
            self.edit_area.setPlainText(self.doc.cues[nxt].display_text)
            self._edit_buf_row = nxt
            self._edit_buf_dirty = False
            self.edit_area.setFocus()

    def _act(self, action: str, rows: Optional[List[int]] = None) -> None:
        if not self.doc:
            return
        rows = rows or self.table.selected_rows()
        if not rows:
            cur = self.table.currentRow()
            rows = [cur] if cur >= 0 else []
        if not rows and action not in ("insert", "close_gaps"):
            self._say("先选中字幕条目。", 2000)
            return
        # 行号不是身份：去重+排序。来自右键菜单/框选/快捷键的列表可能
        # 乱序带重复，后面按 rows[0] 定位锚点会落在意想不到的行上
        rows = sorted(set(int(r) for r in rows))
        doc = self.doc

        if action == "delete":
            self.push_undo()
            doc.remove(rows)
            self._after_struct(min(rows))
        elif action == "insert":
            self.push_undo()
            at = rows[0]
            anchor = doc.cues[at] if at < len(doc.cues) else (doc.cues[-1] if doc.cues else None)
            start = anchor.start if anchor else self.player.position()
            cue = Cue(start=start, end=start + 2.0, text="", original_text="", state="edited")
            doc.insert(at, cue)
            self._after_struct(at)
        elif action == "merge":
            self.push_undo()
            merged = doc.merge(rows)
            # merge 后 normalize_cues 会重排：用合并结果对象找回新行号，
            # 而不是拿 rows[0] 当锚点（不连续选择时必错行）
            anchor_row = doc.index_of(merged) if merged is not None else rows[0]
            self._after_struct(max(0, anchor_row))
        elif action == "split":
            self.push_undo()
            r = rows[0]
            cue = doc.cues[r]
            pos = self.player.position()
            if not (cue.start + 0.05 < pos < cue.end - 0.05):
                pos = cue.start + cue.duration / 2
            doc.split(r, pos)
            self._after_struct(r)
            self._say(f"已在 {sec_to_ts(pos)} 拆分", 2000)
        elif action.startswith("shift:"):
            d = float(action.split(":", 1)[1])
            self.push_undo()
            for r in rows:
                doc.cues[r].shift(d)
            # normalize_cues 内部会 sorted() 重排：按旧下标取 cue 会错行。
            # 先记住操作对象的 id，重排后用 index_of 找回。
            anchor = doc.cues[rows[0]]
            self._after_struct(doc.index_of(anchor))
        elif action.startswith("extend:"):
            d = float(action.split(":", 1)[1])
            self.push_undo()
            for r in rows:
                c = doc.cues[r]
                c.end = max(c.start + 0.2, c.end + d)
            anchor = doc.cues[rows[0]]
            normalize_cues(doc)
            self._after_struct(doc.index_of(anchor))
        elif action.startswith("set_time:"):
            _, r, col = action.split(":")
            r, col = int(r), int(col)
            item = self.table.item(r, col)
            v = ts_to_sec(item.text() if item else "")
            if v is None:
                self._say("时间格式无法识别，请用 00:00:12.340 或 12.34", 2500)
                self.table.render(doc.cues, r)
                return
            self.push_undo()
            c = doc.cues[r]
            if col == COL_S:
                c.start = min(v, c.end - 0.05)
            else:
                c.end = max(v, c.start + 0.05)
            # normalize_cues 会 sorted() 重排：改开始时间使行序变化时，
            # 旧下标 r 就指向别的行了——与 shift/extend 同款，按对象找回
            self._after_struct(doc.index_of(c))
        elif action in ("review", "confirmed"):
            self.push_undo()
            for r in rows:
                doc.cues[r].state = action
            # 状态切换不改行号：局部 update_row 替代全表 render——5000 行
            # 时全量重建 6 个 QTableWidgetItem/行只为改两行底色，可感卡顿
            for r in rows:
                self.table.update_row(r, doc.cues[r])
            self.timeline.update()
        elif action == "revert":
            self.push_undo()
            for r in rows:
                c = doc.cues[r]
                # original_text 为空 = 这行从未被改过（LLM/手动编辑都会先
                # 补 original）；此时直接赋值会把文本清成空串
                if c.original_text:
                    c.text = c.original_text
                c.state = "asr"
            for r in rows:
                self.table.update_row(r, doc.cues[r])
        elif action == "strip_punct":
            self.push_undo()
            import re as _re
            for r in rows:
                c = doc.cues[r]
                c.text = _re.sub(r"[，。！？、；：,.!?;:\s]+$", "", c.display_text).strip()
                c.state = "edited"
                self.table.update_row(r, c)
        elif action == "close_gaps":
            self.push_undo()
            mg = float(getattr(self.cfg, "gap_max", 0.5) or 0.5)
            touched, saved = doc.close_gaps(mg)
            self.table.render(doc.cues, rows[0] if rows else 0)
            self.timeline.update()
            self.main.mark_dirty()
            if touched:
                self._say(f"已衔接 {touched} 处空隙（共 {saved:.1f}s，"
                          f"阈值 ≤{mg:g}s），Ctrl+Z 可撤销", 4000)
            else:
                self._say(f"没有 ≤{mg:g}s 的空隙需要处理。", 2500)
        elif action == "copy":
            from PyQt5.QtWidgets import QApplication
            QApplication.clipboard().setText("\n".join(
                doc.cues[r].display_text for r in rows))
            self._say(f"已复制 {len(rows)} 条文本", 2000)
        elif action == "play_range":
            c = doc.cues[rows[0]]
            self.player.set_loop_a(c.start)
            self.player.set_loop_b(c.end)
            self.player.seek(c.start)
            self.player.play()
        elif action == "play_selection":
            self.player.seek(doc.cues[rows[0]].start)
            self.player.play()
        self.update_status()
        self.main.mark_dirty()

    def _nudge_sel(self, d: float) -> None:
        rows = self.table.selected_rows()
        if rows:
            self._act(f"shift:{d}", rows)

    def _after_struct(self, row: int) -> None:
        if not self.doc:
            return
        normalize_cues(self.doc)
        self.table.render(self.doc.cues, max(0, min(row, len(self.doc.cues) - 1)))
        self.timeline.update()
        self._filter(self.search.text())
        self._refresh_enabled()
        self._update_pos_bar(self.table.currentRow())

    # ------------------------------------------------------------ 撤销
    def _sync_edit_area_after_history(self) -> None:
        """撤销/重做后同步编辑框。

        撤销只重绘了表格：编辑框里还留着撤销前的旧文本，一失焦
        _apply_inline_silent 会把刚撤销掉的改动原样写回——表现为
        「Ctrl+Z 没用，再按一下编辑框还弹出旧字」。这里按当前选中行
        把编辑框刷回文档现值；行数变化（撤销删除/插入）时收拢选中。
        """
        row = self._editing_row
        if not self.doc or not (0 <= row < len(self.doc.cues)):
            row = max(0, min(row, len(self.doc.cues) - 1)) if self.doc else -1
            self._editing_row = row
        if 0 <= row < len(self.doc.cues):
            self.edit_area.setPlainText(self.doc.cues[row].display_text)
        else:
            self.edit_area.clear()
        self._update_pos_bar(row)

    def undo(self) -> None:
        if not self.doc or not self._undo:
            self._say("没有可撤销的操作。", 1500)
            return
        self._redo.append(self.doc.snapshot())
        self.doc.restore(self._undo.pop())
        self.table.render(self.doc.cues)
        self.timeline.update()
        self._sync_edit_area_after_history()
        self.main.mark_dirty()

    def redo(self) -> None:
        if not self.doc or not self._redo:
            return
        self._undo.append(self.doc.snapshot())
        self.doc.restore(self._redo.pop())
        self.table.render(self.doc.cues)
        self.timeline.update()
        self._sync_edit_area_after_history()
        self.main.mark_dirty()

    # ------------------------------------------------------------ 播放同步
    def _on_position(self, sec: float) -> None:
        self.timeline.set_position(sec)
        dur = self.player.duration() or (self.doc.duration if self.doc else 0)
        self.time_label.setText(f"{human_time(sec)} / {human_time(dur)}")
        if not self.doc or not self._follow:
            return
        if not self.player.playing:
            # 暂停时的 seek 多是用户主动选行/改字引起的，
            # 这时绝不能反过来把选中拽回播放头所在行。
            return
        cue = self.doc.at_time(sec)
        if cue is None:
            return
        row = self.doc.index_of(cue)
        if row >= 0 and row != self.table.currentRow():
            # 播放跟随换行前，必须先把编辑框里正在编辑的那行落盘：
            # 否则 _editing_row 被挪到新行后，用户一回编辑框按 Enter，
            # 上一条的半截文本就被写进这一行，覆盖原内容。
            if self.edit_area.hasFocus():
                self._apply_inline_silent()
            self.table.blockSignals(True)
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 5), QAbstractItemView.PositionAtCenter)
            self.table.blockSignals(False)
            self._editing_row = row
            # 刷新编辑缓冲：刚才若落了盘，缓冲已被消费（_edit_buf_dirty=False），
            # 刷成新行是安全的；若带着焦点但没落盘（_apply_inline_silent 因
            # 等值守卫跳过），缓冲本来就是新行内容，重装一遍也无害——
            # 无论如何，这里之后缓冲必须干净地属于新行
            if self.doc and 0 <= row < len(self.doc.cues):
                self.edit_area.setPlainText(self.doc.cues[row].display_text)
                self._edit_buf_row = row
                self._edit_buf_dirty = False

    def _on_duration(self, sec: float) -> None:
        if self.doc and sec > 0:
            self.doc.duration = sec
            self.timeline.duration = max(sec, self.doc.end_time)
            self.timeline.update()

    def _select_row(self, row: int) -> None:
        if self.doc and 0 <= row < len(self.doc.cues):
            self.table.jump(row)
            # jump 到"当前已经是"的行不会触发 currentCellChanged，
            # 必须直接同步一次，否则 _editing_row 会停留在旧行。
            self._on_select(row)
            self.player.seek(self.doc.cues[row].start)

    def _select_range(self, rows) -> None:
        """时间轴框选回传的行列表精确选行。

        曾按 (首,尾) 连续区间选：框选划过一段含空隙的区域时，空隙里用户
        没覆盖到的字幕也被一并选中，随后的删除/移动误伤。"""
        self.table.clearSelection()
        from PyQt5.QtCore import QItemSelectionModel as _ISM
        for r in rows:
            if 0 <= r < self.table.rowCount():
                idx = self.table.model().index(r, 0)
                self.table.selectionModel().select(
                    idx, _ISM.Select | _ISM.Rows)

    def _jump_to(self, row: int) -> None:
        if self.doc and 0 <= row < len(self.doc.cues):
            self.player.seek(self.doc.cues[row].start)
            self.player.play()

    def _step(self, d: int) -> None:
        if not self.doc or not self.doc.cues:
            return
        cur = self.table.currentRow()
        nxt = max(0, min(len(self.doc.cues) - 1, (cur if cur >= 0 else 0) + d))
        self._select_row(nxt)

    # ------------------------------------------------------------ 筛选
    def _toggle_filter(self) -> None:
        self.filter_row.setVisible(not self.filter_row.isVisible())
        if self.filter_row.isVisible():
            self.search.setFocus()

    def _focus_search(self) -> None:
        self.filter_row.setVisible(True)
        self.search.setFocus()
        self.search.selectAll()

    def _filter(self, text=None) -> None:
        # 兼容两种调用：textChanged 直连（防抖定时器无参触发）与显式传参
        if text is None or not isinstance(text, str):
            text = self.search.text()
        text = (text or "").strip()
        only_bad = self.chk_only_problem.isChecked()
        if not self.doc:
            return
        import re as _re
        rx = None
        if text and not text.startswith("#"):
            try:
                rx = _re.compile(text, _re.I)
            except _re.error:
                rx = _re.compile(_re.escape(text), _re.I)
        shown = 0
        # 只在"隐藏了行"的会话里批量包 setUpdatesEnabled：正常无过滤时
        # 不额外触发全视口重绘
        for r in range(self.table.rowCount()):
            if r >= len(self.doc.cues):
                break
            c = self.doc.cues[r]
            ok = True
            if text.startswith("#"):
                try:
                    ok = (int(text[1:]) - 1) == r
                except ValueError:
                    ok = True
            elif rx is not None:
                ok = bool(rx.search(c.display_text))
            if ok and only_bad:
                ok = (c.state == "review" or len(c.display_text) > 28
                      or (c.duration > 0 and len(c.display_text) / c.duration > 9)
                      or c.duration < 0.5)
            self.table.setRowHidden(r, not ok)
            shown += 1 if ok else 0
        total = self.table.rowCount()
        self.stat_label.setText(f"显示 {shown} / {total} 条" if (text or only_bad) else f"共 {total} 条")
        # "#12" 是跳转指令（placeholder 就这么承诺的）：直接定位并滚过去，
        # 不把其它行过滤掉后停在原地；跳完清空，恢复完整列表。
        m = _re.fullmatch(r"#(\d{1,7})", text) if text else None
        if m:
            n = int(m.group(1)) - 1
            if 0 <= n < total:
                self.search.blockSignals(True)
                self.search.clear()
                self.search.blockSignals(False)
                self._filter("")
                self._select_row(n)

    # ------------------------------------------------------------ 文件
    def _replace_dialog(self) -> None:
        """批量替换：搜索词来自当前筛选框（与筛选同源），支持正则。"""
        if not self.doc or not self.doc.cues:
            self._say("还没有字幕内容。", 2000)
            return
        pat = self.search.text().strip()
        if not pat or pat.startswith("#"):
            self._say("先在搜索框输入要找的内容（支持正则），再点替换。", 3500)
            return
        import re as _re
        try:
            rx = _re.compile(pat, _re.I)
        except _re.error as e:
            self._say(f"正则无效：{e}", 4000)
            return
        # 收集命中的行与匹配数，让用户先看规模再动手
        hits: List[Tuple[int, Cue]] = []
        total_matches = 0
        for i, c in enumerate(self.doc.cues):
            n = len(rx.findall(c.display_text))
            if n:
                hits.append((i, c))
                total_matches += n
        if not hits:
            self._say("没有匹配的行。", 2500)
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("批量替换")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(18, 14, 18, 12)
        info = BodyLabel(
            f"搜索词：{pat}\n命中 {len(hits)} 行 / {total_matches} 处（正则，忽略大小写）", dlg)
        info.setWordWrap(True)
        v.addWidget(info)
        row = QHBoxLayout()
        row.addWidget(BodyLabel("替换为（留空即删除匹配内容）", dlg))
        inp = LineEdit(dlg)
        inp.setPlaceholderText("可直接引用分组，如 $1 或 \\1")
        row.addWidget(inp, 1)
        v.addLayout(row)
        note = CaptionLabel("作用于全部命中行 · Ctrl+Z 可整体撤销", dlg)
        v.addWidget(note)
        btns = QHBoxLayout()
        btns.addStretch(1)
        b_cancel = PushButton("取消", dlg)
        b_ok = PrimaryPushButton("全部替换", dlg)
        btns.addWidget(b_cancel)
        btns.addWidget(b_ok)
        v.addLayout(btns)
        b_cancel.clicked.connect(dlg.reject)
        b_ok.clicked.connect(dlg.accept)
        if dlg.exec_() != QDialog.Accepted:
            return
        repl = inp.text()
        # $1 → \1：让分组引用写法跟常用编辑器一致
        repl = _re.sub(r"\$(\d+)", r"\\\1", repl)
        self.push_undo()
        changed = 0
        for i, c in hits:
            new = rx.sub(repl, c.display_text)
            if new == c.display_text:
                continue
            if not c.original_text:
                c.original_text = c.text
            c.text = new
            c.state = "edited"
            changed += 1
        if not changed:
            self._say("替换后内容没有变化。", 2500)
            return
        self.table.render(self.doc.cues, hits[0][0])
        self.timeline.update()
        self._filter(self.search.text())
        self.update_status()
        self.main.mark_dirty()
        self._say(f"已替换 {changed} 行（{total_matches} 处），Ctrl+Z 可撤销", 4000)

    def _open_project(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(self, "打开工程", self.cfg.last_dir or "",
                                            "字幕工程 (*.ssp *.json);;所有文件 (*)")
        if fp:
            self.main.load_project(fp)

    def _import_subtitle(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(self, "导入字幕/文稿", self.cfg.last_dir or "",
                                            "字幕与文本 (*.srt *.vtt *.ass *.lrc *.txt *.json *.md);;所有文件 (*)")
        if not fp:
            return
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError as e:
            self._say(f"读取失败：{e}", 4000)
            return
        cues, fmt = formats.import_text(text, fp)
        if not cues:
            self._say("没能从这个文件解析出字幕。", 3000)
            return
        if self.doc is None:
            self.doc = CueDocument(source_video="", path="")
            # 必须回灌主窗：main.doc 还指着 None 的话，Ctrl+S 会提示"没有内容"，
            # 导入的字幕根本存不下来（main_window 的其它入口都是成对设置的）。
            self.main.doc = self.doc
        if self.doc.cues:
            self.push_undo()
        self.doc.cues = cues
        normalize_cues(self.doc)
        self.doc.meta["imported_from"] = fp
        self.doc.meta["imported_format"] = fmt
        # 必须走 set_document 全套：空态下 split 是隐藏的，只手动 table.render
        # 会把表格渲染进不可见区域、hero 仍显示"拖入视频"，看着像导入没生效。
        self.set_document(self.doc, reset_history=False)
        self.main.mark_dirty()
        self._say(f"已按 {fmt} 格式导入 {len(cues)} 条字幕。", 4000)
        self.main.switch_to("editor")

    # ------------------------------------------------------------ 状态
    def update_status(self) -> None:
        if not self.doc:
            return
        st = self.doc.stats()
        rev = st["review"]
        msg = (f"共 <b>{st['count']}</b> 条 · {human_time(st['duration'])} · "
               f"均 {st['avg_cps']:.0f} 字/条 · {st['chars_per_sec']:.1f} 字/秒 · "
               f"已修正 {st['changed']}")
        if rev:
            msg += f" · {err_span(f'待复查 {rev}')}"
        self.status.setText(msg)
        self.doc_changed.emit()

    def _refresh_enabled(self) -> None:
        has = bool(self.doc and self.doc.cues)
        self.timeline.setEnabled(True)
        self.edit_area.setEnabled(has)
        self.b_play.setEnabled(self.player.duration() > 0)

    def _on_media_state(self, *_a) -> None:
        """媒体后端状态变化时刷新按钮可用性。

        _refresh_enabled 只在结构编辑路径被调：load 失败/换视频后
        播放按钮的可用态就一直停在旧值——有媒体时禁用、没媒体时
        点了没反应。接上 durationChanged/stateChanged 后自动跟上。"""
        try:
            self.b_play.setEnabled(self.player.duration() > 0)
        except RuntimeError:
            pass

    def _say(self, msg: str, ms: int = 3000) -> None:
        self.status.setText(msg)
        QTimer.singleShot(ms, self.update_status)

    # -------------------------------------------------------- 供外部调用
    def apply_llm_text(self, row: int, text: str) -> None:
        if not self.doc or not (0 <= row < len(self.doc.cues)):
            return
        c = self.doc.cues[row]
        # 模型漏输出某行时会传回空串：直接写就把那条字幕清空了，
        # 而且这条路径没有 undo 快照。空文本一律忽略。
        text = (text or "").strip()
        if not text or text == c.display_text:
            return
        # 纠错运行中用户可能正在手改这一行（state=edited 且编辑框聚焦）：
        # 批次结果按批次开始时的旧文本算，覆盖会把用户刚敲的字抹掉。
        # 交给用户改过的行不再动，标记 review 提醒去核对。
        if c.state == "edited" and self._editing_row == row:
            c.state = "review"
            self.table.update_row(row, c)
            return
        if c.original_text == "":
            c.original_text = c.text
        c.text = text
        c.state = "llm"
        self.table.mark_row_llm(row, text)
        # 只递增 token 不清 pixmap：LLM 流式逐条回调 5000 次时，
        # 色块层仍按 paintEvent 的 key 比对懒重建，不会 10Hz 重跑全量
        self.timeline._content_token += 1
        self.main.mark_dirty()

    def mark_all_llm(self) -> None:
        self.timeline.update()
        self.update_status()


class _TransportKeyFilter(QObject):
    """裸键快捷键过滤器：焦点在文本输入控件（含表格编辑态）时完全不拦截。"""

    NAV_KEYS = {Qt.Key_Space, Qt.Key_Left, Qt.Key_Right, Qt.Key_Comma, Qt.Key_Period,
                Qt.Key_BracketLeft, Qt.Key_BracketRight}

    def __init__(self, player, page):
        super().__init__(page)
        self.player = player
        self.page = page

    def install(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() != QEvent.KeyPress:
            return False
        if event.key() not in self.NAV_KEYS:
            return False
        w = QApplication.focusWidget()
        if w is None or not self.page.isAncestorOf(w) and w is not self.page:
            return False
        if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox)):
            return False
        if isinstance(w, CueTable) and w.state() == CueTable.EditingState:
            return False
        if isinstance(w, CueTable) and event.key() in (Qt.Key_Left, Qt.Key_Right):
            return False  # 让表格自己移动焦点
        if self.page.transport_key(event.key(), event.modifiers()):
            event.accept()
            return True
        return False
