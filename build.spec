# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

    pip install pyinstaller
    pyinstaller build.spec --noconfirm

产物：dist/Subtitle Studio/Subtitle Studio.exe（文件夹模式，启动更快、便于改配置）

说明
----
* 采用 onedir 而非 onefile：faster-whisper 依赖的 ctranslate2 / onnxruntime 体积大，
  onefile 每次启动都要解压到临时目录，冷启动会慢十几秒。
* 模型不打包（每个都 1.5–3 GB）。程序运行时会扫描本机已有模型，见 README。
* 配置与缓存写在 exe 同级的 SSData\\，拷到 U 盘即便携。
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(SPECPATH)

# ---------------------------------------------------------------- 版本号
# 真源：<项目根>/VERSION。exe 的文件版本、产品版本都从这里来。
_ver_txt = "0.0.0"
_version_file = os.path.join(ROOT, "VERSION")
if os.path.isfile(_version_file):
    with open(_version_file, "r", encoding="utf-8") as _f:
        _ver_txt = _f.read().strip().splitlines()[0].strip().lstrip("vV")
_parts = (_ver_txt.split("+")[0].split("-")[0] + ".0.0.0").split(".")
_ver_num = tuple(int(x) if x.isdigit() else 0 for x in _parts[:4])
print(f"[build.spec] 版本 {_ver_txt} -> 文件版本 {'.'.join(map(str, _ver_num))}")

# ------------------------------------------------------- 构建信息（git 溯源）
# 打包产物里没有 .git，事后无法知道 exe 出自哪次提交，所以构建期固化一份。
import datetime as _dt
import subprocess as _sp


def _git(*args):
    try:
        r = _sp.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                    timeout=8, creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


_buildinfo_path = os.path.join(SPECPATH, "build", "BUILDINFO")
os.makedirs(os.path.dirname(_buildinfo_path), exist_ok=True)
_commit = _git("rev-parse", "--short", "HEAD")
with open(_buildinfo_path, "w", encoding="utf-8") as _bi:
    _bi.write(f"version={_ver_txt}\n")
    _bi.write(f"commit={_commit}\n")
    _bi.write(f"branch={_git('rev-parse', '--abbrev-ref', 'HEAD')}\n")
    _bi.write(f"dirty={'1' if _git('status', '--porcelain') else '0'}\n")
    _bi.write(f"built={_dt.datetime.now().isoformat(timespec='seconds')}\n")
print(f"[build.spec] 构建信息 commit={_commit or '(无 git)'}")

block_cipher = None

# 需要一起带上的第三方数据/二进制
datas = [(_version_file, "."),         # 让 sstudio.version 在冻结环境也读得到
         (_buildinfo_path, ".")]       # git 溯源信息
bins = []
for pkg in ("faster_whisper", "ctranslate2", "qfluentwidgets"):
    try:
        datas += collect_data_files(pkg)
        bins += collect_dynamic_libs(pkg)
    except Exception:
        pass

hiddenimports = []
for pkg in ("qfluentwidgets", "faster_whisper", "ctranslate2"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    ["run.py"],
    pathex=[os.path.abspath(SPECPATH)],
    binaries=bins,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 用不到的大件一律排除，能省下几百 MB。
    # 注意：PyQt5.QtXml 不能排——qfluentwidgets 硬依赖它（仅 0.2 MB）。
    # 曾因误排除导致打包版 "No module named 'PyQt5.QtXml'"、界面起不来。
    excludes=[
        "torch", "torchaudio", "torchvision", "tensorflow", "keras", "jax",
        "matplotlib", "scipy", "pandas", "IPython", "notebook", "jupyter",
        "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngine", "PyQt5.QtQml",
        "PyQt5.QtQuick", "PyQt5.QtCharts", "PyQt5.QtDataVisualization",
        "PyQt5.Qt3DCore", "PyQt5.Qt3DRender", "PyQt5.QtPdf", "PyQt5.QtSql",
        "PyQt5.QtBluetooth", "PyQt5.QtNfc", "PyQt5.QtSerialPort",
        "PyQt5.QtTest", "PyQt5.QtWebSockets",
        "tkinter", "pydoc_data",   # unittest 不排：省不了 2 MB，却有依赖方 import 它
        # hf_xet（~9MB）：huggingface_hub 的可选 Xet 传输插件。产品默认
        # modelscope/hf-mirror 源，运行时恒设 HF_HUB_DISABLE_XET=1；hub 对
        # 它 import 失败会自动回退普通 HTTP 分片下载。打包体积归因分析
        # （第 245 轮）确认可排：-9MB 安装包。
        "hf_xet",
        # opengl32sw（20MB）是 Qt 的软件 OpenGL 后备渲染器，仅在无 GPU
        # 驱动的裸虚拟机里才用得到。桌面用户都有显卡驱动，排除（binaries
        # 阶段按文件名剔，Analysis excludes 管不到 Qt5/bin 的 DLL）。
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------- 版本资源
# PyInstaller 会用 eval() 反序列化这个文本，格式必须与它自己的 __str__ 一致，
# 所以直接用它的类构造再 str()，而不是手写模板（手写极易因参数名变动而崩）。
from PyInstaller.utils.win32 import versioninfo as _vi

_ver_path = os.path.join(SPECPATH, "build", "version_info.txt")
os.makedirs(os.path.dirname(_ver_path), exist_ok=True)
_info = _vi.VSVersionInfo(
    ffi=_vi.FixedFileInfo(
        filevers=_ver_num, prodvers=_ver_num,
        mask=0x3f, flags=0x0, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        _vi.StringFileInfo([
            _vi.StringTable("040904b0", [
                _vi.StringStruct("CompanyName", "Tuse Creation"),
                _vi.StringStruct("FileDescription",
                                 "视频字幕工坊 - 本地 Whisper 转写 + 大模型错别字修正"),
                _vi.StringStruct("FileVersion", ".".join(map(str, _ver_num))),
                _vi.StringStruct("InternalName", "SubtitleStudio"),
                _vi.StringStruct("LegalCopyright", "Tuse Creation"),
                _vi.StringStruct("OriginalFilename", "Subtitle Studio.exe"),
                _vi.StringStruct("ProductName", "Subtitle Studio 视频字幕工坊"),
                _vi.StringStruct("ProductVersion", _ver_txt),
            ])]),
        _vi.VarFileInfo([_vi.VarStruct("Translation", [1033, 1200])]),
    ])
with open(_ver_path, "w", encoding="utf-8") as _vf:
    _vf.write(str(_info))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Subtitle Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                    # GUI 程序，不带黑窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_ver_path,
    manifest="app.manifest",          # PerMonitorV2 DPI 感知：修高缩放屏模糊
    icon="assets/app.ico",            # 由 make_icon.py 生成（与 splash 同源造型）
)


def _prune_qt5_sw(binaries):
    """体积归因（第 245 轮）：Qt5/bin 里 opengl32sw.dll（20MB 软件渲染器）
    只在无显卡驱动的裸虚拟机里有用；桌面用户都有驱动。按文件名剔掉。
    d3dcompiler_47.dll 保留：ANGLE/DirectX 后端要用，剔了会黑屏。"""
    out = []
    for item in binaries:
        src = item[0] if isinstance(item, tuple) else str(item)
        base = os.path.basename(src).lower()
        if base in ("opengl32sw.dll",):
            continue
        out.append(item)
    return out


coll = COLLECT(
    exe,
    _prune_qt5_sw(a.binaries),
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Subtitle Studio",
)
