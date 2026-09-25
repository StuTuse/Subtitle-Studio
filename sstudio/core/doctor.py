# -*- coding: utf-8 -*-
"""核心组件体检 + 一键修复（首启向导与设置页「环境体检」共用）。

设计原则
--------
* **检查纯本机、秒级完成**：不联网、不扫描大目录，结果可缓存。
* **修复=标准 pip 安装**：只装自己的运行依赖（faster-whisper / PyAV /
  CUDA 运行库 wheel），走多镜像（清华 → 阿里 → 官方），自动跟随系统代理。
  不再借用任何第三方软件的私有目录（那是历史包袱，已在 1.3.0 剔除）。
* 每个检查项有 ``id``，修复结果可复核（修完立刻重查该项）。

检查项分级
----------
required  缺了核心流程跑不通（PyQt5 界面依赖在打包版里恒真；源码运行才可能缺）
recommend 强烈建议（faster-whisper 本地转写、PyAV 解码兜底）
optional  锦上添花（CUDA 12 运行库 → GPU 加速；ffmpeg → 抽音频更快更稳）
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .i18n import S

# ---------------------------------------------------------------- 镜像
PIP_INDEXES = [
    ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里镜像", "https://mirrors.aliyun.com/pypi/simple"),
    ("官方源", None),            # None = 不加 -i 参数
]

# 大 wheel（torch 系列 ~2.5GB）的总时长余量（秒）；--timeout 是"单次
# 网络请求无数据就断"的秒数——防的是镜像黑洞/半死代理下 pip 挂死。
PIP_TIMEOUT = 1200
PIP_NET_TIMEOUT = 30


def _pip_base_args() -> List[str]:
    """python -m pip 是最稳的调用方式（不依赖 PATH 里有 pip.exe）。

    打包版里 ``sys.executable`` 是软件自身的 exe，不认识 ``-m pip`` ——
    必须找到机器上真实的 python.exe。找不到就返回空，由 pip_install 报告。
    """
    if getattr(sys, "frozen", False):
        from . import cuda_rt
        pys = cuda_rt.system_pythons()
        return [pys[0], "-m", "pip"] if pys else []
    return [sys.executable, "-m", "pip"]


def _sys_proxy_env() -> Dict[str, str]:
    """读取 Windows 系统代理（Internet Settings），交给 pip 子进程。

    Clash 等代理默认不开「为 WinINET 写环境变量」，所以这里主动读注册表；
    读不到就什么都不加（直连，国内镜像本来也不需要代理）。
    """
    env: Dict[str, str] = {}
    if os.name != "nt":
        return env
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion"
                            r"\Internet Settings") as k:
            enable, _t = winreg.QueryValueEx(k, "ProxyEnable")
            if not enable:
                return env
            server, _t = winreg.QueryValueEx(k, "ProxyServer")
            server = str(server).strip()
            if not server:
                return env
            if "=" in server:          # "http=...;https=..." 形式
                for part in server.split(";"):
                    if part.lower().startswith("https=") or part.lower().startswith("http="):
                        server = part.split("=", 1)[1]
                        break
            if not server.startswith("http"):
                server = "http://" + server
            env["HTTP_PROXY"] = env["HTTPS_PROXY"] = server
    except Exception:
        pass
    return env


def pip_install(pkgs: List[str], progress: Optional[Callable[[str, float], None]] = None,
                log: Optional[Callable[[str], None]] = None,
                cancel: Optional[Callable[[], bool]] = None) -> Tuple[bool, str]:
    """按镜像顺序安装，返回 (是否成功, 汇总消息)。pkgs 传『包名[>=版本]』。

    进度是「阶段级」的（0..1 之间按镜像数推进）；pip 本身的下载百分比
    通过日志行透出，不解析（pip 进度行带 \r，解析得不偿失）。
    """
    env = dict(os.environ)
    env.update(_sys_proxy_env())
    env.setdefault("PYTHONIOENCODING", "utf-8")
    errs: List[str] = []
    base = _pip_base_args()
    if not base:
        msg = (S("本机没有找到可用的 Python 环境，无法自动安装。\n"
                 "请先安装 Python 3.10+（勾选 Add to PATH），再打开体检窗口重试。",
                 "No usable Python environment found — cannot install automatically.\n"
                 "Install Python 3.10+ first (check Add to PATH), then reopen "
                 "the environment check and retry."))
        if log:
            log(msg)
        return False, msg
    for i, (name, idx) in enumerate(PIP_INDEXES):
        if cancel and cancel():
            return False, S("已取消。", "Cancelled.")
        # 复用循环前算好的 base：frozen 下每次重建都会重新扫系统 python
        # （逐个起子进程探测，单个可耗 10s+），3 镜像 × N 探测纯属浪费，
        # 且不同镜像间可能落到不同解释器
        cmd = base + ["install", "--no-input", "--disable-pip-version-check",
                      f"--timeout={PIP_NET_TIMEOUT}",
                      f"--retries={len(PIP_INDEXES)}", *pkgs]
        if idx:
            cmd += ["-i", idx]
        label = name
        if progress:
            progress(S(f"正在通过{label}安装 {' '.join(pkgs)} …",
                       f"Installing {' '.join(pkgs)} via {label} …"),
                     i / (len(PIP_INDEXES) + 1))
        if log:
            log(f"$ {' '.join(cmd)}")
        t0 = time.time()
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", encoding="utf-8", env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            errs.append(f"{label}: {e}")
            continue
        assert p.stdout is not None
        killed = False
        # 逐行读但检查取消/总时长：以前 for line in p.stdout 在镜像黑洞
        # 下永久阻塞（模态窗按钮全禁用 → 只能任务管理器杀进程）。
        for line in p.stdout:
            if cancel and cancel():
                p.kill()
                killed = True
                break
            if time.time() - t0 > PIP_TIMEOUT:
                p.kill()
                killed = True
                errs.append(S(f"{label}: 超过 {PIP_TIMEOUT}s 未完成，已终止",
                              f"{label}: no completion within {PIP_TIMEOUT}s — terminated"))
                break
            line = line.rstrip()
            if not line:
                continue
            # 错误采集与 log 透传并行：以前 elif 串着，调用方一传 log
            # （两个 UI 恒传），真实报错就收集不到，"所有镜像都失败了"
            # 只剩退出码，无从排障
            if line.startswith(("ERROR", "error:")):
                errs.append(f"{label}: {line[:200]}")
            if log:
                log(line)
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        dt = time.time() - t0
        if killed and cancel and cancel():
            return False, S("已取消。", "Cancelled.")
        if killed:
            continue
        if p.returncode == 0:
            if progress:
                progress(S(f"{' '.join(pkgs)} 安装成功（{label}，{dt:.0f}s）",
                           f"{' '.join(pkgs)} installed ({label}, {dt:.0f}s)"), 1.0)
            return True, S(f"已通过{label}安装（{dt:.0f}s）",
                           f"Installed via {label} ({dt:.0f}s)")
        errs.append(S(f"{label}: pip 退出码 {p.returncode}（{dt:.0f}s）",
                      f"{label}: pip exit code {p.returncode} ({dt:.0f}s)"))
    return False, S("所有镜像都失败了：\n", "All mirrors failed:\n") + "\n".join(errs[-6:])


# ---------------------------------------------------------------- 检查项
@dataclass
class CheckItem:
    id: str
    title: str
    why: str                       # 缺了会怎样（用户视角）
    level: str                     # required | recommend | optional
    ok: bool = False
    detail: str = ""               # 探测到的版本/路径
    fix_pkgs: List[str] = field(default_factory=list)   # pip 包规约；空 = 无 pip 修复
    fix_note: str = ""             # 修复按钮文案；空 = 无法在此窗口修复

    @property
    def fixable(self) -> bool:
        # 打包版（frozen）跑在自身 exe 里，pip 装到系统 Python 的
        # site-packages 后本进程 find_spec 依旧 None——按钮按了"安装成功"
        # 但检查永远红，死循环。frozen 下 pip 类修复一律不给按钮，只留
        # 文字指引（CUDA 运行库除外：按路径找 DLL，装哪儿都能挂上）。
        if getattr(sys, "frozen", False) and self.id != "cuda12":
            return False
        return bool(self.fix_pkgs)


def _mod_version(mod_name: str) -> str:
    try:
        import importlib
        m = importlib.import_module(mod_name)
        return str(getattr(m, "__version__", "") or getattr(m, "VERSION", "") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------- CUDA 预探测
# 机器级坑：Qt Multimedia（DirectShow）激活过的进程里，ctranslate2 的
# CUDA 枚举（get_cuda_device_count）会 access violation（上游三方冲突：
# Qt 后端 × NVIDIA 驱动 × ctranslate2）。播放器 shutdown 之后枚举恢复安全，
# 但向导体检页打开时主窗口/播放器已经建好——不能指望那时再摘后端。
# 因此：CUDA 探测只在【进程早期、任何媒体后端激活之前】做一次并缓存，
# check_all 一律读缓存值；settings_page/向导的重查（force）也走这条缓存，
# 修复后的复核走「重新检查」按钮时同样安全（不再现场枚举 CUDA）。
_GPU_COUNT_CACHE: Optional[int] = None


def preprobe_gpu() -> int:
    """进程早期调用一次：枚举 CUDA 设备数并缓存。重复调用/异常安全。"""
    global _GPU_COUNT_CACHE
    if _GPU_COUNT_CACHE is not None:
        return _GPU_COUNT_CACHE
    n = 0
    try:
        import ctranslate2 as _ct2
        n = _ct2.get_cuda_device_count() or 0
    except BaseException:
        n = 0
    _GPU_COUNT_CACHE = int(n)
    return _GPU_COUNT_CACHE


def _cached_gpu_count() -> int:
    """check_all 内部使用：绝不在现场枚举（见 preprobe_gpu 注释）。"""
    if _GPU_COUNT_CACHE is not None:
        return _GPU_COUNT_CACHE
    # 未预探测（理论上只在独测 core 时发生）：退化为「不枚举、当作无 GPU」，
    # 体检页少一个 CUDA 项好过现场 AV 闪退。
    return 0


def check_all() -> List[CheckItem]:
    """跑全部检查。全部本地操作，耗时 < 2s。"""
    items: List[CheckItem] = []

    # ---- Python（源码运行才有意义；打包版恒真）----
    frozen = getattr(sys, "frozen", False)
    items.append(CheckItem(
        id="python", title=S("Python 运行环境", "Python runtime"),
        why=S("程序运行的基础。", "The foundation the app runs on."),
        level="required", ok=True,
        detail=S("打包版内置", "bundled with the app") if frozen
        else f"{sys.version_info.major}.{sys.version_info.minor}"
             f".{sys.version_info.micro}",
        fix_note="" if frozen else S("请从 python.org 安装 3.10+ 后重试",
                                     "Install 3.10+ from python.org and retry")))

    # ---- PyQt5（打包版随包；源码环境可能缺）----
    pyqt_ok = importlib.util.find_spec("PyQt5") is not None
    items.append(CheckItem(
        id="pyqt5", title=S("界面框架（PyQt5）", "UI framework (PyQt5)"),
        why=S("没有它程序连窗口都画不出来。", "Without it the app cannot draw a window at all."),
        level="required", ok=pyqt_ok or frozen,
        detail=_mod_version("PyQt5.QtCore") or (S("打包版内置", "bundled with the app")
                                                if frozen else S("未安装", "not installed")),
        fix_pkgs=[] if (pyqt_ok or frozen) else ["PyQt5>=5.15.2"],
        fix_note="" if (pyqt_ok or frozen) else S("安装", "Install")))

    # ---- faster-whisper：本地转写的核心 ----
    # 打包版（frozen）里 faster_whisper/av 必须被本进程 import 才算在——
    # PyInstaller 进程不读系统 Python 的 site-packages，pip 装到系统环境
    # 里 find_spec 依旧 None：按钮按了"安装成功"但检查永远红，死循环。
    # 所以打包版这两项不给 pip 修复按钮，只给文字指引（CUDA 运行库不受此限，
    # 它按路径找 DLL，装哪儿都能被 add_dll_directory 挂上）。
    fw_ok = importlib.util.find_spec("faster_whisper") is not None
    items.append(CheckItem(
        id="faster_whisper", title=S("本地语音识别（faster-whisper）",
                                     "Local speech recognition (faster-whisper)"),
        why=S("没有它无法在本地把语音转成字幕（也没法用 GPU 加速）。",
              "Without it, speech cannot be transcribed locally (and no GPU acceleration)."),
        level="recommend", ok=fw_ok or frozen,
        detail=_mod_version("faster_whisper")
        or (S("打包版内置", "bundled with the app") if frozen else S("未安装", "not installed")),
        fix_pkgs=[] if (fw_ok or frozen) else ["faster-whisper>=1.0.0"],
        fix_note="" if (fw_ok or frozen) else S("安装", "Install")))

    # ---- PyAV：不装 ffmpeg 也能解码视频的兜底 ----
    av_ok = importlib.util.find_spec("av") is not None
    items.append(CheckItem(
        id="pyav", title=S("视频解码（PyAV）", "Video decoding (PyAV)"),
        why=S("没装 ffmpeg 时的内置解码兜底；有它 + ffmpeg 双保险。",
              "Built-in decoding fallback when ffmpeg is absent; together with "
              "ffmpeg it is double insurance."),
        level="recommend", ok=av_ok or frozen,
        detail=_mod_version("av") or (S("打包版内置", "bundled with the app")
                                      if frozen else S("未安装", "not installed")),
        fix_pkgs=[] if (av_ok or frozen) else ["av>=12.0.0"],
        fix_note="" if (av_ok or frozen) else S("安装", "Install")))

    # ---- ffmpeg（可选，找不到也能跑）----
    from . import media as _media
    ff = _media.find_ffmpeg()
    items.append(CheckItem(
        id="ffmpeg", title=S("ffmpeg（可选，抽音频更快更稳）",
                             "ffmpeg (optional: faster, steadier audio extraction)"),
        why=S("没有也行——会自动用 PyAV 解码；有的话抽音频兼容性最好。",
              "Optional — PyAV decoding is used automatically; ffmpeg has the "
              "best audio-extraction compatibility."),
        level="optional", ok=bool(ff),
        detail=ff or S("未找到（将使用内置 PyAV 解码）",
                       "not found (built-in PyAV decoding will be used)"),
        fix_note=S("到 https://www.gyan.dev/ffmpeg/builds/ 下载 release-fulls 压缩包，"
                   "解压后把 ffmpeg.exe 所在目录加入 PATH，或放到 C:\\ffmpeg\\bin\\",
                   "Download the release-fulls archive from "
                   "https://www.gyan.dev/ffmpeg/builds/ , extract it, add the "
                   "ffmpeg.exe folder to PATH, or drop it into C:\\ffmpeg\\bin\\")))

    # ---- CUDA 12 运行库（可选，GPU 加速的关键）----
    try:
        from . import cuda_rt as _cuda
        # force=True：修复重查必须绕过会话内缓存——首启已把 unusable 结果
        # 缓存住，一键修复装好 cublas 后不强制重探的话，本会话内永远红
        rt = _cuda.register(force=True)
        gpu = _cached_gpu_count() > 0     # 只读预探测缓存，绝不现场枚举
        if gpu:
            ok = bool(rt.usable and _cuda.probe_loadable(rt))
            items.append(CheckItem(
                id="cuda12", title=S("CUDA 12 运行库（GPU 加速）",
                                     "CUDA 12 runtime (GPU acceleration)"),
                why=S("检测到独立显卡。装上运行库后转写速度可快 5~20 倍。",
                      "Dedicated GPU detected. With the runtime, transcription "
                      "can be 5–20x faster."),
                level="optional", ok=ok,
                detail=(rt.cublas_dir if ok else rt.note) or S("未找到", "not found"),
                fix_pkgs=[] if ok else
                ["nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-runtime-cu12"],
                fix_note="" if ok else S("安装（约 700 MB，装完即用 GPU）",
                                         "Install (~700 MB, GPU ready afterwards)")))
        else:
            items.append(CheckItem(
                id="cuda12", title=S("独立显卡（NVIDIA GPU）", "Dedicated GPU (NVIDIA)"),
                why=S("没检测到 N 卡，将用 CPU 转写（速度慢一些，功能不受影响）。",
                      "No NVIDIA GPU detected; CPU transcription will be used "
                      "(slower, features unaffected)."),
                level="optional", ok=False,
                detail=S("未检测到 CUDA 设备", "No CUDA device detected"), fix_note=""))
    except Exception as e:      # 探测本身失败不拦路
        items.append(CheckItem(id="cuda12", title=S("CUDA 12 运行库（GPU 加速）",
                                                    "CUDA 12 runtime (GPU acceleration)"),
                               why="", level="optional", ok=False,
                               detail=S(f"探测失败：{e}", f"Probe failed: {e}"), fix_note=""))
    return items


def summary(items: List[CheckItem]) -> str:
    """一行摘要，给进度条/状态栏用。"""
    from .i18n import S
    n_ok = sum(1 for i in items if i.ok)
    n_req_bad = sum(1 for i in items if i.level == "required" and not i.ok)
    if n_req_bad:
        return S(f"缺少必需组件（{n_req_bad} 项），程序无法正常工作",
                 f"Missing {n_req_bad} required component(s) — cannot run")
    n_rec_bad = sum(1 for i in items if i.level == "recommend" and not i.ok)
    if n_rec_bad:
        return S(f"核心功能可用；建议补装 {n_rec_bad} 个组件",
                 f"Core features OK; {n_rec_bad} recommended component(s) missing")
    return S(f"一切正常（{n_ok}/{len(items)} 通过）",
             f"All good ({n_ok}/{len(items)} passed)")


def all_required_ok(items: List[CheckItem]) -> bool:
    return all(i.ok for i in items if i.level == "required")
