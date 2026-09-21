# -*- coding: utf-8 -*-
"""暗色模式可读性探针:强制暗色皮肤,渲染截图并给出对比度判定。"""
import os, sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QApplication

from sstudio.core.config import Config
from sstudio.core.model import Cue, CueDocument
from sstudio.ui.main_window import MainWindow

VIDEO = r"D:\Project\Tuse Creation\吐司Tuse\两个平台同步作品\26_14 OSMO360II深度体验\VOICE.mp4"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dark_probe.png")

app = QApplication(sys.argv)
app.setApplicationName("SubtitleStudioProbe")
cfg = Config()
cfg.ui_scale = 1.5
cfg.theme = "dark"          # 本轮要看的皮肤
win = MainWindow(cfg)
win.resize(1707, 1060)
win.show()

cues = []
for i in range(40):
    st = ("asr", "llm", "review", "confirmed", "edited")[i % 5]
    cues.append(Cue(start=1.0 + i * 4.0, end=4.0 + i * 4.0,
                    text="这是第 %d 条%s字幕,深色下要能一眼读清" % (i + 1, st),
                    original_text="", state=st))
doc = CueDocument(source_video=VIDEO, path="", cues=cues)
win.editor.set_document(doc)
win.editor.edit_area.setPlainText("选中一条字幕后,这里的深色文本框也要清晰易读。")

def rel_lum(rgb):
    def f(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (f(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def avg_rgb(img, x0, y0, x1, y1):
    rs = gs = bs = n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = img.pixelColor(x, y)
            rs += c.red(); gs += c.green(); bs += c.blue(); n += 1
    return (rs / n, gs / n, bs / n)

def probe():
    img = win.grab().toImage()
    img.save(OUT)
    w, h = img.width(), img.height()
    ed = win.editor
    # 用控件的真实全局几何映射到截图坐标(截图是整窗)
    def sample(widget, fx0, fy0, fx1, fy1, tag):
        from PyQt5.QtWidgets import QMainWindow
        g = widget.geometry()
        top = widget
        gp = widget.mapTo(win, g.topLeft()) if widget.parentWidget() else widget.mapTo(win, g.topLeft())
        x0, y0 = gp.x() + int(g.width()*fx0), gp.y() + int(g.height()*fy0)
        x1, y1 = gp.x() + int(g.width()*fx1), gp.y() + int(g.height()*fy1)
        rgb = avg_rgb(img, x0, y0, x1, y1)
        lum = rel_lum(rgb)
        report.append("%-10s geom=(%d,%d) avg RGB=%s rel_lum=%.3f" % (
            tag, gp.x(), gp.y(), tuple(int(v) for v in rgb), lum))
        return lum
    report = ["window %dx%d" % (w, h)]
    lum_tbl = sample(ed.table, 0.55, 0.30, 0.80, 0.70, "table-bg")
    lum_edt = sample(ed.edit_area, 0.5, 0.4, 0.9, 0.8, "editbox-bg")
    # 直接单独渲染 edit_area,排除"采错位置"的可能
    pm = ed.edit_area.grab()
    p2 = pm.toImage()
    rgb2 = avg_rgb(p2, 10, 10, p2.width()-10, p2.height()-10)
    report.append("editbox.grab() RGB=%s (%dx%d)" % (tuple(int(v) for v in rgb2), p2.width(), p2.height()))
    qapp = QApplication.instance()
    report.append("app-level stylesheet: %r" % (qapp.styleSheet() or "")[:120])
    report.append("edit geom in win: %s" % ed.edit_area.rect())
    ok_tbl = 0.001 < lum_tbl < 0.09          # 深底,但不全黑(隔行/状态染)
    ok_edt = lum_edt < 0.05                  # 编辑框深底(此前是白底 ≈0.9)
    report.append("VERDICT: table dark: %s | editbox dark: %s" % (ok_tbl, ok_edt))
    print("\n".join(report))
    print("screenshot -> %s" % OUT)
    try:
        win.editor.player.shutdown()
    except Exception:
        pass
    app.exit(0 if (ok_tbl and ok_edt) else 1)

QTimer.singleShot(2500, probe)
rc = app.exec_()
sys.exit(rc)
