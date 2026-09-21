"""主题 / 通用小工具。"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPalette
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
    # qfluentwidgets 只给自家控件换肤；原生 QTableWidget / QPlainTextEdit /
    # 滚动条仍停留在系统（浅色）调色板，暗色下出现"亮块刺眼、灰底灰字"。
    # 这里给 QApplication 整体配一套暗色调色板，两种皮肤才真正一致。
    try:
        _apply_app_palette(is_dark())
    except Exception:
        pass


def _apply_app_palette(dark: bool) -> None:
    """暗色：Fusion 风格调色板（窗口 #202020 系）；浅色：恢复系统默认。"""
    app = QApplication.instance()
    if app is None:
        return
    if not dark:
        app.setPalette(app.style().standardPalette())
        return
    p = QPalette()
    window   = QColor("#202020")   # 窗口底
    base     = QColor("#1b1b1b")   # 输入框/表格底（略深于窗口，层次感）
    alt      = QColor("#242424")   # 表格隔行
    text     = QColor("#e6e6e6")
    dim_text = QColor("#9a9a9a")
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.ToolTipBase, QColor("#2d2d2d"))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#2b2b2b"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.Highlight, QColor("#2f6db3"))     # 选中行/选中文字底
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, QColor("#6cb2f0"))
    p.setColor(QPalette.Light, QColor("#3a3a3a"))
    p.setColor(QPalette.Mid, QColor("#333333"))
    p.setColor(QPalette.Dark, QColor("#151515"))
    try:
        p.setColor(QPalette.PlaceholderText, dim_text)
    except Exception:
        pass
    for role, c in ((QPalette.WindowText, QColor("#6f6f6f")),
                    (QPalette.Text, QColor("#6f6f6f")),
                    (QPalette.ButtonText, QColor("#6f6f6f"))):
        p.setColor(QPalette.Disabled, role, c)
    app.setPalette(p)


def ui_font(size: int = 13) -> QFont:
    for fam in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC"):
        if fam in QFontDatabase().families():
            f = QFont(fam)
            f.setPointSize(size)
            _crisp(f)
            return f
    f = QFont()
    f.setPointSize(size)
    _crisp(f)
    return f


def _crisp(f: QFont) -> None:
    """小字号 CJK 更清晰：全 hinting + 抗锯齿。"""
    try:
        f.setHintingPreference(QFont.PreferFullHinting)
        f.setStyleStrategy(QFont.PreferAntialias)
    except Exception:
        pass


def monospace(size: int = 12) -> QFont:
    for fam in ("Cascadia Mono", "Consolas", "JetBrains Mono", "Sarasa Mono SC", "Courier New"):
        if fam in QFontDatabase().families():
            f = QFont(fam)
            f.setPointSize(size)
            _crisp(f)
            return f
    f = QFont("Courier New")
    f.setPointSize(size)
    _crisp(f)
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


def state_color(state: str, dark: bool) -> QColor:
    """字幕行整行底色（按状态淡染）。"""
    c = {
        # 暗色底提亮一档并与 base (#1b1b1b) 拉开，行染才看得出来
        "asr":       QColor("#333c48") if dark else QColor("#eef3f7"),
        "llm":       QColor("#17382b") if dark else QColor("#e6f7ec"),
        "edited":    QColor("#3c341d") if dark else QColor("#fff6df"),
        "review":    QColor("#432629") if dark else QColor("#ffeaea"),
        "confirmed": QColor("#24374f") if dark else QColor("#e8f1fb"),
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
