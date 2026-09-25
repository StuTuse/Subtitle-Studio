"""主题 / 通用小工具。"""

from __future__ import annotations

import os
import subprocess
import sys

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
    invalidate_theme_cache()       # setTheme 之后缓存作废，is_dark() 重新探测
    # accent 供 accent_hex() 全局取用（不维持对窗口的强引用）
    try:
        app = QApplication.instance()
        if app is not None:
            app._ss_cfg_ref = cfg
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
    p.setColor(QPalette.Highlight, QColor(accent_hex()))   # 选中行/选中文字底
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


def crisp(f: QFont) -> None:
    """小字号 CJK 更清晰：全 hinting + 抗锯齿。公开接口（编辑区等就地调字号的场景用）。"""
    _crisp(f)


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


# 字号档位表：全应用就地调字号一律按档取，不再散落 setPointSize 魔法数。
# qfluentwidgets 的 BodyLabel/CaptionLabel 等已自带口径，这里只管手工调的。
FONT_HERO = 14      # 页面 hero 标题
FONT_EDIT = 10.5    # 字幕编辑区正文（大半号，逐字校对不费眼）
FONT_BADGE = 12     # 徽章/标记（体检行 ✓✕⚠ 等）


def hero_font() -> QFont:
    f = ui_font(FONT_HERO)
    f.setBold(True)
    return f


def edit_font() -> QFont:
    f = ui_font(int(FONT_EDIT))
    f.setPointSizeF(FONT_EDIT)      # 保住半号
    return f


def badge_font() -> QFont:
    f = ui_font(FONT_BADGE)
    f.setBold(True)
    return f


# ------------------------------------------------------------ 布局常量
# 各页统一规格：改一处全局生效，页面里不再散落魔法数。
PAGE_MARGINS = (28, 20, 28, 20)     # 页面四边留白
PAGE_SPACING = 14                   # 页面纵向行距
CARD_MARGINS = (18, 16, 18, 16)     # CardWidget 内边距
CARD_SPACING = 10                   # 卡片内纵向行距
PRIMARY_MIN_W = 160                 # 主操作按钮最小宽


def apply_card_margins(v) -> None:
    """统一卡片内边距：v 是 QVBoxLayout/QHBoxLayout 均可。"""
    v.setContentsMargins(*CARD_MARGINS)
    v.setSpacing(CARD_SPACING)


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
    from ..core.i18n import S
    return {"asr": S("原始", "ASR"), "llm": S("已修正", "Fixed"),
            "edited": S("已编辑", "Edited"), "review": S("待复查", "Review"),
            "confirmed": S("已确认", "Confirmed")}.get(state, state or "")


# 语义状态色：同一个色值不可能在深浅两种底上都达标（实测 #1a7f37 浅底
# 5.08:1 但深底只有 3.39:1，红 #c42b1c 同理），所以按主题各取一档，
# 让"成功/失败/警告"在两种皮肤下都达到 WCAG AA。
_STATE_HEX = {
    "ok":   ("#1a7f37", "#4ac26b"),   # 浅底 / 深底
    "err":  ("#c42b1c", "#ff8a8a"),
    "warn": ("#b8860b", "#e0a83c"),
    "dim":  ("#8a8a8a", "#9a9a9a"),
}


def status_hex(kind: str = "ok") -> str:
    """取当前主题下对比度达标的状态色 hex（供 setStyleSheet 用）。"""
    pair = _STATE_HEX.get(kind, _STATE_HEX["dim"])
    return pair[1] if is_dark() else pair[0]


def _span(kind: str, text: str) -> str:
    return f"<span style='color:{status_hex(kind)}'>{text}</span>"


def ok_span(text: str) -> str:
    return _span("ok", text)


def err_span(text: str) -> str:
    return _span("err", text)


def warn_span(text: str) -> str:
    return _span("warn", text)


def dim_span(text: str) -> str:
    return _span("dim", text)


_is_dark_cache: Optional[bool] = None
_is_dirty = True


def is_dark() -> bool:
    """当前是否暗色主题。

    qfluentwidgets 的 isDarkTheme() 每次 import + 查询，此前被表格渲染
    和 LLM 流式回填逐行调用（5000 条纠错 = 上万次）。主题切换由
    invalidate_theme_cache() 主动通知，这里读缓存即可。
    """
    global _is_dark_cache, _is_dirty
    if _is_dirty or _is_dark_cache is None:
        try:
            from qfluentwidgets import isDarkTheme
            _is_dark_cache = bool(isDarkTheme())
        except Exception:
            _is_dark_cache = False
        _is_dirty = False
    return _is_dark_cache


def invalidate_theme_cache() -> None:
    """主题切换后调用：下一次 is_dark() 重新探测。"""
    global _is_dirty
    _is_dirty = True


def accent_hex() -> str:
    """主题强调色（cfg.accent，深色下提亮一档保证可读）。

    全应用选中态的统一来源：主窗调色板、数值控件弹出列表选中项、
    欢迎向导的进度点/卡片描边都从这里取，避免散落的 #2f6db3 与主题
    青色 (#0aa2c0) 各画各的。
    """
    c = "#0aa2c0"
    try:
        app = QApplication.instance()
        cfg = getattr(app, "_ss_cfg_ref", None) if app else None
        if cfg and getattr(cfg, "accent", ""):
            c = cfg.accent
    except Exception:
        pass
    if is_dark():
        try:
            qc = QColor(c)
            h, s, v, _ = qc.getHsvF()
            qc.setHsvF(h, s * 0.85, min(1.0, v + 0.18))
            c = qc.name()
        except Exception:
            c = "#37c3dd"
    return c


def human_time(sec: float) -> str:
    sec = max(0.0, float(sec or 0))
    # 先四舍五入到百分秒再拆位：否则 3599.999 会显示成 "59:60.00"
    total = round(sec, 2)
    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = total - h * 3600 - m * 60
    if s >= 60:
        s -= 60
        m += 1
    if h:
        return f"{h}:{m:02d}:{s:05.2f}"
    return f"{m}:{s:05.2f}"
