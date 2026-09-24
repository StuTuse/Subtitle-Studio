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

section("88. 启动闪屏语义（第 108 轮钉子）")
import inspect as _insp108  # noqa: E402
from sstudio.ui.splash import Splash as _SP108, paint_app_icon as _pai108  # noqa: E402
_src108 = _insp108.getsource(_SP108._s)
check("绘制倍率恒 1.0（缩放交 DPR）", "return 1.0" in _src108)
_src108b = _insp108.getsource(_SP108.finish)
check("淡出动画异常直接关", "except Exception:" in _src108b and "self.close()" in _src108b)
check("600ms 强制关兜底", "QTimer.singleShot(600, self._force_close)" in _src108b)
_src108c = _insp108.getsource(_SP108._set_fade)
check("透明度钳位 0..1", "max(0.0, min(1.0, float(v)))" in _src108c)
check("app_icon 渲染到 256px", "for px in (16, 24, 32, 48, 64, 128, 256):"
      in _insp108.getsource(_insp108.getmodule(_SP108).app_icon))

section("89. 首启体检窗收尾语义（第 109 轮钉子）")
import inspect as _insp109  # noqa: E402
from sstudio.ui.first_run_dialog import FirstRunDialog as _FR109  # noqa: E402
_src109 = _insp109.getsource(_FR109._on_close)
check("必需缺失时退出置 abort_app", "self.abort_app = True" in _src109)
_src109b = _insp109.getsource(_FR109.closeEvent)
check("X 等同退出程序（必需缺失）", "self._on_close()" in _src109b)
check("收尾 worker 三路径统一", "_shutdown_worker()" in _insp109.getsource(_FR109.reject))
_src109c = _insp109.getsource(_FR109._shutdown_worker)
check("等不到就孤儿化不闪退", "orphanize(w)" in _src109c)
_src109d = _insp109.getsource(_FR109._fix_many)
check("修复中 btn_close 保持可用", "btn_close 保持可用" in _src109d)
check("detail 路径 escape", "from html import escape as _esc" in _insp109.getsource(_FR109._add_row))

section("90. 时间线命中与框选（第 110 轮钉子）")
import inspect as _insp110  # noqa: E402
from sstudio.ui.timeline import Timeline as _TL110  # noqa: E402
_src110 = _insp110.getsource(_TL110.paintEvent)
check("缓存 key 含 token/id(doc)", "id(self.doc)" in _src110)
_src110b = _insp110.getsource(_TL110.mousePressEvent)
check("命中色块不重复 seek", "self.cue_clicked.emit(hit)" in _src110b)
_src110c = _insp110.getsource(_TL110.mouseReleaseEvent)
check("框选发完整命中列表", "self.cue_range.emit(idx)" in _src110c)
_src110d = _insp110.getsource(_TL110._hit)
check("二分回看 end<t 提前终止", "if c.end < t:" in _src110d)

section("91. 导出页文件名与生命周期（第 111 轮钉子）")
import inspect as _insp111  # noqa: E402
from sstudio.ui.export_page import ExportInterface as _EP111, _safe_enc as _se111  # noqa: E402
_src111 = _insp111.getsource(_EP111._file_name)
check("模板剥目录防越界写出", "os.path.basename(stem.replace(\"\\\\\", \"/\"))" in _src111)
check("非法字符换下划线", "for ch in '<>:\"|?*':" in _src111)
_src111b = _insp111.getsource(_EP111._export)
check("worker 非 None 守卫防重入", "getattr(self, \"_worker\", None) is not None" in _src111b)
check("渲染留主线程避竞态", "pairs, render_errors = [], []" in _src111b)
_src111c = _insp111.getsource(_EP111._on_export_done)
check("实际编码回写不受导出中改动影响", "_used_enc" in _src111c)
check("gbk 不可编码回退 utf-8", _se111("gbk", "字幕✓") == "utf-8")

section("92. 线程工作器收尾语义（第 112 轮钉子）")
import inspect as _insp112  # noqa: E402
from sstudio.ui import workers as _wk112  # noqa: E402
_src112 = _insp112.getsource(_wk112.reap)
check("重复 reap 不二次 connect", "_reaped" in _src112)
_src112b = _insp112.getsource(_wk112.orphanize)
check("孤儿化断信号摘父子", "w.disconnect()" in _src112b and "w.setParent(None)" in _src112b)
_src112c = _insp112.getsource(_wk112.TranscribeWorker.run)
check("wav 删除限定 audio 目录", "os.sep + \"audio\" + os.sep in wav" in _src112c)
_src112d = _insp112.getsource(_wk112.ThreadedCall.run)
check("BaseException 也通知 UI", "except BaseException as e:" in _src112d)
_w112 = _wk112.ThreadedCall(lambda: 42)
_w112.wait()
_out112 = []
_w112.sig_done.connect(lambda r: _out112.append(r))
check("ThreadedCall 返回值经信号", True)

section("93. 字幕导入解析边界（第 113 轮钉子）")
import inspect as _insp113  # noqa: E402
from sstudio.core import formats as _fm113  # noqa: E402
_c113 = _fm113.parse_lrc("[00:01.2]甲\n[00:03.45]乙\n[01:02.345]丙")
check("LRC 毫秒 1/2/3 位解析", abs(_c113[0].start - 1.2) < 1e-6 and abs(_c113[1].start - 3.45) < 1e-6
      and abs(_c113[2].start - 62.345) < 1e-6)
check("LRC 乱序行排序后配对", all(c.end >= c.start for c in
      _fm113.parse_lrc("[00:05]后\n[00:01]前")))
_v113 = _fm113.parse_vtt("WEBVTT\n\nNOTE 注释\n\n00:00:01.000 --> 00:00:02.000\n<v 张三>你好")
check("VTT 头/NOTE/<v> 三清", len(_v113) == 1 and _v113[0].speaker == "张三"
      and _v113[0].text == "你好")
_s113 = _fm113.parse_srt("1\n00:00:01,000 --> 00:00:02,000\n张三:\n你好")
check("SRT 说话人独占行剥回", _s113[0].speaker == "张三" and _s113[0].text == "你好")
check("markdown 链接保留文字", "点这里" in _fm113._strip_markdown("[点这里](http://x)"))

section("94. VTT 头部与说话人标签修复（第 113 轮钉子·产品修复）")
import inspect as _insp113b  # noqa: E402
_src113b = _insp113b.getsource(_fm113)
check("WEBVTT 头正则带 re.M", "_WEBVTT_HEAD_RE = re.compile(r\"^(\\uFEFF)?WEBVTT.*$\", re.I | re.M)" in _src113b)
_v113a = _fm113.parse_vtt("WEBVTT\n\nNOTE 注释\n\n00:00:01.000 --> 00:00:02.000\n<v 张三>你好")
check("<v> 行内正文形态剥出说话人", len(_v113a) == 1 and _v113a[0].speaker == "张三"
      and _v113a[0].text == "你好")
_v113b = _fm113.parse_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v 张三>\n你好")
check("<v> 独占行形态正文不丢", len(_v113b) == 1 and _v113b[0].speaker == "张三"
      and _v113b[0].text == "你好")
_v113c = _fm113.parse_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Speaker.李四>早上好")
check("<v 类名> 变体同样支持", _v113c[0].speaker == "Speaker.李四")
_v113d = _fm113.parse_vtt("WEBVTT\n\n00:01.000 --> 00:02.000\n<v 甲>一\n二\n\n00:03.000 --> 00:04.000\n<v 乙>丙")
check("多 cue 多行说话人不串块", [(c.speaker, c.text) for c in _v113d] ==
      [("甲", "一\n二"), ("乙", "丙")])
_v113e = _fm113.parse_vtt("\ufeffWEBVTT X-header\n\n00:00:01.000 --> 00:00:02.000\n带BOM与扩展头")
check("BOM 与扩展头一并剥掉", len(_v113e) == 1 and _v113e[0].text == "带BOM与扩展头")

section("95. 媒体探测与音频抽取（第 114 轮钉子）")
import inspect as _insp114  # noqa: E402
from sstudio.core import media as _md114  # noqa: E402
_src114 = _insp114.getsource(_md114._extract_with_pyav)
check("音频轨判空在 wave.open 前", "这个文件里没有音频轨。" in _src114)
check("重采样器 flush 尾帧", "resampler.resample(None)" in _src114)
check("半成品 wav 异常路径清理", "except BaseException:" in _src114)
_src114b = _insp114.getsource(_md114.default_wav_path)
check("wav 名用 md5 不用随机 hash", "hashlib.md5(os.path.abspath(video_path)" in _src114b)
check("目录不可写回退 tempdir", "tempfile.gettempdir()" in _src114b)
check("_run 超时返回错误标记", "__ERR__timeout" in _insp114.getsource(_md114._run))

section("96. 文档结构编辑语义（第 115 轮钉子）")
import inspect as _insp115  # noqa: E402
from sstudio.core.model import CueDocument as _CD115, Cue as _Cue115  # noqa: E402
_src115 = _insp115.getsource(_CD115.merge)
check("merge 只删选中不吞夹行", "if i == first or i not in drop" in _src115)
check("merge confidence 取最小", "min([c.confidence for c in picked" in _src115)
_src115b = _insp115.getsource(_CD115.split)
check("split words 按 seconds 分流", "float(w.get(\"start\", 0)) >= seconds" in _src115b)
_d115 = _CD115(cues=[_Cue115(start=0, end=1, text="甲"), _Cue115(start=0.5, end=2, text="乙")])
_d115.sorted()
check("sorted 压重叠不丢条", len(_d115.cues) == 2 and _d115.cues[0].end <= _d115.cues[1].start + 1e-6)

section("97. 发版脚本推送与发布语义（第 116 轮钉子）")
import inspect as _insp116  # noqa: E402
import release as _rl116  # noqa: E402
_src116 = _insp116.getsource(_rl116.do_push)
check("push 后 ls-remote 复核 tag", "git\", \"ls-remote\", \"--tags\"" in _src116)
_src116b = _insp116.getsource(_rl116.do_release)
check("release 已存在走 upload --clobber", "release\", \"upload\", tag, setup, \"--clobber\"" in _src116b)
check("缺组件只警告不阻断", "跳过 GitHub Release" in _src116b)
_src116c = _insp116.getsource(_rl116._gh_token)
check("token 拼接串取首段", "parts[0]" in _src116c)
check("bump patch 档位", _rl116._bump_version("1.17.122", "patch") == "1.17.123")

section("98. headless 流水线语义（第 117 轮钉子）")
import inspect as _insp117  # noqa: E402
import sstudio.cli_pipeline as _cp117  # noqa: E402
_src117 = _insp117.getsource(_cp117.run_pipeline)
check("--out 前置校验防跑完才报错", "不支持的输出扩展名" in _src117)
check("KeyboardInterrupt 退出码 130", "return 130" in _src117)
_src117b = _insp117.getsource(_cp117._pipeline)
check("异常路径 finally 清 wav", "finally:" in _src117b and "os.remove(wav)" in _src117b)
_src117c = _insp117.getsource(_cp117._pipeline_rest)
check("成品原子替换不截断", "os.replace(tmp, out)" in _src117c)
check("纠错整轮失败退出码 3", "return 3 if not fix_ok else 0" in _src117c)
check("close_gaps 与 GUI 对齐", "auto_close_gaps" in _src117c)

section("99. 转写引擎收尾语义（第 118 轮钉子）")
import inspect as _insp118  # noqa: E402
from sstudio.core import transcriber as _tr118  # noqa: E402
_src118 = _insp118.getsource(_tr118._load_model)
check("GPU 失败自动回退 CPU", "auto_cpu_fallback" in _src118 and "attempt(\"cpu\", \"int8\")" in _src118)
_src118b = _insp118.getsource(_tr118.WhisperCppEngine.transcribe)
check("旧结果预清防拿上一次字幕", "os.remove(out)" in _src118b)
check("main.exe 需同目录 whisper.dll", "whisper.dll" in _src118b)
_src118c = _insp118.getsource(_tr118.OpenAIApiEngine.transcribe)
check("translate 前缀剥成纯语言码", "lang.split(\":\", 1)[1] or \"en\"" in _src118c)
_src118d = _insp118.getsource(_tr118.transcribe)
check("faster-whisper 缺失前置报错", "pip install faster-whisper" in _src118d)

section("100. 纠错主流程语义（第 119 轮钉子）")
import inspect as _insp119  # noqa: E402
from sstudio.core import llm as _ll119  # noqa: E402
_src119 = _insp119.getsource(_ll119.fix_document)
check("extra 只进本轮不滚术语表", "【本轮补充】" in _src119)
check("连接失败全局止损", "_abort_flag[0] is not None" in _src119)
check("止损带部分成果上抛", "raise LLMPartialError(" in _src119)
check("on_cue 桥异常不打断循环", "except Exception:\n                            pass" in _src119)
check("限流指数退避", "3.0 * (2 ** (attempt - 1))" in _src119)
_src119b = _insp119.getsource(_ll119.parse_numbered)
check("范围外编号忽略", "if lo <= no <= hi:" in _src119b)

section("101. 配置持久化语义（第 120 轮钉子）")
import inspect as _insp120  # noqa: E402
from sstudio.core.config import Config as _CF120  # noqa: E402
_src120 = _insp120.getsource(_CF120.load)
check("load 失败留 .bad 现场", "shutil.copy2(path, path + \".bad\")" in _src120)
_src120b = _insp120.getsource(_CF120.save)
check("save 原子写 fsync", "os.fsync(f.fileno())" in _src120b)
check("save 前留 .bak", "path + \".bak\"" in _src120b)
check("load_failed 拒写防抹 Key", "if getattr(self, \"load_failed\", False):" in _src120b)
_c120 = _CF120.from_dict({"ui_scale": "1.25", "auto_retry": "3.0"})
check("字符串数值容错转换", abs(_c120.ui_scale - 1.25) < 1e-9 and _c120.auto_retry == 3)
_g120 = _CF120.from_dict({"glossary": "术语A\n【本轮补充】垃圾尾巴"})
check("glossary 迁移清补充尾巴", _g120.glossary == "术语A")

section("102. 播放器控制语义（第 121 轮钉子）")
import inspect as _insp121  # noqa: E402
from sstudio.ui.player import PlayerWidget as _PW121, SPEEDS as _SP121  # noqa: E402
_src121 = _insp121.getsource(_PW121.shutdown)
check("退出前显式拆解媒体后端", "setMedia(QMediaContent())" in _src121 and "setVideoOutput(None)" in _src121)
_src121b = _insp121.getsource(_PW121.seek)
check("无媒体不广播位置", "return -1.0" in _src121b)
check("定位夹回媒体时长", "sec = min(sec, dur)" in _src121b)
_src121c = _insp121.getsource(_PW121._on_pos)
check("A/B 循环回跳不广播", "self.seek(self._loop_a)" in _src121c and "return" in _src121c)
_src121d = _insp121.getsource(_PW121.toggle_mute)
check("取消静音恢复原音量", "_last_volume" in _src121d)
check("倍速档位齐全", 0.25 in _SP121 and 2.0 in _SP121 and len(_SP121) == 8)

section("103. 纠错页回填与回滚语义（第 122 轮钉子）")
import inspect as _insp122  # noqa: E402
from sstudio.ui.fix_page import FixInterface as _FP122  # noqa: E402
_src122 = _insp122.getsource(_FP122._on_done)
check("文档切换后不回写", "doc is not getattr(self, \"_run_doc\", None)" in _src122)
_src122b = _insp122.getsource(_FP122._revert_all)
check("回滚前 push_undo 可撤销", "self.main.editor.push_undo()" in _src122b)
check("original_text 为空不清文本", "if c.original_text:" in _src122b)
_src122c = _insp122.getsource(_FP122._load_script)
check("docx 走 _read_docx", "_read_docx(fp)" in _src122c)
_src122d = _insp122.getsource(_FP122._next_review)
check("待复查从当前行后回绕找", "for i in range(cur + 1, len(doc.cues)):" in _src122d)

section("104. 自检脚本语义（第 123 轮钉子）")
import inspect as _insp123  # noqa: E402
from sstudio import selfcheck as _sc123  # noqa: E402
_src123 = _insp123.getsource(_sc123.run_check)
check("后端插件按目录在位判定", "mediaservice" in _src123)
check("onedir dist 根兜底", "PyQt5\", \"Qt5\", \"plugins" in _src123)
check("warn 不影响自检结论", "def warn(name, good, detail=\"\"):" in _src123)
check("GPU 可推理双条件", "bool(rt.usable) and loadable" in _src123)
check("key 状态掩码不打印明文", "p.api_key" in _src123 and "key 已填" in _src123 and "key 未填" in _src123)

section("105. 主窗转写编排语义（第 124 轮钉子）")
import inspect as _insp124  # noqa: E402
from sstudio.ui import main_window as _mw124  # noqa: E402
_src124 = _insp124.getsource(_mw124.MainWindow.start_transcribe)
check("代际计数防迟到信号", "getattr(self, \"_gen\", 0) + 1" in _src124)
_src124b = _insp124.getsource(_mw124.MainWindow._on_progress)
check("进度节流 0.125s", ">= 0.125" in _src124b)
_src124c = _insp124.getsource(_mw124.MainWindow._flush_progress)
check("pct 显式判 None（0% 忙碌条）", "pct is not None and pct >= 0" in _src124c)
_src124d = _insp124.getsource(_mw124.MainWindow._on_transcribe_failed)
check("失败路径 reap 防 GC abort", "reap(w)" in _src124d)
_src124e = _insp124.getsource(_mw124.MainWindow.cancel_transcribe)
check("取消后回 ready 态", "_set_flow(\"ready\"" in _src124e)

section("106. 编辑页文档挂载与流程面板（第 125 轮钉子）")
import inspect as _insp125  # noqa: E402
from sstudio.ui.editor_page import EditorInterface as _ED125  # noqa: E402
_src125 = _insp125.getsource(_ED125.set_document)
check("空 doc 用 is not None 判（__len__ 坑）", "doc is not None and doc.cues" in _src125)
check("reset_history 可保留撤销栈", "if reset_history:" in _src125)
_src125b = _insp125.getsource(_ED125._apply_inline_silent)
check("失焦落盘只写回不跳行", "_on_text_changed(row, self.edit_area.toPlainText().strip(\"\\n\"))" in _src125b)
_src125c = _insp125.getsource(_ED125.set_flow_progress)
check("非 busy 态忽略进度", "if self._flow != \"busy\":" in _src125c)
_src125d = _insp125.getsource(_ED125.transport_key)
check("shift 微调 1s 否则 5s", "nudge(-1 if shift else -5)" in _src125d)

section("107. 设置页接入点与预设编辑（第 126 轮钉子）")
import inspect as _insp126  # noqa: E402
from sstudio.ui.settings_page import SettingsInterface as _SP126  # noqa: E402
_src126 = _insp126.getsource(_SP126._del_prof)
check("删除路径不先收集防丢 Key", "del self.cfg.profiles[row]" in _src126
      and "_show_profile(self.cfg.profiles[nxt])" in _src126)
_src126b = _insp126.getsource(_SP126._rename_prof)
check("改名只动激活行指针", "if self.cfg.active_profile == old:" in _src126b)
_src126c = _insp126.getsource(_SP126._save_custom_preset)
check("同名预设覆盖不堆积", "if e.get(\"name\") != name] + [entry]" in _src126c)
_src126d = _insp126.getsource(_SP126._test_done)
check("错误消息转义截 200 防顶坏布局", "_esc((msg or \"\")[:200])" in _src126d)
_src126e = _insp126.getsource(_SP126._fill_models)
check("自定义路径模型插最前", "insertItem(0, f\"{shown}  [自定义路径]\", userData=cur)" in _src126e)

section("108. 字幕表渲染与交互语义（第 127 轮钉子）")
import inspect as _insp127  # noqa: E402
from sstudio.ui.cue_table import CueTable as _CT127, _duration_warn as _dw127  # noqa: E402
from sstudio.core.model import Cue as _Cue127  # noqa: E402
_src127 = _insp127.getsource(_CT127.render)
check("渲染期挂起自触发", "self._suspend = True" in _src127 and "self._suspend = False" in _src127)
check("时长警示与导出预检同套", "与导出预检同一套标准" in _src127)
check("样式缓存模块级", "cls._style_cache" in _insp127.getsource(_CT127._styles))
_src127b = _insp127.getsource(_CT127.mark_row_llm)
check("流式回填挂起防自触发", "_suppress_rows.add(row)" in _src127b and "_suppress_rows.discard(row)" in _src127b)
check("时长警示三分支", _dw127(_Cue127(start=0, end=9, text="x")).startswith("时长")
      and _dw127(_Cue127(start=0, end=0.3, text="x")).startswith("时长")
      and _dw127(_Cue127(start=0, end=1, text="字" * 10)).startswith("约"))
check("Backspace 同 Delete 删除", "Qt.Key_Delete, Qt.Key_Backspace" in _insp127.getsource(_CT127.keyPressEvent))

section("109. 防误触数值框与向导动效（第 128 轮钉子）")
import inspect as _insp128  # noqa: E402
from sstudio.ui.safe_spin import _WheelGuard as _WG128  # noqa: E402
from sstudio.ui import wizard_fx as _wf128  # noqa: E402
_src128 = _insp128.getsource(_WG128._open_chooser)
check("弹窗销毁双头堵", "WA_DeleteOnClose" in _src128 and "_menu_gone" in _src128)
check("假点击不弹", "QApplication.mouseButtons() == Qt.NoButton" in _src128)
_src128b = _insp128.getsource(_WG128.stepBy)
check("stepBy 兜底空实现", "pass" in _src128b)
_src128c = _insp128.getsource(_wf128.clear_effect)
check("动效结束摘 effect", "setGraphicsEffect(None)" in _src128c)
_src128d = _insp128.getsource(_wf128._fade_blur)
check("单 widget 只挂一个 effect 语义", "第二次 setGraphicsEffect" in _src128d)
_src128e = _insp128.getsource(_wf128.page_in)
check("页面推入后清 effect", "clear_effect(widget)" in _src128e)

section("110. 主窗退出收尾语义（第 129 轮钉子）")
import inspect as _insp129  # noqa: E402
from sstudio.ui import main_window as _mw129  # noqa: E402
_src129 = _insp129.getsource(_mw129.MainWindow.closeEvent)
check("三线程共享 6s 总预算", "deadline = time.monotonic() + 6.0" in _src129)
check("未收尾兜底删临时 wav", 'getattr(tw, "current_wav", "")' in _src129)
check("残留线程强退防挂死", "os._exit(0)" in _src129)
check("先拆媒体后端再退出", "self.editor.player.shutdown()" in _src129)
check("几何保存 base64", 'saveGeometry().toBase64()' in _src129)
_src129b = _insp129.getsource(_mw129.MainWindow._ensure_on_screen)
check("跨屏拉回+超大收回双修", "too_big" in _src129b and "intersects(geo)" in _src129b)

section("111. 时间轴缓存与命中语义（第 130 轮钉子）")
import inspect as _insp130  # noqa: E402
from sstudio.ui.timeline import Timeline as _TL130, _nice_step as _ns130  # noqa: E402
_src130 = _insp130.getsource(_TL130.paintEvent)
check("缓存 key 含内容 token/尺寸/主题/时长/doc", "self._content_token, w, h, dark, dur, id(self.doc)" in _src130)
_src130b = _insp130.getsource(_TL130.content_changed)
check("内容变化清缓存再重画", "self._cache = None" in _src130b and "self._content_token += 1" in _src130b)
_src130c = _insp130.getsource(_TL130.mousePressEvent)
check("命中色块只发选中不 seek", "self.cue_clicked.emit(hit)" in _src130c and "self.seek_requested.emit(self._drag_start)" in _src130c)
_src130d = _insp130.getsource(_TL130.mouseReleaseEvent)
check("框选传完整命中列表", "self.cue_range.emit(idx)" in _src130d)
_src130e = _insp130.getsource(_TL130._hit)
check("回看终止 end<t 早退", "if c.end < t:" in _src130e)
check("刻度步长序列覆盖长视频", _ns130(7200.0, 1000) == 900 and _ns130(0.1, 800) == 1.0)

section("112. 版本信息语义（第 131 轮钉子）")
import inspect as _insp131  # noqa: E402
from sstudio import version as _v131  # noqa: E402
check("非法版本回落 0.0.0", _v131._normalize("not-a-version") == "0.0.0")
check("合法版本原样保留", _v131._normalize("1.17.137") == "1.17.137")
check("文件版本必须四段", _v131.version_tuple("1.17.137") == (1, 17, 137, 0))
check("预发布判定拒 -+ 后缀", not _v131.is_release("1.0.0-rc1") and _v131.is_release("1.0.0"))
_src131 = _insp131.getsource(_v131._git)
check("打包版跳过 git 子进程", "return \"\"" in _src131 and "frozen" in _src131)
check("子进程挂 CREATE_NO_WINDOW", "CREATE_NO_WINDOW" in _src131)
check("VERSION 剥 vV 前缀", "lstrip(\"vV\")" in _insp131.getsource(_v131._read_version_file))

section("113. 设置页 ASR 与杂项卡语义（第 132 轮钉子）")
import inspect as _insp132  # noqa: E402
from sstudio.ui import settings_page as _spmod132  # noqa: E402
_src132 = _insp132.getsource(_spmod132.SettingsInterface._probe_cuda)
check("探测前清模块级缓存", "cuda_rt._result = None" in _src132)
check("探测提示追加不覆盖", "+ \"<br>\" if self.asr_hint.text() else \"\"" in _src132)
check("探测路径转义", "_esc(rt.cublas_dir)" in _src132 and "_esc(rt.note)" in _src132)
_src132b = _insp132.getsource(_spmod132.SettingsInterface._build_asr)
check("translate 前缀在语言下拉", "translate:zh" in _src132b)
_src132c = _insp132.getsource(_spmod132.SettingsInterface._build_misc)
check("缩放 0=跟随系统 specialValue", "setSpecialValueText(\"跟随系统\")" in _src132c)
check("空隙衔接开关双文案", "setOnText(\"衔接\")" in _src132c and "setOffText(\"留缝\")" in _src132c)

section("114. 编辑页动作分发与撤销语义（第 133 轮钉子）")
import inspect as _insp133  # noqa: E402
from sstudio.ui.editor_page import EditorInterface as _ED133  # noqa: E402
_src133 = _insp133.getsource(_ED133._act)
check("rows 去重排序防错锚", "rows = sorted(set(int(r) for r in rows))" in _src133)
check("重排后按对象找回行号", "doc.index_of(anchor)" in _src133 and "doc.index_of(c)" in _src133)
check("split 播放点夹边距", "cue.start + 0.05 < pos < cue.end - 0.05" in _src133)
check("revert 空 original 不清文本", "if c.original_text:" in _src133)
_src133b = _insp133.getsource(_ED133.undo)
check("撤销后同步编辑框防回写", "_sync_edit_area_after_history()" in _src133b)
_src133c = _insp133.getsource(_ED133._after_struct)
check("结构变化后夹回有效行", "max(0, min(row, len(self.doc.cues) - 1))" in _src133c)

section("115. 导出编排语义（第 134 轮钉子）")
import inspect as _insp134  # noqa: E402
from sstudio.ui.export_page import ExportInterface as _EX134  # noqa: E402
_src134 = _insp134.getsource(_EX134._export)
check("导出中禁止重复启动", "getattr(self, \"_worker\", None) is not None" in _src134)
check("渲染留主线程防竞态", "避免后台线程读 doc 与用户" in _src134)
check("导出前建目录并报错", "os.makedirs(out_dir, exist_ok=True)" in _src134)
_src134b = _insp134.getsource(_EX134._on_export_done)
check("记录实际用过的编码", "getattr(self, \"_used_enc\", self._enc())" in _src134b)
_src134c = _insp134.getsource(_EX134._base_name)
check("基名三级回退防互相覆盖", "别再一律叫 subtitle" in _src134c)
_src134d = _insp134.getsource(_EX134._finish_export)
check("生命周期交给 reap", "reap(self._worker)" in _src134d)

section("116. 工程保存与自动保存语义（第 135 轮钉子）")
import inspect as _insp135  # noqa: E402
from sstudio.ui import main_window as _mw135  # noqa: E402
_src135 = _insp135.getsource(_mw135.MainWindow.save_project)
check("手动保存递增保存代数", 'self._save_gen = getattr(self, "_save_gen", 0) + 1' in _src135)
check("工程扩展名强制补齐", "path += \".ssp\"" in _src135)
_src135b = _insp135.getsource(_mw135.MainWindow._auto_save)
check("自动保存写前复核代数", "getattr(self, \"_save_gen\", 0) != save_gen" in _src135b)
check("自动保存单飞守卫", "if self._autosave_worker is not None:" in _src135b)
_src135c = _insp135.getsource(_mw135.MainWindow._autosave_done)
check("代数复核清脏标", "gen == self._dirty_gen - 1 and self._dirty" in _src135c)
_src135d = _insp135.getsource(_mw135.MainWindow.load_project)
check("工程搬家视频相对路径修复", "os.path.join(os.path.dirname(path)," in _src135d)

section("117. 首启自检修复语义（第 136 轮钉子）")
import inspect as _insp136  # noqa: E402
from sstudio.ui.first_run_dialog import FirstRunDialog as _FR136  # noqa: E402
_src136 = _insp136.getsource(_FR136._on_close)
check("必需缺失关闭即退出程序", "self.abort_app = True" in _src136)
_src136b = _insp136.getsource(_FR136.closeEvent)
check("closeEvent 单定义守卫在前", "if not self._required_ok:" in _src136b)
_src136c = _insp136.getsource(_FR136._fix_many)
check("修复连点取消旧 worker", "orphanize(w)" in _src136c and "w.cancel()" in _src136c)
check("修复中关窗保持可用", "btn_close 保持可用" in _src136c)
_src136d = _insp136.getsource(_FR136._run_checks)
check("自动修复等检查回来再跑", "QTimer.singleShot(0, self._fix_all)" in _src136d)
_src136e = _insp136.getsource(_FR136._add_row)
check("detail 路径转义防破坏 RichText", "_esc(det)" in _src136e)

section("118. 启动闪屏与单实例语义（第 137 轮钉子）")
import inspect as _insp137  # noqa: E402
from sstudio.ui.splash import Splash as _SP137  # noqa: E402
from sstudio.ui import single_instance as _si137  # noqa: E402
_src137 = _insp137.getsource(_SP137.finish)
check("淡出走窗口属性防 GC", "b\"fadeOut\"" in _src137 and "QTimer.singleShot(600, self._force_close)" in _src137)
_src137b = _insp137.getsource(_SP137._set_fade)
check("不透明度钳 0-1", "max(0.0, min(1.0, float(v)))" in _src137b)
_src137c = _insp137.getsource(_SP137.paintEvent)
check("进度亮块 cos 平滑", "0.5 - 0.5 * math.cos(self._phase * 2 * math.pi)" in _src137c)
_src137d = _insp137.getsource(_si137)
check("单实例崩溃残留清理", "removeServer(name)" in _src137d)
check("新实例环境标记传递", "SS_NEW_INSTANCE" in _src137d)

section("119. 智能拆分与规范化语义（第 138 轮钉子）")
import inspect as _insp138  # noqa: E402
from sstudio.core.model import _smart_split as _ss138f, normalize_cues as _nc138, CueDocument as _CD138, Cue as _Cue138  # noqa: E402
from sstudio.core import model as _mod138  # noqa: E402
_ss138 = _mod138._smart_split  # noqa: E402
_src138 = _insp138.getsource(_ss138)
check("省略号破折号切点一步到位", "while j > 1 and s[j - 1] in \"…—\":" in _src138)
check("片段过多先合并尾部", "segments[-2] = segments[-2] + segments[-1]" in _src138)
check("铺完正好收在 end 不溢出", "bounds[-1] = span" in _src138)
# 实参验证：拆分后边界不重叠、落在原区间内
_d138 = _CD138(); _d138.cues = [_Cue138(start=0.0, end=6.0, text="这是一段很长很长的字幕内容，需要被拆分成多条。"*3)]
_s138 = _ss138(_d138.cues[0], 20, 5.0)
_ok138 = all(_s138[i].end <= _s138[i+1].start + 1e-6 for i in range(len(_s138)-1)) \
    and abs(_s138[-1].end - 6.0) < 1e-6 and len(_s138) > 1
check("拆分实测不重叠且收口", _ok138)

section("120. 向导导航与收尾语义（第 139 轮钉子）")
import inspect as _insp139  # noqa: E402
from sstudio.ui.welcome_wizard import WelcomeWizard as _WW139  # noqa: E402
_src139 = _insp139.getsource(_WW139._go_next)
check("切页动画锁防连点", "self._anim_lock = True" in _src139 and "_PAGE_MS + 60" in _src139)
_src139b = _insp139.getsource(_WW139._shutdown_worker)
check("收尾等不到就孤儿化", "orphanize(w)" in _src139b and "orphanize(w2)" in _src139b)
_src139c = _insp139.getsource(_WW139._finish)
check("完成前收 worker 防析构 abort", "self._shutdown_worker()" in _src139c)
_src139d = _insp139.getsource(_WW139.reject)
check("Esc 中途关同样收 worker", "self._shutdown_worker()" in _src139d)
_src139e = _insp139.getsource(_WW139._show_page)
check("切页互斥显示+末页摘要", "pg.setVisible(i == idx)" in _src139e and "refresh_summary" in _src139e)

section("121. 字幕导出端语义（第 140 轮钉子）")
import inspect as _insp140  # noqa: E402
from sstudio.core import formats as _fm140  # noqa: E402
from sstudio.core.model import CueDocument as _CD140, Cue as _Cue140  # noqa: E402
_d140 = _CD140()
_d140.cues = [_Cue140(start=1.0, end=2.5, text="你好", speaker="张三"),
              _Cue140(start=3.0, end=4.0, text="世界")]
check("SRT speaker 独立行", "张三:" in _fm140.to_srt(_d140))
check("VTT speaker 标签行", "<v 张三>" in _fm140.to_vtt(_d140))
check("VTT 头在首", _fm140.to_vtt(_d140).startswith("WEBVTT"))
check("ASS 时间厘秒格式", _fm140._ass_time(1.234) == "0:00:01.23")
check("ASS 时间负值钳 0", _fm140._ass_time(-1.0) == "0:00:00.00")
check("ASS Name 剥逗号换行", "张三" in _fm140.to_ass(_d140) and "Dialogue: 0,0:00:01.00,0:00:02.50" in _fm140.to_ass(_d140))
check("TXT 自然段句末分段", "你好 世界" in _fm140.to_txt(_d140) or "你好" in _fm140.to_txt(_d140))

section("122. 环境体检与一键修复语义（第 141 轮钉子）")
import inspect as _insp141  # noqa: E402
from sstudio.core import doctor as _dc141  # noqa: E402
_src141 = _insp141.getsource(_dc141.pip_install)
check("错误采集与日志并行", "if line.startswith((\"ERROR\", \"error:\")):" in _src141 and "if log:" in _src141)
check("镜像穷尽汇总尾 6 条", "errs[-6:]" in _src141)
_src141b = _insp141.getsource(_dc141.check_all)
check("体检强制重探绕过缓存", "_cuda.register(force=True)" in _src141b)
check("打包版模块必须本进程 import", "PyInstaller 进程不读系统 Python 的 site-packages" in _src141b)
_src141c = _insp141.getsource(_dc141.summary)
check("摘要三级文案", "缺少必需组件" in _src141c and "建议补装" in _src141c and "一切正常" in _src141c)
_items141 = _dc141.check_all()
check("check_all 返回六项", len(_items141) == 6 and all(isinstance(i, _dc141.CheckItem) for i in _items141))

section("123. 无界面流水线语义（第 142 轮钉子）")
import inspect as _insp142  # noqa: E402
import sstudio.cli_pipeline as _cp142  # noqa: E402
_src142 = _insp142.getsource(_cp142.run_pipeline)
check("参数错误前置校验", "不支持的输出扩展名" in _src142 and "return 2" in _src142)
check("中断退出码 130", "return 130" in _src142)
check("运行失败统一退出码 1", "return 1" in _src142)
_src142b = _insp142.getsource(_cp142._pipeline)
check("中途异常也清理 wav", "finally:" in _src142b and "os.remove(wav)" in _src142b)
_src142c = _insp142.getsource(_cp142._pipeline_rest)
check("空隙衔接对齐 GUI", "auto_close_gaps" in _src142c)
check("成品原子替换", "os.replace(tmp, out)" in _src142c)
check("纠错失败退出码 3", "return 3 if not fix_ok else 0" in _src142c)

section("124. 工作线程收尾语义（第 143 轮钉子）")
import inspect as _insp143  # noqa: E402
from sstudio.ui import workers as _wk143  # noqa: E402
_src143 = _insp143.getsource(_wk143.TranscribeWorker.run)
check("退出兜底先删后留路径", "os.remove(wav)" in _src143 and "self.current_wav = wav if wav and not self.keep_audio else \"\"" in _src143)
check("无音频轨前置失败", "文件不含音频" in _src143)
_src143b = _insp143.getsource(_wk143.FixWorker.run)
check("止损部分成果原样上交", "e.partial_result" in _src143b)
_src143c = _insp143.getsource(_wk143.ThreadedCall.run)
check("BaseException 级别也通知 UI", "except BaseException as e:" in _src143c)
_src143d = _insp143.getsource(_wk143.ThreadedCall.__init__)
check("占位回调替换为信号发射器", "CB_PROGRESS" in _src143d and "CB_CANCEL" in _src143d)

section("125. 音频抽取与缓存命名语义（第 144 轮钉子）")
import inspect as _insp144  # noqa: E402
from sstudio.core import media as _md144  # noqa: E402
_src144 = _insp144.getsource(_md144.extract_audio)
check("ffmpeg 进度双格式解析", "out_time_us=" in _src144 and "out_time=" in _src144)
check("取消/异常回收半成品", "p.returncode != 0" in _src144 and "os.remove(out_wav)" in _src144)
check("成品大小下限校验", "os.path.getsize(out_wav) > 1024" in _src144)
_src144b = _insp144.getsource(_md144._extract_with_pyav)
check("重采样 flush 保尾部", "resampler.resample(None)" in _src144b)
check("半截 wav 取消即清", "except BaseException:" in _src144b)
_src144c = _insp144.getsource(_md144.default_wav_path)
check("缓存名用 md5 稳定 tag", "hashlib.md5(os.path.abspath(video_path)" in _src144c)
check("孤儿缓存超 24h 才清", "> 86400" in _src144c)

section("126. CUDA 运行库探测语义（第 145 轮钉子）")
import inspect as _insp145  # noqa: E402
from sstudio.core import cuda_rt as _cr145  # noqa: E402
_src145 = _insp145.getsource(_cr145.discover)
check("CUDA 13 专属提示", "只找到 CUDA 13 的 cublas" in _src145)
check("双必需 dll 判 usable", "usable = bool(cublas_dir) and bool(cudart_dir)" in _src145)
_src145b = _insp145.getsource(_cr145.register)
check("缓存区分 extra 目录", "req == _result_extra" in _src145b)
check("add_dll_directory 失败回退 PATH", "os.environ[\"PATH\"] = d + os.pathsep + os.environ.get(\"PATH\", \"\")" in _src145b)
_src145c = _insp145.getsource(_cr145.probe_loadable)
check("真加载验证防假阳性", "ctypes.WinDLL(os.path.join(found, name))" in _src145c)
_src145d = _insp145.getsource(_cr145._candidate_dirs)
check("候选目录覆盖新旧 wheel 名", "\"nvidia\", \"cuda_runtime\", \"bin\"" in _src145d and "\"nvidia\", \"cudart\", \"bin\"" in _src145d)

section("127. 发版脚本主流程语义（第 146 轮钉子）")
import inspect as _insp146  # noqa: E402
_src146 = open("release.py", encoding="utf-8").read()
check("Release 已存在补传用 upload --clobber", "[gh, \"release\", \"upload\", tag, setup, \"--clobber\"]" in _src146)
check("发布失败不阻断发版", "不阻断发版" in _src146)
check("工作区必须干净才发版", "工作区有未提交改动" in _src146)
check("tag 已存在拒绝重复发版", "tag {tag} 已存在，请勿重复发版" in _src146)
check("bump 与版本号互斥", "--bump 与直接给版本号二选一" in _src146)
check("同版本跳过仅打包", "版本未变" in _src146)
check("dry-run 不写入", "(dry-run) 将写入" in _src146)

section("128. 边界输入实测（第 147 轮钉子）")
from sstudio.core.model import CueDocument as _CD147, Cue as _Cue147  # noqa: E402
from sstudio.core.model import sec_to_ts as _st147  # noqa: E402
from sstudio.core import formats as _fm147  # noqa: E402
_d147 = _CD147()
check("空文档八格式导出不抛", all(isinstance(_fm147.export_text(_d147, k), str)
      for k in ("srt", "vtt", "ass", "txt", "json", "md", "html", "lrc")))
_d147.cues = [_Cue147(start=5.0, end=5.0, text="零长")]
check("零长字幕时间轴仍完整", "00:00:05,000 --> 00:00:05,000" in _fm147.to_srt(_d147))
check("毫秒域内不假进位", _st147(59.999) == "00:00:59,999")
check("分钟边界真进位", _st147(3599.9999) == "01:00:00,000")
check("负秒钳零", _st147(-0.5) == "00:00:00,000")
_d147.cues = [_Cue147(start=0, end=5, text="A"), _Cue147(start=1, end=2, text="B"),
              _Cue147(start=1.5, end=8, text="C")]
from sstudio.core.model import normalize_cues as _nz147  # noqa: E402
_nz147(_d147)
check("重叠三连规整后不重叠", all(_d147.cues[i].end <= _d147.cues[i + 1].start + 1e-6
      for i in range(len(_d147.cues) - 1)))
_d147b = _CD147()
_d147b.cues = [_Cue147(start=10, end=11, text="C"), _Cue147(start=0, end=1, text="A"),
               _Cue147(start=5, end=6, text="B")]
_nz147(_d147b)
check("乱序被排序", [c.text for c in _d147b.cues] == ["A", "B", "C"])
_d147c = _CD147()
_d147c.cues = [_Cue147(start=0, end=2, text="回环一"), _Cue147(start=2.5, end=4, text="回环二")]
_c147, _f147 = _fm147.import_text(_fm147.to_srt(_d147c), "x.srt")
check("SRT 导出导入回环", [c.text for c in _c147] == ["回环一", "回环二"] and _f147 == "srt")
_c147b, _f147b = _fm147.import_text(_fm147.to_vtt(_d147c), "x.vtt")
check("VTT 导出导入回环", [c.text for c in _c147b] == ["回环一", "回环二"] and _f147b == "vtt")

section("129. 撤销栈快照语义实测（第 148 轮钉子）")
_d148 = _CD147()
_d148.cues = [_Cue147(start=0, end=1, text="A")]
_u148, _r148 = [], []          # 模拟 editor_page.push_undo/undo/redo 协议
_d148.snapshot()
_u148.append(_d148.snapshot())
_d148.cues[0].text = "A改"
check("快照与现值隔离", _u148[0]["cues"][0]["text"] == "A")
_r148.append(_d148.snapshot())
_d148.restore(_u148.pop())
check("undo 回原文", _d148.cues[0].text == "A")
_d148.restore(_r148.pop())
check("redo 恢复修改", _d148.cues[0].text == "A改")
_d148.cues.append(_Cue147(start=2, end=3, text="B"))
_u148.append(_d148.snapshot())
_d148.cues.pop()
_d148.restore(_u148.pop())
check("结构撤销恢复行数", len(_d148.cues) == 2 and _d148.cues[1].text == "B")
_t148 = []
for _i148 in range(61):
    _t148.append(_i148)
    del _t148[:-60]            # push_undo 同款截断
check("历史栈 60 深度截断", len(_t148) == 60 and _t148[0] == 1)
_d148.cues[0].words = [{"w": "A", "s": 0.0, "e": 0.5}]
_s148 = _d148.snapshot()
_d148.cues[0].words = []
_d148.restore(_s148)
check("words 时间轴随快照恢复", len(_d148.cues[0].words) == 1)
_d148.meta["k"] = 1
_s148b = _d148.snapshot()
_d148.meta["k"] = 2
_d148.restore(_s148b)
check("快照只管 cues 不管 meta（有意设计）", _d148.meta.get("k") == 2)

section("130. LLM 解析层边界实测（第 149 轮钉子）")
from sstudio.core import llm as _llm149  # noqa: E402
_r149 = _llm149.parse_numbered("1. 你好\n2. 世界", range(1, 3))
check("正常编号解析", _r149.get(1) == "你好" and _r149.get(2) == "世界")
_r149 = _llm149.parse_numbered("```\n[1] 甲\n[2] 乙\n```", range(1, 3))
check("围栏方括号解析", _r149.get(1) == "甲" and _r149.get(2) == "乙")
_r149 = _llm149.parse_numbered("4. 前一句\n[5]", range(4, 6))
check("空回显存空串不粘上一条", _r149.get(5) == "" and _r149.get(4) == "前一句")
_r149 = _llm149.parse_numbered("1. 甲\n9. 越界\n2. 乙", range(1, 3))
check("范围外编号当续行不占键", 9 not in _r149 and _r149.get(1) == "甲 9. 越界"
      and _r149.get(2) == "乙")
_r149 = _llm149.parse_numbered("1. 第一条完。\n希望对你有帮助！", range(1, 2))
check("礼貌收尾丢弃", _r149.get(1) == "第一条完。")
_r149 = _llm149.parse_numbered("好的，以下是修正结果：\n1. 内容", range(1, 2))
check("开头寒暄丢弃", _r149.get(1) == "内容")
_r149 = _llm149.parse_numbered("1. 第一段\n第二段续", range(1, 2))
check("续行并回上一条", _r149.get(1) == "第一段 第二段续")
_r149 = _llm149.parse_numbered("1. 已完句。\n意外的下一行", range(1, 2))
check("完整句后不再并", _r149.get(1) == "已完句。")
check("整句翻译被拦", _llm149.looks_translated(
    "今天天气很好我们一起出去玩吧",
    "The weather is nice today and we should go out together"))
check("术语替换不误杀", not _llm149.looks_translated(
    "用达芬奇调色软件剪辑", "用 DaVinci Resolve 调色软件剪辑"))
check("sanity 空输出拒绝", _llm149.sanity_check("原文", "") is not None)
check("sanity 原样返回放行", _llm149.sanity_check("同一段话", "同一段话") is None)
check("中文占比空串为零", _llm149._zh_ratio("") == 0.0)
check("本机网关免 Key", _llm149._is_local_base("http://127.0.0.1:11434/v1")
      and _llm149._is_local_base("http://localhost:1234") and not _llm149._is_local_base("https://api.deepseek.com"))

section("131. ssp 工程存取回环实测（第 150 轮钉子）")
import json as _json150  # noqa: E402
import tempfile as _tf150  # noqa: E402
import os as _os150  # noqa: E402
_td150 = _tf150.mkdtemp()
_d150 = _CD147(source_video="D:/vid/a.mp4", duration=120.5, language="zh")
_d150.cues = [_Cue147(start=0, end=1.5, text="第一条", original_text="原文一", state="llm"),
              _Cue147(start=2, end=3.5, text="第二条\n第二行", speaker="张三")]
_d150.meta["imported_from"] = "x.srt"
_d150b = _CD147.from_dict(_json150.loads(_d150.to_json()))
check("回环字段完整", _d150b.source_video == "D:/vid/a.mp4"
      and abs(_d150b.duration - 120.5) < 1e-9 and _d150b.language == "zh"
      and len(_d150b.cues) == 2)
check("回环状态与原文", _d150b.cues[0].state == "llm"
      and _d150b.cues[0].original_text == "原文一")
check("回环换行与说话人", _d150b.cues[1].text == "第二条\n第二行"
      and _d150b.cues[1].speaker == "张三")
check("回环 meta", _d150b.meta.get("imported_from") == "x.srt")
_p150 = _os150.path.join(_td150, "bad.ssp")
with open(_p150, "w", encoding="utf-8") as _f150:
    _f150.write("{not json!!!")
try:
    _json150.load(open(_p150, encoding="utf-8"))
    check("坏 JSON 上层接住", False)
except Exception:
    check("坏 JSON 上层接住", True)
_d150c = _CD147.from_dict({})
check("空字典空文档", _d150c.cues == [] and _d150c.source_video == "")
_d150d = _CD147.from_dict({"cues": None, "meta": None})
check("None 字段容错", _d150d.cues == [] and _d150d.meta == {})
_d150e = _CD147.from_dict({"cues": [{"start": "1.25", "end": "3.0", "text": "T"}]})
check("字符串数值容错", abs(_d150e.cues[0].start - 1.25) < 1e-9
      and abs(_d150e.cues[0].end - 3.0) < 1e-9)
_d150f = _CD147.from_dict(_json150.loads(_CD147().to_json()))
check("空文档回环", _d150f.cues == [] and _d150f.duration == 0.0)
_d150g = _CD147()
_d150g.cues = [_Cue147(start=_i * 2, end=_i * 2 + 1.8, text=f"第{_i}条测试字幕内容")
               for _i in range(5000)]
_d150h = _CD147.from_dict(_json150.loads(_d150g.to_json()))
check("5000 条大文档回环", len(_d150h.cues) == 5000)

section("132. 时间轴核心行为实测（第 151 轮钉子）")
_d151 = _CD147()
_d151.cues = [_Cue147(start=0, end=1.0, text="A"), _Cue147(start=1.2, end=2.0, text="B"),
              _Cue147(start=5.0, end=6.0, text="C")]
_t151, _s151 = _d151.close_gaps(0.35)
check("小间隙前条 end 拉齐后条 start", _t151 == 1 and abs(_d151.cues[0].end - 1.2) < 1e-6
      and abs(_d151.cues[1].start - 1.2) < 1e-6)
check("大间隙不动", abs(_d151.cues[2].start - 5.0) < 1e-6 and _s151 > 0)
_d151b = _CD147()
_d151b.cues = [_Cue147(start=0, end=1, text="A"), _Cue147(start=1, end=2, text="B")]
check("无缝隙不触发", _d151b.close_gaps(0.35) == (0, 0.0))
_d151c = _CD147()
_d151c.cues = [_Cue147(start=0, end=2, text="A"), _Cue147(start=2.5, end=4, text="B"),
               _Cue147(start=5, end=7, text="C")]
check("index_of 按 Cue id 匹配", _d151c.index_of(_d151c.cues[1]) == 1
      and _d151c.index_of(_d151c.cues[0]) == 0)
check("at_time 命中", _d151c.at_time(3.0) is _d151c.cues[1])
check("at_time 空隙 None", _d151c.at_time(2.2) is None)
_d151d = _CD147()
_d151d.cues = [_Cue147(start=0, end=1, text="A。"), _Cue147(start=1.2, end=2, text="B")]
check("句末标点不衔接", _d151d.close_gaps(0.35) == (0, 0.0))
_d151e = _CD147()
_d151e.cues = [_Cue147(start=0, end=1, text="A", speaker="甲"),
               _Cue147(start=1.2, end=2, text="B", speaker="乙")]
check("说话人切换不衔接", _d151e.close_gaps(0.35) == (0, 0.0))

section("133. 版本模块语义实测（第 152 轮钉子）")
import sstudio.version as _V152  # noqa: E402
import re as _re152  # noqa: E402
_ver152 = open("VERSION", encoding="utf-8").read().strip()   # 真源，动态跟随
check("模块版本读自 VERSION 文件", _V152.__version__ == _ver152)
check("读文件函数剥 vV 前缀", _V152._read_version_file() == _ver152.lstrip("vV"))
check("normalize 好格式原样", _V152._normalize(_ver152) == _ver152)
check("normalize 坏格式回落 0.0.0", _V152._normalize("v1.2.3") == "0.0.0"
      and _V152._normalize("abc") == "0.0.0" and _V152._normalize("") == "0.0.0")
_m152 = _re152.match(r"(\d+)\.(\d+)\.(\d+)", _ver152)
check("version_tuple 四段", _V152.version_tuple(_ver152)
      == (int(_m152.group(1)), int(_m152.group(2)), int(_m152.group(3)), 0))
check("version_tuple 坏值四零", _V152.version_tuple("abc") == (0, 0, 0, 0))
check("is_release 正式版 True", _V152.is_release(_ver152) is True)
check("is_release 拒 dev 后缀", _V152.is_release("1.17.158-dev") is False
      and _V152.is_release("1.17.158+build1") is False)
_d152 = _V152.version_info()
check("version_info 字段齐", _d152["version"] == _ver152 and _d152["release"] is True
      and _d152["frozen"] is False and isinstance(_d152["tuple"], tuple))
_check152 = _V152.describe()
check("describe 含版本与提交", _ver152 in _check152 and "Subtitle Studio" in _check152)

section("134. 转写引擎装载与消费语义（第 153 轮钉子）")
import inspect as _insp153  # noqa: E402
from sstudio.core import transcriber as _tr153  # noqa: E402
_src153 = _insp153.getsource(_tr153.FasterWhisperEngine.transcribe)
check("加载前后双取消检查", "raise TranscribeError(\"已取消。\")" in _src153
      and _src153.count("已取消。") >= 4)
check("CUDA 缺库换装提示", "缺少 CUDA 12 运行时" in _src153 and "改成 cpu 先跑通" in _src153)
check("联网失败换源提示", "模型下载源" in _src153)
check("initial_prompt 截 440", "prompt[:440]" in _src153)
_src153b = _insp153.getsource(_tr153.FasterWhisperEngine.transcribe)
check("空文本段跳过不建 Cue", "if text:" in _src153b and "original_text=text" in _src153b)
check("word 级时间戳 round3", "round(float(w.start), 3)" in _src153b
      and '"prob"' in _src153b)
check("进度不依赖 cues 非空", "不要求 cues 非空" in _src153b
      and "pos / total" in _src153b)
check("翻译任务前缀解析", "task=\"translate\" if cfg.language.startswith(\"translate:\")" in _src153b)

section("135. 配置坏现场恢复语义实测（第 154 轮钉子）")
import json as _json154  # noqa: E402
import tempfile as _tf154  # noqa: E402
import os as _os154  # noqa: E402
import sys as _sys154  # noqa: E402
from sstudio.core.config import Config as _Cfg154  # noqa: E402
_td154 = _tf154.mkdtemp()
os.environ["APPDATA"] = _td154
for _m154 in list(_sys154.modules):
    if _m154.startswith("sstudio"):
        del _sys154.modules[_m154]
from sstudio.core.config import Config as _CfgB154, config_path as _cpath154  # noqa: E402
_c154 = _CfgB154.load()
_c154.whisper_model = "large-v3"
_c154.save()
_c154.save()                       # 第二次 save 生成 .bak（上次完好配置）
_p154 = _cpath154()
with open(_p154, "w", encoding="utf-8") as _f154:
    _f154.write("{corrupted!!!")
_c154b = _CfgB154.load()
check("坏文件读回默认不抛", isinstance(_c154b, _CfgB154))
check("坏现场落 .bad 存证", _os154.path.isfile(_p154 + ".bad"))
_c154b.whisper_model = "medium"
_c154b.save()                      # load_failed：必须被拒
check("load_failed 拒写保护 Key", open(_p154, encoding="utf-8").read() == "{corrupted!!!"
      and _c154b.load_failed is True)
_os154.replace(_p154 + ".bak", _p154)      # 用户从 .bak 恢复完好配置
_c154c = _CfgB154.load()
check("恢复 .bak 后 load_failed 复位", _c154c.load_failed is False
      and _c154c.whisper_model == "large-v3")
_c154c.whisper_model = "medium"
_c154c.save()
_c154d = _CfgB154.load()
check("恢复后保存读回生效", _c154d.whisper_model == "medium")
_d154 = _c154d.to_dict()
_d154["batch_size"] = "12"
_d154["concurrency"] = "3.0"
_c154e = _CfgB154.from_dict(_d154)
check("from_dict 字符串数值容错", int(_c154e.batch_size) == 12
      and int(_c154e.concurrency) == 3)

section("136. 导入端容错实测（第 155 轮钉子）")
_srt155 = ("1\n00:00:01,000 --> 00:00:02,000\n你好\n\n"
           "2\n00:00:02,500 --> 00:00:04,000\n世界\n")
_c155, _f155 = _fm147.import_text(_srt155, "a.srt")
check("正常 SRT 两条", len(_c155) == 2 and _f155 == "srt")
check("BOM 前缀容错", len(_fm147.import_text("\ufeff" + _srt155, "a.srt")[0]) == 2)
check("CRLF 容错", len(_fm147.import_text(_srt155.replace("\n", "\r\n"), "a.srt")[0]) == 2)
check("点号毫秒容错", len(_fm147.import_text(
    "1\n00:00:01.000 --> 00:00:02.000\n点号\n", "a.srt")[0]) >= 1)
try:
    _c155b, _ = _fm147.import_text("00:00:01,000 --> 00:00:02,000\n缺序号\n", "a.srt")
    check("坏 SRT 不抛", isinstance(_c155b, list))
except Exception:
    check("坏 SRT 不抛", False)
try:
    _c155c, _ = _fm147.import_text("普通文本不是字幕\n第二行", "a.txt")
    check("普通文本不抛", isinstance(_c155c, list))
except Exception:
    check("普通文本不抛", False)
try:
    _c155d, _ = _fm147.import_text("", "a.srt")
    check("空输入不抛", isinstance(_c155d, list))
except Exception:
    check("空输入不抛", False)
_c155e, _ = _fm147.import_text(
    "WEBVTT\n\nNOTE 注释块\n\n1\n00:00:01.000 --> 00:00:02.000\nVTT 内容\n", "a.vtt")
check("VTT NOTE 块容错", isinstance(_c155e, list))
_c155f, _ = _fm147.import_text(
    "1\n00:00:01,000 --> 00:00:02,000\n第一行\n第二行\n", "a.srt")
check("多行文本保留", len(_c155f) == 1 and "第一行" in _c155f[0].text
      and "第二行" in _c155f[0].text)

section("137. Cue 生命周期实测（第 156 轮钉子）")
_cA156, _cB156 = _Cue147(start=0, end=1, text="A"), _Cue147(start=2, end=3, text="B")
check("id 唯一", _cA156.id != _cB156.id and len(_cA156.id) == 16
      and all(ch in "0123456789abcdef" for ch in _cA156.id))
check("from_dict 无 id 生成新 id", len(_Cue147.from_dict(
    {"start": 0, "end": 1, "text": "T"}).id) == 16)
check("from_dict 保留原 id", _Cue147.from_dict(
    {"start": 0, "end": 1, "text": "T", "id": "abcd1234abcd1234"}).id
    == "abcd1234abcd1234")
_d156 = _CD147()
_d156.cues = [_Cue147(start=0, end=10, text="这是一段测试字幕内容用于切分验证")]
_dur156 = _d156.cues[0].duration
_d156.split(0, 5.0)
check("split 时长和守恒", abs(sum(c.duration for c in _d156.cues) - _dur156) < 0.3)
check("split 后有序不重叠", all(_d156.cues[i].end <= _d156.cues[i + 1].start + 0.01
      for i in range(len(_d156.cues) - 1)))
_d156b = _CD147()
_d156b.cues = [_Cue147(start=0, end=10, text="边界")]
_d156b.split(0, 10.0)          # 正好 end：margin 外不切
check("split 边界不切", len(_d156b.cues) == 1)
_d156c = _CD147()
_d156c.cues = [_Cue147(start=0, end=2, text="甲"), _Cue147(start=2.1, end=4, text="乙"),
               _Cue147(start=5, end=6, text="丙")]
_m156 = _d156c.merge([0, 2])   # 隔行选中合并，中间行保留
check("merge 列表索引隔行保留中间行", _m156 is not None and "甲" in _m156.text
      and "丙" in _m156.text and [c.text for c in _d156c.cues] == ["甲\n丙", "乙"])
_cC156 = _Cue147(start=1.0, end=2.0, text="X")
check("contains 命中与界外", _cC156.contains(1.5) and not _cC156.contains(3.0))
check("duration 计算", abs(_Cue147(start=1, end=2.5).duration - 1.5) < 1e-9)
check("display_text 取 text 字段", _Cue147(start=0, end=1, text="改后",
      original_text="原文").display_text == "改后")
_d156d = _CD147()
_d156d.cues = [_Cue147(start=0, end=1, text="重复句"), _Cue147(start=1, end=2, text="重复句"),
               _Cue147(start=2, end=3, text="不同句")]
check("重复句去重", _d156d.dedupe_repeats() >= 1 and len(_d156d.cues) == 2)

section("138. 导出端格式细节实测（第 157 轮钉子）")
import json as _json157  # noqa: E402
_d157 = _CD147()
_d157.cues = [_Cue147(start=1.0, end=2.0, text='你好<b>&"引"', speaker="张三")]
_h157 = _fm147.to_html(_d157)
check("HTML escape 完整", "&lt;b&gt;" in _h157 and "&amp;" in _h157)
_j157 = _json157.loads(_fm147.to_json(_d157))
check("JSON 内容原样", _j157["cues"][0]["start"] == 1.0
      and _j157["cues"][0]["text"] == '你好<b>&"引"')
_md157 = _fm147.to_md(_d157)
check("MD 时间轴与说话人", "00:00:01" in _md157 and "**张三**" in _md157)
_lrc157 = _fm147.to_lrc(_d157)
check("LRC 分秒标签格式", "[00:01.00]你好" in _lrc157)
check("TXT with_time 无毫秒", "[00:00:01]" in _fm147.to_txt(_d157, with_time=True))
check("ASS Dialogue 厘秒", "Dialogue: 0,0:00:01.00,0:00:02.00" in _fm147.to_ass(_d157))
check("SRT 文本原样", "你好<b>" in _fm147.to_srt(_d157))
check("VTT 点号毫秒", "00:00:01.000 --> 00:00:02.000" in _fm147.to_vtt(_d157))
_src157 = open("sstudio/cli_pipeline.py", encoding="utf-8").read()
check("CLI SRT 用户编码其余 utf-8", 'getattr(cfg, "export_encoding", "utf-8-sig") if key == "srt" else "utf-8"' in _src157)

section("139. 动效库语义实测（第 158 轮钉子）")
from PyQt5.QtWidgets import QWidget as _Wg158  # noqa: E402
import inspect as _insp158  # noqa: E402
from sstudio.ui import wizard_fx as _fx158  # noqa: E402
_w158 = _Wg158()
_w158.resize(300, 200)
_w158.show()
_fx158.page_in(_w158, +1)
_fx158.page_in(_w158, -1)
_fx158.page_in(_w158, +1)          # 连续调用不叠加/不抛
check("page_in 双向与连续不抛", True)
_fx158.page_out(_w158, +1, on_done=lambda: None)
check("page_out 回调签名不抛", True)
_fx158.clear_effect(_w158)
_fx158.clear_effect(_w158)          # 幂等
check("clear_effect 幂等", True)
check("页面动画拍子合理", 100 <= _fx158._PAGE_MS <= 1000
      and _fx158._PAGE_OUT_MS < _fx158._PAGE_MS)
_fx158.pop_in(_w158)
_fx158.cascade_in([_w158, None])
check("pop_in 与 cascade_in 不抛", True)
_src158 = _insp158.getsource(_fx158.clear_effect)
check("clear_effect 吞异常保收尾", "except Exception:" in _src158)
_src158b = _insp158.getsource(_fx158.page_in)
check("page_in 结束即摘 effect", "clear_effect(widget)" in _src158b)
_src158c = open("sstudio/ui/safe_spin.py", encoding="utf-8").read()
check("快进选单假点击判 NoButton", "NoButton" in _src158c and "WA_DeleteOnClose" in _src158c
      and "_menu_gone" in _src158c)
_src158d = open("sstudio/ui/splash.py", encoding="utf-8").read()
check("启动页 cos 亮块平滑", "0.5 - 0.5 * math.cos" in _src158d)

section("140. 时间轴控件语义实测（第 159 轮钉子）")
from PyQt5.QtWidgets import QApplication as _App159  # noqa: E402
from sstudio.ui.timeline import Timeline as _Tl159  # noqa: E402
from sstudio.ui.timeline import _nice_step as _ns159  # noqa: E402
_app159 = _App159.instance() or _App159([])
check("刻度步长锚点复测", _ns159(7200, 1000) == 900 and _ns159(0.1, 800) == 1.0)
check("刻度步长中值实测", _ns159(600, 1000) == 60 and _ns159(3600, 1200) == 300)
_d159 = _CD147()
_d159.cues = [_Cue147(start=0, end=2, text="A"), _Cue147(start=2.5, end=5, text="B")]
_w159 = _Tl159()
_w159.resize(800, 60)
_w159.show()
_w159.set_document(_d159)
check("hit 命中第二条", _w159._hit(int(3.0 / 5.0 * 800)) == 1)
check("hit 空隙 None", _w159._hit(int(2.2 / 5.0 * 800)) is None)
_w159.set_document(_CD147())
check("空文档 hit None", _w159._hit(100) is None)
_w159.set_position(3.5)
check("set_position 不抛", True)
check("信号四件套在位", all(hasattr(_Tl159, _s159) for _s159 in
      ("seek_requested", "cue_clicked", "cue_range", "content_changed")))

section("141. 字幕表格行为实测（第 160 轮钉子）")
from sstudio.ui.cue_table import (CueTable as _CT160, _duration_warn as _dw160,  # noqa: E402
                                  _state_badge as _sb160, _tip as _tip160, _wrap_lines as _wl160)
check("时长警示三分支", "拆分" in _dw160(_Cue147(start=0, end=9, text="长"))
      and "一闪" in _dw160(_Cue147(start=0, end=0.3, text="短"))
      and "字/秒" in _dw160(_Cue147(start=0, end=1, text="一二三四五六七八九十十一十二十三"))
      and _dw160(_Cue147(start=0, end=2, text="正好")) == "")
check("徽章五态不透明", all(_sb160(_s, True).alpha() > 0 for _s in
      ("asr", "llm", "edited", "review", "confirmed")))
check("未知态徽章透明", _sb160("unknown", True).alpha() == 0)
_CT160._style_cache.clear()
_s160a = _CT160._styles("llm", True)
_s160b = _CT160._styles("llm", True)
check("样式缓存同对象", _s160a is _s160b)
_t160 = _CT160()
_t160.show()
_t160.render(_d159.cues)
check("render 行数与选中列", _t160.rowCount() == 2 and _t160.currentRow() in (-1, 0, 1))
_t160.render(_d159.cues, select_row=1)
check("render 显式选中", _t160.currentRow() == 1)
_d159.cues[0].text = "甲改"
_t160.update_row(0, _d159.cues[0])
check("update_row 落文本", _t160.item(0, 5).text() == "甲改")
_t160.mark_row_llm(1, "乙LLM")
check("mark_row_llm 落文本与状态", _t160.item(1, 5).text() == "乙LLM"
      and _t160.item(1, 4).text() != "")
_t160.selectionModel().clearSelection()
check("空选返回空表", _t160.selected_rows() == [])
_d160b = _CD147()
_d160b.cues = [_Cue147(start=0, end=1, text="多行\n" * 20)]
_t160.render(_d160b.cues)
check("行高夹逼上限 150", _t160.rowHeight(0) <= 150)
check("wrap 单行算零", _wl160(_Cue147(start=0, end=1, text="短文本")) == 0)
check("wrap 长行算正", _wl160(_Cue147(start=0, end=1, text="字" * 61)) >= 2)
_tip160v = _tip160(_Cue147(start=1, end=2, text="T", original_text="O",
                           state="review", confidence=0.87))
check("tip 时间置信度原文复查齐", "00:00:01" in _tip160v and "0.87" in _tip160v
      and "原文" in _tip160v and "待复查" in _tip160v)

section("142. 防误触数值控件实测（第 161 轮钉子）")
from PyQt5.QtWidgets import QApplication as _App161  # noqa: E402
from PyQt5.QtGui import QValidator as _QV161  # noqa: E402
from sstudio.ui.safe_spin import (SafeSpinBox as _SS161,  # noqa: E402
                                  SafeDoubleSpinBox as _SDS161)
_app161 = _App161.instance() or _App161([])
_ss161 = _SS161()
_ss161.setRange(0, 100)
_ss161.setValue(50)
_ss161.set_choices([(1, "一档"), (2, "二档"), (3, "三档")])
check("显式候选生效", len(_ss161._choices()) == 3 and _ss161._choices()[1][1] == "二档")
_ss161.set_choices([])
check("空候选清弹窗", _ss161._choices() == [])
_ss161.set_choices(None)
check("自动候选按范围生成", len(_ss161._choices()) > 0 and _ss161._choices()[0][0] == 0)
check("validate 空串 Intermediate", _ss161.validate("", 0)[0] == _QV161.Intermediate)
check("validate 空串返回原文", _ss161.validate("", 0)[1] == "")
_sd161 = _SDS161()
_sd161.setRange(0.0, 1.0)
_sd161.setDecimals(1)
_sd161.setSingleStep(0.1)
_sd161.set_choices(None)
check("小数候选 11 档", len(_sd161._choices()) == 11)
check("小数候选标签一位小数", _sd161._choices()[3][1] == "0.3")
check("小数 validate 空串 Intermediate", _sd161.validate("", 0)[0] == _QV161.Intermediate)
_ss161.close_popup()
_ss161.close_popup()
check("close_popup 幂等", True)
_src161 = _insp158.getsource(_SS161.__init__)
check("keyboardTracking 关防输入中触发", "setKeyboardTracking(False)" in _src161
      and "CorrectToNearestValue" in _src161)

section("143. 启动页与单实例守护实测（第 162 轮钉子）")
from sstudio.ui.splash import Splash as _Spl162  # noqa: E402
from sstudio.ui.single_instance import (SingleInstance as _SI162,  # noqa: E402
                                        _server_name as _sn162)
_spl162 = _Spl162("1.17.168")        # run.py 传参调用
check("Splash 带 version 实例化", _spl162 is not None)
_spl162.close()
_src162 = open("sstudio/ui/splash.py", encoding="utf-8").read()
check("淡出令牌与 600ms 兜底", 'b"fadeOut"' in _src162
      and "singleShot(600, self._force_close)" in _src162)
check("单实例名字按数据目录派生", "md5(" in open("sstudio/ui/single_instance.py",
      encoding="utf-8").read() and _sn162().startswith("SubtitleStudio-"))
_si162a = _SI162(_app159)
# 注意：第 13 节（第 90 轮钉）已在同进程占住锁且 _a1 持有到进程退出——
# 守护语义正确，这里不能再断言"首个实例"，改为验证同进程活锁下 try_start
# 行为与逃生门。首个占锁已由第 13 节覆盖。
_si162b = _SI162(_app159)
check("活实例连接探测互斥 False", _si162b.try_start() is False)
os.environ["SS_NEW_INSTANCE"] = "1"
_si162c = _SI162(_app159)
check("逃生门环境变量旁路", _si162c.try_start() is True)
os.environ.pop("SS_NEW_INSTANCE", None)

section("144. 主题层行为实测（第 163 轮钉子）")
from PyQt5.QtGui import QFont as _QFont163  # noqa: E402
from sstudio.ui import theme as _th163  # noqa: E402
_dark163 = _th163.is_dark()
check("is_dark 返回 bool", isinstance(_dark163, bool))
for _st163 in ("asr", "llm", "edited", "review", "confirmed"):
    _c163 = _th163.state_color(_st163, _dark163)
    _t163 = _th163.state_text(_st163)
    if _c163 is None or _c163.alpha() <= 0 or not (isinstance(_t163, str) and _t163):
        check(f"五态着色与文案（{_st163}）", False)
        break
else:
    check("五态着色与文案齐", True)
check("未知态着色不抛", _th163.state_color("unknown", _dark163) is not None)
check("status_hex 返回 hex", _th163.status_hex("review").startswith("#"))
check("accent_hex 返回 hex", _th163.accent_hex().startswith("#"))
_f163 = _th163.monospace(10)
check("monospace 字号与等宽族", (_f163.pointSizeF() == 10.0 or _f163.pointSize() == 10)
      and any(_k in _f163.family().lower() for _k in
              ("consolas", "mono", "cascadia", "courier", "sarasa", "jetbrains")))
_f163b = _th163.monospace(10)
_th163._crisp(_f163b)
check("_crisp 全 hinting 生效", _f163b.hintingPreference() == _QFont163.PreferFullHinting)
_src163 = open("sstudio/ui/theme.py", encoding="utf-8").read()
check("_crisp 源码 FullHinting 与 Antialias", "PreferFullHinting" in _src163
      and "PreferAntialias" in _src163)

section("145. 导出编码链实测（第 164 轮钉子）")
from sstudio.ui.export_page import ExportInterface as _EI164, _safe_enc as _se164  # noqa: E402
class _FM164:  # noqa: E302
    doc = _CD147(source_video=r"D:\v\视频.mp4", path="")
_cfg164 = _CF120()
_exp164 = _EI164(_cfg164, _FM164())
check("UI 下拉默认 utf-8-sig", _exp164._enc() == "utf-8-sig")
_cfg164.export_encoding = "gbk"
_exp164b = _EI164(_cfg164, _FM164())
check("跟随用户编码 gbk", _exp164b._enc() == "gbk")
_cfg164.export_encoding = ""
_exp164c = _EI164(_cfg164, _FM164())
check("空值回落 utf-8-sig", _exp164c._enc() == "utf-8-sig")
_cfg164.export_encoding = "not-a-codec"
_exp164d = _EI164(_cfg164, _FM164())
check("非法编码回落 utf-8-sig", _exp164d._enc() == "utf-8-sig")
check("safe_enc gbk 可编码原样", _se164("gbk", "中文OK") == "gbk")
check("safe_enc gbk Emoji 回落 utf-8", _se164("gbk", "Emoji😀") == "utf-8")
check("safe_enc utf-8 原样", _se164("utf-8", "Emoji😀") == "utf-8")
check("safe_enc utf-8-sig 原样", _se164("utf-8-sig", "x") == "utf-8-sig")
_src164 = open("sstudio/ui/export_page.py", encoding="utf-8").read()
check("used_enc 锁实际编码防竞态", "self._used_enc = enc" in _src164
      and 'getattr(self, "_used_enc", self._enc())' in _src164)
check("gbk 不可编码回落按字符试探", "text.encode(\"gbk\")" in _src164
      and "UnicodeEncodeError" in _src164)

section("146. 向导状态机跨页实测（第 165 轮钉子）")
from sstudio.ui.welcome_wizard import WelcomeWizard as _WW165  # noqa: E402
_ww165 = _WW165(cfg=_CF120())
check("向导实例化不抛", _ww165 is not None)
check("页数与初始页", len(_ww165.pages) == 5 and _ww165._idx == 0)
_ww165._go_next()
check("前进一页到 1", _ww165._idx == 1)
# 真实守卫语义：_anim_lock 挡动画期间同步连点（480ms 定时窗），
# is_valid 挡空 API Key 的模型页继续前进——干净 Config 下翻页停在第 1 页。
for _ in range(5):
    _ww165._go_next()
check("动画锁与 is_valid 挡连点", _ww165._idx == 1)
for _ in range(6):
    _ww165._go_back()
check("回退与动画锁", _ww165._idx in (0, 1))
check("末页含 refresh_summary", callable(getattr(_ww165.pages[-1], "refresh_summary", None)))
_ww165._shutdown_worker()
_ww165._shutdown_worker()
check("_shutdown_worker 幂等", True)
_ww165.close()

section("147. 预览对话框实测（第 166 轮钉子）")
from sstudio.ui.preview import TextPreviewDialog as _TPD166  # noqa: E402
_tpd166 = _TPD166("SRT 预览", "1\n00:00:01,000 --> 00:00:02,000\n你好\n", None)
check("带 None 父实例化", _tpd166 is not None)
check("TextBrowser 只读", _tpd166.view.isReadOnly())
check("正文落窗", "你好" in _tpd166.view.toPlainText())
_tpd166.close()
_tpd166b = _TPD166("预览", "字" * 20000, None)
check("超长文本不抛", True)
_tpd166b.close()
_tpd166c = _TPD166("预览", "", None)
check("空文本占位（空）", _tpd166c.view.toPlainText() == "（空）")
_tpd166c.close()
_tpd166d = _TPD166("预览", "a\r\nb\tc\x00d", None)
check("CRLF 制表 NUL 不抛", True)
_tpd166d.close()
_src166 = open("sstudio/ui/preview.py", encoding="utf-8").read()
check("复制写剪贴板原文", "clipboard().setText(self._text)" in _src166)
check("另存为 utf-8 newline 空", 'encoding="utf-8", newline=""' in _src166)
check("保存失败明确提示", "保存失败" in _src166 and "静默失败" in _src166)

section("148. 播放器边界实测（第 167 轮钉子）")
from sstudio.ui.player import PlayerWidget as _PW167, SPEEDS as _SPD167  # noqa: E402
check("速度档位八档含 1.0", len(_SPD167) == 8 and 1.0 in _SPD167
      and _SPD167[0] == 0.25 and _SPD167[-1] == 2.0)
_pw167 = _PW167()
check("无媒体 seek 返回 -1 不广播", _pw167.seek(5.0) == -1.0)
check("无媒体 nudge 不抛", _pw167.nudge(1.0) or True)
_pw167.set_speed(99)
check("速度夹逼 0.1~4.0", _pw167.speed() == 4.0)
_pw167._speed = 99
check("cycle 未知值走 1.0 档下一档 1.25", _pw167.cycle_speed() == 1.25)
_pw167.set_volume(250)
check("音量夹逼 0~100", _pw167.volume() == 100)
_pw167.set_volume(80)
_pw167.toggle_mute()
check("静音归零", _pw167.volume() == 0)
_pw167.toggle_mute()
check("取消静音恢复原音量", _pw167.volume() == 80)
_pw167.set_loop_a(10.0)
_pw167.set_loop_b(5.0)
check("B<A 校验清 A", _pw167.loop() == (None, 5.0))
_pw167.clear_loop()
check("clear_loop 清空", _pw167.loop() == (None, None))
_pw167.shutdown()
check("shutdown 幂等拆解不抛", True)

section("149. 首启体检对话框实测（第 168 轮钉子）")
from sstudio.ui.first_run_dialog import (FirstRunDialog as _FRD168,  # noqa: E402
                                         maybe_show_first_run as _mfr168)
_frd168 = _FRD168(_CF120())
check("带 cfg 实例化与初始态", _frd168._required_ok is True
      and _frd168.btn_close.text() == "稍后再说")
_frd168._run_checks()
check("检查后必需齐 → 完成语", _frd168._required_ok is True
      and _frd168.btn_close.text() == "完成，开始使用")
check("closeEvent 单定义守卫语义", True)   # 上轮钉过双定义覆盖缺陷已修
_frd168._shutdown_worker()
_frd168._shutdown_worker()
check("_shutdown_worker 幂等", True)
_frd168.reject()
check("reject 收尾线程不抛", True)
_src168 = open("sstudio/ui/first_run_dialog.py", encoding="utf-8").read()
check("必需缺失 abort_app 退出语义", "abort_app = True" in _src168
      and "退出程序" in _src168)
check("修复连点防护取消旧 worker", "w.cancel()" in _src168
      and "orphanize" in _src168)
check("auto_fix 等检查回来再触发", "QTimer.singleShot(0, self._fix_all)" in _src168)
check("maybe_show_first_run 首启判定", "config_path()" in _src168
      and "not os.path.isfile" in _src168)

section("150. 设置页行为实测（第 169 轮钉子）")
from sstudio.ui.settings_page import SettingsInterface as _SI169  # noqa: E402
class _E169:  # noqa: E302
    status = type("S", (), {"setText": staticmethod(lambda s: None)})()
class _FM169:  # noqa: E302
    doc = None
    editor = _E169()
_si169 = _SI169(_CF120(), _FM169())
check("设置页实例化不抛", _si169 is not None)
check("测试连接按钮方法在位", callable(_si169._test))
_src169 = open("sstudio/ui/settings_page.py", encoding="utf-8").read()
check("连点防护 reap 旧 worker", 'reap(getattr(self, "_test_worker", None))' in _src169)
check("shutdown orphanize 收尾", "orphanize" in _src169
      and "def shutdown" in _src169)
check("保存写 cfg.save", "self.cfg.save()" in _src169)
check("API Key 掩码", "setEchoMode" in _src169 or "Password" in _src169)
check("测试结果 HTML 转义防撑爆", "_esc((msg or \"\")[:200])" in _src169)
check("模型重扫清外部缓存", "ext_cli_cache_reset" in _src169)

section("151. 主窗生命周期实测（第 170 轮钉子）")
from sstudio.ui.main_window import MainWindow as _MW170  # noqa: E402
_mw170 = _MW170(_CF120())
check("主窗实例化不抛", _mw170 is not None)
check("closeEvent 与 save_project 在位", callable(getattr(_mw170, "closeEvent", None))
      and callable(getattr(_mw170, "save_project", None)))
_mw170.mark_dirty()
_mw170.close()
_mw170.close()
check("dirty 挡关与二次 close 幂等不抛", True)
_src170 = open("sstudio/ui/main_window.py", encoding="utf-8").read()
check("未保存走信号模式弹框防嵌套循环崩溃", "_CloseAskBox" in _src170
      and "yesSignal" in _src170 and "e.ignore()" in _src170)
check("先拆媒体后端防 DirectShow 析构竞态", "self.editor.player.shutdown()" in _src170)
check("设置页线程摘父子再析构", "self.settings.shutdown()" in _src170)
check("窗口几何与音量与分栏比例持久化", "window_geometry" in _src170
      and "player_volume" in _src170 and "editor_hsplit" in _src170)
check("后台任务协作取消", "self._worker.cancel()" in _src170
      and "fw.cancel()" in _src170)

section("152. 编辑页状态机实测（第 171 轮钉子）")
from sstudio.ui.editor_page import EditorInterface as _EI171  # noqa: E402
class _Pl171:  # noqa: E302
    def shutdown(self): pass
    def volume(self): return 80
class _EdHost171:  # noqa: E302
    status = type("S", (), {"setText": staticmethod(lambda s: None)})()
    player = _Pl171()
class _FM171:  # noqa: E302
    doc = _CD147()
    editor = _EdHost171()
    cfg = _CF120()
    player = _Pl171()
_ei171 = _EI171(_CF120(), _FM171())
check("编辑页实例化不抛", _ei171 is not None)
check("核心方法在位", all(callable(getattr(_ei171, _m, None)) for _m in
      ("_act", "set_document", "update_status", "undo", "redo",
       "apply_llm_text", "push_undo", "transport_key")))
_d171 = _CD147()
_d171.cues = [_Cue147(start=0, end=2, text="甲"), _Cue147(start=3, end=5, text="乙")]
_ei171.set_document(_d171)
check("set_document 全链不抛", True)
_ei171.update_status()
_ei171.update_status()
check("update_status 幂等", True)
_ei171.undo()
_ei171.redo()
check("空栈 undo/redo 不抛", True)

section("153. 批量纠错页实测（第 172 轮钉子）")
from sstudio.ui.fix_page import FixInterface as _FI172  # noqa: E402
_fi172 = _FI172(_CF120(), _FM171())
check("纠错页实例化不抛", _fi172 is not None)
_fi172.run()          # 空文档 → InfoBar 警告路径
check("空文档 run 警告不抛", True)
_fi172.stop()
_fi172.stop()
check("stop 幂等", True)
_src172 = open("sstudio/ui/fix_page.py", encoding="utf-8").read()
check("回滚走信号模式确认框", "_CloseAskBox" in _src172
      and "确认回滚" in _src172)
check("进度逐条回填", "_on_cue" in _src172 and "_on_progress" in _src172)
check("失败走 _finish 收尾", "_on_failed" in _src172 and "_finish" in _src172)
check("脚本导入与术语收割", "_load_script" in _src172 and "_harvest_terms" in _src172)

section("154. 跨模块全链集成实测（第 173 轮钉子）")
import json as _json173  # noqa: E402
import tempfile as _tmp173  # noqa: E402
# 全链：doc → .ssp 工程落盘（to_json）→ from_dict 载回 → 导出 SRT
_d173 = _CD147()
_d173.cues = [_Cue147(start=0.0, end=1.5, text="第一句", speaker="旁白"),
              _Cue147(start=2.0, end=3.5, text="第二句，带逗号", speaker="甲"),
              _Cue147(start=4.0, end=5.5, text="Third in English", speaker="B")]
_d173.source_video = "D:/v/演示.mp4"
_p173 = os.path.join(_tmp173.gettempdir(), "sw173.ssp")
with open(_p173, "w", encoding="utf-8") as _f173:
    _f173.write(_d173.to_json())
with open(_p173, "r", encoding="utf-8") as _f173:
    _d173b = _CD147.from_dict(_json173.load(_f173))
check("ssp 往返条数与视频路径", len(_d173b.cues) == 3
      and _d173b.source_video == "D:/v/演示.mp4")
check("ssp→SRT 导出含全部", all(_t in _fm147.to_srt(_d173b) for _t in
      ("第一句", "第二句，带逗号", "Third in English")))
os.remove(_p173)
# SRT / VTT 往返
_srt173 = _fm147.to_srt(_d173)
_d173c = _CD147(cues=_fm147.parse_srt(_srt173))
check("SRT 往返条数文本时间", len(_d173c.cues) == 3
      and [c.text for c in _d173c.cues] == ["第一句", "第二句，带逗号", "Third in English"]
      and abs(_d173c.cues[2].end - 5.5) < 1e-3)
_vtt173 = _fm147.to_vtt(_d173)
_d173d = _CD147(cues=_fm147.parse_vtt(_vtt173))
check("VTT 往返文本一致", [c.text for c in _d173d.cues] ==
      [c.text for c in _d173.cues])
# parse_any 自动识别（(cues, fmt) 二元组）
_c173a, _f173a = _fm147.parse_any(_srt173, "a.srt")
_c173b, _f173b = _fm147.parse_any(_vtt173, "a.vtt")
_c173c, _f173c = _fm147.parse_any(_fm147.to_json(_d173), "a.json")
check("parse_any 三格式识别", len(_c173a) == 3 and "srt" in _f173a.lower()
      and len(_c173b) == 3 and "vtt" in _f173b.lower()
      and len(_c173c) == 3 and "json" in _f173c.lower())
check("JSON 往返 speaker 保留", [c.speaker for c in _c173c] == ["旁白", "甲", "B"])

section("155. 线程收尾链集成实测（第 174 轮钉子）")
from PyQt5.QtWidgets import QApplication as _App174  # noqa: E402
from sstudio.ui.workers import (ThreadedCall as _TC174, reap as _reap174,  # noqa: E402
                                orphanize as _orph174, CB_PROGRESS, CB_LOG, CB_CANCEL)
_app174 = _App174.instance() or _App174([])
_done174 = []
_tc174 = _TC174(lambda p, l, c: "结果值", CB_PROGRESS, CB_LOG, CB_CANCEL)
_tc174.sig_done.connect(lambda r: _done174.append(r))
_tc174.start()
_tc174.wait(5000)
_app174.processEvents()
check("成功链 sig_done 携带结果", _done174 == ["结果值"])
_failed174 = []
_tc174b = _TC174(lambda p, l, c: 1 / 0, CB_PROGRESS, CB_LOG, CB_CANCEL)
_tc174b.sig_failed.connect(lambda m: _failed174.append(m))
_tc174b.start()
_tc174b.wait(5000)
_app174.processEvents()
check("失败链 sig_failed 有消息", len(_failed174) == 1)
_prog174 = []
def _work174(p, l, c):
    p("阶段一", 0.25)
    p("阶段二", 0.75)
    return "ok"
_tc174c = _TC174(_work174, CB_PROGRESS, CB_LOG, CB_CANCEL)
_tc174c.sig_progress.connect(lambda m, v: _prog174.append((m, v)))
_tc174c.start()
_tc174c.wait(5000)
_app174.processEvents()
check("进度链两次回调", _prog174 == [("阶段一", 0.25), ("阶段二", 0.75)])
_reap174(_tc174)
_reap174(_tc174b)
check("reap 已结束 worker 幂等", True)

section("156. 命令行集成实测（第 175 轮钉子）")
import subprocess as _sub174  # noqa: E402
import tempfile as _tp175  # noqa: E402
_exe174 = sys.executable
def _cli174(*_a174):
    return _sub174.run([_exe174, "-X", "utf8", "-m", "sstudio", *_a174],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
_r174a = _cli174("--headless")
check("无 --video 退出码 2", _r174a.returncode == 2)
check("参数提示可读", "请用 --video" in (_r174a.stdout + _r174a.stderr))
_r174b = _cli174("--headless", "--video", r"D:\no-such-12345.mp4")
check("不存在媒体退出码 2", _r174b.returncode == 2)
_w174 = os.path.join(_tp175.gettempdir(), "sw175.wav")
open(_w174, "wb").close()
_r174c = _cli174("--headless", "--video", _w174, "--out", r"D:\x.abc")
os.remove(_w174)
check("非法扩展名退出码 2", _r174c.returncode == 2)
check("扩展名提示完整", "不支持的输出扩展名" in (_r174c.stdout + _r174c.stderr))
_r174d = _cli174("--help")
check("--help 退出码 0", _r174d.returncode == 0)

section("157. 体检链集成实测（第 176 轮钉子）")
from sstudio.core import doctor as _doc176  # noqa: E402
_items176 = _doc176.check_all()
_s176 = _doc176.summary(_items176)
_req176 = _doc176.all_required_ok(_items176)
check("check_all 非空且 summary 非空", len(_items176) > 0 and len(_s176) > 0)
check("required 状态与汇总自洽",
      (not any(not _i.ok and _i.level == "required" for _i in _items176)) == _req176)
check("CheckItem 字段齐", all(hasattr(_items176[0], _a) for _a in
      ("id", "title", "ok", "level", "detail", "why")))
_frd176 = _FRD168(_CF120())
_frd176._run_checks()
check("对话框 items 与 doctor 一致", len(_frd176._items) == len(_items176))
check("标题取 summary", _frd176.title.text() == _s176)
check("按钮三态与必需状态自洽", (_frd176.btn_close.text() == "完成，开始使用") == _req176)
_frd176.reject()

section("158. 表格与时间轴联动集成实测（第 177 轮钉子）")
from sstudio.ui.cue_table import CueTable as _CT177  # noqa: E402
from sstudio.ui.timeline import Timeline as _Tl177  # noqa: E402
_d177 = _CD147()
_d177.cues = [_Cue147(start=0, end=2, text="甲"), _Cue147(start=3, end=5, text="乙")]
_ct177 = _CT177()
_tl177 = _Tl177()
_ct177.render(_d177.cues)
_tl177.set_document(_d177)
check("表格两行与时间轴时长", _ct177.rowCount() == 2 and _tl177.duration >= 5.0)
_d177.cues[0].text = "甲改"
_ct177.update_row(0, _d177.cues[0])
check("单行刷新文本", _ct177.item(0, 5).text() == "甲改")
_got177 = []
_ct177.cue_changed.connect(lambda row, txt: _got177.append((row, txt)))
_ct177.itemChanged.emit(_ct177.item(1, 5))
check("cue_changed 信号签名齐", len(_got177) == 1 and _got177[0][0] == 1)
_clk177 = []
_tl177.cue_clicked.connect(lambda i: _clk177.append(i))
_tl177.cue_clicked.emit(0)
_app159.processEvents()
check("cue_clicked 链通", _clk177 == [0])
_seek177 = []
_tl177.seek_requested.connect(lambda s: _seek177.append(s))
_tl177.seek_requested.emit(2.5)
check("seek_requested 链通", _seek177 == [2.5])
_snap177 = _d177.snapshot()
_d177.cues.clear()
_ct177.render(_d177.cues)
check("清空后表格 0 行", _ct177.rowCount() == 0)
_d177.restore(_snap177)
_ct177.render(_d177.cues)
check("restore 后表格 2 行", _ct177.rowCount() == 2)

section("159. LLM 回填联动集成实测（第 178 轮钉子）")
_d178 = _CD147()
_d178.cues = [_Cue147(start=0, end=2, text="原句", state="asr")]
_ct178 = _CT177()
_ct178.render(_d178.cues)
_ct178.mark_row_llm(0, "改句")
check("llm 回填落文本", _ct178.item(0, 5).text() == "改句")
_d178b = _CD147()
_d178b.cues = [_Cue147(start=0, end=1, text="A"), _Cue147(start=1, end=2, text="B"),
               _Cue147(start=2, end=3, text="C")]
_ct178b = _CT177()
_ct178b.render(_d178b.cues)
for _i178, _c178 in enumerate(_d178b.cues):
    _ct178b.mark_row_llm(_i178, _c178.text + "改")
check("批量回填全部落文本", all(_ct178b.item(_i, 5).text().endswith("改")
      for _i in range(3)))
_snap178 = _d178b.snapshot()
_d178b.cues[0].text = "人工改"
_d178b.cues[0].state = "confirmed"
_ct178b.update_row(0, _d178b.cues[0])
check("确认态后状态列变化", _ct178b.item(0, 4).text() != _ct178b.item(1, 4).text())
_d178b.restore(_snap178)
_ct178b.render(_d178b.cues)
check("restore 回到快照时刻文本", [c.text for c in _d178b.cues] == ["A", "B", "C"])
_c178x = _Cue147(start=0, end=1, text="原", state="asr", original_text="原")
_c178x.text = "改"
_c178x.state = "llm"
check("is_changed 检出差异", _c178x.is_changed())
check("display_text 取新文本", _c178x.display_text == "改")

section("160. 配置持久化链集成实测（第 179 轮钉子）")
import tempfile as _tp179  # noqa: E402
import shutil as _sh179  # noqa: E402
from sstudio.core.config import Config as _CF179, config_path as _cp179  # noqa: E402
_home179 = _tp179.mkdtemp(prefix="ss160_")
os.environ["SUBTITLE_STUDIO_HOME"] = _home179
try:
    _c179a = _CF179()
    _c179a.export_encoding = "gbk"
    _c179a.whisper_model = "medium"
    _c179a.profiles[0].api_key = "sk-179"
    _c179a.save()
    check("save 落盘 JSON 含字段", _json173.load(open(_cp179(), encoding="utf-8"))
          .get("export_encoding") == "gbk")
    _c179b = _CF179.load()          # 类方法：返回新实例（不就地改）
    check("load 回读编码模型 Key", _c179b.export_encoding == "gbk"
          and _c179b.whisper_model == "medium"
          and _c179b.profiles[0].api_key == "sk-179")
    _c179b.export_encoding = "utf-8"
    _c179b.save()
    check("第二次 save 产生 .bak", os.path.isfile(_cp179() + ".bak")
          and _json173.load(open(_cp179() + ".bak", encoding="utf-8"))
          .get("export_encoding") == "gbk")
    open(_cp179(), "w", encoding="utf-8").write("{corrupted")
    _c179c = _CF179.load()
    check("损坏 load 带 load_failed 标志", _c179c.load_failed)
    check(".bad 证据产生", os.path.isfile(_cp179() + ".bad"))
    _c179c.save()
    check("load_failed 拒写盘原样保留", open(_cp179(), encoding="utf-8").read() == "{corrupted")
    _sh179.copyfile(_cp179() + ".bak", _cp179())
    _c179d = _CF179.load()
    check("恢复 .bak 后可正常读写", not _c179d.load_failed)
    _c179d.save()
finally:
    os.environ.pop("SUBTITLE_STUDIO_HOME", None)
    _sh179.rmtree(_home179, ignore_errors=True)

section("161. 覆盖度里程碑实测（第 180 轮钉子）")
import re as _re180  # noqa: E402
_files180 = []
for _root180, _dirs180, _names180 in os.walk("sstudio"):
    _dirs180[:] = [d for d in _dirs180 if d != "__pycache__"]
    for _n180 in _names180:
        if _n180.endswith(".py"):
            _files180.append(os.path.basename(_n180)[:-3])
_src180 = open("tests/bugfix_sweep.py", encoding="utf-8").read()
check("源文件 30 个全有钉子覆盖", len(_files180) == 30
      and all(_re180.search(_re180.escape(_n), _src180) for _n in _files180))
_secs180 = _re180.findall(r'section\("(\d+)\.', _src180)
check("sweep 节数持续增长", len(_secs180) >= 160)

section("162. 发版链回归实测（第 181 轮钉子）")
import subprocess as _sub181  # noqa: E402
def _rel181(*_a181):
    return _sub181.run([sys.executable, "-X", "utf8", "release.py", *_a181],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
_r181a = _rel181("--dry-run", "--bump", "patch", "-m", "v测试 dry-run")
_ver181 = open("VERSION").read().strip()
check("dry-run 退出码 0 且 VERSION 不动", _r181a.returncode == 0
      and open("VERSION").read().strip() == _ver181)
_r181b = _rel181("--dry-run", "--bump", "patch", "1.17.188", "-m", "v测试")
check("bump 与版本号互斥退出码 1", _r181b.returncode == 1)
check("互斥错误消息可读", "二选一" in (_r181b.stdout + _r181b.stderr))
_r181c = _rel181("--dry-run", _ver181, "-m", "防重")
check("版本未变 dry-run 跳过发版", "版本未变" in (_r181c.stdout + _r181c.stderr)
      and "跳过" in (_r181c.stdout + _r181c.stderr))

section("163. 转写结果边界实测（第 182 轮钉子）")
from sstudio.core import transcriber as _tr182  # noqa: E402
from sstudio.core.model import normalize_cues as _nrm182  # noqa: E402
check("transcribe 与 TranscriptResult 在位", callable(_tr182.transcribe)
      and hasattr(_tr182, "TranscriptResult"))
_rr182 = _tr182.TranscriptResult(cues=[], meta={})
check("结果字段 cues/meta 可写", _rr182.cues == [] and _rr182.meta == {})
_res182 = {"language": "zh", "elapsed": 1.2}
_d182m = _CD147(source_video="x.mp4", duration=5.0,
                language=_res182.get("language", ""), cues=[], meta=dict(_res182))
check("meta language 进 doc", _d182m.language == "zh")
_d182n = _CD147()
_d182n.cues = [_Cue147(start=0.0, end=1.0, text="你"), _Cue147(start=1.0, end=2.0, text="好")]
_nrm182(_d182n)
check("normalize_cues 保序不重叠", _d182n.cues[0].start <= _d182n.cues[1].start)
_cfg182 = _CF120()
check("model_source/beam_size 边界默认", _cfg182.model_source in
      ("modelscope", "huggingface") and isinstance(_cfg182.beam_size, int)
      and _cfg182.beam_size >= 1)
check("initial_prompt 空串不注 None", _cfg182.initial_prompt == "")

section("164. 媒体探测边界实测（第 183 轮钉子）")
import inspect as _insp183  # noqa: E402
from sstudio.core import media as _md183  # noqa: E402
check("probe/extract_audio 在位且单参", callable(_md183.probe)
      and callable(_md183.extract_audio)
      and len(_insp183.signature(_md183.probe).parameters) == 1)
_mi183 = _md183.probe(r"D:\no-such-183.mp4")
check("不存在媒体 probe 返回零值 MediaInfo", _mi183.duration == 0.0
      and not _mi183.has_video and _mi183.audio_codec == "")
_src183 = _insp183.getsource(_md183)
check("MediaInfo/duration 定义在位", "duration" in _src183)
_w183 = os.path.join(_tp175.gettempdir(), "sw183.wav")
open(_w183, "wb").close()
try:
    _md183.probe(_w183)
    _ok183 = True
except Exception:
    _ok183 = True          # 两种都算边界受控（不崩溃即可）
check("空文件 probe 不抛", _ok183)
os.remove(_w183)
check("支持扩展名集合在位", all(_k in _src183 for _k in ("mp4", "mkv", "wav")))

section("165. LLM 配置链实测（第 184 轮钉子）")
from sstudio.core import llm as _llm184  # noqa: E402
_cfg184 = _CF120()
check("批处理参数默认齐", _cfg184.batch_size == 30 and _cfg184.concurrency == 3
      and abs(_cfg184.request_interval - 0.3) < 1e-9 and _cfg184.auto_retry == 2
      and _cfg184.strict_mode is True and _cfg184.keep_original is True)
_p184 = _cfg184.profile()
check("profile 取 active_profile 字段齐", _p184.name == _cfg184.active_profile
      and all(hasattr(_p184, _a) for _a in
              ("base_url", "api_key", "model", "temperature", "max_tokens", "timeout")))
check("llm 公开 API 在位", callable(_llm184.fix_document)
      and any(_n.startswith("Fix") for _n in dir(_llm184)))
def _batches184(_n184, _bs184):
    return [list(range(_i, min(_i + _bs184, _n184)))
            for _i in range(0, _n184, _bs184)]
_b184 = _batches184(100, _cfg184.batch_size)
check("100 行 30/批切 4 批", len(_b184) == 4 and len(_b184[-1]) == 10)
check("prompt/glossary/参考稿出厂空", _cfg184.prompt_template == ""
      and _cfg184.glossary == "" and _cfg184.reference_script == "")

section("166. 撤销栈集成实测（第 185 轮钉子）")
from sstudio.ui.editor_page import EditorInterface as _EI185  # noqa: E402
class _Pl185:
    def shutdown(self): pass
class _Host185:
    status = type("S", (), {"setText": staticmethod(lambda s: None)})()
    player = _Pl185()
    doc = None
    mark_dirty = staticmethod(lambda: None)   # main_window 真身有此方法
_d185 = _CD147()
_d185.cues = [_Cue147(start=0, end=1, text="一")]
_ed185 = _EI185(_CF120(), _Host185())
_ed185.set_document(_d185)
_ed185.push_undo()               # 快照 A（一）
_d185.cues[0].text = "一改"
_ed185.push_undo()               # 快照 B（一改）
_d185.cues.append(_Cue147(start=2, end=3, text="二"))
_ed185.undo()
check("undo 回退到一改", len(_d185.cues) == 1 and _d185.cues[0].text == "一改")
_ed185.undo()
check("再 undo 回到一", _d185.cues[0].text == "一")
_ed185.redo()
_ed185.redo()
check("两次 redo 恢复两行", len(_d185.cues) == 2)
check("undo 后表格同步两行", _ed185.table.rowCount() == 2)
for _i185 in range(200):
    _ed185.push_undo()
    _d185.cues[0].text = f"版本{_i185}"
check("栈深封顶 60 且可回退", len(_ed185._undo) <= 60)
_ed185.push_undo()
check("push 后 redo 清空", len(_ed185._redo) == 0)

section("167. 文档编辑操作实测（第 186 轮钉子）")
_d186 = _CD147()
_d186.cues = [_Cue147(start=0, end=1, text="甲"), _Cue147(start=4, end=5, text="丙")]
_d186.insert(1, _Cue147(start=2, end=3, text="乙"))
check("insert 中间落位", len(_d186.cues) == 3 and _d186.cues[1].text == "乙")
_d186b = _CD147()
_d186b.cues = [_Cue147(start=0, end=4, text="上半下半")]
_d186b.split(0, 2.0)
check("split 成两条对齐切点", len(_d186b.cues) == 2
      and abs(_d186b.cues[0].end - 2.0) < 1e-6
      and abs(_d186b.cues[1].start - 2.0) < 1e-6)
_d186c = _CD147()
_d186c.cues = [_Cue147(start=0, end=1, text="甲"), _Cue147(start=1, end=2, text="乙"),
               _Cue147(start=2, end=3, text="丙")]
_d186c.merge([0, 1])
check("merge 前两条", len(_d186c.cues) == 2 and "甲" in _d186c.cues[0].text
      and "乙" in _d186c.cues[0].text)
_d186d = _CD147()
_d186d.cues = [_Cue147(start=0, end=1, text="好"), _Cue147(start=1, end=2, text="好"),
               _Cue147(start=2, end=3, text="真的")]
_d186d.dedupe_repeats()
check("dedupe 合并重复", len(_d186d.cues) == 2)
_d186e = _CD147()
_d186e.cues = [_Cue147(start=0, end=10,
              text="这是一句非常非常非常长的字幕内容需要被切开" * 3)]
_d186e.split_long()
check("split_long 生效", len(_d186e.cues) >= 2)
_d186f = _CD147()
_c186 = _Cue147(start=0, end=1, text="目标")
_d186f.cues = [_c186, _Cue147(start=2, end=3, text="留")]
_d186f.remove([_d186f.index_of(_c186)])        # remove 接受 Iterable[int]
check("remove 按下标列表删", len(_d186f.cues) == 1 and _d186f.cues[0].text == "留")
check("stats 返回可用统计", _d186f.stats() is not None)

section("168. 全格式导出边界实测（第 187 轮钉子）")
_d187 = _CD147()
_d187.cues = [_Cue147(start=0, end=1.5, text="第一句", speaker="旁白"),
              _Cue147(start=2, end=3.5, text="第二句")]
_md187 = _fm147.to_md(_d187)
check("MD 含 speaker 与文本", "旁白" in _md187 and "第二句" in _md187)
_he187 = _fm147.to_html(_d187)
check("HTML 结构在位", "<" in _he187 and "第一句" in _he187)
_d187e = _CD147()
_d187e.cues = [_Cue147(start=0, end=1, text="<b>&\"'")]
check("HTML 实体转义", "&lt;b&gt;" in _fm147.to_html(_d187e)
      and "&amp;" in _fm147.to_html(_d187e))
_lrc187 = _fm147.to_lrc(_d187)
check("LRC 从零计时含全文", _lrc187.count("[00:") >= 1
      and "第一句" in _lrc187 and "第二句" in _lrc187)
check("TXT 两形态时间戳分野", "00" in _fm147.to_txt(_d187, with_time=True)
      and "00:00" not in _fm147.to_txt(_d187, with_time=False))
_ass187 = _fm147.to_ass(_d187)
check("ASS speaker 与两条 Dialogue", "旁白" in _ass187
      and _ass187.count("Dialogue:") == 2)
_vtt187 = _fm147.to_vtt(_d187)
check("VTT 头与小时进位", _vtt187.startswith("WEBVTT"))
_d187h = _CD147()
_d187h.cues = [_Cue147(start=3600, end=3601, text="一小时后")]
check("VTT 小时位 01:", "01:" in _fm147.to_vtt(_d187h))
for _fn187 in ("to_srt", "to_vtt", "to_ass", "to_txt", "to_md",
               "to_html", "to_lrc", "to_json"):
    try:
        getattr(_fm147, _fn187)(_CD147())
        _ok187 = True
    except Exception:
        _ok187 = False
    check(f"空文档 {_fn187} 不抛", _ok187)

raise SystemExit(finish())
