"""启动等待页：应用名 + 版本号 + 自绘小图标 + 不确定进度动画。

不依赖任何外部图片资源：图标是 QPainter 画出来的（圆角方块 + 两条字幕横杠），
打包时不需要额外的 .ico/.png。窗口无边框、置顶、不占任务栏，主窗口画出
第一帧后淡出关闭。
"""

from __future__ import annotations

import math

from PyQt5.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF,
                          Qt, QTimer, pyqtProperty)
from PyQt5.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient)
from PyQt5.QtWidgets import QApplication, QWidget


def paint_app_icon(p: QPainter, rect: QRectF) -> None:
    """应用图标：一块"视频屏"里压着两条字幕杠 + 播放三角。

    独立成函数，将来做窗口图标 / 关于页 / .ico 生成器都能复用同一份造型。
    """
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    radius = w * 0.22

    # 屏体：靛紫→亮蓝斜向渐变（区别于普通"纯蓝方块"）
    body = QPainterPath()
    body.addRoundedRect(x, y, w, h, radius, radius)
    g = QLinearGradient(x, y, x + w, y + h)
    g.setColorAt(0.0, QColor(122, 92, 255))     # 靛紫
    g.setColorAt(0.55, QColor(74, 108, 255))
    g.setColorAt(1.0, QColor(42, 110, 232))     # 亮蓝
    p.fillPath(body, QBrush(g))

    # 屏内上半：暗色"画面区"，模拟播放器黑色遮幅
    screen = QPainterPath()
    screen.addRoundedRect(x + w * 0.10, y + h * 0.12, w * 0.80, h * 0.42,
                          w * 0.07, w * 0.07)
    p.fillPath(screen, QColor(10, 14, 30, 120))

    # 播放三角：画面区正中，半透明白
    cx, cy = x + w * 0.5, y + h * 0.33
    tw = w * 0.16
    tri = QPainterPath()
    tri.moveTo(cx - tw * 0.42, cy - tw * 0.52)
    tri.lineTo(cx - tw * 0.42, cy + tw * 0.52)
    tri.lineTo(cx + tw * 0.62, cy)
    tri.closeSubpath()
    p.fillPath(tri, QColor(255, 255, 255, 215))

    # 底部字幕杠：经典"一长一短居中"的 CC 造型
    bh = max(2.0, h * 0.085)
    for k, (wfrac, alpha) in enumerate(((0.56, 245), (0.38, 200))):
        bw = w * wfrac
        rr = QPainterPath()
        rr.addRoundedRect(x + (w - bw) / 2, y + h * (0.63 + k * 0.155), bw, bh,
                          bh / 2, bh / 2)
        p.fillPath(rr, QColor(255, 255, 255, alpha))

    # 顶部高光描边：一点玻璃质感
    edge = QLinearGradient(x, y, x, y + h)
    edge.setColorAt(0.0, QColor(255, 255, 255, 70))
    edge.setColorAt(0.35, QColor(255, 255, 255, 18))
    edge.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setPen(QPen(QBrush(edge), max(1.0, w * 0.02)))
    p.drawPath(body)
    p.restore()


def app_icon() -> "QIcon":
    """把同一造型渲染成多尺寸 QIcon：窗口标题栏/任务栏用，无需外部文件。"""
    from PyQt5.QtGui import QIcon, QPixmap
    ic = QIcon()
    for px in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        paint_app_icon(p, QRectF(0, 0, px, px))
        p.end()
        ic.addPixmap(pm)
    return ic


class Splash(QWidget):
    """仿 Photoshop 的启动闪屏。``show_stage()`` 可换文案，动画持续跑。"""

    W, H = 560, 330            # 逻辑设计尺寸（再乘 ui_scale）

    def __init__(self, version: str, scale: float = 1.0, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._scale = max(0.5, float(scale or 1.0))
        self._stage = "正在启动…"
        self._phase = 0.0                    # 进度条动画相位 0..1
        self._opacity = 1.0                  # 淡出用（映射到窗口不透明度）
        self._version = version or ""
        self._fade: QPropertyAnimation | None = None
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._tick)
        self.resize(int(self.W * self._scale), int(self.H * self._scale))

    # ------------------------------------------------------------- 生命周期
    def show_splash(self) -> None:
        self._center()
        self.show()
        self.raise_()
        self._anim_timer.start()
        QApplication.processEvents()

    def show_stage(self, text: str) -> None:
        self._stage = text
        self.update()
        QApplication.processEvents()

    def finish(self, animated: bool = True) -> None:
        """主窗口已显示：淡出后真正关闭。

        淡出走 setWindowOpacity 动画——不用 QGraphicsOpacityEffect/无父动画对象，
        避开 MessageBox 那种动画被 GC 的坑。``animated=False`` 直接关（测试用）。
        """
        self._anim_timer.stop()
        if not animated:
            self.close()
            return
        try:
            self._fade = QPropertyAnimation(self, b"fadeOut", self)
            self._fade.setDuration(260)
            self._fade.setStartValue(1.0)
            self._fade.setEndValue(0.0)
            self._fade.setEasingCurve(QEasingCurve.OutCubic)
            self._fade.finished.connect(self.close)
            self._fade.start()
            QTimer.singleShot(600, self._force_close)   # 动画没跑起来的兜底
        except Exception:
            self.close()

    def _force_close(self) -> None:
        if self.isVisible():
            self.close()

    # Qt 动画属性：改窗口不透明度并重绘
    def _get_fade(self) -> float:
        return self._opacity

    def _set_fade(self, v: float) -> None:
        self._opacity = max(0.0, min(1.0, float(v)))
        try:
            self.setWindowOpacity(self._opacity)
        except Exception:
            pass
        self.update()

    fadeOut = pyqtProperty(float, fget=_get_fade, fset=_set_fade)

    # ------------------------------------------------------------- 绘制
    def _tick(self) -> None:
        self._phase = (self._phase + 0.016) % 1.0
        self.update()

    def _center(self) -> None:
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center().x() - self.width() // 2,
                      screen.center().y() - self.height() // 2 - 30)
        except Exception:
            pass

    def paintEvent(self, e) -> None:   # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        s = self._scale
        r = QRectF(0, 0, self.width(), self.height())
        panel = r.adjusted(14 * s, 10 * s, -14 * s, -18 * s)

        # 阴影（两层半透明圆角矩形模拟，成本低）
        for off, alpha in ((3.0, 18), (7.0, 12)):
            sh = panel.translated(0, off * s)
            path = QPainterPath()
            path.addRoundedRect(sh, 18 * s, 18 * s)
            p.fillPath(path, QColor(0, 0, 0, alpha))

        # 深色渐变面板（PS 式）
        grad = QLinearGradient(panel.topLeft(), panel.bottomRight())
        grad.setColorAt(0.0, QColor(27, 38, 66))
        grad.setColorAt(0.55, QColor(18, 25, 45))
        grad.setColorAt(1.0, QColor(10, 14, 26))
        path = QPainterPath()
        path.addRoundedRect(panel, 18 * s, 18 * s)
        p.fillPath(path, QBrush(grad))
        # 右上角微弱高光，避免"死黑"
        glow = QRadialGradient(QPointF(panel.right() - 90 * s, panel.top() + 30 * s),
                               140 * s)
        glow.setColorAt(0.0, QColor(96, 140, 255, 26))
        glow.setColorAt(1.0, QColor(96, 140, 255, 0))
        p.fillPath(path, QBrush(glow))
        p.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        p.drawPath(path)

        # ---- 小图标：视频屏 + 播放三角 + 字幕杠（见 paint_app_icon）
        icon_sz = 92 * s
        ix = panel.center().x() - icon_sz / 2
        iy = panel.top() + 30 * s
        paint_app_icon(p, QRectF(ix, iy, icon_sz, icon_sz))

        # ---- 文字
        p.setPen(QColor(245, 247, 252))
        f = QFont()
        f.setPointSizeF(max(9.0, 19.0 * s))
        f.setBold(True)
        p.setFont(f)
        ty = iy + icon_sz + 30 * s
        p.drawText(QRectF(panel.left(), ty, panel.width(), 40 * s),
                   Qt.AlignHCenter | Qt.AlignVCenter, "Subtitle Studio")
        f2 = QFont()
        f2.setPointSizeF(max(8.0, 11.5 * s))
        p.setFont(f2)
        p.setPen(QColor(196, 205, 224))
        p.drawText(QRectF(panel.left(), ty + 34 * s, panel.width(), 26 * s),
                   Qt.AlignHCenter | Qt.AlignVCenter, "视 频 字 幕 工 坊")

        # ---- 不确定进度条：轨道 + 往复游走的亮块
        track_w = panel.width() * 0.56
        tx = panel.center().x() - track_w / 2
        ty2 = panel.bottom() - 58 * s
        th = max(4.0, 5.0 * s)
        tr = QPainterPath()
        tr.addRoundedRect(tx, ty2, track_w, th, th / 2, th / 2)
        p.fillPath(tr, QColor(255, 255, 255, 26))
        frac = 0.5 - 0.5 * math.cos(self._phase * 2 * math.pi)   # 0→1→0 平滑
        chunk = track_w * 0.28
        cx = tx + frac * (track_w - chunk)
        cg = QLinearGradient(cx, ty2, cx + chunk, ty2)
        cg.setColorAt(0.0, QColor(88, 132, 255, 30))
        cg.setColorAt(0.5, QColor(120, 160, 255, 255))
        cg.setColorAt(1.0, QColor(88, 132, 255, 30))
        cr = QPainterPath()
        cr.addRoundedRect(cx, ty2, chunk, th, th / 2, th / 2)
        p.fillPath(cr, QBrush(cg))

        # ---- 阶段文案 + 版本
        f3 = QFont()
        f3.setPointSizeF(max(7.5, 9.0 * s))
        p.setFont(f3)
        p.setPen(QColor(165, 175, 198))
        p.drawText(QRectF(panel.left(), ty2 + 12 * s, panel.width(), 22 * s),
                   Qt.AlignHCenter | Qt.AlignVCenter, self._stage)
        p.setPen(QColor(120, 130, 152))
        p.drawText(QRectF(panel.left(), panel.bottom() - 30 * s,
                          panel.width(), 20 * s),
                   Qt.AlignHCenter | Qt.AlignVCenter,
                   f"版本 {self._version}  ·  Tuse Creation")
        p.end()
