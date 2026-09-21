"""测试用的小工具：断言计数、临时目录、样例文档。"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:                                          # Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # type: ignore
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")     # type: ignore
except Exception:
    pass

# 把配置/数据目录隔离到临时目录：测试绝不读写用户真实的 %APPDATA%\SubtitleStudio
_HOME = tempfile.mkdtemp(prefix="sstudio_home_")
os.environ["SUBTITLE_STUDIO_HOME"] = _HOME
atexit.register(shutil.rmtree, _HOME, True)

_failures: list = []


def check(name: str, cond, extra="") -> bool:
    ok = bool(cond)
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not ok:
        _failures.append(name)
    return ok


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def finish() -> int:
    print("\n" + ("全部通过" if not _failures else f"失败 {len(_failures)} 项：{_failures}"))
    return 1 if _failures else 0


class TempDir:
    """with TempDir() as d: ... 用完自动删。"""

    def __enter__(self) -> str:
        self.path = tempfile.mkdtemp(prefix="sstudio_test_")
        return self.path

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def sample_doc():
    """一个有代表性的小文档：含错别字原文、说话人、词级时间戳。"""
    from sstudio.core.model import Cue, CueDocument
    cues = [
        Cue(start=0.0, end=2.5, text="大家好，欢迎来到本期评测",
            original_text="大家号 欢迎来到本期评测", state="llm",
            words=[{"start": 0.0, "end": 0.6, "word": "大家", "prob": 0.99},
                   {"start": 0.6, "end": 1.0, "word": "好", "prob": 0.97}]),
        Cue(start=2.5, end=5.0, text="今天聊聊 RTX 5060 这张卡",
            original_text="今天聊聊 RTX 5060 这张卡", speaker="小明"),
        Cue(start=5.2, end=8.0, text="它的提升非常明显。",
            original_text="她的提升非常明显。", state="review"),
    ]
    return CueDocument(source_video=r"D:\demo.mp4", duration=8.0,
                       language="zh", cues=cues)
