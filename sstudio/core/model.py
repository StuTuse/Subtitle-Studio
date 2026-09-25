"""字幕数据模型：全部内部逻辑都围绕 Cue / CueDocument 运转。

设计要点
--------
* 时间一律以「秒(float)」存储，读写各种字幕格式时再转换，避免精度/格式来回折腾。
* 每条 cue 都保留 ``original_text``，这样 LLM 修正后仍可做 diff / 回滚。
* ``state`` 标记这条 cue 的来历（ASR 原文 / LLM 已修正 / 人工编辑 / 待复查），
  UI 据此上色，用户一眼就能看出哪些地方需要人眼确认。
"""

from __future__ import annotations

import copy
import json
import re
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional

TIME_STATES = ("asr", "llm", "edited", "review", "confirmed")


def sec_to_ts(seconds: float, sep: str = ",", millis: bool = True) -> str:
    """秒 -> ``HH:MM:SS,mmm``（sep="," 用于 SRT，"." 用于 VTT/ASS）。

    写出侧固定位宽（SRT/VTT 社区惯例，播放器兼容性最好）；解析侧 _TS_RE
    小时段放开到 3 位，能读回别家工具产出的 100h+ 时间码。
    """
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000.0))
    ms = total_ms % 1000
    s = total_ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if millis:
        return f"{h:02d}:{m:02d}:{sec:02d}{sep}{ms:03d}"
    return f"{h:02d}:{m:02d}:{sec:02d}"


_TS_RE = re.compile(
    r"^\s*(?:(?P<h>\d{1,3}):)?(?P<m>\d{1,2}):(?P<s>\d{1,2})(?:[.,;](?P<ms>\d{1,3}))?\s*$"
)


def ts_to_sec(text: str) -> Optional[float]:
    """解析 ``00:00:12,340`` / ``0:12.34`` / ``12.34`` / ``1:02:03.5`` -> 秒。"""
    if text is None:
        return None
    t = str(text).strip().replace("\u202f", " ")
    if not t:
        return None
    if re.fullmatch(r"\d+(?:[.,]\d+)?", t):  # 纯数字 = 秒
        return float(t.replace(",", "."))
    m = _TS_RE.match(t)
    if not m:
        return None
    h = int(m.group("h") or 0)
    mi = int(m.group("m"))
    s = int(m.group("s"))
    ms_raw = m.group("ms") or "0"
    ms = int(ms_raw.ljust(3, "0")[:3])
    return h * 3600 + mi * 60 + s + ms / 1000.0


@dataclass
class Cue:
    """一条字幕。"""

    start: float
    end: float
    text: str = ""
    original_text: str = ""
    state: str = "asr"  # asr | llm | edited | review | confirmed
    speaker: str = ""
    confidence: Optional[float] = None
    words: List[Dict[str, Any]] = field(default_factory=list)  # [{start,end,word,prob}]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    # ------------------------------------------------------------------ utils
    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def display_text(self) -> str:
        return self.text or ""

    def is_changed(self) -> bool:
        return (self.text or "").strip() != (self.original_text or "").strip()

    def contains(self, seconds: float) -> bool:
        return self.start <= seconds < max(self.end, self.start + 0.001)

    def shift(self, delta: float) -> None:
        self.start = max(0.0, self.start + delta)
        self.end = max(self.start + 0.01, self.end + delta)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Cue":
        # confidence 必须是数字：手改/外部工具生成的工程可能是任意类型，
        # 字符串会让 _tip 的 f"{confidence:.2f}" 直接 ValueError
        conf = d.get("confidence")
        if isinstance(conf, str):
            try:
                conf = float(conf)
            except ValueError:
                conf = None
        elif isinstance(conf, (int, float)):
            conf = float(conf)
        else:
            conf = None
        return Cue(
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 0.0)),
            text=str(d.get("text", "") or ""),
            original_text=str(d.get("original_text", d.get("text", "") or "") or ""),
            state=str(d.get("state", "asr") or "asr"),
            speaker=str(d.get("speaker", "") or ""),
            confidence=conf,
            words=list(d.get("words", []) or []),
            # 旧工程里的 notes 键直接丢弃：字段从未有 UI 使用，已从数据类移除
            id=str(d.get("id") or uuid.uuid4().hex[:16]),
        )


@dataclass
class CueDocument:
    """一个工程 = 一个视频 + 一组字幕 + 元信息。"""

    source_video: str = ""
    duration: float = 0.0
    language: str = ""
    cues: List[Cue] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)  # engine/model/asr info/...
    path: str = ""  # 工程文件路径（.ssp）

    # ------------------------------------------------------------- 基本操作
    def __len__(self) -> int:
        return len(self.cues)

    @property
    def end_time(self) -> float:
        return max([c.end for c in self.cues], default=self.duration)

    def sorted(self) -> None:
        self.cues.sort(key=lambda c: (c.start, c.end))
        self._fix_overlap()

    def _fix_overlap(self) -> None:
        """后一条开始早于前一条结束时，压缩前一条的结束时间（保序，不丢内容）。"""
        for i in range(len(self.cues) - 1):
            a, b = self.cues[i], self.cues[i + 1]
            if a.end <= b.start:
                continue
            if a.start < b.start:
                a.end = max(a.start + 0.01, b.start - 0.001)
            else:
                # 起点相同（ASR 断句常见）：不留整段重叠，前条压成极短窗口
                a.end = a.start + 0.01

    def index_of(self, cue: Cue) -> int:
        for i, c in enumerate(self.cues):
            if c.id == cue.id:
                return i
        return -1

    def at_time(self, seconds: float) -> Optional[Cue]:
        for c in self.cues:
            if c.contains(seconds):
                return c
        return None

    def cue_by_id(self, cid: str) -> Optional[Cue]:
        for c in self.cues:
            if c.id == cid:
                return c
        return None

    # ------------------------------------------------------------- 结构编辑
    def insert(self, index: int, cue: Cue) -> None:
        self.cues.insert(max(0, min(index, len(self.cues))), cue)

    def remove(self, indices: Iterable[int]) -> None:
        keep = set(i for i in indices)
        self.cues = [c for i, c in enumerate(self.cues) if i not in keep]

    def split(self, index: int, seconds: float) -> List[Cue]:
        """在 ``seconds`` 处把第 index 条切成两条（文本按字数比例近似切分）。"""
        cue = self.cues[index]
        if not (cue.start + 0.05 < seconds < cue.end - 0.05):
            return [cue]
        text = cue.display_text
        ratio = (seconds - cue.start) / max(0.001, cue.duration)
        if text:
            cut = max(1, min(len(text) - 1, int(round(len(text) * ratio))))
            t1, t2 = text[:cut].strip(), text[cut:].strip()
        else:
            t1 = t2 = ""
        c1 = Cue(start=cue.start, end=seconds, text=t1, original_text=cue.original_text,
                 state="edited", speaker=cue.speaker, confidence=cue.confidence)
        c2 = Cue(start=seconds, end=cue.end, text=t2, original_text=cue.original_text,
                 state="edited", speaker=cue.speaker, confidence=cue.confidence)
        c1.words = [w for w in cue.words if float(w.get("start", 0)) < seconds]
        c2.words = [w for w in cue.words if float(w.get("start", 0)) >= seconds]
        self.cues[index:index + 1] = [c1, c2]
        return [c1, c2]

    def merge(self, indices: List[int]) -> Optional[Cue]:
        """合并若干条（按给定顺序）为一条。"""
        idx = sorted(set(int(i) for i in indices))
        if len(idx) < 2:
            return None
        picked = [self.cues[i] for i in idx if 0 <= i < len(self.cues)]
        if len(picked) < 2:
            return None
        first = min(i for i in idx if 0 <= i < len(self.cues))
        picked.sort(key=lambda c: c.start)
        texts = [c.display_text.strip() for c in picked if c.display_text.strip()]
        merged = Cue(
            start=picked[0].start,
            end=max(c.end for c in picked),
            text="\n".join(texts),
            original_text="\n".join((c.original_text or "").strip() for c in picked),
            state="edited",
            speaker=picked[0].speaker,
            confidence=min([c.confidence for c in picked if c.confidence is not None], default=None),
        )
        merged.words = [w for c in picked for w in c.words]
        # 只能删除"被选中"的那些行：idx 是表格多选，Ctrl 隔行点选完全正常，
        # 若按 idx[0]..idx[-1] 整段切片会把夹在中间未选中的字幕一起吞掉。
        drop = set(idx)
        # 保留项里首行位置替换成 merged，其余选中行直接消失；
        # 未选中但夹在中间的行原样保留（旧实现按切片整段替换会吞掉它们）。
        self.cues = [merged if i == first else c
                     for i, c in enumerate(self.cues)
                     if i == first or i not in drop]
        return merged

    def split_long(self, max_chars: int = 20, max_dur: float = 7.0) -> int:
        """按「每行最大字数 + 最长时长」把过长的条目重新切分，返回新增条数。"""
        before = len(self.cues)
        out: List[Cue] = []
        for c in self.cues:
            too_long = len(c.display_text.replace("\n", "")) > max_chars or c.duration > max_dur
            if not too_long or not c.display_text.strip():
                out.append(c)
                continue
            pieces = _smart_split(c, max_chars, max_dur)
            out.extend(pieces)
        self.cues = out
        return len(self.cues) - before

    # 句末标点：前一条以这些结尾 → 一句话说完了，后面的空隙是"换句/换人"
    # 的信号，保留不衔接。（"……"是犹豫未说完，不算句末，正常衔接。）
    _SENT_END = "。！？!?"
    _QUOTE_TAIL = "\"'」』）)】》"

    @classmethod
    def _ends_sentence(cls, text: str) -> bool:
        t = (text or "").rstrip()
        while t and t[-1] in cls._QUOTE_TAIL:      # 「…。" 先看标点
            t = t[:-1].rstrip()
        return bool(t) and t[-1] in cls._SENT_END

    def close_gaps(self, max_gap: float = 0.35) -> tuple:
        """消除相邻字幕之间的小空隙，让同一个人连续说话时字幕不闪断。

        对每一对相邻字幕：间隙 gap = 后一条.start - 前一条.end。
        三个条件同时满足才衔接（前一条 end 拉齐到后一条 start）：
          1. 0 < gap <= max_gap；
          2. 前一条不是句末标点（。！？）结尾 —— 句子说完后的停顿往往
             是换气或换人，保留；"……"是话没说完，照常衔接；
          3. 两条都标了 speaker 且不同 → 不衔接（说话人切换处保留停顿）。
        gap > max_gap 视为真实停顿，保留；已有重叠不动（_fix_overlap 负责）。
        返回 (调整条数, 消除的总秒数)。
        """
        cues = sorted(self.cues, key=lambda c: (c.start, c.end))
        touched = 0
        saved = 0.0
        for a, b in zip(cues, cues[1:]):
            gap = b.start - a.end
            if not (0 < gap <= max_gap):
                continue
            if self._ends_sentence(a.display_text):
                continue
            if a.speaker and b.speaker and a.speaker != b.speaker:
                continue
            a.end = b.start
            touched += 1
            saved += gap
        return touched, saved

    def total_gap(self, max_gap: float = 0.35) -> float:
        """当前 ≤max_gap 且符合衔接条件的小空隙总秒数（提示文案用）。"""
        cues = sorted(self.cues, key=lambda c: (c.start, c.end))
        return sum(b.start - a.end for a, b in zip(cues, cues[1:])
                   if 0 < b.start - a.end <= max_gap
                   and not self._ends_sentence(a.display_text)
                   and not (a.speaker and b.speaker and a.speaker != b.speaker))

    def dedupe_repeats(self) -> int:
        """删除 Whisper 常见的连续重复句。"""
        removed = 0
        prev: Optional[Cue] = None
        out: List[Cue] = []
        for c in self.cues:
            t = re.sub(r"\s+", "", c.display_text)
            if prev is not None and t and t == re.sub(r"\s+", "", prev.display_text):
                prev.end = max(prev.end, c.end)
                removed += 1
                continue
            out.append(c)
            prev = c
        self.cues = out
        return removed

    # --------------------------------------------------------------- 统计
    def stats(self) -> Dict[str, Any]:
        total = len(self.cues)
        chars = sum(len(c.display_text.replace("\n", "")) for c in self.cues)
        dur = self.end_time
        changed = sum(1 for c in self.cues if c.is_changed())
        review = sum(1 for c in self.cues if c.state == "review")
        return {
            "count": total,
            "chars": chars,
            "duration": dur,
            "chars_per_sec": (chars / dur) if dur else 0.0,
            "avg_cps": (chars / total) if total else 0.0,
            "changed": changed,
            "review": review,
        }

    # ----------------------------------------------------------- 序列化
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": "subtitle-studio-project",
            "version": 2,
            "source_video": self.source_video,
            "duration": self.duration,
            "language": self.language,
            "meta": self.meta,
            "cues": [c.to_dict() for c in self.cues],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "CueDocument":
        doc = CueDocument(
            source_video=str(d.get("source_video", "") or ""),
            duration=float(d.get("duration", 0.0) or 0.0),
            language=str(d.get("language", "") or ""),
            meta=dict(d.get("meta", {}) or {}),
        )
        # 手改/外部工具生成的工程可能畸形：cues 里混入非 dict 元素会在这里
        # 炸掉整个工程（load_project 只能整文件拒绝）。跳过畸形项，
        # 能救多少救多少——一条坏 cue 不该废掉整个文件。
        cues_raw = d.get("cues", []) or []
        doc.cues = [Cue.from_dict(c) for c in cues_raw if isinstance(c, dict)]
        return doc
    def snapshot(self) -> Dict[str, Any]:
        """给撤销栈用的深拷贝。"""
        return {"cues": [c.to_dict() for c in self.cues]}

    def restore(self, snap: Dict[str, Any]) -> None:
        self.cues = [Cue.from_dict(c) for c in snap.get("cues", [])
                     if isinstance(c, dict)]


def _hard_cut(s: str, max_chars: int) -> List[str]:
    """无标点超长串的硬切：与『平均再切一刀』路径同一条省略号/破折号保护
    规则——切点落在 …/— 整串标点中间时，把切点移到整串标点左端之外，
    宁可这段超一点也不把一个省略号劈成两头的孤立单点。极端串（整段全是
    标点）退化为在第 1 个字符后切，不会挂起。"""
    out: List[str] = []
    rest = s
    while len(rest) > max_chars:
        cut = max_chars
        if cut < len(rest) and rest[cut - 1] in "…—" and rest[cut] in "…—":
            j = cut
            while j > 1 and rest[j - 1] in "…—":
                j -= 1
            cut = j
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        out.append(rest)
    return out


def _smart_split(cue: Cue, max_chars: int, max_dur: float) -> List[Cue]:
    """优先按标点/换行切，其次按字数切；时间按字数比例分配。"""
    text = cue.display_text
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    segments: List[str] = []
    for ln in lines:
        parts = re.split(r"(?<=[，。！？；：,.!?;:])\s*", ln)
        buf = ""
        for p in parts:
            if not p:
                continue
            if len(buf) + len(p) <= max_chars:
                buf += p
            else:
                if buf:
                    segments.append(buf)
                if len(p) > max_chars:      # 无标点的超长串，硬切
                    segments.extend(_hard_cut(p, max_chars))
                    p = ""
                buf = p
        if buf:
            segments.append(buf)
    if not segments:
        return [cue]

    weights = [max(1, len(s)) for s in segments]
    total_w = sum(weights)
    span = max(0.01, cue.duration)
    # 时长上限 -> 需要拆成更多段
    need = max(1, int(-(-span // max_dur)))
    while need > len(segments) and len(segments) < need * 2:
        # 平均再切一刀
        grown: List[str] = []
        gw: List[int] = []
        for s in segments:
            if len(s) > 6:
                mid = len(s) // 2
                # 切点落在 …/— 标点串中间时（'……'是两个 U+2026、'——'两个
                # U+2014），从中间切会把一个标点劈成两半（上段尾一个点、
                # 下段头一个点）。把切点移到整串标点的左端之外即可；这里
                # 必须一步到位而不是逐步挪动——逐步挪在切点左右都是标点时
                # 会左右振荡死循环（第 47 轮实测）。整串皆标点的极端串退化为
                # 在第 1 个字符后切，不会挂起。
                if mid < len(s) and s[mid - 1] in "…—" and s[mid] in "…—":
                    j = mid
                    while j > 1 and s[j - 1] in "…—":
                        j -= 1
                    mid = j
                grown.extend([s[:mid].strip(), s[mid:].strip()])
                gw.extend([mid, len(s) - mid])
            else:
                grown.append(s)
                gw.append(len(s))
        if len(grown) == len(segments):
            break
        segments, weights = grown, gw

    out: List[Cue] = []
    span = max(0.01, cue.duration)
    min_piece = 0.2
    # 片段太多、原区间放不下时，先把尾部片段合并，否则时间轴必然被挤爆。
    while len(segments) > 1 and len(segments) * min_piece > span:
        segments[-2] = segments[-2] + segments[-1]
        segments.pop()
        weights = [max(1, len(s)) for s in segments]
        total_w = sum(weights)

    # 按字数比例铺时间，同时保证每段不短于 min_piece；
    # 因为片段数已满足 len*min_piece <= span，这样铺完必定正好落在 cue.end，
    # 不会溢出到下一条字幕（此前正是这里造成重叠）。
    bounds: List[float] = []
    acc = 0.0
    for w in weights:
        acc += span * (w / total_w)
        bounds.append(acc)
    for i in range(len(bounds)):
        bounds[i] = min(max(bounds[i], (i + 1) * min_piece), span)
    bounds[-1] = span

    for i, seg in enumerate(segments):
        start = cue.start + (bounds[i - 1] if i else 0.0)
        end = cue.start + bounds[i]
        words = [w for w in cue.words if start <= float(w.get("start", 0)) < end]
        out.append(Cue(
            start=round(start, 3), end=round(end, 3), text=seg,
            original_text=cue.original_text, state=cue.state, speaker=cue.speaker,
            confidence=cue.confidence, words=words,
        ))
    return out


def normalize_cues(doc: CueDocument, min_dur: float = 0.2, gap: float = 0.04,
                   close_gaps_under: float = 0.0) -> None:
    """通用清理：保证最短时长、消除重叠、留一点呼吸间隙。

    gap: 相邻条目之间刻意保留的间隙（0 = 无缝衔接）。
    close_gaps_under: >0 时，把不超过该秒数的小空隙补掉（前一条 end 拉齐到
    后一条 start）。连续说话时字幕之间留缝会让字幕"一闪一闪"，靠这个消除。
    """
    doc.sorted()
    for i, c in enumerate(doc.cues):
        if c.end < c.start + min_dur:
            c.end = c.start + min_dur
        if i + 1 < len(doc.cues):
            nxt = doc.cues[i + 1].start
            if c.end > nxt - gap:
                c.end = max(c.start + min_dur, nxt - gap)
    if close_gaps_under > 0:
        doc.close_gaps(close_gaps_under)   # 复用同一套保护规则，避免分叉
