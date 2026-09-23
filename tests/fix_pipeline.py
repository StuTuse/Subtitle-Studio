"""纠错流水线：并发、重试、编号缺失、恶意输出拦截。用假客户端，不联网。"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, section  # noqa: E402

from sstudio.core import llm  # noqa: E402
from sstudio.core.config import Config, LLMProfile  # noqa: E402
from sstudio.core.model import Cue  # noqa: E402

_REAL_CHAT = llm.chat   # 本文件会 monkey-patch llm.chat，真身留一份给第 6 段用

BASE = ["大家号", "这个功能很强眼", "今天天气不错", "它支持四K六十帧", "我们开始吧",
        "这个效果很震撼", "说实话真不错", "价格也很合适", "我觉得可以买",
        "感谢观看本期视频", "记得点赞关注", "下期再见"]
FIX = {"大家号": "大家好", "这个功能很强眼": "这个功能很抢眼",
       "它支持四K六十帧": "它支持 4K 60 帧"}


def make_cues():
    return [Cue(start=i * 2.0, end=i * 2.0 + 1.8, text=t, original_text=t)
            for i, t in enumerate(BASE)]


def make_cfg(batch=4, conc=3, retry=2, strict=True, api_key="sk-fake"):
    """隔离出来的干净配置：不依赖、也不会改动用户真实设置。"""
    cfg = Config()
    cfg.batch_size, cfg.concurrency, cfg.auto_retry = batch, conc, retry
    cfg.strict_mode = strict
    cfg.profiles = [LLMProfile(name="测试", base_url="http://127.0.0.1:9/v1",
                               api_key=api_key, model="test-model")]
    cfg.active_profile = "测试"
    return cfg


def nums_of(user_msg):
    return [int(m) for m in re.findall(r"^\[(\d+)\]", user_msg, flags=re.M)]


section("1. 正常路径：改对、不误伤、重试兜底")
calls = {"n": 0}


def good_chat(prof, messages, on_delta=None, **kw):
    calls["n"] += 1
    nums = nums_of(messages[-1]["content"])
    if calls["n"] <= 2 and nums and min(nums) == 0:      # 第一批先来一次限流
        raise RuntimeError("429 rate limit exceeded")
    return "好的：\n\n" + "\n".join(f"[{n}] {FIX.get(BASE[n], BASE[n])}" for n in nums) \
        + "\n\n希望对你有帮助！"


llm.chat = good_chat
cues = make_cues()
live = []
res = llm.fix_document(make_cfg(), cues, progress=None,
                       on_cue=lambda no, new: live.append((no, new)))
check("3 处错别字被修正", res.changed == 3, res.changed)
check("限流批次经重试成功", not res.failures, res.failures)
check("实时回调收到 3 条", len(live) == 3, live)
check("落在正确行", res.texts[0] == "大家好" and res.texts[1] == "这个功能很抢眼"
      and res.texts[3] == "它支持 4K 60 帧")
check("未出错行保持原文", res.texts[2] == "今天天气不错" and res.texts[11] == "下期再见")
check("礼貌语未混入字幕", all("希望" not in t for t in res.texts))
check("cue 状态推进为 llm", cues[0].state == "llm", cues[0].state)
check("original_text 未被覆盖", cues[0].original_text == "大家号")

section("2. 恶意输出：翻译 / 灌水 / 缺失")
llm.chat = lambda prof, messages, on_delta=None, **kw: "\n".join(
    f"[{n}] " + ("Hello, this is an English sentence translated from Chinese."
                 if n % 4 == 0 else "过" * 90 if n % 4 == 1 else BASE[n])
    for n in nums_of(messages[-1]["content"]))
cues2 = make_cues()
res2 = llm.fix_document(make_cfg(), cues2, progress=None)
reviewed = [i for i, c in enumerate(cues2) if c.state == "review"]
check("翻译输出被拦", any("翻译" in f for f in res2.failures), res2.failures[:2])
check("灌水输出被拦", any("长度" in f for f in res2.failures))
check("被拦行进入 review", len(reviewed) >= 2, reviewed)
check("被拦行未覆盖显示文本", all(cues2[i].display_text == BASE[i] for i in reviewed))
check("被拦行 original_text 保留", all(cues2[i].original_text == BASE[i] for i in reviewed))

section("3. 编号缺失触发重试而非静默漏改")
state = {"tries": 0}


def dropy(prof, messages, on_delta=None, **kw):
    nums = nums_of(messages[-1]["content"])
    state["tries"] += 1
    body = nums[:-1] if state["tries"] <= 3 else nums   # 前几次都少一行
    return "\n".join(f"[{n}] {BASE[n]}" for n in body)


llm.chat = dropy
cues3 = make_cues()
res3 = llm.fix_document(make_cfg(), cues3, progress=None)
check("缺行时发起了重试", state["tries"] > len(BASE) // 4, state["tries"])
check("重试后仍缺的行被记为失败而非错改",
      all(i < len(cues3) for i in range(len(cues3))), "见 failures")

section("4. 彻底失败不炸整个流程")
def dead(prof, messages, on_delta=None, **kw):
    raise RuntimeError("connection reset by peer")


# 连接类错误现在会全局止损并抛 LLMError（服务没开时不再白等几分钟重试）；
# 非连接类错误（如 reset）仍走逐批失败清单，保证单批失败不炸整个流程。
def dead_nonconn(prof, messages, on_delta=None, **kw):
    raise RuntimeError("server exploded with code 500")


llm.chat = dead_nonconn
cues4 = make_cues()
try:
    res4 = llm.fix_document(make_cfg(), cues4, progress=None)
    check("服务全挂时返回失败清单而不是抛异常", len(res4.failures) >= 3,
          len(res4.failures))
    check("失败时字幕文本原样保留", res4.texts == BASE)
    check("失败原因可读", any("500" in f or "reset" in f
                          for f in res4.failures), res4.failures[:1])
except Exception as e:
    check("服务全挂时返回失败清单而不是抛异常", False, e)

section("4b. 连接错误全局止损（服务没开时秒级失败而非重试风暴）")
llm.chat = dead          # "connection reset" 归入连接类
cues4b = make_cues()
t_dead = __import__("time").time()
try:
    llm.fix_document(make_cfg(retry=5), cues4b, progress=None)
    check("连接错误快速止损", False, "没抛异常")
except llm.LLMError as e:
    dt = __import__("time").time() - t_dead
    check("连接错误快速止损", dt < 30 and "连不上" in str(e), "%.1fs %s" % (dt, str(e)[:40]))
except Exception as e:
    check("连接错误快速止损", False, e)

section("5. 取消与无 Key")
llm.chat = good_chat
# 空 Key 拦截只针对云端：本机网关（127.0.0.1）不设 Key 是常态，
# 拦了会跟「测试连接」自相矛盾（见 bugfix_sweep 第 20 节）。这里换
# 云端地址验证拦截仍然生效。
_cfg_nk = make_cfg(api_key="")
_cfg_nk.profiles[0].base_url = "https://api.deepseek.com/v1"
try:
    llm.fix_document(_cfg_nk, make_cues(), progress=None)
    check("未填 Key 时报可读错误", False, "没抛异常")
except llm.LLMError as e:
    check("未填 Key 时报可读错误", "API Key" in str(e), str(e)[:40])
# 本机地址 + 空 Key：放行（chat 已 stub 成功路径，应当正常跑完，
# 绝不能在参数检查就报"尚未配置 API Key"挡死）
try:
    _r_loc = llm.fix_document(make_cfg(api_key=""), make_cues(), progress=None)
    check("本机网关空 Key 不在参数检查被拦（正常跑完）",
          _r_loc.changed > 0, f"changed={_r_loc.changed}")
except llm.LLMError as e:
    check("本机网关空 Key 不在参数检查被拦（正常跑完）",
          "尚未配置" not in str(e), str(e)[:40])

cancelled = {"n": 0}


def slow(prof, messages, on_delta=None, **kw):
    cancelled["n"] += 1
    return "\n".join(f"[{n}] {BASE[n]}" for n in nums_of(messages[-1]["content"]))


llm.chat = slow
res5 = llm.fix_document(make_cfg(), make_cues(), progress=None, cancel=lambda: True)
check("取消后不再修改任何行", res5.changed == 0)

section("6. 「关闭思考」必须验证真生效，关不掉要如实告知")
from types import SimpleNamespace as NS  # noqa: E402


def fake_client(content, reasoning, reason_tok):
    msg = NS(content=content, reasoning_content=reasoning)
    usage = NS(completion_tokens_details=NS(reasoning_tokens=reason_tok))
    resp = NS(choices=[NS(message=msg)], usage=usage)

    class _C:
        @staticmethod
        def create(**kw):
            return resp
    return NS(chat=NS(completions=_C()))


def prof_for(model, no_reason=True):
    return LLMProfile(name="t", base_url="http://127.0.0.1:9/v1", api_key="k",
                      model=model, no_reasoning=no_reason, max_tokens=1024)


_saved_client = llm.load_client
try:
    # 6a 网关忽略开关：照样吐思考 → 记入低效表并给出提示
    # 注意：本文件前面把 llm.chat 换成了假函数，这里必须显式用真身
    p1 = prof_for("stubborn-model")
    llm.load_client = lambda p: fake_client("正文", "还在想…", 30)
    out = _REAL_CHAT(p1, [{"role": "user", "content": "x"}])
    check("忽略开关时正文照样返回", out == "正文", out)
    check("网关不执行时 no_reasoning_note 给出说明",
          "关不掉" in llm.no_reasoning_note(p1) or "忽略" in llm.no_reasoning_note(p1),
          llm.no_reasoning_note(p1)[:50])

    # 6b 网关真的关掉了：无思考、无提示
    p2 = prof_for("obedient-model")
    llm.load_client = lambda p: fake_client("正文", "", 0)
    check("真正生效时没有任何提示",
          _REAL_CHAT(p2, [{"role": "user", "content": "x"}]) == "正文")
    check("真正生效时 note 为空", llm.no_reasoning_note(p2) == "")

    # 6c 没勾开关就不该多事
    p3 = prof_for("stubborn-model", no_reason=False)
    check("未勾选时 note 恒为空", llm.no_reasoning_note(p3) == "")

    # 6d 思考吃光预算：去 cap 重试后仍空 → ReasoningBudgetError 且提示可操作
    p4 = prof_for("budget-model")
    llm.load_client = lambda p: fake_client("", "想了很多但没写正文", 1024)
    try:
        _REAL_CHAT(p4, [{"role": "user", "content": "x"}])
        check("思考吃光预算时报 ReasoningBudgetError", False, "没抛异常")
    except llm.ReasoningBudgetError as e:
        check("思考吃光预算时报 ReasoningBudgetError", True)
        check("报错含可操作建议", "思考" in str(e) and ("token" in str(e) or "模型" in str(e)),
              str(e)[:60].replace("\n", " "))

    # 6e 推理模型名识别：分段匹配，不许误伤
    check("deepseek-r1 判为推理模型", llm._looks_reasoning("deepseek-r1"))
    check("qwq-32b 判为推理模型", llm._looks_reasoning("QwQ-32B"))
    check("o1-mini 判为推理模型", llm._looks_reasoning("o1-mini"))
    check("foo1-mini 不误伤（分段而非子串）", not llm._looks_reasoning("foo1-mini"))
    check("gpt-4o 不误伤", not llm._looks_reasoning("gpt-4o"))
    check("glm-4-flash 不误伤", not llm._looks_reasoning("GLM-4-Flash"))
finally:
    llm.load_client = _saved_client
    llm._REASON_INEFFECTIVE.clear()
    llm._REASONING_NO_CAP.clear()

sys.exit(finish())
