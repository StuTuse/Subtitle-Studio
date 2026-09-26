"""大模型修正层：只改错别字，不动时间轴、不动条数。

核心保障
--------
* **分块 + 编号往返**：把 cue 文本按 ``[编号]`` 形式发给模型，回来按编号解析，
  条数/顺序对不上就自动重试（更严格的提示词），仍失败则回退并标红。
* **严格校验**：长度变化过大、疑似整段翻译、把编号吃掉的情况都会被拦下。
* **术语表 + 原稿**：作为「只读参考资料」注入，模型只依据它们统一用字。
* **流式**：使用 ``client.messages``/chat.completions 的流式输出，UI 能实时看到进度。
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Config, LLMProfile
from .i18n import S
from .model import Cue

Progress = Optional[Callable[[str, float], None]]
Cancel = Optional[Callable[[], bool]]


class LLMError(RuntimeError):
    pass


# ------------------------------------------------------------------ 客户端
_clients: Dict[tuple, Any] = {}


def load_client(prof: LLMProfile):
    from openai import OpenAI

    kwargs: Dict[str, Any] = {"api_key": prof.api_key or "not-needed"}
    if prof.base_url:
        kwargs["base_url"] = prof.base_url.rstrip("/")
    # 推理模型 + 大批次可能单次跑几分钟，超时必须给足，否则白等一场 Connection error
    try:
        kwargs["timeout"] = float(getattr(prof, "timeout", 300.0) or 300.0)
    except Exception:
        kwargs["timeout"] = 300.0
    kwargs["max_retries"] = 0        # 重试由 fix_document 统一管理，避免叠加放大占用
    # 同一接入点复用 client：每批都新建会重建 httpx 连接池，几十批就是
    # 几十次 TCP+TLS 握手。任何会改变 client 行为的字段都在缓存键里。
    key = (kwargs.get("base_url", ""), kwargs["api_key"], kwargs["timeout"])
    c = _clients.get(key)
    if c is None:
        c = _clients[key] = OpenAI(**kwargs)
    return c


class ReasoningBudgetError(LLMError):
    """推理模型的思考把 max_tokens 全吃光，正文为空。"""


_ABORT_NOTE = "__aborted__"      # worker 因全局止损放弃批次时的标记（不进失败清单）
_CANCELLED_NOTE = "__cancelled__"  # worker 因用户取消放弃批次的标记（同样不进失败清单）


class _UserCancelled(Exception):
    """chat() 流式在途时用户点了停止：立即中断当前请求。

    不继承 LLMError——它不是"服务出错"，worker 捕获后翻译成
    _CANCELLED_NOTE 静默收敛，与"没开跑就被取消"的批次同等待遇。
    """

_CONN_REFUSED_MARKERS = (
    "connection refused", "connectionreset", "connection reset",
    "connection aborted", "target machine actively refused",
    "getaddrinfo failed", "name or service not known",
    "nodename nor servname", "network is unreachable",
    "unable to connect", "failed to establish a new connection",
)


def _is_conn_refused(e: BaseException) -> bool:
    """服务根本没开 / 地址不可达（区别于偶发超时）。

    只有这种错误才值得全局止损：重试一万次也不会好。超时必须排除——
    偶发慢响应重试就能过去，误判会把整篇纠错半途掐掉。顺着 __cause__/
    __context__ 链向上找，因为 openai SDK 会把底层的 ConnectionRefusedError
    包进 APIConnectionError。
    """
    seen: set = set()
    chain: Optional[BaseException] = e
    while chain is not None and id(chain) not in seen:
        seen.add(id(chain))
        name = type(chain).__name__
        if name in ("TimeoutError", "ReadTimeout", "ConnectTimeout"):
            return False                     # 明确超时：交给重试，别全局止损
        if name == "ConnectionRefusedError":
            return True                      # Windows 的 str 是本地化文案，认类型最稳
        low = str(chain).lower()
        if any(k in low for k in _CONN_REFUSED_MARKERS):
            return True
        chain = chain.__cause__ or chain.__context__
    return False


def _reasoning_of(msg) -> str:
    for attr in ("reasoning_content", "reasoning", "thinking"):
        v = getattr(msg, attr, None)
        if v:
            return str(v)
    # 有些网关把思考塞在 model_extra / model_fields_set 里
    for holder in ("model_extra", "_model_extra", "__dict__"):
        d = getattr(msg, holder, None)
        if isinstance(d, dict):
            for k in ("reasoning_content", "reasoning", "thinking"):
                if d.get(k):
                    return str(d[k])
    return ""


_REASONING_NO_CAP: set = set()    # 已知"必须去掉 max_tokens 才吐正文"的 (base_url, model)
_REASON_INEFFECTIVE: set = set()  # 勾了"关闭思考"但服务端根本不执行的 (base_url, model)
_STREAM_TRUNCATED: set = set()    # 流式输出曾因 token 上限/断流被腰斩的 (base_url, model)

# 各家推理模型的常见命名。用分段精确匹配而不是子串：早先用 "o1" 做子串，
# 任何名字里带 o1 的模型（如 foo1-mini）都会被误判成推理模型。
_THINKING_MARKERS = ("deepseek-r1", "qwq", "o1", "o3", "thinking", "r1", "reasoning")


def _looks_reasoning(model: str) -> bool:
    seg = re.split(r"[^a-z0-9]+", (model or "").lower())
    if any(m in seg for m in _THINKING_MARKERS):
        return True
    return any(m in (model or "").lower() for m in ("deepseek-r1", "qwq", "-thinking"))


# OpenAI 官方 o 系（o1/o3/o4）与 gpt-5 系：采样参数被服务端硬拒——
# temperature≠1、top_p、以及未知 extra_body 字段一律 400。对这类模型
# 不发 temperature/top_p/thinking，其余照常。
_O_SERIES = re.compile(r"^(o[134](-mini)?)([-_.].+)?$", re.I)


def _strict_openai_reasoning(model: str) -> bool:
    m = (model or "").lower()
    return bool(_O_SERIES.match(m)) or m.startswith(("gpt-5", "chatgpt-4o"))


def _reasoning_usage(resp) -> int:
    """从 usage 里取思考 token 数；老网关没有该字段时返回 0。"""
    try:
        det = getattr(resp.usage, "completion_tokens_details", None)
        if det is None and isinstance(getattr(resp, "usage", None), dict):
            det = resp.usage.get("completion_tokens_details")
        if det is not None:
            rt = getattr(det, "reasoning_tokens", None)
            if rt is None and isinstance(det, dict):
                rt = det.get("reasoning_tokens")
            return int(rt or 0)
    except Exception:
        pass
    return 0


def chat(prof: LLMProfile, messages: List[Dict[str, str]],
         on_delta: Optional[Callable[[str], None]] = None,
         _retry_no_cap: bool = True,
         cancel: Optional[Callable[[], bool]] = None) -> str:
    """一次对话补全；返回完整文本。若 on_delta 提供则走流式。

    推理型模型（DeepSeek-R1 / Qwen3 / GLM 思考版等）会把 ``max_tokens`` 全部花在
    内部思考上，导致 ``content`` 为空。这里自动去掉上限重试一次并记住结论，
    同一接入点后续批次直接不再带 cap，避免重复浪费思考 token。

    ``cancel``：用户取消判定（fix_document 传入）。流式分支在每个 chunk
    检查一次——旧版在途请求只能干等完整响应/超时（最坏 300s），取消后
    界面上"停止"按下去，当前批次还要白等几分钟才真正停下。
    """
    client = load_client(prof)
    key = ((prof.base_url or "").rstrip("/"), prof.model)
    strict_openai = _strict_openai_reasoning(prof.model)
    kwargs: Dict[str, Any] = dict(
        model=prof.model,
        messages=messages,
    )
    if not strict_openai:
        # o 系/gpt-5 系拒绝 temperature≠1 与 top_p（400: unsupported value），
        # 参数干脆不发，服务端按默认采样。
        kwargs["temperature"] = float(prof.temperature)
        try:
            kwargs["top_p"] = float(prof.top_p)
        except Exception:
            pass
    cap = int(prof.max_tokens or 0)
    if cap > 0 and key not in _REASONING_NO_CAP:
        kwargs["max_tokens"] = cap
    # 关闭推理模型的"思考"。字幕纠错是逐字比对，不需要推理：实测同一网关
    # 同一批字幕，开启思考 51s、关闭后 0.3s，准确率不降。
    # 三种网关写法一起带上，但注意：实测并非每种都被执行——本地网关只认
    # reasoning_effort=none，另两种会被静默忽略；还有服务端干脆不支持
    # （如 deepseek-v41-flash）。所以响应回来后要验证是否真的生效，
    # 不生效就在结果里说清楚，而不是让用户以为已经关上了。
    # o 系/gpt-5 系对未知 extra_body 字段也回 400：它们默认不深度思考，
    # 无需关闭，直接跳过。
    want_no_reason = getattr(prof, "no_reasoning", False)
    if want_no_reason and not strict_openai:
        eb = kwargs.setdefault("extra_body", {})
        eb.update({"reasoning_effort": "none",
                   "thinking": {"type": "disabled"},
                   "chat_template_kwargs": {"enable_thinking": False}})
    elif _looks_reasoning(prof.model) and not strict_openai:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    def _check_no_reason_effective(r, msg) -> None:
        """验证"关闭思考"是否真的生效；不生效则记入 _REASON_INEFFECTIVE。"""
        if not want_no_reason or key in _REASON_INEFFECTIVE:
            return
        if _reasoning_usage(r) > 0 or _reasoning_of(msg):
            _REASON_INEFFECTIVE.add(key)

    def once(kw):
        if on_delta is None:
            r = client.chat.completions.create(**kw)
            msg = r.choices[0].message
            _check_no_reason_effective(r, msg)
            text = (getattr(msg, "content", None) or "").strip()
            if text:
                return text, ""
            return "", _reasoning_of(msg)
        kw2 = dict(kw)
        kw2["stream"] = True
        parts: List[str] = []
        reason_parts: List[str] = []
        stream_truncated = False       # finish_reason=length：正文被 token 上限腰斩
        last_resp = None
        try:
            for chunk in client.chat.completions.create(**kw2):
                if cancel is not None and cancel():
                    # 用户取消：立即停流。本批已收到的部分丢弃——fix_document
                    # 会把该批记为"已取消"，绝不能拿半截文本冒充完整修正。
                    raise _UserCancelled()
                last_resp = chunk
                try:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    finish = (chunk.choices[0].finish_reason
                              if chunk.choices else None)
                except (AttributeError, IndexError):
                    delta, finish = None, None
                if finish == "length":
                    # 输出在 token 上限处被截断：非流式路径 401 那套"正文为空
                    # 才报"的判定在这里失灵——截断文本非空，会被当完整结果
                    # 用出去。记下来，让调用方在结果里说清楚。
                    stream_truncated = True
                if delta is None:
                    continue
                d = getattr(delta, "content", None)
                if d:
                    parts.append(d)
                    on_delta(d)
                else:
                    # 推理模型流式时 content 恒为 None、思考走 reasoning 字段；
                    # 不收进来的话这里返回空串被当成功，用户看不到任何提示
                    rd = (getattr(delta, "reasoning_content", None)
                          or getattr(delta, "reasoning", None))
                    if rd:
                        reason_parts.append(rd)
        except _UserCancelled:
            raise                    # 用户取消：原样上抛，worker 翻译成静默取消
        except Exception as e:
            # 半途断流：已有输出留给调用方处理，但"连不上"必须抛出——
            # 吞掉它全局止损就收不到信号，剩余批次会继续排队白等
            if not parts or _is_conn_refused(e):
                raise
            stream_truncated = True    # 异常腰斩：比 finish_reason 更明确
        # 流式也做"关闭思考是否生效"校验：流式分支此前从不校验，
        # no_reasoning_note 对走流式的功能永远报不出参数未生效
        _check_no_reason_effective(last_resp, None)
        if stream_truncated:
            _STREAM_TRUNCATED.add(key)
        return "".join(parts).strip(), "".join(reason_parts).strip()

    text, reasoning = once(kwargs)
    if text or not reasoning or on_delta is not None or not _retry_no_cap:
        if not text and reasoning:
            raise ReasoningBudgetError(
                S(f"模型「{prof.model}」是推理模型，{cap} token 全部用在了内部思考上，"
                  "正文为空。\n\n解决：把「最大输出 token」调大（如 8192 以上），"
                  "或在设置里留空/填 0 表示不限制。",
                  f"Model 「{prof.model}」 is a reasoning model and spent all "
                  f"{cap} tokens on internal thinking — the body is empty.\n\n"
                  "Fix: raise Max output tokens (e.g. 8192+), or leave it "
                  "empty/0 in Settings for unlimited."))
        return text

    # 去掉 max_tokens 上限重试一次
    retry = dict(kwargs)
    retry.pop("max_tokens", None)
    text2, reasoning2 = once(retry)
    if text2:
        _REASONING_NO_CAP.add(key)      # 同接入点后续批次直接不带 cap
        return text2
    raise ReasoningBudgetError(
        S(f"模型「{prof.model}」只输出了内部思考、没有输出正文，且不限长度后仍然如此。\n\n"
          "通常是该服务把 reasoning 与 content 拆成了两个字段。可尝试：\n"
          "  · 换一个非推理模型（如 deepseek-chat、qwen-plus）\n"
          f"  · 或在提示词末尾加一句「直接输出结果，不要解释」\n"
          f"（思考片段末尾：…{reasoning2[-120:]}）",
          f"Model 「{prof.model}」 output only internal reasoning, no body — "
          "even with no token cap.\n\nThe service likely splits reasoning and "
          "content into separate fields. Try:\n"
          "  · switch to a non-reasoning model (deepseek-chat, qwen-plus…)\n"
          "  · or append to the prompt: \"Output the result directly, no explanation\"\n"
          f"(end of reasoning: …{reasoning2[-120:]})"))


def no_reasoning_note(prof: LLMProfile) -> str:
    """勾了「关闭思考」但服务端没执行时，给用户的一句说明；没这回事则返回空串。"""
    if not getattr(prof, "no_reasoning", False):
        return ""
    key = ((prof.base_url or "").rstrip("/"), prof.model)
    if key in _REASON_INEFFECTIVE:
        return (S(f"「{prof.model}」不接受本程序已知的任何一种关闭思考参数"
                  "（reasoning_effort / thinking / chat_template_kwargs 都被忽略），"
                  "思考仍会产生、拖慢速度并消耗 token。纠错结果不受影响；"
                  "想提速可换 reasoning_effort=none 生效的模型（如 Qwen3 / GLM-Flash 系）。",
                  f"「{prof.model}」 accepts none of the known no-reasoning "
                  "parameters (reasoning_effort / thinking / chat_template_kwargs "
                  "are ignored); thinking still runs, slowing it down and "
                  "burning tokens. Fix results are unaffected; for speed switch "
                  "to a model honoring reasoning_effort=none (Qwen3 / GLM-Flash)."))
    return ""


def truncation_note(prof: LLMProfile) -> str:
    """本接入点最近一次流式输出被腰斩（token 上限/断流）时的一句提醒。"""
    key = ((prof.base_url or "").rstrip("/"), prof.model)
    if key not in _STREAM_TRUNCATED:
        return ""
    return (S(f"「{prof.model}」最近有输出在 token 上限处被截断（或连接中断），"
              "对应批次已按原样收下但可能不完整。可调大「最大输出 token」"
              "或减小每批行数后对失败行重跑。",
              f"「{prof.model}」 recently hit the token cap (or the connection "
              "dropped); that batch was accepted as-is but may be incomplete. "
              "Raise Max output tokens or lower batch size and re-run failed rows."))


def test_connection(prof: LLMProfile) -> Tuple[bool, str, float]:
    t0 = time.time()
    try:
        out = chat(prof, [{"role": "user", "content": "回复两个字：可用"}], None)
        msg = (out or "")[:80]
        note = no_reasoning_note(prof)
        if note:
            msg = (msg + "\n" + note) if msg else note
        return True, msg, time.time() - t0
    except Exception as e:
        return False, _friendly_err(e), time.time() - t0


def _friendly_err(e: Exception) -> str:
    s = str(e)
    low = s.lower()
    if isinstance(e, ReasoningBudgetError):
        return s
    if "401" in low or "invalid api key" in low or "authentication" in low:
        return S("API Key 无效或未授权（401）。请检查密钥与 Base URL。",
                 "API key invalid or unauthorized (401). Check the key and Base URL.")
    if "404" in low and "model" in low:
        return S("模型名不存在（404）。请检查模型标识是否与服务商一致。",
                 "Model not found (404). Check the model name against your provider.")
    if "429" in low:
        return S("触发限流（429）。请降低并发/请求频率或稍后再试。",
                 "Rate limited (429). Lower concurrency/frequency or retry later.")
    if "connection" in low or "timeout" in low or "unreachable" in low or "dns" in low:
        return S("网络不通或超时。检查 Base URL、代理设置（本地服务注意 http://127.0.0.1）。",
                 "Network unreachable or timed out. Check Base URL and proxy "
                 "(local services need http://127.0.0.1).")
    if "converting" in low or "openai" in low and "api_key" in low:
        return S("OpenAI SDK 需要非空 api_key，本地服务可填任意占位串（如 ollama）。",
                 "The OpenAI SDK needs a non-empty api_key; for local services "
                 "any placeholder works (e.g. ollama).")
    return s[:220]


def _friendly_short(err: str) -> str:
    """换档提示里引用的失败原因：一句话足够，不贴大段 SDK 文案。"""
    head = (err or "").strip().splitlines()
    return (head[0][:80] if head else S("连接失败", "Connection failed"))


# ------------------------------------------------------------------ 提示词
# 提示词本体是发给模型的协议文本：中文提示词在中文 ASR 字幕场景效果最好，
# 且用户可整段自定义（设置页「恢复默认模板」写回 DEFAULT_TEMPLATE）。
# 因此协议文本不包 S()——它是数据，不是界面文案；界面上的标签/说明才双语。
DEFAULT_SYSTEM = """你是一名专业的中文字幕校对员。你的唯一任务是：修正语音识别（ASR）字幕中的**错别字、同音字错误、标点错误、明显的分词/断词错误**，并统一规范用字。

必须严格遵守：
1. 只做"最小必要"的文字修正。不得改写句式、不得润色、不得翻译、不得总结、不得增删信息、不得调整语序。
2. 严格保持输入的行数与行顺序：输入多少行，输出多少行；第 N 行只对应第 N 行。
3. 每行必须是 `[数字编号] 修正后的文本`，编号原样保留，行内不要换行、不要添加引号/代码块/解释/序号以外的任何符号。
4. 若某行没有需要修正的错误，就原样输出该行文本（不要改动）。
5. 不做任何推测性补写；无法判断的专有名词保留原样。
6. 参考资料只用于**统一用字和术语**，不得当作内容补进字幕。
7. 保留原有的语气与口语特征（包括语气词），除非它明显是识别错误。
8. 中文与英文/数字之间不加空格；英文单词内部不拆分。

输出只有修正后的行，不要任何前后缀说明。"""

DEFAULT_TEMPLATE = """请修正以下字幕的错别字与标点，严格保持行数与编号一一对应。

{context_block}【待修正字幕（共 {count} 行，编号 {first}-{last}）】
{payload}

现在输出修正后的 {count} 行，格式 `[编号] 文本`："""

DEFAULT_GLOSSARY_BLOCK = """【术语与专有名词表（用于统一用字，请勿当作字幕内容）】
{glossary}
"""

DEFAULT_SCRIPT_BLOCK = """【原始稿件/背景资料（仅用于核对人名、地名、专有名词；不得作为内容补写）】
{script}
"""

# 参考稿切片注入时的标题：告诉模型这段资料为什么被选出来
_SCRIPT_SLICE_BLOCK = """【原始稿件相关片段（因包含本批字幕中的词句而选中，仅用于核对专有名词）】
{script}
"""

STRICT_SUFFIX = "\n\n再次强调：必须恰好输出 {count} 行，编号从 {first} 到 {last} 且不得重复或缺失。不要输出任何解释。"


@dataclass
class PromptBundle:
    system: str = DEFAULT_SYSTEM
    template: str = DEFAULT_TEMPLATE
    glossary_block: str = DEFAULT_GLOSSARY_BLOCK
    script_block: str = DEFAULT_SCRIPT_BLOCK

    @staticmethod
    def from_cfg(cfg: Config) -> "PromptBundle":
        b = PromptBundle()
        if (cfg.prompt_template or "").strip():
            b.template = cfg.prompt_template
        return b

    def context_prefix(self, glossary: str, script: str) -> str:
        """稳定前缀：术语表 + 全篇参考稿拼进 system 消息。

        这份内容对每一批都完全相同——放进 system 后，支持 prompt cache
        的网关（DeepSeek/OpenAI 等按前缀计费打折）只需处理一次；此前
        每批 user 消息都原样带一份全量资料，2000 条 × batch 50 × 8000
        token 参考稿 ≈ 32 万 token 纯重复开销。
        """
        parts: List[str] = []
        if glossary.strip():
            parts.append(self.glossary_block.format(glossary=glossary.strip()))
        if script.strip():
            parts.append(self.script_block.format(script=script.strip()))
        if not parts:
            return self.system
        return self.system + "\n\n" + "\n".join(parts)

    def render(self, cues: List[Cue], start_no: int, glossary: str = "",
               script: str = "", script_slice: str = "") -> str:
        payload = "\n".join(f"[{start_no + i}] {(c.display_text or '').strip()}"
                            for i, c in enumerate(cues))
        first, last = start_no, start_no + len(cues) - 1
        # 新语义：glossary/script 全量走 system 前缀；user 消息里只放
        # 与本批文本相关的参考稿切片（老模板的 {glossary_block}/
        # {script_block} 槽位保留传空串，自定义模板不炸）。
        gb = ""
        sb = ""
        cb = ""
        if script_slice.strip():
            cb = _SCRIPT_SLICE_BLOCK.format(script=script_slice.strip())
        try:
            return self.template.format(count=len(cues), first=first, last=last,
                                        payload=payload, glossary_block=gb,
                                        script_block=sb, context_block=cb)
        except KeyError:
            # 用户自定义模板里字段不全时的兜底
            return (self.template + "\n\n" + cb + payload)


@dataclass
class FixResult:
    texts: List[str]
    changed: int = 0
    failures: List[str] = field(default_factory=list)   # 人类可读的问题说明
    raw_batches: List[str] = field(default_factory=list)


# 模型爱在结尾追加的礼貌语。只收「不可能作为台词开头」的说法，
# 避免把「如果你的电脑配置不够」这类正当续行误删。
_META_PREFIXES = (
    "以上", "以上就是", "上述", "以上就是全部", "希望这", "希望对", "如有",
    "请参考", "如有需要", "祝好", "谢谢", "好的，", "好的!", "好的！",
    "以下是", "如下", "说明：", "备注：", "解释：", "注释：", "修正后", "修改后",
)
_META_RE = re.compile(r"^\s*[（(\[【]?\s*(?:" + "|".join(map(re.escape, _META_PREFIXES)) + r")")


def parse_numbered(text: str, expected: range) -> Dict[int, str]:
    """把模型输出解析成 {编号: 文本}。

    容忍：markdown 代码块围栏、``[n]``/``n.``/``n)``/``n：``/裸编号 等编号风格、
    以及个别模型把一条折成两行（续行会并回上一条）。
    同时避免把模型自带的礼貌性收尾（"希望对你有帮助！"）并进最后一条字幕。
    """
    out: Dict[int, str] = {}
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```$", "", cleaned)
    lines = [ln.strip() for ln in cleaned.split("\n")]
    pat = re.compile(r"^\[?\s*(\d{1,5})\s*\]?\s*[\.、:：\)\]]?\s*(.*)$")
    lo, hi = min(expected), max(expected)
    buf_no: Optional[int] = None
    for ln in lines:
        if not ln:
            continue
        m = pat.match(ln)
        if m:
            no = int(m.group(1))
            body = m.group(2).strip()
            if lo <= no <= hi:
                # 编号在期望范围内就当编号行——包括空文本。模型忠实回显
                # "[5]"（该行没内容）时 group(2) 为空：若落进下面的续行
                # 合并，字面量 "[5]" 会粘到上一条字幕上污染文本。存空串，
                # 上游 (got.get(no) or "") 本就把空文本当"未改动"跳过。
                buf_no = no
                out[buf_no] = body
                continue
        if buf_no is None:
            continue                      # 开头的寒暄，丢弃
        if _META_RE.match(ln):
            continue                      # "希望对你有帮助！" 之类的收尾，不污染字幕
        prev = out.get(buf_no, "")
        if re.search(r"[。！？!?.]$", prev):
            continue                      # 上一条已是完整句，视为新内容而非续行
        out[buf_no] = (prev + " " + ln).strip()
    for k in list(out):
        out[k] = _strip_wrappers(out[k])
    return out


def _strip_wrappers(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    if len(s) >= 2 and s[0] in "\"'“「" and s[-1] in "\"'”」":
        s = s[1:-1].strip()
    return s


_EN_FUNC_WORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "that", "this", "these", "those", "is", "are", "was", "were", "be", "been",
    "being", "it", "we", "you", "they", "he", "she", "i", "my", "your", "our",
    "their", "as", "at", "by", "from", "about", "there", "here", "what", "which",
}


def _zh_ratio(t: str) -> float:
    t = t.strip()
    if not t:
        return 0.0
    zh = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    return zh / len(t)


def looks_translated(src: str, dst: str) -> bool:
    """粗略判断模型是不是把内容「翻译」了，而不是「纠错」。

    难点在于合法的术语替换（``达芬奇调色`` → ``DaVinci Resolve 调色``）会让中文占比
    骤降，光看比例会误杀。真正的翻译句子里必然出现英语虚词（the / we / about…），
    而术语替换只会插入一两个专有名词。
    """
    a, b = _zh_ratio(src), _zh_ratio(dst)
    tokens = [w.lower() for w in re.findall(r"[A-Za-z]+", dst)]
    funcs = sum(1 for w in tokens if w in _EN_FUNC_WORDS)
    if a > 0.25 and b < a * 0.35 and funcs >= 2:
        return True                      # 中文句子里塞进了成串英文虚词
    if a < 0.1 and b > 0.5 and src.strip():
        return True                      # 拉丁文原文被转写成中文
    if a > 0.5 and b == 0.0 and src.strip():
        return True                      # 中文全没了
    return False


def sanity_check(src: str, dst: str) -> Optional[str]:
    """返回 None 表示可接受，否则返回拒绝原因。"""
    if dst is None or dst == "":
        return S("输出为空", "Empty output")
    if src.strip() and not dst.strip():
        return S("输出为空", "Empty output")
    ls, ld = len(src.strip()), len(dst.strip())
    # 先判翻译再判长度：整句被换成英文时必然同时触发长度异常，
    # 但「被翻译」才是更准确、也更可操作的原因。
    if looks_translated(src, dst):
        return S("疑似被翻译而非纠错", "Looks translated, not corrected")
    if ls and (ld > ls * 2.2 + 12 or ld < ls * 0.45):
        return S(f"长度异常（{ls}→{ld}）", f"Suspicious length ({ls}→{ld})")
    return None


# ------------------------------------------------------------------ 修正流程
def _is_local_base(base_url: str) -> bool:
    """Ollama / LM Studio 这类本机网关不设 Key；空 Key 不该被拦。"""
    try:
        host = re.sub(r"^https?://", "", (base_url or "").strip().lower())
        host = host.split("/")[0].rsplit("@", 1)[-1].split(":")[0]
        return host in ("127.0.0.1", "localhost", "::1") or host.endswith(".local")
    except Exception:
        return False


def fix_document(cfg: Config, cues: List[Cue], progress: Progress = None,
                 cancel: Cancel = None, on_cue: Optional[Callable[[int, str], None]] = None,
                 extra: str = "") -> FixResult:
    """批量纠错。extra 是"本轮补充指令"：只进本次提示词，不写回 cfg.glossary
    （曾经拼进术语表并保存，点一次运行就在持久配置里叠一份，越滚越大）。"""
    prof = cfg.profile()
    # 空 Key 拦截只该针对真·云端：本机网关（127.0.0.1 / localhost / *.local）
    # 不需要 Key。此前只放行 kind=="ollama"，而 kind 只有走预设下拉才会被
    # 标上——手动填本地地址时 kind 仍是 "openai"，于是「测试连接」能过、
    # 一点「运行纠错」却报"尚未配置 API Key"，同一服务两个入口互相矛盾。
    if (not prof.api_key and prof.kind != "ollama"
            and not _is_local_base(prof.base_url)):
        raise LLMError(S("尚未配置 API Key。请到「模型设置」里填写。",
                         "No API key configured. Fill it in under Model settings."))
    # 备用档：当前档连不上时按顺序热切换（base_url 必须与已挂档不同，
    # 同一网关换档名没有意义）。多档配置的用户在端点重启时不再整篇报废。
    _failover: List[LLMProfile] = [
        p for p in (cfg.profiles or [])
        if getattr(p, "enabled", True) and p.base_url
        and p.base_url.rstrip("/") != (prof.base_url or "").rstrip("/")
    ]
    bundle = PromptBundle.from_cfg(cfg)
    glossary = (cfg.glossary or "").strip()
    if extra.strip():
        glossary = (glossary + "\n【本轮补充】" + extra.strip()).strip()
    script = (cfg.reference_script or "").strip()
    # 参考稿切片：按本批字幕里出现的词句把相关行挑出来，只进该批的
    # user 消息——全篇资料走 system 稳定前缀，切片兜住「本批恰好涉及
    # 资料里某段」的专名核对，两全。
    script_lines = [ln for ln in script.splitlines() if ln.strip()]
    def _script_slice(batch: List[Cue]) -> str:
        if not script_lines or len(script) <= 2000:
            # 短参考稿不值得切：直接走 system 全量（进稳定前缀）
            return ""
        blob = "".join(c.display_text for c in batch)   # 无空格：中文可直接 n-gram
        picked = [ln for ln in script_lines
                  if any(w and w in ln for w in
                         (t for t in blob.split() if len(t) >= 2))]
        if not picked:
            # 中文没有空格分词：n-gram 命中判定（blob 太短构不成 2-gram
            # 时退化为单字命中——单字专名如人名「甲」也要能命中资料行）
            n = 2 if len(blob) >= 2 else 1
            grams = {blob[i:i + n] for i in range(len(blob) - n + 1)
                     if not any(ch.isspace() for ch in blob[i:i + n])}
            if not grams and blob.strip():
                grams = {blob.strip()}
            picked = [ln for ln in script_lines
                      if any(g in ln.lower() for g in grams)
                      and ln.strip()][:20]
        return "\n".join(picked[:20])
    # system 稳定前缀（全量资料）：所有批共用，命中网关 prompt cache
    system_msg = bundle.context_prefix(glossary, script)
    bs = max(1, int(cfg.batch_size))
    batches = [(i, cues[i:i + bs]) for i in range(0, len(cues), bs)]
    result = FixResult(texts=[c.display_text for c in cues])
    done = 0
    lock = __import__("threading").Lock()
    _abort_flag: List[Optional[str]] = [None]   # 服务连不上：全局止损标记
    _prof_holder: List[LLMProfile] = [prof]     # 当前生效档（故障转移时热替换）
    _failed_bases: set = {(prof.base_url or "").rstrip("/")}
    _bases_seen_at_start: set = set(_failed_bases)   # 换档判据基线（主档计入）
    _failover_note: List[str] = []              # 给完成日志的换档说明

    def one(idx: int, batch: List[Cue], attempt: int = 0) -> Optional[Dict[int, str]]:
        msg = bundle.render(batch, idx, script_slice=_script_slice(batch))
        if attempt:
            msg += STRICT_SUFFIX.format(count=len(batch), first=idx,
                                        last=idx + len(batch) - 1)
        # system 是稳定前缀（全量资料），user 只含本批字幕 + 相关切片：
        # 前缀被网关 prompt cache 复用，批次间仅增量计费
        messages = [{"role": "system", "content": system_msg},
                    {"role": "user", "content": msg}]
        raw = chat(_prof_holder[0], messages, None, cancel=cancel)
        got = parse_numbered(raw, range(idx, idx + len(batch)))
        missing = [n for n in range(idx, idx + len(batch)) if n not in got]
        if cfg.strict_mode and (missing or len(got) != len(batch)):
            raise ValueError(S(f"编号缺失/多余（期望 {len(batch)}，得到 {len(got)}）",
                               f"Row numbers missing/extra (expected {len(batch)}, got {len(got)})"))
        return got

    def _try_failover(err: str) -> bool:
        """止损瞬间找下一个可用档；找到就热切换并清止损。线程安全：
        只有一个线程能把换档跑完（双重检查）。返回是否换成了新档。

        返回 True 只代表「真的换到（或换过了）一个还没试过的档」。全部
        备用档都进过 _failed_bases 后必须返回 False，让 worker 走止损
        分支退出——否则并发 worker 会互相把"已换过"当成"有新档可换"，
        无限循环重试（实测 230k 条 progress 刷屏）。"""
        if not _failover:
            return False
        with lock:
            # 快速路径「别的线程已换好档」：本线程拿到的错误是换档前的
            # 旧档报的，重试一次新档。判据用「当前档不是主档」——
            # _abort_flag 初始就是 None（首次 refused 还没设过），且
            # 全部备档耗尽时 _failed_bases 不再增长，绝不能返回 True，
            # 否则 attempt 上限一过又 continue，永远退不出来。
            if _prof_holder[0] is not prof and len(_failed_bases) > len(_bases_seen_at_start):
                # 还有没试过的档才值得再试；没有则 False
                if any((p.base_url or "").rstrip("/") not in _failed_bases
                       for p in _failover):
                    return True
                return False
            nxt = None
            for p in _failover:
                b = (p.base_url or "").rstrip("/")
                if b not in _failed_bases:
                    nxt = p
                    break
            if nxt is None:
                return False
            _failed_bases.add((nxt.base_url or "").rstrip("/"))
            _prof_holder[0] = nxt
            _abort_flag[0] = None
            note = S(f"「{prof.name}」连不上（{_friendly_short(err)}），"
                     f"已自动切换到「{nxt.name}」继续。",
                     f"「{prof.name}」 unreachable ({_friendly_short(err)}); "
                     f"switched to 「{nxt.name}」 automatically.")
            _failover_note.append(note)
            return True

    def _sleep(seconds: float) -> None:
        """可中断的等待：取消时不必等完整退避时间。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if cancel and cancel():
                return
            time.sleep(min(0.25, max(0.01, deadline - time.time())))

    def worker(job: Tuple[int, List[Cue]]):
        idx, batch = job
        try:
            return _worker_inner(job)
        except _UserCancelled:
            # chat() 流式在途被用户掐断：与"没开跑就被取消"同等待遇
            return idx, None, _CANCELLED_NOTE
        except Exception as e:                # noqa: BLE001
            # cancel()/_sleep/render 里冒出的意外异常绝不能逸出到 fut.result()，
            # 否则整个 as_completed 循环被打断，已完成批次的结果全部作废。
            return idx, None, _friendly_err(e)

    def _worker_inner(job: Tuple[int, List[Cue]]):
        idx, batch = job
        if cancel and cancel():
            return idx, None, _CANCELLED_NOTE
        err = ""
        # 换档重试不消耗 attempt 名额：auto_retry=0 的用户在主档挂掉时
        # 仍要能吃到备档的完整一轮。上限 = 配置轮数 + 每个备用档各一轮。
        max_attempt = max(1, int(cfg.auto_retry) + 1) + len(_failover)
        attempt = 0
        while attempt < max_attempt:
            if _abort_flag[0] is not None:
                return idx, None, _ABORT_NOTE
            if attempt:
                # 限流/并发满：等久一点再试，固定几百毫秒只会一直撞在同一个坑里
                _sleep(min(20.0, 3.0 * (2 ** (attempt - 1)))
                       if S("限流", "Rate limited") in err else 1.2 * attempt)
            if cancel and cancel():
                return idx, None, _CANCELLED_NOTE
            try:
                got = one(idx, batch, attempt)
                if got is None:
                    return idx, None, S("无结果", "No result")
                return idx, got, ""
            except ReasoningBudgetError as e:
                # 改配置才能解决，重试只会白等
                return idx, None, _friendly_err(e)
            except Exception as e:
                err = _friendly_err(e)
                if S("API Key", "API key") in err or S("模型名不存在", "Model not found") in err:
                    return idx, None, err          # 参数错误，重试无意义
                if _is_conn_refused(e):
                    # 服务根本没开/地址写错。先试故障转移：还有别的启用档
                    # 就热切换清止损，本批用新档重试；没有备用档才全局止损。
                    if _try_failover(err):
                        with lock:
                            if progress:
                                progress(_failover_note[-1], -1)
                        continue       # 立即用新档重试本批（不耗 attempt 退避）
                    if _abort_flag[0] is None:
                        _abort_flag[0] = err
                    return idx, None, err
                if cancel and cancel():
                    return idx, None, _CANCELLED_NOTE
                attempt += 1
        return idx, None, err

    workers = max(1, min(int(cfg.concurrency), len(batches) or 1))
    aborted_cancels = False
    cancelled_count = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(worker, j) for j in batches]
        for fut in cf.as_completed(futures):
            if fut.cancelled():
                continue                    # 被止损取消的排队批次：直接跳过
            idx, got, err = fut.result()
            if err == _CANCELLED_NOTE:
                # 用户取消而未开跑/中途退出的批次：静默收敛，绝不进失败清单——
                # 旧版把每个批次一条"已取消"塞满 failures（300 批=300 条），
                # 界面显示"完成但有告警"，把用户的主动停止说成了一堆错误。
                with lock:
                    cancelled_count += 1
                continue
            with lock:
                done += 1
                if progress:
                    progress(S(f"已修正 {done}/{len(batches)} 批",
                               f"Fixed {done}/{len(batches)} batches"),
                             done / max(1, len(batches)))
            if _abort_flag[0] is not None and not aborted_cancels:
                # 服务连不上：排队中还没开跑的批次一律不再发起
                aborted_cancels = True
                for f2 in futures:
                    f2.cancel()
            if err:
                if err != _ABORT_NOTE:      # 止损而放弃的批次不进失败清单刷消息
                    result.failures.append(S(f"第 {idx + 1} 行起：{err}",
                                             f"From row {idx + 1}: {err}"))
                continue
            assert got is not None
            batch = cues[idx:idx + bs]
            for off, cue in enumerate(batch):
                no = idx + off
                new = (got.get(no) or "").strip()
                if not new:
                    continue
                old = cue.display_text
                reason = sanity_check(old, new) if cfg.strict_mode else None
                if reason:
                    with lock:
                        result.failures.append(S(f"第 {no + 1} 行被拦下（{reason}）",
                                                 f"Row {no + 1} rejected ({reason})"))
                    cue.state = "review"
                    continue
                if new != old:
                    with lock:
                        result.texts[no] = new
                        result.changed += 1
                        # 直接写回 cue：拒绝的行上面已标 review，这里若只回传 texts，
                        # 调用方一旦忘记应用（如无界面 CLI）就会导出未纠错的字幕。
                        if not cue.original_text:
                            cue.original_text = old
                        cue.text = new
                        cue.state = "llm"
                    if on_cue:
                        # 回调是 UI 桥（Qt 信号发射）：桥本身抛异常不该打断
                        # as_completed 循环——打断会作废所有后续批次成果，
                        # 且 with 块退出还要等在跑线程收尾
                        try:
                            on_cue(no, new)
                        except Exception:
                            pass
    if _failover_note:
        # 换过档的说明进失败清单（警告性质）：用户要知道这轮结果其实
        # 是备用档出的，模型/风格可能与主档不同。
        result.failures.extend(_failover_note)
    if _abort_flag[0] is not None:
        # 止损时已经写回 cue 的批次成果不能丢：把部分结果随异常带上，
        # 调用方（FixWorker/UI）能展示"已修正 N 条 + 失败原因"，而不是
        # 看着一条失败消息以为全部白跑（cue 其实已经改了一半）。
        result.failures.append(S("连接中断，剩余批次未执行",
                                 "Connection lost; remaining batches skipped"))
        raise LLMPartialError(
            S("连不上模型服务，已停止剩余批次。\n\n",
              "Cannot reach the model service; remaining batches stopped.\n\n")
            + _abort_flag[0]
            + S("\n\n请检查：模型服务是否启动（本地网关要先开）、"
                "地址是否正确、网络/代理是否正常。",
                "\n\nCheck: is the model service running (start local gateways "
                "first), is the address right, and do network/proxy work?"), result)
    note = no_reasoning_note(_prof_holder[0])
    if note:
        result.failures.append(note)       # 跑完提示一次即可，不逐批刷屏
    tnote = truncation_note(_prof_holder[0])
    if tnote:
        result.failures.append(tnote)      # 截断提醒同理：一次就够
    if cancelled_count:
        # 用户主动取消：给一条汇总而不是逐批刷屏。已完成批次的成果（写回
        # cue 的）保留有效，日志让用户知道实际修到了多少。
        result.failures.append(S(
            f"已取消：剩余 {cancelled_count} 批未执行，本轮已修正 "
            f"{result.changed} 行",
            f"Cancelled: {cancelled_count} batch(es) skipped; "
            f"{result.changed} row(s) fixed this run"))
    return result


class LLMPartialError(LLMError):
    """批量纠错中途止损：已完成的批次成果在 result 里，别浪费。

    纯连接类止损（全体批次未开始时 result 为空）对调用方而言与普通
    LLMError 无异——isinstance 判断向后兼容。
    """

    def __init__(self, msg: str, partial_result: "FixResult"):
        super().__init__(msg)
        self.partial_result = partial_result
