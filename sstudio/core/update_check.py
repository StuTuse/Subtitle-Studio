# -*- coding: utf-8 -*-
"""新版本检查：读 GitHub latest release API，只比功能版本（x.y.0）。

设计取舍：
* 只认 x.y.0 —— 与 release.py 的发布策略一致（细微更新不挂 Release），
  latest release 永远是功能版本，比较语义干净。
* 完全静默失败：网络不通/超时/限流/代理异常一律当作"没有新版本"，
  绝不为一个提示框拖慢启动或打扰用户（后台线程 + 短超时）。
* 结果缓存一份到内存：一进程只查一次。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

_LATEST_URL = ("https://api.github.com/repos/StuTuse/Subtitle-Studio/"
               "releases/latest")
_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

_cached: dict | None = None


def _parse_ver(v: str) -> tuple:
    m = _TAG_RE.match(v.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else ()


def fetch_latest(timeout: float = 4.0) -> dict:
    """查询 GitHub latest release。返回 {'version': '1.18.0', 'url': …}；
    任何失败返回 {'version': '', 'url': ''}。带进程级缓存。"""
    global _cached
    if _cached is not None:
        return _cached
    out = {"version": "", "url": ""}
    try:
        req = urllib.request.Request(_LATEST_URL, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Subtitle-Studio",       # API 要求 UA，缺了给 403
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        tag = str(data.get("tag_name", ""))
        parsed = _parse_ver(tag)
        if parsed:
            out["version"] = ".".join(map(str, parsed))
            out["url"] = str(data.get("html_url", "")) or (
                "https://github.com/StuTuse/Subtitle-Studio/releases/latest")
    except (OSError, ValueError, KeyError):
        pass                                        # 静默：无网/限流/坏响应
    _cached = out
    return out


def compare(latest: str, current: str) -> int:
    """语义化比较：>0 latest 更新，0 相同，<0 本地更新。"""
    a, b = _parse_ver(latest), _parse_ver(current)
    if not a or not b:
        return 0
    return (a > b) - (a < b)


def has_newer(current: str) -> bool:
    """有可升级的功能版本吗？（第三方打包改号等脏数据一律 False）"""
    info = fetch_latest()
    return compare(info["version"], current) > 0
