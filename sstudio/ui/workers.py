"""把耗时操作放到 QThread 里，避免卡住界面。

统一约定：
* 每个 Worker 都有 ``sig_done(result)`` / ``sig_failed(msg)`` / ``sig_progress(msg, pct)``。
* ``cancel()`` 只是设标志，由被调方轮询（转写按 segment 轮询；LLM 批次间轮询）。
"""

from __future__ import annotations

import os
import traceback
from typing import Any, Callable, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from ..core import llm, media, transcriber
from ..core.config import Config
from ..core.model import Cue, CueDocument


class _BaseWorker(QThread):
    """QThread 子类：调用方 .start() 即后台执行 run()。

    信号从工作线程发出、在主线程槽里接收（Qt 自动 queued），
    所以槽函数里可以放心动界面。
    """

    sig_progress = pyqtSignal(str, float)
    sig_done = pyqtSignal(object)
    sig_failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def cancelled(self) -> bool:
        return self._cancel

    def _progress(self, msg: str, pct: float = -1.0) -> None:
        self.sig_progress.emit(msg, float(pct))


_pending_reap: set = set()

# ThreadedCall 的回调占位：构造时被替换成信号发射器（见 ThreadedCall）
CB_PROGRESS = object()
CB_LOG = object()
CB_CANCEL = object()


def reap(w: Optional["_BaseWorker"]) -> None:
    """安全回收 worker：线程没结束前必须保住 Python 引用，
    否则 GC 会在运行中析构 QThread（Qt 直接 abort）。非阻塞、可重入。"""
    if w is None:
        return
    try:
        if getattr(w, "_reaped", False):
            return                  # 已注册过：重复 reap 会重复 connect finished
        w._reaped = True
        if w.isFinished():
            _pending_reap.discard(w)
            w.deleteLater()
            return
        _pending_reap.add(w)

        def _cleanup(_w=w):
            _pending_reap.discard(_w)
            _w.deleteLater()

        w.finished.connect(_cleanup)
    except RuntimeError:
        pass


_orphans: list = []


def orphanize(w: Optional[QThread]) -> None:
    """限时 wait() 等不到时的兜底：把仍在跑的线程从将死的对话框上摘下来。

    对话框析构会连带析构作为子对象的 QThread——线程还在跑时 Qt 直接
    abort（QThread: Destroyed while thread is still running）。pip 镜像
    黑洞/大包下载时 3 秒等不到很常见，所以关窗路径在 wait 超时后调用
    本函数：断开全部信号（槽的接收者可能已销毁）、摘掉父子关系、把
    Python 引用交给模块级注册表看管，线程自然退出后再 deleteLater。
    """
    if w is None:
        return
    try:
        if not w.isRunning():
            return
        try:
            w.disconnect()          # 不再回调进即将销毁的对话框控件
        except Exception:
            pass
        try:
            w.setParent(None)       # 摘掉父子：对话框析构不再连带析构线程
        except RuntimeError:
            return
        if getattr(w, "_reaped", False):
            _pending_reap.discard(w)
        w._reaped = True
        _orphans.append(w)

        def _gc(_w=w):
            try:
                _orphans.remove(_w)
            except ValueError:
                pass
            try:
                _w.deleteLater()
            except RuntimeError:
                pass

        w.finished.connect(_gc)
    except RuntimeError:
        pass


class TranscribeWorker(_BaseWorker):
    """抽音频 -> 转写。一条命令走完整流程，进度统一映射到 0–1。"""

    sig_stage = pyqtSignal(str)

    # 各阶段在总进度条里占的区间
    EXTRACT_SPAN = (0.02, 0.15)
    TRANSCRIBE_SPAN = (0.15, 1.0)

    def __init__(self, video_path: str, cfg: Config, keep_audio: bool = False):
        super().__init__()
        self.video_path = video_path
        self.cfg = cfg
        self.keep_audio = keep_audio
        self.current_wav = ""          # 提取出的临时 wav 路径，供退出兜底清理

    def run(self) -> None:
        wav = ""
        try:
            self._progress("正在读取媒体信息…", self.EXTRACT_SPAN[0])
            info = media.probe(self.video_path)
            if not info.audio_codec and info.duration == 0:
                self.sig_failed.emit("无法读取这个文件，或文件不含音频。")
                return
            lo, hi = self.EXTRACT_SPAN

            def _ext(msg: str, pct: float) -> None:
                p = lo + (hi - lo) * (0.5 if pct is None or pct < 0 else min(1.0, pct))
                self._progress(f"① {msg}", p)

            wav = media.extract_audio(self.video_path, progress=_ext,
                                      cancel=self.cancelled)
            self.current_wav = wav
            if self.cancelled():
                self.sig_failed.emit("已取消。")
                return
            self.sig_stage.emit("transcribing")
            lo2, hi2 = self.TRANSCRIBE_SPAN

            def _tr(msg: str, pct: float) -> None:
                p = lo2 + (hi2 - lo2) * (0.0 if pct is None or pct < 0 else min(1.0, pct))
                self._progress(f"② {msg}", p)

            res = transcriber.transcribe(wav, self.cfg, progress=_tr,
                                         cancel=self.cancelled)
            doc = CueDocument(source_video=os.path.abspath(self.video_path),
                              duration=info.duration, language=res.meta.get("language", ""),
                              cues=res.cues)
            doc.meta.update(res.meta)
            # 可选：消除字幕间小空隙（连续说话时防字幕闪断）
            if getattr(self.cfg, "auto_close_gaps", True) and doc.cues:
                mg = float(getattr(self.cfg, "gap_max", 0.35) or 0.35)
                touched, saved = doc.close_gaps(mg)
                doc.meta["gaps_closed"] = touched
                doc.meta["gaps_saved"] = round(saved, 1)
            if self.cancelled():
                self.sig_failed.emit("已取消。")
                return
            self._progress(f"完成：{len(doc.cues)} 条字幕", 1.0)
            self.sig_done.emit(doc)
        except Exception as e:
            tb = traceback.format_exc()
            self.sig_failed.emit(str(e) or tb.splitlines()[-1])
        finally:
            self.current_wav = ""
            if wav and not self.keep_audio:
                try:
                    if os.path.isfile(wav) and "audio" in wav:
                        os.remove(wav)
                except OSError:
                    pass


class FixWorker(_BaseWorker):
    """LLM 批量纠错。"""

    sig_cue = pyqtSignal(int, str)   # row, text

    def __init__(self, cfg: Config, cues: List[Cue], extra: str = ""):
        super().__init__()
        self.cfg = cfg
        self.cues = cues
        self.extra = extra

    def run(self) -> None:
        try:
            res = llm.fix_document(self.cfg, self.cues, progress=self._progress,
                                   cancel=self.cancelled, on_cue=self._on_cue,
                                   extra=self.extra)
            self.sig_done.emit(res)
        except llm.LLMPartialError as e:
            # 中途断连：已修正的批次成果原样上交（界面照样刷新/统计），
            # 失败原因附在结果里，用户不会以为全白跑
            r = e.partial_result
            r.failures.append(str(e).splitlines()[0])
            self.sig_done.emit(r)
        except Exception as e:
            self.sig_failed.emit(llm._friendly_err(e) if not isinstance(e, ValueError) else str(e))

    def _on_cue(self, row: int, text: str) -> None:
        self.sig_cue.emit(row, text)


class ChatWorker(_BaseWorker):
    """自由对话 / 自定义提示词处理。"""

    sig_chunk = pyqtSignal(str)

    def __init__(self, prof, system: str, user: str):
        super().__init__()
        self.prof = prof
        self.system = system
        self.user = user

    def run(self) -> None:
        try:
            out = llm.rewrite_with_llm(self.prof, self.system, self.user,
                                       on_delta=lambda d: self.sig_chunk.emit(d))
            self.sig_done.emit(out)
        except Exception as e:
            self.sig_failed.emit(llm._friendly_err(e))


class TestLLMWorker(_BaseWorker):
    def __init__(self, parent, prof):
        super().__init__(parent)
        self.prof = prof

    def run(self) -> None:
        try:
            ok, msg, dt = llm.test_connection(self.prof)
            self.sig_done.emit((ok, msg, dt))
        except Exception as e:
            self.sig_failed.emit(llm._friendly_err(e))


class ThreadedCall(QThread):
    """通用：在线程里跑一个函数，把返回值/异常送回主线程。

    fn 的签名可以带三个**线程安全回调**（由本类注入，调用方在 a/kw 里
    用占位对象 :data:`CB_PROGRESS` / :data:`CB_LOG` / :data:`CB_CANCEL`
    占位即可）：回调在工作线程被调用，经 queued 信号转回主线程执行
    调用方 connect 到 sig_progress / sig_log 的 UI 更新。以前调用方把
    碰控件的闭包直接递进 fn，setValue/setText 就在工作线程跑——跨线程
    UI 访问，体检页/向导"一键修复"偶发闪退的根因。
    """

    sig_progress = pyqtSignal(str, float)
    sig_done = pyqtSignal(object)
    sig_failed = pyqtSignal(str)
    sig_log = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], *a, **kw):
        super().__init__()
        self._cancel_flag = [False]
        # 占位回调替换为信号发射器；fn 内部调用它们时跨线程转发
        a = tuple(self._progress if x is CB_PROGRESS else
                  self._log if x is CB_LOG else
                  self._cancel if x is CB_CANCEL else x
                  for x in a)
        kw = {k: self._progress if v is CB_PROGRESS else
              self._log if v is CB_LOG else
              self._cancel if v is CB_CANCEL else v
              for k, v in kw.items()}
        self.fn, self.a, self.kw = fn, a, kw

    def _progress(self, msg: str, pct: float = -1.0) -> None:
        self.sig_progress.emit(str(msg), float(pct))

    def _log(self, line: str) -> None:
        self.sig_log.emit(str(line))

    def _cancel(self) -> bool:
        return self._cancel_flag[0]

    def cancel(self) -> None:
        self._cancel_flag[0] = True

    def run(self) -> None:
        try:
            self.sig_done.emit(self.fn(*self.a, **self.kw))
        except Exception as e:
            self.sig_failed.emit(f"{type(e).__name__}: {e}")

