"""环境自检：``python -m sstudio --check``。"""

from __future__ import annotations

import os
import sys


def run_check() -> int:
    from .version import describe
    line = "=" * 62
    print(line)
    print(f" {describe()} 环境自检")
    print(line)

    ok = True

    def row(name, good, detail=""):
        nonlocal ok
        mark = "✓" if good else "✗"
        if not good:
            ok = False
        print(f" [{mark}] {name:<22} {detail}")

    def warn(name, good, detail=""):
        """提示项：不影响自检总体结论（有自动兜底时用它）。"""
        print(f" [{'✓' if good else '!'}] {name:<22} {detail}")

    frozen = getattr(sys, "frozen", False)
    print(f"\n 运行形态：{'打包版 exe' if frozen else '源码运行'}"
          f"    Python {sys.version.split()[0]}")
    print(f" {sys.executable}\n")

    # PyQt
    try:
        from PyQt5.QtCore import QT_VERSION_STR
        row("PyQt5", True, QT_VERSION_STR)
    except Exception as e:
        row("PyQt5", False, f"pip install PyQt5  ({e})")
    try:
        import qfluentwidgets
        row("PyQt-Fluent-Widgets", True,
            getattr(qfluentwidgets, "__version__", "?"))
    except Exception as e:
        row("PyQt-Fluent-Widgets", False, f"pip install PyQt-Fluent-Widgets  ({e})")
    try:
        import PyQt5.QtMultimedia  # noqa
        row("QtMultimedia", True, "视频预览可用")
    except Exception as e:
        row("QtMultimedia", False, "pip install PyQt5（含 Multimedia）")

    # 转写
    try:
        import faster_whisper
        row("faster-whisper", True, getattr(faster_whisper, "__version__", "?"))
    except Exception as e:
        row("faster-whisper", False, "pip install faster-whisper")
    try:
        import ctranslate2 as ct2
        n = ct2.get_cuda_device_count()
        row("CTranslate2", True, getattr(ct2, "__version__", "?"))
        if n:
            from sstudio.core import cuda_rt
            rt = cuda_rt.register()
            loadable = cuda_rt.probe_loadable(rt)
            row("GPU 可推理", bool(rt.usable) and loadable,
                (f"{n} 块 GPU，CUDA12 运行库 {rt.cublas_dir}"
                 if rt.usable and loadable else
                 "检测到 GPU 但 CUDA 12 运行库缺失 → 将自动使用 CPU"))
            if not (rt.usable and loadable):
                print(f"        {rt.note}")
        else:
            print("        未检测到 GPU，将使用 CPU（int8）")
    except Exception as e:
        row("CTranslate2", False, str(e)[:60])

    # 大模型
    try:
        import openai
        row("openai SDK", True, openai.__version__)
    except Exception as e:
        row("openai SDK", False, f"pip install openai  ({e})")

    # ffmpeg
    from sstudio.core import media
    ff = media.find_ffmpeg()
    row("ffmpeg", bool(ff), ff or "未找到（将回退到 PyAV 解码）")
    try:
        import av
        row("PyAV", True, av.__version__)
    except Exception as e:
        row("PyAV", False, str(e)[:60])

    # 模型
    from sstudio.core.transcriber import discover_ct2_models
    cands = discover_ct2_models()
    print(f"\n 本地 CTranslate2 模型（faster-whisper 可直接用）：{len(cands)} 个")
    for c in cands:
        print(f"   • {c['name']:<44} {c['size']:>8.0f} MB")
        print(f"     {c['path']}")
    if not cands:
        print("   （无：首次转写会联网下载模型）")

    # 配置
    from sstudio.core.config import Config, config_path, data_dir
    cfg = Config.load()
    p = cfg.profile()
    print(f"\n 配置文件 {config_path()}")
    print(f" 数据目录 {data_dir()}")
    print(f" 当前接入点 {p.name} · {p.model} · {p.base_url} · "
          f"{'key 已填' if p.api_key else 'key 未填'}")
    print(f" 转写引擎 {cfg.asr_engine} / 模型 {cfg.whisper_model} / "
          f"设备 {cfg.whisper_device}\n")
    print(line)
    print(" 自检" + ("通过。" if ok else "存在缺失项，请按上面提示安装。"))
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run_check())
