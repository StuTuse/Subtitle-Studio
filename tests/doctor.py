# -*- coding: utf-8 -*-
"""环境体检（doctor + 首启向导）测试。全程离线：pip 安装用假实现替换。"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, section  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sstudio.core import doctor  # noqa: E402
from sstudio.core.config import Config  # noqa: E402

section("1. 检查项结构与判定")
items = doctor.check_all()
check("返回非空列表", isinstance(items, list) and len(items) >= 5, len(items))
ids = [i.id for i in items]
check("id 不重复", len(ids) == len(set(ids)), ids)
for it in items:
    check(f"「{it.id}」有标题与 why", bool(it.title) and isinstance(it.why, str))
    check(f"「{it.id}」级别合法", it.level in ("required", "recommend", "optional"), it.level)
    check(f"「{it.id}」ok 与 fixable 自洽",
          (not it.ok) or (not it.fixable), (it.id, it.ok, it.fix_pkgs))
required = [i for i in items if i.level == "required"]
check("存在 required 项且当前全部通过",
      required and all(i.ok for i in required), [i.id for i in required if not i.ok])
check("summary 是非空字符串", bool(doctor.summary(items)), doctor.summary(items))
check("required 全过时 summary 不含『必需』", "必需" not in doctor.summary(items))

section("2. pip 修复编排（假 pip：第 1 镜像失败 → 第 2 镜像成功）")
_calls = []


def fake_popen_fail_then_ok(cmd, **kw):
    """返回一个假进程对象：清华镜像退出码 1，其余 0。"""
    class _P:
        def __init__(self, cmd):
            self.cmd = cmd
            self.returncode = 1 if any("tuna" in c for c in cmd) else 0
            self.stdout = iter(["line1\n", "ERROR: simulate\n"] if self.returncode else ["ok\n"])

        def wait(self):
            return self.returncode
    _calls.append(cmd)
    return _P(_calls[-1])


_real_popen = doctor.subprocess.Popen
doctor.subprocess.Popen = fake_popen_fail_then_ok
try:
    ok, msg = doctor.pip_install(["faster-whisper>=1.0.0"])
    check("第 1 镜像失败后第 2 镜像接上", ok, msg)
    check("确实先试了清华再试阿里", len(_calls) == 2 and
          any("tuna" in c for c in _calls[0]) and any("aliyun" in c for c in _calls[1]),
          [c[4:6] for c in _calls])
    check("命令走 python -m pip", all(c[1:3] == ["-m", "pip"] for c in _calls))
finally:
    doctor.subprocess.Popen = _real_popen

section("3. pip 修复编排（全部镜像失败）")
_calls.clear()


def fake_popen_all_fail(cmd, **kw):
    class _P:
        returncode = 1
        stdout = iter(["ERROR: no net\n"])

        def wait(self):
            return 1
    _calls.append(cmd)
    return _P()


doctor.subprocess.Popen = fake_popen_all_fail
try:
    ok, msg = doctor.pip_install(["pyav>=12.0.0"])
    check("全失败返回 False", not ok)
    check("失败信息含镜像名", "清华" in msg and "阿里" in msg, msg[:80])
    check("试完全部镜像", len(_calls) == len(doctor.PIP_INDEXES), len(_calls))
finally:
    doctor.subprocess.Popen = _real_popen

section("4. 系统代理解析（读注册表，读到就用）")
env = doctor._sys_proxy_env()
check("返回 dict，值都是 http(s) 代理或空", isinstance(env, dict) and
      all(v.startswith("http") for v in env.values()), env)

section("5. 首启向导 UI（offscreen 构建冒烟）")
from PyQt5.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)
from sstudio.ui.first_run_dialog import FirstRunDialog  # noqa: E402
cfg = Config()
dlg = FirstRunDialog(cfg)
dlg._run_checks()          # 不 exec_,同步跑检查
check("每个检查项都生成一行", dlg.rows_host.count() == len(dlg._items),
      (dlg.rows_host.count(), len(dlg._items)))
check("标题是摘要文案", dlg.title.text() == doctor.summary(dlg._items), dlg.title.text())
check("required 齐时关闭按钮是『完成』", dlg.btn_close.text().startswith("完成"),
      dlg.btn_close.text())
check("有待修复项时出现一键修复", dlg.btn_fix_all.isVisibleTo(dlg) ==
      any(i.fixable and not i.ok for i in dlg._items), dlg.btn_fix_all.text())
check("日志区默认收起", dlg.log_box.isHidden())   # 对话框未 show,只能用 isHidden
dlg._toggle_log()
check("日志区可展开", not dlg.log_box.isHidden())

sys.exit(finish())
