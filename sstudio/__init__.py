"""Subtitle Studio — 视频字幕工坊。

导入视频 -> 本地 Whisper 转写 -> 大模型只改错别字 -> 导出 SRT/VTT/TXT 等成品。

版本号真源是仓库根的 ``VERSION`` 文件，详见 ``sstudio/version.py``。
"""

from .version import (  # noqa: F401
    APP_NAME,
    APP_NAME_ZH,
    __version__,
    describe,
    is_release,
    version_info,
    version_tuple,
)

__all__ = ["APP_NAME", "APP_NAME_ZH", "__version__", "describe",
           "is_release", "version_info", "version_tuple"]
