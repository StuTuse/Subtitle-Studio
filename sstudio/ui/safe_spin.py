# -*- coding: utf-8 -*-
"""防误触的数值选择控件。

痛点：普通 QSpinBox 挂在表单里，鼠标滚轮一滚、上下方向键一按就把数值改了，
用户经常在不知情中调乱参数（温度、并发、超时……），事后又找不到哪里错了。

这里改成「点开选择」交互：
* 滚轮、方向键、PageUp/Down 全部不再改值（事件直接忽略）；
* 单击控件弹出选项列表，从里面选一个才生效；
* 手动输入仍然允许（advanced 用户），但失焦时自动夹回合法范围。

这样参数的每一个变化都来自一次明确的点击，不会再被误滚。
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QListWidget,
                             QListWidgetItem, QDoubleSpinBox, QSpinBox, QWidget)


class _WheelGuard:
    """混入类：屏蔽一切"无意"改值途径，并提供点击弹出选择列表。"""

    _menu: Optional[QListWidget] = None      # 类级共享：同一时间只弹一个

    # ---------------- 事件屏蔽 ----------------
    def wheelEvent(self, e) -> None:  # noqa: N802
        e.ignore()               # 滚轮不再改值，交给父级滚动容器

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
            e.ignore()           # 方向键也不再步进
            return
        super().keyPressEvent(e)

    def stepBy(self, steps: int) -> None:  # noqa: N802
        # 兜底：任何内部调用到的步进（含键盘加速）都不生效
        pass

    # ---------------- 点击弹出选择 ----------------
    def mousePressEvent(self, e) -> None:  # noqa: N802
        # 只在点击按钮区（上下箭头）或控件主体时弹列表；光标在文本上时先让用户编辑
        super().mousePressEvent(e)
        if self._menu is not None and self._menu.isVisible():
            self._menu.close()
            return
        QTimer.singleShot(0, self._open_chooser)

    def _open_chooser(self) -> None:
        if QApplication.mouseButtons() == Qt.NoButton:
            return               # 焦点转移触发的假点击，不弹
        opts = self._choices()
        if not opts:
            return
        m = QListWidget(None)    # 独立弹出窗，不依赖父级布局
        m.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        m.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        m.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        m.setSelectionMode(QAbstractItemView.SingleSelection)
        cur = self.value()
        for v, label in opts:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, v)
            m.addItem(it)
            if abs(float(v) - float(cur)) < 1e-9:
                m.setCurrentItem(it)
        m.setFixedWidth(max(self.width(), 120))
        # 最多显示 12 项，超出滚动
        row_h = m.sizeHintForRow(0) or 26
        m.setFixedHeight(min(len(opts), 12) * row_h + 12)
        m.itemClicked.connect(lambda item: self._pick(item))
        from .theme import accent_hex, is_dark
        accent = accent_hex()
        if is_dark():
            m.setStyleSheet(
                "QListWidget{background:#2b2b2b;color:#e6e6e6;border:1px solid #4a4a4a;}"
                "QListWidget::item{padding:5px 12px;}"
                "QListWidget::item:hover{background:#37373d;}"
                f"QListWidget::item:selected{{background:{accent};color:#ffffff;}}")
        else:
            m.setStyleSheet(
                "QListWidget{background:#ffffff;color:#1a1a1a;border:1px solid #c9c9c9;}"
                "QListWidget::item{padding:5px 12px;}"
                f"QListWidget::item:selected{{background:{accent};color:#ffffff;}}")
        self._menu = m
        # 弹窗无父级：控件销毁后弹窗若还开着，点击会对已析构的 C++ 对象
        # setValue。关窗即删 + 析构信号清引用，两头都堵上。
        m.setAttribute(Qt.WA_DeleteOnClose, True)
        m.destroyed.connect(lambda: self._menu_gone(m))
        m.move(QCursor.pos())
        m.show()

    def _menu_gone(self, m: QListWidget) -> None:
        if getattr(self, "_menu", None) is m:
            self._menu = None

    def _pick(self, item: QListWidgetItem) -> None:
        v = item.data(Qt.UserRole)
        try:
            self.setValue(float(v) if isinstance(self, QDoubleSpinBox) else int(v))
        except Exception:
            pass
        if self._menu is not None:
            self._menu.close()

    def _choices(self) -> Sequence:
        """子类提供 [(value, label), …]。"""
        return []

    def close_popup(self) -> None:
        try:
            if self._menu is not None and self._menu.isVisible():
                self._menu.close()
        except RuntimeError:      # C++ 对象已随窗口销毁
            self._menu = None


class SafeSpinBox(QSpinBox, _WheelGuard):
    """整数版：点击弹出候选值列表。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setKeyboardTracking(False)   # 输入中不触发 valueChanged
        self.setCorrectionMode(QSpinBox.CorrectToNearestValue)
        self.setAccelerated(False)

    def set_choices(self, choices: Optional[Sequence] = None) -> None:
        """设置候选列表；传 None 才按范围+步长自动生成，传 [] 明确清空弹窗。"""
        self._explicit = None if choices is None else list(choices)

    def _choices(self):
        explicit = getattr(self, "_explicit", None)
        if explicit is not None:
            return explicit
        out: List = []
        step = max(1, self.singleStep())
        v = self.minimum()
        while v <= self.maximum() and len(out) < 40:
            out.append((v, f"{v}{self.suffix()}"))
            v += step
        return out

    def validate(self, text: str, pos: int):  # noqa: N802
        # 允许临时为空/非法，focusOut 时夹回。
        # 状态枚举必须用 QValidator.Intermediate——此前写 QSpinBox.Intermediate，
        # PyQt5 里该枚举只挂在 QValidator 上，每次清空输入框 validate 都抛
        # AttributeError（靠 PyQt 虚方法兜底才没闪退），守卫从未真正生效。
        from PyQt5.QtGui import QValidator
        if not text.strip():
            return (QValidator.Intermediate, text, pos)
        return super().validate(text, pos)


class SafeDoubleSpinBox(QDoubleSpinBox, _WheelGuard):
    """小数版：点击弹出候选值列表。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setKeyboardTracking(False)
        self.setCorrectionMode(QDoubleSpinBox.CorrectToNearestValue)
        self.setAccelerated(False)

    def set_choices(self, choices: Optional[Sequence] = None) -> None:
        self._explicit = None if choices is None else list(choices)

    def _choices(self):
        explicit = getattr(self, "_explicit", None)
        if explicit is not None:
            return explicit
        out: List = []
        step = self.singleStep() or 0.1
        dec = self.decimals()
        v = self.minimum()
        while round(v, dec) <= self.maximum() and len(out) < 40:
            out.append((round(v, dec), f"{round(v, dec):.{dec}f}{self.suffix()}"))
            v += step
        return out

    def validate(self, text: str, pos: int):  # noqa: N802
        # 同 SafeSpinBox：枚举必须取自 QValidator（QDoubleSpinBox 上没有）
        from PyQt5.QtGui import QValidator
        if not text.strip():
            return (QValidator.Intermediate, text, pos)
        return super().validate(text, pos)
