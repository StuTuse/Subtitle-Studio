# -*- coding: utf-8 -*-
"""完整复刻 sweep 第 160 节 → 169 节执行顺序（缓存固化为真实 HOME）。"""
import sys, os, json, tempfile, shutil
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ".")

from sstudio.core.config import config_path as _cp, Config as CF, _CACHE_ROOT

# ---- sweep 早期：第一次 config_path() 在 setenv 前（第 13 节等）----
_ = _cp()
print("缓存固化:", _CACHE_ROOT.get("config"))

# ---- 第 160 节（逐句复刻 sweep 3080-3110 行）----
_home179 = tempfile.mkdtemp(prefix="ss160_")
os.environ["SUBTITLE_STUDIO_HOME"] = _home179
try:
    _c179a = CF()
    _c179a.export_encoding = "gbk"
    _c179a.whisper_model = "medium"
    _c179a.profiles[0].api_key = "sk-179"
    _c179a.save()
    print("160 save1 落盘于:", _cp()[:60], "…(真实?" , "AppData" in _cp(), ")")
    _c179b = CF.load()
    _c179b.export_encoding = "utf-8"
    _c179b.save()
    print("160 .bak:", os.path.isfile(_cp() + ".bak"))
    open(_cp(), "w", encoding="utf-8").write("{corrupted")
    _c179c = CF.load()
    print("160 load_failed:", _c179c.load_failed)
    print("160 .bad:", os.path.isfile(_cp() + ".bad"))
    _c179c.save()
    print("160 损坏残留:", open(_cp(), encoding="utf-8").read() == "{corrupted")
    shutil.copyfile(_cp() + ".bak", _cp())
    _c179d = CF.load()
    print("160 恢复 load_failed:", _c179d.load_failed)
    _c179d.save()
finally:
    os.environ.pop("SUBTITLE_STUDIO_HOME", None)
    shutil.rmtree(_home179, ignore_errors=True)

print("== 真实 config 状态（160 节后）==")
_raw = open(_cp(), encoding="utf-8").read()
print("JSON 合法:", _raw.startswith("{") and "corrupted" not in _raw[:30])
try:
    print("真实 recent len:", len(json.loads(_raw).get("recent_files", [])))
except Exception as e:
    print("真实 JSON 损坏:", e)

# ---- 第 169 节（sweep 3155-3180 行）----
_cfg188 = CF()
for _i188 in range(20):
    _p188 = rf"D:\v\视频{_i188}.mp4"
    if _p188 in _cfg188.recent_files:
        _cfg188.recent_files.remove(_p188)
    _cfg188.recent_files.insert(0, _p188)
    del _cfg188.recent_files[_cfg188.max_recent:]
_p188b = r"D:\v\视频5.mp4"
if _p188b in _cfg188.recent_files:
    _cfg188.recent_files.remove(_p188b)
_cfg188.recent_files.insert(0, _p188b)
del _cfg188.recent_files[_cfg188.max_recent:]
_home188 = tempfile.mkdtemp(prefix="ss169_")
os.environ["SUBTITLE_STUDIO_HOME"] = _home188
try:
    _cfg188.save()
    print("169 save 后文件存在:", os.path.isfile(_cp()),
          "落盘于真实?", "AppData" in _cp())
    _cfg188b = CF.load()
    print("169 load recent len:", len(_cfg188b.recent_files),
          "首:", _cfg188b.recent_files[0] if _cfg188b.recent_files else "空")
finally:
    os.environ.pop("SUBTITLE_STUDIO_HOME", None)
    shutil.rmtree(_home188, ignore_errors=True)
