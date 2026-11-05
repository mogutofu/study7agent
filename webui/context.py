# -*- coding: utf-8 -*-
"""长会话的上下文管理：工具结果剪枝 + 历史自动压缩。

参考 DSH 的 `dsh-compaction-basic` 与 `dsh-compaction-tool-result-pruner`：
  * 上下文快到上限时，把**最旧的一段**历史总结成一条摘要，最近的一段逐字保留；
    只压派生历史，系统提示词与工具说明不动；原始消息归档起来，不是真的删掉。
  * 压缩之前先把过大的工具结果剪成「保留开头 + 中间省略 + 保留结尾」，
    完整原文仍留在会话文件里，模型看到的是剪过的版本。
  * 撞上"上下文超长"错误时，绕过常规阈值做一次更狠的压缩再重试。

为什么必须做：agent 要长时间连续调用工具（画图、整理课件），历史会一直长；
没有这套机制时只能靠"砍掉前面的消息"，模型很快就会忘记任务目标并开始胡来。
只使用 Python 3.8 标准库。
"""

import json
import re

# 工具结果剪枝（对照 dsh-compaction-tool-result-pruner 的默认值）
PRUNE_THRESHOLD_CHARS = 8192
PRUNE_HEAD_CHARS = 4096
PRUNE_TAIL_CHARS = 1024
PRUNE_MARKER = "\n\n[... 工具结果中间部分已省略（原文仍在会话文件里）...]\n\n"

# 自动压缩（对照 dsh-compaction-basic 的默认值）
# 中文大致 1 个字 ≈ 1 token，英文 4 字符 ≈ 1 token，这里按混合文本保守估算
#
# 上下文窗默认值：**按接口自己报的上限来**，别拍脑袋写小。
# 老师反馈"上下文太短了"：以前这里是 65536，于是到 ~52K token 就自动压缩，
# 而 deepseek 这边问出来的 max_tokens 合法区间是 [1, 393216]（一次极小的探测请求，
# 接口原话 "the valid range of max_tokens is [1, 393216]"）——按这套接口的校验口径，
# 这个数就是模型的上下文长度。所以默认放到 393216（384K）。
# 想给某台机器调小：config\agent.json 里写 "context_window": 65536，
# 或按模型档写 "models": {"flash": {"context_window": 65536}}（见 README §2）。
DEFAULT_CONTEXT_WINDOW = 393216
COMPACT_THRESHOLD_RATIO = 0.8
COMPACT_RETAIN_RATIO = 0.16
COMPACT_MIN_RETAIN_TOKENS = 2048
SUMMARY_MAX_TOKENS = 2048
SUMMARY_INPUT_CHARS = 60000          # 送给摘要模型的原文上限（字符兜底）
# 摘要请求本身占上下文的比例上限：超过就可能"摘要请求自己也超窗口"（pi #8371）
SUMMARY_INPUT_RATIO = 0.35
MAX_COMPACT_ARCHIVES = 3

COMPACT_INSTRUCTION = """请把上面的对话历史压缩成一份「工作交接摘要」，供后续继续干活使用。

必须保留（漏了就会让后续工作跑偏）：
1. 用户最初的任务目标与所有明确要求（原话里的关键约束）；
2. 已经完成的工作与它们的结论（例如改造了哪些文件、画了哪些内容、跑通了什么）；
3. 关键路径与标识符：文件绝对路径、函数/变量名、命令、ID、报错原文；
4. 尚未完成的部分、下一步计划、以及踩过的坑（做过但没成功的尝试，避免重复走）。

可以丢弃：寒暄、重复的确认、工具调用的原始参数与完整输出、思考过程细节。

用中文、条目式输出，控制在 800 字以内。直接给摘要，不要客套话。"""


def estimate_tokens(text):
    """粗略估 token：中日韩字符按 1 个/token，其它按 4 字符/token。"""
    if not text:
        return 0
    text = str(text)
    wide = len(re.findall(r"[\u3000-\u9fff\uff00-\uffef]", text))
    rest = len(text) - wide
    return int(wide + rest / 4.0) + 1


def message_text(message):
    """把一条消息里所有"会被发出去"的文本拼起来（用于估算与摘要）。"""
    if not isinstance(message, dict):
        return ""
    chunks = []
    content = message.get("content")
    if isinstance(content, str):
        chunks.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                chunks.append(part.get("text") or "")
    for call in message.get("tool_calls") or []:
        function = (call or {}).get("function") or {}
        chunks.append("%s %s" % (function.get("name") or "", function.get("arguments") or ""))
    if message.get("reasoning"):
        chunks.append(message["reasoning"])
    return "\n".join(chunks)


def conversation_tokens(messages):
    return sum(estimate_tokens(message_text(item)) for item in (messages or []))


def prune_text(text, threshold=PRUNE_THRESHOLD_CHARS,
               head=PRUNE_HEAD_CHARS, tail=PRUNE_TAIL_CHARS):
    """超长文本剪成 头 + 省略标记 + 尾；没超就原样返回。"""
    text = text or ""
    if len(text) <= threshold:
        return text, False
    return text[:head] + PRUNE_MARKER + (text[-tail:] if tail else ""), True


def prune_tool_results(messages, threshold=PRUNE_THRESHOLD_CHARS):
    """把所有过大的 tool 结果就地剪枝，返回 (剪了几条, 省了多少字符)。

    原文没有丢：会话文件里那条消息的 content 仍然是完整内容，
    我们只是把「发请求时用的那份」复制件剪短（存在 `_pruned` 里）。
    """
    pruned = 0
    saved = 0
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        original = message.get("content")
        if not isinstance(original, str) or len(original) <= threshold:
            continue
        if not message.get("_pruned"):
            message["_raw_content"] = original
        text, changed = prune_text(original, threshold)
        if changed:
            message["content"] = text
            message["_pruned"] = True
            pruned += 1
            saved += len(original) - len(text)
    return pruned, saved


def restore_pruned(messages):
    """把剪枝过的工具结果还原成原文（导出/回放时用）。"""
    for message in messages or []:
        if isinstance(message, dict) and message.get("_pruned") and message.get("_raw_content"):
            message["content"] = message["_raw_content"]


def render_transcript(messages, max_tokens=SUMMARY_INPUT_CHARS // 4, max_chars=SUMMARY_INPUT_CHARS):
    """把要压缩的历史渲染成给摘要模型看的文本。

    **必须有上界**：社区已确认的坑（pi #8371「压缩输入没有上界，超出窗口的会话永远压不动」）——
    如果把整段历史原样丢给摘要模型，这次摘要请求自己就会超上下文，压缩永远失败、
    会话再也救不回来。所以按 token 预算裁（超长时留头 60% + 尾 40%，任务开头一定在）。
    """
    lines = []
    for message in messages or []:
        role = message.get("role")
        if role == "user":
            label = "用户" if not message.get("autoContinue") else "系统提醒"
        elif role == "assistant":
            label = "助手"
        elif role == "tool":
            label = "工具结果（%s）" % (message.get("name") or "")
        else:
            label = role or "?"
        text = message_text(message).strip()
        if not text:
            continue
        lines.append("【%s】%s" % (label, text))
    if not lines:
        return ""
    block = "\n".join(lines)
    # 中文大致 1 字 ≈ 1 token、英文 4 字符 ≈ 1 token，所以"字符数 ≤ token 预算"是保守且安全的
    limit = min(int(max_chars or 0) or len(block), max(4000, int(max_tokens or 0)))
    if len(block) <= limit:
        return block
    head = int(limit * 0.6)
    tail = limit - head
    return (block[:head] + "\n\n[... 中间省略 ...]\n\n" + block[-tail:])


def plan_compaction(messages, keep_tokens, force_ratio=None):
    """决定要压缩哪一段。

    返回 (要压缩的条数, 逐字保留的起始下标)；没有可压缩的就返回 (0, 0)。
    规则（与 DSH 一致）：
      * 从最旧的开始压，保留最近 keep_tokens 的那一段；
      * **不要把 assistant(tool_calls) 和它的工具结果切开**——下刀点落在 tool 上就往前挪一格；
      * 至少保留最后两条消息，绝不允许把整段历史都压掉；
      * 第一条 user 消息（原始任务）由调用方单独保留，跟着摘要一起放回前面。

    注意：**不能要求下刀点必须是 user 消息**。真实的长任务常常只有一条 user 消息
    （老师问一次，agent 连着几百轮工具），那样会一路退到开头、永远算不出可压区间。
    """
    messages = list(messages or [])
    if len(messages) < 4:
        return 0, 0
    total = conversation_tokens(messages)
    budget = int(keep_tokens)
    if force_ratio:
        budget = max(COMPACT_MIN_RETAIN_TOKENS, int(budget * force_ratio))
    if total <= budget:
        return 0, 0
    running = 0
    cut = len(messages) - 1
    for index in range(len(messages) - 1, -1, -1):
        running += estimate_tokens(message_text(messages[index]))
        if running >= budget:
            cut = index
            break
    while cut > 1 and messages[cut].get("role") == "tool":
        cut -= 1
    if cut <= 0 or cut >= len(messages) - 1:
        return 0, 0
    return cut, cut
