# -*- coding: utf-8 -*-
"""内联双语机制：``S("中文", "English")`` 按当前界面语言取一种。

设计动机（为什么不用 Qt .ts 翻译工作流）
----------------------------------------
* 全应用约 1241 条中文串散落在 20 个文件里，Qt Linguist 的 ts/qm 流程要求
  每条字面量先包 ``self.tr()`` 再由 lupdate 扫描、翻译、编译 qm、运行时
  QTranslator 安装——一次性改写量是全量的两倍，还引入构建链依赖。
* 内联双语把「源串」与「译串」写在同一个调用点：零构建链、零扫描步骤，
  新增界面时顺手写两个参数即可。默认 zh 返回第一个参数，**与历史源码
  字面量完全一致**——所有既有测试钉子（btn 文本、窗口标题断言）在
  zh 下逐字节不变。
* 切换语言 = 改 ``set_language("en")`` + 重建界面页（字符串在构建期取定），
  由设置页提供开关并在保存后提示重启生效。

分阶段接入：本模块先于 UI 落地，随后各界面文件逐个把硬编码中文换成
``S(中文, English)``。没换到的串在 en 模式下仍显示中文——渐进切换，
绝无「半英文界面崩溃」的风险面。
"""

from __future__ import annotations

import os

_current: str = ""

# 模块导入时读环境变量（测试/自动化可用 SUBTITLE_STUDIO_LANG=en 直定）；
# 正常路径由 config.lang → set_language() 在启动早期写入。
_LANG = (os.environ.get("SUBTITLE_STUDIO_LANG") or "").strip().lower()
if _LANG in ("en", "en-us", "english"):
    _current = "en"

VALID = ("zh", "en")


def set_language(lang: str) -> None:
    """设置当前界面语言。非法值回落 zh。"""
    global _current
    _current = (lang or "").strip().lower()
    if _current not in VALID:
        _current = "zh"


def current_language() -> str:
    return _current or "zh"


def is_en() -> bool:
    return _current == "en"


def S(zh: str, en: str) -> str:
    """双语取值：zh 模式返回 zh（与历史字面量一致），en 模式返回 en。

    en 串为空时兜底回 zh——译文缺失绝不显示空按钮。
    """
    if is_en():
        return en or zh
    return zh


def SS(zh: str, en: str) -> str:
    """S() 的别名，便于 grep 区分「双语接入点」。保留单一实现。"""
    return S(zh, en)
