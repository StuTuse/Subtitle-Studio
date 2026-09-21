"""时间轴：把字幕画成横向色块，支持点击定位、拖动播放头、框选删除。"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPolygon
from PyQt5.QtWidgets import QSizePolicy, QWidget

from ..core.model import Cue, CueDocument, sec_to_ts
from .theme import is_dark


class Timeline(QWidget):
    seek_requested = pyqtSignal(float)
    cue_clicked = pyqtSignal(int)
    cue_range = pyqtSignal(int, int)     # 框选 (start_idx, end_idx)

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

    # ------------------------------------------------------------ API
    def set_document(self, doc: Optional[CueDocument]) -> None:
        self.doc = doc
        if doc:
            self.duration = max(1.0, doc.duration or doc.end_time or 1.0)
        self.update()

    def set_position(self, sec: float) -> None:
        if abs(sec - self.position) < 0.01:
            return
        self.position = sec
        self.update()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        dark = is_dark()
        bg = QColor("#1f1f1f") if dark else QColor("#f3f3f3")
        track = QColor("#2b2b2b") if dark else QColor("#e8e8e8")
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRect(0, 0, w, h)

        bar_top, bar_h = 22, h - 34
        p.setBrush(track)
        p.drawRoundedRect(0, bar_top, w, bar_h, 4, 4)

        if not self.doc or not self.doc.cues:
            p.setPen(QPen(QColor("#8a8a8a")))
            f = QFont()
            f.setPointSize(9)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter, "载入视频并完成转写后，这里会显示字幕时间轴")
            p.end()
            return

        dur = max(1.0, self.duration)
        # 字幕色块
        colors = {
            "asr": QColor("#5b9bd5") if dark else QColor("#8fbfe8"),
            "llm": QColor("#3aa76d") if dark else QColor("#7fd3a2"),
            "edited": QColor("#d8a13a") if dark else QColor("#f0c96a"),
            "review": QColor("#d13438") if dark else QColor("#f08a8a"),
            "confirmed": QColor("#4b7bb5") if dark else QColor("#7fa8dc"),
        }
        for i, c in enumerate(self.doc.cues):
            x0 = int(c.start / dur * w)
            x1 = int(c.end / dur * w)
            col = colors.get(c.state, colors["asr"])
            if i == self._sel:
                col = col.lighter(135)
                p.setPen(QPen(QColor("#ffb900"), 1.4))
            else:
                p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRect(max(0, x0), bar_top + 2, max(1, x1 - x0), bar_h - 4)

        # 刻度
        p.setPen(QPen(QColor("#9a9a9a")))
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

        # 播放头
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
                self.cue_clicked.emit(hit)
        self.seek_requested.emit(self._drag_start)
        self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press_x is not None and e.buttons() & Qt.LeftButton:
            if abs(e.x() - self._press_x) > 4:
                self.seek_requested.emit(self._sec_at(e.x()))

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._press_x is not None and self.doc and abs(e.x() - self._press_x) > 6:
            a, b = sorted([self._drag_start or 0.0, self._sec_at(e.x())])
            idx = [i for i, c in enumerate(self.doc.cues) if c.start < b and c.end > a]
            if idx:
                self.cue_range.emit(idx[0], idx[-1])
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
