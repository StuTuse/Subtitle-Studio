"""无界面流水线：转写 -> （可选）LLM 纠错 -> 导出。

便于批处理与自动化：

    python -m sstudio --headless --video a.mp4 --out a.srt
    python -m sstudio --headless --video a.mp4 --out a.srt --no-fix
"""

from __future__ import annotations

import os
import sys
import time

from .core import formats, media, transcriber
from .core.config import Config
from .core.model import CueDocument, normalize_cues


def _p(msg: str) -> None:
    print(msg, flush=True)


class _Progress:
    """进度回调：终端里原地刷新同一行，重定向到文件时每 5% 打一行。"""

    def __init__(self, label: str = "") -> None:
        self.label = label
        self.last = -100.0
        self.tty = bool(getattr(sys.stdout, "isatty", lambda: False)())

    def __call__(self, msg: str, p: float) -> None:
        if p < 0:                          # 无百分比的阶段说明，单独一行
            _p(f"  {msg}")
            self.last = -100.0
            return
        if self.tty:
            bar = _bar(p)
            sys.stdout.write(f"\r  {self.label}{bar} {p * 100:5.1f}%  {msg[:40]:<40}")
            sys.stdout.flush()
            if p >= 0.999:
                sys.stdout.write("\n")
                sys.stdout.flush()
            return
        if (p * 100) - self.last >= 5 or p >= 0.999:
            self.last = p * 100
            _p(f"  [{p * 100:5.1f}%] {msg}")


def _bar(p: float, width: int = 24) -> str:
    n = int(round(p * width))
    return "█" * n + "░" * (max(0, width - n))


def run_pipeline(args) -> int:
    if not args.video or not os.path.isfile(args.video):
        _p("请用 --video 指定一个存在的视频/音频文件。")
        return 2
    cfg = Config.load()
    video = os.path.abspath(args.video)

    t0 = time.time()
    info = media.probe(video)
    _p(f"媒体：{os.path.basename(video)}  时长 {info.duration:.1f}s")
    wav = media.extract_audio(video, progress=lambda m, p: _p("  " + m))
    _p(f"音频：{wav}")

    res = transcriber.transcribe(wav, cfg, progress=_Progress("转写 "))
    doc = CueDocument(source_video=video, duration=info.duration,
                      language=res.meta.get("language", ""), cues=res.cues,
                      meta=dict(res.meta))
    normalize_cues(doc)
    _p(f"转写完成：{len(doc.cues)} 条，用时 {res.meta.get('elapsed')}s")

    if not args.no_fix:
        from .core import llm
        _p(f"LLM 纠错：{cfg.profile().model}（{cfg.batch_size} 行/批，并发 {cfg.concurrency}）")
        try:
            r = llm.fix_document(cfg, doc.cues,
                                 progress=lambda m, p: _p(f"  [{p * 100:5.1f}%] {m}"))
            _p(f"纠错完成：修改 {r.changed} 条，告警 {len(r.failures)} 条")
            for f in r.failures[:8]:
                _p("   ⚠ " + f)
        except Exception as e:
            _p(f"LLM 纠错失败，保留原始识别文本：{e}")
    else:
        _p("已跳过 LLM 纠错。")

    out = args.out or (os.path.splitext(video)[0] + ".srt")
    key = os.path.splitext(out)[1].lstrip(".").lower()
    fmt_map = {"srt": "srt", "vtt": "vtt", "ass": "ass", "txt": "txt",
               "json": "json", "md": "md", "html": "html", "lrc": "lrc"}
    if key not in fmt_map:
        # 命令行工具不猜意图：扩展名不认识就明确报错，而不是悄悄改名成 .srt
        _p(f"不支持的输出扩展名 .{key}（可选：{'/'.join(fmt_map)}）")
        return 2
    key = fmt_map[key]
    if os.path.isdir(out):
        _p(f"--out 是一个目录：{out}，请给完整文件名")
        return 2
    text = formats.export_text(doc, key)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    # 与 GUI 导出一致：SRT 用用户设置的编码（此前写死 utf-8-sig，
    # 同一工程两种出口产物不一致）
    enc = getattr(cfg, "export_encoding", "utf-8-sig") if key == "srt" else "utf-8"
    with open(out, "w", encoding=enc, errors="replace", newline="") as f:
        f.write(text)
    # 顺带存一份工程文件，便于之后回到 GUI 精修
    proj = os.path.splitext(out)[0] + ".ssp"
    with open(proj, "w", encoding="utf-8") as f:
        f.write(doc.to_json())
    try:
        os.remove(wav)
    except OSError:
        pass
    _p(f"\n输出：{out}\n工程：{proj}\n总耗时 {time.time() - t0:.1f}s")
    return 0
