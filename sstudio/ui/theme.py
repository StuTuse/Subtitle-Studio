"""主题 / 通用小工具。"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication

from ..core.config import Config

try:
    from qfluentwidgets import Theme, setTheme, setThemeColor, ThemeColor
except Exception:  # pragma: no cover
    Theme = None


def apply_theme(cfg: Config) -> None:
    if Theme is None:
        return
    mapping = {"light": Theme.LIGHT, "dark": Theme.DARK, "auto": Theme.AUTO}
    try:
        setTheme(mapping.get(cfg.theme, Theme.AUTO))
        if cfg.accent:
            setThemeColor(ThemeColor(QColor(cfg.accent)))
    except Exception:
        pass


def ui_font(size: int = 13) -> QFont:
    for fam in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC"):
        if fam in QFontDatabase().families():
            f = QFont(fam)
            f.setPointSize(size)
            return f
    f = QFont()
    f.setPointSize(size)
    return f


def monospace(size: int = 12) -> QFont:
    for fam in ("Cascadia Mono", "Consolas", "JetBrains Mono", "Sarasa Mono SC", "Courier New"):
        if fam in QFontDatabase().families():
            f = QFont(fam)
            f.setPointSize(size)
            return f
    f = QFont("Courier New")
    f.setPointSize(size)
    return f


def open_path(path: str) -> None:
    """在资源管理器中打开文件/目录。"""
    if not path or not os.path.exists(path):
        return
    try:
        if os.path.isdir(path):
            os.startfile(path)  # type: ignore[attr-defined]
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception:
        pass


def app_dir() -> str:
    """工程根（源码或打包后的资源根）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def state_color(state: str, dark: bool) -> QColor:
    c = {
        "asr": QColor("#3a3a3a") if dark else QColor("#eef3f7"),
        "llm": QColor("#123a2a") if dark else QColor("#e6f7ec"),
        "edited": QColor("#3a3118") if dark else QColor("#fff6df"),
        "review": QColor("#3d2020") if dark else QColor("#ffeaea"),
        "confirmed": QColor("#1e2c3d") if dark else QColor("#e8f1fb"),
    }.get(state)
    return c or QColor(Qt.transparent)


def state_text(state: str) -> str:
    return {"asr": "原始", "llm": "已修正", "edited": "已编辑", "review": "待复查",
            "confirmed": "已确认"}.get(state, state or "")


def is_dark() -> bool:
    try:
        from qfluentwidgets import isDarkTheme
        return bool(isDarkTheme())
    except Exception:
        return False


def human_time(sec: float) -> str:
    sec = max(0.0, float(sec or 0))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    if h:
        return f"{h}:{m:02d}:{s:05.2f}"
    return f"{m}:{s:05.2f}"
