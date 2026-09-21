# -*- coding: utf-8 -*-
"""「恢复默认设置」测试：大模型接入点（API Key/地址/模型名）必须保留，其余归零；
UI 确认走原生 QMessageBox（同步返回），点「是」后必须真的执行并落盘。"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, section  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
os.environ["SUBTITLE_STUDIO_HOME"] = tempfile.mkdtemp(prefix="ss-reset-test-")

from sstudio.core.config import Config, LLMProfile  # noqa: E402

section("1. reset_to_defaults：保留接入点，其余归零")
cfg = Config.load()
cfg.theme = "dark"
cfg.batch_size = 7
cfg.concurrency = 9
cfg.auto_retry = 5
cfg.strict_mode = False
cfg.beam_size = 9
cfg.vad = False
cfg.profiles = [
    LLMProfile(name="本地网关", base_url="http://127.0.0.1:8790/v1",
               api_key="sk-local-keep", model="Qwen3.8-Flash-Next", max_tokens=9984),
    LLMProfile(name="DeepSeek", base_url="https://api.deepseek.com/v1",
               api_key="sk-deep-keep", model="deepseek-chat"),
]
cfg.active_profile = "本地网关"
cfg.save()

cfg.reset_to_defaults()
cfg.save()
back = Config.load()

check("API Key 原样保留",
      back.profiles[0].api_key == "sk-local-keep" and back.profiles[1].api_key == "sk-deep-keep",
      [p.api_key for p in back.profiles])
check("访问地址原样保留",
      back.profiles[0].base_url == "http://127.0.0.1:8790/v1"
      and back.profiles[1].base_url == "https://api.deepseek.com/v1",
      [p.base_url for p in back.profiles])
check("模型名/max_tokens 原样保留",
      back.profiles[0].model == "Qwen3.8-Flash-Next" and back.profiles[0].max_tokens == 9984,
      (back.profiles[0].model, back.profiles[0].max_tokens))
check("profile 数量与激活项保留",
      len(back.profiles) == 2 and back.active_profile == "本地网关",
      (len(back.profiles), back.active_profile))
d = Config()
for f, label in (("theme", "主题"), ("batch_size", "批量"), ("concurrency", "并发"),
                 ("auto_retry", "重试"), ("strict_mode", "严格模式"),
                 ("beam_size", "beam"), ("vad", "VAD")):
    check(f"{label}已恢复出厂值", getattr(back, f) == getattr(d, f),
          (getattr(back, f), getattr(d, f)))

section("2. UI：确认框点「是」后真的执行（原生 QMessageBox，同步返回）")
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402
app = QApplication.instance() or QApplication([])

cfg2 = Config.load()
cfg2.theme = "dark"
cfg2.batch_size = 7
cfg2.save()

from sstudio.ui.main_window import MainWindow  # noqa: E402
win = MainWindow()
from sstudio.ui.settings_page import SettingsInterface  # noqa: E402
page = win.findChild(SettingsInterface)
check("设置页已创建", page is not None)

# 拦截确认框：返回 Yes，同时验证它问的是原生 QMessageBox（不是 qfluentwidgets 弹层）
seen = {}
def fake_question(parent, title, text, buttons, default):
    seen["title"] = title
    seen["text"] = text
    seen["parent_is_window"] = parent is win
    seen["native"] = True
    return QMessageBox.Yes
QMessageBox.question = staticmethod(fake_question)

page._restore_defaults()
check("确认框用的是原生 QMessageBox（无弹层死锁风险）", seen.get("native") is True)
check("确认文案说明会保留 Key", "保留" in seen.get("text", ""), seen.get("text", "")[:60])

back2 = Config.load()
check("点「是」后设置已落盘重置",
      back2.theme == "auto" and back2.batch_size == 30,
      (back2.theme, back2.batch_size))
check("落盘后 API Key 仍在",
      back2.profiles[0].api_key == "sk-local-keep", back2.profiles[0].api_key)
check("落盘后地址仍在",
      any(p.base_url == "http://127.0.0.1:8790/v1" for p in back2.profiles),
      [p.base_url for p in back2.profiles])

# 点「否」必须什么都不改
cfg3 = Config.load()
cfg3.theme = "dark"
cfg3.save()
QMessageBox.question = staticmethod(
    lambda *a, **k: QMessageBox.No)
page._restore_defaults()
back3 = Config.load()
check("点「否」不改动任何设置", back3.theme == "dark", back3.theme)

finish()
