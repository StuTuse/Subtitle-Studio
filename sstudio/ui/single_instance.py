"""单实例守护。

图标多点两下就会起来好几个进程：同一个 config.json 被两个进程轮流覆盖，
首启向导弹两份，改完设置一边保存一边被另一边的旧配置写回去。Qt 的
QLocalServer 是标准做法：先起来的进程占住名字，后到的连上一喊"醒来"
就退出。注意：进程被杀/崩溃后命名管道句柄随进程消失，listen 不会失败，
所以这里几乎不需要清理残桩；真正会挡住启动的是"僵而不死的活实例"
（卡在退出等待等）——那可以用 --new-instance 或环境变量绕过，见 try_start。
"""
from __future__ import annotations

import hashlib
import os

from PyQt5.QtCore import QObject
from PyQt5.QtNetwork import QLocalServer, QLocalSocket


def _server_name() -> str:
    # 名字按数据目录派生：换 %APPDATA% 位置（便携/多用户）各自独立，
    # 不会出现"另一份安装的实例把这份挡住"的误伤。
    from sstudio.core.config import data_dir
    import os
    key = f"SubtitleStudio|{os.path.normcase(os.path.abspath(data_dir()))}"
    return "SubtitleStudio-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


class SingleInstance(QObject):
    """try_start() 返回 False 表示已有实例在跑（并请求它把自己唤到前台）。

    实例必须一直存活到进程退出（server 挂在其上），调用方持有引用即可。
    """

    def __init__(self, app: QObject):
        super().__init__(app)
        self._app = app
        self._server = None
        self.on_activate = None          # 主窗口建好后赋唤醒回调

    def try_start(self) -> bool:
        # 逃生门：已有实例僵而不死（卡在退出等待等）时，用户双击图标永远
        # 没反应且无路可走。--new-instance 参数或环境变量直接旁路守护。
        if os.environ.get("SS_NEW_INSTANCE") == "1":
            return True
        import sys
        if any(a == "--new-instance" for a in sys.argv[1:]):
            return True
        name = _server_name()
        probe = QLocalSocket(self)
        probe.connectToServer(name)
        if probe.waitForConnected(300):
            probe.write(b"activate")
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            return False
        probe.abort()

        srv = QLocalServer(self)
        if not srv.listen(name):
            QLocalServer.removeServer(name)      # 平台层万一有残桩（无害）
            if not srv.listen(name):
                # 守护本身起不来就放行：宁可可能双开，也不能拒绝启动
                return True
        srv.newConnection.connect(self._on_connection)
        self._server = srv
        return True

    def _on_connection(self):
        srv = self._server
        while srv is not None and srv.hasPendingConnections():
            sock = srv.nextPendingConnection()
            if sock is not None:
                # 读一下唤醒请求（顺手把 socket 收干净），随后再回收
                try:
                    sock.readyRead.connect(sock.deleteLater)
                except Exception:
                    pass
                sock.deleteLater()
        cb = self.on_activate
        if cb is not None:
            try:
                cb()
            except Exception:
                pass
