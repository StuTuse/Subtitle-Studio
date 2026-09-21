"""Subtitle Studio 测试套件。

    python tests/run_all.py            # 跑全部
    python tests/run_all.py --quick    # 只跑不需要 GPU / 不碰真实文件的

各用例均为离线、自包含：LLM 相关用例用假响应驱动，转写用例只在能发现本地模型时才跑。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SUITES = [
    ("formats",      "格式读写往返 + 导入自动识别", False),
    ("llm_logic",    "提示词渲染 / 编号解析 / 严格校验", False),
    ("fix_pipeline", "纠错流水线：并发、重试、拦截", False),
    ("engines",      "模型发现、工程往返、引擎可用性", False),
    ("version",      "版本号真源、打包溯源、发版校验", False),
    ("release_shortcut", "发版脚本：桌面快捷方式同步", False),
    ("doctor",       "环境体检：检查项、镜像修复编排、首启向导", False),
    ("settings_reset", "恢复默认设置：保留 API Key/地址、确认框原生可靠", False),
    ("gui",          "界面构建与交互冒烟（offscreen）", False),
    ("transcribe",   "真实转写（需本地 CT2 模型）", True),
]


def main() -> int:
    quick = "--quick" in sys.argv
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
    except Exception:
        pass
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    print("=" * 62)
    print(" Subtitle Studio 测试")
    print("=" * 62)
    failed = []
    for mod, desc, needs_gpu in SUITES:
        if quick and needs_gpu:
            print(f"\n--  SKIP  {mod:<13} {desc}（--quick）")
            continue
        t0 = time.time()
        print(f"\n--  RUN   {mod:<13} {desc}")
        r = subprocess.run([sys.executable, os.path.join(HERE, mod + ".py")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT)
        dt = time.time() - t0
        out = (r.stdout or "") + (r.stderr or "")
        tail = [ln for ln in out.splitlines() if ln.strip()]
        for ln in tail:
            if "FAIL" in ln or "Traceback" in ln or "Error" in ln:
                print("      " + ln[:150])
        if r.returncode == 0:
            print(f"      PASS  {dt:.1f}s   ({len(tail)} 行输出)")
        else:
            failed.append(mod)
            print(f"      FAIL  exit={r.returncode}  {dt:.1f}s")
            for ln in tail[-14:]:
                print("      " + ln[:160])

    print("\n" + "=" * 62)
    if failed:
        print(" 失败：" + ", ".join(failed))
    else:
        print(" 全部通过 ✓")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
