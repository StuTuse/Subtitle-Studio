"""应用配置：读写 ``~/.subtitle_studio/config.json``（或工程内 config.json）。

不引入 qfluentwidgets 的 QConfig，避免版本 API 漂移；自己写一层轻量持久化，
并且**明文保存 API Key**（本地桌面软件，避免 keyring 依赖导致的启动失败）。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

APP_NAME = "Subtitle Studio"
APP_ORG = "SubtitleStudio"

_CACHE_ROOT: Dict[str, str] = {}


def _makedirs(path: str) -> bool:
    """建目录并验证真的可写（被安全软件/权限拦下时返回 False）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "a", encoding="utf-8"):
            pass
        try:
            os.remove(probe)
        except OSError:
            pass
        return True
    except OSError:
        return False


def _portable_root() -> str:
    """源码目录 / exe 同级目录，用于便携模式或系统目录不可写时回退。"""
    try:
        import sys
        if getattr(sys, "frozen", False):
            return os.path.join(os.path.dirname(sys.executable), "SSData")
        return os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "SSData")
    except Exception:
        return os.path.join(os.path.expanduser("~"), "SSData")


def _home_override() -> str:
    """设了 ``SUBTITLE_STUDIO_HOME`` 就把配置与数据全放那儿（绿色便携 / 测试隔离）。"""
    p = os.environ.get("SUBTITLE_STUDIO_HOME", "").strip()
    if not p:
        return ""
    p = os.path.abspath(os.path.expanduser(p))
    return p if _makedirs(p) else ""


def config_dir() -> str:
    if "config" in _CACHE_ROOT:
        # 缓存命中也要校验目录还在：测试隔离/便携 U 盘场景下目录可能被删
        # （TempDir 退出、U 盘拔出、网络盘断连）。目录没了就清缓存重新解析，
        # 否则 save() 会往不存在的路径写、被原子写容错吞掉 → 配置静默丢。
        if os.path.isdir(_CACHE_ROOT["config"]):
            return _CACHE_ROOT["config"]
        _CACHE_ROOT.pop("config", None)
        _CACHE_ROOT.pop("data", None)
    override = _home_override()
    if override:
        p = os.path.join(override, "config")
        _makedirs(p)
        _CACHE_ROOT["config"] = p
        return p
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        primary = os.path.join(base, "SubtitleStudio")
    else:
        primary = os.path.join(os.environ.get("XDG_CONFIG_HOME") or
                               os.path.join(os.path.expanduser("~"), ".config"),
                               "subtitle-studio")
    # 系统配置目录不可写时，配置与数据放一起（便携模式）
    path = primary if _makedirs(primary) else data_dir()
    _CACHE_ROOT["config"] = path
    return path


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def data_dir() -> str:
    """数据目录：缓存音频、日志、便携回退。优先系统目录，不可写则退回程序目录。"""
    if "data" in _CACHE_ROOT:
        # 同 config_dir()：缓存命中先校验目录仍在，失效（被删/盘断开）则重解析
        if os.path.isdir(_CACHE_ROOT["data"]):
            return _CACHE_ROOT["data"]
        _CACHE_ROOT.pop("data", None)
        _CACHE_ROOT.pop("config", None)
    override = _home_override()
    if override:
        p = os.path.join(override, "data")
        _makedirs(p)
        _CACHE_ROOT["data"] = p
        return p
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        primary = os.path.join(base, "SubtitleStudio")
    else:
        primary = os.path.join(os.environ.get("XDG_DATA_HOME") or
                               os.path.join(os.path.expanduser("~"), ".local", "share"),
                               "subtitle-studio")
    path = primary if _makedirs(primary) else _portable_root()
    if not _makedirs(path):
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "SubtitleStudio")
        _makedirs(path)
    _CACHE_ROOT["data"] = path
    return path


def models_dir() -> str:
    p = os.path.join(data_dir(), "models")
    _makedirs(p)
    return p


@dataclass
class LLMProfile:
    """一个可切换的大模型接入点。"""

    name: str = "默认"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.0
    max_tokens: int = 4096
    top_p: float = 1.0
    timeout: float = 300.0           # 单次请求超时；推理模型大批次需要更久
    no_reasoning: bool = False       # 推理模型关闭思考：纠错任务不需要，能快 10~100 倍
    enabled: bool = True
    # 供应商预设：openai / anthropic / ollama / custom（当前统一走 OpenAI 兼容层）
    kind: str = "openai"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LLMProfile":
        base = LLMProfile()
        for k, v in (d or {}).items():
            if not hasattr(base, k):
                continue
            # 按默认值的类型转换。早先这里对未列出的字段一律 str()，
            # 导致 timeout 变 "300.0"（int() 直接崩）、no_reasoning=False 变
            # 字符串 "False"（恒为真，开关关不掉）。新增字段也不必再改这里。
            default = getattr(base, k)
            try:
                if isinstance(default, bool):
                    v = bool(v) if not isinstance(v, str) else \
                        v.strip().lower() in ("1", "true", "yes", "on")
                elif isinstance(default, int) and not isinstance(default, bool):
                    v = int(float(v))
                elif isinstance(default, float):
                    v = float(v)
                    # NaN/Inf 能穿透 json.loads（Python 扩展字面量）：
                    # temperature=NaN 进请求体会被服务端 400，归默认值
                    if not math.isfinite(v):
                        v = default
                else:
                    v = "" if v is None else str(v)
            except (TypeError, ValueError):
                continue
            # timeout 防呆：手改 config.json 写 0/负数时，OpenAI client 会把它
            # 原样传给底层 httpx，首个请求就异常；钳回默认 300s。
            if k == "timeout":
                try:
                    if float(v) < 1.0:
                        v = 300.0
                except (TypeError, ValueError):
                    v = 300.0
            setattr(base, k, v)
        return base


BUILTIN_PRESETS: List[Dict[str, str]] = [
    {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"name": "Moonshot Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-latest"},
    {"name": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-air"},
    {"name": "豆包 Doubao", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-pro-32k"},
    {"name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat"},
    {"name": "Ollama (本地)", "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:14b", "api_key": "ollama"},
    {"name": "LM Studio (本地)", "base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "api_key": "lm-studio"},
]


@dataclass
class Config:
    # ---- 转写
    asr_engine: str = "faster-whisper"      # faster-whisper | whisper.cpp | openai_api
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "auto"            # auto | cuda | cpu
    whisper_compute: str = "auto"           # auto | float16 | int8_float16 | int8
    whisper_threads: int = 0                 # 0 = 自动
    language: str = "auto"
    beam_size: int = 5
    vad: bool = True
    word_timestamps: bool = True
    initial_prompt: str = ""
    condition_on_previous_text: bool = False
    no_speech_threshold: float = 0.6
    model_dir: str = ""                      # 空 = 用默认缓存目录
    model_source: str = "modelscope"         # 在线下载源：modelscope | hf-mirror | official
    cuda_rt_dir: str = ""                    # 手动指定 CUDA12 运行库目录（含 cublas64_12.dll）
    auto_cpu_fallback: bool = True           # GPU 跑不动时自动改用 CPU
    openai_transcribe_model: str = "whisper-1"

    # ---- 修正（LLM）
    profiles: List[LLMProfile] = field(default_factory=lambda: [LLMProfile()])
    active_profile: str = "默认"
    prompt_template: str = ""                # 空 = 用内置默认模板
    reference_script: str = ""               # 原始稿件 / 术语背景
    glossary: str = ""                       # 术语表：一行一个，或 错=>对
    batch_size: int = 30
    concurrency: int = 3
    request_interval: float = 0.3
    strict_mode: bool = True                 # 严格：条数/顺序必须一致
    keep_original: bool = True
    auto_retry: int = 2

    # ---- 导出 / 界面
    export_dir: str = ""
    last_dir: str = ""
    export_encoding: str = "utf-8-sig"
    theme: str = "auto"                      # light | dark | auto
    accent: str = "#0aa2c0"
    ui_scale: float = 1.0                    # 界面缩放（相对物理像素）。
                                             # 1.0=物理 1:1 最清晰（推荐出厂值，
                                             # 与设置页「1.00 ×（推荐）」一致）；
                                             # 1.25/1.5=更大控件；0=跟随系统
    window_geometry: str = ""
    editor_hsplit: List[int] = field(default_factory=list)  # 编辑页「播放器|修改区」分栏记忆
    recent_files: List[str] = field(default_factory=list)
    max_recent: int = 12
    auto_save: bool = True
    keep_audio: bool = False
    auto_close_gaps: bool = True             # 转写后自动消除字幕间小空隙（防闪烁）
    gap_max: float = 0.35                    # 小于该秒数且不是句末停顿的空隙才衔接
    player_volume: int = 80
    setup_done: bool = False                 # 欢迎向导完成标记（首启配置流程）
    custom_presets: List[Dict[str, str]] = field(default_factory=list)
    # 自定义供应商预设（用户在设置页保存的）。条目键与 BUILTIN_PRESETS 相同：
    # name / base_url / model / api_key(可选) / no_reasoning(可选)。
    # 欢迎向导与设置页的预设下拉 = 内置 + 自定义；出厂仅注入一条示例
    # 预设，用户可随意增删改。

    # ------------------------------------------------------------ I/O
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["profiles"] = [p.to_dict() if isinstance(p, LLMProfile) else p for p in self.profiles]
        d["custom_presets_loaded"] = True   # 见 from_dict：标记出厂预设已处理过
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Config":
        cfg = cls()
        d = dict(d or {})
        profs = d.pop("profiles", None)
        custom_presets_raw = d.pop("custom_presets", None)
        for k, v in d.items():
            if not hasattr(cfg, k):
                continue
            cur = getattr(cfg, k)
            try:
                if isinstance(cur, bool):
                    v = bool(v) if not isinstance(v, str) else \
                        v.strip().lower() in ("1", "true", "yes", "on")
                elif isinstance(cur, int) and not isinstance(cur, bool):
                    v = int(float(v))       # 兼容被写成 "3.0" 的历史配置
                elif isinstance(cur, float):
                    v = float(v)
                elif isinstance(cur, list):
                    v = list(v)
                elif isinstance(cur, str):
                    v = str(v)
            except (TypeError, ValueError):
                continue
            setattr(cfg, k, v)
        # 迁移：历史版本曾支持调用第三方软件自带的转写环境，现已移除；
        # 老配置里的该值回落到默认引擎，避免设置页下拉框落空。
        if cfg.asr_engine not in ("faster-whisper", "whisper.cpp", "openai_api"):
            cfg.asr_engine = "faster-whisper"
        # 迁移：更早的版本每点一次「开始纠错」就往术语表尾部追加一段
        # 【本轮补充】，永久越滚越大。读到旧配置时把这些尾巴清掉。
        if isinstance(cfg.glossary, str) and "【本轮补充】" in cfg.glossary:
            cfg.glossary = cfg.glossary.split("【本轮补充】")[0].strip()
        if profs:
            cfg.profiles = [LLMProfile.from_dict(p) for p in profs if isinstance(p, dict)]
        if not cfg.profiles:
            cfg.profiles = [LLMProfile()]
        if not any(p.name == cfg.active_profile for p in cfg.profiles):
            cfg.active_profile = cfg.profiles[0].name
        # 自定义预设：出厂带一条本地中转示例，仅当用户从未配置过时注入；
        # 一旦有自定义条目（哪怕删光了留空列表），完全尊重配置文件。
        cp = custom_presets_raw if isinstance(custom_presets_raw, list) else []
        cp = [p for p in cp if isinstance(p, dict) and p.get("name")
              and p.get("base_url")]
        if not cp and not d.get("custom_presets_loaded"):
            cp = [{"name": "本地中转 (示例)",
                   "base_url": "http://127.0.0.1:8790/v1",
                   "model": "deepseek-v41-flash", "no_reasoning": "1"}]
        cfg.custom_presets = cp
        return cfg

    # load 失败（配置文件损坏/被锁定）时置 True：本实例里是默认值，
    # 不是用户的真实配置。此时禁止 save() 覆盖——否则一次磁盘抖动就让
    # 默认对象把用户的 API Key 永久抹掉。
    load_failed = False

    @classmethod
    def load(cls) -> "Config":
        cfg = cls.from_dict({})
        path = config_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except FileNotFoundError:
            # 无配置文件（真·首次）：出厂自定义预设在那里注入
            return cfg
        except Exception:
            # 文件存在但读不出来：损坏/锁定。保留现场，绝不用默认值覆盖它。
            try:
                import shutil
                shutil.copy2(path, path + ".bad")
            except OSError:
                pass
            cfg.load_failed = True
            return cfg

    def save(self) -> bool:
        """持久化配置。返回是否真的落盘成功——旧实现吞掉所有异常，磁盘满/
        目录被占用时 UI 显示已保存而内容从未写出，下次启动 Key 无声回滚。

        load_failed 拒写与静默容错路径返回 False，调用方按需提示。
        """
        if getattr(self, "load_failed", False):
            # 配置文件读取失败后的实例带着默认值：落盘会把用户的真实配置
            # （API Key 等）覆盖掉。拒绝写入，等用户处理 .bad 文件。
            return False
        # 原子写：先写临时文件再 os.replace 整块替换。直接 open("w") 会先
        # 清空原文件，写入中途断电/崩溃 → 半截 JSON → 下次 load 失败静默
        # 回退默认，用户的 API Key 全丢。
        try:
            path = config_path()
            # 写之前留一份上次的完好配置：万一新写的这份后来发现有问题，
            # 用户还能从 .bak 捞回来
            try:
                if os.path.isfile(path):
                    import shutil
                    shutil.copy2(path, path + ".bak")
            except OSError:
                pass
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    # 恢复出厂时保留的字段：
    #  · profiles / active_profile —— 大模型接入点（API Key、访问地址、模型名）。
    #    用户明确要求：重置设置绝不能清掉这些，重新填 Key 太折磨人。
    #  · setup_done —— 欢迎向导只此一回：重置设置不该把用户拉回向导重走一遍。
    #  · custom_presets —— 用户自己存的供应商预设，属于"数据"不属于"设置"。
    #  · 其余是窗口位置/最近文件这类"使用痕迹"，不算配置，一并保留。
    _RESET_KEEP = ("profiles", "active_profile", "window_geometry",
                   "recent_files", "last_dir", "export_dir",
                   "player_volume", "editor_hsplit",
                   "setup_done", "custom_presets")

    def reset_to_defaults(self) -> None:
        """一键恢复出厂默认：**大模型接入点（API Key / 访问地址 / 模型名）原样保留**。

        其余全部回到出厂值。调用方负责 ``save()``。
        """
        fresh = Config()
        import copy
        for f in type(self).__dataclass_fields__:
            if f in self._RESET_KEEP:
                continue
            # deepcopy：直接赋引用的话，self 和 fresh 共享同一个列表/字典，
            # 之后改 self 会牵动别的持有者
            setattr(self, f, copy.deepcopy(getattr(fresh, f)))

    # ------------------------------------------------------------ helpers
    def profile(self, name: Optional[str] = None) -> LLMProfile:
        name = name or self.active_profile
        for p in self.profiles:
            if p.name == name:
                return p
        return self.profiles[0]

    def add_recent(self, path: str) -> None:
        if not path:
            return
        path = os.path.abspath(path)
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        # max_recent 可能被手改成 0/负数：负数切片会"删尾巴"，语义完全反了
        self.recent_files = self.recent_files[:max(1, int(self.max_recent or 0))]
