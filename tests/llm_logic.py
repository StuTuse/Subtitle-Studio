"""提示词渲染 / 编号往返解析 / 严格校验。全程离线，不发请求。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, section  # noqa: E402

from sstudio.core import llm  # noqa: E402
from sstudio.core.model import Cue  # noqa: E402

section("1. 编号往返解析")
raw = """```
[0] 今天我们来聊聊这款显卡
[1] 它的性能提升非常明显
[2] 老实谈芯 认为这次很有诚意
[3] 以上就是全部内容
```"""
got = llm.parse_numbered(raw, range(4))
check("剥掉代码块围栏", 0 in got and "```" not in got.get(0, ""))
check("解析出 4 条", len(got) == 4, sorted(got))
check("内容正确", got.get(2) == "老实谈芯 认为这次很有诚意", repr(got.get(2)))

section("2. 各种编号风格")
for style, text in (("[n]", "[0] 甲\n[1] 乙"), ("n.", "0. 甲\n1. 乙"),
                    ("n)", "0) 甲\n1) 乙"), ("n:", "0：甲\n1：乙"),
                    ("n 空格", "0 甲\n1 乙"), ("裸编号", "0\t甲\n1\t乙"),
                    ("补零", "[00] 甲\n[01] 乙")):
    g = llm.parse_numbered(text, range(2))
    check(f"风格 {style}", len(g) == 2 and g.get(0, "").startswith("甲"), g)

section("3. 模型的杂讯输出")
g = llm.parse_numbered("好的，以下是修正后的结果：\n\n[0] 第一句\n[1] 第二句\n\n希望对你有帮助！",
                       range(2))
check("忽略开头寒暄与结尾礼貌语", len(g) == 2 and g[1] == "第二句", g)
g = llm.parse_numbered("[0] 这是第一句的\n完整内容\n[1] 第二句", range(2))
check("折行并回同一条", g.get(0) == "这是第一句的 完整内容", repr(g.get(0)))
g = llm.parse_numbered("[0] 第一句已经结束了。\n补充说明不是续行", range(1))
check("已成句后不再吞续行", g.get(0) == "第一句已经结束了。", repr(g.get(0)))
g = llm.parse_numbered("[0] 甲\n[99] 越界编号", range(1))
check("越界编号被忽略", list(g) == [0], g)
g = llm.parse_numbered("[10] 第十句\n[11] 第十一句", range(10, 12))
check("支持偏移编号", g.get(10) == "第十句", g)

section("4. 严格校验：放行正当修正")
for src, dst in (("今天天气不错", "今天，天气不错。"),
                 ("达芬奇调色", "DaVinci Resolve 调色"),
                 ("大家号", "大家好"),
                 ("这个功能非常强眼", "这个功能非常抢眼"),
                 ("它支持四K 六十帧", "它支持 4K 60 帧"),
                 ("R T X 5060", "RTX 5060")):
    check(f"放行「{src}」→「{dst}」", llm.sanity_check(src, dst) is None,
          llm.sanity_check(src, dst))

section("5. 严格校验：拦下越界行为")
check("空输出被拦", llm.sanity_check("有内容", "") is not None)
check("纯空格被拦", llm.sanity_check("有内容", "   ") is not None)
check("长度暴涨被拦", llm.sanity_check("短句", "短" * 60) is not None)
check("长度暴跌被拦", llm.sanity_check("这是一句比较长的中文句子用来测试", "测试") is not None)
check("中译英被拦", llm.sanity_check("今天我们来聊聊这款显卡的性能表现",
                                     "Today we talk about GPU performance") is not None)
check("整句换成英文且很长被拦",
      llm.sanity_check("你好", "Hello, this is a long English sentence about it.") is not None)
r = llm.sanity_check("今天我们来聊聊显卡", "Today we talk about it.")
check("翻译的归因为「翻译」而非「长度」", r is not None and "翻译" in r, r)

section("6. looks_translated 不误杀术语替换")
check("术语替换不算翻译", not llm.looks_translated("达芬奇调色很好用", "DaVinci Resolve 调色很好用"))
check("纯术语行不算翻译", not llm.looks_translated("RTX5060", "RTX 5060"))
check("整句英文化算翻译", llm.looks_translated("今天我们来聊聊这款显卡的性能表现如何",
                                             "We are going to talk about this GPU today."))

section("7. 提示词渲染")
cues = [Cue(start=0, end=1, text=f"第{i}句 原始") for i in range(3)]
b = llm.PromptBundle()
msg = b.render(cues, 0, "达芬奇=>DaVinci Resolve", "本期讲显卡")
check("含编号行", "[0] 第0句 原始" in msg and "[2] 第2句 原始" in msg)
check("含术语表", "达芬奇" in msg and "术语" in msg)
check("含原始稿件", "本期讲显卡" in msg)
check("声明行数", "共 3 行" in msg)
check("首尾编号提示", "0" in msg and "2" in msg)
check("偏移编号", "[10] 第0句 原始" in b.render(cues, 10, "", ""))
msg2 = b.render(cues, 0, "", "")
check("无术语表时不渲染空块", "术语" not in msg2)
check("无稿件时不渲染空块", "原始稿件" not in msg2)
check("系统提示词强调只改错别字", "错别字" in b.system)
check("系统提示词禁止改写", "改写" in b.system)

section("8. 模板健壮性")
b2 = llm.PromptBundle(template="请修正：{payload}")
try:
    check("自定义模板缺占位符仍可渲染", "[0]" in b2.render(cues, 0, "术语", "稿件"))
except Exception as e:
    check("自定义模板缺占位符仍可渲染", False, e)
try:
    check("坏模板不崩", isinstance(llm.PromptBundle(template="{不存在的字段}").render(
        cues, 0, "", ""), str))
except Exception as e:
    check("坏模板不崩", False, e)

sys.exit(finish())
