# -*- coding: utf-8 -*-
"""2025-07 全面审查修复的回归测试：merge 多选、重叠、说话人往返、
配置原子写/迁移、连接止损判定、glossary 污染、client 复用、时间显示。"""

import _harness
from _harness import check, section, finish, TempDir

from sstudio.core.model import Cue, CueDocument
from sstudio.core import formats


section("1. merge 只吞选中的行（Ctrl 隔行多选不再误删）")
d = CueDocument(cues=[Cue(i, i + 1, "t%d" % i) for i in range(6)])
d.merge([1, 3])
check("隔行合并保留未选中行",
      [c.text for c in d.cues] == ["t0", "t1\nt3", "t2", "t4", "t5"],
      [c.text for c in d.cues])
d2 = CueDocument(cues=[Cue(i, i + 1, "t%d" % i) for i in range(6)])
d2.merge([1, 2])
check("连续合并行为不变", [c.text for c in d2.cues]
      == ["t0", "t1\nt2", "t3", "t4", "t5"])
d3 = CueDocument(cues=[Cue(i, i + 1, "t%d" % i) for i in range(6)])
d3.merge([0, 5])
check("跨全表合并：首行位置放合并结果，中间未选中保留",
      [c.text for c in d3.cues] == ["t0\nt5", "t1", "t2", "t3", "t4"],
      [c.text for c in d3.cues])


section("2. 同起点完全重叠也会被压缩")
d = CueDocument(cues=[Cue(0.0, 5.0, "a"), Cue(0.0, 6.0, "b")])
d._fix_overlap()
check("同起点重叠压缩为极短窗口", d.cues[0].end - d.cues[0].start <= 0.011,
      f"{d.cues[0].end}")


section("3. 说话人 SRT/VTT 往返守恒")
doc = _harness.sample_doc()          # 第 2 条 speaker=小明
srt = formats.to_srt(doc)
back = formats.parse_srt(srt)
check("SRT 往返：正文不掺说话人",
      all(c.text == o.text for c, o in zip(back, doc.cues)),
      [c.text for c in back])
check("SRT 往返：说话人保留", back[1].speaker == "小明", back[1].speaker)
check("SRT 往返：无说话人的不被误标", back[0].speaker == "")
vtt = formats.to_vtt(doc)
back2 = formats.parse_vtt(vtt)
check("VTT 往返：说话人保留", back2[1].speaker == "小明", back2[1].speaker)
check("VTT 往返：正文干净", all("<v" not in c.text for c in back2))
# 正文里带冒号的正常行不能被当成说话人行
tricky = "1\n00:00:00,000 --> 00:00:02,000\n注意: 这一行正文里有冒号\n"
c3 = formats.parse_srt(tricky)
check("正文含冒号不被剥成说话人",
      c3[0].text == "注意: 这一行正文里有冒号" and c3[0].speaker == "", c3[0].text)


section("4. 音频缓存文件名跨进程稳定")
import subprocess, sys, os
p1 = subprocess.run([sys.executable, "-c",
                     "import sys; sys.path.insert(0, %r); import os;"
                     "os.environ['SUBTITLE_STUDIO_HOME']=%r;"
                     "from sstudio.core.media import default_wav_path;"
                     "print(default_wav_path(r'C:\\vid\\A B.mp4'))"
                     % (_harness.ROOT, _harness._HOME)],
                    capture_output=True, text=True, encoding="utf-8")
p2 = subprocess.run([sys.executable, "-c",
                     "import sys; sys.path.insert(0, %r); import os;"
                     "os.environ['SUBTITLE_STUDIO_HOME']=%r;"
                     "from sstudio.core.media import default_wav_path;"
                     "print(default_wav_path(r'C:\\vid\\A B.mp4'))"
                     % (_harness.ROOT, _harness._HOME)],
                    capture_output=True, text=True, encoding="utf-8")
a = p1.stdout.strip().splitlines()[-1] if p1.stdout.strip() else ""
b = p2.stdout.strip().splitlines()[-1] if p2.stdout.strip() else ""
check("两次独立进程得到同一 wav 路径", a and a == b, f"{a} vs {b}")


section("5. 配置原子写与旧数据迁移")
from sstudio.core.config import Config, config_path
with TempDir() as td:
    os.environ["SUBTITLE_STUDIO_HOME"] = td
    cfg = Config()
    cfg.glossary = "达芬奇\nDaVinci\n【本轮补充】把人名改对\n【本轮补充】再加一遍"
    cfg.save()
    with open(config_path(), encoding="utf-8") as f:
        raw = f.read()
    check("保存即完整 JSON（无 .tmp 残留）",
          raw.lstrip().startswith("{") and not os.path.exists(config_path() + ".tmp"))
    cfg2 = Config.load()
    check("读回时【本轮补充】尾巴被清掉",
          cfg2.glossary == "达芬奇\nDaVinci", repr(cfg2.glossary))
    cfg2.max_recent = 0
    for i in range(3):
        cfg2.add_recent(os.path.join(td, "f%d" % i))
    check("max_recent=0 不会清空最近文件", len(cfg2.recent_files) == 1)
    cfg2.max_recent = -5
    cfg2.add_recent(os.path.join(td, "x"))
    check("max_recent 负数保留 1 条、不删尾巴", len(cfg2.recent_files) == 1,
          len(cfg2.recent_files))
os.environ["SUBTITLE_STUDIO_HOME"] = _harness._HOME


section("6. 连接止损只认「连不上」，不认偶发超时")
from sstudio.core.llm import _is_conn_refused


class _APIConn(Exception):
    pass


class _Timeout(Exception):
    pass


inner = ConnectionRefusedError(10061, "No connection could be made")
wrapped = _APIConn("Connection error.")
wrapped.__cause__ = inner
check("ConnectionRefused 判为止损", _is_conn_refused(inner))
check("SDK 包一层的 refused 判为止损", _is_conn_refused(wrapped))
check("纯超时不判为止损", not _is_conn_refused(_Timeout("Request timed out")))
to = _Timeout("x")
cause = Exception("timed out")
to.__cause__ = cause
check("超时链不误伤", not _is_conn_refused(to))
check("DNS 失败判为止损", _is_conn_refused(
    OSError("getaddrinfo failed")))


section("7. 「本轮补充」不再永久写进术语表")
import sstudio.core.llm as llm_mod

calls = []


def fake_chat(prof, messages, on_delta=None, _retry_no_cap=True):
    calls.append(messages[1]["content"])
    return "\n".join(f"[{i}] {c.text}" for i, c in enumerate(_cur[0]))


_cfg = Config()
_cfg.profiles[0].api_key = "sk-test"
_cfg.batch_size = 2
_cfg.concurrency = 1
_cfg.auto_retry = 0
_cfg.strict_mode = False
_cur = [[], []]
for i in range(4):
    _cur[0].append(Cue(i, i + 1, "句子%d" % i))
_orig_chat = llm_mod.chat
llm_mod.chat = fake_chat
try:
    res = llm_mod.fix_document(_cfg, _cur[0], extra="本轮：人名用简体")
    joined = "\n".join(calls)
    check("extra 进了本轮提示词", "本轮：人名用简体" in joined)
    check("cfg.glossary 未被污染", _cfg.glossary == "", repr(_cfg.glossary))
finally:
    llm_mod.chat = _orig_chat


section("8. OpenAI client 按接入点复用")
c1 = llm_mod.load_client(_cfg.profiles[0])
c2 = llm_mod.load_client(_cfg.profiles[0])
check("同配置同一实例", c1 is c2)
_p2 = llm_mod.LLMProfile(api_key="sk-other", base_url="https://b.example/v1")
check("换 Key 换实例", llm_mod.load_client(_p2) is not c1)


section("9. 用时显示不再出现 60 秒")
from sstudio.ui.theme import human_time
check("59:60.00 进位成整点", human_time(3599.999) == "1:00:00.00",
      human_time(3599.999))
check("常规值不变", human_time(65.5) == "1:05.50", human_time(65.5))
check("小时格式", human_time(3661.25) == "1:01:01.25", human_time(3661.25))


section("10. JSON 导入对垃圾字段免疫")
bad = ('[{"start": null, "end": 3, "text": "a"},'
       '{"start": true, "end": 3, "text": "b"},'
       '{"start": 0, "end": [2], "text": "c"},'
       '{"start": 0, "end": 3, "text": "ok"}]')
cues = formats.parse_json_obj(__import__("json").loads(bad))
check("null/bool/数组时间戳的行被跳过", [c.text for c in cues] == ["ok"],
      [c.text for c in cues])


section("11. 模型名解析：精确优先，子串误配垫后")
import sstudio.core.transcriber as tr
_eng = tr.FasterWhisperEngine(_cfg)
_orig_disc = tr.discover_ct2_models
tr.discover_ct2_models = lambda: [
    {"name": "turbo", "path": r"C:\m\faster-whisper-large-v3-turbo"},
    {"name": "v3", "path": r"C:\m\faster-whisper-large-v3"},
]
try:
    check("large-v3 不被 turbo 目录截胡",
          _eng.resolve_model("large-v3") == r"C:\m\faster-whisper-large-v3",
          _eng.resolve_model("large-v3"))
    check("large-v3-turbo 精确命中 turbo",
          _eng.resolve_model("large-v3-turbo") == r"C:\m\faster-whisper-large-v3-turbo")
finally:
    tr.discover_ct2_models = _orig_disc


section("12. worker 内意外异常不拖垮整个纠错")
import sstudio.core.llm as _llm

state = {"n": 0}


def flaky_chat(prof, messages, on_delta=None, _retry_no_cap=True):
    state["n"] += 1
    if state["n"] == 1:
        raise RuntimeError("没预料到的内部错误")
    return "\n".join(f"[{i}] {c.text}" for i, c in enumerate(_cur2[0]))


_cfg2 = Config()
_cfg2.profiles[0].api_key = "sk-test"
_cfg2.batch_size = 2
_cfg2.concurrency = 1
_cfg2.auto_retry = 0
_cfg2.strict_mode = False
_cfg2.glossary = ""
_cur2 = [[Cue(i, i + 1, "句%d" % i) for i in range(6)]]
_orig2 = _llm.chat
_llm.chat = flaky_chat
try:
    res2 = _llm.fix_document(_cfg2, _cur2[0])
    check("首批炸了但其余批次照常返回", len(res2.failures) >= 1
          and "内部错误" in res2.failures[0], res2.failures[:2])
finally:
    _llm.chat = _orig2


section("13. 单实例互斥：第二个进程不再起来")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
from PyQt5.QtWidgets import QApplication
from PyQt5.QtNetwork import QLocalSocket
from sstudio.ui.single_instance import SingleInstance, _server_name

_app = QApplication.instance() or QApplication([])
_a1 = SingleInstance(_app)
check("首个实例占住锁", _a1.try_start() is True)
_a2 = SingleInstance(_app)
check("后到实例检测到在跑的实例、自动让位", _a2.try_start() is False)
_fired = []
_a1.on_activate = lambda: _fired.append(1)
_c = QLocalSocket()
_c.connectToServer(_server_name())
if _c.waitForConnected(800):
    _c.write(b"activate")
    _c.waitForBytesWritten(300)
    _c.disconnectFromServer()
_t0 = time.time()
while time.time() - _t0 < 2.0 and not _fired:
    _app.processEvents()
    time.sleep(0.02)
check("唤醒回调被触发（第二实例会把主窗拉到前台）", bool(_fired))

raise SystemExit(finish())
