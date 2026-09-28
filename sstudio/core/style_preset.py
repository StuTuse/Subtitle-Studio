# -*- coding: utf-8 -*-
"""字幕样式模型 + 预设存储。

两级样式体系（用户核心需求）：
* 全局样式 —— 对整条轨道的所有字幕生效，是「默认外观」；
* 单条覆盖 —— 某一条字幕只想改自己（比如标题行加大加粗），只存差异
  字段，渲染时按字段级合并（override 有值用 override，否则用全局）。

预设存储：全局样式整体保存成命名预设（config.burn_style_presets），
新项目直接套用上一次的字体样式。单条覆盖不进预设（它是工程内的
临时差异），但「从选中条新建预设」路径支持把某条的完整外观固化为
全局预设。

ASS 映射要点（libass 语义，写渲染链前先钉死）：
* 颜色是 &HAABBGGRR（BGR 序 + 透明度 AA：00 不透明、FF 全透明）。
* Shadow = 阴影向下/右的位移像素；Outline = 描边宽度。
  「阴影大小/模糊」libass 原生只有 Shadow（偏移）与 Outline（其中
  BorderStyle=3 时 Outline 充当背景块），模糊由带 \blur 的覆盖标签
  或高斯软化的 BackColour 实现——我们用 Dialogue 行内联 \blur标签
  精确表达「模糊量」，把偏移量拆成 \shad 之外再叠加 \pos 微调。
* Alignment 数字键盘语义：1=左下 2=中下 3=右下 …7=左上 8=中上 9=右上。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# Windows 自带字体里字幕场景的常客；下拉就给这些 + 系统枚举
COMMON_FONTS = [
    "Microsoft YaHei", "SimHei", "SimSun", "KaiTi", "FangSong",
    "DengXian", "Source Han Sans SC", "Noto Sans SC",
    "Arial", "Segoe UI", "Times New Roman", "Verdana",
    "Impact", "Georgia", "Consolas",
]

# ASS 颜色 &HAABBGGRR 的解析（接受 #RRGGBB / #RRGGBBAA / 空）
_HEX6 = re.compile(r"^#([0-9A-Fa-f]{6})$")
_HEX8 = re.compile(r"^#([0-9A-Fa-f]{8})$")


@dataclass
class CueStyle:
    """一条字幕（或全局轨道）的外观。None 字段 = 未覆盖，用全局值。

    默认字段语义（全局样式里 None 等价于出厂默认）：
    font 字体家族名；size 字号（PlayRes 1080 基准 px）；
    color 主文字色 #RRGGBB；alpha 0..1（0 不透明）；
    bold/-1 加粗；italic 斜体；underline 下划线；
    outline 描边宽 px；outline_color 描边色；
    shadow 阴影偏移 px（libass Shadow：向右下平移的距离）；
    shadow_color 阴影色；shadow_blur 阴影/描边模糊量 px（\blur）；
    align 1-9 数字键盘位置；margin_v 底边距 px（align 上排时是顶边距）；
    margin_h 左右边距 px；scale_x/scale_y 横纵缩放 %；spacing 字间距 px。
    """
    font: Optional[str] = None
    size: Optional[int] = None
    color: Optional[str] = None
    alpha: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    outline: Optional[float] = None
    outline_color: Optional[str] = None
    shadow: Optional[float] = None
    shadow_color: Optional[str] = None
    shadow_blur: Optional[float] = None
    align: Optional[int] = None          # 1..9 数字键盘
    margin_v: Optional[int] = None
    margin_h: Optional[int] = None
    scale_x: Optional[int] = None        # 50..200
    scale_y: Optional[int] = None
    spacing: Optional[float] = None

    # ------------------------------------------------------------- 合并
    def merged_over(self, base: "CueStyle") -> "CueStyle":
        """把 self（可能是单条覆盖）的非 None 字段盖到 base 上，返回新对象。"""
        out = CueStyle(**asdict(base))
        for k, v in asdict(self).items():
            if v is not None:
                setattr(out, k, v)
        return out

    def overridden_fields(self) -> List[str]:
        """哪些字段被覆盖了（单条级 UI 高亮「这条有自己的样式」用）。"""
        return [k for k, v in asdict(self).items() if v is not None]

    # ------------------------------------------------------------- 序列化
    def to_dict(self) -> Dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: Optional[Dict]) -> "CueStyle":
        d = d or {}
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# 全局出厂样式（1080p 基准：黑描边白字 + 轻阴影，通用观感）
DEFAULT_STYLE = CueStyle(
    font="Microsoft YaHei", size=64, color="#FFFFFF", alpha=0.0,
    bold=False, italic=False, underline=False,
    outline=2.5, outline_color="#000000",
    shadow=1.0, shadow_color="#000000", shadow_blur=0.0,
    align=2, margin_v=52, margin_h=60, scale_x=100, scale_y=100,
    spacing=0.0,
)


# ------------------------------------------------------------ 颜色转换
def hex_to_ass(color: str, alpha: float = 0.0) -> str:
    """#RRGGBB[AA] + alpha(0..1) → ASS &HAABBGGRR。非法输入回落白色。"""
    m = _HEX8.match(color or "")
    if m:
        rr, gg, bb, aa = (m.group(1)[i:i + 2] for i in (0, 2, 4, 6))
        a = int(aa, 16)
    elif _HEX6.match(color or ""):
        rr, gg, bb = (color[1:3], color[3:5], color[5:7])
        a = int(round(max(0.0, min(1.0, alpha)) * 255))
    else:
        rr, gg, bb, a = "FF", "FF", "FF", 0
    return f"&H{a:02X}{bb}{gg}{rr}"


# ------------------------------------------------------------ ASS 渲染
def style_to_ass_line(name: str, st: CueStyle) -> str:
    """CueStyle → ASS V4+ Style 行。所有字段都显式落值（全局样式必有值）。"""
    bold = -1 if st.bold else 0
    italic = -1 if st.italic else 0
    underline = -1 if st.underline else 0
    align = 2 if not (1 <= (st.align or 2) <= 9) else st.align
    margin_h = max(0, int(st.margin_h or 0))
    return (
        f"Style: {name},{st.font},{int(st.size or 64)},"
        f"{hex_to_ass(st.color, st.alpha or 0.0)},&H000000FF,"
        f"{hex_to_ass(st.outline_color or '#000000')},"
        f"{hex_to_ass(st.shadow_color or '#000000', 0.5)},"
        f"{bold},{italic},{underline},0,"
        f"{int(st.scale_x or 100)},{int(st.scale_y or 100)},"
        f"{st.spacing or 0:.1f},0,1,{st.outline or 0:.1f},"
        f"{st.shadow or 0:.1f},{align},{margin_h},{margin_h},"
        f"{max(0, int(st.margin_v or 0))},1"
    )


def override_tags(st: CueStyle) -> str:
    """单条覆盖 → 行内联覆盖标签（放在 Dialogue 文本最前面）。

    只产出与全局值不同的字段：libass 行内标签覆盖 Style 同名字段。
    blur 同时作用于描边和阴影——正是「阴影模糊量」的 UI 语义。
    """
    tags = []
    if st.font:
        tags.append(rf"\fn{st.font}")
    if st.size:
        tags.append(rf"\fs{int(st.size)}")
    if st.color or st.alpha is not None:
        tags.append(rf"\c{hex_to_ass(st.color or '#FFFFFF', st.alpha or 0.0)}")
    if st.bold is not None:
        tags.append(r"\b1" if st.bold else r"\b0")
    if st.italic is not None:
        tags.append(r"\i1" if st.italic else r"\i0")
    if st.underline is not None:
        tags.append(r"\u1" if st.underline else r"\u0")
    if st.outline is not None:
        tags.append(rf"\bord{st.outline:.1f}")
    if st.outline_color:
        tags.append(rf"\3c{hex_to_ass(st.outline_color)}")
    if st.shadow is not None:
        tags.append(rf"\shad{st.shadow:.1f}")
    if st.shadow_color:
        tags.append(rf"\4c{hex_to_ass(st.shadow_color)}")
    if st.shadow_blur:
        tags.append(rf"\blur{st.shadow_blur:.1f}")
    if st.align and 1 <= st.align <= 9:
        tags.append(rf"\an{st.align}")
    if st.margin_v is not None:
        tags.append(rf"\MarginV{max(0, int(st.margin_v))}")
    if st.scale_x is not None or st.scale_y is not None:
        tags.append(rf"\fscx{int(st.scale_x or 100)}\fscy{int(st.scale_y or 100)}")
    if st.spacing is not None:
        tags.append(rf"\fsp{st.spacing:.1f}")
    return "{}" .format("".join(tags)) if tags else ""


def build_burn_ass(doc, global_style: CueStyle,
                   per_cue: Optional[Dict[str, CueStyle]] = None) -> str:
    """合成用 ASS：PlayRes = 视频真实分辨率（libass 按此缩放，1:1 观感）。

    doc 需要 width/height：取 meta 里的视频分辨率，缺省 1920x1080。
    单条覆盖按 cue.id 查 per_cue，字段级合并后再落 Dialogue。
    """
    w = int(getattr(doc, "meta", {}).get("width") or 1920)
    h = int(getattr(doc, "meta", {}).get("height") or 1080)
    per_cue = per_cue or {}
    header = f"""[Script Info]
; Generated by Subtitle Studio (video burn-in)
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {w}
PlayResY: {h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
    lines = [header + style_to_ass_line("Default", global_style) + "\n",
             "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"]
    from .formats import _ass_time
    for c in doc.cues:
        eff = global_style
        ov = per_cue.get(c.id)
        tag = ""
        if ov is not None:
            eff = ov.merged_over(global_style)
            tag = override_tags(ov)
        txt = c.display_text.strip().replace("\n", "\\N") or " "
        name = (c.speaker or "").replace(",", "").replace("\n", " ").strip()
        lines.append(
            f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,"
            f"{name},0,0,0,,{tag}{txt}"
        )
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ 预设存取
def presets_to_json(presets: Dict[str, CueStyle]) -> str:
    return json.dumps({k: v.to_dict() for k, v in presets.items()},
                      ensure_ascii=False, indent=1)


def presets_from_json(s: str) -> Dict[str, CueStyle]:
    try:
        d = json.loads(s or "{}")
    except ValueError:
        return {}
    return {k: CueStyle.from_dict(v) for k, v in d.items()
            if isinstance(v, dict)}
