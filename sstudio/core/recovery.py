# -*- coding: utf-8 -*-
"""崩溃恢复：编辑期间把未保存工程快照写进 data/recovery/，
启动时发现残留快照就提示恢复。

写盘策略（与自动保存互补）：
- 自动保存（auto_save）只对「已有 .ssp 路径」的工程生效，且限速 45s；
- 会话快照对「从未保存过」的工程也生效——恰恰是这类工程崩溃后
  无处可寻，损失最大。快照节流 8s + 内容 token 去重，正常退出/
  手动保存后立即清掉对应快照。
"""

from __future__ import annotations

import glob
import json
import os
import time
from typing import List, Optional

SNAPSHOT_INTERVAL = 8.0     # 两次快照最小间隔（秒）
KEEP_DAYS = 7               # 恢复目录最长保留期：没人认领的快照到期清掉


def recovery_dir() -> str:
    from .config import data_dir
    d = os.path.join(data_dir(), "recovery")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        import tempfile
        d = os.path.join(tempfile.gettempdir(), "SubtitleStudio", "recovery")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    return d


def _prune_old() -> None:
    """删除超过保留期的孤儿快照（进程崩溃后用户再没打开过软件）。"""
    try:
        now = time.time()
        for f in glob.glob(os.path.join(recovery_dir(), "*.ssprev")):
            if now - os.path.getmtime(f) > KEEP_DAYS * 86400:
                os.remove(f)
    except OSError:
        pass


def write_snapshot(doc, token: int) -> Optional[str]:
    """把工程快照写进恢复目录。token 是内容变更计数：没变就不重写。

    doc 需有 to_json()/cues；异常一律吞掉——恢复机制绝不能干扰主流程。
    返回写盘路径（未写返回 None）。
    """
    try:
        if doc is None or not getattr(doc, "cues", None):
            return None
        d = recovery_dir()
        _prune_old()
        stem = _stem_for(doc)
        path = os.path.join(d, stem + ".ssprev")
        # 已有同名快照且 token 相同：内容没变，跳过写盘
        meta_path = path + ".json"
        try:
            if os.path.isfile(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    if int(json.load(f).get("token", -1)) == token:
                        return None
        except (OSError, ValueError):
            pass
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(doc.to_json())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"token": token, "ts": time.time(),
                       "source": getattr(doc, "source_video", "") or "",
                       "path": getattr(doc, "path", "") or ""}, f)
        return path
    except Exception:
        return None


def _stem_for(doc) -> str:
    """稳定文件名：优先工程路径，其次视频路径指纹——同一工程重启后
    仍能对上号，不会越攒越多。"""
    key = getattr(doc, "path", "") or getattr(doc, "source_video", "") or "unsaved"
    import hashlib
    tag = hashlib.md5(os.path.abspath(key).encode("utf-8", "replace")).hexdigest()[:10]
    base = os.path.splitext(os.path.basename(key))[0][:40] or "unsaved"
    safe = "".join(ch for ch in base if ch not in '<>:"/\\|?*') or "unsaved"
    return f"{safe}_{tag}"


def discard_snapshot(doc) -> None:
    """正常保存/退出时清掉对应快照。"""
    try:
        stem = _stem_for(doc)
        d = recovery_dir()
        for suffix in (".ssprev", ".ssprev.json"):
            p = os.path.join(d, stem + suffix)
            if os.path.isfile(p):
                os.remove(p)
    except Exception:
        pass


def list_snapshots() -> List[dict]:
    """启动时扫描残留快照，按修改时间新→旧返回。"""
    out: List[dict] = []
    try:
        for f in glob.glob(os.path.join(recovery_dir(), "*.ssprev")):
            try:
                meta = {}
                meta_path = f + ".json"
                if os.path.isfile(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                out.append({
                    "file": f,
                    "mtime": os.path.getmtime(f),
                    "title": os.path.basename(f)[:-len(".ssprev")],
                    "source": str(meta.get("source", "") or ""),
                    "path": str(meta.get("path", "") or ""),
                })
            except OSError:
                continue
    except OSError:
        pass
    out.sort(key=lambda x: -x["mtime"])
    return out


def load_snapshot(path: str):
    """读回快照工程。失败返回 None。"""
    try:
        from .model import CueDocument
        with open(path, "r", encoding="utf-8") as f:
            return CueDocument.from_dict(json.load(f))
    except Exception:
        return None
