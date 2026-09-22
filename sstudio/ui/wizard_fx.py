# -*- coding: utf-8 -*-
"""向导动效库：macOS 开箱体验同款 —— 弹性缓动 / 模糊消散 / 级联入场。

参考 D:\\Project\\macOS-Web 的激活向导（WAAPI），这里用 QPropertyAnimation +
QGraphicsOpacityEffect + QGraphicsBlurEffect 复刻同一套节奏：

* 页面切换：前进从右推入、后退从左推入，位移 + 模糊消散（420/300ms）；
* 级联入场：卡片依次上浮，每张错开 55ms（460ms）；
* 徽标绽放：模糊 8px → 0 淡入（520ms）；
* 完成仪式：完成按钮放大成全屏遮罩 → 文案级联 → 遮罩模糊消散露出主窗。

性能注意：QGraphicsEffect 走 CPU 光栅化，只在向导这种一次性界面使用；
动画结束立即 clear_effect 摘掉，主窗口常规操作绝不挂 effect。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import (QEasingCurve, QParallelAnimationGroup, QPoint,
                          QPropertyAnimation, QRect, QTimer)
from PyQt5.QtWidgets import (QGraphicsBlurEffect, QGraphicsOpacityEffect,
                             QWidget)

# macOS 同款缓动曲线
EASE_OUT = QEasingCurve(QEasingCurve.OutCubic)          # 通用入场
SPRING = QEasingCurve(QEasingCurve.OutBack)             # 徽标回弹
EASE_IN = QEasingCurve(QEasingCurve.InCubic)            # 退场加速
FINISH_EASE = QEasingCurve(QEasingCurve.InOutQuart)     # 完成仪式

_PAGE_MS = 420          # 新页推入
_PAGE_OUT_MS = 300      # 旧页退出
_CASCADE_MS = 460       # 卡片级联
_CASCADE_STAGGER = 55   # 级联错开
_POP_MS = 520           # 徽标绽放
_FINISH_MS = 820        # 完成遮罩扩张
_FINAL_FADE_MS = 700    # 遮罩消散


def _animate(target, prop: bytes, v0, v1, duration: int, ease,
             on_done=None) -> QPropertyAnimation:
    a = QPropertyAnimation(target, prop, target)
    a.setDuration(duration)
    a.setStartValue(v0)
    a.setEndValue(v1)
    a.setEasingCurve(ease)
    if on_done:
        a.finished.connect(on_done)
    a.start(QPropertyAnimation.DeleteWhenStopped)
    return a


def _fade_blur(widget: QWidget, blur_from: float, duration: int, ease,
               fade_from: float = 0.0, fade_to: float = 1.0,
               on_done=None) -> QParallelAnimationGroup:
    """透明度 + 模糊一起动：fade_from→fade_to，blur_from→0。"""
    op = QGraphicsOpacityEffect(widget)
    op.setOpacity(fade_from)
    widget.setGraphicsEffect(op)
    a1 = QPropertyAnimation(op, b"opacity", widget)
    a1.setDuration(duration)
    a1.setStartValue(fade_from)
    a1.setEndValue(fade_to)
    a1.setEasingCurve(ease)
    blur = QGraphicsBlurEffect(widget)
    blur.setBlurRadius(blur_from)
    widget.setGraphicsEffect(blur)
    a2 = QPropertyAnimation(blur, b"blurRadius", widget)
    a2.setDuration(duration)
    a2.setStartValue(blur_from)
    a2.setEndValue(0.0)
    a2.setEasingCurve(ease)
    grp = QParallelAnimationGroup(widget)
    grp.addAnimation(a1)
    grp.addAnimation(a2)
    if on_done:
        grp.finished.connect(on_done)
    grp.start(QPropertyAnimation.DeleteWhenStopped)
    return grp


def pop_in(widget: QWidget, delay: int = 0, on_done=None) -> None:
    """徽标/图标绽放：模糊 8px → 0 淡入（macOS 徽标入场节奏）。"""
    if delay > 0:
        QTimer.singleShot(delay, lambda: pop_in(widget, 0, on_done))
        return
    _fade_blur(widget, 8.0, _POP_MS, EASE_OUT, on_done=on_done)


def cascade_in(widgets: List[Optional[QWidget]], delay: int = 90,
               stagger: int = _CASCADE_STAGGER) -> None:
    """卡片级联上浮：依次上移入场 + 模糊消散（macOS 卡片节奏）。"""
    for i, w in enumerate(widgets):
        if w is None:
            continue
        base_pos = w.pos()

        def _start(ww=w, bp=base_pos):
            op = QGraphicsOpacityEffect(ww)
            op.setOpacity(0.0)
            ww.setGraphicsEffect(op)
            a1 = QPropertyAnimation(op, b"opacity", ww)
            a1.setDuration(_CASCADE_MS)
            a1.setStartValue(0.0)
            a1.setEndValue(1.0)
            a1.setEasingCurve(EASE_OUT)
            blur = QGraphicsBlurEffect(ww)
            blur.setBlurRadius(5.0)
            ww.setGraphicsEffect(blur)
            a2 = QPropertyAnimation(blur, b"blurRadius", ww)
            a2.setDuration(_CASCADE_MS)
            a2.setStartValue(5.0)
            a2.setEndValue(0.0)
            a2.setEasingCurve(EASE_OUT)
            a3 = QPropertyAnimation(ww, b"pos", ww)
            a3.setDuration(_CASCADE_MS)
            a3.setStartValue(QPoint(bp.x(), bp.y() + 16))
            a3.setEndValue(bp)
            a3.setEasingCurve(EASE_OUT)
            grp = QParallelAnimationGroup(ww)
            grp.addAnimation(a1)
            grp.addAnimation(a2)
            grp.addAnimation(a3)
            grp.start(QPropertyAnimation.DeleteWhenStopped)

        QTimer.singleShot(delay + i * stagger, _start)


def page_in(widget: QWidget, direction: int) -> None:
    """页面推入：前进从右 (+46px)、后退从左 (-46px)，带模糊消散。"""
    dx = 46 * (1 if direction >= 0 else -1)
    base = widget.pos()
    start = QPoint(base.x() + dx, base.y())
    widget.move(start)
    _fade_blur(widget, 6.0, _PAGE_MS, EASE_OUT,
               on_done=lambda: clear_effect(widget))


def page_out(widget: QWidget, direction: int, on_done=None) -> None:
    """旧页退出：反向滑走 + 模糊加深 + 淡出。"""
    op = QGraphicsOpacityEffect(widget)
    op.setOpacity(1.0)
    widget.setGraphicsEffect(op)
    a1 = QPropertyAnimation(op, b"opacity", widget)
    a1.setDuration(_PAGE_OUT_MS)
    a1.setStartValue(1.0)
    a1.setEndValue(0.0)
    a1.setEasingCurve(EASE_IN)
    blur = QGraphicsBlurEffect(widget)
    blur.setBlurRadius(0.0)
    widget.setGraphicsEffect(blur)
    a2 = QPropertyAnimation(blur, b"blurRadius", widget)
    a2.setDuration(_PAGE_OUT_MS)
    a2.setStartValue(0.0)
    a2.setEndValue(8.0)
    a2.setEasingCurve(EASE_IN)
    grp = QParallelAnimationGroup(widget)
    grp.addAnimation(a1)
    grp.addAnimation(a2)
    dx = -46 * (1 if direction >= 0 else -1)
    pos0 = widget.pos()
    a3 = QPropertyAnimation(widget, b"pos", widget)
    a3.setDuration(_PAGE_OUT_MS)
    a3.setStartValue(pos0)
    a3.setEndValue(QPoint(pos0.x() + dx, pos0.y()))
    a3.setEasingCurve(EASE_IN)
    grp.addAnimation(a3)
    if on_done:
        grp.finished.connect(on_done)
    grp.start(QPropertyAnimation.DeleteWhenStopped)


def clear_effect(widget: QWidget) -> None:
    """动画结束后的收尾：摘掉 effect（留着会持续吃光栅化性能）。"""
    try:
        widget.setGraphicsEffect(None)
        # 复位可能被 page_out 移走的位置（后退重进时 page_in 会重新定位）
    except Exception:
        pass


def finish_reveal(finish_btn: QWidget, wizard: QWidget,
                  logo_widget: Optional[QWidget] = None,
                  text_widgets: Optional[List[QWidget]] = None,
                  on_done=None) -> None:
    """完成仪式：遮罩从按钮矩形扩张铺满 → 文案级联 → 遮罩模糊消散。

    三幕结构复刻 macOS-Web finishWizard。on_done 在遮罩消散完毕后回调，
    调用方此时再 accept() 关闭向导，主窗从遮罩后面浮现。
    """
    geo = finish_btn.geometry()
    # 全屏遮罩：初始精确覆盖按钮矩形（向导坐标系）
    veil = QWidget(wizard)
    veil.setAutoFillBackground(True)
    veil.setStyleSheet(
        "background: qradialgradient(cx:0.5, cy:0, radius:1.3, "
        "stop:0 rgba(47,109,179,235), stop:1 rgba(14,30,52,250));")
    veil.setGeometry(geo)
    veil.raise_()
    veil.show()
    _animate(veil, b"geometry", QRect(geo), QRect(0, 0, wizard.width(),
                                                  wizard.height()),
             _FINISH_MS, FINISH_EASE,
             on_done=lambda: _reveal_text(veil, logo_widget, text_widgets,
                                          wizard, on_done))


def _reveal_text(veil: QWidget, logo: Optional[QWidget],
                 texts: Optional[List[QWidget]], wizard: QWidget,
                 on_done) -> None:
    """第二幕：遮罩铺满后，徽标 + 文案级联登场。"""
    if logo is not None:
        logo.setParent(veil)
        logo.move((veil.width() - logo.width()) // 2,
                  max(40, veil.height() // 2 - 130))
        logo.show()
        pop_in(logo)
    for i, w in enumerate(texts or []):
        if w is None:
            continue
        w.setParent(veil)
        base = w.pos()
        w.move((veil.width() - w.width()) // 2, base.y())
        w.show()
    cascade_in(texts or [], delay=260)
    QTimer.singleShot(1500, lambda: _fade_away(veil, wizard, on_done))


def _fade_away(veil: QWidget, wizard: QWidget, on_done) -> None:
    """第三幕：遮罩模糊消散，露出主窗。"""
    op = QGraphicsOpacityEffect(veil)
    op.setOpacity(1.0)
    veil.setGraphicsEffect(op)
    a1 = QPropertyAnimation(op, b"opacity", veil)
    a1.setDuration(_FINAL_FADE_MS)
    a1.setStartValue(1.0)
    a1.setEndValue(0.0)
    blur = QGraphicsBlurEffect(veil)
    blur.setBlurRadius(0.0)
    veil.setGraphicsEffect(blur)
    a2 = QPropertyAnimation(blur, b"blurRadius", veil)
    a2.setDuration(_FINAL_FADE_MS)
    a2.setStartValue(0.0)
    a2.setEndValue(22.0)
    grp = QParallelAnimationGroup(veil)
    grp.addAnimation(a1)
    grp.addAnimation(a2)

    def _end():
        veil.deleteLater()
        if on_done:
            on_done()

    grp.finished.connect(_end)
    grp.start(QPropertyAnimation.DeleteWhenStopped)
