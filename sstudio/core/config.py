"""应用配置：读写 ``~/.subtitle_studio/config.json``（或工程内 config.json）。

不引入 qfluentwidgets 的 QConfig，避免版本 API 漂移；自己写一层轻量持久化，
并且**明文保存 API Key**（本地桌面软件，避免 keyring 依赖导致的启动失败）。
"""

from __future__ import annotations

import json
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
        return _CACHE_ROOT["config"]
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
        return _CACHE_ROOT["data"]
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
                else:
                    v = "" if v is None else str(v)
            except (TypeError, ValueError):
                continue
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
    ui_scale: float = 1.5                    # 界面缩放（相对物理像素）。
                                             # 1.5=舒适；1.25/1.0=更紧凑；0=跟随系统
    window_geometry: str = ""
    editor_hsplit: List[int] = field(default_factory=list)  # 编辑页「播放器|修改区」分栏记忆
    recent_files: List[str] = field(default_factory=list)
    max_recent: int = 12
    auto_save: bool = True
    keep_audio: bool = False
    auto_close_gaps: bool = True             # 转写后自动消除字幕间小空隙（防闪烁）
    gap_max: float = 0.35                    # 小于该秒数且不是句末停顿的空隙才衔接
    player_volume: int = 80

    # ------------------------------------------------------------ I/O
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["profiles"] = [p.to_dict() if isinstance(p, LLMProfile) else p for p in self.profiles]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Config":
        cfg = cls()
        d = dict(d or {})
        profs = d.pop("profiles", None)
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
        if profs:
            cfg.profiles = [LLMProfile.from_dict(p) for p in profs if isinstance(p, dict)]
        if not cfg.profiles:
            cfg.profiles = [LLMProfile()]
        if not any(p.name == cfg.active_profile for p in cfg.profiles):
            cfg.active_profile = cfg.profiles[0].name
        return cfg

    @classmethod
    def load(cls) -> "Config":
        try:
            with open(config_path(), "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except Exception:
            return cls()

    def save(self) -> None:
        try:
            with open(config_path(), "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # 恢复出厂时保留的字段：
    #  · profiles / active_profile —— 大模型接入点（API Key、访问地址、模型名）。
    #    用户明确要求：重置设置绝不能清掉这些，重新填 Key 太折磨人。
    #  · 其余是窗口位置/最近文件这类"使用痕迹"，不算配置，一并保留。
    _RESET_KEEP = ("profiles", "active_profile", "window_geometry",
                   "recent_files", "last_dir", "export_dir",
                   "player_volume", "editor_hsplit")

    def reset_to_defaults(self) -> None:
        """一键恢复出厂默认：**大模型接入点（API Key / 访问地址 / 模型名）原样保留**。

        其余全部回到出厂值。调用方负责 ``save()``。
        """
        fresh = Config()
        for f in type(self).__dataclass_fields__:
            if f in self._RESET_KEEP:
                continue
            setattr(self, f, getattr(fresh, f))

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
        del self.recent_files[self.max_recent:]
