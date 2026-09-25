"""AI 纠错页：提示词、原稿、术语、批量进度，以及「对比/回滚」。"""

from __future__ import annotations

import os
from typing import Optional

from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox, FluentIcon as FIF,
                            InfoBar, InfoBarPosition, PrimaryPushButton, PushButton,
                            ScrollArea, SpinBox, StrongBodyLabel, SubtitleLabel, SwitchButton,
                            TextEdit)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout, QProgressBar,
                             QSizePolicy, QSplitter, QVBoxLayout, QWidget)

from ..core.config import Config
from ..core.i18n import S
from .safe_spin import SafeSpinBox
from .workers import FixWorker


class FixInterface(QWidget):
    def __init__(self, cfg: Config, main):
        super().__init__()
        self.setObjectName("fix")
        self.setWindowTitle(S("AI 纠错", "AI Fix"))
        self.cfg = cfg
        self.main = main
        self.worker: Optional[FixWorker] = None

        outer = QVBoxLayout(self)
        from .theme import CARD_MARGINS, PAGE_MARGINS, PAGE_SPACING, PRIMARY_MIN_W
        outer.setContentsMargins(*PAGE_MARGINS)
        outer.setSpacing(PAGE_SPACING)
        outer.addWidget(SubtitleLabel(S("让大模型只改错别字",
                                        "Let the LLM fix typos only"), self))

        split = QSplitter(Qt.Horizontal, self)
        outer.addWidget(split, 1)

        # ------------------------------------------------ 左：输入资料
        left = ScrollArea(split)
        left.setWidgetResizable(True)
        left.setStyleSheet("QScrollArea{background:transparent;border:none}")
        left.setMinimumWidth(240)     # 下限防挤没：splitter 收窄到 240 就停，
        #                              再窄由横向滚动条接管，内容不会被裁
        lh = QWidget(left)
        lh.setStyleSheet("background:transparent")
        lv = QVBoxLayout(lh)
        lv.setContentsMargins(0, 0, 12, 0)
        lv.setSpacing(12)

        card = CardWidget(lh)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(*CARD_MARGINS)
        cv.setSpacing(8)
        cv.addWidget(StrongBodyLabel(S("本轮指令（附加在系统提示词之后）",
                                       "This-run instructions (appended to the system prompt)"), card))
        self.extra = TextEdit(card)
        # "至少 3 行、多了内部滚动"代替 maxHeight 限死：限死后用户想多看
        # 两行指令都做不到；最小高度保住布局下限即可
        self.extra.setMinimumHeight(84)
        self.extra.setPlaceholderText(
            S("本轮额外要求，例如：本集嘉宾叫「老石谈芯」，把所有「老实谈新/老实谈心」都改成它；"
              "涉及「RTX 5060」不要写成「RTX5060 显卡」。",
              "Extra requirements for this run, e.g. the guest is 「Laoshi Tanxin」; fix all "
              "misspellings to it; keep 「RTX 5060」 as is, not 「RTX5060 card」."))
        cv.addWidget(self.extra)
        opts = QHBoxLayout()
        self.chk_keep = CheckBox(S("保留原始文本以便对比/回滚",
                                   "Keep original text for diff/rollback"), card)
        self.chk_keep.setChecked(True)
        opts.addWidget(self.chk_keep)
        self.chk_selection_only = CheckBox(S("只处理选中的条目",
                                             "Only selected cues"), card)
        opts.addWidget(self.chk_selection_only)
        opts.addStretch(1)
        cv.addLayout(opts)
        lv.addWidget(card)

        card2 = CardWidget(lh)
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(*CARD_MARGINS)
        c2.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(StrongBodyLabel(S("原始稿件（可选）", "Reference script (optional)"), card2))
        row.addStretch(1)
        for label, slot in ((S("导入…", "Import…"), self._load_script),
                            (S("粘贴", "Paste"), self._paste_script),
                            (S("清空", "Clear"), lambda: self.script.clear())):
            b = PushButton(label, card2)
            b.clicked.connect(slot)
            row.addWidget(b)
        c2.addLayout(row)
        self.script = TextEdit(card2)
        self.script.setMinimumHeight(160)
        self.script.setPlaceholderText(S("讲稿/产品文档/维基摘录。仅用于统一人名与术语。",
                                         "Script/product doc/wiki excerpt. Only to unify names & terms."))
        c2.addWidget(self.script)
        lv.addWidget(card2)

        card3 = CardWidget(lh)
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(*CARD_MARGINS)
        c3.setSpacing(6)
        r3 = QHBoxLayout()
        r3.addWidget(StrongBodyLabel(S("术语表（一行一个；支持 错=>对）",
                                       "Glossary (one per line; wrong=>right)"), card3))
        r3.addStretch(1)
        bg = PushButton(S("从选中字幕生成候选", "Harvest from cues"), card3)
        bg.clicked.connect(self._harvest_terms)
        r3.addWidget(bg)
        c3.addLayout(r3)
        self.glossary = TextEdit(card3)
        self.glossary.setMinimumHeight(120)
        self.glossary.setPlaceholderText("OpenChatCut\n达芬奇=>DaVinci Resolve\n剪映=>CapCut")
        c3.addWidget(self.glossary)
        lv.addWidget(card3)
        lv.addStretch(1)
        left.setWidget(lh)
        split.addWidget(left)

        # ------------------------------------------------ 右：执行与结果
        right = CardWidget(split)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(*CARD_MARGINS)
        rv.setSpacing(10)
        rv.addWidget(StrongBodyLabel(S("执行", "Run"), right))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # 当前接入点一行：name · model · base_url 可能很长。BodyLabel 默认
        # 不换行，minimumSizeHint 会随文本涨到 850~1150px，把整个右栏撑爆——
        # 窗口不宽时 QSplitter 只能把左栏挤没，两边内容都被裁（截断 bug）。
        # 设 Ignored 水平策略 + 允许换行：标签不再参与最小宽计算，超长时
        # 换行显示（Qt 会对过长的 URL 自动断行）。
        self.profile_label = BodyLabel("—", right)
        self.profile_label.setWordWrap(True)
        self.profile_label.setMinimumWidth(0)
        self.profile_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        form.addRow(S("当前接入点", "Active profile"), self.profile_label)
        self.btn_switch = PushButton(S("切换/编辑接入点", "Switch/edit profiles"), right)
        self.btn_switch.clicked.connect(lambda: self.main.switch_to("settings"))
        form.addRow("", self.btn_switch)
        self.batch = SafeSpinBox(right)
        self.batch.setRange(5, 200)
        self.batch.set_choices([(5, S("5 行", "5 rows")), (10, S("10 行", "10 rows")),
                                (15, S("15 行", "15 rows")),
                                (20, S("20 行", "20 rows")), (30, S("30 行（推荐）", "30 rows (recommended)")),
                                (40, S("40 行", "40 rows")), (50, S("50 行", "50 rows")), (80, S("80 行", "80 rows")),
                                (120, S("120 行", "120 rows")), (200, S("200 行", "200 rows"))])
        form.addRow(S("每批行数", "Rows per batch"), self.batch)
        self.conc = SafeSpinBox(right)
        self.conc.setRange(1, 8)
        self.conc.setValue(3)
        self.conc.set_choices([(1, S("1（最稳）", "1 (safest)")), (2, "2"), (3, S("3（推荐）", "3 (recommended)")),
                               (4, "4"), (6, "6"), (8, S("8（易限流）", "8 (rate-limits)") )])
        self.btn_apply_tp = PushButton(S("应用", "Apply"), right)
        self.btn_apply_tp.setToolTip(S("立即保存「每批行数 / 并发请求」，下一次点开始纠错就按新值执行；"
                                       "正在运行的批次不受影响。",
                                       "Saves rows-per-batch / concurrency now; the next run uses them. "
                                       "In-flight batches are unaffected."))
        self.btn_apply_tp.clicked.connect(self._apply_throughput)
        tp_row = QHBoxLayout()
        tp_row.setContentsMargins(0, 0, 0, 0)
        tp_row.setSpacing(8)
        tp_row.addWidget(self.conc, 1)
        tp_row.addWidget(self.btn_apply_tp)
        form.addRow(S("并发请求", "Concurrent requests"), tp_row)
        self.strict = SwitchButton(right)
        self.strict.setOnText(S("严格校验", "Strict"))
        self.strict.setOffText(S("严格校验", "Strict"))
        form.addRow(S("校验", "Validation"), self.strict)
        rv.addLayout(form)

        self.progress = QProgressBar(right)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        rv.addWidget(self.progress)
        self.log = TextEdit(right)
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        rv.addWidget(self.log, 1)

        bar = QHBoxLayout()
        self.btn_run = PrimaryPushButton(FIF.PLAY, S("开始纠错", "Start AI fix"), right)
        self.btn_run.setMinimumWidth(PRIMARY_MIN_W)
        self.btn_run.clicked.connect(self.run)
        self.btn_stop = PushButton(FIF.CLOSE, S("停止", "Stop"), right)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        bar.addWidget(self.btn_run)
        bar.addWidget(self.btn_stop)
        bar.addStretch(1)
        rv.addLayout(bar)

        bar2 = QHBoxLayout()
        self.btn_compare = PushButton(S("查看修改对比", "View changes"), right)
        self.btn_compare.clicked.connect(self._compare)
        self.btn_review = PushButton(S("逐条复查…", "Review one by one…"), right)
        self.btn_review.setToolTip(S("原文 → 修正 逐条对比，可单独采纳或拒绝每一条改动。",
                                     "Compare original → fixed cue by cue; accept or reject each change."))
        self.btn_review.clicked.connect(self._review_one_by_one)
        self.btn_revert = PushButton(S("全部回滚为原始文本", "Restore all originals"), right)
        self.btn_revert.clicked.connect(self._revert_all)
        self.btn_next_review = PushButton(S("跳到下一条待复查", "Jump to next review"), right)
        self.btn_next_review.clicked.connect(self._next_review)
        bar2.addWidget(self.btn_compare)
        bar2.addWidget(self.btn_review)
        bar2.addWidget(self.btn_revert)
        bar2.addWidget(self.btn_next_review)
        bar2.addStretch(1)
        rv.addLayout(bar2)
        # 底部说明：长文案 CaptionLabel 默认按整行不换行量最小宽（816px），
        # 同样会把右栏撑爆。开换行 + Ignored 水平策略，跟随栏宽自适应折行。
        self._hint = CaptionLabel(
            S("纠错只替换文本，绝不动时间轴。严格模式下若模型少给/多给行、或某行长度突变、"
              "疑似被翻译，都会自动重试；仍不合格的行会标红留在「待复查」。",
              "Fixing replaces text only, never timings. In strict mode wrong line counts, "
              "length jumps or suspected translation auto-retry; failing lines stay flagged "
              "as Review."), right)
        self._hint.setWordWrap(True)
        self._hint.setMinimumWidth(0)
        self._hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        rv.addWidget(self._hint)
        split.addWidget(right)
        split.setSizes([520, 520])
        split.setStretchFactor(0, 1)   # 富余宽度两侧均分，不偏向某一栏
        split.setStretchFactor(1, 1)

        QTimer.singleShot(0, self.sync_from_cfg)

    # ------------------------------------------------------------ 同步
    def sync_from_cfg(self) -> None:
        p = self.cfg.profile()
        self.profile_label.setText(f"{p.name} · {p.model} · {p.base_url}")
        self.batch.setValue(self.cfg.batch_size)
        self.conc.setValue(self.cfg.concurrency)
        self.strict.setChecked(self.cfg.strict_mode)
        self.glossary.setPlainText(self.cfg.glossary)
        self.script.setPlainText(self.cfg.reference_script)

    def refresh_silent(self) -> None:
        """设置页改完并发/批量后调用：只同步控件，不往日志里灌水。"""
        if self.worker is None:            # 运行中不动控件，避免和用户操作打架
            self.sync_from_cfg()

    def refresh(self) -> None:
        # 运行中不回灌控件：任务正按用户改过的参数跑，切页回来若用 cfg
        # 旧值覆盖控件，界面显示与后台实际运行参数就不一致了（假界面）
        if self.worker is None:
            self.sync_from_cfg()
        doc = self.main.doc
        if not doc or not doc.cues:
            self.btn_run.setEnabled(False)
            self.log.setPlainText(S("当前没有字幕。请先在「编辑」页导入视频并完成转写。",
                                    "No subtitles yet. Import a video in the Editor page and transcribe first."))
            return
        self.btn_run.setEnabled(self.worker is None)
        st = doc.stats()
        line = (S(f"就绪：{st['count']} 条 / {st['duration']:.0f}s / "
                  f"预计 {max(1, -(-st['count'] // self.batch.value()))} 批",
                  f"Ready: {st['count']} cues / {st['duration']:.0f}s / "
                  f"about {max(1, -(-st['count'] // self.batch.value()))} batches"))
        # 每次切页都会 refresh：内容没变就不重复 append，日志不再无限增长
        cur = self.log.toPlainText().splitlines()
        if not cur or cur[-1] != line:
            self.log.append(line)

    # ------------------------------------------------------------ 执行
    def _apply_throughput(self) -> None:
        """立即把并发/批量写入配置，不必等到点「开始纠错」。"""
        self.cfg.batch_size = self.batch.value()
        self.cfg.concurrency = self.conc.value()
        try:
            self.cfg.save()
        except Exception as e:
            InfoBar.error(S("保存失败", "Save failed"), str(e)[:120], parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        running = self.worker is not None
        InfoBar.success(
            S("已应用", "Applied"),
            S(f"每批 {self.cfg.batch_size} 行 · 并发 {self.cfg.concurrency}"
              + ("（正在运行的批次仍按原设置跑完）" if running else "，下次运行即生效"),
              f"{self.cfg.batch_size} rows/batch · {self.cfg.concurrency} concurrent"
              + (" (in-flight batches keep old settings)" if running
                 else "; takes effect on the next run")),
            parent=self, position=InfoBarPosition.TOP, duration=2600)

    def run(self) -> None:
        if self.worker is not None:
            return                      # 防重入：按钮被 InfoBar 遮挡时键盘还能触发
        doc = self.main.doc
        if not doc or not doc.cues:
            return
        # 把页面上的临时设置写回 cfg（本轮生效，同时持久化）
        self.cfg.batch_size = self.batch.value()
        self.cfg.concurrency = self.conc.value()
        self.cfg.strict_mode = self.strict.isChecked()
        self.cfg.glossary = self.glossary.toPlainText().strip()
        self.cfg.reference_script = self.script.toPlainText()
        self.cfg.keep_original = self.chk_keep.isChecked()
        self.cfg.save()

        rows = self.main.editor.table.selected_rows()
        if self.chk_selection_only.isChecked():
            if not rows:
                self.log.append(S("勾了「只处理选中」但没选中任何行——先在编辑页选行。",
                                  "「Selection only」 is checked but no rows are selected — select rows in the Editor page."))
                return
            cues = [doc.cues[r] for r in rows]
        else:
            cues = list(doc.cues)
        # 行号不是身份：纠错运行中用户可能在编辑页增删/排序（normalize 重排），
        # 之后批次的行号全部漂移，按行号回写会把 A 行修正写进 B 行。
        # 这里记下每条 cue 的稳定 id，回填时按 id 找回当前行。
        self._id_map = [c.id for c in cues]

        self.main.editor.push_undo()
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setValue(0)
        self.log.append(S(f"开始处理 {len(cues)} 条（{self.cfg.profile().model}）…",
                          f"Processing {len(cues)} cues ({self.cfg.profile().model})…"))
        # 记下本轮运行的文档身份：worker 流式回填按行号定位 self.main.doc，
        # 运行中用户若打开/新建另一个文档（main.doc 被换），旧 worker 的
        # 回调会把文本写进新文档对应行——数据串台。身份不符一律丢弃。
        self._run_doc = doc

        # 「本轮指令」是临时输入：作为参数传下去，绝不拼进 cfg.glossary。
        # 早先每点一次运行就往持久配置里追加一份，越滚越大还看不见。
        self.worker = FixWorker(self.cfg, cues, extra=self.extra.toPlainText())
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_cue.connect(self._on_cue)
        self.worker.sig_done.connect(self._on_done)
        self.worker.sig_failed.connect(self._on_failed)
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.log.append(S("正在停止…（当前批次返回后结束）",
                              "Stopping… (finishes after the current batch returns)"))

    def _on_progress(self, msg: str, pct: float) -> None:
        self.progress.setValue(int(max(0.0, pct) * 100))
        self.main.editor.status.setText(msg)

    def _on_cue(self, local_row: int, text: str) -> None:
        doc = self.main.doc
        if doc is not getattr(self, "_run_doc", None):
            return          # 文档已换：旧运行的回填不能写进新文档（串台）
        # 按 cue 稳定 id 找回当前行号：运行中用户增删/排序导致的行号漂移
        # 不再错行；id 消失（该条被删）则丢弃这条回填。
        if local_row >= len(getattr(self, "_id_map", [])):
            return
        cid = self._id_map[local_row]
        row = next((i for i, c in enumerate(doc.cues) if c.id == cid), -1)
        if doc and row >= 0:
            self.main.editor.apply_llm_text(row, text)

    def _on_done(self, res) -> None:
        self._finish()
        doc = self.main.doc
        if doc is not getattr(self, "_run_doc", None):
            # 文档运行中被换掉：本轮结果元数据不写、不重渲染新文档
            self.log.append(S("（文档已切换，本轮纠错结果不再回写）",
                              "(Document switched; this run's results are not written back)"))
            return
        if doc:
            doc.meta["llm_model"] = self.cfg.profile().model
            doc.meta["llm_fixed"] = res.changed
        self.progress.setValue(100)
        self.log.append(S(f"完成：修正 {res.changed} 条。",
                          f"Done: {res.changed} cue(s) fixed."))
        for f in res.failures[:12]:
            self.log.append("  ⚠ " + f)
        if len(res.failures) > 12:
            self.log.append(S(f"  … 还有 {len(res.failures) - 12} 条提示",
                              f"  … {len(res.failures) - 12} more notes"))
        self.main.editor.table.render(doc.cues if doc else [])
        self.main.editor.mark_all_llm()
        self.main.mark_dirty()
        if res.failures:
            InfoBar.warning(S("完成但有告警", "Done with warnings"),
                            S(f"{len(res.failures)} 处需注意，详见日志。",
                              f"{len(res.failures)} item(s) need attention; see the log."),
                            parent=self.main, position=InfoBarPosition.TOP, duration=5000)
        else:
            InfoBar.success(S("纠错完成", "AI fix done"),
                            S(f"共修正 {res.changed} 条字幕。",
                              f"{res.changed} cue(s) fixed in total."),
                            parent=self.main, position=InfoBarPosition.TOP, duration=4000)

    def _on_failed(self, msg: str) -> None:
        self._finish()
        self.log.append(S("✕ 失败：", "✕ Failed: ") + msg)
        # 未配 Key 的错误给「打开设置」跳转：文案与导航页实际名一致（「设置」，
        # 此前提示「模型设置」——页面上没有这个名字，用户找不到去处）
        nokey = "尚未配置 API Key" in msg or "没有配置 API Key" in msg
        w = InfoBar.error(S("纠错失败", "AI fix failed"), msg, parent=self.main,
                          position=InfoBarPosition.TOP, duration=-1 if nokey else 7000,
                          isClosable=True)
        if nokey:
            from qfluentwidgets import TransparentToolButton, FluentIcon as FIF
            btn = TransparentToolButton(FIF.SETTING, w)
            btn.setText(S("打开设置", "Open Settings"))
            btn.clicked.connect(self._open_settings)
            # InfoBar 的按钮区：拿 widget 布局末尾追加
            lay = w.view.layout()
            lay.addWidget(btn)

    def _open_settings(self) -> None:
        self.main.switch_to("settings")

    def _finish(self) -> None:
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        from .workers import reap
        reap(self.worker)
        self.worker = None

    # ------------------------------------------------------------ 辅助
    def _compare(self) -> None:
        doc = self.main.doc
        if not doc:
            return
        lines = []
        for i, c in enumerate(doc.cues, 1):
            if c.is_changed():
                lines.append(f"[{i}] {c.original_text}\n  → {c.text}")
        if not lines:
            InfoBar.info(S("无差异", "No changes"),
                         S("目前没有与原始文本不同的条目。",
                           "No cue differs from its original text."), parent=self.main,
                         position=InfoBarPosition.TOP, duration=2500)
            return
        from .preview import TextPreviewDialog
        TextPreviewDialog(S(f"修改对比（{len(lines)} 处）",
                            f"Changes ({len(lines)})"), "\n".join(lines),
                          self.main).show()

    def _review_one_by_one(self) -> None:
        """逐条复查：原文→修正 逐条对比，按条采纳/拒绝。

        按 cue 稳定 id 定位：纠错后用户增删/排序导致行号漂移也不会错位。
        拒绝的条目回滚为 original_text；未处理的条目保持修正现状。
        """
        from .diff_review import DiffReviewDialog
        doc = self.main.doc
        if not doc:
            return
        entries = DiffReviewDialog.collect(doc)
        if not entries:
            InfoBar.info(S("无差异", "No changes"),
                         S("目前没有与原始文本不同的条目。",
                           "No cue differs from its original text."), parent=self.main,
                         position=InfoBarPosition.TOP, duration=2500)
            return
        dlg = DiffReviewDialog(entries, self.main)
        dlg.exec_()
        actions = dlg.result_actions()
        rejects = [e for e in entries if actions.get(e["id"]) == "reject"]
        if not rejects:
            return
        self.main.editor.push_undo()
        by_id = {c.id: c for c in doc.cues}
        restored = 0
        for e in rejects:
            c = by_id.get(e["id"])
            if c is None or not c.original_text:
                continue
            c.text = c.original_text
            c.state = "asr"
            restored += 1
        if restored:
            self.main.editor.table.render(doc.cues)
            self.main.editor.mark_all_llm()
            self.main.mark_dirty()
            self.log.append(S(f"逐条复查：拒绝并回滚 {restored} 条，"
                              f"保留修正 {len(entries) - restored} 条。",
                              f"Review: rejected {restored}, kept {len(entries) - restored} fixes."))

    def _revert_all(self) -> None:
        # 用主窗的无动画确认框：裸 MessageBox 的淡出动画对象无父级、随时
        # 可能被 GC，done() 永不执行——表现是"点确认/取消都没反应"（与
        # 退出确认框同一坑，main_window._CloseAskBox 注释有完整分析）。
        from .main_window import _CloseAskBox
        box = _CloseAskBox(S("确认回滚", "Confirm rollback"),
                           S("把所有条目恢复为原始识别文本？\n（可用 Ctrl+Z 撤销）",
                             "Restore every cue to the original recognized text?\n(Ctrl+Z to undo)"),
                           self.main)
        if not box.exec_():
            return
        doc = self.main.doc
        if not doc:
            return
        self.main.editor.push_undo()
        for c in doc.cues:
            # original_text 为空 = 这行从未被改过；直接赋值会清空文本
            if c.original_text:
                c.text = c.original_text
            c.state = "asr"
        self.main.editor.table.render(doc.cues)
        self.main.editor.mark_all_llm()
        self.main.mark_dirty()

    def _next_review(self) -> None:
        doc = self.main.doc
        if not doc:
            return
        cur = self.main.editor.table.currentRow()
        for i in range(cur + 1, len(doc.cues)):
            if doc.cues[i].state == "review":
                self.main.switch_to("editor")
                self.main.editor.table.jump(i)
                return
        for i, c in enumerate(doc.cues):
            if c.state == "review":
                self.main.switch_to("editor")
                self.main.editor.table.jump(i)
                return
        InfoBar.info(S("没有待复查", "Nothing to review"),
                     S("所有条目都已处理完毕。", "Every cue has been handled."),
                     parent=self.main, position=InfoBarPosition.TOP, duration=2500)

    def _load_script(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(self, S("导入原始稿件", "Import reference script"),
                                            self.cfg.last_dir or "",
                                            S("文本", "Text") + " (*.txt *.md *.srt *.json);;"
                                            + S("所有文件", "All files") + " (*)")
        if not fp:
            return
        try:
            from .settings_page import _read_docx
            text = _read_docx(fp) if fp.lower().endswith(".docx") else open(
                fp, "r", encoding="utf-8", errors="ignore").read()
            self.script.setPlainText(text)
            self.cfg.last_dir = os.path.dirname(fp)
            self.cfg.save()
        except Exception as e:
            InfoBar.error(S("读取失败", "Read failed"), str(e), parent=self.main,
                          position=InfoBarPosition.TOP, duration=4000)

    def _paste_script(self) -> None:
        t = QGuiApplication.clipboard().text()
        if t:
            self.script.setPlainText(t)

    def _harvest_terms(self) -> None:
        """从当前字幕里挑出疑似专名/英文词，作为术语表候选。"""
        import re
        doc = self.main.doc
        if not doc:
            return
        text = "\n".join(c.display_text for c in doc.cues)
        cands = {}
        for w in re.findall(r"[A-Za-z][A-Za-z0-9+\-_.]{2,}", text):
            cands[w] = cands.get(w, 0) + 1
        for w in re.findall(r"[一-鿿]{2,6}(?=公司|科技|平台|系统|模型|协议|芯片|显卡)", text):
            cands[w] = cands.get(w, 0) + 1
        top = sorted(cands.items(), key=lambda kv: -kv[1])[:60]
        # 合并而非覆盖：用户手填的「错=>对」映射是心血，一键冲掉且无撤销
        # 是数据丢失。已有行原样保留，新候选追加到末尾（重复词去重）。
        existing = [ln.strip() for ln in self.glossary.toPlainText().splitlines()
                    if ln.strip()]
        seen = {ln.split("=>")[0].strip() for ln in existing if "=>" in ln}
        seen |= set(existing)
        added = [k for k, _ in top if k not in seen]
        merged = existing + added
        self.glossary.setPlainText("\n".join(merged))
        InfoBar.success(S("已合并", "Merged"),
                        S(f"保留已有 {len(existing)} 行，新增 {len(added)} 个候选术语。",
                          f"Kept {len(existing)} existing line(s), added {len(added)} candidate term(s)."),
                        parent=self.main, position=InfoBarPosition.TOP, duration=3500)
