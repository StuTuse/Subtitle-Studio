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
from .model import Cue

Progress = Optional[Callable[[str, float], None]]
Cancel = Optional[Callable[[], bool]]


class LLMError(RuntimeError):
    pass


# ------------------------------------------------------------------ 客户端
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
    return OpenAI(**kwargs)


class ReasoningBudgetError(LLMError):
    """推理模型的思考把 max_tokens 全吃光，正文为空。"""


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

# 各家推理模型的常见命名。用分段精确匹配而不是子串：早先用 "o1" 做子串，
# 任何名字里带 o1 的模型（如 foo1-mini）都会被误判成推理模型。
_THINKING_MARKERS = ("deepseek-r1", "qwq", "o1", "o3", "thinking", "r1", "reasoning")


def _looks_reasoning(model: str) -> bool:
    seg = re.split(r"[^a-z0-9]+", (model or "").lower())
    if any(m in seg for m in _THINKING_MARKERS):
        return True
    return any(m in (model or "").lower() for m in ("deepseek-r1", "qwq", "-thinking"))


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
         _retry_no_cap: bool = True) -> str:
    """一次对话补全；返回完整文本。若 on_delta 提供则走流式。

    推理型模型（DeepSeek-R1 / Qwen3 / GLM 思考版等）会把 ``max_tokens`` 全部花在
    内部思考上，导致 ``content`` 为空。这里自动去掉上限重试一次并记住结论，
    同一接入点后续批次直接不再带 cap，避免重复浪费思考 token。
    """
    client = load_client(prof)
    key = ((prof.base_url or "").rstrip("/"), prof.model)
    kwargs: Dict[str, Any] = dict(
        model=prof.model,
        messages=messages,
        temperature=float(prof.temperature),
    )
    cap = int(prof.max_tokens or 0)
    if cap > 0 and key not in _REASONING_NO_CAP:
        kwargs["max_tokens"] = cap
    try:
        kwargs["top_p"] = float(prof.top_p)
    except Exception:
        pass
    # 关闭推理模型的"思考"。字幕纠错是逐字比对，不需要推理：实测同一网关
    # 同一批字幕，开启思考 51s、关闭后 0.3s，准确率不降。
    # 三种网关写法一起带上，但注意：实测并非每种都被执行——本地网关只认
    # reasoning_effort=none，另两种会被静默忽略；还有服务端干脆不支持
    # （如 deepseek-v41-flash）。所以响应回来后要验证是否真的生效，
    # 不生效就在结果里说清楚，而不是让用户以为已经关上了。
    want_no_reason = getattr(prof, "no_reasoning", False)
    if want_no_reason:
        eb = kwargs.setdefault("extra_body", {})
        eb.update({"reasoning_effort": "none",
                   "thinking": {"type": "disabled"},
                   "chat_template_kwargs": {"enable_thinking": False}})
    elif _looks_reasoning(prof.model):
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
        try:
            for chunk in client.chat.completions.create(**kw2):
                try:
                    d = chunk.choices[0].delta.content if chunk.choices else None
                except (AttributeError, IndexError):
                    d = None
                if d:
                    parts.append(d)
                    on_delta(d)
        except Exception:
            if not parts:
                raise
        return "".join(parts).strip(), ""

    text, reasoning = once(kwargs)
    if text or not reasoning or on_delta is not None or not _retry_no_cap:
        if not text and reasoning:
            raise ReasoningBudgetError(
                f"模型「{prof.model}」是推理模型，{cap} token 全部用在了内部思考上，"
                "正文为空。\n\n解决：把「最大输出 token」调大（如 8192 以上），"
                "或在设置里留空/填 0 表示不限制。")
        return text

    # 去掉 max_tokens 上限重试一次
    retry = dict(kwargs)
    retry.pop("max_tokens", None)
    text2, reasoning2 = once(retry)
    if text2:
        _REASONING_NO_CAP.add(key)      # 同接入点后续批次直接不带 cap
        return text2
    raise ReasoningBudgetError(
        f"模型「{prof.model}」只输出了内部思考、没有输出正文，且不限长度后仍然如此。\n\n"
        "通常是该服务把 reasoning 与 content 拆成了两个字段。可尝试：\n"
        "  · 换一个非推理模型（如 deepseek-chat、qwen-plus）\n"
        f"  · 或在提示词末尾加一句「直接输出结果，不要解释」\n"
        f"（思考片段末尾：…{reasoning2[-120:]}）")


def no_reasoning_note(prof: LLMProfile) -> str:
    """勾了「关闭思考」但服务端没执行时，给用户的一句说明；没这回事则返回空串。"""
    if not getattr(prof, "no_reasoning", False):
        return ""
    key = ((prof.base_url or "").rstrip("/"), prof.model)
    if key in _REASON_INEFFECTIVE:
        return (f"「{prof.model}」不接受本程序已知的任何一种关闭思考参数"
                "（reasoning_effort / thinking / chat_template_kwargs 都被忽略），"
                "思考仍会产生、拖慢速度并消耗 token。纠错结果不受影响；"
                "想提速可换 reasoning_effort=none 生效的模型（如 Qwen3 / GLM-Flash 系）。")
    return ""


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
        return "API Key 无效或未授权（401）。请检查密钥与 Base URL。"
    if "404" in low and "model" in low:
        return "模型名不存在（404）。请检查模型标识是否与服务商一致。"
    if "429" in low:
        return "触发限流（429）。请降低并发/请求频率或稍后再试。"
    if "connection" in low or "timeout" in low or "unreachable" in low or "dns" in low:
        return "网络不通或超时。检查 Base URL、代理设置（本地服务注意 http://127.0.0.1）。"
    if "converting" in low or "openai" in low and "api_key" in low:
        return "OpenAI SDK 需要非空 api_key，本地服务可填任意占位串（如 ollama）。"
    return s[:220]


# ------------------------------------------------------------------ 提示词
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

{glossary_block}{script_block}【待修正字幕（共 {count} 行，编号 {first}-{last}）】
{payload}

现在输出修正后的 {count} 行，格式 `[编号] 文本`："""

DEFAULT_GLOSSARY_BLOCK = """【术语与专有名词表（用于统一用字，请勿当作字幕内容）】
{glossary}
"""

DEFAULT_SCRIPT_BLOCK = """【原始稿件/背景资料（仅用于核对人名、地名、专有名词；不得作为内容补写）】
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

    def render(self, cues: List[Cue], start_no: int, glossary: str, script: str) -> str:
        payload = "\n".join(f"[{start_no + i}] {(c.display_text or '').strip()}"
                            for i, c in enumerate(cues))
        gb = self.glossary_block.format(glossary=glossary.strip()) if glossary.strip() else ""
        sb = self.script_block.format(script=script.strip()) if script.strip() else ""
        first, last = start_no, start_no + len(cues) - 1
        try:
            return self.template.format(count=len(cues), first=first, last=last,
                                        payload=payload, glossary_block=gb, script_block=sb)
        except KeyError:
            # 用户自定义模板里字段不全时的兜底
            return (self.template + "\n\n" + gb + sb + payload)


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
        if m and m.group(2) != "":
            no = int(m.group(1))
            if lo <= no <= hi:
                buf_no = no
                out[buf_no] = m.group(2).strip()
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
        return "输出为空"
    if src.strip() and not dst.strip():
        return "输出为空"
    ls, ld = len(src.strip()), len(dst.strip())
    # 先判翻译再判长度：整句被换成英文时必然同时触发长度异常，
    # 但「被翻译」才是更准确、也更可操作的原因。
    if looks_translated(src, dst):
        return "疑似被翻译而非纠错"
    if ls and (ld > ls * 2.2 + 12 or ld < ls * 0.45):
        return f"长度异常（{ls}→{ld}）"
    return None


# ------------------------------------------------------------------ 修正流程
def fix_document(cfg: Config, cues: List[Cue], progress: Progress = None,
                 cancel: Cancel = None, on_cue: Optional[Callable[[int, str], None]] = None
                 ) -> FixResult:
    prof = cfg.profile()
    if not prof.api_key and prof.kind != "ollama":
        raise LLMError("尚未配置 API Key。请到「模型设置」里填写。")
    bundle = PromptBundle.from_cfg(cfg)
    glossary = (cfg.glossary or "").strip()
    script = (cfg.reference_script or "").strip()
    bs = max(1, int(cfg.batch_size))
    batches = [(i, cues[i:i + bs]) for i in range(0, len(cues), bs)]
    result = FixResult(texts=[c.display_text for c in cues])
    done = 0
    lock = __import__("threading").Lock()

    def one(idx: int, batch: List[Cue], attempt: int = 0) -> Optional[Dict[int, str]]:
        msg = bundle.render(batch, idx, glossary, script)
        if attempt:
            msg += STRICT_SUFFIX.format(count=len(batch), first=idx,
                                        last=idx + len(batch) - 1)
        messages = [{"role": "system", "content": bundle.system},
                    {"role": "user", "content": msg}]
        raw = chat(prof, messages, None)
        got = parse_numbered(raw, range(idx, idx + len(batch)))
        missing = [n for n in range(idx, idx + len(batch)) if n not in got]
        if cfg.strict_mode and (missing or len(got) != len(batch)):
            raise ValueError(f"编号缺失/多余（期望 {len(batch)}，得到 {len(got)}）")
        return got

    def _sleep(seconds: float) -> None:
        """可中断的等待：取消时不必等完整退避时间。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if cancel and cancel():
                return
            time.sleep(min(0.25, max(0.01, deadline - time.time())))

    def worker(job: Tuple[int, List[Cue]]):
        nonlocal done
        idx, batch = job
        if cancel and cancel():
            return idx, None, "已取消"
        err = ""
        for attempt in range(max(1, int(cfg.auto_retry) + 1)):
            if attempt:
                # 限流/并发满：等久一点再试，固定几百毫秒只会一直撞在同一个坑里
                _sleep(min(20.0, 3.0 * (2 ** (attempt - 1))) if "限流" in err else 1.2 * attempt)
            if cancel and cancel():
                return idx, None, "已取消"
            try:
                got = one(idx, batch, attempt)
                if got is None:
                    return idx, None, "无结果"
                return idx, got, ""
            except ReasoningBudgetError as e:
                # 改配置才能解决，重试只会白等
                return idx, None, _friendly_err(e)
            except Exception as e:
                err = _friendly_err(e)
                if "API Key" in err or "模型名不存在" in err:
                    return idx, None, err          # 参数错误，重试无意义
                if cancel and cancel():
                    return idx, None, "已取消"
        return idx, None, err

    workers = max(1, min(int(cfg.concurrency), len(batches) or 1))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(worker, j) for j in batches]
        for fut in cf.as_completed(futures):
            idx, got, err = fut.result()
            with lock:
                done += 1
                if progress:
                    progress(f"已修正 {done}/{len(batches)} 批", done / max(1, len(batches)))
            if err:
                result.failures.append(f"第 {idx + 1} 行起：{err}")
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
                        result.failures.append(f"第 {no + 1} 行被拦下（{reason}）")
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
                        on_cue(no, new)
    note = no_reasoning_note(prof)
    if note:
        result.failures.append(note)       # 跑完提示一次即可，不逐批刷屏
    return result


def rewrite_with_llm(prof: LLMProfile, system: str, user: str,
                     on_delta: Optional[Callable[[str], None]] = None) -> str:
    """给"自由提示词/润色"面板用的直通调用。"""
    return chat(prof, [{"role": "system", "content": system},
                       {"role": "user", "content": user}], on_delta)
