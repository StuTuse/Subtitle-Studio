"""Subtitle Studio 启动脚本。

    python run.py                打开图形界面
    python run.py 某视频.mp4      直接载入
    python run.py --check        环境自检

崩溃兜底：打包版是窗口程序（无控制台），一旦抛异常只会留下一个 PyInstaller
黑窗口，用户完全无法反馈问题。这里把完整 traceback 落到 SSData\\crash.log，
并弹窗告知路径。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _crash_dir() -> str:
    """崩溃日志目录：与程序数据目录一致，源码运行则落在项目下。"""
    try:
        from sstudio.core.config import data_dir
        return data_dir()
    except Exception:
        home = os.environ.get("SUBTITLE_STUDIO_HOME")
        if home:
            return home
        if getattr(sys, "frozen", False):
            return os.path.join(os.path.dirname(sys.executable), "SSData")
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "SSData")


def _report(exc: BaseException) -> None:
    import datetime as dt
    import traceback

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        from sstudio.version import describe
        head = f"# {describe()}\n"
    except Exception:
        head = "# (版本信息不可用)\n"
    path = "<无法写入>"
    try:
        d = _crash_dir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "crash.log")
        # 追加而不是覆盖：连续两次启动崩溃时上一次的现场还在
        # （与 __main__ 的 _append_crash 同款约定）
        with open(path, "a", encoding="utf-8") as f:
            f.write(head + f"# {dt.datetime.now().isoformat(timespec='seconds')}\n"
                    f"# Python {sys.version.split()[0]}  "
                    f"{'打包版' if getattr(sys, 'frozen', False) else '源码'}\n\n{text}")
    except Exception:
        pass

    # pythonw / windowed 模式 sys.stderr 可能为 None：print 会抛
    # AttributeError，把本该温和的崩溃报告变成二次异常（弹窗也没了）
    try:
        print(text, file=sys.stderr)
    except (AttributeError, ValueError, OSError):
        pass
    # 命令行/自检/无界面模式下不能弹窗，会把脚本卡死
    blocking = ("--headless", "--check", "--version")
    if not any(a in sys.argv for a in blocking):
        try:
            # 不依赖界面库：界面本身就是崩因的情况也要能报出来
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                f"程序启动时发生错误，详情已保存到：\n\n{path}\n\n{text[:900]}",
                "Subtitle Studio 需要修复", 0x10)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        from sstudio.__main__ import main
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as e:              # noqa: BLE001 — 兜底必须兜住一切
        _report(e)
        raise SystemExit(1)
