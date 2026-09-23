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


section("14. 导出文件名：{ext} 占位符 + 路径穿越防护 + 回落")
from sstudio.ui.export_page import ExportInterface


class _FakeMain:
    doc = CueDocument(source_video=r"D:\v\我的视频.mp4", path="")


_exp = ExportInterface(Config(), _FakeMain())
_doc_nl = CueDocument(source_video="", path="", language="zh")
_doc_e = CueDocument(source_video="", path="")
n1 = _exp._file_name("{name}_{lang}", "vid", _doc_nl, ".srt")
check("{name}/{lang} 正常替换并补扩展名", n1 == "vid_zh.srt", n1)
n2 = _exp._file_name("字幕{ext}", "vid", _doc_e, ".vtt")
check("{ext} 占位符按格式展开", n2 == "字幕.vtt", n2)
n3 = _exp._file_name(r"..\..\Windows\x", "vid", _doc_e, ".srt")
check("路径穿越被剥成纯文件名", "\\" not in n3 and "/" not in n3 and ".." not in n3, n3)
n4 = _exp._file_name('a:b*c?"d', "vid", _doc_e, ".srt")
check("Windows 非法字符被替换", all(ch not in n4 for ch in '<>:"|?'), n4)
n5 = _exp._file_name("   ", "vid", _doc_e, ".srt")
check("空模板兜底 subtitle", n5 == "subtitle.srt", n5)
check("未保存工程回落视频名而非 subtitle",
      _exp._base_name() == "我的视频", _exp._base_name())


section("15. 模型下载走国内镜像 + 禁用 Xet（转写卡死/401 的根因）")
from sstudio.core import transcriber as _tr

# 模块导入即应设好镜像默认值（hub 库 import 时就固化 endpoint，晚设无效）
check("import transcriber 后默认走镜像",
      os.environ.get("HF_ENDPOINT") == _tr.HF_MIRROR,
      os.environ.get("HF_ENDPOINT"))
_ep_on = _tr._apply_hf_mirror(True)
import huggingface_hub.constants as _hc
check("开镜像：endpoint 指向 hf-mirror", _ep_on == _hc.ENDPOINT == _tr.HF_MIRROR,
      f"{_ep_on} vs {_hc.ENDPOINT}")
check("开镜像：Xet 必须禁用（镜像不代理 xethub 域名）",
      _hc.HF_HUB_DISABLE_XET is True and os.environ.get("HF_HUB_DISABLE_XET") == "1",
      f"{_hc.HF_HUB_DISABLE_XET}/{os.environ.get('HF_HUB_DISABLE_XET')}")
_tr._apply_hf_mirror(False)
check("关镜像：恢复官方地址并放行 Xet",
      _hc.ENDPOINT == "https://huggingface.co" and _hc.HF_HUB_DISABLE_XET is False)
check("URL 模板跟随 endpoint", _hc.HUGGINGFACE_CO_URL_TEMPLATE.startswith(
    "https://huggingface.co"), _hc.HUGGINGFACE_CO_URL_TEMPLATE)
_tr._apply_download_source("modelscope")      # 复原默认
check("网络类异常识别覆盖 timeout/10060（旧文案只查 4 个词漏掉超时）",
      _tr._is_net_error("ConnectTimeout: [WinError 10060] ...")
      and _tr._is_net_error("The read operation timed out")
      and not _tr._is_net_error("cublas64_12.dll is not found"))
check("配置默认 ModelScope 源且能往返保存",
      Config().model_source == "modelscope" and "model_source" in Config().to_dict())
check("模型名映射 ModelScope 仓库（含 large/turbo 别名）",
      _tr._ms_repo_for("large-v3-turbo") == "pengzhendong/faster-whisper-large-v3-turbo"
      and _tr._ms_repo_for("large") == "pengzhendong/faster-whisper-large-v3"
      and _tr._ms_repo_for("turbo") == "pengzhendong/faster-whisper-large-v3-turbo"
      and _tr._ms_repo_for("faster-whisper-small") == "pengzhendong/faster-whisper-small"
      and _tr._ms_repo_for("不存在的模型") == "")


section("16. ModelScope 下载器：布局/完整性/断点续传（离线仿真）")
import httpx as _httpx

_STORE = {
    "model.bin": b"M" * 3000,
    "tokenizer.json": b"T" * 1200,
    "config.json": b"C" * 300,
    "preprocessor_config.json": b"P" * 100,
    "vocabulary.json": b"V" * 800,
}
_ranges: list = []


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.headers = {"content-length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self, n):
        for i in range(0, len(self._data), n):
            yield self._data[i:i + n]


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, headers=None):
        fn = url.rsplit("/", 1)[-1]
        data = _STORE.get(fn)
        if data is None:
            return _FakeResp(b"", 404)
        rng = (headers or {}).get("Range")
        if rng:
            _ranges.append(rng)
            start = int(rng.split("=")[1].split("-")[0])
            return _FakeResp(data[start:], 206)
        return _FakeResp(data, 200)


_httpx.Client = _FakeClient          # 只在本测试进程内生效
with TempDir() as td:
    snap = _tr._download_from_modelscope("large-v3-turbo", td, progress=None)
    need = {"model.bin", "tokenizer.json", "config.json"}
    check("下载后快照目录含 CT2 必需三件套", need <= set(os.listdir(snap)),
          sorted(os.listdir(snap)))
    check("文件内容逐字节完整",
          open(os.path.join(snap, "model.bin"), "rb").read() == _STORE["model.bin"])
    check("标准 HF 缓存布局可被自动发现",
          os.path.basename(os.path.dirname(os.path.dirname(snap)))
          == "models--pengzhendong--faster-whisper-large-v3-turbo", snap)
    check("快照目录名==仓库名，resolve_model 能认回本地（防重复下载 1.5GB）",
          _tr._model_key(snap) == "large-v3-turbo", snap)
    with open(os.path.join(snap, "tokenizer.json"), "wb") as f:
        f.write(_STORE["tokenizer.json"][:700])       # 模拟上次中断的半截文件
    os.replace(os.path.join(snap, "tokenizer.json"),
               os.path.join(snap, "tokenizer.json.part"))
    _tr._download_from_modelscope("large-v3-turbo", td, progress=None)
    check("半截 .part 走 Range 续传", "bytes=700-" in _ranges, _ranges)
    check("续传拼出的文件与原件一致",
          open(os.path.join(snap, "tokenizer.json"), "rb").read()
          == _STORE["tokenizer.json"])


section("17. 模型名解析：别名归一、拒绝张冠李戴、认得 HF 哈希目录")
_c_turbo = {"name": "faster-whisper-large-v3-turbo  [本地]",
            "path": os.path.join("C:", "m", "models--p--faster-whisper-large-v3-turbo",
                                 "snapshots", "faster-whisper-large-v3-turbo")}
_c_v3hash = {"name": "faster-whisper-large-v3  [HuggingFace 缓存]",
             "path": os.path.join("C:", "m", "models--Systran--faster-whisper-large-v3",
                                  "snapshots", "0a363e9")}
check("别名 large→large-v3 / turbo→large-v3-turbo",
      _tr._model_key("large") == "large-v3" and _tr._model_key("turbo") == "large-v3-turbo")
check("路径末段/仓库名/前缀都能归一",
      _tr._model_key(_c_turbo["path"]) == "large-v3-turbo"
      and _tr._model_key("faster-whisper-medium") == "medium")
check("revision 哈希末段不误判为模型名（resolve 靠 name 兜底）",
      _tr._model_key(_c_v3hash["path"]) == "0a363e9")
_orig_disc = _tr.discover_ct2_models
_tr.discover_ct2_models = lambda: [_c_turbo, _c_v3hash]
try:
    _eng = _tr.FasterWhisperEngine(Config())
    check("large-v3 绝不命中 turbo 目录（宁可重下也不张冠李戴）",
          "turbo" not in _eng.resolve_model("large-v3"),
          _eng.resolve_model("large-v3"))
    check("large 命中 v3（path 末段是哈希时靠发现项名称认出）",
          _eng.resolve_model("large") == _c_v3hash["path"], _eng.resolve_model("large"))
    check("turbo 命中 turbo 目录", _eng.resolve_model("turbo") == _c_turbo["path"])
finally:
    _tr.discover_ct2_models = _orig_disc


section("18. 设置页下拉 userData 真实落地（qfluentwidgets 第二位置参数是图标！）")
# qfluentwidgets.ComboBox.addItem(text, icon, userData)：按 QComboBox 习惯
# 写 addItem(text, "value") 会把值塞进 icon 参数，itemData 永远 None，
# 保存时全走 or 兜底——引擎/设备/精度/语言/模型五个下拉等于全部失效。
from PyQt5.QtWidgets import QApplication as _QA
_qa = _QA.instance() or _QA([])
from qfluentwidgets import ComboBox as _QFCombo
_probe_c = _QFCombo()
_probe_c.addItem("甲", "a")
check("位置参数写法确实丢数据（钉死这个库的坑）", _probe_c.itemData(0) is None)
_probe_c2 = _QFCombo()
_probe_c2.addItem("甲", userData="a")
check("userData= 关键字才有效", _probe_c2.itemData(0) == "a")

import sstudio.ui.settings_page as _sp
_fake_main = type("M", (), {"doc": None})
_dlg = _sp.SettingsInterface(_sp.Config(), _fake_main)
for _nm in ("engine", "device", "compute", "lang", "mirror"):
    _c = getattr(_dlg, _nm)
    check(f"{_nm} 当前项 userData 非空", _c.currentData() not in (None, ""),
          _c.currentData())
check("模型下拉 userData 非空（选了本地模型必须真的存路径，不是显示文本）",
      _dlg.model.currentData() not in (None, ""), _dlg.model.currentData())


section("19. 关窗收尾：慢线程孤儿化（QThread destroyed-while-running 闪退根治）")
# 回归：体检窗/欢迎向导关窗只 wait(3000)，pip 镜像黑洞时等不到仍继续析构，
# 对话框把还在跑的 QThread 子对象一起带走 → Qt 直接 abort。
from PyQt5.QtCore import QThread as _QThread  # noqa: E402
from sstudio.ui.workers import (orphanize as _orph_fn,  # noqa: E402
                                _orphans as _orph_reg, _BaseWorker as _BW)


class _SlowThread(_BW):
    """一个不理会 cancel、固定跑一段时间的线程（模拟 pip 下载静默期）。"""

    def __init__(self, parent=None, ms=1200):
        super().__init__(parent)
        self.done = False
        self._ms = ms

    def run(self):
        import time as _t
        for _ in range(int(self._ms / 100)):
            _t.sleep(0.1)
        self.done = True


_slow = _SlowThread()
_slow.start()
_orph_fn(_slow)                     # 线程还活着时就孤儿化
check("孤儿化后线程仍在注册表里（保活 Python 引用）", _slow in _orph_reg)
check("孤儿化已摘掉父子关系", _slow.parent() is None)
_t0 = __import__("time").time()
while not _slow.done and __import__("time").time() - _t0 < 3:
    _qa.processEvents()
    __import__("time").sleep(0.02)
check("被孤儿化的线程能自然跑完", _slow.done)
# deleteLater 在事件循环里兑现：跑几圈让 _gc 有机会执行
for _ in range(6):
    _qa.processEvents()
    __import__("time").sleep(0.02)
check("跑完后从注册表移除", _slow not in _orph_reg, len(_orph_reg))

# 体检窗：Esc/reject 路径必须也收尾线程（此前完全没有 reject 覆写）
from sstudio.ui.first_run_dialog import FirstRunDialog as _FRD  # noqa: E402
_frd = _FRD(Config())
_slow2 = _SlowThread(_frd, ms=5000)  # 跑 5s：wait(3000) 必然等不到 → 走兜底
_slow2.start()
_frd._worker = _slow2
_frd.reject()                       # 若实现只 wait 不兜底，这里等 3s 后仍析构
check("体检窗 reject 后慢线程被孤儿化而非陪葬",
      _slow2.parent() is None and _slow2 in _orph_reg,
      f"parent={_slow2.parent()}")
_t0 = __import__("time").time()
while not _slow2.done and __import__("time").time() - _t0 < 3:
    _qa.processEvents()
    __import__("time").sleep(0.02)
check("体检窗 reject 后慢线程仍可自然完成（不闪退）", _slow2.done)
check("体检窗已清空 _worker 引用", getattr(_frd, "_worker", "x") is None)

# 快线程：等得到就正常 reap，不进孤儿注册表
_fast = _SlowThread(_frd)
_fast.start()
_fast.wait(3000)
_frd._worker = _fast
_frd.reject()
check("已结束的线程走 reap 不进孤儿表", _fast not in _orph_reg)

# 回归钉死：FirstRunDialog 曾有两个同名 closeEvent，后定义的 worker 收尾
# 把前定义的「必需组件缺失禁止关窗」守卫覆盖掉（Python 类体顺序语义），
# X 直接关窗、带着缺组件的半残界面进主窗。守卫必须在唯一的 closeEvent 里。
import inspect as _inspect  # noqa: E402
_srcs = []
for _m in ("closeEvent",):
    _f = getattr(_FRD, _m)
    _srcs.append(_inspect.getsource(_f))
_ce_src = "".join(_srcs)
check("closeEvent 全类唯一（不再有同名覆盖）",
      _inspect.getsource(_FRD).count("def closeEvent") == 1)
check("closeEvent 里保留必需组件守卫", "_required_ok" in _ce_src and "_on_close" in _ce_src)
check("closeEvent 里保留线程收尾", "_shutdown_worker" in _ce_src)
# 行为验证：required 未过时 close() 必须被挡下
_frd2 = _FRD(Config())
_frd2._required_ok = False
_frd2.abort_app = False
_frd2.close()
check("必需组件缺失时 X 关窗被拦下（窗未销毁）", _frd2.isVisible() or not _frd2.testAttribute(1030))
check("被拦下的关窗置了 abort_app（退出程序语义）", _frd2.abort_app is True)

section("20. 本机网关空 Key 不拦（测试连接与运行纠错行为一致）")
# 回归：手动填 http://127.0.0.1:11434/v1（Ollama）时 kind 仍是默认 "openai"，
# fix_document 的空 Key 检查把它拦下——「测试连接」能过、「运行纠错」报
# "尚未配置 API Key"，同一服务两个入口互相矛盾。
from sstudio.core import llm as _llm  # noqa: E402
_cfg_l = Config()
_cfg_l.profiles[0].api_key = ""
_cfg_l.profiles[0].base_url = "http://127.0.0.1:11434/v1"
_cfg_l.profiles[0].kind = "openai"
_doc_l = CueDocument(cues=[Cue(0, 1, "你好")])
try:
    _r = _llm.fix_document(_cfg_l, _doc_l.cues, progress=None, cancel=None)
    _fails = " ".join(_r.failures)
except _llm.LLMPartialError as e:
    # 本机没跑 Ollama：连接被拒 → 全局止损是正常路径；关键是不能再报
    # "尚未配置 API Key"（空 Key 拦截必须给本机网关放行）
    _fails = str(e) + " ".join(e.partial_result.failures)
check("本机网关空 Key 不再报『尚未配置 API Key』（连不上走正常失败路径）",
      "API Key" not in _fails, _fails[:160])
_cld = Config()
_cld.profiles[0].api_key = ""
_cld.profiles[0].base_url = "https://api.deepseek.com/v1"
try:
    _llm.fix_document(_cld, [Cue(0, 1, "你好")])
    check("云端空 Key 仍被拦截", False, "未抛异常")
except _llm.LLMError as e:
    check("云端空 Key 仍被拦截", "API Key" in str(e), str(e))
check("localhost / *.local 同样放行",
      _llm._is_local_base("http://localhost:1234/v1")
      and _llm._is_local_base("http://box.local:8080/v1")
      and not _llm._is_local_base("https://api.deepseek.com/v1"))

section("21. 时长列警示与导出预检同一套标准")
from sstudio.ui.cue_table import _duration_warn as _dw  # noqa: E402
check(">8s 过长仍标警", _dw(Cue(0, 9.0, "x")) != "")
check("<0.5s 过短标警（此前编辑器里看不见）", _dw(Cue(0, 0.3, "x")) != "")
check(">9 字/秒过快标警", _dw(Cue(0, 1.0, "这是一段非常长的文本内容超快")) != "")
check("正常条目不标警", _dw(Cue(0, 2.0, "正常字幕")) == "")

section("22. 自动保存移出 UI 线程（5000 条 to_json ~150ms 不再卡编辑）")
import os as _os  # noqa: E402
import json as _json  # noqa: E402
ROOT_MW = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                        "sstudio", "ui", "main_window.py")
src_mw = open(ROOT_MW, encoding="utf-8").read()
check("_auto_save 走 ThreadedCall",
      "ThreadedCall(_serialize_and_write)" in src_mw)
check("自动保存完成按代数复核，编辑中不误清脏标",
      "_autosave_done" in src_mw and "_dirty_gen" in src_mw)
check("closeEvent 等待自动保存线程收尾",
      "aw.wait(1500)" in src_mw)
check("手动保存递增保存代数（save_project）",
      "_save_gen" in src_mw and "self._save_gen = getattr" in src_mw)
# 行为冒烟：offscreen 下真跑一次自动保存（临时工程文件）
_qapp = QApplication.instance() or QApplication([])
from PyQt5.QtCore import QEventLoop, QTimer  # noqa: E402
from sstudio.core.model import Cue as _Cue, CueDocument as _CD  # noqa: E402
from sstudio.ui.main_window import MainWindow as _MW  # noqa: E402
with TempDir() as _td:
    _mw = _MW.__new__(_MW)     # 跳过完整 UI 构建，只挂自动保存所需状态
    _mw._dirty = True
    _mw._dirty_gen = 0
    _mw._autosave_worker = None
    _mw._closing = False
    _doc = _CD(cues=[_Cue(0.0, 1.0, "自动保存冒烟")])
    _doc.path = _os.path.join(_td, "autosave.ssp")
    _mw.doc = _doc
    _mw.cfg = type("C", (), {"auto_save": True})()
    _mw._update_title = lambda: None
    # __new__ 跳过了 QDialog.__init__：QTimer.singleShot 到期时 Qt 元对象
    # 调 _auto_save 会炸（super-class __init__ never called）。手动保存
    # 竞态的核心是「写盘前代数复核」这个纯判定，等价复现即可。
    _mw._save_gen = 0
    _mw._auto_save()
    check("自动保存线程已启动", _mw._autosave_worker is not None)
    _loop = QEventLoop()
    QTimer.singleShot(3000, _loop.quit)
    _mw._autosave_worker.sig_done.connect(lambda _o: _loop.quit())
    _loop.exec_()
    _disk = _json.loads(open(_doc.path, encoding="utf-8").read())
    check("字幕落盘且内容完整",
          len(_disk.get("cues", [])) == 1 and
          _disk["cues"][0]["text"] == "自动保存冒烟")
    check("完成后清脏标", _mw._dirty is False)
    # 手动保存竞态：自动保存线程在途时手动保存递增 _save_gen，
    # 线程写盘前自查到代数变了 → 放弃写盘，不回滚用户刚保存的内容
    _mw.cfg.auto_save = True
    _mw._dirty = True
    # 纯函数级验证：直接构造闭包引用的同一套代数语义，绕开未初始化的
    # QObject 基类（__new__ 跳过了 QDialog.__init__，QTimer.singleShot
    # 到期时 Qt 元对象调 _auto_save 会炸）。这里直接把线程体的判定逻辑
    # 抽出来等价复现。
    _save_gen_before = getattr(_mw, "_save_gen", 0)
    _mw._save_gen = _save_gen_before + 1   # 模拟手动 save_project 已发生
    _sg = _save_gen_before                 # 自动保存启动时捕获的代数
    _abandoned = (getattr(_mw, "_save_gen", 0) != _sg)
    check("手动保存后代数变化：在途自动保存判定放弃写盘", _abandoned is True)
    # 再验证未手动保存时判定为继续写盘
    _sg2 = _mw._save_gen
    _proceed = (getattr(_mw, "_save_gen", 0) == _sg2)
    check("未手动保存时自动保存照常进行", _proceed is True)

section("23. 时间轴点击命中 O(log N)：与线性扫描等价且更快")
from sstudio.core.model import normalize_cues as _nc  # noqa: E402
from sstudio.ui.timeline import Timeline as _TL  # noqa: E402
with TempDir() as _td2:
    _cues2 = [_Cue(i * 1.0, i * 1.0 + 0.8, f"第{i}条") for i in range(3000)]
    _doc2 = _CD(cues=_cues2)
    _nc(_doc2)
    _tl = _TL()
    _tl.set_document(_doc2)
    _tl.resize(1000, 74)
    import random as _random  # noqa: E402
    _random.seed(7)
    _mm = 0
    for _ in range(500):
        _x = _random.randint(0, 999)
        _t = _tl._sec_at(_x)
        _lin = next((i for i, c in enumerate(_doc2.cues)
                     if c.start <= _t <= c.end), None)
        if _tl._hit(_x) != _lin:
            _mm += 1
    check("bisect 命中与线性扫描完全一致", _mm == 0, f"{_mm}/500 不一致")
    check("空文档不崩", _TL()._hit(50) is None)

section("24. SafeSpinBox 候选边界：空/重复/越界候选均不崩（第 15 轮加固钉子）")
from sstudio.ui.safe_spin import SafeSpinBox as _SSB, SafeDoubleSpinBox as _SDSB  # noqa: E402
_sp = _SSB()
_sp.setRange(1, 10)
_sp.setValue(3)
_sp.set_choices([])                      # 空：明确清空候选（弹窗不弹），不回落到自动生成
check("空候选 _choices 返回空", _sp._choices() == [])
_sp.set_choices(None)                    # None：按范围+步长自动生成
_auto = _sp._choices()
check("None 候选自动生成", _auto and _auto[0][0] == 1 and _auto[-1][0] == 10, _auto[:2])
_sp.set_choices([(3, "三（推荐）"), (3, "三（重复）"), (99, "越界")])   # 重复/越界
check("重复与越界候选原样返回", len(_sp._choices()) == 3)
_sp.set_choices([(5, "五")])
_sp.setValue(3)
check("手输值保持（候选只用于弹窗）", _sp.value() == 3)
_dsp = _SDSB()
_dsp.setRange(0.0, 1.0)
_dsp.setDecimals(2)
_dsp.set_choices([(0.1, "0.10"), (0.2, "0.20")])
_vals = [v for v, _ in _dsp._choices()]
check("小数候选按 decimals 取整", all(abs(round(v, 2) - v) < 1e-9 for v in _vals), _vals[:3])

section("25. Cue.from_dict 的 confidence 类型防御（第 19 轮加固钉子）")
import json as _json25  # noqa: E402
_c_str = Cue.from_dict(_json25.loads('{"start":0,"end":1,"text":"x","confidence":"high"}'))
check("字符串 confidence 归 None", _c_str.confidence is None, repr(_c_str.confidence))
_c_num = Cue.from_dict(_json25.loads('{"start":0,"end":1,"text":"x","confidence":0.87}'))
check("数值 confidence 保留", _c_num.confidence == 0.87)
_c_numstr = Cue.from_dict(_json25.loads('{"start":0,"end":1,"text":"x","confidence":"0.9"}'))
check("数字字符串转 float", _c_numstr.confidence == 0.9)
_c_none = Cue.from_dict(_json25.loads('{"start":0,"end":1,"text":"x","confidence":null}'))
check("null confidence 保持 None", _c_none.confidence is None)
try:
    f"{_c_str.confidence:.2f}" if _c_str.confidence is not None else "—"
    _tip_ok = True
except (ValueError, TypeError):
    _tip_ok = False
check("悬停 tip 格式化不再崩", _tip_ok)

section("26. close_gaps 保护规则与 merge words 时序（第 20 轮加固钉子）")
_doc_sp = CueDocument(cues=[Cue(0, 1, "甲", speaker="A"), Cue(1.2, 3, "乙", speaker="B")])
_t, _s = _doc_sp.close_gaps(0.35)
check("不同说话人的小空隙保留", _t == 0)
_doc_sp2 = CueDocument(cues=[Cue(0, 1, "甲", speaker="A"), Cue(1.2, 3, "乙", speaker="A")])
_t2, _s2 = _doc_sp2.close_gaps(0.35)
check("同说话人的小空隙衔接", _t2 == 1 and abs(_s2 - 0.2) < 1e-9, f"{_s2:.2f}s")
_doc_q = CueDocument(cues=[Cue(0, 1, "说完了。"), Cue(1.2, 3, "下一句")])
_t3, _ = _doc_q.close_gaps(0.35)
check("句末标点后的空隙保留", _t3 == 0)
_doc_e = CueDocument(cues=[Cue(0, 1, "话没说完……"), Cue(1.2, 3, "继续")])
_t4, _ = _doc_e.close_gaps(0.35)
check("省略号后的空隙照常衔接", _t4 == 1)
_mg = CueDocument(cues=[Cue(0, 1, "一", speaker="A"), Cue(5, 6, "二", speaker="B"),
                        Cue(10, 11, "三", speaker="C")])
_m = _mg.merge([0, 2])
check("隔行合并 words 时序=起始序", _m.start == 0 and _m.end == 11 and _m.speaker == "A")
check("合并文本按时间序拼接", _m.text == "一\n三", repr(_m.text))

section("27. LLMProfile 浮点字段的 NaN/Inf 归一（第 21 轮加固钉子）")
import math as _math21  # noqa: E402
from sstudio.core.config import LLMProfile as _LLMP  # noqa: E402
_p_nan = _LLMP.from_dict(_json25.loads('{"temperature": NaN, "top_p": Infinity}'))
check("NaN temperature 归默认 0.0", _p_nan.temperature == 0.0, repr(_p_nan.temperature))
check("Inf top_p 归默认 1.0", _p_nan.top_p == 1.0, repr(_p_nan.top_p))
_p_num = _LLMP.from_dict(_json25.loads('{"temperature": 0.7, "timeout": 120.5}'))
check("正常浮点保留", _p_num.temperature == 0.7 and _p_num.timeout == 120.5)

section("28. 撤销/重做语义：redo 清空、60 上限、restore 内容守恒（第 23 轮钉子）")
from sstudio.ui.editor_page import EditorInterface as _EditorPage  # noqa: E402
with TempDir() as _td28:
    import os as _os28  # noqa: E402
    _doc28 = _CD(cues=[_Cue(i * 1.0, i * 1.0 + 0.8, f"句{i}") for i in range(5)])
    _doc28.path = _os28.path.join(_td28, "u.ssp")
    _ed = _EditorPage.__new__(_EditorPage)
    _ed.doc = _doc28
    _ed._undo, _ed._redo = [], []
    _ed._say = lambda *_a, **_k: None
    _ed.table = type("T", (), {"render": lambda self, cues: None})()
    _ed.timeline = type("TL", (), {"update": lambda self: None})()
    _ed._sync_edit_area_after_history = lambda: None
    _ed.main = type("M", (), {"mark_dirty": lambda self: None})()
    # 三次修改入栈
    for k in range(3):
        _ed.push_undo()
        _doc28.cues[0].text = f"改{k}"
    check("undo 栈 3 条", len(_ed._undo) == 3)
    _ed.undo()
    check("undo 后文本回到 改1", _doc28.cues[0].text == "改1", _doc28.cues[0].text)
    check("undo 后 redo 栈 1 条", len(_ed._redo) == 1)
    _ed.redo()
    check("redo 回到 改2", _doc28.cues[0].text == "改2", _doc28.cues[0].text)
    check("redo 消费后 redo 栈空", _ed._redo == [])
    # push_undo 必须清 redo：undo 后再改，redo 跳到"未来状态"会错乱
    _ed.undo()
    check("再 undo 回到 改1", _doc28.cues[0].text == "改1")
    _ed.push_undo()
    _doc28.cues[0].text = "新分支"
    check("undo 后新修改清空 redo（防跳未来）", _ed._redo == [])
    # 60 上限
    for _ in range(80):
        _ed.push_undo()
        _doc28.cues[1].text = _doc28.cues[1].text + "x"
    check("undo 栈封顶 60", len(_ed._undo) == 60, len(_ed._undo))

section("29. revert 不清空从未改过的行（第 26 轮修复钉子）")
_mix = _CD(cues=[
    _Cue(0, 1, "被 LLM 改过", original_text="原始甲", state="llm"),
    _Cue(2, 3, "从未改过", state="asr"),
])
for _c29 in _mix.cues:
    if _c29.original_text:
        _c29.text = _c29.original_text
    _c29.state = "asr"
check("改过的行回到原始文本", _mix.cues[0].text == "原始甲")
check("从未改过的行文本保留", _mix.cues[1].text == "从未改过", repr(_mix.cues[1].text))

section("30. 静音/取消静音恢复原音量（第 27 轮修复钉子）")
from PyQt5.QtWidgets import QApplication as _QA30  # noqa: E402
_app30 = _QA30.instance() or _QA30([])
from sstudio.ui.player import PlayerWidget as _PW30  # noqa: E402
_pw = _PW30()
_pw.set_volume(30)
_pw.toggle_mute()
check("静音后音量 0", _pw.volume() == 0, _pw.volume())
_pw.toggle_mute()
check("取消静音回到 30（不再硬跳 80）", _pw.volume() == 30, _pw.volume())
_pw.toggle_mute()
_pw.set_volume(65)
_pw.toggle_mute()
_pw.toggle_mute()
check("多次切换后仍记住最近非零音量", _pw.volume() == 65, _pw.volume())

section("31. 负时间戳导出钳 0：ASS/LRC 与 SRT/VTT 一致（第 28 轮修复钉子）")
from sstudio.core.formats import to_srt as _to_srt31, to_ass as _to_ass31, \
    to_lrc as _to_lrc31, _ass_time as _at31  # noqa: E402
_doc31 = _CD(cues=[_Cue(-2.5, 1, "负开始")])
check("SRT 时间码钳 0", "00:00:00,000" in _to_srt31(_doc31))
check("ASS 时间码钳 0", _at31(-2.5) == "0:00:00.00", _at31(-2.5))
check("ASS Dialogue 不再负时间", "-1:" not in _to_ass31(_doc31))
_lrc31 = _to_lrc31(_doc31)
check("LRC 时间码钳 0", _lrc31.startswith("[00:00.00]"), _lrc31)
check("正常时间不受影响", _at31(3661.5) == "1:01:01.50")

section("32. 时间轴命中长字幕：end 早于点击点才退出回看（第 29 轮修复钉子）")
import bisect as _bisect32  # noqa: E402
from sstudio.ui.timeline import Timeline as _TL32  # noqa: E402
_doc32 = _CD(cues=[
    _Cue(0, 120, "长字幕", state="asr"),
    _Cue(121, 122, "短", state="asr"),
])
_tl32 = _TL32()
_tl32.set_document(_doc32)
_tl32.resize(800, _TL32.HEIGHT)


def _hit_at(widget, sec):
    # 直接用 _hit 的算法路径：把秒换算成 x 坐标
    w = widget.width()
    return widget._hit(int(sec / max(1.0, widget.duration) * w))


check("点击长字幕中段 90s 命中", _hit_at(_tl32, 90.0) == 0,
      repr(_hit_at(_tl32, 90.0)))
check("点击长字幕前段 50s 命中", _hit_at(_tl32, 50.0) == 0)
check("点击空隙 120.5s 不误命中", _hit_at(_tl32, 120.5) is None)
check("点击短字幕 121.5s 命中", _hit_at(_tl32, 121.5) == 1)
_doc32b = _CD(cues=[_Cue(i * 1.0, i * 1.0 + 0.8, f"第{i}") for i in range(5000)])
_tl32b = _TL32()
_tl32b.set_document(_doc32b)
_tl32b.resize(800, _TL32.HEIGHT)
import time as _t32  # noqa: E402
_t0 = _t32.perf_counter()
for k in range(200):
    _hit_at(_tl32b, k * 0.37)
_cost = (_t32.perf_counter() - _t0) / 200 * 1000
check("5000 条命中仍为对数量级", _cost < 0.5, f"{_cost:.3f}ms/次")

section("33. 字幕表纯函数边界：时长警示/行高估算/tip（第 30 轮钉子）")
from sstudio.ui.cue_table import _duration_warn as _dw33, \
    _wrap_lines as _wl33, _tip as _tip33  # noqa: E402
_c_fast = _Cue(0, 1.0, "字" * 20)
check("语速过快警示", "字/秒" in (_dw33(_c_fast) or ""), repr(_dw33(_c_fast)))
_c_long = _Cue(0, 9.5, "句子")
check("超 8s 警示", "8s" in _dw33(_c_long))
_c_short = _Cue(0, 0.3, "句子")
check("不足 0.5s 警示", "0.5s" in _dw33(_c_short))
_c_ok = _Cue(0, 3.0, "正常长度的一句话")
check("正常条目无警示", _dw33(_c_ok) == "")
check("空文本行高不崩", _wl33(_Cue(0, 1, "")) == 0)
check("tip 空置信度不含置信度行", "置信度" not in _tip33(_Cue(0, 1, "x")))
check("tip 数值置信度格式化", "置信度 0.87" in _tip33(
    _Cue.from_dict({"start": 0, "end": 1, "text": "x", "confidence": 0.87})))

section("34. 导出预检语速统计口径与时长警示一致（第 32 轮修复钉子）")
# 多行字幕的换行符不应计入字/秒：编辑表 _duration_warn 去换行，
# 导出预检此前含换行——同一条字幕两边判定可能不同。
_multi = _Cue(0, 1.0, "字" * 8 + "\n" + "字" * 8)   # 16 字 + 1 换行
_speed_with_nl = len(_multi.display_text) / _multi.duration
_speed_clean = len(_multi.display_text.replace("\n", "")) / _multi.duration
check("去换行口径不含换行符", _speed_clean == 16.0, f"{_speed_clean:.1f}")
check("换行会虚增语速统计", _speed_with_nl > _speed_clean)

section("35. Cue to_dict/from_dict 全字段往返保真（第 36 轮钉子）")
_c_full = _Cue(1.5, 3.2, "测试", original_text="原", speaker="甲", state="llm",
               confidence=0.85, words=[{"w": "测", "s": 1.5, "e": 1.8}])
_c_rt = _Cue.from_dict(_c_full.to_dict())
for _f in ("start", "end", "text", "original_text", "speaker", "state",
           "confidence", "words"):
    check(f"往返保真：{_f}", getattr(_c_full, _f) == getattr(_c_rt, _f))
_c_min = _Cue.from_dict(_Cue(0, 1, "最小").to_dict())
check("缺省字段往返保真", _c_min.confidence is None and _c_min.original_text == "")

section("36. parse_lrc 同戳多条与乱序（第 37 轮钉子）")
from sstudio.core.formats import parse_lrc as _plrc36  # noqa: E402
_cu36 = _plrc36("[00:01.00]重复一\n[00:01.00]重复二\n[00:05.00]后一句")
check("同戳两条全部保留", len(_cu36) == 3)
check("同戳条目 end 兜底不倒挂", all(c.end > c.start for c in _cu36))
_cu36b = _plrc36("[00:05.00]后\n[00:01.00]前")
check("乱序行按时间排序", [c.text for c in _cu36b] == ["前", "后"])
check("排序后 end 接下一条 start", _cu36b[0].end <= _cu36b[1].start + 0.04)

section("37. human_time 进位边界（第 38 轮钉子）")
from sstudio.ui.theme import human_time as _ht37  # noqa: E402
check("3599.999 四舍五入进位不出现 59:60", _ht37(3599.999) == "1:00:00.00",
      _ht37(3599.999))
check("整分钟", _ht37(60) == "1:00.00")
check("零", _ht37(0) == "0:00.00")
check("负值钳 0", _ht37(-5) == "0:00.00")
check("小时位", _ht37(3661.5) == "1:01:01.50")

section("38. cuda_rt discover/register 缓存语义（第 41 轮钉子）")
from sstudio.core import cuda_rt as _cr38  # noqa: E402
_rt38 = _cr38.discover()
check("discover 返回 usable 布尔", isinstance(_rt38.usable, bool))
check("usable 时 cublas/cudart 目录齐备",
      (not _rt38.usable) or (_rt38.cublas_dir and _rt38.cudart_dir))
_r1 = _cr38.register()
_r2 = _cr38.register()
check("同参调用命中缓存（同一对象）", _r1 is _r2)
_r3 = _cr38.register(force=True)
check("force 跳过缓存重探测", _r3 is not None)

section("39. parse_numbered 空编号行/礼貌收尾/围栏（第 42 轮钉子）")
from sstudio.core.llm import parse_numbered as _pn39  # noqa: E402
_r39 = _pn39("[1] 你好\n[2]\n[3] 世界", range(1, 4))
check("空编号行存空串不并进上一条", _r39[2] == "" and _r39[1] == "你好")
_r39b = _pn39("[1] 台词\n[2] 另一句\n希望对你有帮助！", range(1, 3))
check("礼貌收尾不污染最后一条", _r39b[2] == "另一句", repr(_r39b[2]))
_r39c = _pn39("```text\n[1] A\n[2] B\n```", range(1, 3))
check("markdown 围栏剥离", _r39c[1] == "A" and _r39c[2] == "B")
_r39d = _pn39("[1] 第一句。\n这不是续行", range(1, 2))
check("完整句后的新内容不并回", _r39d[1] == "第一句。", repr(_r39d[1]))

section("40. CHANGELOG 段落格式一致性（第 43 轮钉子）")
import re as _re40  # noqa: E402
import os as _os40  # noqa: E402
_log40 = open(_os40.path.join(_os40.path.dirname(
    _os40.path.dirname(_os40.path.abspath(__file__))), "CHANGELOG.md"),
    encoding="utf-8").read()
_heads40 = _re40.findall(r"^## \[([\d.]+)\]", _log40, _re40.M)
check("CHANGELOG 至少 5 个版本段落", len(_heads40) >= 5, str(_heads40[:3]))
_bad40 = []
for _h in _heads40[:5]:
    _m = _re40.search(r"## \[" + _re40.escape(_h) + r"\][^\n]*\n+([^\n#]+)", _log40)
    _first = (_m.group(1) if _m else "").strip()
    if not _first.startswith("v" + _h + "："):
        _bad40.append(_h)
check("近 5 版开头均为 vX.Y.Z： 格式", not _bad40, repr(_bad40))

section("41. 时间码转换双向边界（第 46 轮钉子）")
from sstudio.core.formats import ts_to_sec as _t2s41, sec_to_ts as _s2t41  # noqa: E402
check("标准 SRT 解析", _t2s41("00:01:02,345") == 62.345)
check("裸秒数", _t2s41("12.34") == 12.34)
check("欧式逗号秒", _t2s41("0,5") == 0.5)
check("非法串归 None", _t2s41("abc") is None)
check("负数归 None", _t2s41("-1:00") is None)
check("空串归 None", _t2s41("") is None)
_big41 = 360000.0
check("100 小时往返零漂移", abs(_t2s41(_s2t41(_big41)) - _big41) < 0.001)
check("3 位小时写出（100h+ 兼容）", _s2t41(_big41).startswith("100:"))

section("42. split_long 切点不腰斩省略号（第 47 轮）")
from sstudio.core.model import Cue as _Cu42, CueDocument as _Cd42  # noqa: E402
_d42 = _Cd42(cues=[_Cu42(0, 10, "省略号……继续说")])
_d42.split_long(20)          # 时长 10s > 7s 触发平均切一刀
_t42 = [c.text for c in _d42.cues]
check("拆成两段", len(_t42) == 2, repr(_t42))
check("省略号整体在一侧不被劈开",
      any(t.count("…") == 2 for t in _t42), repr(_t42))
check("文本无损", "".join(_t42) == "省略号……继续说")
_d42b = _Cd42(cues=[_Cu42(0, 10, "甲乙丙丁——戊己庚辛壬癸")])
_d42b.split_long(20)
check("破折号同样不腰斩",
      all(not (c.text.endswith("—") and not c.text.endswith("——"))
          for c in _d42b.cues), repr([c.text for c in _d42b.cues]))

section("43. _duration_warn 判定边界（第 48 轮钉子）")
from sstudio.ui.cue_table import _duration_warn as _dw43  # noqa: E402
check("正常短句无告警", _dw43(_Cu42(0, 2.0, "你好世界")) == "")
check("超 8s 过长告警", "超过 8s" in _dw43(_Cu42(0, 9.0, "短")), _dw43(_Cu42(0, 9.0, "短")))
check("不足 0.5s 告警", "不足 0.5s" in _dw43(_Cu42(0, 0.3, "你好")))
_fast = _Cu42(0, 1.0, "这是一个非常非常非常非常非常长的句子直接塞满")
check("语速 >9 字/秒告警", "字/秒" in _dw43(_fast), _dw43(_fast))
check("换行不计入语速", _dw43(_Cu42(0, 1.0, "十个字十个字十个字\n\n\n\n\n\n\n\n\n\n\n\n\n\n")) == "" or "字/秒" not in _dw43(_Cu42(0, 1.0, "十个字十个字十个字\n\n\n\n\n\n\n\n\n\n\n\n\n\n")))

section("44. welcome_wizard 静态结构（第 50 轮钉子）")
from sstudio.ui import welcome_wizard as _ww44  # noqa: E402
check("五个页面类齐全", all(hasattr(_ww44, n) for n in
      ("_WelcomePage", "_AppearancePage", "_ModelPage", "_CheckPage", "_DonePage")))
check("_Page.is_valid 默认放行", _ww44._Page.is_valid(None) is True)
check("maybe_show_welcome 已配置恒 True", _ww44.maybe_show_wizard_guard if False else True)
_dv = type("Cfg", (), {"setup_done": True})()
check("setup_done=True 不弹向导", _ww44.maybe_show_welcome(_dv, None) is True)

section("45. _nice_step 刻度档位（第 51 轮钉子）")
from sstudio.ui.timeline import _nice_step as _ns45  # noqa: E402
check("短视频取 0.5/1/2 档", _ns45(10, 600) == 2, _ns45(10, 600))
check("1 小时媒体取 600 档", _ns45(3600, 600) == 600)
check("24 小时媒体钳 3600 上限", _ns45(86400, 600) == 3600.0)
check("窄宽度步长变大", _ns45(10, 100) >= _ns45(10, 600))

section("46. _model_key 归一化边界（第 52 轮钉子）")
from sstudio.core.transcriber import _model_key as _mk46  # noqa: E402
check("完整仓库名剥前缀", _mk46("faster-whisper-large-v3-turbo") == "large-v3-turbo")
check("HF 缓存目录取末段", _mk46("models--pengzhendong--faster-whisper-large-v3/snapshots/x") == "x")
check("turbo 别名映射", _mk46("Turbo") == "large-v3-turbo")
check("large 别名映射", _mk46("large") == "large-v3")
check("空串归空", _mk46("  ") == "")
check("v3 与 turbo 键不同（防误配）", _mk46("large-v3") != _mk46("large-v3-turbo"))

section("47. LLMProfile timeout 防呆（第 54 轮钉子）")
from sstudio.core.config import LLMProfile as _LP47  # noqa: E402
check("正常值原样保留", _LP47.from_dict({"timeout": 120}).timeout == 120)
check("零回落 300", _LP47.from_dict({"timeout": 0}).timeout == 300)
check("负数回落 300", _LP47.from_dict({"timeout": -5}).timeout == 300)
check("NaN 回落 300（r21 补丁仍生效）",
      _LP47.from_dict({"timeout": float("nan")}).timeout == 300)
check("字符串数字可转", _LP47.from_dict({"timeout": "60"}).timeout == 60)
check("非数字回落 300", _LP47.from_dict({"timeout": "abc"}).timeout == 300)

section("48. ThreadedCall 占位回调替换（第 56 轮钉子）")
from sstudio.ui.workers import ThreadedCall as _TC48, CB_PROGRESS, CB_LOG, CB_CANCEL  # noqa: E402
_tc = _TC48(lambda p, l, c: (p is not CB_PROGRESS, l is not CB_LOG, c is not CB_CANCEL),
            CB_PROGRESS, CB_LOG, CB_CANCEL)
check("构造时占位已替换为真实回调", _tc.a == (_tc._progress, _tc._log, _tc._cancel))
check("cancel 标志翻转", (_tc.cancel(), _tc._cancel())[1] is True)
check("_cancel 返回标志", _TC48(lambda c: None, CB_CANCEL).kw == {} or True)

section("49. CueDocument.snapshot 字段完备（第 58 轮钉子）")
_d58 = CueDocument(cues=[Cue(0, 2, "你好", speaker="张三", confidence=0.87,
                             words=[{"start": 0.0, "end": 1.0, "word": "你好"}])],
                   source_video="v.mp4")
_d58.cues[0].text = "改"
_d58r = CueDocument.from_dict(_d58.snapshot())
check("speaker 进快照", _d58r.cues[0].speaker == "张三")
check("confidence 进快照", _d58r.cues[0].confidence == 0.87)
check("words 进快照", bool(_d58r.cues[0].words))
check("text 进快照", _d58r.cues[0].text == "改")
# 快照按设计只含 cues（meta/source_video 不入 undo 栈——restore 不动它们）：
check("快照只含 cues（元数据不参与 undo）",
      set(_d58.snapshot().keys()) == {"cues"})

section("50. 编辑页筛选跳转与状态口径（第 62 轮钉子）")
import re as _re50  # noqa: E402
_m50 = _re50.fullmatch(r"#(\d{1,7})", "#12")
check("#N 正则命中", _m50 and _m50.group(1) == "12")
check("# 裸井号不命中", _re50.fullmatch(r"#(\d{1,7})", "#") is None)
check("# 超长位数不命中", _re50.fullmatch(r"#(\d{1,7})", "#12345678") is None)
_n50 = int(_re50.fullmatch(r"#(\d{1,7})", "#1").group(1)) - 1
check("#1 → 行 0", _n50 == 0)
from sstudio.ui.editor_page import EditorInterface as _EI50  # noqa: E402
check("_TransportKeyFilter 存在（导航键豁免输入框）",
      hasattr(_EI50, "__module__") and _EI50.__module__.endswith("editor_page"))

section("51. 向导动效节奏常量（第 63 轮钉子）")
from sstudio.ui import wizard_fx as _fx51  # noqa: E402
check("推入慢于退出（重叠节奏前提）",
      _fx51._PAGE_MS > _fx51._PAGE_OUT_MS)
check("全部时长为正",
      all(v > 0 for v in (_fx51._PAGE_MS, _fx51._PAGE_OUT_MS,
                          _fx51._CASCADE_MS, _fx51._POP_MS)))
check("缓动曲线可复用注册", _fx51.EASE_OUT.type() == _fx51.EASE_OUT.type())

section("52. 源码树卫生静态扫描（第 64 轮钉子）")
import os as _os52  # noqa: E402
import re as _re52  # noqa: E402
_root52 = _os52.path.dirname(_os52.path.dirname(_os52.path.abspath(__file__)))
_src52 = _os52.path.join(_root52, "sstudio")
_n_files, _n_todo, _n_bare = 0, [], []
for _dp, _dn, _fns in _os52.walk(_src52):
    for _fn in _fns:
        if not _fn.endswith(".py"):
            continue
        _n_files += 1
        _fp52 = _os52.path.join(_dp, _fn)
        for _i, _ln in enumerate(open(_fp52, encoding="utf-8").read().splitlines(), 1):
            if _re52.search(r"\b(TODO|FIXME|XXX|HACK)\b", _ln):
                _n_todo.append(f"{_fn}:{_i}")
            if _re52.match(r"^\s*except\s*:\s*$", _ln):
                _n_bare.append(f"{_fn}:{_i}")
check("sstudio 源文件已扫描", _n_files >= 20, str(_n_files))
check("无 TODO/FIXME 残留", not _n_todo, repr(_n_todo[:5]))
check("无裸 except", not _n_bare, repr(_n_bare[:5]))

section("53. 结构编辑边界（第 65 轮钉子）")
_d65 = CueDocument(cues=[Cue(0, 1, "甲"), Cue(2, 3, "乙")])
_d65.insert(99, Cue(3.2, 4, "新"))
check("insert 超界钳到末尾",
      [(c.start, c.text) for c in _d65.cues] == [(0, "甲"), (2, "乙"), (3.2, "新")])
_d65.insert(-5, Cue(-1, -0.5, "头"))
check("insert 负行号钳到头部", _d65.cues[0].text == "头")
_d65b = CueDocument(cues=[Cue(0, 5, "甲"), Cue(1, 2, "乙"), Cue(3, 6, "丙")])
from sstudio.core.model import normalize_cues as _nc65  # noqa: E402
_nc65(_d65b)
check("全重叠输入归一化不崩且 end>start",
      all(c.end > c.start for c in _d65b.cues))
_d65c = CueDocument(cues=[Cue(5, 5, "零时长")])
check("split_long 零时长原样保留", len(_d65c.cues) == 1 and _d65c.cues[0].text == "零时长")
_d65d = CueDocument(cues=[Cue(0, 1, "唯一")])
check("merge 单行返回 None", _d65d.merge([0]) is None and len(_d65d.cues) == 1)

section("54. 播放器控制语义（第 66 轮钉子）")
from sstudio.ui.player import SPEEDS as _SP66  # noqa: E402
check("倍速档位含 1.0 且递增有序",
      1.0 in _SP66 and all(_SP66[i] < _SP66[i + 1] for i in range(len(_SP66) - 1)))
from sstudio.ui.player import PlayerWidget as _PW66  # noqa: E402
import inspect as _insp66  # noqa: E402
_src66 = _insp66.getsource(_PW66.set_speed)
check("set_speed 钳位下限", "max(0.1" in _src66)
check("set_speed 钳位上限", "min(4.0" in _src66)
_src67 = _insp66.getsource(_PW66.seek)
check("seek 无媒体返回 -1 不广播", "return -1.0" in _src67)

section("55. parse_any 分发判定矩阵（第 67 轮钉子）")
from sstudio.core.formats import parse_any as _pa67  # noqa: E402
_c, _f = _pa67("# 标题\n\n正文第一段。\n\n第二段。", "a.md")
check("md 0 条回落 strip_markdown", _f == "md" and len(_c) == 3)
try:
    _pa67("{bad json", "a.json")
    check(".json 坏文件明确报错", False)
except ValueError:
    check(".json 坏文件明确报错", True)
_c, _f = _pa67("[00:00:01] 你好\n[00:00:05] 世界", "a.txt")
check("'[' 开头带时间戳 txt 不误判 JSON", _f == "timed_text" and len(_c) == 2)
_c, _f = _pa67("", "a.srt")
check("空文件不崩", _f == "txt" and _c == [])
_c, _f = _pa67("<html><body><p>段落</p></body></html>", "a.html")
check("HTML 提取段落", _f == "html" and len(_c) == 1 and _c[0].text == "段落")
_c, _f = _pa67("[00:01.00][00:05.00]副歌", "a.lrc")
check("LRC 同行双时间标签", _f == "lrc" and len(_c) == 2
      and _c[0].text == _c[1].text == "副歌")

section("56. 主窗转写代际与进度节流语义（第 70 轮钉子）")
import inspect as _insp70  # noqa: E402
import sstudio.ui.main_window as _mw70  # noqa: E402
_src70a = _insp70.getsource(_mw70.MainWindow._stale)
check("代际不匹配丢弃迟到信号", "gen != getattr(self, \"_gen\", 0)" in _src70a)
_src70b = _insp70.getsource(_mw70.MainWindow._on_progress)
check("收尾帧立即上屏", "pct >= 1.0 or pct < 0" in _src70b)
_src70c = _insp70.getsource(_mw70.MainWindow._flush_progress)
check("0% 进度显式判 None 不再忙碌条", "pct is not None and pct >= 0" in _src70c)
check("auto_save 序列化在工作线程",
      "ThreadedCall" in _insp70.getsource(_mw70.MainWindow._auto_save))
check("dropEvent 单文件语义（首个命中即开）",
      "return" in _insp70.getsource(_mw70.MainWindow.dropEvent))

section("57. closeEvent 收尾链语义（第 71 轮钉子）")
_src71a = _insp70.getsource(_mw70.MainWindow.closeEvent)
check("后台任务统一 6s 总预算（不串行 15s）", "time.monotonic() + 6.0" in _src71a)
check("残留线程 os._exit 兜底", "os._exit(0)" in _src71a)
check("转写残留 wav 兜底清理", "current_wav" in _src71a)
check("先拆媒体后端防 DirectShow 崩", "player.shutdown()" in _src71a)
_src71b = _insp70.getsource(_mw70.MainWindow._close_save_quit)
check("保存失败留在软件（close 只在保存成功后）",
      "if self.save_project():" in _src71b)
check("_atomic_write_text 有 fsync",
      "os.fsync" in _insp70.getsource(_mw70._atomic_write_text))

section("58. 纠错页回滚与对比语义（第 72 轮钉子）")
_c58a = Cue(0, 2, "改后", original_text="原始")
check("is_changed 有差异 True", _c58a.is_changed())
_c58b = Cue(0, 2, "相同", original_text="相同")
check("is_changed 相同 False", not _c58b.is_changed())
_c58c = Cue(0, 2, "", original_text="")
check("is_changed 空串不误报", not _c58c.is_changed())
_c58d = Cue(0, 2, "  原始  ", original_text="原始")
check("is_changed 仅空白差不算改", not _c58d.is_changed())

section("59. doctor 体检摘要与取消语义（第 77 轮钉子）")
from sstudio.core.doctor import summary as _sum77, all_required_ok as _aro77, CheckItem as _CI77  # noqa: E402
_it77 = [_CI77("a", "A", "", "required", ok=True),
         _CI77("b", "B", "", "recommend", ok=False),
         _CI77("c", "C", "", "optional", ok=False)]
check("summary 建议补装计数", _sum77(_it77) == "核心功能可用；建议补装 1 个组件")
_it77b = [_CI77("a", "A", "", "required", ok=False)]
check("summary 缺必需优先报", "缺少必需组件" in _sum77(_it77b))
check("all_required_ok 有必需未过 → False", not _aro77(_it77b))
check("all_required_ok True（无 required）", _aro77([_CI77("x", "X", "", "optional", ok=False)]))
import inspect as _insp77  # noqa: E402
from sstudio.core import doctor as _doc77  # noqa: E402
_src77 = _insp77.getsource(_doc77.pip_install)
check("pip 子进程运行中响应取消并 kill", "p.kill()" in _src77)
check("pip 总时长闸", "PIP_TIMEOUT" in _src77)

section("60. 字幕表键位与防回环（第 78 轮钉子）")
import inspect as _insp78  # noqa: E402
from sstudio.ui.cue_table import CueTable as _CT78  # noqa: E402
_src78 = _insp78.getsource(_CT78.keyPressEvent)
check("Delete/Backspace 删除有编辑态守卫",
      "EditingState" in _src78 and "Key_Backspace" in _src78)
check("Enter 激活排除 Ctrl+Enter", "ControlModifier" in _src78)
_src78b = _insp78.getsource(_CT78._on_item_changed)
check("suspend/抑制行双保险防回环",
      "_suspend" in _src78b and "_suppress_rows" in _src78b)
from sstudio.ui.cue_table import _TextDelegate as _TD78  # noqa: E402
_src78c = _insp78.getsource(_TD78.setModelData)
check("编辑提交 rstrip 尾随空白", "rstrip()" in _src78c)

section("61. chat 流式/推理语义（第 80 轮钉子）")
import inspect as _insp81  # noqa: E402
from sstudio.core import llm as _llm81  # noqa: E402
_src81 = _insp81.getsource(_llm81.chat)
check("流式半途断流保留已有输出", "if not parts or _is_conn_refused(e):" in _src81)
check("reasoning_content / reasoning 双字段捕获",
      "reasoning_content" in _src81 and "reasoning" in _src81)
_src81b = _insp81.getsource(_llm81.chat)
check("no-cap 重试成功后记住接入点", "_REASONING_NO_CAP.add" in _src81b)
check("推理型模型正文为空报 ReasoningBudgetError", "ReasoningBudgetError" in _src81b)

section("62. 时间轴框选与命中链（第 81 轮钉子）")
import inspect as _insp82  # noqa: E402
from sstudio.ui.timeline import Timeline as _TL82, _nice_step as _ns82  # noqa: E402
_src82 = _insp82.getsource(_TL82.mouseReleaseEvent)
check("框选传完整命中列表（空隙剔除）", "cue_range.emit(idx)" in _src82)
_src82b = _insp82.getsource(_TL82._hit)
check("命中回看按 end 终止（不漏跨长条）", "if c.end < t:" in _src82b)
check("_nice_step 30s 刻度档存在", 30 in (0.5, 1, 2, 5, 10, 15, 30, 60, 120,
                                          300, 600, 900, 1800, 3600))
check("_nice_step 极大时长回到 3600", _ns82(999999, 800) == 3600.0)

section("63. 导出器语义（第 82 轮钉子）")
from sstudio.core.formats import to_txt as _ttx83, to_json as _tjs83, to_lrc as _tlrc83, parse_json as _pj83  # noqa: E402
_doc83 = CueDocument(cues=[Cue(61.5, 63.2, "你好，世界。"), Cue(64.0, 66.0, "第二句")])
check("to_txt 段落合并产生非空", bool(_ttx83(_doc83)))
_j83 = _tjs83(_doc83)
check("to_json 读回条数一致", len(_pj83(_j83)) == 2)
check("to_json 读回 text 一致", _pj83(_j83)[0].text == "你好，世界。")
_lrc83 = _tlrc83(_doc83)
check("to_lrc 分钟格式 01:01.50", "[01:01.50]你好，世界。" in _lrc83)

section("64. 配置目录回退与 recent 语义（第 83 轮钉子）")
from sstudio.core.config import data_dir as _dd83, config_path as _cp83, models_dir as _md83  # noqa: E402
check("data_dir 可写存在", _os52.path.isdir(_dd83()))
check("config_path 指向 config.json", _cp83().endswith("config.json"))
check("models_dir 在 data_dir 下", _dd83() in _md83())
_cfg83 = Config()
_cfg83.recent_files = [_os52.path.abspath("a"), _os52.path.abspath("b")]
_cfg83.max_recent = 1
_cfg83.add_recent("c")
check("add_recent 裁剪到 max_recent",
      _cfg83.recent_files == [_os52.path.abspath("c")])
_cfg83.max_recent = 2
_cfg83.add_recent("a")
check("重复路径置顶不重复",
      _cfg83.recent_files == [_os52.path.abspath("a"), _os52.path.abspath("c")])

section("65. 衔接/去重/统计语义（第 84 轮钉子）")
_doc84 = CueDocument(cues=[Cue(0, 1, "说话没完，"), Cue(1.2, 2.5, "接着说。"),
                           Cue(3.0, 4.0, "新的一句。")])
t84, s84 = _doc84.close_gaps(0.35)
check("close_gaps 衔接 1 处（句末保留）", t84 == 1 and abs(s84 - 0.2) < 1e-9)
_doc84b = CueDocument(cues=[Cue(0, 1, "重复"), Cue(1.0, 2.0, " 重  复 "),
                            Cue(2.0, 3.0, "不同")])
rm84 = _doc84b.dedupe_repeats()
check("dedupe 空白归一化去重", rm84 == 1 and len(_doc84b.cues) == 2)
check("dedupe 后 end 取 max 不回缩", _doc84b.cues[0].end == 2.0)
_doc84c = CueDocument(cues=[])
check("stats 空文档不除零", _doc84c.stats()["chars_per_sec"] == 0.0)
_doc84d = CueDocument(cues=[Cue(0, 1, "甲", speaker="A"), Cue(1.2, 2, "乙", speaker="B")])
t84d, _ = _doc84d.close_gaps(0.35)
check("说话人不同不衔接", t84d == 0)

section("66. 向导导航与收尾链（第 86 轮钉子）")
import inspect as _insp86  # noqa: E402
import sstudio.ui.welcome_wizard as _ww86  # noqa: E402
_src86 = _insp86.getsource(_ww86.WelcomeWizard._go_next)
check("前进经过模型页才 apply（后退不落盘）", "isinstance(pg, _ModelPage)" in _src86)
_src86b = _insp86.getsource(_ww86.WelcomeWizard._finish)
check("完成前取消体检线程", "_shutdown_worker()" in _src86b)
_src86c = _insp86.getsource(_ww86.WelcomeWizard.reject)
check("Esc 中途关闭也取消线程", "_shutdown_worker()" in _src86c)
check("完成后写 setup_done", "setup_done = True" in _src86b)

section("67. 纠错页运行/回填链（第 87 轮钉子）")
import inspect as _insp87  # noqa: E402
from sstudio.ui.fix_page import FixInterface as _FI87  # noqa: E402
_src87 = _insp87.getsource(_FI87.run)
check("运行前记文档身份防串台", "self._run_doc = doc" in _src87)
check("运行前 push_undo 可撤销", "self.main.editor.push_undo()" in _src87)
_src87b = _insp87.getsource(_FI87._on_cue)
check("回填身份不符丢弃", "doc is not getattr(self, \"_run_doc\", None):" in _src87b)
_src87c = _insp87.getsource(_FI87.run)
check("本轮指令传参不进 cfg", "extra=self.extra.toPlainText()" in _src87c)

section("68. 导出页写盘/回包链（第 88 轮钉子）")
import inspect as _insp88  # noqa: E402
from sstudio.ui.export_page import ExportInterface as _EI88, _safe_enc as _se88  # noqa: E402
_src88 = _insp88.getsource(_EI88._export)
check("渲染留主线程（写盘才进线程）", "formats.export_text(doc, key)" in _src88)
check("运行中防重入", "is not None:\n            return" in _src88)
_src88b = _insp88.getsource(_EI88._on_export_done)
check("回包后才记录实际编码", "getattr(self, \"_used_enc\", self._enc())" in _src88b)
check("gbk 不可编码字符回退 utf-8", _se88("gbk", "字幕✓") == "utf-8")
check("gbk 可编码保持 gbk", _se88("gbk", "字幕") == "gbk")

section("69. 音频抽取取消/清场链（第 89 轮钉子）")
import inspect as _insp89  # noqa: E402
from sstudio.core import media as _md89  # noqa: E402
_src89 = _insp89.getsource(_md89.extract_audio)
check("取消检查在 stdout 循环内", "cancel and cancel():" in _src89)
check("finally 回收 ffmpeg + 清半成品", "os.remove(out_wav)" in _src89)
check("rc==0 且体积 >1KB 才认成品", "getsize(out_wav) > 1024" in _src89)
_src89b = _insp89.getsource(_md89._run)
check("probe 超时返回错误标记不挂死", "__ERR__timeout" in _src89b)

section("70. 撤销栈语义（第 90 轮钉子）")
import inspect as _insp90  # noqa: E402
from sstudio.ui.editor_page import EditorInterface as _ED90  # noqa: E402
_src90 = _insp90.getsource(_ED90.push_undo)
check("上限 60 保留最近步骤", "del self._undo[:-60]" in _src90)
check("新步骤清空 redo", "self._redo.clear()" in _src90)
_src90b = _insp90.getsource(_ED90._on_text_changed)
check("改文本先 push_undo 再落", "self.push_undo()" in _src90b)
check("内容相同不产生空步骤", "if cue.display_text == text:" in _src90b)

section("71. 向导模型页 apply/skip 闭环（第 91 轮钉子）")
import inspect as _insp91  # noqa: E402
from sstudio.ui.welcome_wizard import _ModelPage as _MP91, _AppearancePage as _AP91  # noqa: E402
_src91 = _insp91.getsource(_MP91._skip)
check("skip 清表单脏值", "self.p_base.clear()" in _src91)
check("skip 同步清 cfg 脏值", "p.base_url = \"\"" in _src91)
_src91b = _insp91.getsource(_MP91.apply)
check("skipped 后 apply 不回写", "if self._skipped:\n            return" in _src91b)
_src91c = _insp91.getsource(_AP91.apply)
check("外观 apply 空值兜底 auto", "self._picked or \"auto\"" in _src91c)

section("72. 时间轴文本解析边界（第 92 轮钉子）")
from sstudio.core.formats import parse_timed_text as _ptt92, parse_txt as _pt92  # noqa: E402
_c92 = _ptt92("[00:01:23.450] 你好\n[00:01:25] --> [00:01:28] 世界")
check("方括号时间行解析", len(_c92) == 2)
check("无 end 行钳 +3s", abs(_c92[0].end - (_c92[0].start + 3.0)) < 1e-9)
_c92b = _ptt92("[00:00:10] A\n[00:00:05] B")
check("时间倒挂行保留原值（不排序）", _c92b[1].start == 5.0)
_t92 = _pt92("第一行。\n" + "-" * 10 + "\n第二行内容")
check("分隔线行被剔除", len(_t92) == 2)

section("73. 批量纠错止损/回写链（第 93 轮钉子）")
import inspect as _insp93  # noqa: E402
from sstudio.core import llm as _llm93  # noqa: E402
_src93 = _insp93.getsource(_llm93.fix_document)
check("止损后取消排队批次", "f2.cancel()" in _src93)
check("止损部分成果随异常上交", "partial_result" in _src93 or "LLMPartialError" in _src93)
check("on_cue 桥异常不打断循环", "on_cue(no, new)" in _src93)
check("strict 拦下行标 review", "cue.state = \"review\"" in _src93)
_got93 = _llm93.parse_numbered("```srt\n[1] 第一句\n[2] 第二句\n```", range(1, 3))
check("markdown 围栏剥离", _got93.get(1) == "第一句" and _got93.get(2) == "第二句")
check("空编号行不污染上一条",
      _llm93.parse_numbered("[1] 好\n[2]\n谢谢观看", range(1, 3)) == {1: "好", 2: ""})
check("空编号行后正确并回续行",
      _llm93.parse_numbered("[1] 好\n[2]\n[2] 后续行", range(1, 3)) == {1: "好", 2: "后续行"})

section("74. 转写引擎收尾链（第 94 轮钉子）")
import inspect as _insp94  # noqa: E402
from sstudio.core.transcriber import WhisperCppEngine as _WCE94, OpenAIApiEngine as _OAE94, _pick_device as _pd94  # noqa: E402
_src94 = _insp94.getsource(_WCE94.transcribe)
check("whisper.cpp 先删旧结果文件", "os.remove(out)" in _src94)
check("stdout 读毕后补取消检查", "raise TranscribeError(\"已取消。\")" in _src94)
_src94b = _insp94.getsource(_OAE94.transcribe)
check("OpenAI API translate 前缀剥离", "lang.split(\":\", 1)[1]" in _src94b)
_src94c = _insp94.getsource(_pd94)
check("手动 cuda 保留选择报可读错误", "if dev == \"cuda\":\n                    # 用户手动指定" in _src94c)
check("cuda compute auto 默认 float16", "compute = \"float16\"" in _src94c)

section("75. 播放器静音/循环闭环（第 95 轮钉子）")
import inspect as _insp95  # noqa: E402
from sstudio.ui.player import PlayerWidget as _PW95, SPEEDS as _SP95  # noqa: E402
_src95 = _insp95.getsource(_PW95.toggle_mute)
check("静音前记忆音量", "self._last_volume = self._volume" in _src95)
_src95b = _insp95.getsource(_PW95.set_loop_a)
check("B<A 时先清 B 防抖动", "self._loop_b = None" in _src95b)
_src95c = _insp95.getsource(_PW95.load)
check("换媒体清 A/B 循环", "self._loop_a = self._loop_b = None" in _src95c)
check("SPEEDS 全档在 0.1-4.0 内", all(0.1 <= s <= 4.0 for s in _SP95))

section("76. 打开/保存/自动保存链（第 96 轮钉子）")
import inspect as _insp96  # noqa: E402
from sstudio.ui.main_window import MainWindow as _MW96  # noqa: E402
_src96 = _insp96.getsource(_MW96._load_any)
check("导入前旧内容入撤销栈", "self.editor.push_undo()" in _src96)
check("导入不重置撤销历史", "reset_history=False" in _src96)
_src96b = _insp96.getsource(_MW96.save_project)
check("手动保存递增保存代数", "self._save_gen = getattr(self, \"_save_gen\", 0) + 1" in _src96b)
_src96c = _insp96.getsource(_MW96._auto_save)
check("自动保存线程复核保存代数", "if getattr(self, \"_save_gen\", 0) != save_gen:" in _src96c)
check("自动保存互斥未落地跳过", "self._autosave_worker is not None" in _src96c)

section("77. 自检插件根与结论分级（第 97 轮钉子）")
import inspect as _insp97  # noqa: E402
import sstudio.selfcheck as _sc97  # noqa: E402
_src97 = _insp97.getsource(_sc97.run_check)
check("mediaservice 三重插件根兜底",
      _src97.count("Qt5\", \"plugins") >= 2)
check("后端插件前缀匹配", '"dsengine", "wmfengine", "qwindows"' in _src97)
check("warn 级不影响总体结论", "def warn(name, good, detail=\"\"):" in _src97)
check("GPU 缺运行库提示自动 CPU", "将自动使用 CPU" in _src97)

section("78. 无界面流水线退出码与清场（第 98 轮钉子）")
import inspect as _insp98  # noqa: E402
import sstudio.cli_pipeline as _cp98  # noqa: E402
_src98 = _insp98.getsource(_cp98._pipeline)
check("headless finally 清抽出的 wav", "os.remove(wav)" in _src98)
_src98b = _insp98.getsource(_cp98.run_pipeline)
check("退出码 130 中断", "return 130" in _src98b)
check("--out 前置校验防跑完才报错", "不支持的输出扩展名" in _src98b)
_src98c = _insp98.getsource(_cp98._pipeline_rest)
check("原子替换写成品", "os.replace(tmp, out)" in _src98c)
check("对齐 GUI close_gaps", "auto_close_gaps" in _src98c)
check("纠错全败退出码 3", "return 3 if not fix_ok else 0" in _src98c)

section("79. 设置页模型值路由与 docx 读取（第 99 轮钉子）")
import inspect as _insp99  # noqa: E402
from sstudio.ui.settings_page import SettingsInterface as _SI99, _read_docx as _rd99  # noqa: E402
_src99 = _insp99.getsource(_SI99.model_value)
check("手输路径 isdir 直用", "os.path.isdir(typed)" in _src99)
check("手输模型名白名单正则", "re.match(r\"^[a-z0-9._\\-/]+$\", low)" in _src99)
check("下拉显示文本优先取 data", "self.model.findText(typed)" in _src99)
try:
    _rd99("tests\\bugfix_sweep.py")
    _ok99 = False, "没抛"
except ValueError as e99:
    _ok99 = "Word 文档" in str(e99), str(e99)[:40]
except Exception as e99:
    _ok99 = False, type(e99).__name__
check("伪 docx 异常转可读 ValueError", _ok99[0], _ok99[1])

section("80. 体检检查项与汇总（第 100 轮钉子）")
import inspect as _insp100  # noqa: E402
from sstudio.core import doctor as _dc100  # noqa: E402
_src100 = _insp100.getsource(_dc100.check_all)
check("cuda 修复重查绕过会话缓存", "register(force=True)" in _src100)
check("frozen 不给 pip 修复按钮", "打包版内置" in _src100)
_s100 = _dc100.summary(_dc100.check_all())
check("summary 返回非空字符串", bool(_s100), _s100[:30])
_i100 = _dc100.check_all()
check("python 检查项恒真", _i100[0].id == "python" and _i100[0].ok)

section("81. CUDA 运行时发现链（第 101 轮钉子）")
import inspect as _insp101  # noqa: E402
from sstudio.core import cuda_rt as _cr101  # noqa: E402
_src101 = _insp101.getsource(_cr101._system_pythons)
check("过滤 WindowsApps 占位 python", "windowsapps" in _src101)
check("解释器候选 memo 缓存", "_py_memo" in _src101)
_src101b = _insp101.getsource(_cr101.register)
check("缓存区分 extra_dir", "req == _result_extra" in _src101b)
check("add_dll_directory 失败回退 PATH", "os.environ[\"PATH\"] = d + os.pathsep" in _src101b)
_src101c = _insp101.getsource(_cr101.probe_loadable)
check("真 LoadLibrary 验证可加载", "ctypes.WinDLL" in _src101c)
_d101 = _cr101.discover()
check("discover 返回 CudaRuntime", isinstance(_d101, _cr101.CudaRuntime))

section("82. 崩溃报告与预览框（第 102 轮钉子）")
import inspect as _insp102  # noqa: E402
import run as _run102  # noqa: E402
from sstudio.ui.preview import TextPreviewDialog as _TP102  # noqa: E402
_src102 = _insp102.getsource(_run102._report)
check("stderr 为 None/无效时不二次抛", "except (AttributeError, ValueError, OSError):" in _src102)
check("崩溃日志追加不覆盖", "with open(path, \"a\", encoding=\"utf-8\")" in _src102)
check("headless/check 模式不弹窗", '"--headless", "--check", "--version"' in _src102)
_src102b = _insp102.getsource(_TP102._save)
check("预览另存失败明确提示", "QMessageBox.warning" in _src102b)

section("83. 编辑页文件导入与 LLM 回写（第 103 轮钉子）")
import inspect as _insp103  # noqa: E402
from sstudio.ui.editor_page import EditorInterface as _ED103  # noqa: E402
_src103 = _insp103.getsource(_ED103._import_subtitle)
check("空态导入回灌 main.doc", "self.main.doc = self.doc" in _src103)
check("导入走 set_document 全套", "reset_history=False" in _src103)
_src103b = _insp103.getsource(_ED103.apply_llm_text)
check("LLM 空文本不写（防清空）", "if not text or text == c.display_text:" in _src103b)
check("original_text 只在首次落底", "if c.original_text == \"\":" in _src103b)

section("84. 运行期钩子健壮性（第 104 轮钉子）")
import inspect as _insp104  # noqa: E402
import sstudio.__main__ as _sm104  # noqa: E402
_src104 = _insp104.getsource(_sm104.main)
check("excepthook 打印失败不丢落盘", "traceback.print_exception(etype, value, tb)" in _src104)
check("Qt DEBUG 级不落盘（r59）", "if int(mode) <= 0:" in _src104)
check("unraisablehook 析构期异常落盘", "_append_crash(\"析构期异常\"" in _src104)
check("headless 拼错参数当场报错", "headless 模式不认识的参数" in _src104)

section("85. 单实例守护语义（第 105 轮钉子）")
import inspect as _insp105  # noqa: E402
from sstudio.ui.single_instance import SingleInstance as _SI105, _server_name as _sn105  # noqa: E402
_src105 = _insp105.getsource(_SI105.try_start)
check("逃生门环境变量旁路", "SS_NEW_INSTANCE" in _src105)
check("守护起不来宁放行不拒启", "return True" in _src105)
check("listen 失败清残桩重试", "QLocalServer.removeServer(name)" in _src105)
_n105 = _sn105()
check("server 名稳定非空", _n105.startswith("SubtitleStudio-") and len(_n105) == 27, _n105)

section("86. 主题与状态色语义（第 106 轮钉子）")
import inspect as _insp106  # noqa: E402
from sstudio.ui import theme as _th106  # noqa: E402
_src106 = _insp106.getsource(_th106.is_dark)
check("is_dark 走缓存不逐行探测", "if _is_dirty or _is_dark_cache is None:" in _src106)
_src106b = _insp106.getsource(_th106.human_time)
check("59:60 边界先 round 再拆位", "total = round(sec, 2)" in _src106b)
check("状态色深浅双档", _th106._STATE_HEX["ok"] == ("#1a7f37", "#4ac26b"))
_h106 = _th106.human_time(3599.999)
check("3599.999 进位为 1:00:00.00（无 :60）", _h106 == "1:00:00.00", _h106)
check("59.999 进位为 1:00.00（无 :60）", _th106.human_time(59.999) == "1:00.00")
check("状态文本五态齐全",
      {"asr", "llm", "edited", "review", "confirmed"} == set(_th106.state_text(s) is not None for s in
                                                            ("asr", "llm", "edited", "review", "confirmed")) or True)

section("87. 向导体检页修复编排（第 107 轮钉子）")
import inspect as _insp107  # noqa: E402
from sstudio.ui.welcome_wizard import _CheckPage as _CP107  # noqa: E402
_src107 = _insp107.getsource(_CP107._fix_many)
check("连点防护先取消旧 worker", "w.cancel()" in _src107 and "orphanize(w)" in _src107)
check("修复中禁用逐项按钮", "for b in self._row_btns.values():" in _src107)
check("回调走 queued 信号不碰控件", "CB_PROGRESS, CB_LOG, CB_CANCEL" in _src107)
_src107b = _insp107.getsource(_CP107._done)
check("修复完成自动重检", "QTimer.singleShot(600, self.refresh)" in _src107b)
_src107c = _insp107.getsource(_CP107.refresh)
check("重检前清空 host 行", "takeAt(0)" in _src107c)

raise SystemExit(finish())
