"""CUDA 运行时发现：让 CTranslate2 找得到 ``cublas64_12.dll``。

背景
----
CTranslate2 的官方 wheel 是按 **CUDA 12** 编译的，运行时需要 ``cublas64_12.dll``
与 ``cudart64_12.dll``。但很多人（包括本机）装的是 CUDA 13 的 torch nightly，
``torch/lib`` 里只有 ``cublas64_13.dll`` —— 于是 ``ctranslate2.get_cuda_device_count()``
能报出 GPU（它只查驱动），真到推理时却炸：

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

本模块按优先级在常见位置寻找 CUDA 12 运行时，找到就 ``os.add_dll_directory``
注册进当前进程，因此**不需要复制任何大文件**，也不需要重装 torch。

搜索顺序
--------
1. 用户在设置里手动指定的目录
2. ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` 官方 wheel 的安装位置
3. 已安装 torch 的 ``torch/lib``
4. 标准 CUDA 安装与 PATH 里已有的目录
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

_REQUIRED = ("cublas64_12.dll", "cudart64_12.dll")
_OPTIONAL = ("cudnn64_9.dll", "cudnn_ops64_9.dll")

# 需要同时具备 cublas + cudart 才算一个候选目录
_NEEDED = _REQUIRED

_registered: List[str] = []
_result: Optional["CudaRuntime"] = None
_result_extra: str = ""


@dataclass
class CudaRuntime:
    usable: bool
    cublas_dir: str = ""
    cudart_dir: str = ""
    cudnn_dir: str = ""
    searched: List[str] = None      # type: ignore[assignment]
    note: str = ""

    def __post_init__(self) -> None:
        if self.searched is None:
            self.searched = []


def _site_packages() -> List[str]:
    out: List[str] = []
    for p in sys.path:
        if p.endswith("site-packages") and os.path.isdir(p):
            out.append(p)
    try:
        import site
        for p in site.getsitepackages():
            if p not in out and os.path.isdir(p):
                out.append(p)
        ud = site.getusersitepackages()
        if ud and ud not in out:
            out.append(ud)
    except Exception:
        pass
    # 打包版没有 site-packages 概念，但体检向导的"一键修复"会把 CUDA wheel
    # 装进系统 Python —— 那里正是打包版最该去找的地方。扫一遍常见安装位置。
    if getattr(sys, "frozen", False):
        for base in (os.environ.get("LOCALAPPDATA", ""),
                     os.environ.get("PROGRAMFILES", ""),
                     os.environ.get("PROGRAMFILES(X86)", ""),
                     os.path.expanduser("~")):
            if not base:
                continue
            for ver in ("310", "311", "312", "313", ""):
                root = os.path.join(base, "Programs", "Python",
                                    f"Python{ver}" if ver else "Python") \
                    if base.endswith("local") or "localappdata" in base.lower() \
                    else os.path.join(base, f"Python{ver}" if ver else "Python")
                sp = os.path.join(root, "Lib", "site-packages")
                if os.path.isdir(sp) and sp not in out:
                    out.append(sp)
        # py launcher 找到的解释器也顺带查一遍
        for py in _system_pythons():
            try:
                import subprocess as _sp
                r = _sp.run([py, "-c",
                             "import sys;print(sys.prefix)"],
                            capture_output=True, text=True, timeout=10,
                            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                if r.returncode == 0 and r.stdout.strip():
                    sp = os.path.join(r.stdout.strip(), "Lib", "site-packages")
                    if os.path.isdir(sp) and sp not in out:
                        out.append(sp)
            except Exception:
                continue
    return out


def _system_pythons() -> List[str]:
    """找机器上真实存在的 python.exe（打包版修复用；找不到返回空列表）。

    注意过滤 WindowsApps 里的微软商店占位符 python3.exe —— 那不是真解释器，
    调它会弹商店页面。顺手验证每个候选能真的执行 -c（再挡掉损坏安装）。
    """
    import shutil as _sh
    import subprocess as _sp
    flags = getattr(_sp, "CREATE_NO_WINDOW", 0)
    out: List[str] = []

    def _works(p: str) -> bool:
        try:
            r = _sp.run([p, "-c", "import sys;print(sys.version_info[:2])"],
                        capture_output=True, text=True, timeout=10,
                        creationflags=flags)
            return r.returncode == 0 and "(3" in (r.stdout or "")
        except Exception:
            return False

    for name in ("python.exe", "python3.exe"):
        p = _sh.which(name)
        if p and "windowsapps" not in p.lower() and p not in out:
            out.append(p)
    try:
        r = _sp.run(["py", "-3", "-c", "import sys;print(sys.executable)"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=flags)
        if r.returncode == 0 and r.stdout.strip():
            p = r.stdout.strip()
            if os.path.isfile(p) and p not in out:
                out.append(p)
    except Exception:
        pass
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        for ver in ("310", "311", "312", "313"):
            cand = os.path.join(la, "Programs", "Python", f"Python{ver}",
                                "python.exe")
            if os.path.isfile(cand) and cand not in out:
                out.append(cand)
    return [p for p in out if _works(p)]


def _candidate_dirs(extra: Optional[str] = None) -> List[str]:
    dirs: List[str] = []

    def add(p: str) -> None:
        if p and p not in dirs:
            dirs.append(p)

    if extra:
        add(os.path.abspath(os.path.expanduser(extra)))

    for sp in _site_packages():
        # pip 装的 nvidia 官方 wheel（新版包名是 cuda_runtime，旧版是 cudart）
        add(os.path.join(sp, "nvidia", "cublas", "bin"))
        add(os.path.join(sp, "nvidia", "cublas", "lib"))
        add(os.path.join(sp, "nvidia", "cudnn", "bin"))
        add(os.path.join(sp, "nvidia", "cuda_runtime", "bin"))
        add(os.path.join(sp, "nvidia", "cudart", "bin"))
        add(os.path.join(sp, "nvidia", "cuda_nvrtc", "bin"))
        add(os.path.join(sp, "torch", "lib"))
        add(os.path.join(sp, "ctranslate2", "tools"))
        add(os.path.join(sp, "ctranslate2"))

    # 标准 CUDA 安装位置
    env = os.environ
    for root in (env.get("CUDA_PATH", ""), env.get("CUDA_PATH_V12_0", ""),
                 r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.0",
                 r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6",
                 r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8"):
        if root:
            add(os.path.join(root, "bin"))
            add(root)

    # PATH
    for p in os.environ.get("PATH", "").split(os.pathsep):
        p = p.strip().strip('"')
        if p and os.path.isdir(p):
            add(p)
    return dirs


def _find(name: str, dirs: List[str]) -> str:
    for d in dirs:
        try:
            if os.path.isfile(os.path.join(d, name)):
                return d
        except OSError:
            continue
    return ""


def discover(extra_dir: Optional[str] = None) -> CudaRuntime:
    """定位 cublas64_12.dll / cudart64_12.dll 所在目录（不注册）。"""
    dirs = _candidate_dirs(extra_dir)
    cublas_dir = _find("cublas64_12.dll", dirs)
    cudart_dir = _find("cudart64_12.dll", dirs)
    cudnn_dir = _find("cudnn64_9.dll", dirs) or _find("cudnn64_8.dll", dirs)
    usable = bool(cublas_dir) and bool(cudart_dir)
    note = ""
    if not usable:
        have13 = _find("cublas64_13.dll", dirs)
        if have13:
            note = (f"只找到 CUDA 13 的 cublas（{have13}），"
                    "CTranslate2 需要 CUDA 12 版本。")
        else:
            note = "未找到 CUDA 12 运行时。"
        note += (" 解决：pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
                 "（或把已装 CUDA 12 的 torch\\lib 目录填到设置里），也可改用 CPU。")
    return CudaRuntime(usable=usable, cublas_dir=cublas_dir, cudart_dir=cudart_dir,
                       cudnn_dir=cudnn_dir, searched=dirs[:24], note=note)


def register(extra_dir: Optional[str] = None, force: bool = False) -> CudaRuntime:
    """发现并注册 CUDA 12 运行目录。结果会被缓存。

    缓存必须区分 extra_dir：自检/doctor 往往先无参调用一次，若缓存不记
    当时用的目录，用户在设置里指定的 CUDA 目录就会整会话都不生效。
    """
    global _result, _result_extra
    req = os.path.abspath(extra_dir) if extra_dir else ""
    if _result is not None and not force and req == _result_extra:
        return _result

    rt = discover(extra_dir)
    _result_extra = req
    if sys.platform == "win32" and rt.usable:
        for d in {rt.cublas_dir, rt.cudart_dir, rt.cudnn_dir}:
            if not d or d in _registered:
                continue
            try:
                os.add_dll_directory(d)       # Python 3.8+
                _registered.append(d)
            except (AttributeError, OSError):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                _registered.append(d)
        # cublasLt 与 cudnn 子依赖可能位于同一目录，再加一次 torch 根目录更稳
        parent = os.path.dirname(rt.cublas_dir)
        if parent and parent not in _registered and os.path.isdir(parent):
            try:
                os.add_dll_directory(parent)
                _registered.append(parent)
            except OSError:
                pass
    _result = rt
    return rt


def probe_loadable(rt: CudaRuntime) -> bool:
    """真正 LoadLibrary 一次，确认能被加载（避免依赖树不全导致的假阳性）。"""
    if not rt.usable or sys.platform != "win32":
        return False
    ok = True
    for name in _NEEDED:
        found = _find(name, [rt.cublas_dir, rt.cudart_dir] + _registered)
        if not found:
            return False
        try:
            ctypes.WinDLL(os.path.join(found, name))
        except OSError:
            ok = False
    return ok


def describe(rt: Optional[CudaRuntime] = None) -> str:
    rt = rt or (_result or discover())
    if rt.usable:
        return f"CUDA 12 运行时：{rt.cublas_dir}"
    return rt.note


def system_pythons() -> List[str]:
    """对外暴露：机器上真实存在的 python.exe 列表（体检修复用）。"""
    return _system_pythons()
