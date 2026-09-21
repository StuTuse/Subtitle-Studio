# -*- coding: utf-8 -*-
"""release.py 快捷方式同步功能测试（不碰真实桌面，目录全部注入临时目录）。

真实读写 .lnk 走 Windows COM；无 pywin32 时走 PowerShell 回退，
两条路径都在这里各测一遍。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import TempDir, check, finish, section  # noqa: E402

import release as R  # noqa: E402

EXE = r"D:\Project\AgentPAGE\DSH\subtitle-studio\dist\Subtitle Studio\Subtitle Studio.exe"
OLD = r"D:\Project\AgentPAGE\DSH\subtitle-studio\dist_old\Subtitle Studio\Subtitle Studio.exe"
FOREIGN = r"D:\别的软件\app.exe"
NAME = "Subtitle Studio.lnk"

section("1. COM 路径：旧指向同步 / 新建 / 不越权 / 已是最新")

HAS_PYWIN = True
try:
    import pythoncom  # noqa: F401
    from win32com.client import Dispatch  # noqa: F401
except Exception:
    HAS_PYWIN = False

if HAS_PYWIN:
    import pythoncom
    from win32com.client import Dispatch
    pythoncom.CoInitialize()

    def read_target(lnk_path):
        return Dispatch("WScript.Shell").CreateShortcut(lnk_path).TargetPath or ""

    def make_lnk(lnk_path, target):
        s = Dispatch("WScript.Shell").CreateShortcut(lnk_path)
        s.TargetPath = target
        s.Save()

    with TempDir() as td:
        lnk = os.path.join(td, NAME)
        make_lnk(lnk, OLD)
        touched = R.sync_shortcut(EXE, dirs=[td])
        check("旧指向被同步到新 exe", touched == [lnk] and read_target(lnk) == EXE,
              read_target(lnk))

    with TempDir() as td:
        touched = R.sync_shortcut(EXE, dirs=[td])
        lnk = os.path.join(td, NAME)
        check("没有 .lnk 时新建并指向 exe",
              touched == [lnk] and read_target(lnk) == EXE, read_target(lnk))

    with TempDir() as td:
        lnk = os.path.join(td, NAME)
        make_lnk(lnk, FOREIGN)
        touched = R.sync_shortcut(EXE, dirs=[td])
        check("指向别的软件的 .lnk 不越权改动", touched == [] and read_target(lnk) == FOREIGN,
              read_target(lnk))

    with TempDir() as td:
        lnk = os.path.join(td, NAME)
        make_lnk(lnk, EXE)
        touched = R.sync_shortcut(EXE, dirs=[td])
        check("已指向最新产物时不动（无多余写盘）", touched == [], touched)

    with TempDir() as td:
        lnk = os.path.join(td, NAME)
        make_lnk(lnk, OLD)
        R.sync_shortcut(EXE, dirs=[td, td])     # 同一目录重复传入
        check("同目录重复传入不炸、结果正确", read_target(lnk) == EXE, read_target(lnk))

    with TempDir() as td1, TempDir() as td2:
        make_lnk(os.path.join(td1, NAME), OLD)
        touched = R.sync_shortcut(EXE, dirs=[td1, td2])
        check("多目录：有旧指向的更新、没 .lnk 的新建",
              len(touched) == 2
              and read_target(os.path.join(td1, NAME)) == EXE
              and read_target(os.path.join(td2, NAME)) == EXE, touched)
else:
    print("· 本机无 pywin32，COM 分支跳过（走下方 PowerShell 回退分支）")

section("2. 无 pywin32 时的 PowerShell 回退分支")
# 用 sys.modules 注入制造 ImportError
saved = {k: sys.modules.get(k) for k in ("pythoncom", "win32com", "win32com.client")}
for k in ("pythoncom", "win32com", "win32com.client"):
    sys.modules.pop(k, None)
try:
    with TempDir() as td:
        touched = R.sync_shortcut(EXE, dirs=[td])
        lnk = os.path.join(td, NAME)
        ok = (os.path.isfile(lnk) and touched == [lnk])
        if HAS_PYWIN:
            target = read_target(lnk)      # 用真实 COM 读回来验证
            ok = ok and target == EXE
        check("回退分支能创建指向正确的 .lnk", ok, touched)
finally:
    for k, v in saved.items():
        if v is not None:
            sys.modules[k] = v

section("3. 空目录列表与 argparse")
check("空 dirs 直接跳过", R.sync_shortcut(EXE, dirs=[]) == [])
r = __import__("subprocess").run(
    [sys.executable, os.path.join(ROOT := os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "release.py"), "--help"],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
check("--sync-shortcut 已出现在 --help", "--sync-shortcut" in (r.stdout or ""), r.returncode)
check("--bump 已出现在 --help", "--bump" in (r.stdout or ""))

sys.exit(finish())
