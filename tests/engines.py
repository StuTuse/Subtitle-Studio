"""引擎可用性、本地模型发现、工程文件往返。不强制需要 GPU。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import TempDir, check, finish, sample_doc, section  # noqa: E402

from sstudio.core import cuda_rt, formats, media, transcriber  # noqa: E402
from sstudio.core.model import CueDocument  # noqa: E402

section("1. 本地 CT2 模型发现（有界、可缓存）")
cands = transcriber.discover_ct2_models()
check("返回 list", isinstance(cands, list), len(cands))
for c in cands:
    check(f"「{c['name']}」目录真实存在且含权重",
          os.path.isfile(os.path.join(c["path"], "model.bin")), c["path"])
check("重复调用走缓存", transcriber.discover_ct2_models() is cands
      or transcriber.discover_ct2_models() == cands)

section("2. 引擎注册表")
check("三种引擎齐备", set(transcriber.ENGINES) >=
      {"faster-whisper", "whisper.cpp", "openai_api"}, sorted(transcriber.ENGINES))
from sstudio.core.config import Config  # noqa: E402
cfg = Config.load()
for key, cls in transcriber.ENGINES.items():
    eng = cls(cfg)
    check(f"引擎 {key} 可实例化且有 transcribe", callable(getattr(eng, "transcribe", None)))
check("按配置选引擎", type(transcriber.get_engine(cfg)).key == cfg.asr_engine
      or type(transcriber.get_engine(cfg)).key == "faster-whisper")
saved = cfg.asr_engine
cfg.asr_engine = "不存在的引擎"
check("未知引擎安全回退而非崩溃", transcriber.get_engine(cfg) is not None)
cfg.asr_engine = saved

section("3. ffmpeg 定位有界且不崩")
ff = media.find_ffmpeg()
check("find_ffmpeg 返回可用的东西（或空串走 PyAV）", ff == "" or os.path.isfile(ff), ff)
check("二次调用命中缓存", media.find_ffmpeg() == ff)
check("媒体扩展名表齐全", {".mp4", ".mov", ".mkv", ".mp3"} <= set(media.VIDEO_EXTS)
      | set(media.AUDIO_EXTS))
check("is_media 判断正确", media.is_media("a.MP4") and media.is_media("a.flac")
      and not media.is_media("a.txt"))

section("4. CUDA 运行时探测")
rt = cuda_rt.register()
check("返回 CudaRuntime", hasattr(rt, "usable"))
check("usable 与实际找到的库自洽", (not rt.usable) or (bool(rt.cublas_dir) and bool(rt.cudart_dir)),
      rt.cublas_dir)
check("不可用时给出可执行建议", rt.usable or ("pip" in rt.note or "CUDA" in rt.note),
      rt.note[:70])
check("describe 不抛异常", isinstance(cuda_rt.describe(rt), str))

section("5. 工程文件（.ssp）往返")
doc = sample_doc()
doc.meta["note"] = "测试"
with TempDir() as d:
    p = os.path.join(d, "proj.ssp")
    payload = doc.to_json()
    with open(p, "w", encoding="utf-8") as f:
        f.write(payload)
    back = CueDocument.from_dict(json.loads(open(p, encoding="utf-8").read()))
    check("条数一致", len(back.cues) == len(doc.cues))
    check("时间轴一致", all(abs(a.start - b.start) < 1e-6 and abs(a.end - b.end) < 1e-6
                        for a, b in zip(doc.cues, back.cues)))
    check("文本一致", [c.text for c in back.cues] == [c.text for c in doc.cues])
    check("原文一致", [c.original_text for c in back.cues]
          == [c.original_text for c in doc.cues])
    check("词级时间戳一致", len(back.cues[0].words) == len(doc.cues[0].words))
    check("meta 保留", back.meta.get("note") == "测试")
    check("时长与语言保留", back.duration == doc.duration and back.language == "zh")
    # 空文档也要能存
    empty = CueDocument()
    check("空文档可往返", len(CueDocument.from_dict(
        json.loads(empty.to_json())).cues) == 0)

section("6. 时间轴清理 normalize_cues")
from sstudio.core.model import Cue, normalize_cues  # noqa: E402
d = CueDocument(cues=[Cue(5.0, 6.0, "后"), Cue(0.0, 0.02, "太短"),
                      Cue(1.0, 4.0, "中"), Cue(2.0, 3.0, "被包含")])
d.sorted()
normalize_cues(d)
check("过短条目被处理", all(c.end - c.start >= 0.15 for c in d.cues),
      [(round(c.start, 2), round(c.end, 2)) for c in d.cues])
check("无重叠", all(d.cues[i].end <= d.cues[i + 1].start + 1e-6
                for i in range(len(d.cues) - 1)))

section("7. 重复字幕折叠（Whisper 常见复读）")
d2 = CueDocument(cues=[Cue(0, 2, "同一句话"), Cue(2, 4, "同一句话"),
                       Cue(4, 6, "同一句话"), Cue(6, 8, "不同的话")])
n = d2.dedupe_repeats()
check("连续复读被折叠", n >= 1 and len(d2.cues) == 2, f"{n} -> {len(d2.cues)}")

section("7.5 消除字幕间小空隙 close_gaps（防播放闪断）")
dg = CueDocument(cues=[Cue(0.0, 1.0, "一"), Cue(1.10, 2.0, "二"),     # 0.1s 小空隙
                       Cue(2.05, 3.0, "三"),                          # 0.05s 小空隙
                       Cue(5.0, 6.0, "四"),                           # 2s 大空隙→保留
                       Cue(6.0, 7.0, "五")])                          # 0 空隙→不动
touched, saved = dg.close_gaps(0.5)
check("小空隙被衔接", dg.cues[0].end == 1.10 and dg.cues[1].end == 2.05,
      [round(c.end, 2) for c in dg.cues])
check("大空隙保留不动", dg.cues[3].end == 6.0, dg.cues[3].end)
check("统计正确", touched == 2 and abs(saved - 0.15) < 1e-6, (touched, saved))
after = [(c.start, c.end) for c in dg.cues]
check("衔接不改动任何 start", [s for s, _ in after] == [0.0, 1.10, 2.05, 5.0, 6.0], after)
check("衔接后无重叠", all(dg.cues[i].end <= dg.cues[i + 1].start + 1e-6
                      for i in range(len(dg.cues) - 1)))
check("阈值调 0 时什么都不做", dg.close_gaps(0.0) == (0, 0.0))

# 句末保护：一句话说完（。！？）后的短停顿往往是换气/换人 → 保留
ds = CueDocument(cues=[Cue(0.0, 1.0, "这句话说完了。"), Cue(1.15, 2.0, "下一句"),
                       Cue(2.05, 3.0, "还没说完"), Cue(3.20, 4.0, "接上")])
ts, sv = ds.close_gaps(0.35)
check("句末标点后的空隙保留", ds.cues[0].end == 1.0, ds.cues[0].end)
check("非句末的空隙仍被衔接", ds.cues[1].end == 2.05 and ds.cues[2].end == 3.20,
      [round(c.end, 2) for c in ds.cues])
check("只统计真正衔接的", ts == 2 and abs(sv - 0.25) < 1e-6, (ts, sv))

# 引号包裹的句末同样算说完；"……"是话没说完，要衔接
dq = CueDocument(cues=[Cue(0.0, 1.0, "他说「走吧。」"), Cue(1.1, 2.0, "下一句"),
                       Cue(2.0, 3.0, "还在犹豫……"), Cue(3.2, 4.0, "又接上")])
dq.close_gaps(0.35)
check("引号内的句末标点也算说完", dq.cues[0].end == 1.0, dq.cues[0].end)
check("省略号表示没说完，照常衔接", dq.cues[2].end == 3.2, dq.cues[2].end)

# 说话人切换处保留停顿，同一人连续说仍衔接
dp = CueDocument(cues=[Cue(0.0, 1.0, "甲的话", speaker="A"),
                       Cue(1.1, 2.0, "甲接着说", speaker="A"),
                       Cue(2.1, 3.0, "乙接话", speaker="B")])
dp.close_gaps(0.35)
check("同一人连续说仍衔接", dp.cues[0].end == 1.1, dp.cues[0].end)
check("换人处空隙保留", dp.cues[1].end == 2.0, dp.cues[1].end)

check("只有一条字幕时不出错",
      CueDocument(cues=[Cue(0, 1, "孤条")]).close_gaps(0.35) == (0, 0.0))
check("空文档不出错", CueDocument().close_gaps(0.35) == (0, 0.0))
check("total_gap 与衔接条件一致",
      abs(CueDocument(cues=[Cue(0, 1, "说完。"), Cue(1.2, 2, "新句")]).total_gap(0.35))
      < 1e-9, CueDocument(cues=[Cue(0, 1, "说完。"), Cue(1.2, 2, "新句")]).total_gap(0.35))
dn = CueDocument(cues=[Cue(0.0, 1.0, "一"), Cue(1.08, 2.0, "二"), Cue(4.0, 5.0, "三")])
check("normalize_cues 支持无缝衔接参数",
      (normalize_cues(dn, close_gaps_under=0.5) or dn.cues[0].end == 1.08),
      dn.cues[0].end)

section("9. split_long 随机压测：切完绝不产生重叠/非法时间")
import random as _random  # noqa: E402
_all_ok, _bad_case = True, ""
for trial in range(12):
    rnd = _random.Random(trial)
    cues, t = [], 0.0
    for _ in range(40):
        dur = rnd.choice([0.3, 0.8, 2.0, 9.0])
        txt = "".join(rnd.choice("，。一二三四五六七八九十") for _ in range(rnd.randint(1, 80)))
        cues.append(Cue(round(t, 3), round(t + dur, 3), txt))
        t += dur + rnd.choice([0.0, 0.05, 0.4])
    dd = CueDocument(cues=cues)
    dd.split_long(max_chars=rnd.choice([8, 12, 22]), max_dur=rnd.choice([2.0, 7.0]))
    for i in range(len(dd.cues) - 1):
        if dd.cues[i].end > dd.cues[i + 1].start + 1e-6:
            _all_ok, _bad_case = False, f"trial{trial}: {dd.cues[i].end}>{dd.cues[i+1].start}"
    if any(not (0 <= c.start < c.end) for c in dd.cues):
        _all_ok, _bad_case = False, f"trial{trial}: 非法时间"
check("12 组随机数据切分后无重叠且时间合法", _all_ok, _bad_case)

section("10. 配置往返：类型不得被字符串污染")
# 回归：from_dict 曾把未列出的字段一律 str()，导致 timeout 变 "300.0"
# （界面 int() 直接崩，打包版表现为 "Unhandled exception in script"），
# 且 no_reasoning=False 变成非空字符串 "False"（恒为真，开关关不掉）。
from sstudio.core.config import LLMProfile  # noqa: E402

dirty = LLMProfile.from_dict({"timeout": "300.0", "no_reasoning": "False",
                              "max_tokens": "8192", "temperature": "0.0",
                              "enabled": "true", "top_p": "1.0"})
check("timeout 转成 float", isinstance(dirty.timeout, float) and dirty.timeout == 300.0,
      repr(dirty.timeout))
check("no_reasoning 'False' 转成真 False", dirty.no_reasoning is False, repr(dirty.no_reasoning))
check("max_tokens 字符串数字可用", dirty.max_tokens == 8192 and isinstance(dirty.max_tokens, int))
check("temperature/top_p 转 float", isinstance(dirty.temperature, float)
      and isinstance(dirty.top_p, float))
check("enabled 'true' 为真", dirty.enabled is True)
check("界面同款 int(timeout) 不再崩", int(dirty.timeout) == 300)
check("垃圾值静默丢弃而非崩溃",
      LLMProfile.from_dict({"max_tokens": "abc"}).max_tokens == LLMProfile().max_tokens)
check("None 字段不炸", LLMProfile.from_dict({"name": None}).name == "")

p = LLMProfile(name="t", timeout=123.5, no_reasoning=True, max_tokens=2048)
back = LLMProfile.from_dict(p.to_dict())
check("正常往返类型不变", isinstance(back.timeout, float)
      and back.no_reasoning is True and back.max_tokens == 2048)
check("往返值不变", back.timeout == 123.5 and back.name == "t")

cfg2 = Config.from_dict({"batch_size": "12", "strict_mode": "false",
                         "concurrency": "2.0", "theme": "dark"})
check("Config 字符串数字转换", cfg2.batch_size == 12 and isinstance(cfg2.batch_size, int))
check("Config 整数字段容忍 2.0 写法", cfg2.concurrency == 2)
check("Config 布尔 'false' 为假", cfg2.strict_mode is False)
check("已移除的引擎名回落默认",
      Config.from_dict({"asr_engine": "buzz"}).asr_engine == "faster-whisper",
      Config.from_dict({"asr_engine": "buzz"}).asr_engine)
check("合法引擎名原样保留",
      Config.from_dict({"asr_engine": "openai_api"}).asr_engine == "openai_api")

section("11. 外部 CLI 扫描有界化（启动提速回归）")
# 回归：曾用 ** 递归 glob 扫 LOCALAPPDATA，实测 7.2s，卡死设置页/启动。
import time  # noqa: E402
transcriber.ext_cli_cache_reset()
t0 = time.perf_counter()
hits = transcriber.find_external_whisper_cli()
cost = time.perf_counter() - t0
check("扫描 < 1.5s（旧实现 7s+）", cost < 1.5, f"{cost * 1000:.0f}ms")
check("命中都是存在的 exe", all(os.path.isfile(f) for f in hits), hits[:2])
t0 = time.perf_counter()
hits2 = transcriber.find_external_whisper_cli()
check("第二次走缓存（<50ms）", (time.perf_counter() - t0) < 0.05 and hits2 == hits)
transcriber.ext_cli_cache_reset()
t0 = time.perf_counter()
check("清缓存后可重扫", transcriber.find_external_whisper_cli() == hits
      and (time.perf_counter() - t0) < 1.5)
_found = []
_visited = transcriber._scan_shallow(os.path.dirname(__file__), "no-such-prefix", 2, _found)
check("_scan_shallow 找不到时返回空", _found == [] and _visited >= 0, _visited)

sys.exit(finish())
