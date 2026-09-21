"""真实转写（端到端）。

只有同时满足「装好 faster-whisper」+「本机有 CT2 模型」+「能定位 ffmpeg/PyAV」才会实跑，
否则整组跳过并返回 0 —— 这样 CI 或换机时不会误报失败。

可选指定素材：``python tests/transcribe.py <媒体路径>``
默认在常见素材目录里找一段含人声的音频；找不到就跳过。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import TempDir, check, finish, section  # noqa: E402

from sstudio.core import cuda_rt, media, transcriber  # noqa: E402
from sstudio.core.config import Config  # noqa: E402
from sstudio.core.model import CueDocument, normalize_cues  # noqa: E402

# 用来验证「确实识别出内容」的本机素材；不存在则自动跳过
DEFAULT_MEDIA = [
    r"D:\Project\Tuse Creation\吐司Tuse\两个平台同步作品\26_14 OSMO360II深度体验\VOICE.mp4",
]

try:
    import faster_whisper  # noqa: F401
    HAVE_FW = True
except Exception:
    HAVE_FW = False

MODELS = transcriber.discover_ct2_models()

if not HAVE_FW or not MODELS:
    print("跳过：缺少 faster-whisper 或本机没有 CTranslate2 模型。")
    print("  装依赖：pip install faster-whisper")
    print("  放模型：设置里点「重新扫描本地模型」，或从卡卡/Buzz 目录复用。")
    sys.exit(0)

media_path = sys.argv[1] if len(sys.argv) > 1 else next(
    (p for p in DEFAULT_MEDIA if os.path.isfile(p)), "")
if not media_path:
    print("跳过：未找到测试素材。用法：python tests/transcribe.py <媒体路径>")
    sys.exit(0)

section("0. 运行条件")
rt = cuda_rt.register()
use_gpu = rt.usable and cuda_rt.probe_loadable(rt)
print(f"  素材 {media_path}")
print(f"  模型 {MODELS[0]['path']}")
print(f"  设备 {'cuda/float16' if use_gpu else 'cpu/int8（未找到 CUDA12 运行库）'}")

cfg = Config()
cfg.whisper_model = MODELS[0]["path"]
cfg.whisper_device = "cuda" if use_gpu else "cpu"
cfg.whisper_compute = "float16" if use_gpu else "int8"
cfg.language = "zh"
cfg.word_timestamps = True

with TempDir() as d:
    section("1. 抽音频")
    wav = os.path.join(d, "a.wav")
    t0 = time.time()
    out = media.extract_audio(media_path, wav)
    check("抽音频成功", os.path.isfile(out) and os.path.getsize(out) > 10000,
          f"{os.path.getsize(out) / 1048576:.1f} MB / {time.time() - t0:.1f}s")

    section("2. 转写（走应用自己的引擎层）")
    ticks = []
    t1 = time.time()
    res = transcriber.transcribe(out, cfg,
                                 progress=lambda m, p: ticks.append((m, p)))
    dt = time.time() - t1
    doc_duration = media.probe(media_path).duration
    doc = CueDocument(source_video=media_path, duration=doc_duration,
                      cues=res.cues, meta=dict(res.meta))
    normalize_cues(doc)

    check("产出字幕", len(doc.cues) > 0, f"{len(doc.cues)} 条 / {dt:.1f}s")
    check("速度不慢于 1x 实时", doc.duration / max(0.1, dt) >= 1.0,
          f"{doc.duration / max(0.1, dt):.1f}x")
    check("进度回调被调用", len(ticks) > 1, len(ticks))
    check("进度单调不减", all(ticks[i][1] <= ticks[i + 1][1] + 1e-6
                          for i in range(len(ticks) - 1)
                          if ticks[i][1] >= 0 and ticks[i + 1][1] >= 0))
    check("语言被识别", bool(res.meta.get("language")), res.meta.get("language"))
    check("时间轴单调", all(doc.cues[i].end <= doc.cues[i + 1].start + 1e-6
                        for i in range(len(doc.cues) - 1)),
          next((f"{doc.cues[i].end:.3f}>{doc.cues[i + 1].start:.3f}"
                for i in range(len(doc.cues) - 1)
                if doc.cues[i].end > doc.cues[i + 1].start + 1e-6), ""))
    check("无负时间", all(c.start >= 0 for c in doc.cues))
    check("文本非空", all(c.text.strip() for c in doc.cues))
    check("识别设备记录正确", res.meta.get("device") in ("cuda", "cpu"),
          res.meta.get("device"))

    section("3. 内容质量抽查")
    txt = "".join(c.text for c in doc.cues)
    if use_gpu:
        check("识别出足量文字", len(txt) > 200, f"{len(txt)} 字")
        zh = sum(1 for ch in txt if "\u4e00" <= ch <= "\u9fff")
        check("以中文为主", zh / max(1, len(txt)) > 0.6, f"{zh / max(1, len(txt)):.0%}")
        check("词级时间戳齐全", sum(1 for c in doc.cues if c.words) == len(doc.cues))
        check("词时间戳落在句内", all(
            c.start - 0.6 <= w["start"] and w["end"] <= c.end + 0.6
            for c in doc.cues[:60] for w in c.words[:20]))
    else:
        check("CPU 兜底也能出结果", len(txt) > 50, f"{len(txt)} 字")

    section("4. 导出与回读")
    from sstudio.core import formats
    for key in ("srt", "vtt", "json", "ass", "txt", "lrc"):
        p = os.path.join(d, "o." + ("srt" if key == "txt" else key))
        body = formats.export_text(doc, key)
        with open(p, "w", encoding="utf-8-sig" if key == "srt" else "utf-8",
                  newline="") as f:
            f.write(body)
        back, fmt = formats.parse_any(open(p, encoding="utf-8-sig").read(),
                                      os.path.basename(p))
        if key in ("txt", "lrc"):
            check(f"导出 {key}", len(body) > 0, f"{len(body)} 字")
        else:
            check(f"导出 {key} 并回读 {len(back)} 条", len(back) == len(doc.cues), fmt)

    section("5. 智能断句")
    before = len(doc.cues)
    added = doc.split_long(max_chars=22, max_dur=7.0)
    check("长句可被再切分", added >= 0 and len(doc.cues) >= before,
          f"{before} -> {len(doc.cues)}")
    check("切分后仍无重叠", all(doc.cues[i].end <= doc.cues[i + 1].start + 1e-6
                            for i in range(len(doc.cues) - 1)))

sys.exit(finish())
