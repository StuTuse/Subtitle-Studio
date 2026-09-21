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
                             QSplitter, QVBoxLayout, QWidget)

from ..core import llm
from ..core.config import Config
from .workers import FixWorker


class FixInterface(QWidget):
    def __init__(self, cfg: Config, main):
        super().__init__()
        self.setObjectName("fix")
        self.setWindowTitle("AI 纠错")
        self.cfg = cfg
        self.main = main
        self.worker: Optional[FixWorker] = None
        self._live: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 18, 28, 16)
        outer.setSpacing(12)
        outer.addWidget(SubtitleLabel("让大模型只改错别字", self))

        split = QSplitter(Qt.Horizontal, self)
        outer.addWidget(split, 1)

        # ------------------------------------------------ 左：输入资料
        left = ScrollArea(split)
        left.setWidgetResizable(True)
        left.setStyleSheet("QScrollArea{background:transparent;border:none}")
        lh = QWidget(left)
        lh.setStyleSheet("background:transparent")
        lv = QVBoxLayout(lh)
        lv.setContentsMargins(0, 0, 12, 0)
        lv.setSpacing(12)

        card = CardWidget(lh)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 14, 18, 14)
        cv.setSpacing(8)
        cv.addWidget(StrongBodyLabel("本轮指令（附加在系统提示词之后）", card))
        self.extra = TextEdit(card)
        self.extra.setMaximumHeight(96)
        self.extra.setPlaceholderText(
            "本轮额外要求，例如：本集嘉宾叫「老石谈芯」，把所有「老实谈新/老实谈心」都改成它；"
            "涉及「RTX 5060」不要写成「RTX5060 显卡」。")
        cv.addWidget(self.extra)
        opts = QHBoxLayout()
        self.chk_keep = CheckBox("保留原始文本以便对比/回滚", card)
        self.chk_keep.setChecked(True)
        opts.addWidget(self.chk_keep)
        self.chk_selection_only = CheckBox("只处理选中的条目", card)
        opts.addWidget(self.chk_selection_only)
        opts.addStretch(1)
        cv.addLayout(opts)
        lv.addWidget(card)

        card2 = CardWidget(lh)
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(18, 14, 18, 14)
        c2.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(StrongBodyLabel("原始稿件（可选）", card2))
        row.addStretch(1)
        for label, slot in (("导入…", self._load_script), ("粘贴", self._paste_script),
                            ("清空", lambda: self.script.clear())):
            b = PushButton(label, card2)
            b.clicked.connect(slot)
            row.addWidget(b)
        c2.addLayout(row)
        self.script = TextEdit(card2)
        self.script.setMinimumHeight(160)
        self.script.setPlaceholderText("讲稿/产品文档/维基摘录。仅用于统一人名与术语。")
        c2.addWidget(self.script)
        lv.addWidget(card2)

        card3 = CardWidget(lh)
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(18, 14, 18, 14)
        c3.setSpacing(6)
        r3 = QHBoxLayout()
        r3.addWidget(StrongBodyLabel("术语表（一行一个；支持 错=>对）", card3))
        r3.addStretch(1)
        bg = PushButton("从选中字幕生成候选", card3)
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
        rv.setContentsMargins(18, 14, 18, 14)
        rv.setSpacing(10)
        rv.addWidget(StrongBodyLabel("执行", right))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.profile_label = BodyLabel("—", right)
        form.addRow("当前接入点", self.profile_label)
        self.btn_switch = PushButton("切换/编辑接入点", right)
        self.btn_switch.clicked.connect(lambda: self.main.switch_to("settings"))
        form.addRow("", self.btn_switch)
        self.batch = SpinBox(right)
        self.batch.setRange(5, 200)
        form.addRow("每批行数", self.batch)
        self.conc = SpinBox(right)
        self.conc.setRange(1, 8)
        self.conc.setValue(3)
        self.btn_apply_tp = PushButton("应用", right)
        self.btn_apply_tp.setToolTip("立即保存「每批行数 / 并发请求」，下一次点开始纠错就按新值执行；"
                                     "正在运行的批次不受影响。")
        self.btn_apply_tp.clicked.connect(self._apply_throughput)
        tp_row = QHBoxLayout()
        tp_row.setContentsMargins(0, 0, 0, 0)
        tp_row.setSpacing(8)
        tp_row.addWidget(self.conc, 1)
        tp_row.addWidget(self.btn_apply_tp)
        form.addRow("并发请求", tp_row)
        self.strict = SwitchButton(right)
        self.strict.setOnText("严格校验")
        self.strict.setOffText("严格校验")
        form.addRow("校验", self.strict)
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
        self.btn_run = PrimaryPushButton(FIF.PLAY, "开始纠错", right)
        self.btn_run.setMinimumWidth(150)
        self.btn_run.clicked.connect(self.run)
        self.btn_stop = PushButton(FIF.CLOSE, "停止", right)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        bar.addWidget(self.btn_run)
        bar.addWidget(self.btn_stop)
        bar.addStretch(1)
        rv.addLayout(bar)

        bar2 = QHBoxLayout()
        self.btn_compare = PushButton("查看修改对比", right)
        self.btn_compare.clicked.connect(self._compare)
        self.btn_revert = PushButton("全部回滚为原始文本", right)
        self.btn_revert.clicked.connect(self._revert_all)
        self.btn_next_review = PushButton("跳到下一条待复查", right)
        self.btn_next_review.clicked.connect(self._next_review)
        bar2.addWidget(self.btn_compare)
        bar2.addWidget(self.btn_revert)
        bar2.addWidget(self.btn_next_review)
        bar2.addStretch(1)
        rv.addLayout(bar2)
        rv.addWidget(CaptionLabel(
            "纠错只替换文本，绝不动时间轴。严格模式下若模型少给/多给行、或某行长度突变、"
            "疑似被翻译，都会自动重试；仍不合格的行会标红留在「待复查」。", right))
        split.addWidget(right)
        split.setSizes([520, 520])

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
        self.sync_from_cfg()
        doc = self.main.doc
        if not doc or not doc.cues:
            self.btn_run.setEnabled(False)
            self.log.setPlainText("当前没有字幕。请先在「编辑」页导入视频并完成转写。")
            return
        self.btn_run.setEnabled(self.worker is None)
        st = doc.stats()
        self.log.append(f"就绪：{st['count']} 条 / {st['duration']:.0f}s / "
                        f"预计 {max(1, -(-st['count'] // self.batch.value()))} 批")

    # ------------------------------------------------------------ 执行
    def _apply_throughput(self) -> None:
        """立即把并发/批量写入配置，不必等到点「开始纠错」。"""
        self.cfg.batch_size = self.batch.value()
        self.cfg.concurrency = self.conc.value()
        try:
            self.cfg.save()
        except Exception as e:
            InfoBar.error("保存失败", str(e)[:120], parent=self,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        running = self.worker is not None
        InfoBar.success(
            "已应用",
            f"每批 {self.cfg.batch_size} 行 · 并发 {self.cfg.concurrency}"
            + ("（正在运行的批次仍按原设置跑完）" if running else "，下次运行即生效"),
            parent=self, position=InfoBarPosition.TOP, duration=2600)

    def run(self) -> None:
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
        if self.extra.toPlainText().strip():
            self.cfg.glossary = (self.cfg.glossary + "\n【本轮补充】"
                                 + self.extra.toPlainText().strip()).strip()
        self.cfg.save()

        rows = self.main.editor.table.selected_rows()
        if self.chk_selection_only.isChecked() and rows:
            cues = [doc.cues[r] for r in rows]
            self._row_map = rows
        else:
            cues = list(doc.cues)
            self._row_map = list(range(len(cues)))

        self.main.editor.push_undo()
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setValue(0)
        self.log.append(f"开始处理 {len(cues)} 条（{self.cfg.profile().model}）…")

        self.worker = FixWorker(self.cfg, cues)
        self.worker.sig_progress.connect(self._on_progress)
        self.worker.sig_cue.connect(self._on_cue)
        self.worker.sig_done.connect(self._on_done)
        self.worker.sig_failed.connect(self._on_failed)
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.log.append("正在停止…（当前批次返回后结束）")

    def _on_progress(self, msg: str, pct: float) -> None:
        self.progress.setValue(int(max(0.0, pct) * 100))
        self.main.editor.status.setText(msg)

    def _on_cue(self, local_row: int, text: str) -> None:
        row = self._row_map[local_row] if local_row < len(self._row_map) else local_row
        doc = self.main.doc
        if doc and 0 <= row < len(doc.cues):
            self.main.editor.apply_llm_text(row, text)

    def _on_done(self, res) -> None:
        self._finish()
        doc = self.main.doc
        if doc:
            doc.meta["llm_model"] = self.cfg.profile().model
            doc.meta["llm_fixed"] = res.changed
        self.progress.setValue(100)
        self.log.append(f"完成：修正 {res.changed} 条。")
        for f in res.failures[:12]:
            self.log.append("  ⚠ " + f)
        if len(res.failures) > 12:
            self.log.append(f"  … 还有 {len(res.failures) - 12} 条提示")
        self.main.editor.table.render(doc.cues if doc else [])
        self.main.editor.mark_all_llm()
        self.main.mark_dirty()
        if res.failures:
            InfoBar.warning("完成但有告警", f"{len(res.failures)} 处需注意，详见日志。",
                            parent=self.main, position=InfoBarPosition.TOP, duration=5000)
        else:
            InfoBar.success("纠错完成", f"共修正 {res.changed} 条字幕。", parent=self.main,
                            position=InfoBarPosition.TOP, duration=4000)

    def _on_failed(self, msg: str) -> None:
        self._finish()
        self.log.append("✕ 失败：" + msg)
        InfoBar.error("纠错失败", msg, parent=self.main,
                      position=InfoBarPosition.TOP, duration=7000)

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
            InfoBar.info("无差异", "目前没有与原始文本不同的条目。", parent=self.main,
                         position=InfoBarPosition.TOP, duration=2500)
            return
        from .preview import TextPreviewDialog
        TextPreviewDialog(f"修改对比（{len(lines)} 处）", "\n".join(lines),
                          self.main).show()

    def _revert_all(self) -> None:
        from qfluentwidgets import MessageBox
        box = MessageBox("确认回滚", "把所有条目恢复为原始识别文本？\n（可用 Ctrl+Z 撤销）",
                         self.main)
        if not box.exec_():
            return
        doc = self.main.doc
        if not doc:
            return
        self.main.editor.push_undo()
        for c in doc.cues:
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
                self.main.editor.switch_page = None
                self.main.switch_to("editor")
                self.main.editor.table.jump(i)
                return
        for i, c in enumerate(doc.cues):
            if c.state == "review":
                self.main.switch_to("editor")
                self.main.editor.table.jump(i)
                return
        InfoBar.info("没有待复查", "所有条目都已处理完毕。", parent=self.main,
                     position=InfoBarPosition.TOP, duration=2500)

    def _load_script(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(self, "导入原始稿件", self.cfg.last_dir or "",
                                            "文本 (*.txt *.md *.srt *.json);;所有文件 (*)")
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
            InfoBar.error("读取失败", str(e), parent=self.main,
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
        self.glossary.setPlainText("\n".join(k for k, _ in top))
        InfoBar.success("已生成", f"从字幕中提取 {len(top)} 个候选术语，请删掉不需要的。",
                        parent=self.main, position=InfoBarPosition.TOP, duration=3500)
