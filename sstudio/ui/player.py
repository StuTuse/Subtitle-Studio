"""播放器：QMediaPlayer + QVideoWidget。

提供播放/暂停、逐帧式快退快进、倍速、静音、A/B 循环，以及 seek 请求信号。
QMediaPlayer 在某些 Windows 机器上对 H.265/10bit 支持有限，这里做了降级提示。
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt5.QtCore import QUrl, pyqtSignal, Qt
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import QSizePolicy, QVBoxLayout, QWidget, QLabel

from ..core.i18n import S

SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]

# 成片检查标准与 export_page 一致：单行超 28 字会进导出警告。
# overlay 预览按同一规则折行，所见即导出效果。
OVERLAY_MAX_CHARS = 28


def wrap_subtitle(text: str, max_chars: int = OVERLAY_MAX_CHARS) -> str:
    """按成片检查标准折行：先保留手动的 \\n，再对超长行硬折到 28 字。"""
    out = []
    for ln in (text or "").split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        while len(ln) > max_chars:
            out.append(ln[:max_chars])
            ln = ln[max_chars:]
        if ln:
            out.append(ln)
    return "\n".join(out)


class PlayerWidget(QWidget):
    positionChanged = pyqtSignal(float)     # 秒
    durationChanged = pyqtSignal(float)
    stateChanged = pyqtSignal(bool)         # 是否在播放
    error = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._loop_a: Optional[float] = None
        self._loop_b: Optional[float] = None
        self._speed = 1.0
        self._volume = 80
        self._last_volume = 80      # 静音前的音量，取消静音时恢复
        self._media_ok = True

        self.video = QVideoWidget(self)
        self.video.setMinimumHeight(160)
        # Ignored：让布局无视 QVideoWidget 的 sizeHint —— 载入视频后它会变成
        # 视频的完整尺寸（如 950×534），把分栏上半区越撑越大，字幕表被挤到
        # 半屏以下。视频在控件内部本来就等比留黑边，Ignored 不影响画面比例。
        self.video.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.video.setStyleSheet("background:#000;border-radius:8px;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video)

        # 字幕 overlay：当前时间的字幕按 28 字规则折行画在画面底部，
        # 不用开导出就能看到"成片里字幕长什么样"。QLabel 盖在 video 上，
        # 自绘描边样式模拟播放器渲染。
        self.overlay = QLabel(self.video)
        self.overlay.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        self.overlay.setWordWrap(True)
        self.overlay.setStyleSheet(
            "color:#fff;background:rgba(0,0,0,140);"
            "border-radius:4px;padding:4px 10px;"
            "font-size:14px;font-weight:500;")
        self.overlay.setVisible(False)
        self._overlay_text = ""

        # 解码失败常驻角标：H.265/10bit 黑屏时用户要一直看得见原因
        self.badge = QLabel(self.video)
        self.badge.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.badge.setWordWrap(True)
        self.badge.setStyleSheet(
            "color:#ffb020;background:rgba(20,20,20,220);"
            "border:1px solid rgba(255,176,32,120);border-radius:8px;"
            "padding:14px 22px;font-size:14px;")
        self.badge.setVisible(False)
        self._badge_text = ""

        self.player = QMediaPlayer(self, QMediaPlayer.VideoSurface)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(lambda ms: self.durationChanged.emit(ms / 1000.0))
        self.player.stateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.error.connect(self._on_err)

    # ------------------------------------------------------------ 控制
    def shutdown(self) -> None:
        """进程退出前显式拆解媒体后端。

        Windows 上 QMediaPlayer 走 DirectShow：若在 Qt/Python 拆机阶段
        才被动析构（graph 还在跑、信号还会回调进已死的 Python 对象），
        会随机触发 0xC0000005 —— 表现为"窗口关了进程卡住/弹崩溃框"。
        在事件循环还活着、控件还健在时主动停+卸媒体+断信号，可消除竞态。
        """
        try:
            for sig in (self.player.positionChanged, self.player.durationChanged,
                        self.player.stateChanged, self.player.mediaStatusChanged,
                        self.player.error):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            self.player.stop()
            self.player.setMedia(QMediaContent())     # 真正释放解码 graph
            self.player.setVideoOutput(None)
        except Exception:
            pass

    def load(self, path: str) -> None:
        if not path or not os.path.isfile(path):
            return
        self.set_badge("")       # 换视频：清掉上一个的解码失败角标
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(os.path.abspath(path))))
        self._loop_a = self._loop_b = None
        self.player.setVolume(self._volume)

    def play(self) -> None:
        if self.player.state() != QMediaPlayer.PlayingState:
            self.player.play()

    def pause(self) -> None:
        self.player.pause()

    def toggle(self) -> None:
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    @property
    def playing(self) -> bool:
        return self.player.state() == QMediaPlayer.PlayingState

    def position(self) -> float:
        return self.player.position() / 1000.0

    def duration(self) -> float:
        return self.player.duration() / 1000.0

    def seek(self, seconds: float) -> float:
        """定位。返回实际生效的秒数；未加载媒体时返回 -1 且不发信号——
        曾无条件 emit positionChanged，把播放头/时间标签推到一个从未
        真正生效的位置，且 dur 会回落到上一个工程的残留时长。"""
        sec = max(0.0, seconds)
        dur = 0.0
        try:
            dur = self.duration()
            if dur > 0:
                sec = min(sec, dur)           # 夹回实际媒体长度
                self.player.setPosition(int(sec * 1000))
            else:
                return -1.0                   # 没有媒体：不下发、不广播
        except Exception:
            return -1.0
        self.positionChanged.emit(sec)
        return sec

    def nudge(self, delta: float) -> None:
        self.seek(self.position() + delta)

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, min(4.0, speed))
        try:
            self.player.setPlaybackRate(self._speed)
        except Exception:
            pass

    def speed(self) -> float:
        return self._speed

    def cycle_speed(self) -> float:
        try:
            i = SPEEDS.index(self._speed)
        except ValueError:
            i = 3
        self.set_speed(SPEEDS[(i + 1) % len(SPEEDS)])
        return self._speed

    def set_volume(self, v: int) -> None:
        self._volume = max(0, min(100, int(v)))
        self.player.setVolume(self._volume)

    def volume(self) -> int:
        return self._volume

    def toggle_mute(self) -> int:
        # 静音要记住上次音量：取消静音回到原音量，而不是硬跳 80
        if self._volume:
            self._last_volume = self._volume
            self.set_volume(0)
        else:
            self.set_volume(getattr(self, "_last_volume", 80) or 80)
        return self._volume

    # ------------------------------------------------------------ A/B 循环
    def set_loop_a(self, t: Optional[float] = None) -> None:
        self._loop_a = self.position() if t is None else t
        # A>B 会让 _on_pos 每个 tick 从 loop_a 反复 seek——表现为播放头
        # 原地抖动。校验兜底，必要时先清掉另一端。
        if self._loop_b is not None and self._loop_a is not None \
                and self._loop_b < self._loop_a:
            self._loop_b = None

    def set_loop_b(self, t: Optional[float] = None) -> None:
        self._loop_b = self.position() if t is None else t
        if self._loop_a is not None and self._loop_b is not None \
                and self._loop_b < self._loop_a:
            self._loop_a = None

    def clear_loop(self) -> None:
        self._loop_a = self._loop_b = None

    def loop(self):
        return self._loop_a, self._loop_b

    # ------------------------------------------------------------ 字幕 overlay
    def set_subtitle(self, text: str) -> None:
        """显示/隐藏字幕预览。text 为当前时间的字幕文本，空串隐藏。

        折行与导出检查同一标准（28 字），表格里的"两行"与成片效果
        从此一致。

        tick 每 50-250ms 调一次：文本没变时直接返回（旧版无条件
        setText+adjustSize+setVisible，10k 条文档每 tick 三遍 Python
        循环之外还多出一轮整控件重排——文本不变时这些全是白做）。
        """
        t = wrap_subtitle(text or "")
        if t == getattr(self, "_overlay_text", None):
            return
        self._overlay_text = t
        if t:
            self.overlay.setText(t)
            self.overlay.adjustSize()
            self._place_overlay()
            self.overlay.setVisible(True)
        else:
            self.overlay.setVisible(False)

    def subtitle_text(self) -> str:
        return self._overlay_text

    def _place_overlay(self) -> None:
        # 底边贴视频区下沿、宽不超视频 90%：超宽靠 wordWrap 竖排
        w = self.video.width()
        h = self.video.height()
        self.overlay.setMaximumWidth(int(w * 0.9))
        self.overlay.move((w - self.overlay.width()) // 2,
                          h - self.overlay.height() - int(h * 0.06))

    def resizeEvent(self, ev) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(ev)
        if self.overlay.isVisible():
            self._place_overlay()
        if self.badge.isVisible():
            self._place_badge()

    # ------------------------------------------------------------ 内部
    def _on_pos(self, ms: int) -> None:
        sec = ms / 1000.0
        if self._loop_b is not None and self._loop_a is not None and sec >= self._loop_b:
            self.seek(self._loop_a)
            return
        self.positionChanged.emit(sec)

    def _on_state(self, state: int) -> None:
        self.stateChanged.emit(state == QMediaPlayer.PlayingState)

    def _on_status(self, status: int) -> None:
        if status == QMediaPlayer.InvalidMedia:
            # 常驻角标而非 6 秒小字：用户对着黑屏播放器会以为程序坏了——
            # 提示必须在屏幕上一直可见，直到换可解码的视频
            self.set_badge(S("⚠ 无法解码此视频\n可能是 H.265/10bit 或缺少解码器\n"
                             "字幕与时间轴仍可正常编辑",
                             "⚠ Cannot decode this video\n"
                             "Possibly H.265/10bit or a missing codec\n"
                             "Subtitles and timeline still work"))
            self.error.emit(S("该视频无法在此解码（可能是 H.265/10bit 或缺少解码器）。"
                              "字幕与时间轴仍可正常编辑，仅预览受限。",
                              "This video cannot be decoded here (possibly H.265/10bit "
                              "or a missing codec). Subtitles and the timeline still "
                              "work; preview only is limited."))

    def set_badge(self, text: str) -> None:
        """常驻角标（如解码失败提示）。空串隐藏。"""
        self._badge_text = text or ""
        if self._badge_text:
            self.badge.setText(self._badge_text)
            self.badge.adjustSize()
            self._place_badge()
            self.badge.setVisible(True)
        else:
            self.badge.setVisible(False)

    def badge_text(self) -> str:
        return self._badge_text

    def _place_badge(self) -> None:
        w = self.video.width()
        self.badge.setMaximumWidth(int(w * 0.92))
        self.badge.adjustSize()
        self.badge.move((w - self.badge.width()) // 2,
                        max(8, (self.video.height() - self.badge.height()) // 2))

    def _on_err(self, _err: int) -> None:
        s = self.player.errorString() or ""
        if s:
            self.error.emit(S("播放器：", "Player: ") + s)
