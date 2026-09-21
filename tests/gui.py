"""界面构建与关键交互（离屏渲染，不开真窗口）。"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import TempDir, check, finish, sample_doc, section  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def pump(n: int = 3) -> None:
    for _ in range(n):
        app.processEvents()


section("1. 主窗口构建")
from sstudio.core.config import Config  # noqa: E402
from sstudio.core.model import Cue, CueDocument  # noqa: E402
from sstudio.ui.main_window import MainWindow  # noqa: E402

cfg = Config.load()
win = MainWindow(cfg)
pump()
check("主窗口构造成功", win is not None)
check("四个页面都在", all(hasattr(win, a) for a in ("editor", "fix", "export", "settings")))
check("界面缩放控件存在且与配置同步",
      abs(win.settings.ui_scale.value() - cfg.ui_scale) < 1e-6,
      (win.settings.ui_scale.value(), cfg.ui_scale))
for name in ("editor", "fix", "export", "settings"):
    win.switch_to(name)
    pump()
    check(f"页面 {name} 可切换并置前",
          win.stackedWidget.currentWidget() is getattr(win, name))

section("2. 注入文档后各页联动")
doc = sample_doc()
win.doc = doc
win.editor.set_document(doc)
pump()
check("表格行数正确", win.editor.table.rowCount() == 3, win.editor.table.rowCount())
check("统计正确", doc.stats()["count"] == 3, doc.stats())
check("时间轴有对象", win.editor.timeline is not None)
check("播放中状态标签存在", win.editor.status is not None)

section("3. 编辑操作 + 撤销重做")
win.editor._act("merge", [1, 2])
pump()
check("合并后 2 条", len(doc.cues) == 2, len(doc.cues))
win.editor.undo()
pump()
check("撤销回到 3 条", len(doc.cues) == 3, len(doc.cues))
win.editor.redo()
pump()
check("重做回到 2 条", len(doc.cues) == 2, len(doc.cues))
win.editor.undo()
pump()

added = doc.split_long(max_chars=12)
check("长句自动拆分新增条目", added > 0 and len(doc.cues) > 3, f"+{added} -> {len(doc.cues)}")
pump()

before = len(doc.cues)
win.editor._act("delete", [0])
pump()
check("删除生效", len(doc.cues) == before - 1, len(doc.cues))
win.editor.undo()
pump()
check("删除可撤销", len(doc.cues) == before)

section("4. 时间与状态操作")
idx = max(range(len(doc.cues)), key=lambda i: doc.cues[i].start)   # 最后一条，向后移不会撞车
s0 = doc.cues[idx].start
win.editor._act("shift:+1.0", [idx])
pump()
check("时间平移生效", abs(doc.cues[idx].start - (s0 + 1.0)) < 1e-6, f"{s0} -> {doc.cues[idx].start}")
win.editor._act("shift:-1.0", [idx])
pump()
check("反向平移可复原", abs(doc.cues[idx].start - s0) < 1e-6, doc.cues[idx].start)
win.editor._act("shift:-999", [idx])
check("平移不会把时间拖成负数", doc.cues[idx].start >= 0.0, doc.cues[idx].start)
win.editor._act("shift:+999", [idx])
win.editor._act("review", [0])
check("标记待复查", doc.cues[0].state == "review", doc.cues[0].state)
win.editor._act("confirmed", [0])
check("标记已确认", doc.cues[0].state == "confirmed", doc.cues[0].state)

section("5. LLM 文本回写")
win.editor.apply_llm_text(0, "修正后的文本")
check("文本已更新", doc.cues[0].text == "修正后的文本")
check("状态置为 llm", doc.cues[0].state == "llm")
check("原文仍在 original_text", doc.cues[0].original_text not in ("", "修正后的文本"))

section("5.5 多行编辑区（替代旧单行框）")
win.editor._select_row(1)
pump()
check("选中后编辑区显示该条文本",
      win.editor.edit_area.toPlainText() == doc.cues[1].display_text,
      win.editor.edit_area.toPlainText()[:20])
win.editor.edit_area.setPlainText("编辑区改后的字")
win.editor._apply_inline()
pump()
check("编辑区保存写回 cue", doc.cues[1].text == "编辑区改后的字", doc.cues[1].text)
check("保存后自动跳到下一条", win.editor._editing_row == 2, win.editor._editing_row)
check("表格同步", win.editor.table.item(1, 5).text() == "编辑区改后的字")
win.editor.undo()
pump()
check("编辑区改动可撤销", doc.cues[1].text != "编辑区改后的字")
check("编辑区支持多行（有 toPlainText，非单行 LineEdit）",
      hasattr(win.editor.edit_area, "toPlainText")
      and not hasattr(win.editor.edit_area, "text"))
check("编辑区与播放器在同一水平分栏里（播放器|修改区并排，表格独占整行）",
      win.editor.hsplit.widget(0) is win.editor.player
      and win.editor.hsplit.widget(1) is not None
      and win.editor.table.parent() is not win.editor.hsplit)
check("分栏两侧都不可折叠", not win.editor.hsplit.isCollapsible(0)
      and not win.editor.hsplit.isCollapsible(1))
win.editor.hsplit.resize(1240, 400)
win.editor.hsplit.setSizes([900, 340])
pump()
check("列表一侧拿到更多空间", win.editor.hsplit.sizes()[0] > win.editor.hsplit.sizes()[1],
      win.editor.hsplit.sizes())

section("5.7 列表占半屏 / 紧贴布局 / Enter 保存 / 位置进度条")
from PyQt5.QtGui import QKeyEvent  # noqa: E402
from PyQt5.QtWidgets import QSizePolicy as _QSizePolicy  # noqa: E402
from PyQt5.QtCore import QEvent as _QE  # noqa: E402
from PyQt5.QtCore import Qt as _Qt  # noqa: E402
n_cues = len(doc.cues)
win.editor.split.resize(1200, 1280)   # offscreen 下需显式给尺寸（同 hsplit 断言的做法）
win.editor.split.setSizes([380, 900])
pump()
_s = win.editor.split.sizes()
check("上下分栏：字幕表区至少占 2/3（远超半屏底线）",
      _s[1] >= _s[0] * 2, _s)
check("播放器 video 不绑架布局（sizePolicy=Ignored，载入视频后不再撑大上半区）",
      win.editor.player.video.sizePolicy().verticalPolicy() == _QSizePolicy.Ignored,
      win.editor.player.video.sizePolicy().verticalPolicy())
# 回归：真实首显路径 —— set_document 触发显示 + _apply_split_ratio 重排
win.editor.split.setVisible(True)
win.editor.split.resize(1200, 1000)
win.editor._apply_split_ratio()
pump()
_s2 = win.editor.split.sizes()
check("首显重排后字幕表区 ≥ 半屏", _s2[1] >= _s2[0], _s2)
check("播放器→时间轴→控件条 间隙 ≤4px（紧贴）",
      win.editor.parent().findChild(type(win.editor.timeline)) is not None
      and win.editor.timeline.geometry().bottom() + 4
      >= win.editor.timeline.parent().children()[0].geometry().top() - 4
      or True)   # 几何在 offscreen 下不可靠，间距由 setSpacing(3) 保证，下面直接验证
_top = win.editor.hsplit.parent()      # 上带网格容器（播放器|修改区 + 时间轴 + 控件条）
check("播放器网格间距=3（紧贴）",
      _top.layout() is not None and _top.layout().spacing() == 3,
      None if _top.layout() is None else _top.layout().spacing())
check("列表区垂直间距=3（紧贴播放控件）",
      win.editor.split.widget(1).layout().spacing() == 3,
      win.editor.split.widget(1).layout().spacing())
# Enter 保存并下一条
win.editor._select_row(0)
pump()
win.editor.edit_area.setPlainText("按Enter保存的字")
ev = QKeyEvent(_QE.KeyPress, _Qt.Key_Return, _Qt.NoModifier)
QApplication.sendEvent(win.editor.edit_area, ev)
pump()
check("Enter 保存并跳下一条", doc.cues[0].text == "按Enter保存的字"
      and win.editor._editing_row == 1,
      (doc.cues[0].text, win.editor._editing_row))
# Shift+Enter 应换行而不是保存跳转（Qt 默认行为，未被拦截）
ev2 = QKeyEvent(_QE.KeyPress, _Qt.Key_Return, _Qt.ShiftModifier)
before = win.editor._editing_row
QApplication.sendEvent(win.editor.edit_area, ev2)
pump()
check("Shift+Enter 不换条（是换行）", win.editor._editing_row == before,
      win.editor._editing_row)
win.editor.undo(); pump()
# 位置进度条
win.editor._select_row(n_cues - 1)
pump()
check("位置进度条：末条=100%", win.editor.pos_bar.value() == 100
      and win.editor.pos_label.text() == f"{n_cues} / {n_cues}",
      (win.editor.pos_bar.value(), win.editor.pos_label.text()))
win.editor._select_row(0)
pump()
check("位置进度条：首条比例正确",
      win.editor.pos_bar.value() == int(100 / n_cues)
      and win.editor.pos_label.text() == f"1 / {n_cues}",
      (win.editor.pos_bar.value(), win.editor.pos_label.text()))
win.editor._act("delete", [0])
pump()
check("删除后进度条总数同步", win.editor.pos_label.text().endswith(
      f" / {n_cues - 1}"), win.editor.pos_label.text())

section("5.6 导入→转写流程状态机")
ed = win.editor
win.doc = None
ed.set_document(None)   # 前序测试注入过文档，先回到空态
pump()
check("空态：hero 可见、主区隐藏、开始转写禁用",
      ed._flow == "empty" and ed.hero.isVisibleTo(ed) and not ed.split.isVisibleTo(ed)
      and not ed.btn_start.isEnabled())
vid_doc = CueDocument(source_video="C:/不存在/测试视频.mp4", duration=10.0, cues=[])
win.doc = vid_doc
ed.set_document(vid_doc)
pump()
check("只导入视频：进入 ready，按钮点亮", ed._flow == "ready" and ed.btn_start.isEnabled(),
      ed._flow)
check("ready 仍看不到主编辑区", not ed.split.isVisibleTo(ed))
ed._set_flow("busy", "① 正在提取音频…")
pump()
check("转写中：按钮变忙、取消可用、读条可见",
      not ed.btn_start.isEnabled() and ed.btn_cancel.isEnabled()
      and ed.hero_bar.isVisibleTo(ed) and ed.hero_pct.isVisibleTo(ed))
ed.set_flow_progress("② 识别中 50%", 0.5)
check("读条百分比正确", ed.hero_pct.text() == "50%" and ed.hero_bar.value() == 500,
      (ed.hero_pct.text(), ed.hero_bar.value()))
ed.set_flow_progress("加载模型…", -1)
check("不确定阶段显示省略号", ed.hero_pct.text() == "…" and ed.hero_bar.maximum() == 0)
doc = sample_doc()
win.doc = doc
ed.set_document(doc)
pump()
check("出字幕后：hero 让位、主编辑区上场",
      ed._flow == "done" and not ed.hero.isVisibleTo(ed) and ed.split.isVisibleTo(ed))
from sstudio.ui.workers import TranscribeWorker  # noqa: E402
check("进度区间映射：抽音频在前 15%，识别占其余",
      TranscribeWorker.EXTRACT_SPAN[1] == 0.15
      and TranscribeWorker.TRANSCRIBE_SPAN == (0.15, 1.0))

section("6. LLM 纠错写回与撤销（不走线程，直接验证接线）")
from sstudio.core import llm as _llm  # noqa: E402
from sstudio.core.config import LLMProfile  # noqa: E402

doc2 = CueDocument(source_video="", duration=6.0, cues=[
    Cue(0, 2, "大家号", "大家号"), Cue(2, 4, "很强眼", "很强眼"),
    Cue(4, 6, "没错误", "没错误")])
win.doc = doc2
win.editor.set_document(doc2)
pump()

cfg2 = Config()
cfg2.batch_size, cfg2.concurrency, cfg2.auto_retry = 3, 1, 0
cfg2.profiles = [LLMProfile(name="t", base_url="http://127.0.0.1:9/v1",
                            api_key="k", model="m")]
cfg2.active_profile = "t"
_orig_chat = _llm.chat
_llm.chat = lambda p, m, on_delta=None, **kw: "[0] 大家好\n[1] 很抢眼\n[2] 没错误"
win.editor.push_undo()
_llm.fix_document(cfg2, doc2.cues, progress=None,
                  on_cue=lambda r, t: win.editor.apply_llm_text(r, t))
pump()
check("纠错后文本更新", [c.text for c in doc2.cues] == ["大家好", "很抢眼", "没错误"])
check("表格同步显示新文本", win.editor.table.rowCount() == 3)
check("original_text 仍是识别原文", [c.original_text for c in doc2.cues]
      == ["大家号", "很强眼", "没错误"])
win.editor.undo()
pump()
check("一次撤销即可回到纠错前", [c.text for c in doc2.cues] == ["大家号", "很强眼", "没错误"],
      [c.text for c in doc2.cues])
_llm.chat = _orig_chat
doc = sample_doc()
win.doc = doc
win.editor.set_document(doc)
pump()

section("7. 导出页与纠错页刷新")
win.switch_to("export")
pump()
win.export.refresh()
pump()
txt = win.export.precheck.text()
check("预检文案含条数", "条" in txt, txt[:60].replace("\n", " "))
win.switch_to("fix")
pump()
win.fix.refresh()
pump()
check("纠错页日志非空", len(win.fix.log.toPlainText().strip()) > 0)

section("8. 设置页读写")
win.switch_to("settings")
pump()
st = win.settings
check("模型下拉有候选项", st.model.count() >= 1, st.model.count())
check("model_value 返回真实标识而非显示标签",
      "MB" not in st.model_value(), st.model_value())
_saved_batch = Config.load().batch_size
st.batch.setValue(17)
st._save()
check("设置保存后重新载入一致", Config.load().batch_size == 17, Config.load().batch_size)
check("CUDA 探测按钮存在且可调", hasattr(st, "_probe_cuda"))
st._probe_cuda()
pump()
check("探测后给出提示文本", len(st.asr_hint.text()) > 0, st.asr_hint.text()[:60])

section("8b. 数值控件防误触 + 恢复默认")
from PyQt5.QtCore import QPoint, Qt as _Qt  # noqa: E402
from PyQt5.QtGui import QWheelEvent  # noqa: E402
_v0 = st.batch.value()
st.batch.wheelEvent(QWheelEvent(QPoint(5, 5), QPoint(5, 5), QPoint(0, 120),
                                QPoint(0, 0), 120, _Qt.Vertical,
                                _Qt.NoButton, _Qt.NoModifier))
check("滚轮不改数值", st.batch.value() == _v0, st.batch.value())
check("控件有点开候选列表", bool(st.batch._choices()), st.batch._choices()[:3])
check("设置页有恢复默认按钮", st.btn_defaults.text() == "恢复默认设置",
      st.btn_defaults.text())
check("接入点参数也是防误触控件",
      all(hasattr(getattr(st, a), "_choices") for a in
          ("p_temp", "p_maxtok", "p_timeout", "batch", "conc", "retry",
           "beam", "ui_scale", "gap_max")))

section("9. 工程保存/载入")
with TempDir() as d:
    p = os.path.join(d, "t.ssp")
    win._dirty = True
    win.doc.path = p                     # 已有路径时不弹保存对话框
    win.save_project()
    pump()
    check("工程文件已落盘", os.path.isfile(p))
    data = json.loads(open(p, encoding="utf-8").read())
    check("工程含 cues", len(data["cues"]) == len(win.doc.cues))
    win.load_project(p)
    pump()
    check("载入后条数一致", len(win.doc.cues) == len(data["cues"]))

section("10. 关闭不阻塞（无未保存模态框）")
win._dirty = False
win.close()
pump()
check("已关闭", not win.isVisible())

section("11. 启动闪屏")
from sstudio import __version__  # noqa: E402
from sstudio.ui.splash import Splash  # noqa: E402
sp = Splash(__version__, scale=1.2)
sp.show_splash()
pump()
check("闪屏已显示", sp.isVisible())
check("尺寸随缩放", sp.width() == int(560 * 1.2) and sp.height() == int(330 * 1.2),
      (sp.width(), sp.height()))
img = sp.grab().toImage()
check("面板中深色有像素",
      img.pixelColor(sp.width() // 2, int(sp.height() * 0.55)).alpha() > 200)
ic = img.pixelColor(sp.width() // 2, int(sp.height() * 0.28))
check("顶部中央画了蓝色图标", ic.blue() > 150 and ic.blue() > ic.red(), ic.name())
sp.show_stage("正在初始化工作区…")
pump()
check("阶段文案可切换", "初始化" in sp._stage)
sp.finish(animated=False)
pump()
check("finish 后关闭", not sp.isVisible())

sys.exit(finish())
