# -*- coding: utf-8 -*-
"""首次启动体检向导：检查必要组件，缺啥点一下就补装。

触发：首次使用（无配置文件）时自动弹一次；设置页「环境体检」按钮可随时重开。
只有 required 缺失时才强制「修复后才能开始用」；其余都可以「稍后再说」。
"""

from __future__ import annotations

import os
from typing import List

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

try:
    from qfluentwidgets import (BodyLabel, CardWidget, CaptionLabel, InfoBar,
                                PrimaryPushButton, ProgressRing, StrongBodyLabel,
                                TitleLabel)
except Exception:  # pragma: no cover
    CardWidget = QWidget
    BodyLabel = QLabel
    CaptionLabel = QLabel
    StrongBodyLabel = QLabel
    TitleLabel = QLabel
    PrimaryPushButton = QPushButton

    def InfoBar(*a, **kw):
        class _N:
            @staticmethod
            def success(**kw):
                pass
            @staticmethod
            def warning(**kw):
                pass
            @staticmethod
            def error(**kw):
                pass
        return _N()

    def ProgressRing(*a, **kw):
        return QProgressBar()

from ..core import doctor
from ..core.config import Config
from ..core.i18n import S
from .theme import dim_span, status_hex
from .workers import ThreadedCall, reap

_LEVEL_TAG = {"required": S("必需", "Required"), "recommend": S("建议", "Recommended"),
              "optional": S("可选", "Optional")}


class FirstRunDialog(QDialog):
    """体检窗口。用法：dlg = FirstRunDialog(cfg); dlg.exec_()"""

    def __init__(self, cfg: Config, parent=None, auto_fix: bool = False):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle(S("环境体检 · Subtitle Studio",
                              "Environment check · Subtitle Studio"))
        self.resize(640, 560)
        self.setMinimumSize(560, 480)
        self._items: List[doctor.CheckItem] = []
        self._log: List[str] = []
        self._fixing_id = ""
        self._worker = None
        self._build_ui()
        self._auto_fix = bool(auto_fix)
        QTimer.singleShot(60, self._run_checks)

    # ------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 16)
        v.setSpacing(10)

        self.title = TitleLabel(S("正在检查运行环境…", "Checking your environment…"), self)
        v.addWidget(self.title)
        self.subtitle = CaptionLabel(
            S("只检查本机组件，不上传任何信息。缺什么可以一键补装。",
              "Checks local components only; nothing is uploaded. "
              "Missing pieces can be installed with one click."), self)
        v.addWidget(self.subtitle)

        self.ring = ProgressRing(self)
        self.ring.setFixedSize(22, 22)

        # 检查结果列表
        self.rows_host = QVBoxLayout()
        self.rows_host.setSpacing(6)
        v.addLayout(self.rows_host)

        v.addStretch(1)

        # 修复进度（默认隐藏）
        self.fix_bar = QProgressBar(self)
        self.fix_bar.setRange(0, 100)
        self.fix_bar.setVisible(False)
        v.addWidget(self.fix_bar)
        self.fix_label = CaptionLabel("", self)
        self.fix_label.setVisible(False)
        self.fix_label.setWordWrap(True)
        v.addWidget(self.fix_label)

        # 日志折叠区（默认隐藏）
        self.log_box = QPlainTextEdit(self)
        self.log_box.setReadOnly(True)
        self.log_box.setFont(self.font())
        self.log_box.setVisible(False)
        self.log_box.setMinimumHeight(120)
        v.addWidget(self.log_box, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.btn_log = QPushButton(S("详细日志", "Details"), self)
        self.btn_log.clicked.connect(self._toggle_log)
        btns.addWidget(self.btn_log)
        btns.addStretch(1)
        self.btn_fix_all = PrimaryPushButton(S("一键修复", "Fix all"), self)
        self.btn_fix_all.clicked.connect(self._fix_all)
        btns.addWidget(self.btn_fix_all)
        self._required_ok = True     # 必需组件是否齐全；决定关闭按钮的真实语义
        self.btn_close = QPushButton(S("稍后再说", "Later"), self)
        self.btn_close.clicked.connect(self._on_close)
        btns.addWidget(self.btn_close)
        v.addLayout(btns)

    def _add_row(self, it: doctor.CheckItem) -> None:
        card = CardWidget(self)
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(10)
        mark = QLabel("✓" if it.ok else ("✕" if it.level == "required" else "⚠"), card)
        f = mark.font(); f.setBold(True); f.setPointSize(12); mark.setFont(f)
        mk = 'ok' if it.ok else ('err' if it.level == "required" else 'warn')
        mark.setStyleSheet(f"color:{status_hex(mk)}")
        mark.setFixedWidth(18)
        h.addWidget(mark)

        mid = QVBoxLayout()
        mid.setSpacing(1)
        name = BodyLabel(f"{it.title}（{_LEVEL_TAG.get(it.level, it.level)}）", card)
        mid.addWidget(name)
        det = it.detail or ""
        from html import escape as _esc
        # detail 常含探测到的路径（& < > 都可能出现）：RichText 下直接拼会破坏解析
        line2 = it.why + (f"　{dim_span(_esc(det))}" if det else "")
        sub = CaptionLabel(line2, card)
        sub.setWordWrap(True)
        try:
            sub.setTextFormat(Qt.RichText)
        except Exception:
            pass
        mid.addWidget(sub)
        h.addLayout(mid, 1)

        if it.fixable and not it.ok:
            btn = PrimaryPushButton(it.fix_note or S("安装", "Install"), card)
            btn.clicked.connect(lambda _=False, i=it: self._fix_one(i))
            h.addWidget(btn)
        elif it.fix_note and not it.ok:
            tip = CaptionLabel(S("手动安装", "Manual install"), card)
            tip.setToolTip(it.fix_note)
            h.addWidget(tip)
        self.rows_host.addWidget(card)
        it._row_btn = btn if (it.fixable and not it.ok) else None   # type: ignore

    def _clear_rows(self) -> None:
        while self.rows_host.count():
            r = self.rows_host.takeAt(0)
            if r.widget():
                r.widget().deleteLater()

    # ------------------------------------------------------------ 关闭
    def _on_close(self) -> None:
        """必需组件没装齐时，这个按钮显示"退出程序"，就必须真的退出。

        否则用户带着缺 PyQt 组件 / 没有转写引擎的半残界面继续用，
        随便点一下才是更难解释的报错。注意这里不能直接 QApplication.quit()：
        exec_() 自己就是一层事件循环，quit 只会把它弹回调用处——主窗口
        照样会冒出来。置 abort_app 标志，由 maybe_show_first_run 的调用方
        决定结束进程。
        """
        if self._required_ok:
            self.reject()
            return
        self.abort_app = True
        self.reject()

    # 注意：本类只保留一个 closeEvent。曾有两个同名定义——「必需组件缺失
    # 就别往下走」的守卫在前、worker 等待收尾在后，Python 后者覆盖前者，
    # 守卫被静默废掉（缺组件时 X 也能关窗，用户带着半残界面继续点）。
    def closeEvent(self, e) -> None:  # noqa: N802
        if not self._required_ok:
            e.ignore()
            self._on_close()        # X 等同「退出程序」：abort_app=True
            return
        self._shutdown_worker()
        super().closeEvent(e)

    # ------------------------------------------------------------ 检查
    def _run_checks(self) -> None:
        # CUDA 项只读 doctor 的预探测缓存（见 doctor.preprobe_gpu 注释：
        # 媒体后端激活过的进程里现场枚举会 access violation），此处不补
        # preprobe_gpu——空缓存按「未检测到 GPU」处理。
        self._items = doctor.check_all()
        self._clear_rows()
        for it in self._items:
            self._add_row(it)
        need = [i for i in self._items if not i.ok and i.fixable]
        s = doctor.summary(self._items)
        self.title.setText(s)
        if doctor.all_required_ok(self._items):
            self._required_ok = True
            self.btn_close.setText(S("完成，开始使用", "Done, start using"))
            self.btn_fix_all.setVisible(bool(need))
            self.btn_fix_all.setText(S(f"一键修复（{len(need)}）", f"Fix all ({len(need)})")
                                     if need else "")
        else:
            self._required_ok = False
            self.btn_close.setText(S("退出程序", "Quit"))
            self.btn_fix_all.setVisible(True)
            self.btn_fix_all.setText(S(f"一键修复（{len(need)}）", f"Fix all ({len(need)})"))
        if getattr(self, "_auto_fix", False):
            # 等检查结果真正回来再自动修复：定在 400ms 的定时器在慢机器上
            # 会抢在 _run_checks 之前跑，静默空转一次
            self._auto_fix = False
            QTimer.singleShot(0, self._fix_all)

    # ------------------------------------------------------------ 修复
    def _fix_all(self) -> None:
        todo = [i for i in self._items if not i.ok and i.fixable]
        if todo:
            self._fix_many(todo)

    def _fix_one(self, it: doctor.CheckItem) -> None:
        self._fix_many([it])

    def _fix_many(self, todo: List[doctor.CheckItem]) -> None:
        # 连点防护（与 settings_page._test 同款）：上一个修复 worker 还在跑时
        # 再点一个「安装」按钮，旧引用会被直接覆盖——两个 pip 子进程并行 +
        # 旧 worker 的 sig_done 回调会 reap 掉正在运行的新 worker
        # （deleteLater 运行中线程是经典闪退源）。先取消并回收旧的。
        w = getattr(self, "_worker", None)
        if w is not None:
            self._worker = None
            try:
                if w.isRunning():
                    w.cancel()
                    if not w.wait(1500):
                        from .workers import orphanize
                        orphanize(w)
                else:
                    reap(w)
            except RuntimeError:
                pass                # 线程对象已被回收（C++ 侧已删）
        self.btn_fix_all.setEnabled(False)
        # 逐项按钮同样禁用：修复中再点别的一项 = 并行 pip + 竞态回收
        for it in self._items:
            b = getattr(it, "_row_btn", None)
            if b is not None:
                b.setEnabled(False)
        # btn_close 保持可用：修复中关窗走 closeEvent 请求取消（见下）。
        # 以前禁用 + 取消恒假，镜像黑洞时模态窗只能任务管理器杀进程。
        self.fix_bar.setVisible(True)
        self.fix_label.setVisible(True)
        self.fix_bar.setValue(2)
        pkgs: List[str] = []
        for it in todo:
            pkgs.extend(it.fix_pkgs)

        def work(progress, log, cancel):
            return doctor.pip_install(pkgs, progress=progress, log=log, cancel=cancel)

        def on_prog(msg: str, pct: float) -> None:
            # pct 已是 0..1（镜像阶段），映射到进度条
            self.fix_bar.setValue(int(max(0, min(1, pct)) * 100))
            self.fix_label.setText(msg)

        def on_log(line: str) -> None:
            self._log.append(line)
            if self.log_box.isVisible():
                self.log_box.setPlainText("\n".join(self._log[-400:]))

        from .workers import ThreadedCall, CB_PROGRESS, CB_LOG, CB_CANCEL
        self._worker = ThreadedCall(work, CB_PROGRESS, CB_LOG, CB_CANCEL)
        # 进度/日志回调经 queued 信号回主线程执行（ThreadedCall 注入包装），
        # 工作线程绝不直接碰控件（跨线程 UI 访问会偶发闪退）。
        self._worker.sig_progress.connect(on_prog)
        self._worker.sig_log.connect(on_log)
        self._worker.setParent(self)
        self._worker.sig_done.connect(lambda res: self._on_fix_done(res, todo))
        self._worker.sig_failed.connect(lambda m: self._on_fix_done((False, m), todo))
        reap(self._worker)
        self._worker.start()

    def _shutdown_worker(self) -> None:
        """关窗收尾：请求取消 → 限时等待 → 等不到就孤儿化（见 workers.orphanize）。

        三条退出路径（X/完成/退出程序/Esc）统一走这里；只 wait 不兜底的
        版本在 pip 镜像黑洞时 3 秒等不到仍会继续析构 → abort 闪退。
        """
        w = getattr(self, "_worker", None)
        if w is None:
            return
        self._worker = None
        try:
            if w.isRunning():
                w.cancel()      # 请求取消；run() 轮询到后走"已取消"分支
                # 给 3s 让 cancel 标志传到逐行读取的检查点
                if not w.wait(3000):
                    from .workers import orphanize
                    orphanize(w)
            else:
                from .workers import reap
                reap(w)
        except RuntimeError:
            pass                # 线程对象已被回收（C++ 侧已删）

    def reject(self) -> None:  # noqa: N802
        """Esc / 程序化关闭同样要收尾线程：exec_() 返回后对话框被 GC，
        析构时线程若还在跑就是 QThread destroyed-while-running。"""
        self._shutdown_worker()
        super().reject()

    def _on_fix_done(self, result, todo) -> None:
        ok, msg = result if isinstance(result, tuple) else (False, str(result))
        reap(self._worker)
        self._worker = None
        self.btn_fix_all.setEnabled(True)
        self.btn_close.setEnabled(True)
        # 恢复逐项修复按钮（_fix_many 里统一禁用过）
        for it in self._items:
            b = getattr(it, "_row_btn", None)
            if b is not None:
                b.setEnabled(True)
        if ok:
            self.fix_bar.setValue(100)
            self.fix_label.setText(S("修复完成，正在重新检查…",
                                     "Fixed. Re-checking…"))
            QTimer.singleShot(600, self._run_checks)
            QTimer.singleShot(2400, lambda: (self.fix_bar.setVisible(False),
                                             self.fix_label.setVisible(False)))
        else:
            self.fix_label.setText(msg)
            InfoBar.error(title="安装失败", content=msg.splitlines()[0][:120],
                          orient=Qt.Horizontal, isClosable=True, duration=8000,
                          parent=self)
            self.btn_log.click() if not self.log_box.isVisible() else None

    def _toggle_log(self) -> None:
        vis = not self.log_box.isVisible()
        self.log_box.setVisible(vis)
        self.btn_log.setText(S("收起日志", "Hide log") if vis else S("详细日志", "Details"))
        if vis:
            self.log_box.setPlainText("\n".join(self._log) or S("（暂无日志）", "(no log yet)"))
            self.resize(self.width(), max(560, self.height()))


def maybe_show_first_run(cfg: Config, parent=None) -> bool:
    """首次使用（配置文件不存在）时弹一次体检窗口。

    返回 False 表示必需组件缺失且用户选择/被迫退出——调用方应结束进程。
    """
    from ..core.config import config_path
    try:
        first = not os.path.isfile(config_path())
    except Exception:
        first = False
    if not first:
        return True
    dlg = FirstRunDialog(cfg, parent)
    dlg.setModal(True)
    dlg.exec_()
    return not getattr(dlg, "abort_app", False)
