# -*- coding: utf-8 -*-
"""布局探针:真实视频+字幕下,量字幕表是否稳过半屏,并渲染截图。"""
import os, sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")   # 模拟用户 150% 缩放

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication

from sstudio.core.config import Config
from sstudio.core.model import Cue, CueDocument
from sstudio.ui.main_window import MainWindow

VIDEO = r"D:\Project\Tuse Creation\吐司Tuse\两个平台同步作品\26_14 OSMO360II深度体验\VOICE.mp4"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_layout_probe.png")

app = QApplication(sys.argv)
app.setApplicationName("SubtitleStudioProbe")
cfg = Config()
cfg.ui_scale = 1.5
win = MainWindow(cfg)
win.resize(1707, 1060)          # 逻辑像素 ≈ 2560x1600 物理
win.show()

cues = [Cue(start=1.0 + i * 4.0, end=4.0 + i * 4.0,
            text="这是第 %d 条测试字幕,用来撑起表格行高看看换行效果" % (i + 1),
            original_text="", state="asr") for i in range(40)]
doc = CueDocument(source_video=VIDEO, path="", cues=cues)
win.editor.set_document(doc)

def probe():
    ed = win.editor
    ed_h = ed.height()
    split_h = ed.split.height()
    s = ed.split.sizes()
    tbl = ed.table
    tbl_geo = tbl.geometry()
    bottom = ed.split.widget(1)
    report = []
    report.append("editor height          = %d" % ed_h)
    report.append("split sizes (top,bot)  = %s  (total %d)" % (s, sum(s)))
    report.append("table geometry         = %dx%d at (%d,%d)" % (
        tbl_geo.width(), tbl_geo.height(), tbl_geo.x(), tbl_geo.y()))
    report.append("bottom band width      = %d, table width = %d" % (bottom.width(), tbl_geo.width()))
    report.append("hsplit sizes (ply,edt) = %s" % (ed.hsplit.sizes(),))
    ok_ratio = tbl_geo.height() >= ed_h * 0.5
    ok_width = tbl_geo.width() >= ed.width() * 0.9
    ok_split = s[1] >= s[0]
    report.append("VERDICT: table>=50%% editor height: %s | table>=90%% width: %s | split bot>=top: %s"
                  % (ok_ratio, ok_width, ok_split))
    win.grab().save(OUT)
    report.append("screenshot -> %s" % OUT)
    print("\n".join(report))
    try:
        win.editor.player.shutdown()   # 先拆媒体后端，避免 DirectShow 退出竞态
    except Exception:
        pass
    app.exit(0 if (ok_ratio and ok_width and ok_split) else 1)

QTimer.singleShot(2500, probe)   # 给 QVideoWidget 载入真实视频并触发 sizeHint 膨胀的时间
rc = app.exec_()
sys.exit(rc)
