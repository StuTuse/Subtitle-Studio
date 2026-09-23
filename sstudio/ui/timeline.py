"""时间轴：把字幕画成横向色块，支持点击定位、拖动播放头、框选删除。"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygon
from PyQt5.QtWidgets import QSizePolicy, QWidget

from ..core.model import CueDocument, sec_to_ts
from .theme import is_dark


class Timeline(QWidget):
    seek_requested = pyqtSignal(float)
    cue_clicked = pyqtSignal(int)
    cue_range = pyqtSignal(list)         # 框选命中的完整行列表（含空隙剔除）

    HEIGHT = 74

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.doc: Optional[CueDocument] = None
        self.position = 0.0
        self.duration = 1.0
        self._sel: Optional[int] = None
        self._press_x: Optional[int] = None
        self._drag_start: Optional[float] = None
        self.setMinimumHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        # 静态层缓存：背景+轨道+全部字幕色块+刻度。播放头每帧移动只需重画
        # 一根线，不必重跑几千条色块的循环（实测 8000 条 16.8ms/帧 → <1ms）。
        self._cache: Optional[QPixmap] = None
        self._cache_key = None
        self._content_token = 0       # 字幕内容变更计数（含时间），缓存据此失效

    # ------------------------------------------------------------ API
    def set_document(self, doc: Optional[CueDocument]) -> None:
        self.doc = doc
        if doc:
            self.duration = max(1.0, doc.duration or doc.end_time or 1.0)
        self.content_changed()

    def content_changed(self) -> None:
        """字幕内容/时间变了：递增 token。paintEvent 比对 key 时才真正
        重建 pixmap——把"清缓存"和"重建"分开，避免重建本身又把 key 刷新、
        旧内容从此赖在缓存里不走的死循环。"""
        self._content_token += 1
        self._cache = None
        self._repaint()

    def set_position(self, sec: float) -> None:
        if abs(sec - self.position) < 0.01:
            return
        self.position = sec
        # 播放头移动不算内容变化：走 _repaint，保留静态层缓存
        self._repaint()

    def update(self) -> None:  # noqa: A003
        # 外部调 update() 都表示"字幕内容变了"：与 content_changed() 等价。
        # 不清 pixmap、只递增 token——曾被滥用为"重画请求"，LLM 纠错期间
        # 每个 position tick 都把缓存清掉，几千条色块以 10Hz 重跑，
        # 缓存形同虚设。真正的内容变化走 content_changed()。
        self._content_token += 1
        self._repaint()

    def _repaint(self) -> None:
        super().update()

    # ------------------------------------------------------------ 绘制
    def _build_static(self, w: int, h: int, dark: bool) -> QPixmap:
        """背景+轨道+字幕色块+刻度——只有内容/尺寸/主题变了才重画这一层。"""
        dpr = self.devicePixelRatioF()
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor("#1f1f1f") if dark else QColor("#f3f3f3")
        track = QColor("#2b2b2b") if dark else QColor("#e8e8e8")
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRect(0, 0, w, h)

        bar_top, bar_h = 22, h - 34
        p.setBrush(track)
        p.drawRoundedRect(0, bar_top, w, bar_h, 4, 4)

        if not self.doc or not self.doc.cues:
            p.setPen(QPen(QColor("#a8a8a8") if dark else QColor("#8a8a8a")))
            f = QFont()
            f.setPointSize(9)
            p.setFont(f)
            p.drawText(QRect(0, 0, w, h),
                       Qt.AlignCenter, "载入视频并完成转写后，这里会显示字幕时间轴")
            p.end()
            return pm

        dur = max(1.0, self.duration)
        # 字幕色块（暗色整体提亮，与 #1f1f1f 底拉开）
        colors = {
            "asr": QColor("#6faee0") if dark else QColor("#8fbfe8"),
            "llm": QColor("#43c283") if dark else QColor("#7fd3a2"),
            "edited": QColor("#e8b04c") if dark else QColor("#f0c96a"),
            "review": QColor("#f2606a") if dark else QColor("#f08a8a"),
            "confirmed": QColor("#5e93d6") if dark else QColor("#7fa8dc"),
        }
        for c in self.doc.cues:
            x0 = int(c.start / dur * w)
            x1 = int(c.end / dur * w)
            p.setPen(Qt.NoPen)
            p.setBrush(colors.get(c.state, colors["asr"]))
            p.drawRect(max(0, x0), bar_top + 2, max(1, x1 - x0), bar_h - 4)

        # 刻度（暗色下时间文字用亮灰，浅灰在深底里看不清）
        p.setPen(QPen(QColor("#b8b8b8") if dark else QColor("#8a8a8a")))
        f = QFont()
        f.setPointSize(8)
        p.setFont(f)
        step = _nice_step(dur, w)
        t = 0.0
        while t <= dur:
            x = int(t / dur * w)
            p.drawLine(x, bar_top + bar_h, x, bar_top + bar_h + 5)
            if x + 30 < w:
                p.drawText(x + 2, h - 3, sec_to_ts(t, millis=False)[3:])
            t += step
        p.end()
        return pm

    def paintEvent(self, e) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        dark = is_dark()
        dur = max(1.0, self.duration)
        # 内容 token 是缓存失效的唯一权威：任何一次 resize/重 polish 引发的
        # update() 只递增 token 不清 pixmap；只有 token/尺寸/主题真变了才重建。
        # 旧实现"update() 清缓存 + paintEvent 重建后写回 key"会互相把 key
        # 刷新，改了 cues 但没调 update() 的路径（split/merge/apply_llm_text）
        # 色块就一直停在旧内容。
        key = (self._content_token, w, h, dark, dur, id(self.doc))
        if self._cache is None or self._cache_key != key:
            self._cache = self._build_static(w, h, dark)
            self._cache_key = key

        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)

        bar_top, bar_h = 22, h - 34
        # 选中高亮与播放头是每帧都可能动的——画在缓存层之上，
        # 跟随播放换行时只走这几笔，不必重建几千条色块。
        i = self._sel
        if self.doc and i is not None and 0 <= i < len(self.doc.cues):
            c = self.doc.cues[i]
            x0 = int(c.start / dur * w)
            x1 = int(c.end / dur * w)
            p.setPen(QPen(QColor("#ffb900"), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawRect(max(0, x0), bar_top + 2, max(1, x1 - x0), bar_h - 4)

        px = int(self.position / dur * w)
        p.setPen(QPen(QColor("#ffb900"), 2))
        p.drawLine(px, 8, px, h - 8)
        p.setBrush(QColor("#ffb900"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygon([QPoint(px - 5, 6), QPoint(px + 5, 6), QPoint(px, 15)]))
        p.end()

    # ------------------------------------------------------------ 交互
    def _sec_at(self, x: int) -> float:
        if self.width() <= 0:
            return 0.0
        return max(0.0, min(self.duration, x / self.width() * self.duration))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.LeftButton:
            return
        self._press_x = e.x()
        self._drag_start = self._sec_at(e.x())
        if self.doc:
            hit = self._hit(e.x())
            self._sel = hit
            if hit is not None:
                # 命中色块只发选中：cue_clicked → _select_row → seek(cue.start)
                # 已会驱动播放，再发 seek_requested(点击点) 会把两种语义
                # 互相覆盖（选中一条却播到点击的中间位置）
                self.cue_clicked.emit(hit)
            else:
                self.seek_requested.emit(self._drag_start)
        else:
            self.seek_requested.emit(self._drag_start)
        self._repaint()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press_x is not None and e.buttons() & Qt.LeftButton:
            if abs(e.x() - self._press_x) > 4:
                self.seek_requested.emit(self._sec_at(e.x()))

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._press_x is not None and self.doc and abs(e.x() - self._press_x) > 6:
            a, b = sorted([self._drag_start or 0.0, self._sec_at(e.x())])
            idx = [i for i, c in enumerate(self.doc.cues) if c.start < b and c.end > a]
            if idx:
                # 传完整命中列表：按 (首,尾) 区间选会把空隙里没覆盖到的
                # 字幕一并选中，后续删除/移动误伤
                self.cue_range.emit(idx)
        self._press_x = None
        self._drag_start = None

    def _hit(self, x: int) -> Optional[int]:
        if not self.doc:
            return None
        t = self._sec_at(x)
        for i, c in enumerate(self.doc.cues):
            if c.start <= t <= c.end:
                return i
        return None


def _nice_step(dur: float, width: int) -> float:
    target = max(1.0, dur / max(2.0, width / 90.0))
    for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600):
        if s >= target:
            return s
    return 3600.0
