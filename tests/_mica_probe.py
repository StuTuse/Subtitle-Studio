# -*- coding: utf-8 -*-
"""独立进程探针（bugfix_sweep 229 节调用）：真实构建 MainWindow，
验证 Mica 关闭 + 不透明底色 + 堆叠页互斥。所有检查点通过 exit 0。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _harness
from _harness import check, finish
from PyQt5.QtWidgets import QApplication
app = QApplication([])
from sstudio.core.config import Config
from sstudio.ui.main_window import MainWindow
w = MainWindow(Config())
w.show()
app.processEvents()
check("Mica 已关", not w.isMicaEffectEnabled())
check("底色不透明", w.backgroundColor.alpha() == 255)
from qfluentwidgets.common.config import isDarkTheme
expect = w._darkBackgroundColor if isDarkTheme() else w._lightBackgroundColor
check("底色回落主题色", w.backgroundColor == expect)
swv = w.stackedWidget.view
vis = [swv.widget(i).isVisible() for i in range(swv.count())]
check("堆叠页互斥可见", vis.count(True) == 1)
check("页面无残留 effect",
      all(p.graphicsEffect() is None
          for p in (w.editor, w.fix, w.export, w.settings)))
rc = finish()
try:
    w.close()
except Exception:
    pass
os._exit(1 if rc else 0)
