"""发版脚本：改版本 -> 更新 CHANGELOG -> git 提交打 tag -> 可选打包。

用法（在项目根目录）：

    python release.py                    # 交互式：显示当前版本，问下一个版本号
    python release.py 1.1.0              # 直接发布 1.0.0 -> 1.1.0（打 tag，不打包）
    python release.py 1.1.0 --build      # 发布并打包 onedir 产物到 dist/
    python release.py 1.0.1 --dry-run    # 只演练，不写入任何东西
    python release.py --build            # 版本不变，仅重新打包（不发版）
    python release.py --bump minor -m "说明"   # 不想算版本号：按档位自动加一后发版
    python release.py --sync-shortcut    # 只把桌面快捷方式指回 dist 里的 exe

约定
----
* 版本号真源是根目录 ``VERSION``（语义化版本 MAJOR.MINOR.PATCH）。
* 发布 = 提交 VERSION+CHANGELOG -> 打 ``vX.Y.Z`` tag -> （可选）PyInstaller。
* 加 --push 时发版完成后自动推送 main+tag 到 GitHub（推送结果以远端
  ls-remote 复核为准，不确认不算成功）。
* 每次打包完成会自动同步桌面快捷方式指向新产物（找不到就新建）。
* git 身份用仓库级配置；未配置时脚本会停下来提醒，不会替你乱填。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
from typing import Optional

# 输出重定向到管道（如后台任务、CI）时 Windows 默认走 GBK，
# ✓/· 这类字符会直接 UnicodeEncodeError 崩掉 —— 统一强制 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")   # type: ignore
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(ROOT, "VERSION")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
# 注意：整段只用一个 \Z 收尾。早先写成两段字符串时中间混进了一个 $，
# 使 ``1.2.3-rc.1`` 这类预发布号永远匹配不上（tests/version.py 抓到的）。
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
                     r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
                     r"(?:\+(?P<build>[0-9A-Za-z.-]+))?\Z")


def run(cmd, check=True, capture=False):
    print("  $", " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=capture, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        out = (r.stdout or "") + (r.stderr or "") if capture else ""
        sys.exit(f"命令失败（exit={r.returncode}）：{' '.join(cmd)}\n{out}")
    return r


def read_version() -> str:
    if not os.path.isfile(VERSION_FILE):
        sys.exit("缺少 VERSION 文件")
    return open(VERSION_FILE, encoding="utf-8").read().strip()


def write_version(v: str) -> None:
    with open(VERSION_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(v + "\n")


def git(*args, **kw):
    return run(["git", *args], **kw)


def ensure_repo() -> None:
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print("· 还不是 git 仓库，正在初始化 …")
        git("init", "-b", "main")


def ensure_identity() -> None:
    """git config --get 会自动回落到全局配置，所以查一次就够。"""
    missing = []
    for key in ("user.name", "user.email"):
        r = subprocess.run(["git", "config", "--get", key],
                           cwd=ROOT, capture_output=True, text=True)
        if not (r.stdout or "").strip():
            missing.append(key)
    if missing:
        sys.exit(
            "git 还没有配置提交身份：" + ", ".join(missing) + "\n"
            "我不能替你乱填，请任选其一后重跑本脚本：\n"
            f"  仅本仓库： git -C \"{ROOT}\" config user.name \"你的名字\"\n"
            f"            git -C \"{ROOT}\" config user.email \"you@example.com\"\n"
            "  全局：     git config --global user.name \"你的名字\"")


def bump_kind(old: str, new: str) -> str:
    a, b = _SEMVER.match(old), _SEMVER.match(new)
    if not b:
        sys.exit(f"非法版本号：{new}（需形如 1.2.3 或 1.2.3-beta.1）")
    if (a.group(1), a.group(2), a.group(3)) != (b.group(1), b.group(2), b.group(3)):
        if b.group(1) != a.group(1):
            return "Major"
        if b.group(2) != a.group(2):
            return "Minor"
        return "Patch"
    return "预发布"


def update_changelog(old: str, new: str, notes: str) -> None:
    """把新版本段落插到 CHANGELOG 顶部。发布说明来自 -m 或交互输入。"""
    today = _dt.date.today().isoformat()
    entry = [f"## [{new}] - {today}", ""]
    if notes.strip():
        entry += [ln.rstrip() for ln in notes.strip().splitlines()]
    else:
        entry += ["- （补充发布说明）"]
    entry.append("")
    old_text = open(CHANGELOG, encoding="utf-8").read() if os.path.isfile(CHANGELOG) else ""
    body = old_text.split("\n", 1)[1] if old_text.startswith("# ") else old_text
    head = old_text.split("\n", 1)[0] if old_text.startswith("# ") else "# 更新日志"
    body = body.lstrip("\n")
    tail = "" if not body else ("\n" + body if body.startswith("## [") else body)
    with open(CHANGELOG, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "\n\n" + "\n".join(entry) + tail)
    print(f"· CHANGELOG 已加入 [{new}] 段落，记得检查措辞")


def do_build() -> None:
    print("\n· PyInstaller 打包中（onedir，可能要几分钟）…")
    run([sys.executable, "-m", "PyInstaller", "build.spec", "--noconfirm"])
    out = os.path.join(ROOT, "dist", "Subtitle Studio")
    exe = os.path.join(out, "Subtitle Studio.exe")
    if os.path.isfile(exe):
        size = sum(os.path.getsize(os.path.join(dp, f))
                   for dp, _, fs in os.walk(out) for f in fs) / 1048576
        print(f"✓ 产物：{exe}\n  体积 {size:.0f} MB")
        sync_shortcut(exe)
        do_installer()
    else:
        sys.exit("× 打包结束但没找到 exe，请查看上方日志")


# ------------------------------------------------------------ 安装包（Inno Setup）
def _find_iscc() -> str:
    """定位 Inno Setup 编译器；找不到返回空串。"""
    cands = [
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""),
                     r"Inno Setup 6\ISCC.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), r"Inno Setup 6\ISCC.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Inno Setup 6\ISCC.exe"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    which = shutil.which("ISCC")
    return which or ""


def do_installer() -> None:
    """用 Inno Setup 把 dist 产物打成单文件安装程序。没有 ISCC 时温和跳过。

    版本号经 /DAppVersion= 注入（ISPP 对 VERSION 文件的字符串/整数比较行为
    因版本而异，注入是最稳的方式）。
    """
    iss = os.path.join(ROOT, "installer.iss")
    if not os.path.isfile(iss):
        print("· 无 installer.iss，跳过安装包")
        return
    iscc = _find_iscc()
    if not iscc:
        print("· 未安装 Inno Setup 6，跳过安装包编译。"
              "安装后重跑本步即可生成 setup.exe：\n"
              "  https://jrsoftware.org/isdl.php")
        return
    print("\n· Inno Setup 编译安装包中（lzma2，1~2 分钟）…")
    r = run([iscc, f"/DAppVersion={read_version()}", iss], check=False, capture=True)
    out = os.path.join(ROOT, "installer")
    setup = os.path.join(
        out, f"SubtitleStudio-{read_version()}-setup.exe")
    if os.path.isfile(setup):
        mb = os.path.getsize(setup) / 1048576
        print(f"✓ 安装包：{setup}\n  体积 {mb:.1f} MB")
    else:
        print("× 未找到安装包产物，ISCC 输出：")
        print((r.stdout if isinstance(r.stdout, str) else "")[-800:])


# ------------------------------------------------------------ 桌面快捷方式
def _shortcut_dirs() -> list:
    """当前用户桌面 + 公共桌面（去重、过滤不存在的）。"""
    import ctypes
    dirs = []
    for csidl in (0, 0x0019):       # CSIDL_DESKTOP, CSIDL_COMMON_DESKTOPDIR
        buf = ctypes.create_unicode_buffer(260)
        try:
            if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
                dirs.append(buf.value)
        except Exception:
            pass
    return [d for d in dict.fromkeys(dirs) if d and os.path.isdir(d)]


def sync_shortcut(exe_path: str, dirs: "Optional[list]" = None) -> list:
    """把所有指向旧产物的桌面快捷方式改指 exe_path；一个都没有就新建一个。

    覆盖当前用户与公共桌面（测试可注入 ``dirs``）；只动 TargetPath 指向
    本项目 dist 的 .lnk，其它快捷方式一概不碰。返回受影响的 .lnk 路径列表。
    """
    if dirs is None:
        dirs = _shortcut_dirs()
    name = "Subtitle Studio.lnk"
    if not dirs:
        print("· 未找到桌面目录，跳过快捷方式同步")
        return []
    touched = []
    try:
        import pythoncom
        from win32com.client import Dispatch  # type: ignore
        pythoncom.CoInitialize()
        shell = Dispatch("WScript.Shell")
        use_com = True
    except Exception:
        use_com = False

    if not use_com:
        # 没有 pywin32：退回用 PowerShell 的 COM 托管（新建/同步各一个）
        for d in dirs:
            lnk_path = os.path.join(d, name)
            _mk = (f"$s=(New-Object -ComObject WScript.Shell)"
                   f".CreateShortcut('{lnk_path}');"
                   f"$s.TargetPath='{exe_path}';$s.Save()")
            run(["powershell", "-NoProfile", "-Command", _mk], capture=True)
            touched.append(lnk_path)
        print(f"✓ 桌面快捷方式已同步（PowerShell 回退）→ {exe_path}")
        return touched

    for d in dirs:
        lnk_path = os.path.join(d, name)
        try:
            if not os.path.isfile(lnk_path):
                lnk = shell.CreateShortcut(lnk_path)
                lnk.TargetPath = exe_path
                lnk.WorkingDirectory = os.path.dirname(exe_path)
                lnk.Description = "Subtitle Studio · 视频字幕工坊"
                lnk.Save()
                touched.append(lnk_path)
                print(f"✓ 新建快捷方式：{lnk_path}")
                continue
            target = shell.CreateShortcut(lnk_path).TargetPath or ""
            norm = os.path.normcase(os.path.normpath(target))
            norm_new = os.path.normcase(os.path.normpath(exe_path))
            if norm == norm_new:
                continue                      # 已经指向最新产物
            # 归属判定：目标 basename 是本软件 exe 就算我们的快捷方式
            #（不管在哪个 dist/目录里；用户给别的软件建快捷方式不会用这个名字）。
            is_ours = os.path.normcase(os.path.basename(target)) == \
                os.path.normcase(os.path.basename(exe_path))
            if target and not is_ours:
                continue                      # 指向别的软件，不越权
            lnk = shell.CreateShortcut(lnk_path)
            lnk.TargetPath = exe_path
            lnk.WorkingDirectory = os.path.dirname(exe_path)
            lnk.Save()
            touched.append(lnk_path)
            print(f"✓ 快捷方式已同步 → {exe_path}\n  ({lnk_path})")
        except Exception as e:
            # 公共桌面新建通常需要管理员权限；单个目录失败不影响其它目录
            print(f"· 跳过 {lnk_path}（{getattr(e, 'excepinfo', None) and e.excepinfo[2] or e}）")
    if not touched:
        print("· 桌面快捷方式已指向最新产物，无需改动")
    return touched


def _bump_version(old: str, kind: str) -> str:
    """按档位加一：major/minor/patch。"""
    m = _SEMVER.match(old)
    if not m:
        sys.exit(f"当前 VERSION 内容非法：{old!r}")
    ma, mi, pa = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if kind == "major":
        return f"{ma + 1}.0.0"
    if kind == "minor":
        return f"{ma}.{mi + 1}.0"
    if kind == "patch":
        return f"{ma}.{mi}.{pa + 1}"
    sys.exit(f"未知档位：{kind}（可选 major/minor/patch）")


# ------------------------------------------------------------ GitHub 推送
def _has_remote() -> bool:
    r = subprocess.run(["git", "remote", "get-url", "origin"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0 and (r.stdout or "").strip() != ""


def do_push(tag: str) -> None:
    """推送 main 与发版 tag 到 GitHub。失败给出可执行的补救命令，不吞错。"""
    if not _has_remote():
        print("· 未配置 origin 远端，跳过推送。"
              "配置后手动推：git remote add origin git@github.com:StuTuse/Subtitle-Studio.git")
        return
    print("\n· 推送到 GitHub（main + tag）…")
    run(["git", "push", "origin", "main", tag], check=False, capture=True)
    # 复核：远端引用里能看到刚发的 tag 才算成功
    r = subprocess.run(["git", "ls-remote", "--tags", "origin", tag],
                       cwd=ROOT, capture_output=True, text=True)
    if tag in (r.stdout or ""):
        print(f"✓ 已同步到 GitHub：main + {tag}")
    else:
        err = ((r.stderr or "") + (r.stdout or "")).strip()
        sys.exit(f"× 推送未确认（tag {tag} 不在远端）。稍后手动重试：\n"
                 f"    git push origin main {tag}\n"
                 f"  报错片段：{err[:300]}")


def _gh_token() -> str:
    """取 GitHub API 令牌：环境变量优先，其次 gh 配置文件里的 oauth_token。

    环境变量若是被外部脚本拼脏的（比如把多行匹配拼成 "ghp_a ghp_b"），
    只取第一个空白分隔的 token——宁可试错也不能带拼接串去请求（必 401）。
    """
    raw = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    parts = raw.split()
    if parts:
        return parts[0]
    cfg = os.path.join(os.environ.get("USERPROFILE", ""), ".config", "gh", "hosts.yml")
    try:
        inside = False
        for ln in open(cfg, encoding="utf-8"):
            s = ln.strip()
            if s.startswith("github.com:"):
                inside = True
            elif inside and s.startswith("oauth_token:"):
                return s.split(":", 1)[1].strip()
            elif inside and ln and not ln.startswith((" ", "\t")):
                inside = False       # 出了 github.com 段
    except OSError:
        pass
    return ""


def do_release(tag: str, notes: str) -> None:
    """在 GitHub 发布 Release 并上传本版安装包（gh CLI + 令牌）。

    与 do_installer 同策略：任何前置缺失/失败只警告、不阻断发版链，
    之后可以手动补挂（网页或重跑本步）。
    """
    new = tag[1:] if tag.startswith("v") else tag
    setup = os.path.join(ROOT, "installer", f"SubtitleStudio-{new}-setup.exe")
    gh = shutil.which("gh")
    tok = _gh_token()
    if not gh or not tok:
        miss = "gh CLI" if not gh else "GitHub 令牌"
        print(f"· 缺少 {miss}，跳过 GitHub Release。"
              f"可在网页手动上传：\n  https://github.com/StuTuse/Subtitle-Studio/releases/new?tag={tag}")
        return
    if not os.path.isfile(setup):
        print("· 未找到本版安装包（--build 会生成），跳过 GitHub Release")
        return
    env = dict(os.environ)
    env["GH_TOKEN"] = tok
    env.pop("GITHUB_TOKEN", None)
    print(f"\n· 发布 GitHub Release {tag}（上传安装包，约 1~2 分钟）…")
    body = (f"**Subtitle Studio {new}**\n\n{notes.strip()}\n\n"
            "---\n\n下载下方的 **setup.exe** 双击安装即可（免管理员权限，"
            "约 90 MB）。装完自动启动一次，首次使用会进入「环境体检」，"
            "缺的组件可一键补装。")
    r = subprocess.run(
        [gh, "release", "create", tag, setup, "--title",
         f"Subtitle Studio {new}", "--notes", body, "--target", "main"],
        cwd=ROOT, capture_output=True, text=True, env=env)
    out = (r.stdout or "").strip()
    if r.returncode == 0 and "releases" in out:
        print(f"✓ GitHub Release 已发布：{out.splitlines()[-1]}")
        return
    err = (r.stderr or out or "").strip()
    if "already exists" in err:      # Release 已存在：只补传/覆盖安装包文件
        # gh CLI 的上传子命令是 `gh release upload`（没有 upload-file 这个
        # 子命令——写错了 already-exists 分支永远走不到成功路径）
        r2 = subprocess.run(
            [gh, "release", "upload", tag, setup, "--clobber"],
            cwd=ROOT, capture_output=True, text=True, env=env)
        if r2.returncode == 0:
            print(f"✓ Release {tag} 已存在，安装包已补传/覆盖")
        else:
            print(f"· 安装包补传失败（不阻断发版）：{(r2.stderr or '')[:200]}")
    else:
        print(f"· GitHub Release 发布失败（不阻断发版，可稍后手动补挂）：{err[:260]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Subtitle Studio 发版")
    ap.add_argument("version", nargs="?", help="新版本号，如 1.1.0；省略则进入交互模式")
    ap.add_argument("-m", "--notes", default="", help="本版发布说明（写进 CHANGELOG）")
    ap.add_argument("--bump", choices=("major", "minor", "patch"), metavar="LEVEL",
                    help="不算版本号：在当前版本上按档位加一后发版（与 version 二选一）")
    ap.add_argument("--build", action="store_true", help="发布后执行 PyInstaller 打包")
    ap.add_argument("--build-only", action="store_true", help="不发版，仅打包")
    ap.add_argument("--sync-shortcut", action="store_true",
                    help="不发版不打包：只把桌面快捷方式同步到 dist 产物")
    ap.add_argument("--push", action="store_true",
                    help="发版完成后自动推送 main + tag 到 GitHub（默认不推）")
    ap.add_argument("--dry-run", action="store_true", help="只演练不写入")
    args = ap.parse_args()

    if args.sync_shortcut:
        exe = os.path.join(ROOT, "dist", "Subtitle Studio", "Subtitle Studio.exe")
        if not os.path.isfile(exe):
            sys.exit("找不到打包产物，先打包再同步：python release.py --build-only\n  " + exe)
        sync_shortcut(exe)
        return 0

    if args.build_only:
        do_build()
        return 0

    old = read_version()
    new = args.version or (_bump_version(old, args.bump) if args.bump else "")
    if not new:
        new = input(f"当前版本 {old}，输入新版本号（回车=不改版本仅打包）: ").strip()
        if not new:
            do_build()
            return 0
    if args.version and args.bump:
        sys.exit("--bump 与直接给版本号二选一")

    if new == old:
        print(f"· 版本未变（{old}），跳过发版")
        if args.build:
            do_build()
        return 0

    print(f"· 发版 {old} -> {new}（{bump_kind(old, new)}）")
    ensure_repo()
    ensure_identity()

    # 工作区必须干净：避免把无关改动混进发版提交
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty and not args.dry_run:
        sys.exit("工作区有未提交改动，请先提交或 stash 后再发版：\n" +
                 "\n".join("  " + l for l in dirty.splitlines()[:10]))

    tag = f"v{new}"
    existing = subprocess.run(["git", "tag", "-l", tag], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    if existing:
        sys.exit(f"tag {tag} 已存在，请勿重复发版")

    if args.dry_run:
        print(f"  (dry-run) 将写入 VERSION={new}、更新 CHANGELOG、"
              f"提交并打 tag {tag}" + ("、执行打包" if args.build else "")
              + ("、推送到 GitHub" if args.push else ""))
        return 0

    write_version(new)
    update_changelog(old, new, args.notes)
    git("add", "VERSION", "CHANGELOG.md")
    git("commit", "-m", f"release: v{new}")
    git("tag", "-a", tag, "-m", f"v{new}")
    print(f"✓ 已提交并打 tag {tag}")

    if args.build:
        do_build()

    if args.push:
        do_push(tag)
        do_release(tag, args.notes)
    else:
        print(f"· 未推送（--push 可在发版后自动推：main + tag {tag}，并挂 GitHub Release）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
