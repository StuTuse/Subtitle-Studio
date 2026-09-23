"""字幕格式的读写。

内置：SRT / VTT / ASS / LRC / TXT(纯文本) / JSON / TS(时间轴文本) / Markdown / HTML
设计：所有格式只做「CueDocument <-> 文本」的转换，不含任何 UI 逻辑。
"""

from __future__ import annotations

import html
import json
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

from .model import Cue, CueDocument, sec_to_ts, ts_to_sec

# --------------------------------------------------------------------- 解析
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"</?(?:i|b|u|font|span|c)[^>]*>", re.I)
_WEBVTT_HEAD_RE = re.compile(r"^(\uFEFF)?WEBVTT.*$", re.I)
_SENT_END_RE = re.compile(r"[。！？!?.\"'”’」』]\s*$")
_CLAUSE_START_RE = re.compile(r"^[，。！？、；：,.!?;:]")
_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


def _clean_inline(text: str) -> str:
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    return text.replace("&nbsp;", " ").strip()


_SPEAKER_LINE_RE = re.compile(r"^\s*([^:\n]{1,24}?):$")


def parse_srt(text: str) -> List[Cue]:
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: List[Cue] = []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        idx = 0
        if "-->" not in lines[0]:
            if len(lines) < 2 or "-->" not in lines[1]:
                continue
            idx = 1
        times = lines[idx].split("-->")
        if len(times) < 2:
            continue
        start = ts_to_sec(times[0].strip().split(" ")
                          [0])
        pos = times[1].strip().split(" ")
        end = ts_to_sec(pos[0])
        if start is None or end is None:
            continue
        body_lines = lines[idx + 1:]
        speaker = ""
        # to_srt 把说话人写成独占一行的「名字:」；导入时剥回去，否则往返一次
        # 每条字幕正文都无缘无故多了个前缀。只认整行就是「短词+冒号」的情况。
        if body_lines:
            m = _SPEAKER_LINE_RE.match(body_lines[0])
            if m:
                speaker = m.group(1).strip()
                body_lines = body_lines[1:]
        body = "\n".join(body_lines).strip()
        cues.append(Cue(start=start, end=end, text=_clean_inline(body),
                        original_text=_clean_inline(body), speaker=speaker))
    return cues


def parse_vtt(text: str) -> List[Cue]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WEBVTT_HEAD_RE.sub("", text, count=1)
    text = re.sub(r"^NOTE\b.*?(?=\n\s*\n|$)", "", text, flags=re.S | re.M)
    text = re.sub(r"^STYLE\b.*?(?=\n\s*\n|$)", "", text, flags=re.S | re.M)
    text = re.sub(r"^[{}]$", "", text, flags=re.M)
    # <v 说话人> 是 VTT 的标准语音标签，整行替换成 to_srt 同款、parse_srt
    # 能剥回来的「名字:」行；直接混进正文的话会被 _strip_vtt_tags 删掉。
    text = re.sub(r"^<v(?:\.[^>\s]*)?\s+([^>]+)>\s*$", r"\1:", text, flags=re.M)
    return parse_srt(text)


_LRC_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# [hh:mm:ss] 三段式是时间轴文本而非 LRC 歌词（LRC 只有 分:秒）
_HMS_RE = re.compile(r"\[\d{1,2}:\d{2}:\d{2}")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                    ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(ent, ch)
    return text


def _strip_markdown(text: str) -> str:
    out = []
    for ln in text.splitlines():
        ln = re.sub(r"^\s{0,3}#{1,6}\s*", "", ln)          # 标题
        ln = re.sub(r"^\s*[-*+]\s+", "", ln)                # 列表符号
        ln = re.sub(r"`([^`]*)`", r"\1", ln)                # 行内代码
        ln = re.sub(r"\*\*([^*]*)\*\*", r"\1", ln)
        ln = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"\1", ln)
        ln = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", ln)        # 图片
        ln = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", ln)    # 链接
        if re.fullmatch(r"\s*[-=*_]{3,}\s*", ln or ""):
            continue
        out.append(ln)
    return "\n".join(out)


def parse_lrc(text: str) -> List[Cue]:
    lines = text.replace("\r\n", "\n").split("\n")
    marks: List[Tuple[float, str]] = []
    for ln in lines:
        stamps = _LRC_RE.findall(ln)
        body = _LRC_RE.sub("", ln).strip()
        for h_or_m, s, ms in stamps:
            t = int(h_or_m) * 60 + int(s) + (int((ms or "0").ljust(3, "0")[:3]) / 1000.0)
            marks.append((t, body))
    # 先按时间排序再配对 end：LRC 允许多时间标签/乱序行，乱序配对会产出
    # end < start 的倒挂 cue，导入后整条时间轴错乱
    marks.sort(key=lambda x: x[0])
    cues: List[Cue] = []
    for i, (t, body) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else t + 4.0
        if end <= t:
            end = t + 4.0
        cues.append(Cue(start=t, end=min(end, t + 12.0), text=body, original_text=body))
    return cues


def parse_json(text: str) -> List[Cue]:
    return parse_json_obj(json.loads(text))


def parse_json_obj(data) -> List[Cue]:
    if isinstance(data, dict):
        data = data.get("cues") or data.get("transcripts") or data.get("segments") or []
    cues: List[Cue] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        st = item.get("start", item.get("start_time", item.get("from")))
        en = item.get("end", item.get("end_time", item.get("to")))
        if isinstance(st, str):
            st = ts_to_sec(st)
        if isinstance(en, str):
            en = ts_to_sec(en)
        if isinstance(st, bool) or isinstance(en, bool):
            continue                    # bool 是 int 子类，别让 true/false 当成时间
        if st is not None and not isinstance(st, (int, float)):
            continue
        if en is not None and not isinstance(en, (int, float)):
            continue
        txt = item.get("text", item.get("content", "")) or ""
        if st is None or en is None or not str(txt).strip():
            continue
        cues.append(Cue(start=float(st), end=float(en), text=str(txt).strip(),
                        original_text=str(txt).strip(),
                        confidence=item.get("confidence", item.get("avg_logprob")),
                        speaker=str(item.get("speaker", "") or "")))
    return cues


def parse_ass(text: str) -> List[Cue]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: List[Cue] = []
    fmt: Optional[List[str]] = None
    in_events = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            in_events = s.lower() == "[events]"
            if in_events:
                fmt = None      # 只认 [Events] 段里的 Format 定义
            continue
        if in_events and s.lower().startswith("format:") and fmt is None:
            fmt = [x.strip().lower() for x in s[7:].split(",")]
            continue
        if not s.lower().startswith("dialogue:"):
            continue
        raw = s[9:]
        if fmt:
            vals = raw.split(",", len(fmt) - 1)
            d = dict(zip(fmt, vals))
            st, en = ts_to_sec(d.get("start", "")), ts_to_sec(d.get("end", ""))
            body = d.get("text", "")
        else:
            cols = raw.split(",", 9)
            if len(cols) < 10:
                continue
            st, en = ts_to_sec(cols[1]), ts_to_sec(cols[2])
            body = cols[9]
        if st is None or en is None:
            continue
        body = re.sub(r"\{[^}]*\}", "", body).replace("\\N", "\n").replace("\\n", "\n")
        body = body.replace("\\h", " ").strip()
        cues.append(Cue(start=st, end=en, text=body, original_text=body))
    return cues


_SENT_END_CH = "。！？!?…；;"
_QUOTE_CH = "”’」』\"'"


def _split_sentences(line: str, max_chars: int = 42) -> List[str]:
    """把长句按中文句末标点切成适合做字幕的短句（标点后的引号跟着走）。"""
    parts: List[str] = []
    buf = ""
    for ch in line:
        buf += ch
        if ch in _SENT_END_CH:
            parts.append(buf)
            buf = ""
    if buf.strip():
        parts.append(buf)
    # 把悬挂在下一段开头的闭引号并回上一段
    merged: List[str] = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        if merged and len(s) <= 2 and s[0] in _QUOTE_CH:
            merged[-1] += s
        else:
            merged.append(s)
    parts = merged or [line.strip()]

    out: List[str] = []
    for p in parts:
        if len(p) <= max_chars:
            out.append(p)
        else:                       # 超长再按逗号兜底切一刀
            cur = ""
            for seg in re.split(r"([，,])", p):
                seg = seg.strip()
                if not seg:
                    continue
                add = seg if all(c in "，, " for c in seg) else seg + "，"
                if cur and len(cur) + len(add) > max_chars:
                    out.append(cur.rstrip("，,"))
                    cur = add
                else:
                    cur += add
            if cur.strip("，,"):
                out.append(cur.rstrip("，,"))
    # Whisper 等 ASR 的输出常常完全没有标点，上面两刀都切不开；
    # 最后按固定长度硬切，保证导入的长文本不会变成一条巨大的字幕。
    final: List[str] = []
    for p in out or [line.strip()]:
        while len(p) > max_chars:
            cut = max(p.rfind(" ", 0, max_chars), max_chars)
            final.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            final.append(p)
    return [o for o in final if o]


def parse_txt(text: str) -> List[Cue]:
    """无时间轴的纯文本 -> 按句切分并均分时间轴（后续可用「智能断句」改善）。

    导入的往往是稿件：一个自然段可能几百字，直接当一条字幕没法用，
    所以先按句末标点切短句，再按字数加权铺时间轴。
    """
    raw = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    raw = [ln for ln in raw if ln and not re.fullmatch(r"[\s\-—=]*", ln)]
    # 文件头残留行不是内容：只有 WEBVTT 头 / 纯提示词的空 VTT 回落到
    # parse_txt 时，用户不该得到一条文本叫 "WEBVTT" 的字幕
    raw = [ln for ln in raw if not _WEBVTT_HEAD_RE.match(ln)]
    lines: List[str] = []
    for ln in raw:
        if len(ln) <= 42:
            lines.append(ln)
        else:
            lines.extend(_split_sentences(ln))
    if not lines:
        return []
    # 没有时间信息，只能按正常语速估：约 4 字/秒（英文约 2.5 词/秒），单条 1~10s。
    # 这只是占位时间轴，导入后仍可「智能断句」或对照视频逐条微调。
    cues, cursor = [], 0.0
    for ln in lines:
        dur = min(10.0, max(1.0, len(ln) / 4.0))
        end = cursor + dur
        cues.append(Cue(start=round(cursor, 3), end=round(end, 3), text=ln, original_text=ln))
        cursor = end
    return cues


_TIME_LINE_RE = re.compile(
    r"^\s*(?:\[|\()?"
    r"(?P<a>\d{1,2}:\d{2}(?::\d{2})?[.,;]?\d{0,3})"
    r"(?:\s*(?:-->|→|->|~|–|-)\s*(?P<b>\d{1,2}:\d{2}(?::\d{2})?[.,;]?\d{0,3})"
    r"|\s*\+?\s*(?P<d>\d+(?:\.\d+)?)s?)?"
    r"(?:\]|\))?\s*(?P<t>.*)$"
)


def parse_timed_text(text: str) -> List[Cue]:
    """形如 ``[00:01:23.450] 文本`` / ``00:01:23,450 --> 00:01:25,000`` 的时间轴文本。"""
    lines = text.replace("\r\n", "\n").split("\n")
    cues: List[Cue] = []
    for ln in lines:
        if not ln.strip():
            continue
        m = _TIME_LINE_RE.match(ln)
        if not m or not m.group("a"):
            continue
        st = ts_to_sec(m.group("a"))
        en = ts_to_sec(m.group("b")) if m.group("b") else None
        if en is None and m.group("d"):
            try:
                en = st + float(m.group("d"))
            except ValueError:
                en = None
        if st is None:
            continue
        if en is None or en <= st:
            en = st + 3.0
        body = _clean_inline(m.group("t") or "").strip()
        cues.append(Cue(start=st, end=en, text=body, original_text=body))
    return cues


def parse_any(text: str, filename: str = "") -> Tuple[List[Cue], str]:
    """尽力识别导入文件类型，返回 (cues, 识别到的格式名)。"""
    ext = os.path.splitext(filename or "")[1].lower()
    head = text[:4000]
    stripped = head.lstrip()

    # JSON 只在「确实能 loads 成功」时才认领：带时间戳的 txt 同样是 '[' 开头，
    # 光看首字符会把 [00:00:00] 误判成 JSON 数组。
    if stripped[:1] in "[{":
        try:
            return parse_json_obj(json.loads(text)), "json"
        except Exception as e:
            if ext == ".json":
                raise ValueError(f"JSON 字幕解析失败：{e}") from e

    try:
        if ext == ".vtt" or stripped.startswith("WEBVTT"):
            cues = parse_vtt(text)
            if cues:
                return cues, "vtt"
            # 认领了格式但 0 条：别把"空结果"当成功，回落兜底再试一轮
        if ext == ".ass" or "[Events]" in head:
            cues = parse_ass(text)
            if cues:
                return cues, "ass"
        # 扩展名是 .lrc 就直接按 LRC 解析；混了 [hh:mm:ss] 加强时间戳的
        # LRC 文件很常见，不能因此放弃认领
        if ext == ".lrc" or (_LRC_RE.search(head) and not _HMS_RE.search(head)):
            cues = parse_lrc(text)
            if cues:
                return cues, "lrc"
        if "-->" in head:
            cues = parse_srt(text)
            if cues:
                return cues, "srt"
        timed = parse_timed_text(text)
        if len(timed) >= 2:
            return timed, "timed_text"
        if ext in (".html", ".htm") or re.search(
                r"<(html|body|p|div|br|table|!DOCTYPE)\b", head, re.I):
            cues = parse_html(text)
            return (cues, "html") if cues else (parse_txt(_strip_html(text)), "html")
        if ext == ".md":
            cues = parse_md(text)
            return (cues, "md") if cues else (parse_txt(_strip_markdown(text)), "md")
        return parse_txt(text), "txt"
    except Exception:
        return parse_txt(text), "txt"


# --------------------------------------------------------------------- 导出
def to_srt(doc: CueDocument) -> str:
    out = []
    for i, c in enumerate(doc.cues, 1):
        head = f"{i}\n{sec_to_ts(c.start)} --> {sec_to_ts(c.end)}"
        if c.speaker:
            head += f"\n{c.speaker}:"
        out.append(f"{head}\n{c.display_text.strip()}\n")
    return "\n".join(out)


def to_vtt(doc: CueDocument) -> str:
    out = ["WEBVTT\n"]
    for i, c in enumerate(doc.cues, 1):
        lines = [str(i), f"{sec_to_ts(c.start, sep='.')} --> {sec_to_ts(c.end, sep='.')}"]
        if c.speaker:
            lines.append(f"<v {c.speaker}>")
        lines.append(c.display_text.strip())
        out.append("\n".join(lines) + "\n")
    return "\n".join(out)


def _ass_time(sec: float) -> str:
    total_cs = int(round(sec * 100))
    cs = total_cs % 100
    s = total_cs // 100
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


ASS_HEADER = """[Script Info]
; Generated by Subtitle Studio
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def to_ass(doc: CueDocument, font: str = "Microsoft YaHei", fontsize: int = 54) -> str:
    out = [ASS_HEADER.format(font=font, fontsize=fontsize)]
    for c in doc.cues:
        txt = c.display_text.strip().replace("\n", "\\N")
        name = c.speaker.replace(",", "")
        out.append(
            f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,{name},0,0,0,,{txt}"
        )
    return "\n".join(out) + "\n"


def to_txt(doc: CueDocument, with_time: bool = False, plain: bool = True) -> str:
    """纯文本导出。plain=True 时把断行合并成自然段落，方便当作稿件阅读/喂给 LLM。"""
    if with_time:
        return "\n".join(
            f"[{sec_to_ts(c.start, sep='.', millis=False)}] {c.display_text.strip()}"
            for c in doc.cues
        )
    if not plain:
        return "\n".join(c.display_text.strip() for c in doc.cues)

    # 把逐条字幕拼成自然段落：句末标点 + 停顿较大 + 已有足够长度 => 分段
    paras: List[str] = []
    buf = ""
    prev_end: Optional[float] = None
    for c in doc.cues:
        t = re.sub(r"\s+", " ", c.display_text).strip()
        if not t:
            continue
        if not buf:
            buf, prev_end = t, c.end
            continue
        gap = max(0.0, c.start - (prev_end if prev_end is not None else c.start))
        ends_sentence = bool(_SENT_END_RE.search(buf))
        if ends_sentence and gap > 0.9 and len(buf) > 24:
            paras.append(buf)
            buf, prev_end = t, c.end
            continue
        # 中文之间直接相接，英文之间补空格
        joiner = "" if (_CJK_RE.search(buf[-1:]) and _CJK_RE.match(t[:1])) else " "
        if _CLAUSE_START_RE.match(t[:1]):
            joiner = ""
        buf += joiner + t
        prev_end = c.end
    if buf:
        paras.append(buf)
    return "\n\n".join(paras)


def to_json(doc: CueDocument, indent: int = 2) -> str:
    payload = {
        "source_video": doc.source_video,
        "duration": doc.duration,
        "language": doc.language,
        "meta": doc.meta,
        "cues": [
            {
                "index": i,
                "start": round(c.start, 3),
                "end": round(c.end, 3),
                "start_ts": sec_to_ts(c.start),
                "end_ts": sec_to_ts(c.end),
                "text": c.display_text,
                "original_text": c.original_text,
                "speaker": c.speaker,
                "state": c.state,
                "words": c.words,
            }
            for i, c in enumerate(doc.cues, 1)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def to_md(doc: CueDocument) -> str:
    out = [f"# {os.path.basename(doc.source_video) or 'Subtitle'}", ""]
    for c in doc.cues:
        sp = f"**{c.speaker}** " if c.speaker else ""
        out.append(f"- `{sec_to_ts(c.start, sep='.', millis=False)} → "
                   f"{sec_to_ts(c.end, sep='.', millis=False)}` {sp}{c.display_text.strip()}")
    return "\n".join(out) + "\n"


def to_html(doc: CueDocument) -> str:
    rows = []
    for i, c in enumerate(doc.cues, 1):
        rows.append(
            "<tr><td>{i}</td><td>{s}</td><td>{e}</td><td>{t}</td></tr>".format(
                i=i, s=sec_to_ts(c.start), e=sec_to_ts(c.end),
                t=html.escape(c.display_text).replace("\n", "<br>")))
    return ("<!doctype html><meta charset='utf-8'><title>Subtitles</title>"
            "<style>body{font-family:system-ui;margin:24px}table{border-collapse:collapse;width:100%}"
            "td,th{border-bottom:1px solid #ddd;padding:6px 8px;vertical-align:top;text-align:left}"
            "code{color:#666}</style><table><tr><th>#</th><th>Start</th><th>End</th><th>Text</th></tr>"
            + "".join(rows) + "</table>")


def to_lrc(doc: CueDocument) -> str:
    out = []
    for c in doc.cues:
        m = int(c.start // 60)
        s = c.start - m * 60
        out.append(f"[{m:02d}:{s:05.2f}]{c.display_text.strip()}")
    return "\n".join(out)


def parse_md(text: str) -> List[Cue]:
    """读回 to_md 的清单格式：``- `00:00:01 → 00:00:03` 文本``。"""
    cues: List[Cue] = []
    pat = re.compile(
        r"^\s*[-*]\s*`?\s*([\d:.]+)\s*(?:→|-->|->)\s*([\d:.]+)\s*`?\s*(.*)$")
    for ln in text.splitlines():
        m = pat.match(ln)
        if not m:
            continue
        st, en = ts_to_sec(m.group(1)), ts_to_sec(m.group(2))
        if st is None or en is None:
            continue
        body = _clean_inline(m.group(3)).strip()
        if body:
            cues.append(Cue(start=st, end=max(en, st + 0.2), text=body, original_text=body))
    return cues


_TD_RE = re.compile(r"(?is)<td[^>]*>(.*?)</td>")
_TR_RE = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")


def parse_html(text: str) -> List[Cue]:
    """读回 to_html 的对照表：<tr><td>#</td><td>start</td><td>end</td><td>text</td></tr>。"""
    cues: List[Cue] = []
    for tr in _TR_RE.findall(text):
        tds = _TD_RE.findall(tr)
        if len(tds) != 4:
            continue
        st = ts_to_sec(_unescape_entities(re.sub(r"<[^>]+>", "", tds[1])).strip())
        en = ts_to_sec(_unescape_entities(re.sub(r"<[^>]+>", "", tds[2])).strip())
        body = _unescape_entities(re.sub(r"(?i)<br\s*/?>", "\n", tds[3]))
        body = re.sub(r"<[^>]+>", "", body).strip()
        if st is None or not body:
            continue                       # 表头行等
        cues.append(Cue(start=st, end=en if en and en > st else st + 3.0,
                        text=body, original_text=body))
    return cues


def _unescape_entities(s: str) -> str:
    for ent, ch in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
                    ("&quot;", '"'), ("&#x27;", "'"), ("&apos;", "'"),
                    ("&amp;", "&")):
        s = s.replace(ent, ch)
    return s


# ------------------------------------------------------------------- 注册表
class FormatSpec:
    def __init__(self, key: str, label: str, ext: str, writer: Callable[[CueDocument], str],
                 importer: Optional[Callable[[str, str], List[Cue]]] = None,
                 desc: str = ""):
        self.key, self.label, self.ext = key, label, ext
        self.writer, self.importer, self.desc = writer, importer, desc


FORMATS: Dict[str, FormatSpec] = {
    "srt": FormatSpec("srt", "SubRip 字幕 (.srt)", ".srt", to_srt, parse_srt, "最通用的字幕格式"),
    "vtt": FormatSpec("vtt", "WebVTT (.vtt)", ".vtt", to_vtt, parse_vtt, "网页/B站/YouTube 友好"),
    "ass": FormatSpec("ass", "Advanced ASS (.ass)", ".ass", to_ass, parse_ass, "带样式，适合压制/特效"),
    "txt": FormatSpec("txt", "纯文本 (.txt)", ".txt", lambda d: to_txt(d), parse_txt, "干净稿件，无时间轴"),
    "txt_time": FormatSpec("txt_time", "带时间戳文本 (.txt)", ".txt",
                           lambda d: to_txt(d, with_time=True), parse_timed_text, "逐行带时间码"),
    "json": FormatSpec("json", "结构化 JSON (.json)", ".json", to_json, parse_json, "词级时间戳/二次开发"),
    "md": FormatSpec("md", "Markdown 清单 (.md)", ".md", to_md, parse_md, "可读的逐条清单"),
    "html": FormatSpec("html", "HTML 对照表 (.html)", ".html", to_html, parse_html, "可直接浏览器打开校对"),
    "lrc": FormatSpec("lrc", "歌词 LRC (.lrc)", ".lrc", to_lrc, parse_lrc, "音乐/播客"),
}

EXPORT_ORDER = ["srt", "vtt", "ass", "txt", "txt_time", "json", "md", "html", "lrc"]


def export_text(doc: CueDocument, key: str) -> str:
    spec = FORMATS.get(key)
    if not spec:
        raise ValueError(f"未知导出格式: {key}")
    return spec.writer(doc)


def import_text(text: str, filename: str = "") -> Tuple[List[Cue], str]:
    return parse_any(text, filename)
