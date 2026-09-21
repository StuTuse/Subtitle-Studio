"""格式读写往返 + 导入自动识别。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, section, sample_doc  # noqa: E402

from sstudio.core import formats  # noqa: E402
from sstudio.core.model import Cue, CueDocument, sec_to_ts, ts_to_sec  # noqa: E402

doc = sample_doc()

section("1. 九种格式导出→导入往返")
for key in formats.EXPORT_ORDER:
    text = formats.export_text(doc, key)
    ext = "srt" if key == "txt_time" else key
    cues, fmt = formats.parse_any(text, "x." + ext)
    if key == "txt":                       # 纯文本本就拼成段落，回读成一条属预期
        check(f"{key:<9} -> {fmt}", len(cues) >= 1, f"{len(cues)} 条")
    else:
        check(f"{key:<9} -> {fmt}", len(cues) == len(doc.cues), f"{len(cues)} 条")

section("2. 时间码")
for t in ("00:00:00,000", "00:01:02,500", "01:23:45.678", "00:00:09,090"):
    back = sec_to_ts(ts_to_sec(t))
    check(f"{t} 往返", ts_to_sec(back) == ts_to_sec(t), back)
check("VTT 用点号", sec_to_ts(1.5, sep=".", millis=True) == "00:00:01.500")
check("无毫秒", sec_to_ts(61.0, millis=False) == "00:01:01")
check("脏时间码返回 None", ts_to_sec("not-a-time") is None)

section("3. 脏文件容错")
dirty = """1
00:00:01,000 --> 00:00:03,000
第一句

2
00:00:03,000 --> 00:00:05,000
第二句
中间还有第二行
第三个换行

3
00:00:05,000 --> 00:00:07,000
<b>富文本</b> 标签要清掉
"""
cues = formats.parse_srt(dirty)
check("多行字幕合成一条", len(cues) == 3, len(cues))
check("多行保留换行（合法的单条多行字幕）",
      cues[1].text == "第二句\n中间还有第二行\n第三个换行", repr(cues[1].text))
check("HTML 标签被清掉", cues[2].text == "富文本 标签要清掉", cues[2].text)

cues = formats.parse_srt("1\r\n00:00:01,000 --> 00:00:02,000\r\nCRLF 文件\r\n\r\n")
check("CRLF 可解析", len(cues) == 1 and cues[0].text == "CRLF 文件")
check("BOM 可解析", len(formats.parse_srt("\ufeff" + formats.to_srt(doc))) == 3)

ass = formats.to_ass(doc)
check("ASS 样式段不被当字幕", all("[V4+ Styles]" not in c.text and "Format" not in c.text
                              for c in formats.parse_ass(ass)))
check("ASS 事件数正确", len(formats.parse_ass(ass)) == 3)

section("4. 导入格式自动识别")
cases = {
    "srt": (formats.to_srt(doc), len(doc.cues)),
    "vtt": (formats.to_vtt(doc), len(doc.cues)),
    "ass": (formats.to_ass(doc), len(doc.cues)),
    "lrc": (formats.to_lrc(doc), len(doc.cues)),
    "json": (formats.to_json(doc), len(doc.cues)),
    "md": (formats.to_md(doc), len(doc.cues)),
    "html": (formats.to_html(doc), len(doc.cues)),
}
for name, (text, n) in cases.items():
    cues, fmt = formats.parse_any(text, "x." + name)
    check(f"识别 {name}", fmt == name and len(cues) == n, f"{fmt} / {len(cues)}")

# 不带扩展名时靠内容判断
cues, fmt = formats.parse_any(formats.to_srt(doc), "")
check("无扩展名靠内容识别 SRT", fmt == "srt", fmt)
cues, fmt = formats.parse_any(formats.to_json(doc), "")
check("无扩展名靠内容识别 JSON", fmt == "json", fmt)
# 带时间戳的 txt 以 '[' 开头，不能被误判成 JSON
cues, fmt = formats.parse_any(formats.to_txt(doc, with_time=True), "a.txt")
check("带时间戳 TXT 不误判为 JSON", fmt in ("timed_text", "lrc") and len(cues) == 3,
      f"{fmt} / {len(cues)}")

section("5. 无时间轴 TXT 的切分")
plain = "我有个问题特别想问大家就是你平常记录生活吗" * 6
cues = formats.parse_txt(plain)
check("无标点长文被切开", len(cues) > 1, f"{len(cues)} 条")
check("每条不超 42 字", max(len(c.text) for c in cues) <= 42,
      max(len(c.text) for c in cues))
check("首条从 0 开始", cues[0].start == 0.0)
check("时间轴单调递增", all(cues[i].end <= cues[i + 1].start + 1e-6
                        for i in range(len(cues) - 1)))
check("短句给合理时长", all(1.0 <= c.end - c.start <= 10.0
                        for c in formats.parse_txt("短句一\n短句二\n短句三")))
check("短句保持原样", [c.text for c in formats.parse_txt("他说：“今天天气不错”。然后走了。")]
      == ["他说：“今天天气不错”。然后走了。"])
check("长句中引号并回上一句",
      formats._split_sentences("他说：“今天天气不错”。" * 10)[0] == "他说：“今天天气不错”。")
check("长句按句号切开", len(formats._split_sentences("句子一。句子二。句子三。" * 8)) >= 3)
check("空文本返回空", formats.parse_txt("") == [] and formats.parse_txt("  \n\n ") == [])
check("分隔线被忽略", formats.parse_txt("---\n正文内容\n===")[0].text == "正文内容")

section("6. 时间戳文本的多种写法")
for line in ("[00:01:02.500] 方括号", "00:01:02,500 --> 00:01:05,000 箭头",
             "(00:01) +30s 括号加时长", "00:61.5 分秒点号"):
    cues = formats.parse_timed_text(line + "\n[00:02:00.000] 第二条")
    check(f"解析「{line[:18]}」", len(cues) == 2, len(cues))

section("7. JSON 结构完整性")
data = json.loads(formats.to_json(doc))
check("含版本与来源", "cues" in data and data.get("source_video", "").endswith("demo.mp4"))
back = CueDocument.from_dict(data)
check("词级时间戳保留", sum(1 for c in back.cues if c.words) == 1)
check("original_text 保留", all(c.original_text for c in back.cues))
check("说话人保留", back.cues[1].speaker == "小明")
check("状态保留", back.cues[2].state == "review")

section("8. 导出内容正确性")
srt = formats.to_srt(doc)
check("SRT 序号从 1 开始", srt.startswith("1\n00:00:00,000"))
check("SRT 逗号分隔毫秒", "--> 00:00:02,500" in srt)
check("VTT 头存在", formats.to_vtt(doc).startswith("WEBVTT"))
check("VTT 点号毫秒", "00:00:02.500" in formats.to_vtt(doc))
check("纯 TXT 无时间码", ":" not in formats.to_txt(doc).split("\n")[0][:6])
check("带时间 TXT 有码", "[00:00:00]" in formats.to_txt(doc, with_time=True))
check("说话人进 SRT", "小明:" in formats.to_srt(doc))
check("LRC 分:秒格式", "[00:00.00]" in formats.to_lrc(doc))

sys.exit(finish())
