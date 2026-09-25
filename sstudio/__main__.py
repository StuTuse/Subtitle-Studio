"""命令行入口：``python -m sstudio`` / ``python run.py``。"""

from __future__ import annotations

import argparse
import os
import sys


def _bootstrap() -> None:
    """让 ``python sstudio/__main__.py`` 在任意工作目录都能 import sstudio。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)
    # Windows 控制台默认 GBK，打印中文/符号会抛 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _os_scale() -> float:
    """Windows 显示缩放比例（100% → 1.0，150% → 1.5）。"""
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes
        hdc = ctypes.windll.user32.GetDC(None)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(None, hdc)
        return (dpi / 96.0) if dpi else 1.0
    except Exception:
        return 1.0


def _scale_factor_for(ui_scale: float) -> float:
    """QT_SCALE_FACTOR：让有效渲染倍率（OS缩放 × factor）恰为 ui_scale。

    ui_scale 以物理像素计：1.0 = 物理 1:1，字最小最锐利；0 不调（跟随系统）。
    """
    return min(2.0, max(0.3, float(ui_scale) / _os_scale()))


def main(argv=None) -> int:
    _bootstrap()
    # 语言先于 argparse：帮助文本也是界面的一部分（SUBTITLE_STUDIO_LANG
    # 与 config.lang 双入口；config 要等 _bootstrap 后才能 import）。
    try:
        from sstudio.core.i18n import set_language as _sl, current_language as _cl
        from sstudio.core.config import Config as _C0
        _sl(getattr(_C0.load(), "lang", "zh") if os.environ.get(
            "SUBTITLE_STUDIO_LANG", "") == "" else _cl())
    except Exception:
        pass
    from sstudio.core.i18n import S
    parser = argparse.ArgumentParser(
        prog="subtitle-studio",
        description=S("视频字幕工坊：本地 Whisper 转写 + 大模型纠错 + 多格式导出",
                      "Subtitle workshop: local Whisper transcription + LLM "
                      "fixing + multi-format export"))
    parser.add_argument("file", nargs="?", help=S("要打开的视频/音频/字幕/工程文件",
                                                  "Video/audio/subtitle/project file to open"))
    parser.add_argument("--version", action="store_true", help=S("打印版本后退出",
                                                                 "Print version and exit"))
    parser.add_argument("--check", action="store_true", help=S("打印运行环境自检结果",
                                                               "Print environment self-check"))
    parser.add_argument("--headless", action="store_true",
                        help=S("无界面跑完整流水线：转写 + 纠错 + 导出（配合 --video 等）",
                               "Run the full pipeline headless: transcribe + fix + "
                               "export (with --video etc.)"))
    parser.add_argument("--video", help=S("headless 模式：视频路径",
                                          "headless mode: video path"))
    parser.add_argument("--out", help=S("headless 模式：输出文件（.srt/.txt/…）",
                                        "headless mode: output file (.srt/.txt/…)"))
    parser.add_argument("--no-fix", action="store_true", help=S("headless 模式：跳过 LLM 纠错",
                                                                "headless mode: skip LLM fixing"))
    args, extra = parser.parse_known_args(argv)

    if args.headless and extra:
        # headless 是给脚本用的：拼错的参数必须当场报错（exit 2），
        # 不能像 GUI 那样把多余项当成"要打开的文件"静默走偏
        parser.error(S("headless 模式不认识的参数：", "Unrecognized arguments in "
                       "headless mode: ") + " ".join(extra))

    if args.version:
        from sstudio import describe
        print(describe())
        return 0

    if args.check:
        from sstudio.selfcheck import run_check
        return run_check()

    if args.headless:
        from sstudio.cli_pipeline import run_pipeline
        return run_pipeline(args)

    # 运行时兜底：打包版靠 app.manifest 声明 PerMonitorV2；源码运行或
    # 旧 Python 时在这里自己声明，避免 Windows 位图拉伸导致整体发糊。
    if sys.platform == "win32":
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PerMonitorV2
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtWidgets import QApplication

    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass

    # 缩放取整必须 PassThrough：默认策略会把 133%/150% 取整成 1 或 2，
    # 和下面的 QT_SCALE_FACTOR 相乘后尺寸就"不是我们算的那个值"了。
    # 注意：PyQt5 的这个枚举挂在 QtCore.Qt 下，不在 QGuiApplication 上。
    try:
        from PyQt5.QtGui import QGuiApplication as _QGA
        _QGA.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass

    from sstudio.core.config import Config
    cfg = Config.load()

    # 界面语言：config.lang → i18n.S()。必须在任何界面构建前写入——字符串
    # 在控件构造期取定，事后改只影响之后新建的界面。（argparse 段已按
    # env/config 预置过一次；这里再以 cfg 为准对齐一次，幂等。）
    try:
        from sstudio.core.i18n import set_language
        set_language(getattr(cfg, "lang", "zh"))
    except Exception:
        pass

    # 界面缩放：见 _scale_factor_for。默认 ui_scale=1.0 → 高缩放屏上也按
    # 物理像素 1:1 渲染：清晰、紧凑，不再"放大发糊"。0 = 跟随系统缩放。
    # 必须在 QApplication 创建前写环境变量，Qt 才会读到。
    try:
        if float(cfg.ui_scale) > 0:
            os.environ["QT_SCALE_FACTOR"] = f"{_scale_factor_for(cfg.ui_scale):.3f}"
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("Subtitle Studio")
    app.setOrganizationName("SubtitleStudio")

    # 单实例：图标多点两下会同时起来几个进程，互写 config.json、弹两份首启
    # 向导。后到的把在跑的那份唤到前台，自己安静退出。守护本身起不来时
    # try_start 返回 True 放行，绝不为这个挡住正常启动。
    from sstudio.ui.single_instance import SingleInstance
    single = SingleInstance(app)
    if not single.try_start():
        return 0

    # 默认字体是 SimSun 9pt：点阵感重、发虚。统一换成雅黑 UI。
    from sstudio.ui.theme import ui_font
    app.setFont(ui_font(9))

    # 启动等待页：主窗口要构建 4 个页面 + 播放器，双击后"没反应"的观感
    # 主要来自这段构建时间。先亮 splash，边构建边推进文案。
    # splash 的几何/绘制倍率完全跟随 QT_SCALE_FACTOR（Splash 内部取实时
    # DPR），这里不再传 ui_scale —— 传了就会双重放大（历史 UI 错位 bug）。
    from sstudio import __version__
    from sstudio.ui.splash import Splash, app_icon
    app.setWindowIcon(app_icon())      # 标题栏/任务栏用同一套自绘图标
    splash = Splash(__version__)
    splash.show_splash()

    splash.show_stage(S("正在加载界面…", "Loading UI…"))
    from sstudio.ui.main_window import MainWindow

    # CUDA 预探测必须在 MainWindow/播放器（DirectShow 后端）激活之前做：
    # 媒体后端激活过的进程里 ctranslate2 的 CUDA 枚举会 access violation
    # （上游 Qt × NVIDIA 驱动 × ctranslate2 三方冲突，见 doctor.preprobe_gpu）。
    # 早期枚举一次进缓存，欢迎向导/设置页体检读缓存，永不在现场枚举。
    try:
        from sstudio.core import doctor as _doctor
        _doctor.preprobe_gpu()
    except Exception:
        pass

    splash.show_stage(S("正在初始化工作区…", "Initializing workspace…"))
    win = MainWindow(cfg)

    # ---- 启动闪烁根治（第三轮，最终方案）----
    # 前两轮（关 Mica 提前 / 底色动画静止化 / 关 blurBehind 残留）都对，
    # 但用户实测仍闪，描述精确化后定位到真正时序：主窗在 splash 底下先
    # show，splash 淡出关闭时 Windows 重算 z 序与焦点归属（Tool 窗不持有
    # 焦点，关闭瞬间才把焦点交还主窗），主窗整窗重绘一次 = "打开动画结束
    # 之后整个窗口消失再出现一下"。终案：**主窗不在 splash 底下 show**，
    # 等 splash 完全关闭的下一拍才首次 show——入场只有一次合成，没有叠层
    # 切换。show 之前仍先 process 两圈把构造期遗留的样式/排版事件吃干净。
    app.processEvents()
    app.processEvents()

    def _wake():
        # 第二实例请求唤醒：从托盘/最小化拉回前台
        try:
            if win.isMinimized():
                win.showNormal()
            win.show()
            win.raise_()
            win.activateWindow()
        except Exception:
            pass
    single.on_activate = _wake

    from PyQt5.QtGui import QGuiApplication as _QGA2

    def _reveal_main():
        """splash 完全关闭后的下一拍：主窗首次 show（唯一一次入场合成）。"""
        for _p in _QGA2.topLevelWindows():    # 强制主窗真正画出第一帧
            _p.requestUpdate()
        win.show()
        app.processEvents()
        app.processEvents()   # 让 resize/排版真正落一帧，防"半成品第一帧"

    # splash 淡出（260ms）+ 关闭 + 下一拍 → _reveal_main。
    # 兜底：即使淡出动画没跑起来，finish 内部 610ms 处也会回调一次。
    splash.finish(on_closed=_reveal_main)

    def _maybe_welcome(cfg_, win_):
        """欢迎向导（主窗 reveal 之后的下一拍才弹，见下方 singleShot 注释）。

        从原 main() 内联逻辑抽成函数以便延后执行。向导自身崩了不能连累
        主程序：退回旧的纯体检窗口，仍留线索。
        """
        try:
            from sstudio.ui.welcome_wizard import maybe_show_welcome
            if not maybe_show_welcome(cfg_, parent=win_):
                win_.hide()
                QTimer.singleShot(0, app.quit)
                return
        except Exception:
            try:
                import traceback
                from sstudio.core.config import data_dir
                with open(os.path.join(data_dir(), "crash.log"), "a",
                          encoding="utf-8") as f:
                    f.write("\n# " + S("欢迎向导异常", "Welcome wizard exception") + "\n")
                    traceback.print_exc(file=f)
            except Exception:
                pass
            try:
                from sstudio.ui.first_run_dialog import maybe_show_first_run
                if not maybe_show_first_run(cfg_, parent=win_):
                    win_.hide()
                    QTimer.singleShot(0, app.quit)
            except Exception:
                pass

    # 崩溃恢复：上次会话有未保存的工程快照 → 主动问一次要不要恢复。
    # 延后一拍弹（InfoBar/对话框要等主窗真出来）；「不恢复」只忽略这一次，
    # 快照保留到保留期结束——用户后悔还有第二次机会。
    def _offer_recovery():
        try:
            from sstudio.core import recovery as _rec
            snaps = _rec.list_snapshots()
            if not snaps:
                return
            s = snaps[0]
            from PyQt5.QtWidgets import QMessageBox
            from sstudio.core.i18n import S as _S
            src = os.path.basename(s.get("source") or "") or s["title"]
            box = QMessageBox(win)
            box.setWindowTitle(_S("恢复未保存的工程", "Restore unsaved project"))
            box.setIcon(QMessageBox.Question)
            box.setText(_S(f"检测到上次会话未保存的工程：\n{src}\n\n要恢复它吗？",
                           f"An unsaved project from the last session was found:\n"
                           f"{src}\n\nRestore it?"))
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.button(QMessageBox.Yes).setText(_S("恢复", "Restore"))
            box.button(QMessageBox.No).setText(_S("暂不", "Not now"))
            if box.exec_() == QMessageBox.Yes:
                doc = _rec.load_snapshot(s["file"])
                if doc is None:
                    QMessageBox.warning(win, _S("恢复失败", "Restore failed"),
                                        _S("快照文件已损坏，无法读取。",
                                           "The snapshot file is corrupt and cannot be read."))
                    return
                doc.path = s.get("path") or ""
                if doc.source_video and not os.path.isfile(doc.source_video):
                    fixed = os.path.join(os.path.dirname(s["file"]),
                                         os.path.basename(doc.source_video))
                    if os.path.isfile(fixed):
                        doc.source_video = fixed
                win.doc = doc
                win.editor.set_document(doc)
                win._dirty = True
                win.mark_dirty()
                win.switch_to("editor")
                win._update_title()
        except Exception:
            pass
    QTimer.singleShot(400, _offer_recovery)

    # 首次使用：欢迎向导（选外观 → 连模型 → 环境体检），完成写 setup_done=1。
    # 之后启动直接进主界面；体检可从设置页随时重开。
    # 返回 False = 必需组件缺失且用户点了"退出程序"，此时不能再进主界面。
    # 注意：主窗 reveal 由 splash 的 on_closed 回调触发（splash 淡出 260ms
    # +一拍），而向导在 main() 里同步 exec_ —— 它构造时主窗可能尚未 show。
    # 向导 parent 到隐藏主窗没问题（QDialog 模态独立显示），但向导**关闭
    # 之后**主窗才第一次出现会显得"点完成没反应"。所以向导也要等主窗
    # reveal 后再弹：统一挂到下一拍定时器，reveal 时间点 ≈ splash 关闭，
    # 400ms 定时器晚于它，时序安全。
    QTimer.singleShot(400, lambda: _maybe_welcome(cfg, win))

    target = args.file or (extra[0] if extra else "")
    if target:
        if os.path.isfile(target):
            QTimer.singleShot(250, lambda: win._load_any(os.path.abspath(target)))
        else:
            # 拖到图标/命令行给的文件不存在：GUI 静默忽略会让人以为
            # "程序坏了"。明确弹窗说明；批处理误传路径也能从控制台看到。
            from PyQt5.QtWidgets import QMessageBox

            def _warn_missing():
                try:
                    from sstudio.core.i18n import S as _S
                    QMessageBox.warning(win, _S("文件不存在", "File not found"),
                                        _S(f"找不到要打开的文件：\n{target}",
                                           f"Cannot find the file to open:\n{target}"))
                except Exception:
                    pass
            QTimer.singleShot(250, _warn_missing)

    # 运行期兜底：启动段的异常由 run.py 接住，但进入事件循环之后（点按钮、
    # 加载文件回调里）抛出的异常不走那条路径。打包版是窗口程序、没有控制台，
    # 用户只会看到窗口突然消失，什么线索都不剩。这里挂一个钩子，至少把完整
    # traceback 落到 crash.log，并提示日志位置。
    from sstudio.core.config import data_dir as _dd
    _crash_log = os.path.join(_dd(), "crash.log")

    def _append_crash(title: str, text: str) -> None:
        # 追加而不是覆盖：连续两次崩溃时上一次的现场还在，别抹掉
        try:
            import datetime as dt
            with open(_crash_log, "a", encoding="utf-8") as f:
                f.write(f"\n# {title}  {dt.datetime.now().isoformat(timespec='seconds')}\n")
                f.write(text)
        except Exception:
            pass

    def _hook(etype, value, tb):
        try:
            # pythonw/windowed 下 stderr 可能为 None：print_exception 抛
            # AttributeError 会把 _hook 本身变成二次异常、丢掉落盘和弹窗
            traceback.print_exception(etype, value, tb)
        except Exception:
            pass
        try:
            _append_crash(S("运行期异常", "Runtime exception"), traceback.format_exc())
            try:
                from PyQt5.QtWidgets import QMessageBox
                from sstudio.core.i18n import S as _S
                QMessageBox.critical(win if win.isVisible() else None,
                                     _S("Subtitle Studio 遇到问题",
                                        "Subtitle Studio ran into a problem"),
                                     _S("操作触发了一个错误，详情已保存到：\n\n",
                                        "An operation triggered an error; details "
                                        "were saved to:\n\n") + _crash_log)
            except Exception:
                pass
        except Exception:
            pass
    sys.excepthook = _hook

    def _unraisable_hook(ua):
        # 解释器拆机阶段 Qt 调不进 Python 的异常走 unraisablehook，此前无人接
        try:
            import traceback
            from sstudio.core.i18n import S as _S
            obj = getattr(ua, "object", None)
            exc = getattr(ua, "exc_value", None)
            _append_crash(_S("析构期异常", "Exception during teardown"),
                          "".join(
                              traceback.format_exception(type(exc), exc, getattr(exc, "__traceback__", None))
                          ) if exc else _S(f"{ua!r}  (对象: {obj!r})\n",
                                           f"{ua!r}  (object: {obj!r})\n"))
        except Exception:
            pass
    sys.unraisablehook = _unraisable_hook

    # Qt 侧消息（qWarning/qCritical）也要落盘：窗口 exe 没有控制台，
    # "DirectShow player service not found" 这类 0xC0000005 前兆只出现在
    # 这里，不接住的话 crash.log 里一个字都不会有
    try:
        from PyQt5.QtCore import qInstallMessageHandler

        def _qt_msg(mode, ctx, message):
            try:
                label = {0: "DEBUG", 1: "WARN", 2: "CRIT", 3: "FATAL", 4: "INFO"}.get(
                    int(mode), "MSG")
                # DEBUG 级不落盘：长会话里每秒一条的 tick 警告会把 crash.log
                # 撑到几十万行且毫无诊断价值；诊断下限取 WARN。
                if int(mode) <= 0:
                    return
                _append_crash(f"Qt {label}",
                              f"{message}  (文件 {ctx.file}:{ctx.line})\n")
            except Exception:
                pass
        qInstallMessageHandler(_qt_msg)
    except Exception:
        pass

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
