"""导出页：多格式批量导出 + 导出前预检。"""

from __future__ import annotations

import os
from typing import List

from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox,
                            FluentIcon as FIF, InfoBar, InfoBarPosition, LineEdit,
                            PrimaryPushButton, PushButton, StrongBodyLabel, SubtitleLabel)
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QVBoxLayout, QWidget)

from ..core import formats
from ..core.config import Config
from .theme import open_path


class ExportInterface(QWidget):
    def __init__(self, cfg: Config, main):
        super().__init__()
        self.setObjectName("export")
        self.setWindowTitle("导出成品")
        self.cfg = cfg
        self.main = main
        self.setLayout(QVBoxLayout(self))
        lay = self.layout()
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(14)

        head = SubtitleLabel("导出成品字幕", self)
        lay.addWidget(head)

        body = QHBoxLayout()
        lay.addLayout(body, 1)

        # 左：格式选择
        left = CardWidget(self)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(18, 16, 18, 16)
        lv.addWidget(StrongBodyLabel("选择导出格式（可多选）", left))
        self.fmt_list = QListWidget(left)
        self.fmt_list.setSelectionMode(QListWidget.NoSelection)
        for key in formats.EXPORT_ORDER:
            spec = formats.FORMATS[key]
            it = QListWidgetItem(f"{spec.label}   —  {spec.desc}")
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if key in ("srt", "txt") else Qt.Unchecked)
            self.fmt_list.addItem(it)
        lv.addWidget(self.fmt_list, 1)
        row = QHBoxLayout()
        self.btn_all = PushButton("全选", left)
        self.btn_none = PushButton("全不选", left)
        self.btn_all.clicked.connect(lambda: self._check_all(Qt.Checked))
        self.btn_none.clicked.connect(lambda: self._check_all(Qt.Unchecked))
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_none)
        row.addStretch(1)
        lv.addLayout(row)
        body.addWidget(left, 3)

        # 右：选项 + 预检
        right = CardWidget(self)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(18, 16, 18, 16)
        rv.addWidget(StrongBodyLabel("输出设置", right))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.out_dir = LineEdit(right)
        self.out_dir.setPlaceholderText("留空则输出到视频所在文件夹")
        self.out_dir.setText(self.cfg.export_dir or "")
        pick = PushButton("浏览…", right)
        pick.clicked.connect(self._pick_dir)
        drow = QHBoxLayout()
        drow.addWidget(self.out_dir, 1)
        drow.addWidget(pick)
        form.addRow("输出目录", drow)

        self.name_tpl = LineEdit(right)
        self.name_tpl.setText("{name}")
        self.name_tpl.setToolTip("可用占位符：{name} 视频文件名，{lang} 语言，{ext} 由格式决定")
        form.addRow("文件名模板", self.name_tpl)

        self.enc = ComboBox(right)
        self.enc.addItems(["utf-8-sig（Windows 记事本友好）", "utf-8（推荐/播放器）", "gbk（老设备）"])
        self.enc.setCurrentIndex(["utf-8-sig", "utf-8", "gbk"].index(self.cfg.export_encoding)
                                 if self.cfg.export_encoding in ("utf-8-sig", "utf-8", "gbk") else 0)
        form.addRow("文本编码", self.enc)

        self.chk_video_name = CheckBox("以视频文件名命名（否则用当前工程名）", right)
        self.chk_video_name.setChecked(True)
        form.addRow("命名", self.chk_video_name)

        self.chk_burn = CheckBox("导出后自动打开所在文件夹", right)
        self.chk_burn.setChecked(True)
        form.addRow("", self.chk_burn)
        rv.addLayout(form)

        rv.addWidget(StrongBodyLabel("导出前检查", right))
        self.precheck = BodyLabel("—", right)
        self.precheck.setWordWrap(True)
        self.precheck.setTextFormat(Qt.RichText)
        rv.addWidget(self.precheck)
        rv.addStretch(1)
        body.addWidget(right, 2)

        # 底部按钮
        bar = QHBoxLayout()
        self.btn_preview = PushButton(FIF.VIEW, "预览 SRT 前 30 行", self)
        self.btn_preview.clicked.connect(self._preview)
        self.btn_export = PrimaryPushButton(FIF.SAVE_AS, "导 出", self)
        self.btn_export.setMinimumWidth(180)
        self.btn_export.clicked.connect(self._export)
        bar.addStretch(1)
        bar.addWidget(self.btn_preview)
        bar.addWidget(self.btn_export)
        lay.addLayout(bar)

        self._last_files: List[str] = []

    # ------------------------------------------------------------ 逻辑
    def refresh(self) -> None:
        doc = self.main.doc
        if not doc or not doc.cues:
            self.precheck.setText("当前没有字幕，先完成转写。")
            self.btn_export.setEnabled(False)
            return
        self.btn_export.setEnabled(True)
        st = doc.stats()
        problems = []
        too_long = [c for c in doc.cues if len(c.display_text.replace("\n", "")) > 28]
        too_short = [c for c in doc.cues if c.duration < 0.5]
        too_slow = [c for c in doc.cues
                    if c.duration > 0 and len(c.display_text) / c.duration > 9]
        overlap = sum(1 for i in range(len(doc.cues) - 1)
                      if doc.cues[i].end > doc.cues[i + 1].start + 0.01)
        empty = [i for i, c in enumerate(doc.cues) if not c.display_text.strip()]
        if too_long:
            problems.append(f"{len(too_long)} 条过长（>28 字）")
        if too_short:
            problems.append(f"{len(too_short)} 条 <0.5 秒")
        if too_slow:
            problems.append(f"{len(too_slow)} 条语速过快（>9 字/秒）")
        if overlap:
            problems.append(f"{overlap} 处时间重叠")
        if empty:
            problems.append(f"{len(empty)} 条内容为空")
        html = (f"共 <b>{st['count']}</b> 条 · 总时长 <b>{st['duration']:.1f}s</b> · "
                f"平均 <b>{st['avg_cps']:.1f}</b> 字/条 · 已修正 <b>{st['changed']}</b> 条")
        if problems:
            html += "<br><span style='color:#c42b1c'>⚠ " + "；".join(problems) + "</span>"
        else:
            html += "<br><span style='color:#1a7f37'>✓ 未发现明显问题</span>"
        self.precheck.setText(html)

    def _check_all(self, state) -> None:
        for i in range(self.fmt_list.count()):
            self.fmt_list.item(i).setCheckState(state)

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                             self.out_dir.text() or self.cfg.last_dir or "")
        if d:
            self.out_dir.setText(d)

    def _targets(self) -> List[str]:
        return [self.fmt_list.item(i).data(Qt.UserRole)
                for i in range(self.fmt_list.count())
                if self.fmt_list.item(i).checkState() == Qt.Checked]

    def _out_dir(self) -> str:
        d = self.out_dir.text().strip()
        if d:
            return d
        doc = self.main.doc
        if doc and doc.source_video:
            return os.path.dirname(doc.source_video)
        return self.cfg.last_dir or os.path.expanduser("~")

    def _base_name(self) -> str:
        doc = self.main.doc
        if self.chk_video_name.isChecked() and doc and doc.source_video:
            return os.path.splitext(os.path.basename(doc.source_video))[0]
        return (os.path.splitext(os.path.basename(doc.path))[0]
                if doc and doc.path else "subtitle")

    def _enc(self) -> str:
        return ["utf-8-sig", "utf-8", "gbk"][self.enc.currentIndex()]

    def _export(self) -> None:
        doc = self.main.doc
        if not doc or not doc.cues:
            InfoBar.warning("没有内容", "当前没有可导出的字幕。", parent=self.main,
                            position=InfoBarPosition.TOP, duration=2500)
            return
        keys = self._targets()
        if not keys:
            InfoBar.warning("未选格式", "请至少勾选一种导出格式。", parent=self.main,
                            position=InfoBarPosition.TOP, duration=2500)
            return
        out_dir = self._out_dir()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            InfoBar.error("目录不可写", str(e), parent=self.main,
                          position=InfoBarPosition.TOP, duration=4000)
            return
        base = self._base_name()
        enc = self._enc()
        written = []
        for key in keys:
            spec = formats.FORMATS[key]
            try:
                text = formats.export_text(doc, key)
            except Exception as e:
                InfoBar.error("导出失败", f"{spec.label}: {e}", parent=self.main,
                              position=InfoBarPosition.TOP, duration=4000)
                continue
            name = self.name_tpl.text().replace("{name}", base).replace(
                "{lang}", doc.language or "") + spec.ext
            fp = os.path.join(out_dir, name)
            try:
                with open(fp, "w", encoding=_safe_enc(enc, text), newline="") as f:
                    f.write(text)
                written.append(fp)
            except Exception as e:
                InfoBar.error("写入失败", f"{name}: {e}", parent=self.main,
                              position=InfoBarPosition.TOP, duration=4000)
        if written:
            self.cfg.export_encoding = enc
            self.cfg.export_dir = self.out_dir.text().strip()
            self.cfg.last_dir = out_dir
            self.cfg.save()
            self._last_files = written
            InfoBar.success("导出完成",
                            "\n".join(os.path.basename(p) for p in written),
                            parent=self.main, position=InfoBarPosition.TOP, duration=4500)
            if self.chk_burn.isChecked():
                open_path(os.path.dirname(written[0]))
            self.main.editor.status.setText(f"已导出 {len(written)} 个文件 → {out_dir}")

    def _preview(self) -> None:
        doc = self.main.doc
        if not doc or not doc.cues:
            return
        from .preview import TextPreviewDialog
        TextPreviewDialog("SRT 预览", formats.to_srt(doc)[:6000], self.main).exec_()

    def open_last(self) -> None:
        if self._last_files:
            open_path(os.path.dirname(self._last_files[0]))


def _safe_enc(enc: str, text: str) -> str:
    if enc == "gbk":
        try:
            text.encode("gbk")
            return "gbk"
        except UnicodeEncodeError:
            return "utf-8"
    return enc
