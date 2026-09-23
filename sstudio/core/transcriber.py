"""转写引擎层：把「视频/音频 -> 带时间戳的 Cue 列表」统一成一个接口。

支持的后端
----------
1. ``faster-whisper``：本机 GPU/CPU 推理（默认，推荐）。会自动发现本机已下载的
   CTranslate2 模型目录（HuggingFace 缓存、ModelScope、自家模型目录等），
   命中就直接用，不再重复下载。
2. ``whisper.cpp``：调用本机 ggml 模型可执行文件（可选）。
3. ``openai_api``：调用 OpenAI 兼容的 /v1/audio/transcriptions 接口
   （也适用于自建 whisper 服务、Groq、Gemini 兼容层等）。

所有引擎都返回 ``(cues, meta)``，meta 里记录 engine/model/language/耗时。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..core.config import Config, models_dir
from ..core.model import Cue, CueDocument, normalize_cues

Progress = Callable[[str, float], None]        # (message, 0..1 或 -1 表示不确定)
Cancel = Callable[[], bool]

# 外部 CLI 扫描缓存：进程内扫一次就够（见 find_external_whisper_cli）
_ext_cli_cache: Optional[List[str]] = None


def _ext_cli_cache_set(v: List[str]) -> None:
    global _ext_cli_cache
    _ext_cli_cache = v


def ext_cli_cache_reset() -> None:
    """「重新扫描本地模型」按钮调用：清缓存后重扫。"""
    global _ext_cli_cache
    _ext_cli_cache = None


class TranscribeError(RuntimeError):
    pass


# ---------------------------------------------------------------- 模型下载
HF_MIRROR = "https://hf-mirror.com"
MODELSCOPE = "https://modelscope.cn"

# 模块导入即设默认镜像：huggingface_hub 在第一次被 import 时就把 HF_ENDPOINT
# 读进 constants，晚设无效。本模块先于 doctor/faster_whisper 的任何导入路径
# 加载（settings_page、workers 都在模块层 import 本文件），所以放这里最稳。
# 真正的下载地址在每次转写前按设置再校准一次（_apply_download_source）。
os.environ.setdefault("HF_ENDPOINT", HF_MIRROR)


def _ensure_hf_endpoint() -> None:
    """惰性兜底：即使别处抢先 import 了 hub 库，至少把环境变量摆正。"""
    os.environ.setdefault("HF_ENDPOINT", HF_MIRROR)


def _is_net_error(msg: str) -> bool:
    low = msg.lower()
    return any(k in low for k in (
        "network", "resolve", "offline", "hf client", "connecttimeout",
        "timeout", "timed out", "10060", "getaddrinfo", "connection error",
        "readtimeout", "ssl", "proxy"))


# faster-whisper 认的模型名 -> ModelScope 上对应仓库（内容即 CT2 格式，可直接用）。
_MS_REPOS = {n: f"pengzhendong/faster-whisper-{n}" for n in (
    "tiny", "base", "small", "medium", "large-v1", "large-v2",
    "large-v3", "large-v3-turbo")}
_MODEL_ALIASES = {"large": "large-v3", "turbo": "large-v3-turbo"}


def _model_key(s: str) -> str:
    """把 'faster-whisper-large-v3-turbo'/'turbo'/路径末段归一成同一个键。"""
    s = (s or "").strip().lower().replace("models--", "")
    s = os.path.basename(s.rstrip("/\\")).replace("--", "-")
    if s.startswith("faster-whisper-"):
        s = s[len("faster-whisper-"):]
    return _MODEL_ALIASES.get(s, s)


def _ms_repo_for(model: str) -> str:
    """把 'large-v3-turbo'/'large'/'turbo' 等映射到 ModelScope 仓库名。"""
    return _MS_REPOS.get(_model_key(model), "")


def _apply_hf_mirror(enabled: bool) -> str:
    """（兼容旧入口）True=走 hf-mirror，False=官方。新代码请用 _apply_download_source。"""
    return _apply_download_source("hf-mirror" if enabled else "official")


def _apply_download_source(source: str) -> str:
    """按设置切换 HuggingFace 侧的下载地址，返回生效的 endpoint。

    环境变量 HF_ENDPOINT 只对「还没 import 过 hub 库」的进程有效；库已
    加载时直接改 constants（snapshot_download 的 endpoint 默认取自这里），
    两条路都堵上。

    走镜像时还要关掉 Xet 存储：新 huggingface_hub 优先用 Xet 协议下载，
    它固定连 cas-server.xethub.hf.co —— 镜像不代理这个域名，结果是
    「镜像明明通了却报 401」（实测）。关掉后退回普通 HTTP 分片下载。
    """
    ep = {"hf-mirror": HF_MIRROR, "modelscope": HF_MIRROR}.get(
        source, "https://huggingface.co")
    disable_xet = ep != "https://huggingface.co"
    os.environ["HF_ENDPOINT"] = ep
    os.environ["HF_HUB_DISABLE_XET"] = "1" if disable_xet else "0"
    try:
        import huggingface_hub
        consts = getattr(huggingface_hub, "constants", None)
        if consts is not None:
            if getattr(consts, "ENDPOINT", "") != ep:
                consts.ENDPOINT = ep
                if hasattr(consts, "HUGGINGFACE_CO_URL_TEMPLATE"):
                    consts.HUGGINGFACE_CO_URL_TEMPLATE = \
                        ep + "/{repo_id}/resolve/{revision}/{filename}"
            consts.HF_HUB_DISABLE_XET = disable_xet
    except Exception:
        pass
    return ep


def _download_from_modelscope(model: str, dest_root: str,
                              progress: Optional[Progress] = None,
                              cancel: Optional[Cancel] = None) -> str:
    """从 ModelScope 拉取 CT2 模型目录，返回可直接喂给 faster-whisper 的路径。

    为什么自己写：国内网络下 huggingface.co 直连超时、hf-mirror 大文件只有
    ~0.3MB/s（实测 1.6GB 要 90 分钟，且 hub 下载器会直接卡死），而 ModelScope
    同一个模型实测 18MB/s。用它的 HTTP 直链 + 标准 HF 缓存目录结构落地，
    discover_ct2_models 之后就能照常发现，与 HF 下载的模型完全等价。
    """
    import httpx

    repo = _ms_repo_for(model)
    if not repo:
        raise TranscribeError(
            f"「{model}」不在内置下载列表里。\n"
            "请改用 tiny/base/small/medium/large-v3/large-v3-turbo 之一，"
            "或在「设置 → 模型」里直接填本地模型目录。")
    files = list(_CT2_FILES) + ["preprocessor_config.json", "vocabulary.json"]
    cache_root = os.path.join(dest_root, "models--" + repo.replace("/", "--"))
    # 快照目录名用仓库名而不是随意字符串：discover/resolve 靠路径末段匹配
    # 模型名，叫 "modelscope" 会让下次 resolve 认不出来、又下一遍 1.5GB。
    snap = os.path.join(cache_root, "snapshots", repo.split("/")[-1])
    os.makedirs(snap, exist_ok=True)
    total_bytes = 0.0
    done_bytes = 0.0
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, read=120.0),
                          follow_redirects=True) as cli:
            sizes = {}
            for fn in files:
                u = f"{MODELSCOPE}/models/{repo}/resolve/master/{fn}"
                try:
                    with cli.stream("GET", u) as r:
                        if r.status_code == 200:
                            sizes[fn] = int(r.headers.get("content-length") or 0)
                        elif r.status_code == 404:
                            sizes[fn] = 0       # 该仓库没这个文件：可选，跳过
                except Exception:
                    sizes[fn] = 0
            total_bytes = float(sum(sizes.values())) or 1.0
            for fn in files:
                if cancel and cancel():
                    raise TranscribeError("已取消。")
                target = os.path.join(snap, fn)
                expect = sizes.get(fn, 0)
                if expect and os.path.isfile(target) and os.path.getsize(target) == expect:
                    done_bytes += expect       # 已在本地：断点续传的核心
                    continue
                if not expect:
                    continue
                url = f"{MODELSCOPE}/models/{repo}/resolve/master/{fn}"
                tmp = target + ".part"
                pos = os.path.getsize(tmp) if os.path.isfile(tmp) else 0
                hdr = {"Range": f"bytes={pos}-"} if pos else {}
                with cli.stream("GET", url, headers=hdr) as r:
                    if r.status_code not in (200, 206):
                        raise TranscribeError(
                            f"ModelScope 下载失败（HTTP {r.status_code}）：{url}")
                    if pos and r.status_code == 200:
                        pos = 0              # 服务器不认 Range，重来
                    mode = "ab" if (pos and r.status_code == 206) else "wb"
                    got = pos
                    last_ui = 0.0
                    with open(tmp, mode) as fh:
                        for chunk in r.iter_bytes(1024 * 512):
                            if cancel and cancel():
                                raise TranscribeError("已取消。")
                            fh.write(chunk)
                            got += len(chunk)
                            done_bytes += len(chunk)
                            now = time.perf_counter()
                            if progress and now - last_ui > 0.25:
                                last_ui = now
                                mb = got / 1048576.0
                                progress(
                                    f"下载模型 {fn} {mb:.0f}/{expect/1048576:.0f} MB"
                                    f"（合计约 {done_bytes/1048576:.0f}/"
                                    f"{total_bytes/1048576:.0f} MB，完成后永久复用）", -1)
                os.replace(tmp, target)
    except TranscribeError:
        raise
    except ImportError as e:
        raise TranscribeError(f"缺少 httpx 依赖，无法下载模型：{e}") from e
    except Exception as e:
        if _is_net_error(str(e)):
            raise TranscribeError(
                f"从 ModelScope 下载「{model}」失败：网络不通。\n"
                "可换「设置 → 模型下载源」为 hf-mirror，或手动下载模型目录后在设置里填路径。") from e
        raise TranscribeError(f"从 ModelScope 下载失败：{e}") from e
    if not all(os.path.isfile(os.path.join(snap, f)) for f in _CT2_FILES):
        raise TranscribeError(
            f"ModelScope 仓库 {repo} 缺少 CT2 必需文件，换其它模型或下载源试试。")
    return snap


@dataclass
class TranscriptResult:
    cues: List[Cue]
    meta: Dict[str, Any]


# ---------------------------------------------------------------- 模型发现
_MODEL_PATTERNS = [
    # (探测路径, 描述)——只扫标准缓存位置与自家模型目录
    (os.path.expanduser(r"~\.cache\huggingface\hub"), "HuggingFace 缓存"),
    (os.path.expanduser(r"~\.cache\modelscope\hub"), "ModelScope 缓存"),
    (os.path.expandvars(r"%USERPROFILE%\.cache\whisper"), "openai-whisper"),
]

_CT2_FILES = ("model.bin", "tokenizer.json", "config.json")


def discover_ct2_models() -> List[Dict[str, str]]:
    """扫描本机已有的 CTranslate2 模型目录（可直接喂给 faster-whisper）。"""
    found: List[Dict[str, str]] = []
    seen = set()
    roots = [models_dir()] + [p for p, _ in _MODEL_PATTERNS]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            # 目录内直接是 CT2，或是 models--xxx/snapshots/rev 结构
            cand = d
            if name.startswith("models--"):
                snap = os.path.join(d, "snapshots")
                if not os.path.isdir(snap):
                    continue
                revs = sorted(os.listdir(snap))
                if not revs:
                    continue
                cand = os.path.join(snap, revs[-1])
                name = name.replace("models--", "").replace("--", "/")
            if all(os.path.isfile(os.path.join(cand, f)) for f in _CT2_FILES):
                ap = os.path.abspath(cand)
                if ap in seen:
                    continue
                seen.add(ap)
                label = name.split("/")[-1]
                src = next((s for p, s in _MODEL_PATTERNS
                            if root == p or root.startswith(p)), "本地")
                found.append({"name": f"{label}  [{src}]", "id": ap, "path": ap,
                              "size": _dir_size_mb(cand)})
    return found


def _dir_size_mb(path: str) -> float:
    try:
        tot = 0
        for dp, _dn, fn in os.walk(path):
            for f in fn:
                try:
                    tot += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        return round(tot / 1048576.0, 1)
    except Exception:
        return 0.0


def discover_ggml_models() -> List[Dict[str, str]]:
    """whisper.cpp 的 ggml-*.bin。"""
    out = []
    roots = [os.path.expanduser("~"), "D:\\", os.path.expandvars(r"%LOCALAPPDATA%")]
    seen = set()
    for r in roots:
        if not os.path.isdir(r):
            continue
        for dp, dn, fn in os.walk(r):
            dn[:] = [d for d in dn if d not in ("Windows", "node_modules", "__pycache__",
                                                ".git", "venv", "site-packages")]
            if dp[len(r):].count(os.sep) > 6:
                dn[:] = []
                continue
            for f in fn:
                if f.startswith("ggml") and f.endswith(".bin"):
                    p = os.path.join(dp, f)
                    if p in seen:
                        continue
                    seen.add(p)
                    out.append({"name": f, "id": p, "path": p, "size": _dir_size_mb(dp)})
    return out


def find_external_whisper_cli() -> List[str]:
    """找独立版 whisper CLI（whisper-cli.exe / main.exe 之类）。

    以前用 ``**`` 递归 glob 直扫 LOCALAPPDATA 整棵树，实测 7 秒多——而且这函数
    被设置页构造调用，直接把启动拖慢一大截。改成限深遍历 + 进程内缓存。
    """
    if _ext_cli_cache is not None:
        return _ext_cli_cache
    out: List[str] = []
    roots = [os.path.expandvars(r"%LOCALAPPDATA%")]
    seen_roots = set()
    truncated = False
    for root in roots:
        real = os.path.normcase(os.path.abspath(root)) if root else ""
        if not real or real in seen_roots or not os.path.isdir(root):
            continue
        seen_roots.add(real)
        try:
            visited = _scan_shallow(root, "faster-whisper", 3, out)
            if visited > _EXT_MAX_VISIT:
                truncated = True
        except Exception:
            pass
    # 被条目上限截断过时不缓存：否则"只扫了一半"的结果会被当成完整答案，
    # 后面真正装了 whisper 的目录永远扫不到，除非重启。
    if not truncated:
        _ext_cli_cache_set(out)
    return out


_EXT_MAX_VISIT = 6000          # 每次遍历最多看这么多个目录项，防止意外巨树


def _scan_shallow(root: str, name_prefix: str, max_depth: int,
                  out: List[str]) -> int:
    """深度受限地找 name_prefix*.exe；返回访问过的条目数（超限即停）。"""
    visited = 0
    prefix = name_prefix.lower()
    stack = [(root, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            visited += 1
            if visited > _EXT_MAX_VISIT:
                return visited
            try:
                if e.is_file(follow_symlinks=False) and \
                        e.name.lower().startswith(prefix) and \
                        e.name.lower().endswith(".exe"):
                    out.append(e.path)
                elif depth < max_depth and e.is_dir(follow_symlinks=False):
                    ln = e.name.lower()
                    # 常见巨型/无关目录直接跳过，省下绝大部分遍历时间
                    if ln in ("node_modules", "cache", "caches", "packages",
                              "temp", "tmp", "pip", "pip-selfcheck.json"):
                        continue
                    stack.append((e.path, depth + 1))
            except OSError:
                continue
    return visited


# ------------------------------------------------------------ faster-whisper
class FasterWhisperEngine:
    key = "faster-whisper"
    label = "faster-whisper（本机推理，推荐）"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @staticmethod
    def available() -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def resolve_model(self, model: str) -> str:
        """把 'large-v3-turbo' 这类名字解析成本地路径；找不到就原样返回（联网下载）。"""
        m = (model or "").strip()
        if not m:
            return "large-v3-turbo"
        if os.path.isdir(m):
            return m
        key = _model_key(m)
        cands = discover_ct2_models()
        # 必须按归一化后的「全名」相等来配：老写法先精确比 basename 再退
        # 子串，导致用户选 large-v3 时子串命中 large-v3-turbo 目录——
        # 以为在用满血 v3，实际跑的是 turbo。宁可回退下载也不能认错。
        # 名字要同时看 path 末段和发现项名称：HF 官方缓存的 path 末段是
        # revision 哈希，光比 path 会把本机已有的模型认不出来、重复下载。
        for cand in cands:
            label = (cand.get("name") or "").split()[0]
            if key in (_model_key(cand["path"]), _model_key(label)):
                return cand["path"]
        return m

    def transcribe(self, audio_path: str, progress: Optional[Progress] = None,
                   cancel: Optional[Cancel] = None) -> TranscriptResult:
        try:
            from faster_whisper import WhisperModel
        except Exception as e:
            raise TranscribeError(
                "未安装 faster-whisper。请在终端执行：\n\n    pip install faster-whisper\n\n"
                f"（原始错误：{e}）") from e

        from . import cuda_rt

        cfg = self.cfg
        source = str(getattr(cfg, "model_source", "modelscope") or "modelscope")
        ep = _apply_download_source(source)
        model_path = self.resolve_model(cfg.whisper_model)
        device, compute = _pick_device(cfg, progress)
        if not os.path.isdir(model_path):
            # 本地没有：按设置从 ModelScope / hf-mirror / 官方下载
            if source == "modelscope" and _ms_repo_for(cfg.whisper_model):
                if progress:
                    progress("本地未找到模型，正在从 ModelScope 下载"
                             "（首次约 1.5 GB，完成后永久复用）…", -1)
                model_path = _download_from_modelscope(
                    cfg.whisper_model,
                    getattr(cfg, "model_dir", "") or models_dir(),
                    progress=progress, cancel=cancel)
            elif progress:
                progress(f"本地未找到模型，正在从 {ep.replace('https://', '')} 下载"
                         "（首次约 1.5 GB，完成后永久复用）…", -1)
        if progress:
            if os.path.isdir(model_path):
                progress(f"加载模型 {os.path.basename(str(model_path))}（{device}/{compute}）…", -1)
        t0 = time.time()
        try:
            model, device, compute, note = _load_model(
                WhisperModel, model_path, device, compute, cfg)
            if note and progress:
                progress(note, -1)
        except TranscribeError:
            raise
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            if "cublas" in low or "cudart" in low or "cudnn" in low or "dll is not found" in low:
                raise TranscribeError(
                    "GPU 推理失败：缺少 CUDA 12 运行时（cublas64_12.dll）。\n\n"
                    f"{cuda_rt.describe()}\n\n"
                    "临时办法：在「设置 → 计算设备」改成 cpu 先跑通。") from e
            if _is_net_error(msg):
                hint = ("请检查网络，或在「设置 → 模型下载源」换一项"
                        f"（当前：{source}），也可改选已下载的本地模型。")
                raise TranscribeError(
                    f"本地找不到模型「{cfg.whisper_model}」，且无法联网下载。\n{hint}") from e
            raise TranscribeError(f"模型加载失败：{msg}") from e

        prompt = (cfg.initial_prompt or "").strip()
        kw: Dict[str, Any] = dict(
            beam_size=max(1, int(cfg.beam_size)),
            vad_filter=bool(cfg.vad),
            word_timestamps=bool(cfg.word_timestamps),
            condition_on_previous_text=bool(cfg.condition_on_previous_text),
            no_speech_threshold=float(cfg.no_speech_threshold),
            task="translate" if cfg.language.startswith("translate:") else "transcribe",
        )
        lang = cfg.language
        if lang.startswith("translate:"):
            lang = lang.split(":", 1)[1]
        if lang and lang != "auto":
            kw["language"] = lang
        if prompt:
            kw["initial_prompt"] = prompt[:440]
        if cfg.vad:
            kw["vad_parameters"] = {"min_silence_duration_ms": 300, "speech_pad_ms": 200}

        if progress:
            progress("模型就绪，开始识别…", 0.0)
        total = _audio_duration(audio_path)

        # faster-whisper 返回的是惰性生成器，缺 cublas 这类运行时错误要等到
        # 真正迭代时才抛出；而完整消费一次又不该重复跑模型。因此把「跑一次 +
        # 边跑边转成 Cue」封装成 _consume，GPU 失败时整段用 CPU 重来一遍。
        # 注意不在这一步缓存 segment 列表：大视频（4h）的 segment 对象与
        # 随后的 Cue 列表同时驻留会让峰值内存翻倍——直接边迭代边建 Cue。
        def _consume(mdl) -> Tuple[List[Cue], Any]:
            segments, info = mdl.transcribe(audio_path, **kw)
            cues: List[Cue] = []
            for seg in segments:
                if cancel and cancel():
                    raise TranscribeError("已取消。")
                text = (seg.text or "").strip()
                if text:
                    words = []
                    for w in (seg.words or []):
                        words.append({"start": round(float(w.start), 3),
                                      "end": round(float(w.end), 3),
                                      "word": w.word,
                                      "prob": round(float(getattr(w, "probability", 0) or 0), 3)})
                    probs = [w["prob"] for w in words if w["prob"]]
                    cues.append(Cue(
                        start=round(float(seg.start), 3), end=round(float(seg.end), 3),
                        text=text, original_text=text, state="asr",
                        confidence=round(sum(probs) / len(probs), 3) if probs else None,
                        words=words,
                    ))
                if progress and total and cues:
                    pos = float(seg.end)
                    progress(f"识别中 {pos / total * 100:.0f}%", min(0.999, pos / total))
            return cues, info

        try:
            cues, info = _consume(model)
        except TranscribeError:
            raise
        except Exception as e:  # noqa: BLE001
            if _looks_like_gpu_error(e) and device == "cuda" and \
                    getattr(cfg, "auto_cpu_fallback", True):
                cuda_rt._result = None
                if progress:
                    progress("GPU 运行库缺失，改用 CPU 重新识别…", -1)
                try:
                    model, device, compute, note = _load_model(
                        WhisperModel, model_path, "cpu", "int8", cfg)
                    cues, info = _consume(model)
                except TranscribeError:
                    raise
                except Exception as e2:  # noqa: BLE001
                    raise TranscribeError(
                        f"CPU 兜底同样失败：{e2}\n\n{cuda_rt.describe()}") from e2
            elif _looks_like_gpu_error(e):
                raise TranscribeError(
                    f"GPU 识别失败：{e}\n\n{cuda_rt.describe()}\n\n"
                    "或在「设置 → 计算设备」改成 cpu。") from e
            else:
                raise TranscribeError(
                    f"识别失败：{e}\n\n可尝试：设置 → 计算设备改成 cpu，"
                    "或把量化精度改成 int8。") from e

        if progress:
            progress("整理时间轴…", 0.999)

        doc = CueDocument(source_video=audio_path, duration=total,
                          language=getattr(info, "language", "") or "", cues=cues)
        normalize_cues(doc)
        meta = {
            "engine": self.key, "model": str(model_path),
            "device": device, "compute": compute,
            "language": getattr(info, "language", ""),
            "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
            "elapsed": round(time.time() - t0, 1), "audio_duration": round(total, 1),
            "speed": round(total / max(0.01, time.time() - t0), 2),
        }
        return TranscriptResult(cues=doc.cues, meta=meta)


def _looks_like_gpu_error(e: BaseException) -> bool:
    low = str(e).lower()
    return any(k in low for k in ("cublas", "cudart", "cudnn", "dll is not found",
                                  "cuda error", "no cuda platforms available",
                                  "cuda libraries"))


def _pick_device(cfg: Config, progress: Optional[Progress] = None) -> Tuple[str, str]:
    """决定 device / compute_type。

    ``auto`` 时先确认 GPU 真的可用：``get_cuda_device_count()`` 只看驱动，
    真正的坑是 CUDA 12 运行库缺失（torch cu13 环境下极常见），这里提前探测并
    自动降级，避免用户看到一句看不懂的 cublas 报错。
    """
    from . import cuda_rt

    dev, compute = cfg.whisper_device, cfg.whisper_compute
    if dev in ("auto", "") or dev == "cuda":
        have_gpu = False
        try:
            import ctranslate2 as ct2
            have_gpu = ct2.get_cuda_device_count() > 0
        except Exception:
            have_gpu = False
        if have_gpu:
            rt = cuda_rt.register(getattr(cfg, "cuda_rt_dir", "") or None)
            if not rt.usable or not cuda_rt.probe_loadable(rt):
                if dev == "cuda":
                    # 用户手动指定了 cuda：保留选择，让上层抛出可读的错误
                    pass
                else:
                    if progress:
                        progress("未找到 CUDA 12 运行时，自动改用 CPU…", -1)
                    dev, compute = "cpu", (compute if compute not in ("auto", "") else "int8")
                    return dev, "int8" if compute in ("auto", "") else compute
            else:
                dev = "cuda"
        else:
            if dev == "auto":
                dev = "cpu"
    if dev == "cpu" and compute in ("auto", ""):
        compute = "int8"
    if dev == "cuda" and compute in ("auto", ""):
        compute = "float16"
    return dev, compute


def _load_model(WhisperModel, model_path: str, device: str, compute: str, cfg: Config):
    """加载模型；GPU 实际不可用时自动回退 CPU。返回 (model, device, compute, note)。"""
    from . import cuda_rt

    def attempt(dev: str, comp: str):
        return WhisperModel(
            str(model_path), device=dev, compute_type=comp,
            cpu_threads=int(getattr(cfg, "whisper_threads", 0) or 0),
            download_root=getattr(cfg, "model_dir", "") or models_dir(),
        )

    try:
        return attempt(device, compute), device, compute, ""
    except Exception as e:
        low = str(e).lower()
        gpu_broken = device == "cuda" and (
            "cublas" in low or "cudart" in low or "cudnn" in low
            or "dll is not found" in low)
        if gpu_broken and getattr(cfg, "auto_cpu_fallback", True):
            cuda_rt._result = None            # 让下次重新探测
            note = ("GPU 不可用，已改用 CPU：" + cuda_rt.describe())
            m = attempt("cpu", "int8")
            return m, "cpu", "int8", note
        raise


def _audio_duration(path: str) -> float:
    from .media import probe
    try:
        return float(probe(path).duration or 0.0)
    except Exception:
        return 0.0


# --------------------------------------------------------------- whisper.cpp
class WhisperCppEngine:
    key = "whisper.cpp"
    label = "whisper.cpp（CPU 友好）"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def transcribe(self, audio_path: str, progress: Optional[Progress] = None,
                   cancel: Optional[Cancel] = None) -> TranscriptResult:
        import shutil as _s
        exe = _s.which("whisper-cli") or ""
        if not exe:
            # 老版 whisper.cpp 的可执行文件就叫 main.exe——但 PATH 里任何
            # 程序的 main.exe 都会命中，必须同目录有 whisper.dll 才认
            cand = _s.which("main")
            if cand and (os.path.isfile(os.path.join(os.path.dirname(cand), "whisper.dll"))
                         or os.path.isfile(os.path.join(os.path.dirname(cand), "libwhisper.dll"))):
                exe = cand
        if not exe:
            raise TranscribeError("未找到 whisper-cli。请安装 whisper.cpp 或在 PATH 中提供。")
        model = self.cfg.whisper_model
        if not os.path.isfile(model):
            raise TranscribeError("请选择一个 ggml-*.bin 模型文件。")
        out = audio_path + ".whispercpp.json"
        # 上次运行（取消/崩溃）可能留下旧结果：不清掉的话，本次即使
        # whisper-cli 没产出新文件也会把**上一次的字幕**当新结果解析成功。
        if os.path.isfile(out):
            try:
                os.remove(out)
            except OSError:
                pass
        lang = self.cfg.language or "auto"
        task = "transcribe"
        if lang.startswith("translate:"):
            # 与 faster-whisper 引擎同一套约定：translate:xx 表示翻译成 xx 语
            task = "translate"
            lang = lang.split(":", 1)[1] or "en"
        cmd = [exe, "-m", model, "-f", audio_path, "-oj", "-of", out, "-l",
               lang if lang != "auto" else "auto"]
        if task == "translate":
            cmd += ["-tr"]
        if progress:
            progress("whisper.cpp 识别中…", -1)
        t0 = time.time()
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, errors="replace",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            raise TranscribeError(f"无法启动 whisper.cpp：{e}")
        assert p.stdout is not None
        try:
            for line in p.stdout:
                if cancel and cancel():
                    p.kill()
                    raise TranscribeError("已取消。")
                m = re.search(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->", line)
                if m and progress:
                    progress(f"whisper.cpp: {m.group(1)}", -1)
            p.wait()
            # stdout 读毕但 cancel 在最后一段进度后才发生：补一次检查，
            # 否则取消请求被"stdout 已无行可读"吞掉，白等到底
            if cancel and cancel():
                raise TranscribeError("已取消。")
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        if not os.path.isfile(out):
            raise TranscribeError("whisper.cpp 未产出结果文件。")
        try:
            with open(out, "r", encoding="utf-8") as f:
                data = json.load(f)
        finally:
            # 取消/解析异常路径也要清掉临时结果，否则泄漏在音频旁
            try:
                os.remove(out)
            except OSError:
                pass
        cues = []
        for tr in data.get("transcription", []):
            text = (tr.get("text") or "").strip()
            if not text:
                continue
            off = tr.get("offsets") or {}      # 坏输出里 offsets 可能是 null
            try:
                st = float(off.get("from", 0)) / 1000.0
                en = float(off.get("to", 0)) / 1000.0
            except (TypeError, ValueError):
                continue
            cues.append(Cue(start=st, end=en, text=text, original_text=text))
        return TranscriptResult(cues=cues, meta={"engine": self.key, "model": model,
                                                 "elapsed": round(time.time() - t0, 1)})


# ------------------------------------------------------------ OpenAI 兼容 API
class OpenAIApiEngine:
    key = "openai_api"
    label = "调用云端语音转写 API（Whisper API 等）"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def transcribe(self, audio_path: str, progress: Optional[Progress] = None,
                   cancel: Optional[Cancel] = None) -> TranscriptResult:
        from .llm import load_client
        prof = self.cfg.profile()
        client = load_client(prof)
        if progress:
            progress("上传音频到云端转写…", 0.2)
        # 与 faster-whisper 引擎同一套约定：translate:xx 表示「翻译成 xx 语」；
        # API 的 language 参数只认纯语言码，带前缀直接 400
        lang = self.cfg.language or "auto"
        if lang.startswith("translate:"):
            lang = lang.split(":", 1)[1] or "en"
        with open(audio_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model=self.cfg.openai_transcribe_model, file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"] if self.cfg.word_timestamps else ["segment"],
                language=None if lang == "auto" else lang,
            )
        cues: List[Cue] = []
        for s in getattr(resp, "segments", None) or []:
            text = (s.get("text") if isinstance(s, dict) else getattr(s, "text", "")).strip()
            if not text:
                continue
            cues.append(Cue(
                start=float(s.get("start") if isinstance(s, dict) else s.start),
                end=float(s.get("end") if isinstance(s, dict) else s.end),
                text=text, original_text=text))
        return TranscriptResult(cues=cues, meta={
            "engine": self.key, "model": self.cfg.openai_transcribe_model,
            "language": getattr(resp, "language", "")})


ENGINES: Dict[str, Any] = {
    FasterWhisperEngine.key: FasterWhisperEngine,
    WhisperCppEngine.key: WhisperCppEngine,
    OpenAIApiEngine.key: OpenAIApiEngine,
}


def get_engine(cfg: Config):
    cls = ENGINES.get(cfg.asr_engine, FasterWhisperEngine)
    return cls(cfg)


def transcribe(audio_path: str, cfg: Config, progress: Optional[Progress] = None,
               cancel: Optional[Cancel] = None) -> TranscriptResult:
    eng = get_engine(cfg)
    if cfg.asr_engine == FasterWhisperEngine.key and not FasterWhisperEngine.available():
        raise TranscribeError("未安装 faster-whisper：pip install faster-whisper")
    return eng.transcribe(audio_path, progress=progress, cancel=cancel)
