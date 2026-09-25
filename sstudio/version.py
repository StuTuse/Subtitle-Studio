"""版本信息：唯一真源是仓库根目录的 ``VERSION`` 文件。

发版只需改那一行，然后 ``python release.py``（自动写 CHANGELOG、打 tag、打包）。

读取顺序（兼顾源码运行与 PyInstaller 打包）：
1. 随包携带的 ``VERSION``（build.spec 已打包进去）
2. 源码仓库根的 ``VERSION``
3. 构建时生成的 ``_buildinfo``（含 git 提交号等，仅打包产物有）
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Optional, Tuple

APP_NAME = "Subtitle Studio"
APP_NAME_ZH = "视频字幕工坊"
__all__ = ["APP_NAME", "APP_NAME_ZH", "__version__", "version_tuple",
           "version_info", "describe", "git_commit", "is_release"]

_FALLBACK = "0.0.0"
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+](\S+))?$")


def _roots() -> list:
    """可能存放 VERSION 的目录，按优先级。"""
    out = []
    if getattr(sys, "frozen", False):                 # PyInstaller
        out.append(getattr(sys, "_MEIPASS", ""))
        out.append(os.path.dirname(os.path.dirname(sys.executable)))
        out.append(os.path.dirname(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    out.append(os.path.dirname(here))                 # 仓库根（sstudio 的上一级）
    out.append(here)
    return [r for r in out if r]


def _read_version_file() -> Optional[str]:
    for root in _roots():
        p = os.path.join(root, "VERSION")
        try:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    txt = f.read().strip()
                if txt:
                    return txt.splitlines()[0].strip().lstrip("vV")
        except OSError:
            continue
    return None


def _normalize(raw: str) -> str:
    m = _SEMVER_RE.match(raw or "")
    return raw if m else _FALLBACK


__version__ = _normalize(_read_version_file() or _FALLBACK)


def version_tuple(v: Optional[str] = None) -> Tuple[int, int, int, int]:
    """``(major, minor, patch, build)`` —— 给 Windows 文件版本用，必须四段。"""
    m = _SEMVER_RE.match(v or __version__)
    if not m:
        return (0, 0, 0, 0)
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), 0


def is_release(v: Optional[str] = None) -> bool:
    """不带 ``-``/``+`` 预发布后缀即视为正式版。"""
    return _SEMVER_RE.match(v or __version__) is not None and \
        not re.search(r"[-+]", (v or __version__))


def _git(args, root: str) -> str:
    """跑 git 并返回 stdout；任何失败都返回空串。

    打包版是无窗口程序，Windows 下起子进程会闪黑框，必须挂 CREATE_NO_WINDOW。
    """
    if getattr(sys, "frozen", False):
        return ""                       # 打包版没有 .git，直接跳过，省一次进程开销
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True,
                           text=True, timeout=6,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            return (r.stdout or "").strip()
    except Exception:
        pass
    return ""


def git_commit(here: Optional[str] = None) -> str:
    """当前 git 短提交号；不在仓库或无 git 时返回空串。"""
    return _git(["rev-parse", "--short", "HEAD"],
                here or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def git_dirty(here: Optional[str] = None) -> bool:
    root = here or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return bool(_git(["status", "--porcelain"], root))


def _read_buildinfo() -> dict:
    """打包时由 build.spec 生成的 ``BUILDINFO``：记录构建时的 git 号。

    打包产物里没有 .git，若不在构建期固化，事后就无法知道某个 exe 是哪次提交编的。
    """
    for root in _roots():
        p = os.path.join(root, "BUILDINFO")
        try:
            if os.path.isfile(p):
                out = {}
                with open(p, "r", encoding="utf-8") as f:
                    for ln in f:
                        if "=" in ln:
                            k, _, v = ln.partition("=")
                            out[k.strip()] = v.strip()
                return out
        except OSError:
            continue
    return {}


def build_commit() -> str:
    """构建期 git 号（打包版）或当前工作区 git 号（源码运行）。"""
    return git_commit() or _read_buildinfo().get("commit", "")


def describe() -> str:
    """用于关于框、标题栏、--version 的一行版本描述。"""
    parts = [APP_NAME, __version__]
    if not is_release():
        parts.append("(预发布)")
    c = build_commit()
    if c:
        dirty = git_dirty() or _read_buildinfo().get("dirty") == "1"
        parts.append(f"{c}{'*' if dirty else ''}")
    if getattr(sys, "frozen", False):
        parts.append("[打包版]")
    return " ".join(parts)


def version_info() -> dict:
    info = {
        "version": __version__,
        "tuple": version_tuple(),
        "app": APP_NAME,
        "app_zh": APP_NAME_ZH,
        "release": is_release(),
        "commit": build_commit(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    info["dirty"] = git_dirty() or (info["frozen"] and
                                    _read_buildinfo().get("dirty") == "1")
    return info
