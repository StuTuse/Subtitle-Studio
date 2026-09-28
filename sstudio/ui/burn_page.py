# -*- coding: utf-8 -*-
"""视频合成页：把调整好的字幕烧录进原视频，输出画质无损的新视频。

布局（左样式编辑 3 : 右预览与渲染 2）：
* 样式编辑：预设下拉（存/删/另存）+ 字体基础（字体/字号/颜色/粗斜下划）
  + 位置（9 宫格 + 边距）+ 阴影特效（偏移/模糊/颜色）
* 预览与渲染：视频概况卡、ASS 预览（前几行）、「开始合成」+ 进度条

单条级样式：入口在编辑页字幕表右键菜单「本条样式…」（editor_page 调
BurnPage.edit_cue_style），弹出同款样式编辑器但作用于该 cue 的覆盖
字段；渲染时字段级合并进 ASS 行内标签。
"""

from __future__ import annotations

import os
from typing import Dict, Optional

from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, ComboBox,
                            FluentIcon as FIF, InfoBar, InfoBarPosition,
                            LineEdit, PrimaryPushButton, PushButton,
                            StrongBodyLabel, SubtitleLabel, SwitchButton)

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QColorDialog, QFileDialog, QFormLayout,
                             QHBoxLayout, QGridLayout, QSpinBox, QVBoxLayout,
                             QWidget)

from ..core import render_video
from ..core.config import Config
from ..core.i18n import S
from ..core.style_preset import (COMMON_FONTS, CueStyle, DEFAULT_STYLE,
                                 build_burn_ass, override_tags)
from ..core.model import Cue
from .safe_spin import SafeSpinBox, SafeDoubleSpinBox
from .theme import CARD_MARGINS

_QUALITY_LABELS = [
    ("near_lossless", S("视觉无损（推荐）", "Visually lossless (recommended)")),
    ("lossless", S("完全无损（体积大）", "Mathematically lossless (large)")),
    ("balanced", S("均衡（文件小）", "Balanced (smaller)")),
    ("h264_nvenc", S("NVIDIA 硬件加速", "NVIDIA NVENC hardware")),
    ("h264_amf", S("AMD 硬件加速", "AMD AMF hardware")),
    ("h264_qsv", S("Intel 硬件加速", "Intel QSV hardware")),
]

_ALIGN_GRID = [(7, 8, 9), (4, 5, 6), (1, 2, 3)]   # ASS 数字键盘语义


class StyleEditor(QWidget):
    """样式编辑器组件：全局样式与单条覆盖共用同一套控件。"""

    changed = None                              # 构造后由调用方接（避免循环引用）

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self.compact = compact
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(12)

        # ---- 字体
        self.font = ComboBox(self)
        self.font.addItems(COMMON_FONTS)
        form.addRow(S("字体", "Font"), self.font)
        self.size = SafeSpinBox(self)
        self.size.setRange(10, 400)
        self.size.setValue(int(DEFAULT_STYLE.size))
        form.addRow(S("字号", "Size"), self.size)
        self.color_hex = "#FFFFFF"
        self.btn_color = PushButton(self)
        self.btn_color.setText(S("主色 #FFFFFF", "Primary #FFFFFF"))
        self.btn_color.clicked.connect(self._pick_color)
        form.addRow(S("颜色", "Color"), self.btn_color)

        # ---- 样式开关
        sw = QHBoxLayout()
        self.bold = SwitchButton(self)
        self.bold.setOnText(S("粗体", "Bold"))
        self.bold.setOffText(S("粗体", "Bold"))
        sw.addWidget(self.bold)
        self.italic = SwitchButton(self)
        self.italic.setOnText(S("斜体", "Italic"))
        self.italic.setOffText(S("斜体", "Italic"))
        sw.addWidget(self.italic)
        self.underline = SwitchButton(self)
        self.underline.setOnText(S("下划线", "Underline"))
        self.underline.setOffText(S("下划线", "Underline"))
        sw.addWidget(self.underline)
        sw.addStretch(1)
        form.addRow(S("字形", "Style"), sw)

        # ---- 位置
        self.align = ComboBox(self)
        for label in (S("底部居中", "Bottom center"), S("底部居左", "Bottom left"),
                      S("底部居右", "Bottom right"), S("顶部居中", "Top center"),
                      S("顶部居左", "Top left"), S("顶部居右", "Top right"),
                      S("中部居中", "Middle center")):
            self.align.addItem(label)
        self._align_values = [2, 1, 3, 8, 7, 9, 5]
        form.addRow(S("位置", "Position"), self.align)
        self.margin_v = SafeSpinBox(self)
        self.margin_v.setRange(0, 600)
        self.margin_v.setValue(int(DEFAULT_STYLE.margin_v))
        form.addRow(S("边距（底/顶）", "Margin (bottom/top)"), self.margin_v)
        self.margin_h = SafeSpinBox(self)
        self.margin_h.setRange(0, 600)
        self.margin_h.setValue(int(DEFAULT_STYLE.margin_h))
        form.addRow(S("边距（左右）", "Margin (sides)"), self.margin_h)

        # ---- 阴影特效
        self.shadow = SafeDoubleSpinBox(self)
        self.shadow.setRange(0, 20)
        self.shadow.setSingleStep(0.5)
        self.shadow.setDecimals(1)
        self.shadow.setValue(float(DEFAULT_STYLE.shadow))
        form.addRow(S("阴影偏移量", "Shadow offset"), self.shadow)
        self.shadow_blur = SafeDoubleSpinBox(self)
        self.shadow_blur.setRange(0, 30)
        self.shadow_blur.setSingleStep(0.5)
        self.shadow_blur.setDecimals(1)
        form.addRow(S("阴影模糊量", "Shadow blur"), self.shadow_blur)
        self.shadow_hex = "#000000"
        self.btn_shadow_color = PushButton(self)
        self.btn_shadow_color.setText(S("阴影色 #000000", "Shadow #000000"))
        self.btn_shadow_color.clicked.connect(self._pick_shadow_color)
        form.addRow(S("阴影颜色", "Shadow color"), self.btn_shadow_color)

        # ---- 描边
        self.outline = SafeDoubleSpinBox(self)
        self.outline.setRange(0, 20)
        self.outline.setSingleStep(0.5)
        self.outline.setDecimals(1)
        self.outline.setValue(float(DEFAULT_STYLE.outline))
        form.addRow(S("描边宽度", "Outline width"), self.outline)
        self.outline_hex = "#000000"
        self.btn_outline_color = PushButton(self)
        self.btn_outline_color.setText(S("描边色 #000000", "Outline #000000"))
        self.btn_outline_color.clicked.connect(self._pick_outline_color)
        form.addRow(S("描边颜色", "Outline color"), self.btn_outline_color)

        v.addLayout(form)
        for w in (self.font, self.size, self.align, self.margin_v,
                  self.margin_h, self.shadow, self.shadow_blur,
                  self.outline):
            w.setMaximumWidth(430)
            if hasattr(w, "setMinimumWidth"):
                w.setMinimumWidth(240)
        # 任何控件变化都通知宿主
        for sig in (self.font.currentTextChanged, self.size.valueChanged,
                    self.align.currentIndexChanged, self.margin_v.valueChanged,
                    self.margin_h.valueChanged, self.shadow.valueChanged,
                    self.shadow_blur.valueChanged, self.outline.valueChanged,
                    self.bold.checkedChanged, self.italic.checkedChanged,
                    self.underline.checkedChanged):
            sig.connect(self._emit_changed)

    # ------------------------------------------------------------ 事件
    def _emit_changed(self, *_a) -> None:
        if self.changed is not None:
            self.changed()

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.color_hex), self,
                                  S("选择字幕颜色", "Pick subtitle color"))
        if c.isValid():
            self.color_hex = c.name()
            self.btn_color.setText(f"{S('主色', 'Primary')} {self.color_hex}")
            self._emit_changed()

    def _pick_shadow_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.shadow_hex), self,
                                  S("选择阴影颜色", "Pick shadow color"))
        if c.isValid():
            self.shadow_hex = c.name()
            self.btn_shadow_color.setText(
                f"{S('阴影色', 'Shadow')} {self.shadow_hex}")
            self._emit_changed()

    def _pick_outline_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.outline_hex), self,
                                  S("选择描边颜色", "Pick outline color"))
        if c.isValid():
            self.outline_hex = c.name()
            self.btn_outline_color.setText(
                f"{S('描边色', 'Outline')} {self.outline_hex}")
            self._emit_changed()

    # ------------------------------------------------------------ 读/写
    def collect(self) -> CueStyle:
        """从控件读出完整样式（全局编辑用：所有字段都落值）。"""
        return CueStyle(
            font=self.font.currentText().strip() or "Microsoft YaHei",
            size=self.size.value(), color=self.color_hex, alpha=0.0,
            bold=self.bold.isChecked(), italic=self.italic.isChecked(),
            underline=self.underline.isChecked(),
            outline=self.outline.value(), outline_color=self.outline_hex,
            shadow=self.shadow.value(), shadow_color=self.shadow_hex,
            shadow_blur=self.shadow_blur.value(),
            align=self._align_values[max(0, self.align.currentIndex())],
            margin_v=self.margin_v.value(), margin_h=self.margin_h.value(),
        )

    def apply(self, st: CueStyle) -> None:
        """把控件刷成样式值（单条覆盖只刷非 None 字段）。"""
        if st.font:
            self.font.setCurrentText(st.font)
        if st.size is not None:
            self.size.setValue(int(st.size))
        if st.color:
            self.color_hex = st.color
            self.btn_color.setText(f"{S('主色', 'Primary')} {st.color}")
        if st.bold is not None:
            self.bold.setChecked(st.bold)
        if st.italic is not None:
            self.italic.setChecked(st.italic)
        if st.underline is not None:
            self.underline.setChecked(st.underline)
        if st.outline is not None:
            self.outline.setValue(float(st.outline))
        if st.outline_color:
            self.outline_hex = st.outline_color
            self.btn_outline_color.setText(f"{S('描边色', 'Outline')} {st.outline_color}")
        if st.shadow is not None:
            self.shadow.setValue(float(st.shadow))
        if st.shadow_color:
            self.shadow_hex = st.shadow_color
            self.btn_shadow_color.setText(f"{S('阴影色', 'Shadow')} {st.shadow_color}")
        if st.shadow_blur is not None:
            self.shadow_blur.setValue(float(st.shadow_blur))
        if st.align is not None and st.align in self._align_values:
            self.align.setCurrentIndex(self._align_values.index(st.align))
        if st.margin_v is not None:
            self.margin_v.setValue(int(st.margin_v))
        if st.margin_h is not None:
            self.margin_h.setValue(int(st.margin_h))

    def collect_override(self) -> CueStyle:
        """单条覆盖收集：只收与全局不同的字段由宿主对比得出，这里收全量，
        宿主负责 diff。"""
        return self.collect()


class BurnInterface(QWidget):
    """视频合成主页面。"""

    def __init__(self, cfg: Config, main):
        super().__init__()
        self.setObjectName("burn")
        self.setWindowTitle(S("视频合成", "Video compose"))
        self.cfg = cfg
        self.main = main
        self._render_worker = None
        self._cancelled = [False]
        self._video_info = {}
        self._per_cue: Dict[str, CueStyle] = {}    # cue.id → 覆盖样式

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        twin = QHBoxLayout()
        twin.setContentsMargins(28, 18, 32, 28)
        twin.setSpacing(14)
        outer.addLayout(twin, 1)

        # ------------------------------------------------ 左：样式编辑
        left = CardWidget(self)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(*CARD_MARGINS)
        lv.setSpacing(10)
        lv.addWidget(StrongBodyLabel(S("字幕样式", "Subtitle style"), left))

        prow = QHBoxLayout()
        self.preset = ComboBox(left)
        self._fill_presets()
        prow.addWidget(self.preset, 1)
        self.btn_preset_apply = PushButton(FIF.ACCEPT, S("应用", "Apply"), left)
        self.btn_preset_apply.clicked.connect(self._apply_preset)
        prow.addWidget(self.btn_preset_apply)
        self.btn_preset_save = PushButton(FIF.SAVE, S("存为预设", "Save preset"), left)
        self.btn_preset_save.clicked.connect(self._save_preset)
        prow.addWidget(self.btn_preset_save)
        self.btn_preset_del = PushButton(FIF.DELETE, S("删", "Del"), left)
        self.btn_preset_del.clicked.connect(self._del_preset)
        prow.addWidget(self.btn_preset_del)
        lv.addLayout(prow)

        self.editor = StyleEditor(left)
        lv.addWidget(self.editor, 1)
        self.editor.changed = self._on_style_changed
        twin.addWidget(left, 3)

        # ------------------------------------------------ 右：渲染
        right_card = CardWidget(self)
        rv = QVBoxLayout(right_card)
        rv.setContentsMargins(*CARD_MARGINS)
        rv.setSpacing(10)
        rv.addWidget(StrongBodyLabel(S("合成输出", "Compose output"), right_card))

        self.video_info = CaptionLabel(S("未加载视频", "No video loaded"), right_card)
        self.video_info.setWordWrap(True)
        rv.addWidget(self.video_info)

        qform = QFormLayout()
        qform.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.quality = ComboBox(right_card)
        for _k, _label in _QUALITY_LABELS:
            self.quality.addItem(_label, userData=_k)
        self.quality.setCurrentIndex(0)
        qform.addRow(S("画质", "Quality"), self.quality)
        rv.addLayout(qform)

        self.out_edit = LineEdit(right_card)
        self.out_edit.setPlaceholderText(S("输出到视频同目录：xxx.subtitled.mp4",
                                           "Next to the video: xxx.subtitled.mp4"))
        rv.addWidget(self.out_edit)

        self.btn_render = PrimaryPushButton(FIF.PLAY, S("开始合成", "Start compose"),
                                            right_card)
        self.btn_render.setMinimumWidth(160)
        self.btn_render.clicked.connect(self._start_render)
        rv.addWidget(self.btn_render, 0, Qt.AlignRight)

        self.btn_cancel = PushButton(FIF.CLOSE, S("取消合成", "Cancel"), right_card)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel_render)
        rv.addWidget(self.btn_cancel, 0, Qt.AlignRight)

        self.progress_label = CaptionLabel("", right_card)
        rv.addWidget(self.progress_label)
        rv.addStretch(1)
        twin.addWidget(right_card, 2)

    # ------------------------------------------------------------ 预设
    def _presets(self) -> Dict[str, CueStyle]:
        from ..core.style_preset import presets_from_json
        return presets_from_json(
            __import__("json").dumps(self.cfg.burn_style_presets,
                                     ensure_ascii=False))

    def _fill_presets(self) -> None:
        self.preset.clear()
        for name in sorted(self.cfg.burn_style_presets):
            self.preset.addItem(name, userData=name)

    def _apply_preset(self) -> None:
        name = self.preset.currentData()
        if not name:
            return
        d = self.cfg.burn_style_presets.get(name)
        if not d:
            return
        self.editor.apply(CueStyle.from_dict(d))
        self._on_style_changed()
        InfoBar.success(S("预设已应用", "Preset applied"),
                        S(f"已套用「{name}」。", f"Applied 「{name}」."),
                        parent=self.main, position=InfoBarPosition.TOP,
                        duration=2600)

    def _save_preset(self) -> None:
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, S("保存样式预设", "Save style preset"),
            S("预设名称：", "Preset name:"),
            text=self.preset.currentText().strip() or "")
        name = (name or "").strip()
        if not ok or not name:
            return
        self.cfg.burn_style_presets[name] = self.editor.collect().to_dict()
        self.cfg.save()
        self._fill_presets()
        self.preset.setCurrentText(name)
        InfoBar.success(S("预设已保存", "Preset saved"),
                        S(f"「{name}」已保存，下次做新项目可直接套用。",
                          f"「{name}」saved; reuse it in any new project."),
                        parent=self.main, position=InfoBarPosition.TOP,
                        duration=3200)

    def _del_preset(self) -> None:
        name = self.preset.currentData()
        if not name:
            return
        self.cfg.burn_style_presets.pop(name, None)
        self.cfg.save()
        self._fill_presets()
        InfoBar.success(S("已删除", "Deleted"), name, parent=self.main,
                        position=InfoBarPosition.TOP, duration=2000)

    # ------------------------------------------------------------ 样式
    def _on_style_changed(self) -> None:
        pass                      # 预览刷新由页面 show 时统一做

    def global_style(self) -> CueStyle:
        return self.editor.collect()

    # ------------------------------------------------------------ 单条级
    def set_cue_override(self, cue_id: str, style: Optional[CueStyle]) -> None:
        """编辑页右键「本条样式」落到这里：None = 清除本条覆盖。"""
        if style is None:
            self._per_cue.pop(cue_id, None)
        else:
            self._per_cue[cue_id] = style

    def cue_override(self, cue_id: str) -> Optional[CueStyle]:
        return self._per_cue.get(cue_id)

    def overridden_count(self) -> int:
        return len(self._per_cue)

    # ------------------------------------------------------------ ASS
    def build_ass(self, doc) -> str:
        return build_burn_ass(doc, self.global_style(), self._per_cue)

    # ------------------------------------------------------------ 视频
    def refresh_video(self) -> None:
        """页面 show 时刷新视频信息（doc.source_video 可能在编辑页被换）。"""
        doc = getattr(self.main, "doc", None)
        path = (doc.source_video if doc else "") or ""
        if not path or not os.path.isfile(path):
            self._video_info = {}
            self.video_info.setText(S("未加载视频", "No video loaded"))
            return
        try:
            self._video_info = render_video.probe_video(path)
            w, h = self._video_info["width"], self._video_info["height"]
            dur = self._video_info["duration"]
            gb = self._video_info["size"] / 1073741824
            self.video_info.setText(S(
                f"{os.path.basename(path)}\n{w}×{h} · {int(dur // 60)}:{int(dur % 60):02d}"
                f" · {gb:.2f} GB · "
                + (S("含音频", "with audio") if self._video_info["audio"]
                   else S("无音频", "no audio")),
                f"{os.path.basename(path)}\n{w}×{h} · {int(dur // 60)}:{int(dur % 60):02d}"
                f" · {gb:.2f} GB · "
                + ("with audio" if self._video_info["audio"] else "no audio")))
            self.doc_meta_video(w, h)
        except Exception as e:
            self.video_info.setText(f"{S('读取失败', 'Probe failed')}: {e}")
        if not self.out_edit.text().strip() and path:
            self.out_edit.setText(render_video.default_out_path(path))

    def doc_meta_video(self, w: int, h: int) -> None:
        """把视频分辨率写进 doc.meta（build_burn_ass 的 PlayRes 用）。"""
        doc = getattr(self.main, "doc", None)
        if doc is not None:
            doc.meta["width"] = int(w)
            doc.meta["height"] = int(h)

    # ------------------------------------------------------------ 渲染
    def _start_render(self) -> None:
        doc = getattr(self.main, "doc", None)
        video = (doc.source_video if doc else "") or ""
        if not video or not os.path.isfile(video):
            InfoBar.warning(S("还没有视频", "No video"),
                            S("请先在编辑页导入视频。", "Import a video in the editor first."),
                            parent=self.main, position=InfoBarPosition.TOP,
                            duration=5000)
            return
        if not doc.cues:
            InfoBar.warning(S("没有字幕", "No cues"),
                            S("当前工程没有任何字幕可烧录。", "Nothing to burn — no cues."),
                            parent=self.main, position=InfoBarPosition.TOP,
                            duration=5000)
            return
        out = self.out_edit.text().strip() or render_video.default_out_path(video)
        if os.path.abspath(out) == os.path.abspath(video):
            InfoBar.warning(S("输出路径无效", "Invalid output"),
                            S("输出文件不能与原视频相同。", "Output must differ from the source."),
                            parent=self.main, position=InfoBarPosition.TOP,
                            duration=5000)
            return

        # ASS 落临时文件（放输出目录旁，路径含中文也能被 ffmpeg 的转义格式吃下）
        ass_path = os.path.splitext(out)[0] + ".burn.ass"
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(self.build_ass(doc))

        self._cancelled[0] = False
        self.btn_render.setEnabled(False)
        self.btn_cancel.setVisible(True)
        quality = self.quality.currentData() or "near_lossless"
        from .workers import ThreadedCall, CB_PROGRESS, CB_LOG, CB_CANCEL
        self._render_worker = ThreadedCall(
            render_video.burn_video, video, ass_path, out,
            quality=quality,
            progress=CB_PROGRESS, cancel=CB_CANCEL, log=CB_LOG)
        self._render_worker.sig_progress.connect(self._on_progress)
        self._render_worker.sig_done.connect(self._on_done)
        self._render_worker.sig_failed.connect(self._on_failed)
        self._render_worker.start()

    def _on_progress(self, msg: str, pct: float) -> None:
        if pct >= 0:
            self.progress_label.setText(S(f"合成中 {pct * 100:.0f}%",
                                         f"Composing {pct * 100:.0f}%"))

    def _on_done(self, _result) -> None:
        self.btn_render.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_label.setText(S("合成完成 ✓", "Compose finished ✓"))
        out = self.out_edit.text().strip()
        InfoBar.success(S("合成完成", "Compose finished"),
                        S(f"已输出：{os.path.basename(out)}",
                          f"Written: {os.path.basename(out)}"),
                        parent=self.main, position=InfoBarPosition.TOP,
                        duration=6000)

    def _on_failed(self, msg: str) -> None:
        self.btn_render.setEnabled(True)
        self.btn_cancel.setVisible(False)
        self.progress_label.setText("")
        if "cancelled" in msg.lower() or S("已取消。", "Cancelled.") in msg:
            self.progress_label.setText(S("已取消", "Cancelled"))
            return
        InfoBar.error(S("合成失败", "Compose failed"), msg[:400],
                      parent=self.main, position=InfoBarPosition.TOP,
                      duration=-1, isClosable=True)

    def _cancel_render(self) -> None:
        self._cancelled[0] = True
        if self._render_worker is not None:
            self._render_worker.cancel()

    # ------------------------------------------------------------ 生命周期
    def showEvent(self, e) -> None:            # noqa: N802
        super().showEvent(e)
        self.refresh_video()
