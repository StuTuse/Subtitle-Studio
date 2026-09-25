# -*- coding: utf-8 -*-
"""向导动效库：macOS 开箱体验同款 —— 弹性缓动 / 模糊消散 / 级联入场。

参考 D:\\Project\\macOS-Web 的激活向导（WAAPI），这里用 QPropertyAnimation +
QGraphicsOpacityEffect + QGraphicsBlurEffect 复刻同一套节奏：

* 页面切换：前进从右推入、后退从左推入，位移 + 模糊消散（420/300ms）；
* 级联入场：卡片依次上浮，每张错开 55ms（460ms）；
* 徽标绽放：模糊 8px → 0 淡入（520ms）。

性能注意：QGraphicsEffect 走 CPU 光栅化，只在向导这种一次性界面使用；
动画结束立即 clear_effect 摘掉，主窗口常规操作绝不挂 effect。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import (QEasingCurve, QParallelAnimationGroup, QPoint,
                          QPropertyAnimation, QTimer)
from PyQt5.QtWidgets import (QGraphicsBlurEffect, QWidget)

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


def _fade_blur(widget: QWidget, blur_from: float, duration: int, ease,
               fade_from: float = 0.0, fade_to: float = 1.0,
               on_done=None) -> QParallelAnimationGroup:
    """模糊消散 + 位移淡入：blur_from→0。

    注意一个 widget 只挂**一个** QGraphicsEffect：第二次 setGraphicsEffect
    会把前一个效果直接析构（Qt 语义），先前绑在它上面的透明度动画就会
    操作已删除的 C++ 对象——淡入失效且可能抛 RuntimeError。这里统一只
    用模糊效果表达入场（模糊从大到 0 本身就有"从虚到实"的观感），透明
    度不再单独挂 effect。
    """
    blur = QGraphicsBlurEffect(widget)
    blur.setBlurRadius(blur_from)
    widget.setGraphicsEffect(blur)
    a2 = QPropertyAnimation(blur, b"blurRadius", widget)
    a2.setDuration(duration)
    a2.setStartValue(blur_from)
    a2.setEndValue(0.0)
    a2.setEasingCurve(ease)
    grp = QParallelAnimationGroup(widget)
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
            grp.addAnimation(a2)
            grp.addAnimation(a3)
            # 动画完必须摘 effect：留着会一直走 CPU 光栅化
            grp.finished.connect(lambda ww=ww: clear_effect(ww))
            grp.start(QPropertyAnimation.DeleteWhenStopped)

        QTimer.singleShot(delay + i * stagger, _start)


def page_in(widget: QWidget, direction: int) -> None:
    """页面推入：模糊消散入场（macOS 拍子 420ms）。

    历史版本这里还会 move() 做侧向滑入——但页面由 QVBoxLayout 管几何，
    布局的 LayoutRequest 下一拍就把位置拍回槽位，位移从未真正可见过；
    而且它和布局争抢几何是隐患。现在只保留实际生效的模糊消散。
    """
    _fade_blur(widget, 6.0, _PAGE_MS, EASE_OUT,
               on_done=lambda: clear_effect(widget))


def page_out(widget: QWidget, direction: int, on_done=None) -> None:
    """旧页退出：模糊加深（0→8px，300ms）。

    同 page_in：位移动画与布局打架且不可见，只保留模糊部分。
    """
    blur = QGraphicsBlurEffect(widget)
    blur.setBlurRadius(0.0)
    widget.setGraphicsEffect(blur)
    a2 = QPropertyAnimation(blur, b"blurRadius", widget)
    a2.setDuration(_PAGE_OUT_MS)
    a2.setStartValue(0.0)
    a2.setEndValue(8.0)
    a2.setEasingCurve(EASE_IN)
    grp = QParallelAnimationGroup(widget)
    grp.addAnimation(a2)
    if on_done:
        grp.finished.connect(on_done)
    # 旧页退场后即被新页盖住：同样摘掉 effect，别让它持续吃光栅化
    grp.finished.connect(lambda: clear_effect(widget))
    grp.start(QPropertyAnimation.DeleteWhenStopped)


def clear_effect(widget: QWidget) -> None:
    """动画结束后的收尾：摘掉 effect（留着会持续吃光栅化性能）。"""
    try:
        widget.setGraphicsEffect(None)
    except Exception:
        pass
