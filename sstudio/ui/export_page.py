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
from .theme import CARD_MARGINS, PRIMARY_MIN_W, err_span, ok_span, open_path
from .workers import ThreadedCall


def _write_exports(pairs, out_dir, enc):
    """后台线程里跑：试探编码 → 写盘。pairs 是主线程渲染好的 [(名字, 文本)]，
    线程不碰 doc——导出期间用户还能在编辑页改字幕，跨线程读会读到半截。
    每个文件走 tmp + os.replace 原子落盘：退出路径的 os._exit(0) 不等线程
    finally，裸 open 写一半正好被强杀的话，目标位置就留下截断的成品；
    原子替换保证盘上要么没有、要么是完整文件。"""
    written, errors = [], []
    for name, text in pairs:
        try:
            fp = os.path.join(out_dir, name)
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding=_safe_enc(enc, text), newline="") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, fp)
            written.append(fp)
        except Exception as e:
            errors.append((name, str(e)))
    if not written:
        raise OSError("；".join(f"{n}: {e}" for n, e in errors) or "没有写出任何文件")
    return written, errors


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
        lv.setContentsMargins(*CARD_MARGINS)
        lv.addWidget(StrongBodyLabel("选择导出格式（可多选）", left))
        self.fmt_list = QListWidget(left)
        self.fmt_list.setSelectionMode(QListWidget.NoSelection)
        for key in formats.EXPORT_ORDER:
            spec = formats.FORMATS[key]
            it = QListWidgetItem(f"{spec.label} — {spec.desc}")
            # 窄窗下列表项被省略号截断时，悬浮还有完整说明可看
            it.setToolTip(f"{spec.label}：{spec.desc}")
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
        rv.setContentsMargins(*CARD_MARGINS)
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
        self.btn_export = PrimaryPushButton(FIF.SAVE_AS, "导出", self)
        self.btn_export.setMinimumWidth(PRIMARY_MIN_W)
        self.btn_export.clicked.connect(self._export)
        bar.addStretch(1)
        bar.addWidget(self.btn_preview)
        bar.addWidget(self.btn_export)
        lay.addLayout(bar)

        self._last_files: List[str] = []
        self._worker = None

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
            html += "<br>" + err_span("⚠ " + "；".join(problems))
        else:
            html += "<br>" + ok_span("✓ 未发现明显问题")
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
        if doc is not None and doc.source_video:   # 空字幕文档布尔为 False，用 is not None
            return os.path.dirname(doc.source_video)
        return self.cfg.last_dir or os.path.expanduser("~")

    def _base_name(self) -> str:
        # 注意：CueDocument 定义了 __len__，空字幕文档布尔值是 False，
        # 判空一律用 is not None，否则刚导入还没转写时这里会错退 "subtitle"。
        doc = self.main.doc
        if doc is None:
            return "subtitle"
        if self.chk_video_name.isChecked() and doc.source_video:
            return os.path.splitext(os.path.basename(doc.source_video))[0]
        if doc.path:
            return os.path.splitext(os.path.basename(doc.path))[0]
        # 工程还没存过：退到视频名，别再一律叫 subtitle——多格式导出会互相覆盖
        if doc.source_video:
            return os.path.splitext(os.path.basename(doc.source_video))[0]
        return "subtitle"

    def _file_name(self, tpl: str, base: str, doc, ext: str) -> str:
        """套文件名模板。模板是用户输入的：占位符补全后必须剥掉目录、
        换掉 Windows 非法字符，否则一句 ..\\x 就能写到输出目录外面去。"""
        lang = doc.language if doc is not None else ""
        stem = tpl.replace("{name}", base).replace("{lang}", lang or "")
        stem = stem.replace("{ext}", "")            # 扩展名统一在下面补
        for ch in '<>:"|?*':
            stem = stem.replace(ch, "_")
        stem = os.path.basename(stem.replace("\\", "/")).strip()
        return (stem or "subtitle") + ext

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
        if getattr(self, "_worker", None) is not None:
            return                      # 上一次导出还没落地
        base = self._base_name()
        enc = self._enc()
        tpl = self.name_tpl.text()
        # 渲染留在主线程：纯内存操作、快，而且避免后台线程读 doc 与用户
        # 编辑竞态。真正慢的写盘（网络盘/编码试探）才丢给线程。
        pairs, render_errors = [], []
        for key in keys:
            spec = formats.FORMATS[key]
            try:
                pairs.append((self._file_name(tpl, base, doc, spec.ext),
                              formats.export_text(doc, key)))
            except Exception as e:
                render_errors.append((spec.label, str(e)))
        for label, err in render_errors:
            InfoBar.error("导出失败", f"{label}: {err}", parent=self.main,
                          position=InfoBarPosition.TOP, duration=4000)
        if not pairs:
            return
        self.btn_export.setEnabled(False)
        self._worker = ThreadedCall(_write_exports, pairs, out_dir, enc)
        self._worker.sig_done.connect(self._on_export_done)
        self._worker.sig_failed.connect(self._on_export_failed)
        self._worker.start()
        # 不再 finished→deleteLater：sig_done 与 finished 都是跨线程 queued
        # 投递、先后无序，deleteLater 抢先会把 sig_done 连同 worker 一起删掉
        # ——按钮永久禁用、后续导出被 _worker 非 None 守卫静默拦截。生命周期
        # 交给 reap()（_finish_export 里统一收）。

    def _finish_export(self) -> None:
        from .workers import reap
        reap(self._worker)
        self._worker = None
        self.btn_export.setEnabled(bool(self.main.doc and self.main.doc.cues))

    def _on_export_failed(self, msg: str) -> None:
        self._finish_export()
        InfoBar.error("导出失败", msg, parent=self.main,
                      position=InfoBarPosition.TOP, duration=4000)

    def _on_export_done(self, outcome) -> None:
        self._finish_export()
        written, errors = outcome
        for name, err in errors:
            InfoBar.error("写入失败", f"{name}: {err}", parent=self.main,
                          position=InfoBarPosition.TOP, duration=4000)
        if not written:
            return
        enc = self._enc()
        out_dir = os.path.dirname(written[0])
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
