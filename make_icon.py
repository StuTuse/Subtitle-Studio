"""生成 assets/app.ico：用 splash 里同一套 paint_app_icon 矢量绘制各尺寸。

用法：python make_icon.py
改图标造型只需改 sstudio/ui/splash.py::paint_app_icon，然后重跑本脚本。
"""

import io
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtWidgets import QApplication

from sstudio.ui.splash import paint_app_icon

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render_png(px: int) -> bytes:
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    paint_app_icon(p, QRectF(0, 0, px, px))
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build_ico(out_path: str) -> None:
    images = [(s, render_png(s)) for s in SIZES]
    n = len(images)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    dirpart, blobs = b"", b""
    for size, data in images:
        wh = 0 if size >= 256 else size          # 256 在目录里记 0
        dirpart += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(out_path, "wb") as f:
        f.write(header + dirpart + blobs)
    print(f"wrote {out_path} ({os.path.getsize(out_path)} bytes, sizes={SIZES})")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    here = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(here, "assets")
    os.makedirs(assets, exist_ok=True)
    build_ico(os.path.join(assets, "app.ico"))
