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

# ---------------------------------------------------------------- 镜像
PIP_INDEXES = [
    ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里镜像", "https://mirrors.aliyun.com/pypi/simple"),
    ("官方源", None),            # None = 不加 -i 参数
]

PIP_TIMEOUT = 1200             # 大 wheel（torch 系列 ~2.5GB）留足时间


def _pip_base_args() -> List[str]:
    """python -m pip 是最稳的调用方式（不依赖 PATH 里有 pip.exe）。"""
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
    for i, (name, idx) in enumerate(PIP_INDEXES):
        if cancel and cancel():
            return False, "已取消。"
        cmd = _pip_base_args() + ["install", "--no-input", "--disable-pip-version-check",
                                  *pkgs]
        if idx:
            cmd += ["-i", idx]
        label = name
        if progress:
            progress(f"正在通过{label}安装 {' '.join(pkgs)} …", i / (len(PIP_INDEXES) + 1))
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
        for line in p.stdout:
            line = line.rstrip()
            if not line:
                continue
            if log:
                log(line)
            elif line.startswith(("ERROR", "error:")):
                errs.append(f"{label}: {line[:200]}")
        p.wait()
        dt = time.time() - t0
        if p.returncode == 0:
            if progress:
                progress(f"{' '.join(pkgs)} 安装成功（{label}，{dt:.0f}s）", 1.0)
            return True, f"已通过{label}安装（{dt:.0f}s）"
        errs.append(f"{label}: pip 退出码 {p.returncode}（{dt:.0f}s）")
    return False, "所有镜像都失败了：\n" + "\n".join(errs[-6:])


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
        return bool(self.fix_pkgs)


def _mod_version(mod_name: str) -> str:
    try:
        import importlib
        m = importlib.import_module(mod_name)
        return str(getattr(m, "__version__", "") or getattr(m, "VERSION", "") or "")
    except Exception:
        return ""


def check_all() -> List[CheckItem]:
    """跑全部检查。全部本地操作，耗时 < 2s。"""
    items: List[CheckItem] = []

    # ---- Python（源码运行才有意义；打包版恒真）----
    frozen = getattr(sys, "frozen", False)
    items.append(CheckItem(
        id="python", title="Python 运行环境",
        why="程序运行的基础。",
        level="required", ok=True,
        detail="打包版内置" if frozen else f"{sys.version_info.major}.{sys.version_info.minor}"
              f".{sys.version_info.micro}",
        fix_note="" if frozen else "请从 python.org 安装 3.10+ 后重试"))

    # ---- PyQt5（打包版随包；源码环境可能缺）----
    pyqt_ok = importlib.util.find_spec("PyQt5") is not None
    items.append(CheckItem(
        id="pyqt5", title="界面框架（PyQt5）",
        why="没有它程序连窗口都画不出来。",
        level="required", ok=pyqt_ok or frozen,
        detail=_mod_version("PyQt5.QtCore") or ("打包版内置" if frozen else "未安装"),
        fix_pkgs=[] if (pyqt_ok or frozen) else ["PyQt5>=5.15.2"],
        fix_note="" if (pyqt_ok or frozen) else "安装"))

    # ---- faster-whisper：本地转写的核心 ----
    fw_ok = importlib.util.find_spec("faster_whisper") is not None
    items.append(CheckItem(
        id="faster_whisper", title="本地语音识别（faster-whisper）",
        why="没有它无法在本地把语音转成字幕（也没法用 GPU 加速）。",
        level="recommend", ok=fw_ok,
        detail=_mod_version("faster_whisper") or "未安装",
        fix_pkgs=[] if fw_ok else ["faster-whisper>=1.0.0"],
        fix_note="" if fw_ok else "安装"))

    # ---- PyAV：不装 ffmpeg 也能解码视频的兜底 ----
    av_ok = importlib.util.find_spec("av") is not None
    items.append(CheckItem(
        id="pyav", title="视频解码（PyAV）",
        why="没装 ffmpeg 时的内置解码兜底；有它 + ffmpeg 双保险。",
        level="recommend", ok=av_ok,
        detail=_mod_version("av") or "未安装",
        fix_pkgs=[] if av_ok else ["av>=12.0.0"],
        fix_note="" if av_ok else "安装"))

    # ---- ffmpeg（可选，找不到也能跑）----
    from . import media as _media
    ff = _media.find_ffmpeg()
    items.append(CheckItem(
        id="ffmpeg", title="ffmpeg（可选，抽音频更快更稳）",
        why="没有也行——会自动用 PyAV 解码；有的话抽音频兼容性最好。",
        level="optional", ok=bool(ff),
        detail=ff or "未找到（将使用内置 PyAV 解码）",
        fix_note="到 https://www.gyan.dev/ffmpeg/builds/ 下载 release-fulls 压缩包，"
                 "解压后把 ffmpeg.exe 所在目录加入 PATH，或放到 C:\\ffmpeg\\bin\\"))

    # ---- CUDA 12 运行库（可选，GPU 加速的关键）----
    try:
        from . import cuda_rt as _cuda
        rt = _cuda.register()
        gpu = False
        try:
            import ctranslate2 as _ct2
            gpu = _ct2.get_cuda_device_count() > 0
        except Exception:
            gpu = False
        if gpu:
            ok = bool(rt.usable and _cuda.probe_loadable(rt))
            items.append(CheckItem(
                id="cuda12", title="CUDA 12 运行库（GPU 加速）",
                why="检测到独立显卡。装上运行库后转写速度可快 5~20 倍。",
                level="optional", ok=ok,
                detail=(rt.cublas_dir if ok else rt.note) or "未找到",
                fix_pkgs=[] if ok else
                ["nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cudart-cu12"],
                fix_note="" if ok else "安装（约 700 MB，装完即用 GPU）"))
        else:
            items.append(CheckItem(
                id="cuda12", title="独立显卡（NVIDIA GPU）",
                why="没检测到 N 卡，将用 CPU 转写（速度慢一些，功能不受影响）。",
                level="optional", ok=False, detail="未检测到 CUDA 设备", fix_note=""))
    except Exception as e:      # 探测本身失败不拦路
        items.append(CheckItem(id="cuda12", title="CUDA 12 运行库（GPU 加速）",
                               why="", level="optional", ok=False,
                               detail=f"探测失败：{e}", fix_note=""))
    return items


def summary(items: List[CheckItem]) -> str:
    """一行摘要，给进度条/状态栏用。"""
    n_ok = sum(1 for i in items if i.ok)
    n_req_bad = sum(1 for i in items if i.level == "required" and not i.ok)
    if n_req_bad:
        return f"缺少必需组件（{n_req_bad} 项），程序无法正常工作"
    n_rec_bad = sum(1 for i in items if i.level == "recommend" and not i.ok)
    if n_rec_bad:
        return f"核心功能可用；建议补装 {n_rec_bad} 个组件"
    return f"一切正常（{n_ok}/{len(items)} 通过）"


def all_required_ok(items: List[CheckItem]) -> bool:
    return all(i.ok for i in items if i.level == "required")
