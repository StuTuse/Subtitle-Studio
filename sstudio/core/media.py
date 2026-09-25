"""音频抽取 + 时长探测。

优先直接解码视频（PyAV 能读 mp4/mkv/mov，无需外部 ffmpeg）；
若环境里有可用 ffmpeg（系统 PATH 等），则用 ffmpeg 抽成
16kHz 单声道 wav，兼容性最好。
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

Progress = Callable[[str, float], None]


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    has_video: bool = False
    width: int = 0
    height: int = 0
    audio_codec: str = ""
    sample_rate: int = 0


def _si(x: float) -> str:
    if x >= 3600:
        return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{int(x % 60):02d}"
    return f"{int(x // 60):02d}:{int(x % 60):02d}"


VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mts",
              ".m2ts", ".mpg", ".mpeg", ".mxf", ".rmvb", ".vob", ".ogv", ".3gp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr", ".mka"}


def is_media(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (VIDEO_EXTS | AUDIO_EXTS)


def media_filters() -> str:
    # 文件对话框的过滤器标签；en 下给英文标签（扩展名清单是数据，共用）。
    from .i18n import S
    return S("媒体文件", "Media files") + " (%s)" % " ".join(
        "*" + e for e in sorted(VIDEO_EXTS | AUDIO_EXTS))


# ------------------------------------------------------------------ ffmpeg
_EXTRA_FFMPEG: List[str] = []          # 预留给便携版随附的 ffmpeg

_FFMPEG_CACHE: Optional[str] = None


def find_ffmpeg(force: bool = False) -> str:
    """按 PATH → 已知随附工具 → 有限深度常见安装点 找 ffmpeg。找不到返回 ''。

    结果会缓存；绝不做「整盘递归」这种可能扫几十分钟的扫描。
    """
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE is not None and not force:
        return _FFMPEG_CACHE

    found = ""
    p = shutil.which("ffmpeg")
    if p:
        found = p
    if not found:
        for cand in _EXTRA_FFMPEG:
            if os.path.isfile(cand):
                found = cand
                break
    if not found:
        # 有界扫描：只探这些根目录下最多 3 层
        roots = [r"C:\ffmpeg\bin", r"C:\ffmpeg", r"C:\Program Files\ffmpeg\bin",
                 r"D:\ffmpeg\bin", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links")]
        for root in roots:
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                if dirpath[len(root):].count(os.sep) >= 3:
                    dirnames[:] = []
                    continue
                if "ffmpeg.exe" in filenames:
                    found = os.path.join(dirpath, "ffmpeg.exe")
                    break
            if found:
                break
    _FFMPEG_CACHE = found
    return found


def probe(path: str) -> MediaInfo:
    info = MediaInfo(path=path)
    try:
        import av  # PyAV
    except Exception:
        ff = find_ffmpeg()
        if ff:
            out = _run([ff, "-hide_banner", "-i", path], merge_stderr=True)
            for line in out.splitlines():
                if "Duration:" in line:
                    info.duration = _parse_dur(line)
        return info
    try:
        with av.open(path) as c:
            info.duration = (c.duration or 0) / 1_000_000.0
            for s in c.streams.video:
                info.has_video, info.width, info.height = True, s.width or 0, s.height or 0
                break
            for s in c.streams.audio:
                info.audio_codec = s.codec_context.name or ""
                info.sample_rate = s.codec_context.sample_rate or 0
                break
    except Exception:
        pass
    return info


def _parse_dur(line: str) -> float:
    try:
        seg = line.split("Duration:")[1].split(",")[0].strip()
        h, m, rest = seg.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    except Exception:
        return 0.0


def _run(cmd: List[str], merge_stderr: bool = False, timeout: float = 30.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=_flags())
        return (r.stdout or "") + ((r.stderr or "") if merge_stderr else "")
    except subprocess.TimeoutExpired:
        # 损坏/网络流文件会让 ffmpeg 挂死：超时返回错误标记，
        # 上层当探测失败处理（没有时长 → 走 PyAV 兜底或报错），
        # 绝不能在 cancel 生效之前把整条转写链挂死
        return "__ERR__timeout"
    except Exception as e:
        return f"__ERR__{e}"


def _flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def extract_audio(path: str, out_wav: str = "", sr: int = 16000,
                  progress: Optional[Progress] = None,
                  cancel: Optional[Callable[[], bool]] = None) -> str:
    """返回一个 faster-whisper 可直接读取的音频路径（wav 或原文件）。

    带 progress 时解析 ffmpeg 的 -progress 输出给出真实百分比；
    带 cancel 时可中途终止。任一失败回退 PyAV 解码。
    """
    out_wav = out_wav or default_wav_path(path)
    os.makedirs(os.path.dirname(out_wav) or ".", exist_ok=True)
    ff = find_ffmpeg()
    if ff:
        if progress:
            progress("正在抽取音频轨…", 0.0)
        total = 0.0
        try:
            total = probe(path).duration or 0.0
        except Exception:
            total = 0.0
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-progress", "pipe:1", "-i", path,
               "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le",
               "-map", "0:a:0", out_wav]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True,
                                 errors="replace", creationflags=_flags())
        except OSError:
            p = None
        if p is not None:
            last = 0.0
            assert p.stdout is not None
            try:
                for line in p.stdout:
                    if cancel and cancel():
                        raise RuntimeError("已取消。")
                    if not total:
                        continue
                    line = line.strip()
                    secs = -1.0
                    if line.startswith("out_time="):        # 00:01:23.456000
                        try:
                            h, m, s = line[9:].split(":")
                            secs = int(h) * 3600 + int(m) * 60 + float(s)
                        except ValueError:
                            secs = -1.0
                    elif line.startswith("out_time_us="):   # 微秒
                        try:
                            secs = int(line.split("=", 1)[1]) / 1e6
                        except ValueError:
                            secs = -1.0
                    if secs >= 0 and secs - last >= 1.0:
                        last = secs
                        if progress:
                            progress(f"正在抽取音频轨… {_si(secs)} / {_si(total)}",
                                     min(0.999, secs / total))
            finally:
                # 取消/异常路径也要回收 ffmpeg 并清掉写了一半的 wav：
                # 此前 kill 后直接 raise，进程与半成品都留在缓存目录
                try:
                    if p.poll() is None:
                        p.kill()
                        p.wait()
                except OSError:
                    pass
                if os.path.isfile(out_wav) and p.returncode != 0:
                    try:
                        os.remove(out_wav)
                    except OSError:
                        pass
            rc = p.returncode if p.returncode is not None else p.wait()
            if rc == 0 and os.path.isfile(out_wav) and os.path.getsize(out_wav) > 1024:
                return out_wav
    # 回退：PyAV 解码
    return _extract_with_pyav(path, out_wav, sr, progress, cancel)


def _extract_with_pyav(path: str, out_wav: str, sr: int,
                       progress: Optional[Progress],
                       cancel: Optional[Callable[[], bool]] = None) -> str:
    import av
    import wave

    if progress:
        progress("正在解码音频（PyAV）…", -1)
    total = probe(path).duration or 0.0
    c = av.open(path)
    try:
        stream = next(iter(c.streams.audio), None)
        if stream is None:
            # 必须在 wave.open 之前判空：否则留下一个半空 wav 骗过上层的大小校验
            raise RuntimeError("这个文件里没有音频轨。")
        try:
            with wave.open(out_wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
                last_report = 0.0
                for frame in c.decode(stream):
                    if cancel and cancel():
                        raise RuntimeError("已取消。")
                    for rf in resampler.resample(frame):
                        wf.writeframes(rf.to_ndarray().tobytes())
                    if progress and total:
                        pos = (float(frame.pts * frame.time_base)
                               if frame.pts is not None else last_report)
                        if pos - last_report > 2.0:
                            last_report = pos
                            progress(f"正在解码音频… {_si(pos)} / {_si(total)}",
                                     min(0.999, pos / total))
                # flush 重采样器：否则缓冲区里残留的音频尾部（最后不足一帧的
                # 部分）被丢掉——字幕最后一条的结束时间会超出音频长度零点几秒
                for rf in resampler.resample(None):
                    wf.writeframes(rf.to_ndarray().tobytes())
        except BaseException:
            # 取消/解码错误路径清掉写了一半的 wav：半截文件留在缓存目录
            # 既占空间，又可能被下次转写的大小校验误判成可用成品
            try:
                if os.path.isfile(out_wav):
                    os.remove(out_wav)
            except OSError:
                pass
            raise
    finally:
        c.close()
    return out_wav


def default_wav_path(video_path: str) -> str:
    from .config import data_dir
    stem = os.path.splitext(os.path.basename(video_path))[0]
    stem = "".join(ch for ch in stem if ch not in '<>:"/\\|?*')[:80]
    d = os.path.join(data_dir(), "audio")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "SubtitleStudio", "audio")
        os.makedirs(d, exist_ok=True)
    # 不能用内置 hash()：Python 对字符串哈希做了随机化，每次启动结果都不同，
    # 抽好的音频缓存就永远命中不了。md5 稳定且足够短。
    import hashlib
    tag = hashlib.md5(os.path.abspath(video_path).encode("utf-8", "replace")).hexdigest()[:8]
    dest = os.path.join(d, f"{stem}_{tag}.wav")
    # 顺手清掉旧版 hash 命名留下的同名片段（曾经每次启动都换名，攒了一堆孤儿）
    try:
        now = time.time()
        for fn in os.listdir(d):
            if (fn.startswith(stem[:40] + "_") and fn.endswith(".wav")
                    and os.path.join(d, fn) != dest and not fn.endswith(f"_{tag}.wav")
                    and now - os.path.getmtime(os.path.join(d, fn)) > 86400):
                os.remove(os.path.join(d, fn))
    except OSError:
        pass
    return dest


WAVE_SR = 8000          # 波形分析采样率：8kHz 足够画能量包络
WAVE_POINTS_PER_SEC = 50   # 每秒峰值点数：1 小时视频 ≈ 18 万点（float 列表 ~1.4MB）


def extract_waveform(path: str, points_per_sec: int = WAVE_POINTS_PER_SEC,
                     cancel: Optional[Callable[[], bool]] = None
                     ) -> tuple:
    """从媒体文件提取归一化波形峰值（给时间轴画能量条）。

    返回 (peaks, duration)：peaks 是 0..1 的 float 列表，均匀覆盖全片；
    失败/取消返回 ([], 0.0)。纯 Python 解码（PyAV resample 到 8kHz 单声道），
    一小时视频约 20~40s，工作线程调用不卡 UI。
    """
    import av as _av
    try:
        info = probe(path)
        total = info.duration or 0.0
        if total <= 0:
            return [], 0.0
        c = _av.open(path)
        try:
            stream = next(iter(c.streams.audio), None)
            if stream is None:
                return [], 0.0
            resampler = _av.AudioResampler(format="s16", layout="mono",
                                           rate=WAVE_SR)
            n_points = max(1, int(total * points_per_sec))
            # 每点桶内最大绝对值 = 该时刻的能量峰值
            bucket = [0.0] * n_points
            scale = 1.0 / 32768.0
            for frame in c.decode(stream):
                if cancel and cancel():
                    return [], 0.0
                for rf in resampler.resample(frame):
                    arr = rf.to_ndarray()
                    if arr.ndim > 1:
                        arr = arr[0]
                    # 分帧写桶：pts 换算出该帧起止位置
                    start = 0.0
                    if frame.pts is not None and frame.time_base:
                        start = float(frame.pts * frame.time_base)
                    fi = int(start * WAVE_SR)
                    step = n_points / (WAVE_SR * (total or 1.0))
                    for i, v in enumerate(arr):
                        a = abs(int(v)) * scale
                        pi = int((fi + i) * step)
                        if 0 <= pi < n_points and a > bucket[pi]:
                            bucket[pi] = a
            # 轻度归一化：95 分位封顶（防个别爆点把全片压扁）
            if not bucket:
                return [], 0.0
            srt = sorted(bucket)
            ref = srt[int(len(srt) * 0.95)] or 1.0
            cap = min(1.0, max(ref, 0.05))
            peaks = [min(1.0, v / cap) for v in bucket]
            return peaks, total
        finally:
            c.close()
    except Exception:
        return [], 0.0
