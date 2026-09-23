"""无界面流水线：转写 -> （可选）LLM 纠错 -> 导出。

便于批处理与自动化：

    python -m sstudio --headless --video a.mp4 --out a.srt
    python -m sstudio --headless --video a.mp4 --out a.srt --no-fix

退出码约定：0=成功；1=转写/导出等运行失败；2=参数错误；
3=转写与导出成功、但 LLM 纠错整轮失败或无效果（产出可用、建议人工复核）。
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
    # --out 校验放在最前面：扩展名不认识/目标路径不对当场报错，
    # 不能等转写+纠错跑了几个小时才告诉用户参数写错了
    out = args.out or ""
    if out:
        key = os.path.splitext(out)[1].lstrip(".").lower()
        fmt_map = {"srt": "srt", "vtt": "vtt", "ass": "ass", "txt": "txt",
                   "json": "json", "md": "md", "html": "html", "lrc": "lrc"}
        if key not in fmt_map:
            _p(f"不支持的输出扩展名 .{key}（可选：{'/'.join(fmt_map)}）")
            return 2
        if os.path.isdir(out):
            _p(f"--out 是一个目录：{out}，请给完整文件名")
            return 2
    else:
        fmt_map = {}
        key = "srt"
    cfg = Config.load()
    video = os.path.abspath(args.video)

    try:
        return _pipeline(args, cfg, video, out or (os.path.splitext(video)[0] + ".srt"), key)
    except KeyboardInterrupt:
        _p("\n已中断。")
        return 130
    except Exception as e:
        # 裸 traceback 抛到顶层 exit 1 和上面的 return 2 语义分叉，
        # 批处理脚本无法区分"参数错"和"运行失败"——这里统一为 1 并给可读消息
        _p(f"[错误] {e}")
        return 1


def _pipeline(args, cfg: Config, video: str, out: str, key: str) -> int:
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
    # 与 GUI 转写路径对齐：GUI 默认执行 close_gaps（auto_close_gaps），
    # headless 漏了这一步会导致同一配置下两种出口时间轴不一致
    if getattr(cfg, "auto_close_gaps", True) and doc.cues:
        mg = float(getattr(cfg, "gap_max", 0.35) or 0.35)
        touched, saved = doc.close_gaps(mg)
        doc.meta["gaps_closed"] = touched
        doc.meta["gaps_saved"] = round(saved, 1)
        if touched:
            _p(f"已衔接 {touched} 处字幕空隙（共 {saved:.1f}s）")
    _p(f"转写完成：{len(doc.cues)} 条，用时 {res.meta.get('elapsed')}s")
    if not doc.cues:
        # 与 GUI 转写路径对齐（GUI 第 18 轮加了同样告警）：纯静音/音乐/
        # 语言设置不对时一条都识别不出，批处理拿到空 srt 还报成功会
        # 让自动化链路把空文件当有效产物继续用。
        _p("⚠ 没有识别出任何字幕——可能是纯静音/音乐片段，或语言设置不匹配。"
           "可检查 --no-fix 之外的语言配置后重试。")

    fix_ok = True
    if not args.no_fix:
        from .core import llm
        _p(f"LLM 纠错：{cfg.profile().model}（{cfg.batch_size} 行/批，并发 {cfg.concurrency}）")
        try:
            r = llm.fix_document(cfg, doc.cues,
                                 progress=lambda m, p: _p(f"  [{p * 100:5.1f}%] {m}"))
            _p(f"纠错完成：修改 {r.changed} 条，告警 {len(r.failures)} 条")
            for f in r.failures[:8]:
                _p("   ⚠ " + f)
            if getattr(r, "changed", 0) == 0 and r.failures:
                fix_ok = False        # 全部批次被拦下/失败：不算完全成功
        except Exception as e:
            _p(f"LLM 纠错失败，保留原始识别文本：{e}")
            fix_ok = False
    else:
        _p("已跳过 LLM 纠错。")

    text = formats.export_text(doc, key)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    # 与 GUI 导出一致：SRT 用用户设置的编码（此前写死 utf-8-sig，
    # 同一工程两种出口产物不一致）
    enc = getattr(cfg, "export_encoding", "utf-8-sig") if key == "srt" else "utf-8"
    tmp = out + ".tmp"
    with open(tmp, "w", encoding=enc, errors="replace", newline="") as f:
        f.write(text)
    os.replace(tmp, out)          # 原子替换，中断不会留下截断的成品
    # 顺带存一份工程文件，便于之后回到 GUI 精修
    proj = os.path.splitext(out)[0] + ".ssp"
    with open(proj, "w", encoding="utf-8") as f:
        f.write(doc.to_json())
    try:
        os.remove(wav)
    except OSError:
        pass
    _p(f"\n输出：{out}\n工程：{proj}\n总耗时 {time.time() - t0:.1f}s")
    # 约定：0=成功；1=转写/导出失败（由 run_pipeline 的 except 统一）；
    # 3=转写导出成功但 LLM 纠错整轮失败/无效果（产出可用、需人工复核）
    return 3 if not fix_ok else 0
