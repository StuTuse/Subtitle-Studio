"""模型设置页：多套 OpenAI 兼容接入点 + 提示词 + 术语表 + 原始稿件。"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List

from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, ComboBox, EditableComboBox,
                            FluentIcon as FIF, InfoBar, InfoBarPosition, LineEdit,
                            PasswordLineEdit, PrimaryPushButton, PushButton, ScrollArea,
                            SimpleCardWidget, SpinBox, DoubleSpinBox, StrongBodyLabel,
                            SubtitleLabel, SwitchButton, TextEdit, TogglePushButton)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget)

from ..core import llm
from ..core.config import BUILTIN_PRESETS, Config, LLMProfile
from ..core.i18n import S
from ..core.transcriber import discover_ct2_models
from .safe_spin import SafeDoubleSpinBox, SafeSpinBox
from .theme import CARD_MARGINS, PRIMARY_MIN_W, err_span, ok_span, warn_span
from .workers import TestLLMWorker

# 预设下拉里的特殊动作项（itemData 哨兵值；普通条目存的是 >=0 的下标）
_PRESET_SAVE = -1
_PRESET_DELETE = -2
_PRESET_SEP = -3


class SettingsInterface(QWidget):
    def __init__(self, cfg: Config, main):
        super().__init__()
        self.setObjectName("settings")
        self.setWindowTitle(S("模型与转写设置", "Models & transcription"))
        self.cfg = cfg
        self.main = main
        self._loading = True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        outer.addWidget(scroll)
        host = QWidget(scroll)
        host.setStyleSheet("background:transparent")
        scroll.setWidget(host)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(28, 18, 32, 28)
        lay.setSpacing(16)

        lay.addWidget(SubtitleLabel(S("大模型接入（OpenAI 兼容）",
                                      "LLM connection (OpenAI-compatible)"), self))
        lay.addWidget(self._build_llm(host))
        lay.addWidget(self._build_prompt(host))
        lay.addWidget(self._build_asr(host))
        lay.addWidget(self._build_misc(host))
        lay.addWidget(self._build_doctor(host))
        lay.addStretch(1)

        bar = QHBoxLayout()
        self.btn_defaults = PushButton(S("恢复默认设置", "Restore defaults"), self)
        self.btn_defaults.setIcon(FIF.SYNC)
        self.btn_defaults.setToolTip(
            S("把除「大模型接入」外的所有设置恢复到出厂默认值。\n\n"
              "适合「调乱了但不知道哪里错了」的情况。\n"
              "你填好的 API Key、访问地址、模型名都会原样保留，不会丢。",
              "Restore every setting except the LLM connection to factory defaults.\n\n"
              "For when things got messed up. Your API key, URL and model name are kept."))
        self.btn_defaults.clicked.connect(self._restore_defaults)
        bar.addWidget(self.btn_defaults)
        bar.addSpacing(12)
        bar.addStretch(1)
        self.btn_save = PrimaryPushButton(S("保存设置", "Save settings"), self)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setMinimumWidth(PRIMARY_MIN_W)
        self.btn_save.clicked.connect(self._save)
        bar.addWidget(self.btn_save)
        outer.addLayout(bar)

        self._load()
        self._loading = False

    # ------------------------------------------------------------- 大模型
    def _build_llm(self, parent) -> CardWidget:
        card = CardWidget(parent)
        v = QVBoxLayout(card)
        v.setContentsMargins(*CARD_MARGINS)
        v.setSpacing(12)

        hint = CaptionLabel(S("支持 DeepSeek / OpenAI / Kimi / 通义 / 智谱 / 豆包 / 硅基流动 / "
                              "OpenRouter / Ollama / LM Studio 等一切 OpenAI 兼容服务。"
                              "本地服务把 API Key 随便填（如 ollama）。",
                              "Works with DeepSeek / OpenAI / Kimi / Qwen / Zhipu / Doubao / "
                              "SiliconFlow / OpenRouter / Ollama / LM Studio — any "
                              "OpenAI-compatible service. For local ones the key can be anything (e.g. ollama)."), card)
        hint.setWordWrap(True)
        v.addWidget(hint)

        top = QHBoxLayout()
        top.addWidget(BodyLabel(S("接入点", "Profiles"), card))
        self.prof_list = QListWidget(card)
        # 不设 maxHeight 硬上限：右侧一列（3 按钮 + 预设下拉 + 存预设）自然高
        # ~190px，132 上限会让列表比按钮列矮一截，卡片底部左右失衡
        self.prof_list.setMinimumHeight(120)
        self.prof_list.currentRowChanged.connect(self._on_prof_row)
        top.addWidget(self.prof_list, 1)
        pv = QVBoxLayout()
        for label, icon, slot in ((S("新建", "New"), FIF.ADD, self._add_prof),
                                  (S("删除", "Delete"), FIF.DELETE, self._del_prof),
                                  (S("测试连接", "Test"), FIF.SYNC, self._test)):
            b = PushButton(icon, label, card)
            b.clicked.connect(slot)
            pv.addWidget(b)
        self.preset = ComboBox(card)
        self.preset.setPlaceholderText(S("供应商预设…", "Provider presets…"))
        self._fill_presets()
        self.preset.activated.connect(self._on_preset_activated)
        pv.addWidget(self.preset)
        self.btn_preset_save = PushButton(FIF.SAVE, S("存为预设", "Save as preset"), card)
        self.btn_preset_save.setToolTip(
            S("把当前接入点的地址/模型/Key 存为自定义预设，"
              "之后在预设下拉里一键套用；自定义预设可随时删除。",
              "Save this profile's URL/model/key as a custom preset; apply it later "
              "with one click. Custom presets can be deleted anytime."))
        self.btn_preset_save.clicked.connect(self._save_custom_preset)
        pv.addWidget(self.btn_preset_save)
        pv.addStretch(1)
        top.addLayout(pv)
        v.addLayout(top)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(14)
        self.p_name = LineEdit(card)
        self.p_name.editingFinished.connect(self._rename_prof)
        self.p_base = LineEdit(card)
        self.p_base.setPlaceholderText("https://api.deepseek.com/v1")
        self.p_key = PasswordLineEdit(card)
        self.p_key.setPlaceholderText("sk-…")
        self.p_model = LineEdit(card)
        self.p_model.setPlaceholderText("deepseek-chat")
        self.p_temp = SafeDoubleSpinBox(card)
        self.p_temp.setRange(0, 2)
        self.p_temp.setSingleStep(0.1)
        self.p_temp.setDecimals(2)
        self.p_temp.set_choices()
        self.p_maxtok = SafeSpinBox(card)
        self.p_maxtok.setRange(256, 128000)
        self.p_maxtok.setSingleStep(256)
        self.p_maxtok.setValue(4096)
        self.p_maxtok.set_choices()
        self.p_timeout = SafeSpinBox(card)
        self.p_timeout.setRange(30, 1800)
        self.p_timeout.setSingleStep(30)
        self.p_timeout.setSuffix(S(" 秒", "s"))
        self.p_timeout.setValue(300)
        self.p_timeout.set_choices()
        self.p_noreason = SwitchButton(card)
        self.p_noreason.setChecked(False)
        self.p_noreason.setOnText(S("关闭思考", "No thinking"))
        self.p_noreason.setOffText(S("正常", "Normal"))
        form.addRow(S("名称", "Name"), self.p_name)
        form.addRow("Base URL", self.p_base)
        form.addRow("API Key", self.p_key)
        form.addRow(S("模型", "Model"), self.p_model)
        form.addRow(S("温度", "Temperature"), self.p_temp)
        form.addRow(S("最大输出 token", "Max output tokens"), self.p_maxtok)
        form.addRow(S("请求超时", "Request timeout"), self.p_timeout)
        form.addRow(S("推理模型", "Reasoning model"), self.p_noreason)
        v.addLayout(form)

        self.p_adv_hint = CaptionLabel(
            S("「关闭思考」适用于 DeepSeek-R1 / Qwen3 / GLM 思考版等推理模型："
              "字幕纠错不需要推理，关掉后实测可快 10~100 倍，且不会把 max_tokens "
              "全部消耗在思考上导致正文为空。并非所有服务都支持该参数——"
              "程序会在请求后自动验证是否真的生效，关不掉时会在纠错结果里明确告知。",
              "「No thinking」 targets reasoning models (DeepSeek-R1 / Qwen3 / GLM): "
              "subtitle fixing doesn't need reasoning; turning it off is 10–100× faster "
              "and keeps max_tokens for the actual text. Not every service supports it — "
              "the app verifies automatically and says so in the fix log."), card)
        self.p_adv_hint.setWordWrap(True)
        v.addWidget(self.p_adv_hint)
        self.test_result = BodyLabel("", card)
        self.test_result.setWordWrap(True)
        self.test_result.setTextFormat(Qt.RichText)
        v.addWidget(self.test_result)
        return card

    # ------------------------------------------------------------- 提示词
    def _build_prompt(self, parent) -> CardWidget:
        card = CardWidget(parent)
        v = QVBoxLayout(card)
        v.setContentsMargins(*CARD_MARGINS)
        v.setSpacing(10)
        v.addWidget(StrongBodyLabel(S("纠错提示词与参考资料",
                                      "Fix prompt & reference material"), card))
        v.addWidget(CaptionLabel(
            S("可用占位符：<code>{payload}</code> 待修正文本、<code>{count}</code> 行数、"
              "<code>{first}</code>/<code>{last}</code> 编号范围、<code>{glossary_block}</code>、"
              "<code>{script_block}</code>。",
              "Placeholders: <code>{payload}</code> text to fix, <code>{count}</code> rows, "
              "<code>{first}</code>/<code>{last}</code> numbering, <code>{glossary_block}</code>, "
              "<code>{script_block}</code>."), card))

        row = QHBoxLayout()
        b1 = PushButton(S("恢复默认模板", "Reset template"), card)
        b1.clicked.connect(lambda: self.prompt.setPlainText(llm.DEFAULT_TEMPLATE))
        b2 = PushButton(S("复制系统提示词", "Copy system prompt"), card)
        b2.clicked.connect(lambda: QGuiApplication.clipboard().setText(llm.DEFAULT_SYSTEM))
        b3 = PushButton(S("粘贴系统提示词供查看", "Paste system prompt to view"), card)
        b3.clicked.connect(lambda: self.prompt.setPlainText(
            llm.DEFAULT_SYSTEM + "\n\n" + "=" * 30 + "\n"
            + S("（以上为系统提示词，仅供参考；此处仅编辑用户模板）",
                "(The above is the system prompt, for reference; edit only the user template here)")
            + "\n\n" + llm.DEFAULT_TEMPLATE))
        b4 = PushButton(S("插入术语表片段", "Insert glossary block"), card)
        b4.clicked.connect(lambda: self.prompt.insert("{glossary_block}"))
        row.addWidget(b1)
        row.addWidget(b2)
        row.addWidget(b3)
        row.addWidget(b4)
        row.addStretch(1)
        v.addLayout(row)

        self.prompt = TextEdit(card)
        self.prompt.setMinimumHeight(190)
        self.prompt.setPlaceholderText(llm.DEFAULT_TEMPLATE)
        v.addWidget(self.prompt)

        v.addWidget(StrongBodyLabel(S("术语表 / 专有名词（每行一个，或写 错误写法=>正确写法）",
                                      "Glossary / names (one per line, or wrong=>right)"), card))
        self.glossary = TextEdit(card)
        # 与纠错页同名编辑器统一"至少 120"（那边 setMinimumHeight(120)）：
        # 一边 max 110 一边 min 120，同一份数据两处高矮伸缩都不一致
        self.glossary.setMinimumHeight(120)
        self.glossary.setPlaceholderText(S(
            "OpenChatCut\n达芬奇=>DaVinci Resolve\n剪映=>CapCut",
            "DaVinci=>DaVinci Resolve\nPremiere=>Premiere Pro\nJianYing=>CapCut"))
        v.addWidget(self.glossary)

        srow = QHBoxLayout()
        srow.addWidget(StrongBodyLabel(S("原始稿件 / 背景资料（可选，仅供核对用字）",
                                         "Reference script / background (optional, for wording only)"), card))
        srow.addStretch(1)
        b_load = PushButton(FIF.FOLDER, S("导入文稿…", "Import doc…"), card)
        b_load.clicked.connect(self._load_script)
        b_clip = PushButton(FIF.COPY, S("从剪贴板", "From clipboard"), card)
        b_clip.clicked.connect(lambda: self.script.setPlainText(
            QGuiApplication.clipboard().text()))
        b_clear = PushButton(FIF.DELETE, S("清空", "Clear"), card)
        b_clear.clicked.connect(lambda: self.script.clear())
        srow.addWidget(b_load)
        srow.addWidget(b_clip)
        srow.addWidget(b_clear)
        v.addLayout(srow)
        self.script = TextEdit(card)
        self.script.setMinimumHeight(140)
        self.script.setPlaceholderText(S("把讲稿、PPT 文字、产品介绍粘贴进来。模型只会用它统一人名/术语，"
                                         "不会把内容补进字幕。",
                                         "Paste scripts, slides or product intro. The model only uses it to "
                                         "unify names/terms; it never adds this into subtitles."))
        v.addWidget(self.script)

        opts = QHBoxLayout()
        self.strict = SwitchButton(card)
        self.strict.setOnText(S("严格模式", "Strict"))
        self.strict.setOffText(S("严格模式", "Strict"))
        self.strict.setChecked(True)
        opts.addWidget(self.strict)
        opts.addWidget(CaptionLabel(S("严格：校验行数/编号/长度，异常自动重试并标红",
                                      "Strict: checks count/numbering/length; retries and flags issues"), card))
        opts.addSpacing(18)
        self.batch = SafeSpinBox(card)
        self.batch.setRange(5, 200)
        self.batch.setValue(30)
        self.batch.set_choices([(5, S("5 行", "5 rows")), (10, S("10 行", "10 rows")), (15, S("15 行", "15 rows")),
                                (20, S("20 行", "20 rows")), (30, S("30 行（推荐）", "30 rows (recommended)")),
                                (40, S("40 行", "40 rows")), (50, S("50 行", "50 rows")), (80, S("80 行", "80 rows")),
                                (120, S("120 行", "120 rows")), (200, S("200 行", "200 rows"))])
        opts.addWidget(BodyLabel(S("每批行数", "Rows/batch"), card))
        opts.addWidget(self.batch)
        self.conc = SafeSpinBox(card)
        self.conc.setRange(1, 8)
        self.conc.setValue(3)
        self.conc.set_choices([(1, S("1（最稳）", "1 (safest)")), (2, "2"), (3, S("3（推荐）", "3 (recommended)")),
                               (4, "4"), (6, "6"), (8, S("8（易限流）", "8 (rate-limits)"))])
        opts.addWidget(BodyLabel(S("并发", "Concurrent"), card))
        opts.addWidget(self.conc)
        self.retry = SafeSpinBox(card)
        self.retry.setRange(0, 5)
        self.retry.setValue(2)
        self.retry.set_choices([(0, S("0（不重试）", "0 (no retry)")), (1, "1"), (2, S("2（推荐）", "2 (recommended)")),
                                (3, "3"), (4, "4"), (5, S("5（最多）", "5 (max)"))])
        opts.addWidget(BodyLabel(S("重试", "Retries"), card))
        opts.addWidget(self.retry)
        opts.addStretch(1)
        v.addLayout(opts)
        return card

    # ------------------------------------------------------------- 转写
    def _build_asr(self, parent) -> CardWidget:
        card = CardWidget(parent)
        v = QVBoxLayout(card)
        v.setContentsMargins(*CARD_MARGINS)
        v.setSpacing(10)
        v.addWidget(StrongBodyLabel(S("语音转写引擎", "Speech transcription engine"), card))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.engine = ComboBox(card)
        self.engine.addItem(S("faster-whisper（本机推理，推荐）", "faster-whisper (local, recommended)"), userData="faster-whisper")
        self.engine.addItem("whisper.cpp", userData="whisper.cpp")
        self.engine.addItem(S("云端语音转写 API", "Cloud speech API"), userData="openai_api")
        self._gate_engines()
        self.engine.currentIndexChanged.connect(self._engine_changed)
        form.addRow(S("引擎", "Engine"), self.engine)

        self.model = EditableComboBox(card)
        self.model.setMinimumWidth(360)
        self.model.activated.connect(self._model_picked)
        form.addRow(S("模型", "Model"), self.model)

        self.btn_rescan = PushButton(S("重新扫描本地模型", "Rescan local models"), card)
        self.btn_rescan.clicked.connect(self._rescan_models)
        form.addRow("", self.btn_rescan)

        self.device = ComboBox(card)
        self.device.addItem(S("auto（自动选择）", "auto (pick automatically)"), userData="auto")
        self.device.addItem(S("cuda（N 卡加速）", "cuda (NVIDIA GPU)"), userData="cuda")
        self.device.addItem(S("cpu（纯 CPU）", "cpu (CPU only)"), userData="cpu")
        form.addRow(S("计算设备", "Device"), self.device)

        self.compute = ComboBox(card)
        for c in ("auto", "float16", "int8_float16", "int8", "float32"):
            self.compute.addItem(c, userData=c)
        form.addRow(S("量化精度", "Compute precision"), self.compute)

        self.lang = ComboBox(card)
        for label, val in ((S("auto（自动识别）", "auto (detect)"), "auto"),
                           ("zh（中文）", "zh"),
                           ("en（英语）", "en"), ("ja（日语）", "ja"), ("ko（韩语）", "ko"),
                           ("yue（粤语）", "yue"), ("fr（法语）", "fr"), ("de（德语）", "de"),
                           (S("translate:zh（其它语言→翻译成中文）", "translate:zh (translate to Chinese)"), "translate:zh")):
            self.lang.addItem(label, userData=val)
        form.addRow(S("语言", "Language"), self.lang)

        self.beam = SafeSpinBox(card)
        self.beam.setRange(1, 10)
        self.beam.setValue(5)
        self.beam.set_choices([(1, S("1（贪心，最快）", "1 (greedy, fastest)")), (2, "2"), (3, "3"),
                               (5, S("5（推荐）", "5 (recommended)")), (8, "8"), (10, "10")])
        form.addRow("Beam size", self.beam)

        self.vad = SwitchButton(card)
        self.vad.setChecked(True)
        form.addRow(S("VAD 静音过滤", "VAD silence filter"), self.vad)

        self.wts = SwitchButton(card)
        self.wts.setChecked(True)
        form.addRow(S("词级时间戳", "Word timestamps"), self.wts)

        self.cpt = SwitchButton(card)
        self.cpt.setChecked(False)
        form.addRow(S("依赖上文（易重复时关闭）", "Condition on context (off if repetitive)"), self.cpt)

        self.fallback = SwitchButton(card)
        self.fallback.setChecked(True)
        form.addRow(S("GPU 不可用时自动转 CPU", "Fall back to CPU when GPU unavailable"), self.fallback)

        self.mirror = ComboBox(card)
        for label, val in ((S("ModelScope（国内最快，推荐）", "ModelScope (fastest in China, recommended)"), "modelscope"),
                           (S("hf-mirror（HuggingFace 国内镜像）", "hf-mirror (HuggingFace China mirror)"), "hf-mirror"),
                           (S("huggingface.co（官方，需科学上网）", "huggingface.co (official; may need a proxy)"), "official")):
            self.mirror.addItem(label, userData=val)
        self.mirror.setToolTip(
            S("本地没有模型、需要在线下载时的来源。\n\n"
              "ModelScope：阿里国内 CDN，实测 18MB/s，下载 1.5GB 模型约 2 分钟；\n"
              "hf-mirror：HuggingFace 国内镜像，部分大文件很慢；\n"
              "官方地址：国内直连通常超时，仅海外/有代理时选用。",
              "Where to download models when missing locally.\n\n"
              "ModelScope: Alibaba CDN in China, ~18MB/s measured, ~2 min for 1.5GB;\n"
              "hf-mirror: HuggingFace China mirror, slow for some large files;\n"
              "Official: usually times out from mainland China; for overseas/proxy users."))
        form.addRow(S("模型下载源", "Model download source"), self.mirror)

        crow = QHBoxLayout()
        self.cuda_dir = LineEdit(card)
        self.cuda_dir.setPlaceholderText(S("含 cublas64_12.dll 的目录（留空=自动探测）",
                                           "Folder with cublas64_12.dll (empty = auto-detect)"))
        b_probe = PushButton(S("探测 CUDA", "Detect CUDA"), card)
        b_probe.clicked.connect(self._probe_cuda)
        crow.addWidget(self.cuda_dir, 1)
        crow.addWidget(b_probe)
        form.addRow(S("CUDA 12 运行库", "CUDA 12 runtime"), crow)
        v.addLayout(form)

        self.asr_hint = BodyLabel("", card)
        self.asr_hint.setWordWrap(True)
        self.asr_hint.setTextFormat(Qt.RichText)
        v.addWidget(self.asr_hint)
        return card

    def _probe_cuda(self) -> None:
        from html import escape as _esc
        from ..core import cuda_rt
        cuda_rt._result = None
        extra = self.cuda_dir.text().strip() or None
        rt = cuda_rt.register(extra)
        ok = rt.usable and cuda_rt.probe_loadable(rt)
        if ok:
            self.asr_hint.setText(
                (self.asr_hint.text() + "<br>" if self.asr_hint.text() else "")
                + ok_span(S(f"✓ CUDA 12 运行库就绪：{_esc(rt.cublas_dir)}",
                            f"✓ CUDA 12 runtime ready: {_esc(rt.cublas_dir)}")))
        else:
            self.asr_hint.setText(
                (self.asr_hint.text() + "<br>" if self.asr_hint.text() else "")
                + err_span(f"✕ {_esc(rt.note)}"))

    # ------------------------------------------------------------- 其它
    def _build_misc(self, parent) -> CardWidget:
        card = CardWidget(parent)
        v = QVBoxLayout(card)
        v.setContentsMargins(*CARD_MARGINS)
        v.addWidget(StrongBodyLabel(S("其它", "Misc"), card))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.theme = ComboBox(card)
        self.theme.addItems([S("auto（跟随系统）", "auto (follow system)"),
                             S("light（浅色）", "light"), S("dark（深色）", "dark")])
        form.addRow(S("主题", "Theme"), self.theme)
        self.ui_scale = SafeDoubleSpinBox(card)
        self.ui_scale.setRange(0, 3)
        self.ui_scale.setSingleStep(0.05)
        self.ui_scale.setDecimals(2)
        self.ui_scale.setSpecialValueText(S("跟随系统", "System"))
        self.ui_scale.setSuffix(" ×")
        self.ui_scale.set_choices([(0, S("跟随系统", "System")),
                                   (1.0, S("1.00 ×（推荐）", "1.00 × (recommended)")),
                                   (1.25, "1.25 ×"), (1.5, "1.50 ×"),
                                   (1.75, "1.75 ×"), (2.0, "2.00 ×")])
        self.ui_scale.setToolTip(
            S("界面整体大小倍率，重启后生效。\n\n"
              "1.00 = 按物理像素 1:1 渲染：最清晰、最紧凑（推荐，"
              "高缩放屏上不会出现放大发糊）。\n"
              "1.25 / 1.50 = 想要更大的控件时选这个。\n"
              "跟随系统 = 交给 Windows 的显示缩放决定（你的屏幕是 150%，"
              "界面会明显变大）。",
              "Overall UI size multiplier; takes effect after restart.\n\n"
              "1.00 = render 1:1 at physical pixels: sharpest and most compact "
              "(recommended; avoids blur on high-DPI screens).\n"
              "1.25 / 1.50 = larger controls.\n"
              "System = let Windows display scaling decide (on a 150% screen the UI "
              "gets noticeably bigger)."))
        form.addRow(S("界面缩放", "UI scale"), self.ui_scale)
        self.ui_lang = ComboBox(card)
        self.ui_lang.addItem("中文", userData="zh")
        self.ui_lang.addItem("English", userData="en")
        self.ui_lang.setToolTip(
            S("界面语言，重启软件后生效。",
              "UI language; takes effect after restart."))
        form.addRow(S("界面语言", "UI language"), self.ui_lang)
        self.autosave = SwitchButton(card)
        self.autosave.setChecked(True)
        form.addRow(S("自动保存工程", "Auto-save project"), self.autosave)
        self.keepaudio = SwitchButton(card)
        self.keepaudio.setChecked(False)
        form.addRow(S("保留抽取出的音频", "Keep extracted audio"), self.keepaudio)
        self.gap_auto = SwitchButton(card)
        self.gap_auto.setChecked(True)
        self.gap_auto.setOnText(S("衔接", "Join"))
        self.gap_auto.setOffText(S("留缝", "Keep gap"))
        self.gap_auto.setToolTip(
            S("连续说话时，字幕之间若只有一两帧的小空隙，播放时字幕会一闪一闪。\n\n"
              "开启（衔接）：转写完成后自动补上不超过阈值的小空隙——前一条的"
              "结束时间拉齐到后一条的开始，字幕连续不闪断。\n\n"
              "以下情况即使是小空隙也会保留（因为往往是真人换句/换人）：\n"
              "· 前一条以句号、问号、感叹号结尾（一句话说完了）\n"
              "· 前后两条标了不同的说话人\n"
              "（省略号结尾表示话没说完，照常衔接。）",
              "Tiny one-frame gaps between cues flicker during playback.\n\n"
              "Join: after transcription, closes gaps within the threshold by pulling "
              "the previous cue's end to the next cue's start, so subtitles flow.\n\n"
              "Small gaps are kept when (usually a real sentence/speaker change):\n"
              "· the previous cue ends with 。！? ! (a finished sentence)\n"
              "· the two cues have different speakers\n"
              "(ellipsis means the sentence isn't finished; still joined.)"))
        form.addRow(S("字幕空隙", "Subtitle gaps"), self.gap_auto)
        self.gap_max = SafeDoubleSpinBox(card)
        self.gap_max.setRange(0.05, 3.0)
        self.gap_max.setSingleStep(0.05)
        self.gap_max.setDecimals(2)
        self.gap_max.setSuffix(S(" 秒", "s"))
        self.gap_max.set_choices([(0.1, S("0.10 秒", "0.10 s")), (0.2, S("0.20 秒", "0.20 s")),
                                  (0.35, S("0.35 秒（推荐）", "0.35 s (recommended)")), (0.5, S("0.50 秒", "0.50 s")),
                                  (0.8, S("0.80 秒", "0.80 s")), (1.0, S("1.00 秒", "1.00 s")),
                                  (1.5, S("1.50 秒", "1.50 s")), (3.0, S("3.00 秒", "3.00 s"))])
        self.gap_max.setToolTip(S("不超过这个时长、且不是句末停顿的空隙会被衔接掉。"
                                  "0.35 秒适合大多数口播；节奏快可调小到 0.2，"
                                  "断得碎可调大到 0.5。",
                                  "Gaps within this length and not end-of-sentence pauses are joined. "
                                  "0.35s suits most speech; 0.2 for fast pace, 0.5 for choppy audio."))
        form.addRow(S("空隙阈值", "Gap threshold"), self.gap_max)
        v.addLayout(form)
        return card

    # ------------------------------------------------------------ 环境体检
    def _build_doctor(self, parent) -> CardWidget:
        card = CardWidget(parent)
        v = QVBoxLayout(card)
        v.setContentsMargins(*CARD_MARGINS)
        v.setSpacing(10)
        v.addWidget(StrongBodyLabel(S("环境体检", "Environment check"), card))
        tip = CaptionLabel(
            S("检查本地转写、视频解码、GPU 加速等组件是否就绪；"
              "缺什么可以一键从国内镜像补装。首次启动时也会自动检查。",
              "Checks local transcription, video decoding, GPU acceleration and more; "
              "missing pieces install from a China mirror with one click. Also runs "
              "automatically on first start."), card)
        tip.setWordWrap(True)
        v.addWidget(tip)
        h = QHBoxLayout()
        h.addStretch(1)
        self.btn_doctor = PrimaryPushButton(S("打开体检窗口", "Open check window"), card)
        self.btn_doctor.clicked.connect(self._open_doctor)
        h.addWidget(self.btn_doctor)
        v.addLayout(h)
        return card

    def _open_doctor(self) -> None:
        from .first_run_dialog import FirstRunDialog
        dlg = FirstRunDialog(self.cfg, parent=self.window())
        dlg.setModal(True)
        dlg.exec_()

    # ------------------------------------------------------------ 载入/保存
    def _prof_disp(self, name: str) -> str:
        """接入点名的展示层转译：默认档「默认」→ en "Default"（存储层不变）。"""
        return S("默认", "Default") if name == "默认" else name

    def _load(self) -> None:
        cfg = self.cfg
        self._loading = True
        self.prof_list.clear()
        for p in cfg.profiles:
            self.prof_list.addItem(QListWidgetItem(
                f"{self._prof_disp(p.name)}  ·  {p.model}"))
        idx = max(0, next((i for i, p in enumerate(cfg.profiles)
                           if p.name == cfg.active_profile), 0))
        self.prof_list.setCurrentRow(idx)
        self._show_profile(cfg.profiles[idx])
        self.prompt.setPlainText(cfg.prompt_template or llm.DEFAULT_TEMPLATE)
        self.glossary.setPlainText(cfg.glossary)
        self.script.setPlainText(cfg.reference_script)
        self.strict.setChecked(cfg.strict_mode)
        self.batch.setValue(cfg.batch_size)
        self.conc.setValue(cfg.concurrency)
        self.retry.setValue(cfg.auto_retry)
        i = self.engine.findData(cfg.asr_engine)
        self.engine.setCurrentIndex(max(0, i))
        self.beam.setValue(cfg.beam_size)
        self.vad.setChecked(cfg.vad)
        self.wts.setChecked(cfg.word_timestamps)
        self.cpt.setChecked(cfg.condition_on_previous_text)
        self.compute.setCurrentText(cfg.whisper_compute)
        self.theme.setCurrentText({S("light（浅色）", "light"): "light",
                                   S("dark（深色）", "dark"): "dark"}.get(
            cfg.theme, S("auto（跟随系统）", "auto (follow system)")))
        self.ui_scale.setValue(float(cfg.ui_scale))
        self._ui_scale_loaded = float(cfg.ui_scale)
        _li = self.ui_lang.findData(getattr(cfg, "lang", "zh") or "zh")
        self.ui_lang.setCurrentIndex(max(0, _li))
        self.autosave.setChecked(cfg.auto_save)
        self.keepaudio.setChecked(bool(cfg.keep_audio))
        self.gap_auto.setChecked(bool(getattr(cfg, "auto_close_gaps", True)))
        self.gap_max.setValue(float(getattr(cfg, "gap_max", 0.35) or 0.35))
        self.fallback.setChecked(bool(cfg.auto_cpu_fallback))
        mi = self.mirror.findData(getattr(cfg, "model_source", "modelscope") or "modelscope")
        self.mirror.setCurrentIndex(max(0, mi))
        self.cuda_dir.setText(getattr(cfg, "cuda_rt_dir", "") or "")
        di = self.device.findData(cfg.whisper_device)
        self.device.setCurrentIndex(max(0, di))
        ci = self.compute.findData(cfg.whisper_compute)
        self.compute.setCurrentIndex(max(0, ci))
        li = self.lang.findData(cfg.language)
        self.lang.setCurrentIndex(max(0, li))
        self._fill_models()
        self._refresh_asr_hint()
        self._loading = False

    def _save(self) -> None:
        self._collect_profile()
        cfg = self.cfg
        cfg.prompt_template = self.prompt.toPlainText().strip()
        if cfg.prompt_template == llm.DEFAULT_TEMPLATE.strip():
            cfg.prompt_template = ""
        cfg.glossary = self.glossary.toPlainText().strip()
        cfg.reference_script = self.script.toPlainText()
        cfg.strict_mode = self.strict.isChecked()
        cfg.batch_size = self.batch.value()
        cfg.concurrency = self.conc.value()
        cfg.auto_retry = self.retry.value()
        cfg.asr_engine = self.engine.currentData() or "faster-whisper"
        cfg.whisper_model = self.model_value()
        cfg.whisper_device = self.device.currentData() or "auto"
        cfg.whisper_compute = self.compute.currentData() or "auto"
        cfg.language = self.lang.currentData() or "auto"
        cfg.beam_size = self.beam.value()
        cfg.vad = self.vad.isChecked()
        cfg.word_timestamps = self.wts.isChecked()
        cfg.condition_on_previous_text = self.cpt.isChecked()
        cfg.theme = self.theme.currentText().split("（")[0]
        scale_changed = abs(float(self.ui_scale.value()) - float(getattr(
            self, "_ui_scale_loaded", self.ui_scale.value()))) > 1e-6
        cfg.ui_scale = float(self.ui_scale.value())
        lang_changed = (self.ui_lang.currentData() or "zh") != getattr(cfg, "lang", "zh")
        cfg.lang = self.ui_lang.currentData() or "zh"
        cfg.auto_save = self.autosave.isChecked()
        cfg.keep_audio = self.keepaudio.isChecked()
        cfg.auto_close_gaps = self.gap_auto.isChecked()
        cfg.gap_max = float(self.gap_max.value())
        cfg.auto_cpu_fallback = self.fallback.isChecked()
        cfg.model_source = self.mirror.currentData() or "modelscope"
        cfg.cuda_rt_dir = self.cuda_dir.text().strip()
        if not cfg.save():
            # 旧实现吞掉写盘异常：磁盘满/被占用时也显示"已保存"，重启后
            # 设置无声回滚。现在失败明确报错。
            InfoBar.error(S("保存失败", "Save failed"),
                          S("设置未能写入本地配置文件（磁盘满或被占用？）。"
                            "当前会话内设置仍生效，请检查磁盘后重试。",
                            "Settings could not be written to the config file "
                            "(disk full or locked?). They still apply for this session; "
                            "check the disk and retry."),
                          parent=self.main, position=InfoBarPosition.TOP, duration=6000)
            return
        tip = S("设置已写入本地配置文件。", "Settings written to the local config file.")
        if scale_changed:
            tip += S(" 界面缩放已更新，重启软件后生效。",
                     " UI scale updated; takes effect after restart.")
        if lang_changed:
            from ..core.i18n import set_language
            set_language(cfg.lang)
            tip += S(" 界面语言已切换（英文翻译分批接入中，未覆盖文案仍显示中文），"
                     "重启软件后完全生效。",
                     " UI language switched (translation is rolling out; uncovered "
                     "strings stay Chinese); fully effective after restart.")
        if cfg.concurrency > 2:
            tip += (S(f" 并发={cfg.concurrency}：若服务商限流（报 429 / 并发已达上限），"
                       "请调回 1–2。",
                      f" Concurrency={cfg.concurrency}: if the provider rate-limits "
                      "(HTTP 429 / concurrency cap), dial back to 1–2."))
        InfoBar.success(S("已保存", "Saved"), tip, parent=self.main,
                        position=InfoBarPosition.TOP, duration=2600)
        self.main.apply_cfg_theme()
        # 并发/批量改完立刻反映到「AI 纠错」页的控件，免得两边显示不一致
        try:
            self.main.fix.refresh_silent()
        except Exception:
            pass

    # ------------------------------------------------------- 恢复默认
    def _restore_defaults(self) -> None:
        """恢复出厂设置：**大模型接入点（API Key / 地址 / 模型名）保留不动**。

        确认框用原生 QMessageBox：同步返回、无淡出动画，杜绝
        qfluentwidgets 弹层偶发的「点了确定没反应」死锁。
        """
        from PyQt5.QtWidgets import QMessageBox
        r = QMessageBox.question(
            self.window(), S("恢复默认设置？", "Restore defaults?"),
            S("除「大模型接入」外的所有设置将恢复为出厂默认值。\n\n"
              "你填好的 API Key、访问地址、模型名都会**原样保留**，不用重新填。\n"
              "工程文件与字幕不受影响。",
              "All settings except the LLM connection will be reset to factory defaults.\n\n"
              "Your API key, URL and model name are **kept as is** — no re-entry needed.\n"
              "Project files and subtitles are unaffected."),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self.cfg.reset_to_defaults()
        self.cfg.save()
        self._load()
        InfoBar.success(S("已恢复默认", "Defaults restored"),
                        S("除大模型接入点外的设置已重置；API Key 与地址均已保留。",
                          "Everything except the LLM connection was reset; API key and URL kept."),
                        parent=self.main, position=InfoBarPosition.TOP, duration=4000)
        try:
            self.main.apply_cfg_theme()
            self.main.fix.refresh_silent()
        except Exception:
            pass

    # ------------------------------------------------------- profile 操作
    def _show_profile(self, p: LLMProfile) -> None:
        """把 profile 灌进表单（期间屏蔽写回，避免把空值覆盖进配置）。

        p_name 显示转译名（默认档 en→"Default"）；用户一旦编辑即按字面值
        存回——数据层永远是「默认」，与 active_profile 匹配不受影响。
        """
        self._loading = True
        try:
            self.p_name.setText(self._prof_disp(p.name))
            self.p_base.setText(p.base_url)
            self.p_key.setText(p.api_key)
            self.p_model.setText(p.model)
            self.p_temp.setValue(p.temperature)
            self.p_maxtok.setValue(p.max_tokens)
            self.p_timeout.setValue(int(getattr(p, "timeout", 300) or 300))
            self.p_noreason.setChecked(bool(getattr(p, "no_reasoning", False)))
        finally:
            self._loading = False

    def _on_prof_row(self, row: int) -> None:
        if row < 0 or self._loading or row >= len(self.cfg.profiles):
            return
        self._collect_profile()
        self.cfg.active_profile = self.cfg.profiles[row].name
        self._show_profile(self.cfg.profiles[row])

    def _collect_profile(self) -> None:
        if self._loading:
            return
        row = self.prof_list.currentRow()
        if row < 0 or row >= len(self.cfg.profiles):
            return
        p = self.cfg.profiles[row]
        p.base_url = self.p_base.text().strip() or p.base_url
        p.api_key = self.p_key.text().strip()
        p.model = self.p_model.text().strip() or p.model
        p.temperature = self.p_temp.value()
        p.max_tokens = self.p_maxtok.value()
        p.timeout = float(self.p_timeout.value())
        p.no_reasoning = self.p_noreason.isChecked()
        item = self.prof_list.item(row)
        if item is not None:
            item.setText(f"{self._prof_disp(p.name)}  ·  {p.model}")

    def _rename_prof(self) -> None:
        row = self.prof_list.currentRow()
        if row < 0 or self._loading:
            return
        new = self.p_name.text().strip() or S(f"接入点{row + 1}", f"Profile {row + 1}")
        if any(p.name == new for i, p in enumerate(self.cfg.profiles) if i != row):
            new = f"{new}-{row + 1}"
        old = self.cfg.profiles[row].name
        self.cfg.profiles[row].name = new
        # 只在重命名的就是当前激活接入点时才动 active_profile：以前无条件
        # 赋值，给非激活行改名会把激活指针静默切走（保存后纠错用的模型变了）。
        if self.cfg.active_profile == old:
            self.cfg.active_profile = new
        self.p_name.setText(new)
        self.prof_list.item(row).setText(
            f"{self._prof_disp(new)}  ·  {self.cfg.profiles[row].model}")

    def _add_prof(self) -> None:
        self._collect_profile()
        n = len(self.cfg.profiles) + 1
        p = LLMProfile(name=S(f"接入点{n}", f"Profile {n}"), base_url="https://api.openai.com/v1",
                       model="gpt-4o-mini")
        self.cfg.profiles.append(p)
        self.prof_list.addItem(QListWidgetItem(f"{self._prof_disp(p.name)}  ·  {p.model}"))
        self.prof_list.setCurrentRow(len(self.cfg.profiles) - 1)

    def _del_prof(self) -> None:
        row = self.prof_list.currentRow()
        if row < 0:
            return
        if len(self.cfg.profiles) <= 1:
            InfoBar.warning(S("无法删除", "Cannot delete"),
                            S("至少保留一个接入点。", "Keep at least one profile."),
                            parent=self.main, position=InfoBarPosition.TOP, duration=2200)
            return
        # 先记下表单此刻归属哪一行；删完 currentRowChanged 会把另一行装进表单，
        # 但信号里 row<0/_loading 的守卫可能不触发 _collect——真正的坑在反向：
        # 表单里还留着被删接入点的值时切换行，_collect_profile 会把死者的
        # base_url/API Key 写进幸存行。删除路径只动列表与 cfg，绝不先收集。
        del self.cfg.profiles[row]
        self.prof_list.blockSignals(True)
        self.prof_list.takeItem(row)
        self.prof_list.blockSignals(False)
        nxt = max(0, min(row, len(self.cfg.profiles) - 1))
        self.cfg.active_profile = self.cfg.profiles[nxt].name
        # 先把幸存行装进表单，再 setCurrentRow：信号触发的 _collect_profile
        # 收到的已是幸存行自己的值，等于无操作；顺序反了就会把被删项残值
        # 覆盖进幸存行（丢 Key 类数据损坏）。
        self._show_profile(self.cfg.profiles[nxt])
        self.prof_list.setCurrentRow(nxt)

    # ------------------------------------------------------- 自定义预设
    def _all_presets(self) -> List[Dict[str, str]]:
        """预设下拉的完整条目：内置 + 用户自定义（存 config.custom_presets）。"""
        return list(BUILTIN_PRESETS) + [
            p for p in (getattr(self.cfg, "custom_presets", []) or [])
            if isinstance(p, dict) and p.get("name") and p.get("base_url")]

    def _fill_presets(self) -> None:
        """重灌预设下拉：内置若干 + 分隔 + 自定义若干 + 「存为自定义预设…」。

        itemData 记条目在 _all_presets() 里的下标；动作项用特殊负值。
        （qfluentwidgets ComboBox.addItem 第二参是 icon，userData 必须走第三参。）
        """
        self.preset.clear()
        presets = self._all_presets()
        n_builtin = len(BUILTIN_PRESETS)
        for i, pre in enumerate(presets):
            label = pre["name"] + ("" if i < n_builtin else "　★")
            self.preset.addItem(label, None, i)
        if n_builtin < len(presets):
            # qfluentwidgets ComboBox 没有 addSeparator，用一条禁用分隔行代替
            self.preset.addItem("──────────", None, _PRESET_SEP)
            self.preset.setItemEnabled(self.preset.count() - 1, False)
        self.preset.addItem(S("☆ 存当前设置为自定义预设…", "☆ Save current as custom preset…"), None, _PRESET_SAVE)
        # 自定义条目的删除动作
        if len(presets) > n_builtin:
            self.preset.addItem(S("✕ 删除自定义预设…", "✕ Delete a custom preset…"), None, _PRESET_DELETE)
        self.preset.setCurrentIndex(-1)

    def _on_preset_activated(self, idx: int) -> None:
        data = self.preset.itemData(idx)
        try:
            data = int(data)
        except (TypeError, ValueError):
            self.preset.setCurrentIndex(-1)
            return
        if data == _PRESET_SEP:          # 分隔行（本就被禁用，双保险）
            self.preset.setCurrentIndex(-1)
            return
        if data == _PRESET_SAVE:
            self._save_custom_preset()
            return
        if data == _PRESET_DELETE:
            self._delete_custom_preset()
            return
        self._apply_preset(data)
        self.preset.setCurrentIndex(-1)

    def _save_custom_preset(self) -> None:
        """把当前表单里的接入点存为自定义预设（写 config 并立即落盘）。"""
        self._collect_profile()
        row = self.prof_list.currentRow()
        if row < 0:
            return
        p = self.cfg.profiles[row]
        if not p.base_url:
            InfoBar.warning(S("存不了", "Cannot save"),
                            S("先填接口地址再存预设。", "Fill the base URL before saving a preset."),
                            parent=self.main, position=InfoBarPosition.TOP,
                            duration=2600)
            return
        name = p.name or S(f"自定义{len(self.cfg.custom_presets) + 1}",
                           f"Custom {len(self.cfg.custom_presets) + 1}")
        entry = {"name": name, "base_url": p.base_url, "model": p.model,
                 "no_reasoning": "1" if p.no_reasoning else ""}
        # 同名覆盖，避免列表里堆出重复项
        self.cfg.custom_presets = [e for e in getattr(self.cfg, "custom_presets", [])
                                   if e.get("name") != name] + [entry]
        self.cfg.save()
        self._fill_presets()
        InfoBar.success("已存为自定义预设",
                        f"「{name}」已加入预设下拉，其他接入点也能一键套用。",
                        parent=self.main, position=InfoBarPosition.TOP,
                        duration=2600)

    def _delete_custom_preset(self) -> None:
        """删除一条自定义预设（不动内置预设，也不动接入点本身）。"""
        presets = self._all_presets()
        n_builtin = len(BUILTIN_PRESETS)
        custom = presets[n_builtin:]
        if not custom:
            InfoBar.warning(S("没有可删的", "Nothing to delete"),
                            S("当前没有自定义预设。", "No custom presets yet."),
                            parent=self.main, position=InfoBarPosition.TOP,
                            duration=2400)
            return
        names = [p["name"] for p in custom]
        from PyQt5.QtWidgets import QInputDialog
        sel, ok = QInputDialog.getItem(
            self, S("删除自定义预设", "Delete custom preset"),
            S("选择要删除的预设：", "Pick the preset to delete:"), names, 0, False)
        if not ok:
            return
        self.cfg.custom_presets = [e for e in self.cfg.custom_presets
                                   if e.get("name") != sel]
        self.cfg.save()
        self._fill_presets()
        InfoBar.success(S("已删除", "Deleted"),
                        S(f"预设「{sel}」已移除（接入点本身不受影响）。",
                          f"Preset 「{sel}」 removed (the profile itself is untouched)."),
                        parent=self.main, position=InfoBarPosition.TOP,
                        duration=2400)

    def _apply_preset(self, idx: int) -> None:
        presets = self._all_presets()
        if idx < 0 or idx >= len(presets):
            return
        pre = presets[idx]
        row = self.prof_list.currentRow()
        if row < 0:
            return
        p = self.cfg.profiles[row]
        p.name = pre["name"]
        p.base_url = pre["base_url"]
        p.model = pre["model"]
        p.api_key = p.api_key or pre.get("api_key", "")
        p.no_reasoning = bool(pre.get("no_reasoning")) or p.no_reasoning
        p.kind = "ollama" if "127.0.0.1" in pre["base_url"] else "openai"
        self._show_profile(p)
        self.prof_list.item(row).setText(
            f"{self._prof_disp(p.name)}  ·  {p.model}")
        self.preset.setCurrentIndex(-1)

    def _test(self) -> None:
        self._collect_profile()
        from .workers import reap
        reap(getattr(self, "_test_worker", None))   # 连点测试时安全回收上一个
        self._test_worker = None
        p = self.cfg.profile()
        self.test_result.setText(S("正在测试连接…", "Testing connection…"))
        w = TestLLMWorker(self, p)
        w.sig_done.connect(lambda r: self._test_done(r, w))
        w.sig_failed.connect(lambda m: self._test_done((False, m, 0.0), w))
        w.start()
        self._test_worker = w

    def _test_done(self, res, w) -> None:
        ok, msg, dt = res
        from html import escape as _esc
        # 服务端偶发把整页 HTML / 长 traceback 塞进错误消息：不截断会把
        # 标签撑到几百行高，设置页布局整个被顶坏
        msg = _esc((msg or "")[:200])
        if ok:
            self.test_result.setText(
                S(f"{ok_span(f'✓ 连接成功（{dt:.2f}s）')} 模型回复：{msg}",
                  f"{ok_span(f'✓ Connected ({dt:.2f}s)')} Model says: {msg}"))
        else:
            self.test_result.setText(S(f"{err_span('✕ 失败')} {msg}",
                                       f"{err_span('✕ Failed')} {msg}"))

    def shutdown(self) -> None:
        """主窗退出前收尾：测试连接线程可能还在 HTTP 在途（test_connection
        不接收 cancel），主窗作为祖先析构会连带析构运行中的 QThread 直接
        abort。等不到就 orphanize 摘掉父子关系，线程自然结束后自行回收。"""
        w = getattr(self, "_test_worker", None)
        if w is None:
            return
        from .workers import orphanize, reap
        reap(w)
        if w.isRunning():
            orphanize(w)
        self._test_worker = None

    # ----------------------------------------------------------- ASR 部分
    def _rescan_models(self) -> None:
        """重新扫描本地模型：外部 CLI 扫描有进程内缓存，先清掉再填。"""
        from ..core.transcriber import ext_cli_cache_reset
        ext_cli_cache_reset()
        self._fill_models()

    def _fill_models(self) -> None:
        typed = (self.model.currentText() or "").strip()
        self.model.blockSignals(True)
        self.model.clear()
        cands = discover_ct2_models()
        for c in cands:
            self.model.addItem(S(f"{c['name']}（{c['size']:.0f} MB）",
                                 f"{c['name']} ({c['size']:.0f} MB)"), userData=c["path"])
        for quick in ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny"):
            self.model.addItem(S(f"⤓ 在线下载 {quick}", f"⤓ Download {quick}"), userData=quick)
        cur = self.cfg.whisper_model or typed
        idx = self.model.findData(cur)
        if idx < 0 and cur:
            # 配置里存的可能是自定义路径：作为额外一项插到最前
            shown = os.path.basename(cur.rstrip("/" + "\\")) or cur
            self.model.insertItem(0, S(f"{shown}  [自定义路径]", f"{shown}  [custom path]"), userData=cur)
            idx = 0
        self.model.setCurrentIndex(max(0, idx))
        if typed and not self.model.currentText():
            self.model.setCurrentText(typed)
        self.model.blockSignals(False)
        self._model_value = self.model.currentData() or cur or "large-v3-turbo"
        self._refresh_asr_hint()

    def _model_picked(self, idx: int) -> None:
        data = self.model.itemData(idx)
        if data:
            self._model_value = data

    def model_value(self) -> str:
        """取真正要写进配置的模型标识：手输的路由/名称优先，否则用下拉选中项。"""
        typed = (self.model.currentText() or "").strip()
        if typed:
            # 下拉选中项的显示文本 == 手输文本时，优先用其 data
            idx = self.model.findText(typed)
            if idx >= 0 and self.model.itemData(idx):
                self._model_value = self.model.itemData(idx)
                return self._model_value
            if os.path.isdir(typed):
                return typed
            low = typed.lower()
            if " " not in typed and ("whisper" in low or re.match(r"^[a-z0-9._\-/]+$", low)):
                return typed
        return self._model_value or "large-v3-turbo"

    def _gate_engines(self) -> None:
        """把本机明显用不了的引擎置灰，避免用户选了才发现跑不动。"""
        try:
            try:
                from ..core.transcriber import find_external_whisper_cli
                cpp_ok = bool(find_external_whisper_cli()) or bool(
                    __import__("shutil").which("whisper-cli"))
            except Exception:
                cpp_ok = False
            for i in range(self.engine.count()):
                data = self.engine.itemData(i)
                if data == "whisper.cpp":
                    self.engine.setItemText(i, S("whisper.cpp（未检测到可执行文件）",
                                                 "whisper.cpp (executable not found)")
                                            if not cpp_ok else "whisper.cpp")
        except Exception:
            pass

    def _engine_changed(self) -> None:
        self._refresh_asr_hint()

    def _refresh_asr_hint(self) -> None:
        eng = self.engine.currentData()
        cands = discover_ct2_models()
        from html import escape as _esc
        parts = []
        if eng == "faster-whisper":
            try:
                import faster_whisper  # noqa
                parts.append(ok_span(S("✓ faster-whisper 已安装",
                                       "✓ faster-whisper installed")))
            except Exception:
                parts.append(err_span(S("✕ 未安装：pip install faster-whisper",
                                        "✕ Not installed: pip install faster-whisper")))
            from ..core import cuda_rt
            rt = cuda_rt.register(getattr(self.cfg, "cuda_rt_dir", "") or None)
            if rt.usable and cuda_rt.probe_loadable(rt):
                parts.append(ok_span(S(f"✓ CUDA 12 运行库：{_esc(rt.cublas_dir)}",
                                       f"✓ CUDA 12 runtime: {_esc(rt.cublas_dir)}")))
            else:
                parts.append(warn_span(S(f"⚠ GPU 不可用（{_esc(rt.note)}）"
                                         "—— 将自动用 CPU 识别。",
                                         f"⚠ GPU unavailable ({_esc(rt.note)}) — CPU will be used.")))
            if cands:
                # 路径是动态文本：含 & < > 时直接拼 <code> 会破坏富文本解析
                parts.append(S(f"发现 {len(cands)} 个本地 CT2 模型：",
                               f"Found {len(cands)} local CT2 model(s): ") +
                             S("；", "; ").join(f"<code>{_esc(c['path'])}</code>" for c in cands[:3]))
            else:
                parts.append(S("未发现本地 CT2 模型，将在线下载。",
                               "No local CT2 models; will download."))
        elif eng == "whisper.cpp":
            parts.append(S("需要在 PATH 中提供 whisper-cli / main.exe，并选择 ggml-*.bin 模型。",
                           "Provide whisper-cli / main.exe on PATH and pick a ggml-*.bin model."))
        else:
            parts.append(S("调用 OpenAI 兼容的 /v1/audio/transcriptions。"
                            "模型名可在下方「云端转写模型」中改（如 whisper-1、whisper-large-v3）。",
                           "Calls the OpenAI-compatible /v1/audio/transcriptions. "
                           "Change the model below (e.g. whisper-1, whisper-large-v3)."))
        self.asr_hint.setText("<br>".join(parts))

    # ------------------------------------------------------------- 其它
    def _load_script(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(self, S("导入原始稿件", "Import reference script"),
                                            self.cfg.last_dir or "",
                                            S("文本文档", "Text documents")
                                            + " (*.txt *.md *.srt *.vtt *.json *.docx);;"
                                            + S("所有文件", "All files") + " (*)")
        if not fp:
            return
        try:
            if fp.lower().endswith(".docx"):
                text = _read_docx(fp)
            else:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            self.script.setPlainText(text)
            self.cfg.last_dir = os.path.dirname(fp)
            self.cfg.save()
        except Exception as e:
            InfoBar.error(S("读取失败", "Read failed"), str(e), parent=self.main,
                          position=InfoBarPosition.TOP, duration=3500)


def _read_docx(fp: str) -> str:
    import zipfile
    from xml.etree import ElementTree as ET

    try:
        with zipfile.ZipFile(fp) as z:
            xml = z.read("word/document.xml")
    except KeyError:
        # 扩展名是 .docx 但不是 Word 包（或已损坏）：KeyError 文案用户看不懂
        raise ValueError(S("这不是有效的 Word 文档（缺 word/document.xml），请另存为 .docx 再导入",
                           "Not a valid Word document (word/document.xml missing); "
                           "re-save as .docx and import again"))
    except zipfile.BadZipFile as e:
        # 伪 docx（改了扩展名的文本/其它二进制）根本不是 zip 包：
        # 裸 "File is not a zip file" 用户看不懂，与 KeyError 同样转可读文案
        raise ValueError(S("这不是有效的 Word 文档（无法按 zip 解包），"
                           "请确认文件是真正的 .docx 再导入",
                           "Not a valid Word document (cannot open as zip); "
                           "make sure the file is a real .docx")) from e
    root = ET.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines = []
    for para in root.iter(f"{ns}p"):
        txt = "".join(t.text or "" for t in para.iter(f"{ns}t"))
        if txt.strip():
            lines.append(txt.strip())
    return "\n".join(lines)
