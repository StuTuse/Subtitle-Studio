"""版本系统：VERSION 真源、语义化解析、冻结环境读取、git 溯源。"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import TempDir, check, finish, section  # noqa: E402

from sstudio import version as V  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

section("1. VERSION 文件是唯一真源")
vpath = os.path.join(ROOT, "VERSION")
check("VERSION 文件存在", os.path.isfile(vpath), vpath)
raw = open(vpath, encoding="utf-8").read().strip()
check("内容形如 x.y.z", bool(re.match(r"^\d+\.\d+\.\d+([-+]\S+)?$", raw)), repr(raw))
check("模块版本 == 文件版本", V.__version__ == raw.splitlines()[0].lstrip("vV"),
      f"{V.__version__} vs {raw}")
check("__init__ 导出的也是同一个", __import__("sstudio").__version__ == V.__version__)

section("2. 语义化版本解析边界")
check("正常版 release=True", V.is_release("1.2.3"))
check("预发布 release=False", not V.is_release("1.2.3-beta.1"))
check("build 元数据 release=False", not V.is_release("1.2.3+20260920"))
check("tuple 四段（Windows 文件版本要求）", len(V.version_tuple("2.5.9")) == 4)
check("tuple 数值正确", V.version_tuple("2.5.9") == (2, 5, 9, 0))
check("预发布也能出四段", V.version_tuple("0.1.0-rc.1") == (0, 1, 0, 0))
check("垃圾输入不抛异常", V.version_tuple("abc") == (0, 0, 0, 0))
# 空串按"用当前版本"处理（v or __version__），关键是不许抛异常
check("空输入回落当前版本且不抛异常",
      V.version_tuple("") == V.version_tuple(V.__version__))
check("None 输入同样安全", V.version_tuple(None) == V.version_tuple(V.__version__))
check("非法版本号安全兜底为 0.0.0", V._normalize("v1") == "0.0.0")
check("合法版本号原样保留", V._normalize("1.2.3") == "1.2.3")

section("3. describe() 输出可读且不含敏感信息")
d = V.describe()
check("含产品名", V.APP_NAME in d, d)
check("含版本号", V.__version__ in d, d)
check("不含绝对路径", ":\\" not in d and "/" not in d.replace("Subtitle", ""), d)
info = V.version_info()
check("version_info 键齐备",
      {"version", "tuple", "app", "release", "commit", "dirty", "frozen"} <= set(info))
check("源码运行时 frozen=False", info["frozen"] is False)

section("4. git 不可用时不得崩溃（打包版路径）")
_saved = V._git
try:
    V._git = lambda args, root: ""       # 模拟无 git / 无 .git
    check("无 git 时 describe 仍返回字符串", isinstance(V.describe(), str))
    check("无 git 时 version_info 仍完整", V.version_info()["version"] == V.__version__)
    check("无 git 时 commit 为空串", V.version_info()["commit"] == "")
finally:
    V._git = _saved

section("5. 冻结环境（模拟 PyInstaller）能读到 VERSION 与 BUILDINFO")
with TempDir() as td:
    with open(os.path.join(td, "VERSION"), "w", encoding="utf-8") as f:
        f.write("9.9.9\n")
    with open(os.path.join(td, "BUILDINFO"), "w", encoding="utf-8") as f:
        f.write("version=9.9.9\ncommit=abc1234\nbranch=main\ndirty=1\nbuilt=2026-09-20T10:00:00\n")
    # 模拟 sys._MEIPASS + sys.frozen
    _meipass = getattr(sys, "_MEIPASS", None)
    _frozen = getattr(sys, "frozen", None)
    sys._MEIPASS = td
    sys.frozen = True
    try:
        check("冻结环境读到随包 VERSION", V._read_version_file() == "9.9.9",
              V._read_version_file())
        bi = V._read_buildinfo()
        check("BUILDINFO 解析出 commit", bi.get("commit") == "abc1234", bi)
        check("BUILDINFO 解析出 dirty", bi.get("dirty") == "1")
        check("冻结版跳过 git 子进程（防黑框闪烁）", V.git_commit() == "")
        check("冻结版仍能从 BUILDINFO 拿到 commit", V.build_commit() == "abc1234")
        check("describe 标出打包形态", "[打包版]" in V.describe(), V.describe())
        check("describe 带构建号与脏标记", "abc1234*" in V.describe(), V.describe())
    finally:
        if _meipass is None:
            del sys._MEIPASS
        else:
            sys._MEIPASS = _meipass
        if _frozen is None:
            del sys.frozen
        else:
            sys.frozen = _frozen

section("6. release.py 可导入且版本号校验严格")
sys.path.insert(0, ROOT)
import release as R  # noqa: E402

for good in ("1.0.0", "0.0.1", "10.20.30", "1.2.3-beta.1"):
    check(f"合法版本号 {good}", R._SEMVER.match(good) is not None)
for bad in ("1", "1.0", "1.0.0.", "v1.0.0", "01.0.0", "", "1.0.0.0", "x.y.z"):
    check(f"非法版本号 {bad!r} 被拒", R._SEMVER.match(bad) is None)
check("bump 类型 major", R.bump_kind("1.2.3", "2.0.0") == "Major")
check("bump 类型 minor", R.bump_kind("1.2.3", "1.3.0") == "Minor")
check("bump 类型 patch", R.bump_kind("1.2.3", "1.2.4") == "Patch")
check("bump 类型预发布", R.bump_kind("1.2.3", "1.2.3-rc.1") == "预发布")

section("7. .gitignore 把敏感与产物都挡住了")
gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
for pat in ("SSData/", "__pycache__/", "dist/", "build/", "_probe_"):
    check(f".gitignore 覆盖 {pat}", pat in gi)
check("build.spec 未被误忽略（要入库）",
      "build.spec" not in gi.replace("build/", ""))
r = subprocess.run(["git", "check-ignore", "-q", "SSData/config.json"],
                   cwd=ROOT, capture_output=True)
check("git 确认 SSData 被忽略", r.returncode == 0)

section("8. CHANGELOG 与 VERSION 对得上")
cl = os.path.join(ROOT, "CHANGELOG.md")
check("CHANGELOG 存在", os.path.isfile(cl))
if os.path.isfile(cl):
    txt = open(cl, encoding="utf-8").read()
    check(f"含当前版本段落 [{V.__version__}]", f"[{V.__version__}]" in txt)
    check("声明了语义化版本约定", "语义化版本" in txt or "semver" in txt.lower())

sys.exit(finish())
