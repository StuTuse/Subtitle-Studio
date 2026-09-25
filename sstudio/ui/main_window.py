"""总控窗口：导航 + 转写进度 + 工程持久化 + 拖拽导入。"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional

from qfluentwidgets import (FluentIcon as FIF, FluentWindow, InfoBar, InfoBarPosition,
                            MessageBox, NavigationItemPosition, ProgressBar,
                            setTheme)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QDialog, QFileDialog, QLabel

from ..core import media
from ..core.config import Config
from ..core.i18n import S
from ..core.model import CueDocument
from ..version import __version__
from .editor_page import EditorInterface
from .export_page import ExportInterface
from .fix_page import FixInterface
from .settings_page import SettingsInterface
from .theme import apply_theme
from .workers import TranscribeWorker, reap


def _snapshot_to_json(snap: dict, doc: CueDocument) -> str:
    """把撤销栈格式的快照序列化成完整工程 JSON。

    自动保存的序列化在工作线程跑：直接 to_json 会与 UI 线程的
    normalize_cues（list.sort 迭代中改列表）竞态。这里只碰 UI 线程
    预先抽好的只读快照（cue dict 列表）+ 文档级三字段（字符串/数值，
    跨线程读安全）。
    """
    import json as _json
    return _json.dumps({
        "format": "subtitle-studio-project",
        "version": 2,
        "source_video": doc.source_video,
        "duration": doc.duration,
        "language": doc.language,
        "meta": doc.meta,
        "cues": snap.get("cues", []),
    }, ensure_ascii=False, indent=2)


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
        self._prog_pending = None      # 进度节流：被丢弃的最新一帧
        self._prog_timer = QTimer(self)   # 转写进度节流定时器
        self._autosave_worker = None   # 自动保存线程（单飞，见 _auto_save）
        self._dirty_gen = 0            # dirty 代数：自动保存完成时复核用
        self._queue: list = []         # 批量队列：待顺序转写的媒体绝对路径
        self._queue_active = False     # 队列开关：_queue_advance 只在开时续跑
        self._prog_timer.setSingleShot(True)
        self._prog_timer.timeout.connect(self._flush_progress)

        self.setWindowTitle(S(f"Subtitle Studio · 视频字幕工坊  v{__version__}",
                              f"Subtitle Studio · subtitle workshop  v{__version__}"))
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

        # ------------------------------------------------------------ 界面叠影修复
        # qfluentwidgets 1.8.4 在 Win11 上默认开启 Mica：DWM 背板
        # （ACCENT_ENABLE_HOSTBACKDROP + SYSTEMBACKDROP）+ 框架延伸拉满整个
        # 客户区 + 窗口底色全透明（paintEvent 画 alpha=0）。其上又叠
        # StackedWidget 的半透明白样式和本程序的自定义 QApplication 调色板，
        # DWM 合成一步跟不上，用户看到的就是「界面渲染了好几层，叠在一起」。
        # 关掉 Mica 回到不透明实底，并把被拉满的框架延伸复位成标准窗口。
        # 注意必须**先**关 Mica 再 apply_theme：setMicaEffectEnabled(False)
        # 会触发 120ms 底色动画（alpha=0→实色），若放在 apply_theme 之后，
        # show() 时动画仍在跑，首帧合成跳变 = 窗口闪一下。先关 Mica，
        # 底色动画在 splash 背后（MainWindow 尚未显示）就结束。
        self.setMicaEffectEnabled(False)
        try:
            self.windowEffect.addShadowEffect(self.winId())
        except Exception:
            pass

        apply_theme(self.cfg)

        # ------------------------------------------------------------ 页面
        self.editor = EditorInterface(self.cfg, self)
        self.fix = FixInterface(self.cfg, self)
        self.export = ExportInterface(self.cfg, self)
        self.settings = SettingsInterface(self.cfg, self)

        self.addSubInterface(self.editor, FIF.EDIT, S("字幕编辑", "Editor"))
        self.addSubInterface(self.fix, FIF.BROOM, S("AI 纠错", "AI Fix"))
        self.addSubInterface(self.export, FIF.SAVE, S("导出成品", "Export"))
        self.addSubInterface(self.settings, FIF.SETTING, S("设置", "Settings"),
                             position=NavigationItemPosition.BOTTOM)

        # ------------------------------------------------------------ 进度条
        # FluentWindow 没有 statusBar，用一个悬浮在底部的细条 + 文案代替。
        # 用确定值 ProgressBar：转写/纠错都有真实百分比；不确定阶段
        # （pct<0）走 setRange(0,0) 的忙碌态。之前用 IndeterminateProgressBar
        # 时 setValue 被其 paintEvent 完全忽略——百分比永远画不出来。
        self.progress = ProgressBar(self)
        self.progress.setFixedHeight(3)
        self.progress.setVisible(False)
        self.progressLabel = QLabel("", self)
        self.progressLabel.setObjectName("progressLabel")
        self.progressLabel.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        from .theme import FONT_BADGE
        self.progressLabel.setStyleSheet(
            f"QLabel{{background:rgba(0,0,0,170);color:#fff;border-radius:9px;"
            f"padding:4px 12px;font-size:{FONT_BADGE}px;}}")
        self.progressLabel.setVisible(False)
        self.progress.raise_()
        self.progressLabel.raise_()

        self.editor.doc_changed.connect(self._on_doc_changed)
        self.stackedWidget.currentChanged.connect(self._on_page)

        self._restore_geometry()

        # ------------------------------------------------------------ 启动闪烁根治
        # 窗口显示前的"静止化"三连，把所有会拖到 show() 之后才收敛的状态
        # 全部提前钉死，首帧即为最终帧，DWM 不再有任何跳变可闪：
        # ① 底色动画：apply_theme 的 setTheme 会经 themeChanged 触发 120ms
        #    backgroundColorAni（实测 show 时仍在 Running）——停掉并直接把
        #    bgColorObject 置终值，首帧画的就是实色。
        # ② DWM 模糊背板残留：qframelesswindow 的 AcrylicWindow 在 Win11 上
        #    构造时 updateFrameless 会 DwmEnableBlurBehindWindow(True) +
        #    setAcrylicEffect；我们关 Mica 只清 accent policy，blurBehind
        #    独立 API 仍开着——开窗后 DWM 还在对客户区做模糊重合成 = 闪。
        #    显式 disable 掉。
        # ③ 主题样式：apply_theme 已在构造期（splash 盖着时）全量刷新完毕，
        #    show 后不再有 QSS 抖动源。
        self.backgroundColorAni.stop()
        self.setBackgroundColor(self._normalBackgroundColor())
        try:
            self.windowEffect.disableBlurBehindWindow(self.winId())
        except Exception:
            pass
        # 命令行文件由 __main__ 统一 singleShot 打开（带 isfile 校验）；
        # 这里不再扫 sys.argv——曾导致同一文件被两个定时器各开一次
        # （双 probe、双 InfoBar），且 "--out 路径" 这类旗标值会被误当目标。

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
        # 编辑页快捷键按页路由：WindowShortcut 固定启用会在其它页误伤
        # （纠错页文本框按 Ctrl+Z 触发字幕撤销而非输入框撤销）
        if hasattr(self.editor, "set_page_active"):
            self.editor.set_page_active(w is self.editor)
        if w is self.fix:
            self.fix.refresh()
        elif w is self.export:
            self.export.refresh()
        elif w is self.settings:
            pass

    def goto_fix(self) -> None:
        if not self.doc or not self.doc.cues:
            self._warn(S("先完成转写", "Transcribe first"),
                       S("还没有字幕内容。请在「字幕编辑」页导入视频并点「转写」。",
                         "No subtitles yet. Import a video in the Editor page and press Transcribe."))
            return
        self.switch_to("fix")

    def switch_to(self, key: str) -> None:
        mapping = {"editor": self.editor, "fix": self.fix, "export": self.export,
                   "settings": self.settings}
        w = mapping.get(key)
        if w is not None:
            self.switchTo(w)

    # ------------------------------------------------------------ 拖拽
    # dropEvent 认识的所有扩展名：媒体 + 字幕/文本/工程。dragEnter 必须
    # 放行同一集合，否则字幕/工程拖进来在 enter 阶段就被 Qt 拒收，
    # dropEvent 里对应的处理分支永远不会执行（表现：拖入毫无反应）。
    _DROP_EXTS = (".ssp", ".srt", ".vtt", ".ass", ".lrc", ".txt", ".json",
                  ".md")

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                p = u.toLocalFile()
                if media.is_media(p) or (os.path.splitext(p)[1].lower()
                                         in self._DROP_EXTS):
                    e.acceptProposedAction()
                    return
        e.ignore()

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        media_paths: list = []
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if media.is_media(p):
                media_paths.append(p)
            elif os.path.splitext(p)[1].lower() in self._DROP_EXTS:
                self._load_any(p)
                return
        if len(media_paths) >= 2:
            # 多文件拖入 = 批量队列：逐个 转写 → 存 .ssp → 下一个。
            # 当前有转写在跑也不打断：队列会在当前任务结束后自动接上。
            self.enqueue_batch(media_paths)
            return
        if media_paths:
            self.open_media(media_paths[0])

    # ------------------------------------------------------------ 打开
    def open_media_dialog(self) -> None:
        start = self.cfg.last_dir or ""
        fp, _ = QFileDialog.getOpenFileName(self, S("选择视频/音频", "Choose video/audio"),
                                            start,
                                            media.media_filters() + ";;" + S("所有文件 (*)", "All files (*)"))
        if fp:
            self.open_media(fp)

    def open_media(self, path: str) -> None:
        if self._worker is not None:
            self._warn(S("正在转写", "Transcribing"),
                       S("请先点「取消」结束当前转写，再导入新视频。",
                         "Press Cancel to stop the current transcription before importing."))
            return
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self._warn(S("文件不存在", "File not found"), path)
            return
        if self.doc and self.doc.cues and self._dirty:
            # 用无动画的 _CloseAskBox：裸 MessageBox 的淡出动画对象无父级，
            # 被 GC 后 done() 永不执行——表现是"点保存/不保存都没反应"
            box = _CloseAskBox(S("当前工程未保存", "Unsaved project"),
                               S("打开新视频会替换当前字幕。要先保存吗？\n"
                                 "（选择「取消」则直接打开）",
                                 "Opening a new video replaces the current subtitles. Save first?\n"
                                 "(Cancel opens directly)"), self)
            box.yesButton.setText(S("保存并打开", "Save and open"))
            box.cancelButton.setText(S("直接打开", "Open directly"))
            r = box.exec_()
            if r:
                self.save_project()
        info = media.probe(path)
        if info.duration <= 0:
            self._warn(S("无法读取", "Cannot read"),
                       S("这个文件可能不是有效的媒体文件，或缺少解码器。",
                         "This may not be a valid media file, or a decoder is missing."))
            return
        self.doc = CueDocument(source_video=path, duration=info.duration)
        self.editor.set_document(self.doc)
        self.switch_to("editor")
        self.cfg.last_dir = os.path.dirname(path)
        self.cfg.add_recent(path)
        self.cfg.save()
        self.editor.status.setText(
            S(f"已载入 {os.path.basename(path)}（{_fmt(info.duration)}，"
              f"{'含视频 ' + str(info.width) + 'x' + str(info.height) if info.has_video else '纯音频'}）"
              " — 点「开始转写」，自动提音频并识别。",
              f"Loaded {os.path.basename(path)} ({_fmt(info.duration)}, "
              f"{'video ' + str(info.width) + 'x' + str(info.height) if info.has_video else 'audio only'})"
              " — press Transcribe to extract audio and recognize."))
        InfoBar.success(S("导入成功", "Imported"),
                        S(f"{os.path.basename(path)}（{_fmt(info.duration)}）已就绪，"
                          "点「开始转写」即可。",
                          f"{os.path.basename(path)} ({_fmt(info.duration)}) is ready — press Transcribe."),
                        parent=self,
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
                self._warn(S("解析失败", "Parse failed"),
                           S("这个文件里没找到可识别的字幕行。",
                             "No recognizable subtitle lines found in this file."))
                return
            if self.doc is None:
                self.doc = CueDocument()
            # 与编辑页「导入字幕」一致：旧内容非空先入撤销栈，导入后
            # Ctrl+Z 能回到导入前（否则旧 undo 快照对着被整体替换的
            # cues，撤销会错乱地恢复旧列表）
            if self.doc.cues:
                self.editor.push_undo()
            self.doc.cues = cues
            from ..core.model import normalize_cues
            normalize_cues(self.doc)
            self.doc.meta["imported_from"] = os.path.abspath(path)
            self.doc.meta["imported_format"] = fmt
            self.editor.set_document(self.doc, reset_history=False)
            self.editor._say(S(f"已按 {fmt} 导入 {len(cues)} 条。",
                                f"Imported {len(cues)} cues as {fmt}."), 4000)
            self.mark_dirty()
        except Exception as e:
            self._warn(S("打开失败", "Open failed"), str(e))

    # ------------------------------------------------------------ 工程
    def save_project(self, force_dialog: bool = False) -> bool:
        if self.doc is None:    # 空字幕但有视频也允许存工程，判空用 is None
            self._warn(S("没有内容", "Nothing to save"),
                       S("当前没有可保存的工程。", "There is no project to save."))
            return False
        path = self.doc.path
        if force_dialog or not path:
            default = ""
            if self.doc.source_video:
                default = os.path.splitext(self.doc.source_video)[0] + ".ssp"
            path, _ = QFileDialog.getSaveFileName(self, S("保存工程", "Save project"),
                                                  default,
                                                  S("字幕工程", "Subtitle project") + " (*.ssp)")
            if not path:
                return False
            if not path.lower().endswith(".ssp"):
                path += ".ssp"
        # 保存代数 +1：让仍在后台跑的自动保存线程在写盘前自查到"已被手动
        # 保存取代"，放弃用序列化中的旧快照覆盖这次刚落盘的新内容
        self._save_gen = getattr(self, "_save_gen", 0) + 1
        try:
            _atomic_write_text(path, self.doc.to_json())
        except OSError as e:
            self._warn(S("保存失败", "Save failed"), str(e))
            return False
        try:
            from ..core import recovery
            recovery.discard_snapshot(self.doc)   # 手动保存成功：恢复快照完成使命
        except Exception:
            pass
        self.doc.path = path
        self.cfg.add_recent(path)
        self._dirty = False
        self.cfg.save()
        self._update_title()
        InfoBar.success(S("已保存", "Saved"), os.path.basename(path), parent=self,
                        position=InfoBarPosition.TOP, duration=2000)
        return True

    def load_project(self, path: str) -> None:
        # 与 open_media 同一条防护：拖 .ssp 进窗口/菜单打开工程也会整替换
        # self.doc，旧实现没有 dirty 检查——正在编辑的内容被静默清空且
        # set_document 默认抹掉撤销栈，Ctrl+Z 都救不回来。
        if self._worker is not None:
            self._warn(S("正在转写", "Transcribing"),
                       S("请先等当前转写结束或取消，再打开工程。",
                         "Wait for (or cancel) the current transcription before opening a project."))
            return
        if self.doc and self.doc.cues and self._dirty:
            box = _CloseAskBox(S("当前工程未保存", "Unsaved project"),
                               S("打开新工程会替换当前字幕。要先保存吗？\n"
                                 "（选择「取消」则直接打开）",
                                 "Opening another project replaces the current subtitles. Save first?\n"
                                 "(Cancel opens directly)"), self)
            box.yesButton.setText(S("保存并打开", "Save and open"))
            box.cancelButton.setText(S("直接打开", "Open directly"))
            r = box.exec_()
            if r:
                if not self.save_project():
                    return      # 保存被用户取消/失败：别丢当前内容
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            doc = CueDocument.from_dict(data)
        except Exception as e:
            self._warn(S("打开失败", "Open failed"), f"{type(e).__name__}: {e}")
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
        try:
            from ..core import recovery
            recovery.discard_snapshot(doc)   # 已正常打开盘上工程：对应快照过期
        except Exception:
            pass
        self.cfg.add_recent(path)
        self.cfg.last_dir = os.path.dirname(path)
        self.cfg.save()
        self.switch_to("editor")
        self._update_title()
        InfoBar.success(S("已打开工程", "Project opened"),
                        S(f"{len(doc.cues)} 条字幕", f"{len(doc.cues)} cues"),
                        parent=self, position=InfoBarPosition.TOP, duration=2500)

    def mark_dirty(self) -> None:
        self._dirty = True
        # 递增 dirty 代数：自动保存完成时用它判断"写盘期间是否又有编辑"。
        # 旧实现从不在这里递增，_autosave_done 的复核恒成立——写盘期间
        # 用户的新输入会被误清脏标，盘上内容比界面旧却显示"已保存"。
        self._dirty_gen += 1
        self._update_title()
        if self.cfg.auto_save and self.doc and self.doc.path and len(self.doc.cues):
            QTimer.singleShot(2500, self._auto_save)
        # 崩溃恢复快照：自动保存只覆盖已有 .ssp 的工程，且限速 45s；
        # 从未保存过的新工程崩溃后无处可寻——这里按 8s 节流补一层
        self._schedule_snapshot()

    def _schedule_snapshot(self) -> None:
        try:
            from ..core import recovery
            now = time.monotonic()
            if now - getattr(self, "_snap_ts", 0.0) < recovery.SNAPSHOT_INTERVAL:
                if getattr(self, "_snap_timer", None) is None:
                    self._snap_timer = QTimer(self)
                    self._snap_timer.setSingleShot(True)
                    self._snap_timer.timeout.connect(self._write_snapshot)
                if not self._snap_timer.isActive():
                    self._snap_timer.start(2000)     # 尾随 2s：保证最后一拍也落盘
                return
            self._snap_ts = now
            recovery.write_snapshot(self.doc, self._dirty_gen)
        except Exception:
            pass

    def _write_snapshot(self) -> None:
        try:
            from ..core import recovery
            recovery.write_snapshot(self.doc, self._dirty_gen)
        except Exception:
            pass

    def _auto_save(self) -> None:
        if not (self._dirty and self.doc and self.doc.path):
            return
        if self._autosave_worker is not None:
            return          # 上一次自动保存还没落地：下次 mark_dirty 会再排
        # 落盘限速：LLM 流式纠错每条回调一次 mark_dirty，5000 条会在
        # 10 分钟里触发约 240 次「全量序列化 + fsync + replace」，纯写放大
        # （SSD 磨损 + 杀软反复扫描）。两次自动保存至少隔 45s；间隔不足
        # 时用尾随定时器顺延到点再存，最新内容不会丢——脏标还在。
        now = time.time()
        last = getattr(self, "_last_autosave_ts", 0.0)
        wait = 45.0 - (now - last)
        if wait > 0:
            QTimer.singleShot(int(wait * 1000) + 250, self._auto_save)
            return
        self._last_autosave_ts = now
        doc, path = self.doc, self.doc.path
        # 序列化在工作线程跑，期间 UI 还在改 doc.cues（normalize 的
        # list.sort() 迭代中改列表会让 to_json 崩或漏条目）：在 UI 线程
        # 先做快照，线程只碰这份只读数据。
        snap = doc.snapshot()
        gen = self._dirty_gen
        save_gen = getattr(self, "_save_gen", 0)
        # 5000 条字幕 to_json ~150ms：放工作线程。完成后按代数复核：
        # 期间没再编辑（代数没变）才清脏标——变过说明有新编辑，新编辑
        # 自己会再排一次自动保存，这次写盘只是中间态。
        from .workers import ThreadedCall

        def _serialize_and_write() -> str:
            # 写盘前复核保存代数：期间用户手动保存过（save_project 递增
            # _save_gen）就放弃——线程手里的快照比盘上的旧，覆盖会回滚
            # 用户刚保存的内容。
            if getattr(self, "_save_gen", 0) != save_gen:
                return ""
            _atomic_write_text(path, _snapshot_to_json(snap, doc))
            return path

        w = ThreadedCall(_serialize_and_write)
        self._autosave_worker = w
        w.sig_done.connect(lambda _p: self._autosave_done(gen))
        w.sig_failed.connect(lambda _m: self._autosave_done(gen, failed=True))
        w.start()

    def _autosave_done(self, gen: int, failed: bool = False) -> None:
        from .workers import reap
        reap(self._autosave_worker)
        self._autosave_worker = None
        if failed:
            return      # 写盘失败：保留脏标，下次 mark_dirty 会再排自动保存
        if gen == self._dirty_gen and self._dirty:
            # 自启动这次自动保存起没有新编辑（代数未变）：这次写盘内容
            # 就是最新状态，清脏标安全
            self._dirty = False
            self._update_title()

    def _update_title(self) -> None:
        # 空字幕 doc 布尔为 False：统一用 is not None，否则导入后标题显示"未命名"
        d = self.doc
        name = os.path.basename(d.path) if (d is not None and d.path) else \
            (os.path.basename(d.source_video) if (d is not None and d.source_video)
             else S("未命名", "Untitled"))
        self.setWindowTitle(f"{'● ' if self._dirty else ''}{name} — Subtitle Studio")

    # ------------------------------------------------------------ 转写
    def start_transcribe(self, queue_mode: bool = False) -> None:
        if self._worker is not None:
            return          # 已在转写中：由「取消」按钮负责停止
        # 注意：CueDocument 有 __len__，空字幕 doc 的布尔值是 False，判空用 is None
        if self.doc is None or not self.doc.source_video:
            if queue_mode:
                # 队列模式没有视频可弹对话框：跳过此项（_queue_next 兜底续跑）
                QTimer.singleShot(300, self._queue_next)
                return
            self.open_media_dialog()
            if self.doc is None or not self.doc.source_video:
                return
        if not self.doc.source_video or not os.path.isfile(self.doc.source_video):
            if queue_mode:
                QTimer.singleShot(300, self._queue_next)
                return
            self._warn(S("视频文件丢失", "Video file missing"),
                       S("请先重新导入视频。", "Please import the video again."))
            return

        from ..core.transcriber import FasterWhisperEngine
        if self.cfg.asr_engine == "faster-whisper" and not FasterWhisperEngine.available():
            # 同 open_media：裸 MessageBox 的动画被 GC 会"点了没反应"
            box = _CloseAskBox(S("缺少 faster-whisper", "faster-whisper missing"),
                             S("还没安装 faster-whisper。\n\n"
                               "在终端运行：  pip install faster-whisper\n\n"
                               "（本机已有 CTranslate2 模型，无需重新下载）\n\n"
                               "是否现在打开「设置」改用其它引擎？",
                               "faster-whisper is not installed.\n\n"
                               "Run in a terminal:  pip install faster-whisper\n\n"
                               "(Existing CTranslate2 models on this machine are reused)\n\n"
                               "Open Settings to switch engines?"), self)
            box.yesButton.setText(S("打开设置", "Open Settings"))
            box.cancelButton.setText(S("我知道了", "Got it"))
            if box.exec_():
                self.switch_to("settings")
            return

        # 模型缺失预检：首次转写要下约 1.5GB，黑盒等待是旅程最大痛点。
        # 开跑前明说，用户可选继续或先去设置换本地模型。
        # 队列模式跳过该问框：队列已经开跑，反复弹窗打断批量；下载进度
        # 本身可见，模型缺失的失败也会在单个任务上报错并续跑下一个。
        try:
            from ..core.transcriber import model_missing, model_download_size_mb
            _mdl = (self.cfg.whisper_model or "").strip() or "large-v3-turbo"
            if model_missing(_mdl) and not queue_mode:
                mb = model_download_size_mb(_mdl)
                box = _CloseAskBox(
                    S("首次使用需下载模型", "First use: model download needed"),
                    S(f"本地还没有「{_mdl}」模型（约 {mb / 1024:.1f} GB）。\n\n"
                      "首次转写会先自动下载（国内源实测约 2~5 分钟，完成后永久复用；"
                      "进度条会显示下载进度）。\n\n是否继续？",
                      f"Model 「{_mdl}」 (about {mb / 1024:.1f} GB) is not on this machine.\n\n"
                      "The first transcription downloads it automatically (about 2–5 min "
                      "on a China mirror, reused forever afterwards; progress is shown).\n\n"
                      "Continue?"), self)
                box.yesButton.setText(S("继续，先下载", "Continue, download first"))
                box.cancelButton.setText(S("先不转写", "Not now"))
                if not box.exec_():
                    return
        except Exception:
            pass    # 预检失败不拦路：让转写流程自己报错

        self._gen = getattr(self, "_gen", 0) + 1      # 代际：旧 worker 的迟到信号一律丢弃
        gen = self._gen
        self.switch_to("editor")
        self.editor._set_flow("busy", S("① 正在提取音频…", "① Extracting audio…"))
        self._begin_progress(S("正在提取音频…", "Extracting audio…"))
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
        # 用户主动取消 = 连批量队列一起停（避免"点了取消还在后台一个个跑"）。
        # 放在 worker 判空之前：队列开着但当前项恰在换挡间隙（无 worker）
        # 时点取消也必须停队，否则定时器还会拉起下一项。
        if self._queue_running():
            self._queue = []
            self._queue_active = False
            self._queue_stop_note = True
        if self._worker is None:
            if getattr(self, "_queue_stop_note", False):
                self._queue_stop_note = False
                self.editor.status.setText(
                    S("批量队列已停止，可重新开始。",
                      "Batch queue stopped. You can restart anytime."))
            return
        w = self._worker
        w.cancel()
        reap(w)
        self._worker = None
        # 释放已加载的转写模型：取消落在加载期（large-v3 冷加载 30~120s）
        # 时旧线程要等加载完才退，不释放的话用户随即换模型重开会出现
        # 两份 1.5GB 模型同时驻留（内存/显存 OOM 风险）。
        try:
            from ..core import transcriber
            transcriber.release_models()
        except Exception:
            pass
        self._end_progress()
        name = os.path.basename(self.doc.source_video) if (
            self.doc is not None and self.doc.source_video) else S("视频", "video")
        self.editor._set_flow("ready", S(f"已导入：{name}", f"Imported: {name}"))
        self.editor.status.setText(S("已取消转写，可重新开始。",
                                     "Cancelled. You can start again anytime."))
        if getattr(self, "_queue_stop_note", False):
            self._queue_stop_note = False
            self.editor.status.setText(S("批量队列已停止，可重新开始。",
                                         "Batch queue stopped. You can restart anytime."))

    def _on_progress(self, gen: int, msg: str, pct: float) -> None:
        if self._stale(gen):
            return
        # whisper 每个 segment 都发一次进度，一路刷三处控件（adjustSize 还会
        # 触发布局）。节流到 ~8 帧/秒：中间帧丢弃、最新状态存着由定时器补画，
        # 收尾帧（>=1.0 或 <0）立即上屏，不会停在半截。
        now = time.monotonic()
        final = pct is not None and (pct >= 1.0 or pct < 0)
        self._prog_pending = (msg, pct)
        if final or now - getattr(self, "_prog_ts", 0.0) >= 0.125:
            self._flush_progress()
        elif not self._prog_timer.isActive():
            self._prog_timer.start(130)

    def _flush_progress(self) -> None:
        self._prog_timer.stop()
        pend = getattr(self, "_prog_pending", None)
        if pend is None:
            return
        self._prog_pending = None
        self._prog_ts = time.monotonic()
        msg, pct = pend
        self.progressLabel.setText(msg)
        self.progressLabel.adjustSize()
        self._place_progress_label()
        self.editor.status.setText(msg)
        self.editor.set_flow_progress(msg, pct)
        # pct=0.0 时旧写法 'pct and pct >= 0' 会短路成忙碌条：首帧进度
        # （0%）显示成无限转圈。改为显式判 None。
        if pct is not None and pct >= 0:
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
            S(f"转写完成：{len(doc.cues)} 条 · 用时 {m.get('elapsed', 0)}s · "
              f"速度 {m.get('speed', 0)}x · 语言 {m.get('language', '?')} · "
              "可去「AI 纠错」修错别字。",
              f"Transcribed: {len(doc.cues)} cues · {m.get('elapsed', 0)}s · "
              f"{m.get('speed', 0)}x · language {m.get('language', '?')} · "
              "run AI Fix to polish typos."))
        self.mark_dirty()
        if not doc.cues:
            # 转写"成功"但一条都没识别出来：多半是纯静音/无语音，或语言
            # 参数选错。报"成功（0 条）"会让用户以为坏了却无从下手。
            self._warn(S("没有识别出任何字幕", "No subtitles recognized"),
                       S("音频里可能没有可识别的语音（纯静音/音乐），"
                         "或「设置 → 语言」与实际语音不符。可换语言后重试。",
                         "The audio may contain no recognizable speech (silence/music), "
                         "or Settings → Language mismatches. Try another language."))
        InfoBar.success(S("转写完成", "Transcription done"),
                        S(f"{len(doc.cues)} 条字幕（{m.get('engine', '')}）。"
                          + (f"已自动衔接 {m.get('gaps_closed', 0)} 处字幕空隙，"
                             "防止播放时闪断。" if m.get("gaps_closed") else "")
                          + "建议接着做 AI 纠错。",
                          f"{len(doc.cues)} cues ({m.get('engine', '')}). "
                          + (f"Auto-closed {m.get('gaps_closed', 0)} subtitle gaps to "
                             "prevent flicker." if m.get("gaps_closed") else "")
                          + " Next: AI Fix."),
                        parent=self, position=InfoBarPosition.TOP, duration=6000)
        # 批量队列：本条完成 → 自动把 .ssp 存到视频旁边 → 接着下一条。
        # 自动保存绝不弹框（队列模式下 getSaveFileName 会卡住整个队列）。
        if self._queue_running():
            self._queue_autosave(doc)
        self._queue_advance()

    def _queue_autosave(self, doc: CueDocument) -> None:
        """批量队列的落盘策略：优先工程已有路径；否则存到视频旁边
        <视频名>.ssp。只吞 OSError，写失败不打断队列。"""
        try:
            path = doc.path
            if not path and doc.source_video:
                path = os.path.splitext(doc.source_video)[0] + ".ssp"
            if not path:
                return
            if not path.lower().endswith(".ssp"):
                path += ".ssp"
            self._save_gen = getattr(self, "_save_gen", 0) + 1
            _atomic_write_text(path, doc.to_json())
            doc.path = path
            self._dirty = False
            self.cfg.add_recent(path)
            self.editor.status.setText(
                S(f"批量队列：已保存 {os.path.basename(path)}。",
                  f"Batch queue: saved {os.path.basename(path)}."))
        except OSError:
            pass

    def _on_transcribe_failed(self, gen: int, msg: str) -> None:
        if self._stale(gen):
            return
        cancelled = "取消" in (msg or "")
        w = self._worker
        self._worker = None
        # 必须 reap：sig_failed 发出时线程往往还没走完 finally 清理。直接丢掉
        # 引用会让 GC 在运行中析构 QThread，Qt 直接 abort（成功路径就是这么做的）。
        reap(w)
        # 失败/取消同样释放模型：加载中途炸掉（磁盘满/DLL 缺失）时实例
        # 已经驻留，不释放就等下一次转写叠加
        try:
            from ..core import transcriber
            transcriber.release_models()
        except Exception:
            pass
        self._end_progress()
        self.progressLabel.setText("")
        if self.doc is not None and self.doc.source_video:
            self.editor._set_flow("ready",
                                  S(f"已导入：{os.path.basename(self.doc.source_video)}",
                                    f"Imported: {os.path.basename(self.doc.source_video)}"))
        else:
            self.editor._set_flow("empty")
        if cancelled:
            self.editor.status.setText(S("已取消转写，可重新开始。",
                                         "Cancelled. You can start again anytime."))
            # 队列模式下的取消 = 停止整个批量（不自动续跑下一条）
            if self._queue_running():
                self._queue = []
                self._queue_active = False
                self.editor.status.setText(S("批量队列已停止，可重新开始。",
                                             "Batch queue stopped. You can restart anytime."))
            return
        self.editor.status.setText(S("转写失败。", "Transcription failed."))
        if self._queue_running():
            # 单个任务失败不拖垮整个队列：记下错误，自动续跑下一个
            self.editor.status.setText(
                S(f"转写失败（{os.path.basename(getattr(self.doc, 'source_video', '') or '')}），"
                  "队列继续处理下一个。",
                  f"Failed ({os.path.basename(getattr(self.doc, 'source_video', '') or '')}); "
                  "queue continues with the next item."))
        self._queue_advance()
        InfoBar.error(S("转写失败", "Transcription failed"), msg[:600], parent=self,
                      position=InfoBarPosition.TOP, duration=9000)

    def _begin_progress(self, msg: str) -> None:
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.progressLabel.setText(msg)
        self.progressLabel.setVisible(True)
        self.progressLabel.adjustSize()
        QTimer.singleShot(0, self.update)

    def _end_progress(self) -> None:
        self._prog_pending = None
        self._prog_timer.stop()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        self.progressLabel.setVisible(False)

    # ------------------------------------------------------------ 批量队列
    def enqueue_batch(self, paths: list) -> None:
        """多文件顺序转写队列：逐个 导入 → 转写 → 存 .ssp → 下一个。

        当前转写在跑时不打断：队列排后面，当前任务收尾时自动接上。
        已在队列里的文件去重；队列上限 200 防止误拖整个文件夹失控。
        """
        q = getattr(self, "_queue", [])
        seen = {os.path.abspath(p) for p in q}
        seen |= {os.path.abspath(self.doc.source_video) if
                 (self.doc is not None and self.doc.source_video) else ""}
        added = 0
        for p in paths:
            ap = os.path.abspath(p)
            if ap in seen or len(q) >= 200:
                continue
            q.append(ap)
            seen.add(ap)
            added += 1
        self._queue = q
        if added:
            InfoBar.success(S("已加入批量队列", "Added to batch queue"),
                            S(f"{added} 个文件排队（共 {len(q)} 个待处理），"
                              "逐个转写并保存 .ssp。可点「取消」随时停止。",
                              f"{added} file(s) queued ({len(q)} pending). "
                              "Each is transcribed and saved as .ssp. Press Cancel to stop."),
                            parent=self, position=InfoBarPosition.TOP,
                            duration=5000)
            if self._worker is None and not self._queue_running():
                QTimer.singleShot(600, self._queue_next)

    def _queue_running(self) -> bool:
        return getattr(self, "_queue_active", False)

    def _queue_next(self) -> None:
        """取下一个队列项开跑。队列空了回到普通 ready 态。"""
        q = getattr(self, "_queue", [])
        # 跳过运行中已被删掉/消失的文件
        while q and not os.path.isfile(q[0]):
            q.pop(0)
        if not q:
            self._queue_active = False
            return
        self._queue_active = True
        path = q.pop(0)
        self._queue = q
        self.open_media(path)          # 走同一套未保存确认/导入链
        if self.doc is not None and self.doc.source_video and \
                os.path.abspath(self.doc.source_video) == path and \
                self._worker is None:
            # open_media 被未保存框挡住（用户点了取消）或导入失败：
            # 队列不能卡死，跳过它继续
            QTimer.singleShot(400, self._queue_next)
            return
        if self._worker is not None:
            self.start_transcribe(queue_mode=True)

    def _queue_advance(self) -> None:
        """单个任务收尾（成功/失败/取消）后由 _on_transcribed/_failed 调。"""
        if not self._queue_running():
            return
        QTimer.singleShot(800, self._queue_next)

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
        注意：width()/availableGeometry() 都是 Qt 逻辑坐标（QT_SCALE_FACTOR
        已含 ui_scale），两边同单位直接比，不要再乘任何缩放系数。
        """
        try:
            from PyQt5.QtWidgets import QApplication
            scr = QApplication.screenAt(self.frameGeometry().center()) \
                or QApplication.primaryScreen()
            if scr is None:
                return
            avail = scr.availableGeometry()
            w = min(self.width(), avail.width())
            h = min(self.height(), avail.height())
            # 允许最小值略大于屏（极端小屏时 Qt 自行处理），这里只做温和收敛
            self.resize(max(min(900, avail.width()), w),
                        max(min(600, avail.height()), h))
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
        """跨屏/改过缩放后，恢复的位置或尺寸超出当前屏，拉回并收进主屏。

        之前只处理"完全在屏幕外"的极端情况：恢复的窗口尺寸大于可用区时
        （换屏/改 ui_scale 后常见），标题栏和底栏露在屏外，配合启动闪屏的
        切换观感就是"渲染两遍但错开"。这里一并把超大窗口收回可用区。
        """
        try:
            from PyQt5.QtWidgets import QApplication
            scr = QApplication.screenAt(self.frameGeometry().center()) \
                or QApplication.primaryScreen()
            if scr is None:
                return
            avail = scr.availableGeometry()
            geo = self.frameGeometry()
            on_screen = any(scr2.availableGeometry().intersects(geo)
                            for scr2 in QApplication.screens())
            too_big = geo.width() > avail.width() or geo.height() > avail.height()
            if on_screen and not too_big:
                return
            if too_big:
                self.resize(min(self.width(), avail.width()),
                            min(self.height(), avail.height()))
            if not on_screen:
                fg = self.frameGeometry()
                self.move(avail.center().x() - fg.width() // 2,
                          avail.center().y() - fg.height() // 2)
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
            box = _CloseAskBox(S("退出前保存？", "Save before quitting?"),
                               S("当前字幕尚未保存。", "Subtitles are not saved yet."), self)
            box.yesButton.setText(S("保存并退出", "Save and quit"))
            box.cancelButton.setText(S("不保存退出", "Quit without saving"))
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
        # 设置页"测试连接"线程可能还在 HTTP 在途（挂在 settings 页下），
        # 主窗析构会连带析构运行中的 QThread 直接 abort——先摘掉父子
        try:
            self.settings.shutdown()
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
        # 先请求三个后台任务协作取消，再统一等待：导出/纠错线程收到后会在
        # 批次边界收尾，比干等 5s 超时快得多
        try:
            if self._worker:
                self._worker.cancel()
        except RuntimeError:
            pass
        try:
            fw = getattr(self.fix, "worker", None)
            if fw is not None:
                fw.cancel()
        except RuntimeError:
            pass
        try:
            ew = getattr(self.export, "_worker", None)
            if ew is not None:
                ew.cancel()
        except RuntimeError:
            pass
        stragglers = False
        # 自动保存线程也要等：它持有 doc 引用在后台序列化，不等就 os._exit
        # 的话写盘可能落在半截（tmp+replace 保证不会截断目标文件，但还是等）
        aw = self._autosave_worker
        if aw is not None:
            try:
                if not aw.wait(1500):
                    stragglers = True
            except RuntimeError:
                pass
        # 三个线程统一走一个 6s 总预算：原来最坏 3×5s 串行等 15s，
        # 期间 UI 冻结，看起来像死机
        deadline = time.monotonic() + 6.0
        for w in (self._worker,
                  getattr(self.fix, "worker", None),
                  getattr(self.export, "_worker", None)):
            if w is None:
                continue
            try:
                remain = int((deadline - time.monotonic()) * 1000)
                if remain <= 0 or not w.wait(max(100, remain)):
                    stragglers = True
            except RuntimeError:
                pass
        # 转写线程 5s 内没收尾时 os._exit 会跳过它的 finally，临时 wav
        # （可达数百 MB）就永久留在 %TEMP%：这里兜底删掉
        tw = self._worker
        if stragglers and tw is not None:
            try:
                wav = getattr(tw, "current_wav", "")
                if wav and os.path.isfile(wav):
                    os.remove(wav)
            except OSError:
                pass
        super().closeEvent(e)
        if stragglers:
            # cancel 是协作式的：在途 HTTP 要等超时才返回，线程池线程又不是
            # daemon——解释器拆机会 join 它们，表现为"窗口关了进程还挂几分钟"，
            # 期间线程再碰已销毁的 Qt 对象就是随机 0xC0000005。
            # 此刻配置已保存、播放器已拆、窗口已关；导出走 tmp+os.replace
            # 原子落盘，强杀最多丢一个 .tmp，不丢任何已完成的数据。
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)

    def _close_save_quit(self) -> None:
        """「保存并退出」：保存成功才走关闭流程；取消保存则留在软件里。"""
        if self.save_project():
            self._close_box = None
            self._closing = True             # 别在第二次 closeEvent 又弹一次
            self.close()

    def _close_discard_quit(self) -> None:
        """「不保存退出」。用户明确不要这些内容：连恢复快照一起清掉，
        否则下次启动又弹一次「恢复未保存工程」，等于不尊重刚才的选择。"""
        try:
            from ..core import recovery
            if self.doc is not None:
                recovery.discard_snapshot(self.doc)
        except Exception:
            pass
        self._close_box = None
        self._closing = True
        self.close()


def _atomic_write_text(path: str, text: str) -> None:
    """写临时文件再 os.replace：与 config.save 同款，避免写一半崩溃留下损坏工程。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _fmt(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
