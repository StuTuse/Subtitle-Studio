"""总控窗口：导航 + 转写进度 + 工程持久化 + 拖拽导入。"""

from __future__ import annotations

import json
import os
from typing import Optional

from qfluentwidgets import (FluentIcon as FIF, FluentWindow, InfoBar, InfoBarPosition,
                            IndeterminateProgressBar, MessageBox, NavigationItemPosition,
                            setTheme)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QDialog, QFileDialog, QLabel, QVBoxLayout, QWidget

from ..core import media
from ..core.config import Config
from ..core.model import CueDocument
from ..version import __version__
from .editor_page import EditorInterface
from .export_page import ExportInterface
from .fix_page import FixInterface
from .settings_page import SettingsInterface
from .theme import apply_theme, open_path
from .workers import TranscribeWorker, reap


class _CloseAskBox(MessageBox):
    """退出确认框：跳过淡入/淡出动画。

    库里的 MaskDialogBase 在 showEvent/done() 里各跑一个 QPropertyAnimation，
    动画对象是无父级的局部变量：关闭窗口时它随时可能被 GC，动画永远不
    finished → 回调里的 QDialog.done() 永不执行 → 表现就是「点保存/不保存
    都没反应」；在 Qt 拆机阶段还会随机触发访问违例（0xC0000005，窗口关了
    进程还在）。确认框不需要动画，直接瞬时显示和关闭。
    """

    def showEvent(self, e) -> None:  # noqa: N802
        self.setGraphicsEffect(None)
        QDialog.showEvent(self, e)

    def done(self, code: int) -> None:  # noqa: N802
        self.setGraphicsEffect(None)
        QDialog.done(self, code)


class MainWindow(FluentWindow):
    def __init__(self, cfg: Optional[Config] = None):
        super().__init__()
        self.cfg = cfg or Config.load()
        self.doc: Optional[CueDocument] = None
        self._dirty = False
        self._worker: Optional[TranscribeWorker] = None
        self._closing = False          # 已进入关闭流程（不再弹"退出前保存"）
        self._close_box = None         # 退出确认框（信号模式，须保引用）

        self.setWindowTitle(f"Subtitle Studio · 视频字幕工坊  v{__version__}")
        self.setWindowIcon(FIF.CAPTION_TEXT.icon() if hasattr(FIF, "CAPTION_TEXT")
                           else FIF.VIDEO.icon())
        self.resize(1440, 900)
        self.setMinimumSize(1100, 720)
        # 1366×768 一类小屏：最小值本身放不下，会卡在桌面上动不了
        try:
            from PyQt5.QtWidgets import QApplication
            _scr = QApplication.primaryScreen()
            if _scr is not None:
                _av = _scr.availableGeometry()
                self.setMinimumSize(min(1100, _av.width() - 40),
                                    min(720, _av.height() - 40))
        except Exception:
            pass
        self._fit_to_screen()
        self.setAcceptDrops(True)

        apply_theme(self.cfg)

        # ------------------------------------------------------------ 页面
        self.editor = EditorInterface(self.cfg, self)
        self.fix = FixInterface(self.cfg, self)
        self.export = ExportInterface(self.cfg, self)
        self.settings = SettingsInterface(self.cfg, self)

        self.addSubInterface(self.editor, FIF.EDIT, "字幕编辑")
        self.addSubInterface(self.fix, FIF.BROOM, "AI 纠错")
        self.addSubInterface(self.export, FIF.SAVE, "导出成品")
        self.addSubInterface(self.settings, FIF.SETTING, "设置",
                             position=NavigationItemPosition.BOTTOM)

        # ------------------------------------------------------------ 进度条
        # FluentWindow 没有 statusBar，用一个悬浮在底部的细条 + 文案代替。
        self.progress = IndeterminateProgressBar(self)
        self.progress.setFixedHeight(3)
        self.progress.setVisible(False)
        self.progressLabel = QLabel("", self)
        self.progressLabel.setObjectName("progressLabel")
        self.progressLabel.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.progressLabel.setStyleSheet(
            "QLabel{background:rgba(0,0,0,170);color:#fff;border-radius:9px;"
            "padding:4px 12px;font-size:12px;}")
        self.progressLabel.setVisible(False)
        self.progress.raise_()
        self.progressLabel.raise_()

        self.editor.doc_changed.connect(self._on_doc_changed)
        self.stackedWidget.currentChanged.connect(self._on_page)

        self._restore_geometry()
        QTimer.singleShot(200, self._maybe_open_cli_file)

    # ------------------------------------------------------------ 底部悬浮进度
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if not hasattr(self, "progress"):
            return
        w, h = self.width(), self.height()
        self.progress.setGeometry(0, 0, w, 3)
        self._place_progress_label()

    def _place_progress_label(self) -> None:
        w, h = self.width(), self.height()
        self.progressLabel.setMinimumWidth(0)
        self.progressLabel.adjustSize()
        lw = min(max(160, self.width() - 80), self.progressLabel.sizeHint().width() + 28)
        lh = max(26, self.progressLabel.sizeHint().height())
        self.progressLabel.setFixedSize(lw, lh)
        self.progressLabel.move((w - lw) // 2, h - lh - 20)

    # ------------------------------------------------------------ 首页/菜单
    def _show_home(self) -> None:
        self.open_media_dialog()

    def _on_page(self, idx: int) -> None:
        w = self.stackedWidget.currentWidget()
        if w is self.fix:
            self.fix.refresh()
        elif w is self.export:
            self.export.refresh()
        elif w is self.settings:
            pass

    def goto_fix(self) -> None:
        if not self.doc or not self.doc.cues:
            self._warn("先完成转写", "还没有字幕内容。请在「字幕编辑」页导入视频并点「转写」。")
            return
        self.switch_to("fix")

    def switch_to(self, key: str) -> None:
        mapping = {"editor": self.editor, "fix": self.fix, "export": self.export,
                   "settings": self.settings}
        w = mapping.get(key)
        if w is not None:
            self.switchTo(w)

    # ------------------------------------------------------------ 拖拽
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                if media.is_media(u.toLocalFile()):
                    e.acceptProposedAction()
                    return
        e.ignore()

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if media.is_media(p):
                self.open_media(p)
                return
            if os.path.splitext(p)[1].lower() in (".ssp", ".srt", ".vtt", ".ass", ".lrc",
                                                  ".txt", ".json"):
                self._load_any(p)
                return

    # ------------------------------------------------------------ 打开
    def open_media_dialog(self) -> None:
        start = self.cfg.last_dir or ""
        fp, _ = QFileDialog.getOpenFileName(self, "选择视频/音频", start,
                                            media.media_filters() + ";;所有文件 (*)")
        if fp:
            self.open_media(fp)

    def open_media(self, path: str) -> None:
        if self._worker is not None:
            self._warn("正在转写", "请先点「取消」结束当前转写，再导入新视频。")
            return
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self._warn("文件不存在", path)
            return
        if self.doc and self.doc.cues and self._dirty:
            box = MessageBox("当前工程未保存", "打开新视频会替换当前字幕。要先保存吗？\n"
                                              "（选择「取消」则直接打开）", self)
            box.yesButton.setText("保存并打开")
            box.cancelButton.setText("直接打开")
            r = box.exec_()
            if r:
                self.save_project()
        info = media.probe(path)
        if info.duration <= 0:
            self._warn("无法读取", "这个文件可能不是有效的媒体文件，或缺少解码器。")
            return
        self.doc = CueDocument(source_video=path, duration=info.duration)
        self.editor.set_document(self.doc)
        self.switch_to("editor")
        self.cfg.last_dir = os.path.dirname(path)
        self.cfg.add_recent(path)
        self.cfg.save()
        self.editor.status.setText(
            f"已载入 {os.path.basename(path)}（{_fmt(info.duration)}，"
            f"{'含视频 ' + str(info.width) + 'x' + str(info.height) if info.has_video else '纯音频'}）"
            " — 点「开始转写」，自动提音频并识别。")
        InfoBar.success("导入成功",
                        f"{os.path.basename(path)}（{_fmt(info.duration)}）已就绪，"
                        "点「开始转写」即可。", parent=self,
                        position=InfoBarPosition.TOP, duration=3500)
        self.mark_dirty()

    def _load_any(self, path: str) -> None:
        if path.lower().endswith(".ssp"):
            self.load_project(path)
            return
        self.switch_to("editor")
        try:
            from ..core import formats
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            cues, fmt = formats.import_text(text, path)
            if not cues:
                self._warn("解析失败", "这个文件里没找到可识别的字幕行。")
                return
            if self.doc is None:
                self.doc = CueDocument()
            self.doc.cues = cues
            self.editor.set_document(self.doc, reset_history=False)
            self.editor._say(f"已按 {fmt} 导入 {len(cues)} 条。", 4000)
            self.mark_dirty()
        except Exception as e:
            self._warn("打开失败", str(e))

    def _maybe_open_cli_file(self) -> None:
        import sys
        for a in sys.argv[1:]:
            if os.path.isfile(a):
                self._load_any(a) if not media.is_media(a) else self.open_media(a)
                return

    # ------------------------------------------------------------ 工程
    def save_project(self, force_dialog: bool = False) -> bool:
        if self.doc is None:    # 空字幕但有视频也允许存工程，判空用 is None
            self._warn("没有内容", "当前没有可保存的工程。")
            return False
        path = self.doc.path
        if force_dialog or not path:
            default = ""
            if self.doc.source_video:
                default = os.path.splitext(self.doc.source_video)[0] + ".ssp"
            path, _ = QFileDialog.getSaveFileName(self, "保存工程", default,
                                                  "字幕工程 (*.ssp)")
            if not path:
                return False
            if not path.lower().endswith(".ssp"):
                path += ".ssp"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.doc.to_json())
        except OSError as e:
            self._warn("保存失败", str(e))
            return False
        self.doc.path = path
        self.cfg.add_recent(path)
        self._dirty = False
        self.cfg.save()
        self._update_title()
        InfoBar.success("已保存", os.path.basename(path), parent=self,
                        position=InfoBarPosition.TOP, duration=2000)
        return True

    def load_project(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            doc = CueDocument.from_dict(data)
        except Exception as e:
            self._warn("打开失败", f"{type(e).__name__}: {e}")
            return
        doc.path = os.path.abspath(path)
        if doc.source_video and not os.path.isfile(doc.source_video):
            fixed = os.path.join(os.path.dirname(path),
                                 os.path.basename(doc.source_video))
            if os.path.isfile(fixed):
                doc.source_video = fixed
        self.doc = doc
        self.editor.set_document(doc)
        self._dirty = False
        self.cfg.add_recent(path)
        self.cfg.last_dir = os.path.dirname(path)
        self.cfg.save()
        self.switch_to("editor")
        self._update_title()
        InfoBar.success("已打开工程", f"{len(doc.cues)} 条字幕", parent=self,
                        position=InfoBarPosition.TOP, duration=2500)

    def mark_dirty(self) -> None:
        self._dirty = True
        self._update_title()
        if self.cfg.auto_save and self.doc and self.doc.path and len(self.doc.cues):
            QTimer.singleShot(2500, self._auto_save)

    def _auto_save(self) -> None:
        if self._dirty and self.doc and self.doc.path:
            try:
                with open(self.doc.path, "w", encoding="utf-8") as f:
                    f.write(self.doc.to_json())
                self._dirty = False
                self._update_title()
            except OSError:
                pass

    def _update_title(self) -> None:
        # 空字幕 doc 布尔为 False：统一用 is not None，否则导入后标题显示"未命名"
        d = self.doc
        name = os.path.basename(d.path) if (d is not None and d.path) else \
            (os.path.basename(d.source_video) if (d is not None and d.source_video)
             else "未命名")
        self.setWindowTitle(f"{'● ' if self._dirty else ''}{name} — Subtitle Studio")

    # ------------------------------------------------------------ 转写
    def start_transcribe(self) -> None:
        if self._worker is not None:
            return          # 已在转写中：由「取消」按钮负责停止
        # 注意：CueDocument 有 __len__，空字幕 doc 的布尔值是 False，判空用 is None
        if self.doc is None or not self.doc.source_video:
            self.open_media_dialog()
            if self.doc is None or not self.doc.source_video:
                return
        if not self.doc.source_video or not os.path.isfile(self.doc.source_video):
            self._warn("视频文件丢失", "请先重新导入视频。")
            return

        from ..core.transcriber import FasterWhisperEngine
        if self.cfg.asr_engine == "faster-whisper" and not FasterWhisperEngine.available():
            box = MessageBox("缺少 faster-whisper",
                             "还没安装 faster-whisper。\n\n"
                             "在终端运行：  pip install faster-whisper\n\n"
                             "（本机已有 CTranslate2 模型，无需重新下载）\n\n"
                             "是否现在打开「设置」改用其它引擎？", self)
            box.yesButton.setText("打开设置")
            box.cancelButton.setText("我知道了")
            if box.exec_():
                self.switch_to("settings")
            return

        self._gen = getattr(self, "_gen", 0) + 1      # 代际：旧 worker 的迟到信号一律丢弃
        gen = self._gen
        self.switch_to("editor")
        self.editor._set_flow("busy", "① 正在提取音频…")
        self._begin_progress("正在提取音频…")
        w = TranscribeWorker(self.doc.source_video, self.cfg,
                             keep_audio=bool(getattr(self.cfg, "keep_audio", False)))
        self._worker = w
        w.sig_progress.connect(lambda m, p, g=gen: self._on_progress(g, m, p))
        w.sig_stage.connect(lambda s: None)
        w.sig_done.connect(lambda d, g=gen: self._on_transcribed(g, d))
        w.sig_failed.connect(lambda m, g=gen: self._on_transcribe_failed(g, m))
        w.start()

    def _stale(self, gen: int) -> bool:
        """取消后旧线程仍可能补发信号（如"已取消。"）；代际不匹配就忽略，
        否则会把新一轮转写的进度/状态打回旧值。"""
        return gen != getattr(self, "_gen", 0)

    def cancel_transcribe(self) -> None:
        """hero 面板上的「取消」：终止当前转写，回到待转写状态。"""
        if self._worker is None:
            return
        w = self._worker
        w.cancel()
        reap(w)
        self._worker = None
        self._end_progress()
        name = os.path.basename(self.doc.source_video) if (
            self.doc is not None and self.doc.source_video) else "视频"
        self.editor._set_flow("ready", f"已导入：{name}")
        self.editor.status.setText("已取消转写，可重新开始。")

    def _on_progress(self, gen: int, msg: str, pct: float) -> None:
        if self._stale(gen):
            return
        self.progressLabel.setText(msg)
        self.progressLabel.adjustSize()
        self._place_progress_label()
        self.editor.status.setText(msg)
        self.editor.set_flow_progress(msg, pct)
        if pct and pct >= 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(pct * 1000))
        else:
            self.progress.setRange(0, 0)

    def _on_transcribed(self, gen: int, doc: CueDocument) -> None:
        if self._stale(gen):
            return
        w = self._worker
        self._worker = None
        reap(w)
        self._end_progress()
        if self.doc is not None:   # 空字幕 doc 布尔为 False，必须 is not None
            doc.path = self.doc.path
            doc.source_video = self.doc.source_video
            if not doc.duration:
                doc.duration = self.doc.duration
        self.doc = doc
        self.editor.set_document(doc)
        self.editor.set_flow_progress("", -1)
        self.progressLabel.setText("")
        m = doc.meta
        self.editor.status.setText(
            f"转写完成：{len(doc.cues)} 条 · 用时 {m.get('elapsed', 0)}s · "
            f"速度 {m.get('speed', 0)}x · 语言 {m.get('language', '?')} · "
            "可去「AI 纠错」修错别字。")
        self.mark_dirty()
        InfoBar.success("转写完成", f"{len(doc.cues)} 条字幕（{m.get('engine', '')}）。"
                        + (f"已自动衔接 {m.get('gaps_closed', 0)} 处字幕空隙，"
                           "防止播放时闪断。" if m.get("gaps_closed") else "")
                        + "建议接着做 AI 纠错。", parent=self,
                        position=InfoBarPosition.TOP, duration=6000)

    def _on_transcribe_failed(self, gen: int, msg: str) -> None:
        if self._stale(gen):
            return
        cancelled = "取消" in (msg or "")
        self._worker = None
        self._end_progress()
        self.progressLabel.setText("")
        if self.doc is not None and self.doc.source_video:
            self.editor._set_flow("ready",
                                  f"已导入：{os.path.basename(self.doc.source_video)}")
        else:
            self.editor._set_flow("empty")
        if cancelled:
            self.editor.status.setText("已取消转写，可重新开始。")
            return
        self.editor.status.setText("转写失败。")
        InfoBar.error("转写失败", msg[:600], parent=self,
                      position=InfoBarPosition.TOP, duration=9000)

    def _begin_progress(self, msg: str) -> None:
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.progressLabel.setText(msg)
        self.progressLabel.setVisible(True)
        self.progressLabel.adjustSize()
        QTimer.singleShot(0, lambda: self.resizeEvent(None) if False else self.update())

    def _end_progress(self) -> None:
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        self.progressLabel.setVisible(False)

    # ------------------------------------------------------------ 杂项
    def _on_doc_changed(self) -> None:
        if self.stackedWidget.currentWidget() is self.export:
            self.export.refresh()

    def _warn(self, title: str, msg: str) -> None:
        InfoBar.warning(title, msg, parent=self, position=InfoBarPosition.TOP, duration=5000)

    def _fit_to_screen(self) -> None:
        """把初始尺寸收进当前屏幕可用区。

        原来写死 resize(1440, 900)：在 1366×768 笔记本、或 150% 缩放的 1080p 屏上
        窗口会比桌面还高，标题栏跑到屏幕外、底部控件点不到。这里按可用区夹一遍。
        """
        try:
            from PyQt5.QtWidgets import QApplication
            scr = QApplication.screenAt(self.frameGeometry().center()) \
                or QApplication.primaryScreen()
            if scr is None:
                return
            avail = scr.availableGeometry()
            w = min(self.width(), int(avail.width() * 0.96))
            h = min(self.height(), int(avail.height() * 0.94))
            # 允许最小值略大于屏（极端小屏时 Qt 自行处理），这里只做温和收敛
            self.resize(max(900, w), max(600, h))
        except Exception:
            pass

    def apply_cfg_theme(self) -> None:
        apply_theme(self.cfg)

    def _restore_geometry(self) -> None:
        try:
            if self.cfg.window_geometry:
                from PyQt5.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromBase64(
                    self.cfg.window_geometry.encode("ascii")))
                self._ensure_on_screen()
        except Exception:
            pass

    def _ensure_on_screen(self) -> None:
        """跨屏/改过缩放后，恢复的位置可能在屏幕外，拉回主屏。"""
        try:
            from PyQt5.QtWidgets import QApplication
            geo = self.frameGeometry()
            for scr in QApplication.screens():
                if scr.availableGeometry().intersects(geo):
                    return
            center = QApplication.primaryScreen().availableGeometry().center()
            fg = self.frameGeometry()
            self.move(center.x() - fg.width() // 2, center.y() - fg.height() // 2)
        except Exception:
            pass

    def closeEvent(self, e) -> None:  # noqa: N802
        # 未保存 → 先挡下这次关闭，弹框用"信号模式"。
        # 不在 closeEvent 里用 box.exec_()：MessageBox 关闭时有淡出动画，
        # 动画对象无父级、可能被 GC，动画不完成 exec_ 就永远不返回——
        # 表现是"点保存/不保存都没反应"；嵌套事件循环在关闭过程中还会
        # 直接崩溃（实测 0xC0000005）。信号模式不开嵌套循环，没有这两个坑。
        if (self._dirty and self.doc is not None and self.doc.cues
                and not self._closing):
            e.ignore()
            if self._close_box is not None:
                return                       # 已经问过了，等用户点
            box = _CloseAskBox("退出前保存？", "当前字幕尚未保存。", self)
            box.yesButton.setText("保存并退出")
            box.cancelButton.setText("不保存退出")
            self._close_box = box
            box.yesSignal.connect(self._close_save_quit)
            box.cancelSignal.connect(self._close_discard_quit)
            box.finished.connect(lambda _=0: setattr(self, "_close_box", None))
            box.show()
            return
        self._closing = True
        # 先拆媒体后端：QMediaPlayer(DirectShow) 在进程拆机阶段被动析构会
        # 随机 0xC0000005（表现为"窗口关了进程卡住/弹崩溃框"）
        try:
            self.editor.player.shutdown()
        except Exception:
            pass
        try:
            self.cfg.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.cfg.player_volume = self.editor.player.volume()
            # 记住编辑页「播放器|修改区」的分栏比例，下次开还是这个布局
            try:
                self.cfg.editor_hsplit = [int(x) for x in self.editor.hsplit.sizes()]
            except Exception:
                pass
            self.cfg.save()
        except Exception:
            pass
        if self._worker:
            self._worker.cancel()
            # 退出时不能把还在跑的线程丢给 GC：等它自己收尾（最多 5s）
            try:
                self._worker.wait(5000)
            except RuntimeError:
                pass
        super().closeEvent(e)

    def _close_save_quit(self) -> None:
        """「保存并退出」：保存成功才走关闭流程；取消保存则留在软件里。"""
        if self.save_project():
            self._close_box = None
            self._closing = True             # 别在第二次 closeEvent 又弹一次
            self.close()

    def _close_discard_quit(self) -> None:
        """「不保存退出」。"""
        self._close_box = None
        self._closing = True
        self.close()


def _fmt(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
