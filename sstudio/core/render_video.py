# -*- coding: utf-8 -*-
"""视频合成：把当前字幕烧录进原视频，输出画质与原片一致的新视频。

链路（ffmpeg 单进程一次拷贝音频）：
    ffmpeg -i in.mp4 -vf "ass=burn.ass" -c:v <enc> -c:a copy out.mp4

画质策略（用户要求"成片和原本的画质相同，只是增加了字幕"）：
* 无损优先：libx264 -qp 0（数学无损，但体积可能巨大，供「归档」档）
* 视觉无损默认：libx264 -crf 12 —— 肉眼与原片不可区分的近无损档；
  x264 crf 12 的 PSNR/SSIM 普遍高于流媒体母版，作为默认是安全余量
* 硬件加速可选（nvenc/amf/qsv）：默认关——硬编在低码率下的画质
  不如 libx264 crf12，"画质相同"优先于渲染速度；用户要快可以开
* 分辨率/帧率/像素格式全部跟随原片（ffprobe 读出后原样回填），
  不做任何缩放/插帧/色彩空间转换
* 音频流 -c:a copy 原样复制，不经重编码

libass 烧录要点：ass 滤镜按脚本内 PlayRes 缩放，PlayRes=视频真实
分辨率 → 字幕 1:1 落在像素网格上，不吃缩放模糊。fontsdir 留空走
系统字体（Windows 自带中文字体全覆盖）。

进度/取消：-progress pipe 输出 out_time_us，按片长换算真实百分比；
cancel 查询回调置位后 terminate() 掉 ffmpeg，删除半成品。
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, Optional

from .media import find_ffmpeg, probe

# 视觉无损默认档；无损归档档；硬件档映射
QUALITY_PRESETS = {
    "lossless": {"vcodec": "libx264", "params": ["-qp", "0", "-preset", "veryslow"]},
    "near_lossless": {"vcodec": "libx264", "params": ["-crf", "12", "-preset", "slow"]},
    "balanced": {"vcodec": "libx264", "params": ["-crf", "18", "-preset", "medium"]},
    "h264_nvenc": {"vcodec": "h264_nvenc", "params": ["-rc", "constqp", "-qp", "14",
                                                      "-preset", "p6"]},
    "h264_amf": {"vcodec": "h264_amf", "params": ["-quality", "quality"]},
    "h264_qsv": {"vcodec": "h264_qsv", "params": ["-global_quality", "14"]},
}

_FMT_EXT = {"mp4": ".mp4", "mkv": ".mkv", "mov": ".mov"}
_PROG_RE = re.compile(r"out_time_us=(\d+)")


def _pick_encoder(vcodec: str) -> str:
    """选编码器：请求的不可用时回落 libx264（软编永远存在）。"""
    if vcodec == "libx264":
        return vcodec
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if vcodec in (r.stdout or ""):
        return vcodec
    return "libx264"


def default_out_path(video_path: str, fmt: str = "mp4") -> str:
    base = os.path.splitext(video_path)[0]
    return f"{base}.subtitled{_FMT_EXT.get(fmt, '.mp4')}"


def probe_video(path: str) -> dict:
    """合成页展示用的视频概况（复用 media.probe 的 MediaInfo）。"""
    info = probe(path)
    return {
        "width": info.width, "height": info.height,
        "duration": info.duration, "audio": bool(info.audio_codec),
        "audio_codec": info.audio_codec,
        "size": (os.path.getsize(path) if os.path.isfile(path) else 0),
    }


def burn_video(video_path: str, ass_path: str, out_path: str,
               quality: str = "near_lossless",
               progress: Optional[Callable[[float], None]] = None,
               cancel: Optional[Callable[[], bool]] = None,
               log: Optional[Callable[[str], None]] = None) -> bool:
    """烧录主链路。成功返回 True；取消/失败返回 False（半成品已删）。

    progress(0..1)；cancel() 返回 True 时尽快中断；log(msg) 收 ffmpeg 报错。
    """
    ff = find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg：请安装 ffmpeg 或确认其在 PATH 中")
    if not os.path.isfile(video_path):
        raise RuntimeError(f"视频不存在：{video_path}")

    info = probe(video_path)
    dur = max(0.1, info.duration)
    q = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["near_lossless"])
    enc = _pick_encoder(q["vcodec"])

    ass_arg = ass_path.replace("\\", "/").replace(":", "\\:")
    cmd = [
        ff, "-y", "-hide_banner", "-nostdin",
        "-i", video_path,
        "-vf", f"ass='{ass_arg}'",
        "-c:v", enc, *q["params"],
        "-c:a", "copy",                       # 音频原样复制，不重编码
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        out_path,
    ]
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # stderr 走临时文件而不是 PIPE：ffmpeg 会往 stderr 写 banner/警告，
    # 只建管道不读它，写满 64KB 缓冲后 ffmpeg 整个进程卡死（实测 2 秒
    # 测试片都能挂住）——读取循环只在 stdout 收 -progress，stderr 异步
    # 落文件，结束时按需读尾部给报错。
    import tempfile
    errf = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=creation)
    cancelled = False
    rc = -1
    try:
        for line in proc.stdout or ():
            m = _PROG_RE.search(line or "")
            if m and progress:
                try:
                    progress(max(0.0, min(1.0, int(m.group(1)) / 1e6 / dur)))
                except Exception:
                    pass
            if cancel and cancel():
                cancelled = True
                proc.terminate()
                break
        rc = proc.wait()
    finally:
        if proc.poll() is None:               # 异常路径兜底回收
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        errf.seek(0)
        err_tail = (errf.read() or "")[-600:]
        errf.close()
    if cancelled:
        if os.path.isfile(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
        return False
    if rc != 0 or not os.path.isfile(out_path):
        if log:
            log(err_tail)
        raise RuntimeError(f"ffmpeg 合成失败（exit={rc}）：{err_tail[:300]}")
    return True
