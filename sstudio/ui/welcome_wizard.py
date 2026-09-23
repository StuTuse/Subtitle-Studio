# -*- coding: utf-8 -*-
"""首次使用欢迎向导（类似 Windows"开箱体验"/系统激活界面）。

流程：欢迎 → 外观选择 → 大模型接入（可跳过）→ 环境体检（可跳过）→ 完成。
每一步都是独立页面，底部 上一步/下一步 导航；进度圆点指示当前位置。

触发：仅当配置文件不存在（真·第一次使用）。设置页可随时重开体检，
但欢迎向导只此一回 —— 完成后写 `setup_done=1`，之后启动直接进主界面。
"""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem,
                             QPlainTextEdit, QProgressBar, QPushButton,
                             QVBoxLayout, QWidget)

try:
    from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, ComboBox,
                                InfoBar, LineEdit, PasswordLineEdit,
                                PrimaryPushButton, ProgressRing, PushButton,
                                StrongBodyLabel, SwitchButton, TitleLabel)
except Exception:  # pragma: no cover —— 无 qfluentwidgets 的最小兜底
    BodyLabel = CaptionLabel = StrongBodyLabel = TitleLabel = QLabel
    CardWidget = PushButton = PrimaryPushButton = QPushButton
    LineEdit = PasswordLineEdit = QLineEdit
    ComboBox = QListWidget
    SwitchButton = QPushButton

    def InfoBar(*a, **kw):
        class _N:
            @staticmethod
            def success(**kw):
                pass
            @staticmethod
            def error(**kw):
                pass
        return _N()

    def ProgressRing(*a, **kw):
        return QProgressBar()

from .. import __version__
from ..core import doctor
from ..core.config import BUILTIN_PRESETS, Config, LLMProfile
from .first_run_dialog import FirstRunDialog
from .splash import app_icon
from .theme import dim_span, is_dark, status_hex
from . import wizard_fx


# ==================================================================== 页面基类
class _Page(QWidget):
    """向导里的一页。子类只管内容，导航/校验交给 WelcomeWizard。"""

    def __init__(self, wizard: "WelcomeWizard"):
        super().__init__()
        self.wizard = wizard
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(36, 28, 36, 20)
        self.v.setSpacing(12)

    def title(self, text: str, sub: str = "") -> None:
        self.v.addWidget(TitleLabel(text, self))
        if sub:
            c = CaptionLabel(sub, self)
            c.setWordWrap(True)
            self.v.addWidget(c)

    def on_enter(self) -> None:
        """切到本页时回调（用于启动检查等）。"""

    def is_valid(self) -> bool:
        """点"下一步"时的校验；False 则导航被拦下。"""
        return True


# ==================================================================== 1 欢迎
class _WelcomePage(_Page):
    def __init__(self, w: "WelcomeWizard"):
        super().__init__(w)
        self.v.addStretch(1)
        icon = QLabel(self)
        icon.setPixmap(app_icon().pixmap(96, 96))
        icon.setAlignment(Qt.AlignHCenter)
        self.v.addWidget(icon)
        t = TitleLabel(f"欢迎使用 Subtitle Studio", self)
        t.setAlignment(Qt.AlignHCenter)
        self.v.addWidget(t)
        v = BodyLabel(f"版本 {__version__} · 视频字幕工坊", self)
        v.setAlignment(Qt.AlignHCenter)
        self.v.addWidget(v)
        self.v.addSpacing(14)
        for line in ("本地 Whisper 转写 · 大模型纠错 · 多格式导出",
                     "接下来只需两步：选外观、连模型（可选）。",
                     "全程离线可用，不上传任何素材。"):
            lab = CaptionLabel(line, self)
            lab.setAlignment(Qt.AlignHCenter)
            self.v.addWidget(lab)
        self.v.addStretch(2)


# ==================================================================== 2 外观
class _AppearancePage(_Page):
    def __init__(self, w: "WelcomeWizard"):
        super().__init__(w)
        self.title("选一个顺眼的外观", "之后在「设置」里随时可以改。")

        self.cards: List[CardWidget] = []
        row = QHBoxLayout()
        row.setSpacing(14)
        for key, name, desc in (
                ("auto", "跟随系统", "Windows 深色模式开了就用深色"),
                ("light", "浅色", "白底黑字，白天护眼"),
                ("dark", "深色", "黑底白字，夜间剪辑常用")):
            card = CardWidget(self)
            card.setFixedHeight(150)
            cv = QVBoxLayout(card)
            cv.setContentsMargins(16, 12, 16, 12)
            swatch = QLabel(card)
            swatch.setFixedHeight(52)
            bg = {"auto": "#3a3a3a", "light": "#f3f3f3", "dark": "#1b1b1b"}[key]
            fg = "#e6e6e6" if key != "light" else "#333333"
            swatch.setStyleSheet(
                f"background:{bg};color:{fg};border-radius:8px;"
                "font-size:11px;")
            swatch.setAlignment(Qt.AlignCenter)
            swatch.setText("Aa 字幕 · 00:12")
            cv.addWidget(swatch)
            n = StrongBodyLabel(name, card)
            cv.addWidget(n)
            d = CaptionLabel(desc, card)
            d.setWordWrap(True)
            cv.addWidget(d)
            card.setCursor(Qt.PointingHandCursor)
            card.clicked.connect(lambda _, k=key: self._pick(k))
            # 不覆写 enterEvent/leaveEvent：CardWidget 自带悬停反馈；
            # 之前覆写成调 self._hl（空方法），鼠标一进卡片就 AttributeError
            self.cards.append(card)
            row.addWidget(card, 1)
        self.v.addLayout(row)
        self.v.addStretch(1)
        self._picked = ""
        self._pick("auto", paint=False)

    def _pick(self, key: str, paint: bool = True) -> None:
        self._picked = key
        from .theme import accent_hex
        on_border = accent_hex()
        for i, (key_i, _, _) in enumerate(
                (("auto", "", ""), ("light", "", ""), ("dark", "", ""))):
            card = self.cards[i]
            selected = key_i == key
            card.setStyleSheet(
                f"CardWidget{{border:2px solid {on_border if selected else 'transparent'};"
                f"border-radius:10px;}}")
            card.setProperty("theme_key", key_i)

    def apply(self, cfg: Config) -> None:
        cfg.theme = self._picked or "auto"


# ==================================================================== 3 模型
class _ModelPage(_Page):
    def __init__(self, w: "WelcomeWizard"):
        super().__init__(w)
        self.title("连一个大模型（用于 AI 纠错）",
                   "字幕纠错会调用它修错别字。可以先跳过——之后在「设置」里随时配。")

        self.preset = ComboBox(self)
        self._fill_presets()
        self.preset.currentIndexChanged.connect(self._apply_preset)
        self.v.addWidget(self.preset)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.p_base = LineEdit(self)
        self.p_base.setPlaceholderText("https://api.deepseek.com/v1")
        self.p_key = PasswordLineEdit(self)
        self.p_key.setPlaceholderText("sk-…（本地服务随便填，如 ollama）")
        self.p_model = LineEdit(self)
        self.p_model.setPlaceholderText("deepseek-chat")
        self.p_test = CaptionLabel("", self)
        form.addRow("接口地址", self.p_base)
        form.addRow("API Key", self.p_key)
        form.addRow("模型名", self.p_model)
        form.addRow("", self.p_test)
        self.v.addLayout(form)

        btns = QHBoxLayout()
        self.btn_test = PushButton("测试连接", self)
        self.btn_test.clicked.connect(self._test)
        btns.addWidget(self.btn_test)
        self.btn_skip = PushButton("暂时跳过（离线转写也能用）", self)
        self.btn_skip.clicked.connect(self._skip)
        btns.addWidget(self.btn_skip)
        btns.addStretch(1)
        self.v.addLayout(btns)
        self.v.addStretch(1)
        self._skipped = False
        self._worker = None

    def _fill_presets(self) -> None:
        """预设下拉 = 内置 + 用户自定义（config.custom_presets，出厂带示例预设）。"""
        self.preset.addItem("选择服务商预设…", None, None)
        for p in self._presets():
            self.preset.addItem(p["name"], None, p)   # (text, icon, userData)

    def _presets(self) -> list:
        cp = getattr(self.wizard.cfg, "custom_presets", []) or []
        return list(BUILTIN_PRESETS) + [p for p in cp
                                        if isinstance(p, dict)
                                        and p.get("name") and p.get("base_url")]

    def _apply_preset(self, idx: int) -> None:
        p = self.preset.itemData(idx)
        if not isinstance(p, dict):
            return
        self.p_base.setText(p["base_url"])
        self.p_model.setText(p["model"])
        if p.get("api_key"):
            self.p_key.setText(p["api_key"])

    def _skip(self) -> None:
        self._skipped = True
        self.p_base.clear()
        self.p_key.clear()
        self.p_test.setText(dim_span("已跳过。到「设置」里随时可配。"))
        self.wizard.update_nav()

    def _test(self) -> None:
        if self._worker is not None:
            return
        base = self.p_base.text().strip()
        if not base:
            self.p_test.setText(f"<span style='color:{status_hex('err')}'>"
                                "先填接口地址，或点「暂时跳过」。</span>")
            return
        prof = LLMProfile(name="welcome", base_url=base,
                          api_key=self.p_key.text().strip(),
                          model=self.p_model.text().strip() or "deepseek-chat")
        from .workers import TestLLMWorker, reap
        reap(getattr(self, "_worker", None))
        self._worker = None
        self.p_test.setText("正在测试连接…")
        self.btn_test.setEnabled(False)
        w = TestLLMWorker(self, prof)
        w.sig_done.connect(lambda r: self._tested(r))
        w.sig_failed.connect(lambda m: self._tested((False, m, 0.0)))
        w.start()
        self._worker = w

    def _tested(self, res) -> None:
        self.btn_test.setEnabled(True)
        ok, msg, dt = res
        from html import escape as _esc
        msg = _esc(msg or "")
        if ok:
            self.p_test.setText(
                f"<span style='color:{status_hex('ok')}'>✓ 连接成功（{dt:.2f}s）</span>"
                f" 模型回复：{msg[:60]}")
        else:
            self.p_test.setText(f"<span style='color:{status_hex('err')}'>"
                                f"✕ {msg[:120]}</span>")

    def on_enter(self) -> None:
        # 首次进本页：预填第一个预设（DeepSeek），减少空白表单的无从下手感
        if not self.p_base.text().strip() and self.preset.count() > 1:
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(1)
            self.preset.blockSignals(False)
            self._apply_preset(1)

    def is_valid(self) -> bool:
        # 跳过 or 填了地址即可过；Key 可后补
        return self._skipped or bool(self.p_base.text().strip())

    def apply(self, cfg: Config) -> None:
        if self._skipped:
            return
        base = self.p_base.text().strip()
        if not base:
            return
        p = cfg.profiles[0]
        p.base_url = base
        p.api_key = self.p_key.text().strip()
        p.model = self.p_model.text().strip() or p.model
        cfg.active_profile = p.name


# ==================================================================== 4 体检
class _CheckPage(_Page):
    """环境体检页：复用 FirstRunDialog 的逻辑但嵌入向导布局。"""

    def __init__(self, w: "WelcomeWizard"):
        super().__init__(w)
        self.title("环境体检", "检查转写引擎、视频解码、GPU 加速是否就绪。缺什么可一键补装。")
        self.host = QVBoxLayout()
        self.host.setSpacing(6)
        self.v.addLayout(self.host)
        self.v.addStretch(1)

        self.fix_bar = QProgressBar(self)
        self.fix_bar.setRange(0, 100)
        self.fix_bar.setVisible(False)
        self.v.addWidget(self.fix_bar)
        self.fix_label = CaptionLabel("", self)
        self.fix_label.setVisible(False)
        self.fix_label.setWordWrap(True)
        self.v.addWidget(self.fix_label)

        self.log_box = QPlainTextEdit(self)
        self.log_box.setReadOnly(True)
        self.log_box.setVisible(False)
        self.log_box.setMinimumHeight(110)
        self.v.addWidget(self.log_box, 1)

        row = QHBoxLayout()
        self.btn_log = QPushButton("详细日志", self)
        self.btn_log.clicked.connect(self._toggle_log)
        row.addWidget(self.btn_log)
        row.addStretch(1)
        self.btn_fix = PrimaryPushButton("一键修复", self)
        self.btn_fix.clicked.connect(self._fix_all)
        row.addWidget(self.btn_fix)
        self.btn_rescan = PushButton("重新检查", self)
        self.btn_rescan.clicked.connect(self.refresh)
        row.addWidget(self.btn_rescan)
        self.v.addLayout(row)

        self._items: List[doctor.CheckItem] = []
        self._log_lines: List[str] = []
        self._worker = None

    def on_enter(self) -> None:
        QTimer.singleShot(60, self.refresh)

    def refresh(self) -> None:
        while self.host.count():
            r = self.host.takeAt(0)
            if r.widget():
                r.widget().deleteLater()
        self._items = doctor.check_all()
        for it in self._items:
            self.host.addWidget(self._row(it))
        need = [i for i in self._items if not i.ok and i.fixable]
        self.btn_fix.setVisible(bool(need))
        self.btn_fix.setText(f"一键修复（{len(need)}）" if need else "")

    def _row(self, it: doctor.CheckItem) -> QWidget:
        card = CardWidget(self)
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(10)
        mark = QLabel("✓" if it.ok else ("✕" if it.level == "required" else "⚠"), card)
        from .theme import badge_font
        mark.setFont(badge_font())
        mark.setStyleSheet(f"color:{status_hex('ok' if it.ok else ('err' if it.level == 'required' else 'warn'))}")
        mark.setFixedWidth(18)
        h.addWidget(mark)
        mid = QVBoxLayout()
        mid.setSpacing(1)
        mid.addWidget(BodyLabel(f"{it.title}（"
                                f"{'必需' if it.level == 'required' else '建议' if it.level == 'recommend' else '可选'}）", card))
        det = it.detail or ""
        from html import escape as _esc
        sub = CaptionLabel(it.why + (f"　{dim_span(_esc(det))}" if det else ""), card)
        sub.setWordWrap(True)
        try:
            sub.setTextFormat(Qt.RichText)
        except Exception:
            pass
        mid.addWidget(sub)
        h.addLayout(mid, 1)
        if it.fixable and not it.ok:
            btn = PrimaryPushButton(it.fix_note or "安装", card)
            btn.clicked.connect(lambda _=False, i=it: self._fix_one(i))
            h.addWidget(btn)
        elif it.fix_note and not it.ok:
            tip = CaptionLabel("手动安装", card)
            tip.setToolTip(it.fix_note)
            h.addWidget(tip)
        return card

    # ---------------- 修复（与 FirstRunDialog 同一套编排） ----------------
    def _fix_all(self) -> None:
        todo = [i for i in self._items if not i.ok and i.fixable]
        if todo:
            self._fix_many(todo)

    def _fix_one(self, it: doctor.CheckItem) -> None:
        self._fix_many([it])

    def _fix_many(self, todo: List[doctor.CheckItem]) -> None:
        self.btn_fix.setEnabled(False)
        self.fix_bar.setVisible(True)
        self.fix_label.setVisible(True)
        self.fix_bar.setValue(2)
        pkgs: List[str] = []
        for it in todo:
            pkgs.extend(it.fix_pkgs)

        def work(progress, log, cancel):
            return doctor.pip_install(pkgs, progress=progress, log=log, cancel=cancel)

        def on_prog(msg, pct):
            self.fix_bar.setValue(int(max(0, min(1, pct)) * 100))
            self.fix_label.setText(msg)

        def on_log(line):
            self._log_lines.append(line)
            if self.log_box.isVisible():
                self.log_box.setPlainText("\n".join(self._log_lines[-400:]))

        from .workers import ThreadedCall, CB_PROGRESS, CB_LOG, CB_CANCEL, reap
        self._worker = ThreadedCall(work, CB_PROGRESS, CB_LOG, CB_CANCEL)
        # 进度/日志回调经 queued 信号回主线程执行（ThreadedCall 注入包装），
        # 工作线程绝不直接碰控件（跨线程 UI 访问会偶发闪退）。
        self._worker.sig_progress.connect(on_prog)
        self._worker.sig_log.connect(on_log)
        self._worker.setParent(self)
        self._worker.sig_done.connect(lambda res: self._done(res))
        self._worker.sig_failed.connect(lambda m: self._done((False, m)))
        reap(self._worker)
        self._worker.start()

    def _cancel_worker(self) -> None:
        w = getattr(self, "_worker", None)
        if w is not None and w.isRunning():
            w.cancel()          # pip 逐行读时轮询 _cancel_flag，下一个检查点退出

    def _done(self, result) -> None:
        ok, msg = result if isinstance(result, tuple) else (False, str(result))
        from .workers import reap
        reap(self._worker)
        self._worker = None
        self.btn_fix.setEnabled(True)
        if ok:
            self.fix_bar.setValue(100)
            self.fix_label.setText("修复完成，正在重新检查…")
            QTimer.singleShot(600, self.refresh)
            QTimer.singleShot(2400, lambda: (self.fix_bar.setVisible(False),
                                             self.fix_label.setVisible(False)))
        else:
            self.fix_label.setText(msg)
            InfoBar.error(title="安装失败", content=msg.splitlines()[0][:120],
                          orient=Qt.Horizontal, isClosable=True, duration=8000,
                          parent=self)
            self._toggle_log(force=True)

    def _toggle_log(self, force: bool = False) -> None:
        vis = True if force else not self.log_box.isVisible()
        self.log_box.setVisible(vis)
        self.btn_log.setText("收起日志" if vis else "详细日志")
        if vis:
            self.log_box.setPlainText("\n".join(self._log_lines) or "（暂无日志）")


# ==================================================================== 5 完成
class _DonePage(_Page):
    def __init__(self, w: "WelcomeWizard"):
        super().__init__(w)
        self.v.addStretch(1)
        t = TitleLabel("一切就绪！", self)
        t.setAlignment(Qt.AlignHCenter)
        self.v.addWidget(t)
        self.summary = BodyLabel("", self)
        self.summary.setAlignment(Qt.AlignHCenter)
        self.summary.setWordWrap(True)
        self.v.addWidget(self.summary)
        self.v.addSpacing(10)
        tip = CaptionLabel("点「完成」进入主界面：导入一个视频就能开始做字幕。"
                           "遇到问题可到「设置」里改参数或重开体检。", self)
        tip.setAlignment(Qt.AlignHCenter)
        tip.setWordWrap(True)
        self.v.addWidget(tip)
        self.v.addStretch(2)

    def refresh_summary(self, cfg: Config, items: List[doctor.CheckItem]) -> None:
        ok_n = sum(1 for i in items if i.ok)
        theme_name = {"auto": "跟随系统", "light": "浅色", "dark": "深色"}.get(
            cfg.theme, cfg.theme)
        has_key = bool(cfg.profile().api_key)
        model_txt = f"{cfg.profile().model}" if has_key else "未配置（可离线转写）"
        # 每栏独占一行：比例字体里连续空格不产生对齐效果，长模型名还会折行
        self.summary.setText(
            f"外观：{theme_name}\n"
            f"纠错模型：{model_txt}\n"
            f"环境：{ok_n}/{len(items)} 项通过\n"
            f"配置保存在 SSData\\config.json")


# ==================================================================== 主向导
class WelcomeWizard(QDialog):
    """欢迎 + 配置向导。用法：WelcomeWizard(cfg).exec_()"""

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("欢迎使用 Subtitle Studio")
        self.setWindowIcon(app_icon())
        self.resize(720, 560)
        self.setMinimumSize(620, 500)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 顶部进度圆点
        self.dots_host = QWidget(self)
        dh = QHBoxLayout(self.dots_host)
        dh.setContentsMargins(36, 14, 36, 0)
        dh.setSpacing(8)
        dh.addStretch(1)
        self.dot_labels: List[QLabel] = []
        for i in range(5):
            d = QLabel(self.dots_host)
            d.setFixedSize(10, 10)
            d.setStyleSheet("border-radius:5px;background:#c0c0c0;")
            dh.addWidget(d)
            self.dot_labels.append(d)
        dh.addStretch(1)
        v.addWidget(self.dots_host)

        self.stack_host = QWidget(self)
        self.stack = QVBoxLayout(self.stack_host)
        self.stack.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.stack_host, 1)

        # 五个页面
        self.pages: List[_Page] = []
        for cls in (_WelcomePage, _AppearancePage, _ModelPage, _CheckPage,
                    _DonePage):
            pg = cls(self)
            pg.setVisible(False)
            self.stack.addWidget(pg)
            self.pages.append(pg)
        self._idx = 0
        self._anim_lock = False

        # 底部导航
        nav = QHBoxLayout()
        nav.setContentsMargins(36, 8, 36, 16)
        self.btn_back = PushButton("上一步", self)
        self.btn_back.clicked.connect(self._go_back)
        self.btn_back.setVisible(False)       # 第一页不显示，_show_page 会再调
        nav.addWidget(self.btn_back)
        nav.addStretch(1)
        self.btn_next = PrimaryPushButton("下一步", self)
        self.btn_next.setMinimumWidth(140)
        self.btn_next.clicked.connect(self._go_next)
        nav.addWidget(self.btn_next)
        self.btn_finish = PrimaryPushButton("完成", self)
        self.btn_finish.setMinimumWidth(140)
        self.btn_finish.clicked.connect(self._finish)
        self.btn_finish.setVisible(False)
        nav.addWidget(self.btn_finish)
        v.addLayout(nav)
        self._nav_ready = True
        self._show_page(0)

    # ------------------------------------------------------------ 导航
    def _show_page(self, idx: int, direction: int = 0) -> None:
        """切页。direction: +1 前进 / -1 后退 / 0 首次进入（无动画）。"""
        prev = self.pages[self._idx] if hasattr(self, "_idx") else None
        self._idx = idx
        cur = self.pages[idx]
        for i, pg in enumerate(self.pages):
            pg.setVisible(i == idx)
        self._paint_dots(idx)
        is_first = idx == 0
        is_last = idx == len(self.pages) - 1
        self.btn_back.setVisible(not is_first)
        self.btn_next.setVisible(not is_last)
        self.btn_finish.setVisible(is_last)
        if not is_last:
            self.btn_next.setText("下一步" if idx < len(self.pages) - 2
                                  else "去体检")
        cur.on_enter()
        self.update_nav()
        if is_last and hasattr(self.pages[-1], "refresh_summary"):
            self.pages[-1].refresh_summary(self.cfg, self.pages[3]._items)
        # ---- 动效 ----
        if direction and prev is not None and prev is not cur:
            # 旧页滑出 → 新页从另一侧推入（macOS 前进右推/后退左推）
            wizard_fx.page_in(cur, direction)

    def _paint_dots(self, idx: int) -> None:
        """进度点：当前 = 实心强调色胶囊，已过 = 半透明强调色，未到 = 灰。"""
        from .theme import accent_hex
        acc = accent_hex()
        for i, d in enumerate(self.dot_labels):
            done = i < idx
            cur = i == idx
            if is_dark():
                bg = (acc if cur else "#2f5f8a" if done else "#3a3a3a")
            else:
                bg = (acc if cur else "#8fb8e0" if done else "#c0c0c0")
            d.setStyleSheet(f"border-radius:5px;background:{bg};")
            if cur:
                d._from_w = d.width()

    def update_nav(self) -> None:
        pg = self.pages[self._idx]
        ok = pg.is_valid()
        if self._idx == len(self.pages) - 1:
            self.btn_finish.setEnabled(True)
        else:
            self.btn_next.setEnabled(ok)

    def _go_next(self) -> None:
        pg = self.pages[self._idx]
        if not pg.is_valid() or self._anim_lock:
            return
        if isinstance(pg, _ModelPage):
            pg.apply(self.cfg)
        nxt = min(self._idx + 1, len(self.pages) - 1)
        if nxt == self._idx:
            return
        self._anim_lock = True
        out = self.pages[self._idx]
        wizard_fx.page_out(out, +1,
                           on_done=lambda: (wizard_fx.clear_effect(out),
                                            out.setVisible(False)))
        # 不等退出走完就开始推入（重叠节奏更像 macOS），退出动画自己会收尾
        self._show_page(nxt, +1)
        QTimer.singleShot(wizard_fx._PAGE_MS + 60,
                          lambda: setattr(self, "_anim_lock", False))

    def _go_back(self) -> None:
        if self._anim_lock:
            return
        prev = max(self._idx - 1, 0)
        if prev == self._idx:
            return
        self._anim_lock = True
        out = self.pages[self._idx]
        wizard_fx.page_out(out, -1,
                           on_done=lambda: (wizard_fx.clear_effect(out),
                                            out.setVisible(False)))
        self._show_page(prev, -1)
        QTimer.singleShot(wizard_fx._PAGE_MS + 60,
                          lambda: setattr(self, "_anim_lock", False))

    def _shutdown_worker(self) -> None:
        """关向导收尾：请求取消 → 限时等待 → 等不到就孤儿化。

        pip 装大包/镜像黑洞时 3 秒等不到很常见；只 wait 不兜底的话，
        析构连带给还在跑的 QThread 上西天 → abort 闪退。
        """
        from .workers import orphanize, reap
        for pg in self.pages:
            if isinstance(pg, _CheckPage):
                w = getattr(pg, "_worker", None)
                if w is None:
                    continue
                pg._worker = None
                try:
                    if w.isRunning():
                        w.cancel()      # pip 逐行读时轮询 _cancel_flag
                        if not w.wait(3000):
                            orphanize(w)
                    else:
                        reap(w)
                except RuntimeError:
                    pass
            # 模型页连接测试线程同样可能是活的
            w2 = getattr(pg, "_worker", None) if not isinstance(pg, _CheckPage) else None
            if w2 is not None:
                try:
                    pg._worker = None
                    if w2.isRunning():
                        w2.cancel()
                        if not w2.wait(2000):
                            orphanize(w2)
                    else:
                        reap(w2)
                except RuntimeError:
                    pass

    def _finish(self) -> None:
        """完成：直接落盘关闭（不做扩张/级联仪式动画）。"""
        if self._anim_lock:
            return
        self._anim_lock = True
        # 体检页若还在装包，请求取消并稍候——不等的话对话框销毁时线程
        # 还在跑，QThread 析构直接 abort 闪退
        self._shutdown_worker()
        app_pg = self.pages[1]
        if isinstance(app_pg, _AppearancePage):
            app_pg.apply(self.cfg)
        self.cfg.setup_done = True
        self.cfg.save()
        self.accept()

    def reject(self) -> None:  # noqa: N802
        """Esc / 点 X 中途关向导：同 _finish，先取消装包线程再关。"""
        self._shutdown_worker()
        super().reject()


def maybe_show_welcome(cfg: Config, parent=None) -> bool:
    """首次使用时弹欢迎向导。返回 False = 用户中途关掉向导（罕见），照常进主界面。"""
    if getattr(cfg, "setup_done", False):
        return True
    dlg = WelcomeWizard(cfg, parent)
    dlg.setModal(True)
    dlg.exec_()
    return True
