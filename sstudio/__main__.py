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
    parser = argparse.ArgumentParser(
        prog="subtitle-studio",
        description="视频字幕工坊：本地 Whisper 转写 + 大模型纠错 + 多格式导出")
    parser.add_argument("file", nargs="?", help="要打开的视频/音频/字幕/工程文件")
    parser.add_argument("--version", action="store_true", help="打印版本后退出")
    parser.add_argument("--check", action="store_true", help="打印运行环境自检结果")
    parser.add_argument("--headless", action="store_true",
                        help="无界面跑完整流水线：转写 + 纠错 + 导出（配合 --video 等）")
    parser.add_argument("--video", help="headless 模式：视频路径")
    parser.add_argument("--out", help="headless 模式：输出文件（.srt/.txt/…）")
    parser.add_argument("--no-fix", action="store_true", help="headless 模式：跳过 LLM 纠错")
    args, extra = parser.parse_known_args(argv)

    if args.headless and extra:
        # headless 是给脚本用的：拼错的参数必须当场报错（exit 2），
        # 不能像 GUI 那样把多余项当成"要打开的文件"静默走偏
        parser.error("headless 模式不认识的参数：" + " ".join(extra))

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
    # splash 的物理尺寸取有效渲染倍率（ui_scale，0=跟随系统缩放）。
    from sstudio import __version__
    from sstudio.ui.splash import Splash, app_icon
    app.setWindowIcon(app_icon())      # 标题栏/任务栏用同一套自绘图标
    try:
        eff_scale = float(cfg.ui_scale) or _os_scale()
    except Exception:
        eff_scale = 1.0
    splash = Splash(__version__, scale=eff_scale)
    splash.show_splash()

    splash.show_stage("正在加载界面…")
    from sstudio.ui.main_window import MainWindow

    splash.show_stage("正在初始化工作区…")
    win = MainWindow(cfg)

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

    win.show()
    from PyQt5.QtGui import QGuiApplication as _QGA2
    for _p in _QGA2.topLevelWindows():        # 强制主窗口真正画出第一帧
        _p.requestUpdate()
    app.processEvents()
    splash.finish()

    # 首次使用：弹环境体检（检查必要组件，缺啥可一键补装）。只在首启自动弹。
    # 返回 False = 必需组件缺失且用户点了"退出程序"，此时不能再进主界面。
    try:
        from sstudio.ui.first_run_dialog import maybe_show_first_run
        if not maybe_show_first_run(cfg, parent=win):
            win.hide()
            return 0
    except Exception:
        # 向导自身崩了不能连累主程序，但也不能一点线索都不留
        try:
            import traceback
            from sstudio.core.config import data_dir
            with open(os.path.join(data_dir(), "crash.log"), "a",
                      encoding="utf-8") as f:
                f.write("\n# 首启体检窗口异常\n")
                traceback.print_exc(file=f)
        except Exception:
            pass

    target = args.file or (extra[0] if extra else "")
    if target and os.path.isfile(target):
        QTimer.singleShot(250, lambda: win._load_any(os.path.abspath(target)))

    # 运行期兜底：启动段的异常由 run.py 接住，但进入事件循环之后（点按钮、
    # 加载文件回调里）抛出的异常不走那条路径。打包版是窗口程序、没有控制台，
    # 用户只会看到窗口突然消失，什么线索都不剩。这里挂一个钩子，至少把完整
    # traceback 落到 crash.log，并提示日志位置。
    def _hook(etype, value, tb):
        import traceback
        traceback.print_exception(etype, value, tb)
        try:
            import datetime as dt
            from sstudio.core.config import data_dir
            p = os.path.join(data_dir(), "crash.log")
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"# 运行期异常  {dt.datetime.now().isoformat(timespec='seconds')}\n\n")
                traceback.print_exception(etype, value, tb, file=f)
            try:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.critical(win if win.isVisible() else None,
                                     "Subtitle Studio 遇到问题",
                                     "操作触发了一个错误，详情已保存到：\n\n" + p)
            except Exception:
                pass
        except Exception:
            pass
    sys.excepthook = _hook

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
