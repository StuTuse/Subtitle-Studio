"""转写引擎层：把「视频/音频 -> 带时间戳的 Cue 列表」统一成一个接口。

支持的后端
----------
1. ``faster-whisper``：本机 GPU/CPU 推理（默认，推荐）。会自动发现本机已下载的
   CTranslate2 模型目录（卡卡字幕助手 VideoCaptioner、HF 缓存、ModelScope 等），
   命中就直接用，不再重复下载。
2. ``whisper.cpp``：调用本机 ggml 模型可执行文件（可选）。
3. ``buzz``：调用本机 Buzz 程序（GUI 版本通常无 CLI，则回退到用 Buzz 自带的
   Python 环境跑 openai-whisper 的 .pt 权重）。
4. ``openai_api``：调用 OpenAI 兼容的 /v1/audio/transcriptions 接口
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


@dataclass
class TranscriptResult:
    cues: List[Cue]
    meta: Dict[str, Any]


# ---------------------------------------------------------------- 模型发现
_MODEL_PATTERNS = [
    # (探测路径, 描述)
    (os.path.expandvars(r"D:\VideoCaptioner\AppData\models"), "卡卡字幕助手"),
    (os.path.expandvars(r"%LOCALAPPDATA%\VideoCaptioner\models"), "卡卡字幕助手"),
    (os.path.expanduser(r"~\.cache\huggingface\hub"), "HuggingFace 缓存"),
    (os.path.expanduser(r"~\.cache\modelscope\hub"), "ModelScope 缓存"),
    (os.path.expandvars(r"%USERPROFILE%\.cache\whisper"), "openai-whisper"),
    (os.path.expandvars(r"%LOCALAPPDATA%\Buzz\Buzz\Cache\models"), "Buzz"),
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


_BUZZ_PY_CACHE: Dict[str, str] = {}


def find_buzz_python() -> str:
    """Buzz 是 PyInstaller 打包的，_internal 里带着 openai-whisper 依赖。

    结果会缓存：目录遍历在 onedir 打包（内含 torch）下并不便宜，
    而设置页与引擎可用性检查都会调用它。
    """
    if "v" in _BUZZ_PY_CACHE:
        return _BUZZ_PY_CACHE["v"]
    found = ""
    cands = [
        os.path.expandvars(r"D:\Buzz\_internal\python.exe"),
        os.path.expandvars(r"D:\Buzz\Buzz\_internal\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Buzz\_internal\python.exe"),
    ]
    for c in cands:
        if os.path.isfile(c):
            found = c
            break
    if not found:
        # 独立 venv 的 python.exe 一定在环境根部（根目录或 Scripts\），
        # 因此只扫 3 层即可判定；Buzz 这类 onedir 打包根本不带 python.exe，
        # 无界遍历（其 _internal 里塞了整个 torch）会卡住界面。
        for root in (r"D:\Buzz", os.path.expandvars(r"%LOCALAPPDATA%\Buzz")):
            if not os.path.isdir(root):
                continue
            base = root.rstrip("\\/").count(os.sep)
            for dp, dn, fn in os.walk(root):
                if dp.count(os.sep) - base >= 3:
                    dn[:] = []
                    continue
                if "python.exe" in fn:
                    found = os.path.join(dp, "python.exe")
                    break
            if found:
                break
    _BUZZ_PY_CACHE["v"] = found
    return found


def find_buzz_pt_models() -> Dict[str, str]:
    d = os.path.expandvars(r"%LOCALAPPDATA%\Buzz\Buzz\Cache\models\whisper")
    out: Dict[str, str] = {}
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".pt"):
                out[f[:-3]] = os.path.join(d, f)
    return out


def find_external_whisper_cli() -> List[str]:
    """卡卡附带的 faster-whisper-xxl.exe 之类的独立 CLI。

    以前用 ``**`` 递归 glob 直扫 LOCALAPPDATA 整棵树，实测 7 秒多——而且这函数
    被设置页构造调用，直接把启动拖慢一大截。改成限深遍历 + 进程内缓存。
    """
    if _ext_cli_cache is not None:
        return _ext_cli_cache
    out: List[str] = []
    roots = [r"D:\VideoCaptioner",
             os.path.expandvars(r"%LOCALAPPDATA%")]
    seen_roots = set()
    for root in roots:
        real = os.path.normcase(os.path.abspath(root)) if root else ""
        if not real or real in seen_roots or not os.path.isdir(root):
            continue
        seen_roots.add(real)
        try:
            _scan_shallow(root, "faster-whisper", 3, out)
        except Exception:
            pass
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
        key = m.lower()
        for cand in discover_ct2_models():
            base = os.path.basename(cand["path"]).lower()
            if base == key or base == f"faster-whisper-{key}" or key in base:
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
        model_path = self.resolve_model(cfg.whisper_model)
        device, compute = _pick_device(cfg, progress)
        if progress:
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
            if "network" in low or "resolve" in low or "offline" in low or "hf client" in low:
                raise TranscribeError(
                    f"本地找不到模型「{cfg.whisper_model}」，且无法联网下载。\n"
                    "请到「设置 → 转写」里改选一个已下载的本地模型。") from e
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
        # 收 segment」封装成 _consume，GPU 失败时整段用 CPU 重来一遍。
        def _consume(mdl) -> Tuple[List[Any], Any]:
            segments, info = mdl.transcribe(audio_path, **kw)
            out: List[Any] = []
            for seg in segments:
                if cancel and cancel():
                    raise TranscribeError("已取消。")
                out.append(seg)
                if progress and total and out:
                    pos = float(out[-1].end)
                    progress(f"识别中 {pos / total * 100:.0f}%", min(0.999, pos / total))
            return out, info

        try:
            seg_list, info = _consume(model)
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
                    seg_list, info = _consume(model)
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

        cues: List[Cue] = []
        for seg in seg_list:
            text = (seg.text or "").strip()
            if not text:
                continue
            words = []
            for w in (seg.words or []):
                words.append({"start": round(float(w.start), 3), "end": round(float(w.end), 3),
                              "word": w.word, "prob": round(float(getattr(w, "probability", 0) or 0), 3)})
            probs = [w["prob"] for w in words if w["prob"]]
            cues.append(Cue(
                start=round(float(seg.start), 3), end=round(float(seg.end), 3),
                text=text, original_text=text, state="asr",
                confidence=round(sum(probs) / len(probs), 3) if probs else None,
                words=words,
            ))
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
        exe = _s.which("whisper-cli") or _s.which("main") or ""
        if not exe:
            raise TranscribeError("未找到 whisper-cli / main.exe。请安装 whisper.cpp 或在 PATH 中提供。")
        model = self.cfg.whisper_model
        if not os.path.isfile(model):
            raise TranscribeError("请选择一个 ggml-*.bin 模型文件。")
        out = audio_path + ".whispercpp.json"
        cmd = [exe, "-m", model, "-f", audio_path, "-oj", "-of", out, "-l",
               self.cfg.language if self.cfg.language != "auto" else "auto"]
        if progress:
            progress("whisper.cpp 识别中…", -1)
        t0 = time.time()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert p.stdout is not None
        for line in p.stdout:
            if cancel and cancel():
                p.kill()
                raise TranscribeError("已取消。")
            m = re.search(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->", line)
            if m and progress:
                progress(f"whisper.cpp: {m.group(1)}", -1)
        p.wait()
        if not os.path.isfile(out):
            raise TranscribeError("whisper.cpp 未产出结果文件。")
        with open(out, "r", encoding="utf-8") as f:
            data = json.load(f)
        cues = []
        for tr in data.get("transcription", []):
            text = (tr.get("text") or "").strip()
            if not text:
                continue
            cues.append(Cue(start=tr.get("offsets", {}).get("from", 0) / 1000.0,
                            end=tr.get("offsets", {}).get("to", 0) / 1000.0,
                            text=text, original_text=text))
        if os.path.isfile(out):
            os.remove(out)
        return TranscriptResult(cues=cues, meta={"engine": self.key, "model": model,
                                                 "elapsed": round(time.time() - t0, 1)})


# -------------------------------------------------------------------- Buzz
class BuzzEngine:
    key = "buzz"
    label = "调用本机 Buzz 的模型（openai-whisper .pt）"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    @staticmethod
    def available() -> bool:
        return bool(find_buzz_python())

    def transcribe(self, audio_path: str, progress: Optional[Progress] = None,
                   cancel: Optional[Cancel] = None) -> TranscriptResult:
        exe = find_buzz_python()
        models = find_buzz_pt_models()
        if not exe:
            pt = ", ".join(sorted(models)) or "（无）"
            raise TranscribeError(
                "无法调用 Buzz：它被打包成图形程序（PyInstaller onedir），"
                "目录里只有 python312.dll，没有可独立调用的 python.exe。\n\n"
                f"Buzz 已下载的 .pt 权重：{pt}\n\n"
                "这些 .pt 无法被 faster-whisper 直接读取，但**同名模型一般都有 "
                "CTranslate2 版本**。请在「设置 → 模型」里改选一个带 "
                "[卡卡字幕助手] 的本地模型，效果等价且速度更快。")
        name = self.cfg.whisper_model or "large-v3-turbo"
        for k in models:
            if name in k:
                name = k
                break
        ckpt = models.get(name) or (next(iter(models.values())) if models else name)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_buzz_worker.py")
        cmd = [exe, script, audio_path, ckpt,
               self.cfg.language if self.cfg.language != "auto" else "auto",
               "1" if self.cfg.word_timestamps else "0"]
        if progress:
            progress(f"Buzz / openai-whisper 识别中（{os.path.basename(ckpt)}）…", -1)
        t0 = time.time()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace", encoding="utf-8",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert p.stdout is not None
        payload, err = "", []
        for line in p.stdout:
            if cancel and cancel():
                p.kill()
                raise TranscribeError("已取消。")
            line = line.rstrip()
            if line.startswith("__JSON__"):
                payload = line[8:]
            elif line:
                err.append(line)
                if progress:
                    progress(line[-70:], -1)
        p.wait()
        if not payload:
            raise TranscribeError("Buzz 转写失败：\n" + "\n".join(err[-12:]))
        data = json.loads(payload)
        cues = [Cue(start=float(s["start"]), end=float(s["end"]),
                    text=(s.get("text") or "").strip(),
                    original_text=(s.get("text") or "").strip())
                for s in data.get("segments", []) if (s.get("text") or "").strip()]
        return TranscriptResult(cues=cues, meta={
            "engine": self.key, "model": ckpt, "language": data.get("language", ""),
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
        with open(audio_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model=self.cfg.openai_transcribe_model, file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"] if self.cfg.word_timestamps else ["segment"],
                language=None if self.cfg.language == "auto" else self.cfg.language,
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
    BuzzEngine.key: BuzzEngine,
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
