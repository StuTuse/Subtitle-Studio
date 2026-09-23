"""播放器：QMediaPlayer + QVideoWidget。

提供播放/暂停、逐帧式快退快进、倍速、静音、A/B 循环，以及 seek 请求信号。
QMediaPlayer 在某些 Windows 机器上对 H.265/10bit 支持有限，这里做了降级提示。
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt5.QtCore import QUrl, pyqtSignal
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]


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
            self.error.emit("该视频无法在此解码（可能是 H.265/10bit 或缺少解码器）。"
                            "字幕与时间轴仍可正常编辑，仅预览受限。")

    def _on_err(self, _err: int) -> None:
        s = self.player.errorString() or ""
        if s:
            self.error.emit(f"播放器：{s}")
