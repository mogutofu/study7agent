# -*- coding: utf-8 -*-
"""Study7Agent WebUI 核心层。

职责：
  1. 配置：复用 CLI 的 config/agent.json，WebUI 专属项放在 config/webui.json。
  2. DeepSeek / OpenAI 兼容客户端：流式解析 content、reasoning_content、tool_calls、usage。
  3. Agent 工具循环：原生 function calling（替代 CLI 的 ```agent-action 文本协议）。
  4. 会话存储：webui/data/sessions/*.json，消息保持 OpenAI 格式。

只使用 Python 3.8.10 标准库，离线内网 / Windows 7 x64 可直接运行。
"""

import base64
import copy
import hashlib
import http.client
import json
import os
import random
import re
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

import context as context_module
import desktop as desktop_module
import notify

VERSION = "1.1.0-beta11"

# --------------------------------------------------------------------------
# 图片（识图）
# --------------------------------------------------------------------------

IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 6          # 兜底值；实际用 webui.max_images（默认 256）
# 单次请求里图片的总字节预算：超过就截断并说明。
# 256 张图按每张 1MB 算就是 256MB，直接把请求撑爆——所以张数上限之外必须有字节兜底。
IMAGE_PAYLOAD_BUDGET_BYTES = 24 * 1024 * 1024
# 输出被长度上限截断时，自动接着写的最大次数（避免无限续写）
MAX_AUTO_CONTINUE = 3
# "把工具调用写成文字"时提醒重试几次（两次还改不过来就停下来说明原因）
MAX_TOOL_TEXT_RETRY = 2
# 工具参数被截断时，最多重发这一步几次（社区已知会卡死重试循环的坑）
MAX_TRUNCATED_RETRY = 2
# 循环检测窗口：在最近多少次工具轮里统计同一动作（能抓 A-B-A-B 交替循环）
CALL_LOG_WINDOW = 8
# 截断后的重试提示：第二次要明确要求"把参数写精简"（hermes #20975）
TRUNCATED_HINT = ("你刚才那次工具调用的参数被截断了（没有收到完整的 JSON）。"
                  "请重新发起这次调用，并确认参数是完整合法的 JSON。")
TRUNCATED_HINT_LENGTH = ("你刚才那次工具调用的参数**超过了单次输出长度上限**被截断。"
                         "请把这次调用拆小：长内容分多次 write_file/edit_file 写入，"
                         "或只传必要的参数。")
TRUNCATED_HINT_CONCISE = ("还是被截断了。请**把参数写到最精简**：不要重复原文，"
                          "长文本分成多次小调用（例如先写前 50 行，再用 edit_file 追加）。")


def is_length_truncation(finish_reason, calls):
    """是不是"因为输出长度上限"被截断的工具调用（对照 hermes #7680 / opencode #18108）。

    长度截断和网络中断是两类问题：前者要提示模型"把参数写小"，后者重发就行。
    """
    if (finish_reason or "") != "length":
        return False
    if not calls:
        return False
    return any(not _call_args_ok(call) for call in calls)
# 推理等级：越高想得越久、越贵。空字符串 = 不改动接口参数（沿用网关默认）
# 官方文档：reasoning_effort 取 low/high/max（minimal/medium/xhigh/ultra 会被映射）
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "max")
# 兼容 OpenRouter 风格：low/medium/high 是别名，但真实网关（如 api.deepseek.com）也接受
DEFAULT_AUTH_HEADER = "Authorization"
# 历史消息里保留最近几次带图消息的图片（图片会重复计费，只留最近两张）
MAX_IMAGE_HISTORY = 2

# 模型不支持图片时的典型回复，用来给出“换模型”提示
IMAGE_REFUSAL = re.compile(
    r"(无法查看|无法识别|不能查看|不能识别|看不到这?张图|图片未能成功加载|"
    r"cannot (?:view|see|read) (?:the|this) image|unable to view the image|"
    r"image (?:is )?not supported)",
    re.I)


def image_mime(path):
    return IMAGE_MIME.get(os.path.splitext(path or "")[1].lower(), "")


def is_image_path(path):
    return bool(image_mime(path))


def image_size(data):
    """解析常见图片格式的宽高，失败返回 (0, 0)。仅用于界面展示。"""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
        if data[:2] == b"BM":
            width, height = struct.unpack("<ii", data[18:26])
            return abs(int(width)), abs(int(height))
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X":
                width = int.from_bytes(data[24:27], "little") + 1
                height = int.from_bytes(data[27:30], "little") + 1
                return width, height
            if chunk == b"VP8 ":
                width = int.from_bytes(data[26:28], "little") & 0x3FFF
                height = int.from_bytes(data[28:30], "little") & 0x3FFF
                return width, height
        if data[:2] == b"\xff\xd8":
            index = 2
            while index < len(data) - 9:
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", data[index + 5:index + 9])
                    return int(width), int(height)
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                length = struct.unpack(">H", data[index + 2:index + 4])[0]
                index += 2 + length
    except (struct.error, IndexError, ValueError):
        pass
    return 0, 0


def load_image_part(item):
    """把一条图片记录转成 OpenAI 兼容的 image_url 部件；不可用时返回 None。"""
    url = ""
    path = ""
    mime = ""
    if isinstance(item, dict):
        url = item.get("url") or ""
        path = item.get("path") or ""
        mime = item.get("mime") or ""
    else:
        path = str(item or "")
    if url:
        return {"type": "image_url", "image_url": {"url": url}}
    if not path or not os.path.isfile(path):
        return None
    mime = mime or image_mime(path)
    if not mime:
        return None
    try:
        if os.path.getsize(path) > MAX_IMAGE_BYTES:
            return None
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    return {"type": "image_url",
            "image_url": {"url": "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))}}


def looks_like_image_refusal(text):
    return bool(text) and bool(IMAGE_REFUSAL.search(text))

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

AGENT_DEFAULTS = {
    "base_url": "http://127.0.0.1:8000/v1/chat/completions",
    "model": "local-model",
    "active_model": "",
    "models": {},
    "api_key": "",
    "stream": True,
    "timeout_seconds": 300,
    "ca_file": "",
    "insecure_skip_verify": False,
    "headers": {},
    "auth_profiles": {},
    "max_content_chars": 120000,
    "command": {
        "enabled": True,
        "confirm_prefixes": [
            "del", "erase", "format", "reg", "net", "netsh", "powershell",
            "wmic", "shutdown", "sc", "takeown", "icacls", "diskpart", "cipher",
        ],
        "blocked_prefixes": [],
        "always_confirm": False,
        "audit_log": os.path.join("logs", "commands.log"),
        "max_output_bytes": 65536,
        # 画图这类多步任务（一笔一个工具轮）轮次需求很大，默认给到 1024；
        # 真正兜底的是 max_run_seconds（单次运行时长）与熔断，不靠轮次卡人
        "max_tool_rounds": 1024,
    },
    "memory": {
        "enabled": True,
        "session_file": os.path.join("sessions", "default.json"),
        "max_messages": 40,
        "max_context_items": 12,
    },
}

DEFAULT_SYSTEM_PROMPT = (    "你是 Study7Agent，一个运行在 Windows 7 离线内网办公机上的中文优先助手，"
    "这台机器通常是教室里的教学一体机或办公室电脑。\n"
    "\n"
    "工作方式：\n"
    "- 需要读取、修改本机文件或执行命令时，直接调用提供的工具，不要只在回答里描述你打算怎么做。\n"
    "- 你自己的工作区是 {{workspace}}：新建的文件默认放在这里，用相对路径（例如 课件\\第三课.pptx）"
    "就表示写在工作区内；用户没有指定位置时不要写到别的盘符或桌面。\n"
    "- 用户给出其他绝对路径时按用户的路径读写。\n"
    "- 修改文件前先用 read_file 确认真实内容，不要凭猜测覆盖；write_file 会自动保存可回滚快照。\n"
    "- 命令执行受策略限制：被拦截的前缀（blocked_prefixes）无法执行，不要绕道尝试；"
    "高风险命令默认直接执行，做事前先想清楚这条命令会改掉什么。\n"
    "- 多步任务先给出简短计划，然后连续执行工具直到完成，最后再用中文总结结果。\n"
    "\n"
    "修改自己（自我修改能力）：\n"
    "- 这套程序（WebUI、工具、技能、配置）就在你的安装目录里，你可以读也可以改："
    "先 read_file 看清现状，再用 edit_file 做小步替换，不要整文件重写。\n"
    "- 改完自己的 .py / .json / skills 之后**必须**调用 self_check 跑自检（不需要联网和密钥）；"
    "自检不过就按报错继续修，不要带着失败的自检往下走。\n"
    "- 自检通过后再用 restart_service 重启服务生效，reason 写清改了什么。\n"
    "- 只有两处不要碰：随包的 python\\ 运行目录，和 webui\\data（用户的对话记录）；"
    "改坏了程序会起不来或丢掉老师的历史。\n"
    "\n"
    "教学场景（常见需求）：\n"
    "- 课件用 write_file 写成 .pptx（大纲：# 标题 / ## 小标题 / - 要点 / | 表格 | / > 讲稿备注 / --- 分页）；\n"
    "  学校有统一模板时把模板路径填进 template 参数；生成后回读确认页数再交付。\n"
    "- 教案、通知、试卷写 .docx，成绩表写 .xlsx，都能直接用 write_file 生成。\n"
    "- 课件面向教室大屏：正文用大字号、每页字少、重点突出；讲解细节写进讲稿备注。\n"
    "- 用户没说清年级、学科、课题时先问一句，不要凭空编造教材内容。\n"
    "\n"
    "课堂互动（在一体机上上课时用）：\n"
    "- 抽人 / 分组用 pick_students，限时活动用 class_timer，讲评与出题用 show_on_screen 投大屏。\n"
    "- 投屏内容面向学生：大字、短句，不要出现对话语气和内部文件名；一次只投一屏。\n"
    "- 学生的错题记进 mistake_book，讲评后按知识点生成重练卷；成绩统计用 score_stats（代码精算，不要心算）。\n"
    "\n"
    "画图（数学函数 / 几何图形）：\n"
    "- **平面（2D）上的一切作图只准用 plane 工具**：函数 plot_function、方程 plot_implicit、"
    "圆 / 直线 / 交点 / 动点用 create_object（结构化）或 execute_command（GeoGebra 式命令），"
    "一次建一整组对象用 plane_batch。轨迹、滑动条也在 plane 里。\n"
    "- **画函数 / 方程不要写范围**：plot_function 和 plot_implicit 都不给 xmin / xmax，"
    "服务端会按老师当前视野自动延伸（往两边各多采两屏，缩放、平移时自动补）。"
    "只有老师明确说「只画 -2 到 3 这一段」才给范围 —— 自己随手写 -10 到 10 会画出"
    "一小截就断掉的线，老师会说「不会画无限函数」。\n"
    "- **平面上的东西不要再叫 show_figure**：它现在只剩立体（kind=solid），"
    "收到平面请求会返回结构化错误让你改用 plane；照着改就行，别反复试。\n"
    "- **不要**为了画数学图去打开希沃白板 / 几何画板软件，**也不要**用 draw_path 在屏幕上描：\n"
    "  draw_path 是给「老师已经打开了画板，让你在上面手绘」用的（老师明确要求时才行），慢而且会画歪；\n"
    "  plane 的坐标是本地算出来的，曲线和图形一定是对的，而且**父对象一动、依赖它的交点/中点/圆跟着动**\n"
    "  （这是老师要的联动，要挪一个点用 update_object 改它就行）。\n"
    "- **图一画出来就停下**：不要再截屏验收、不要再用别的工具去「检查」或「补充」，\n"
    "  用一两句话把图讲清楚就结束这一轮。画完还乱操作是明确禁止的。\n"
    "- 题目给了边长、角度、坐标就按它算，不要自己改数。\n"
    "- **立体（3D）用 show_figure(kind=\"solid\")**：题目里出现棱柱/棱锥/长方体/正方体/圆柱/圆锥/球/旋转体/空间/异面直线/二面角这些字眼时，必须画成空间图，画成平面图是错的。\n"
    "  写法：A(0,0,0) 定义空间点；prism A B C 3 三棱柱；pyramid A B C 3 三棱锥；box A B C D 2 长方体；圆柱 cylinder O 3 5；圆锥 cone O 3 5；球 sphere O 3（或 sphere O A）；circle3 A B C 过三点的圆；seg/face 连棱画面；\n"
    "  线线角 A B | C D、线面角 A B | C D E、面面角 A B C | D E F 算空间角（结果会给分数/根号形式的精确值）。\n"
    "\n"
    "识图（图片输入）：\n"
    "- 用户可以直接发图片（截图、相机拍的课本或学生作业、表格照片）；先如实描述图里看到的内容，再回答。\n"
    "- 需要看本机上的图片文件时调用 view_image；list_dir 里出现的 .jpg/.png 照片可以用它查看。\n"
    "- 图片看不清、被裁掉或没有对应内容时要说清楚，不要猜；看不清的地方可以请用户重拍或补拍局部。\n"
    "\n"
    "操作窗口与界面（Win7 自带，不用装东西）：\n"
    "- 先 snapshot，一次拿到「画面 + 元素清单 + 窗口表」：元素形如 [12] (640,380) 按钮 \"确定\"，\n"
    "  方括号里的编号就是 label；动手就用 click / type / scroll / move 的 label=编号。\n"
    "- label 每次 snapshot 都会重新编号：翻页、切窗口、弹对话框之后必须重新 snapshot，别拿旧编号硬试。\n"
    "- 也可以直接给 loc=[x, y]（屏幕物理像素，按最近一次截图算）：两次调用之间窗口被移动或关掉过，\n"
    "  系统会拒绝这个坐标并要求你重新截图（screenshot 或 snapshot 都行）——不要拿旧坐标硬点。\n"
    "- 做完动作要从**新状态**里验证：点下去不等于事情办成了，再 snapshot 一次确认结果\n"
    "  （动作返回的读回只是线索，不是结论）。\n"
    "- 元素太少或为空（Chromium/Qt/自绘界面常见）：如实说明，改用 screenshot 看画面 + click 的 loc 坐标，\n"
    "  不要对着同一个快照反复重试。\n"
    "- 翻页 / 往下看：用 scroll（先给 label=编号或 loc=[x, y] 指到那块能滚的区域，比如课件正文、网页中间；\n"
    "  wheel_times 一格大约三行，滚几格再看一眼）。别去拖滚动条：又细又容易拖飞。\n"
    "- 打字用 type（text=要输入的字；中文按 Unicode 逐个字符注入，不切输入法、不占剪贴板），\n"
    "  快捷键用 shortcut（ctrl+s、alt+tab、f5；多媒体键 play_pause / volume_up 这些也走它）。\n"
    "- 等窗口或对话框出现用 wait_for（window / gone / text / stable，比死等固定秒数靠谱）；\n"
    "  app 用来切前台、关闭窗口、改窗口大小或启动程序；clipboard 读写剪贴板里的文字。\n"
    "- 别的程序和你共用同一块桌面：两次调用之间窗口可能已经被移动、关闭或改过；操作失败先重新看一次现状。\n"
    "  取消或超时**不会**撤销已经发出去的操作，重试前先确认当前状态。\n"
    "\n"
    "联网搜索：\n"
    "- 需要最新资料、外部例子或校内没有的信息时用 web_search；关键词要具体，必要时加引号锁定短语。\n"
    "- 结果不相关就换关键词或换引擎（engine 参数）；需要正文时用 fetch_top 或 read_url，不要反复空搜。\n"
    "- 搜不到就如实说搜不到，绝不编造标题、链接或页码。\n"
    "\n"
    "屏幕与教室设备：\n"
    "- 悬浮工具栏常驻屏幕右侧，上面已经有现成按钮：希沃白板、展台、QQ、切到PPT、计时、\n"
    "  抽学号、截图给AI、板书保存、课堂标记、数学函数、几何画板、计算器、回到桌面、\n"
    "  打开网页、翻译查词、备忘录、日历、天气。需要这些效果时先想「工具栏上有没有」，\n"
    "  不要自己另造一套。\n"
    "\n"
    "读文件：\n"
    "- read_file 不只认常见文本后缀：不认识的扩展名会按内容判断，UTF-8 / GBK 的中文文件都能读。\n"
    "- 给到 .zip 时会**自动解压到工作区**并列出里面的文件，接着直接读解压出来的文件即可。\n"
    "- 音频只能给出格式信息和标题/艺术家等标签；离线环境**不能把语音转成文字**，\n"
    "  除非音频旁边有同名的 .srt / .vtt / .lrc / .txt 字幕文件——那就读它，\n"
    "  并且必须说明这是随附字幕、不是转写。绝对不要假装自己听懂了录音。\n"
    "\n"
    "产物：\n"
    "- 你写出来 / 下载 / 保存的文件会作为「本次产物」显示在对话里，老师可以直接点开或打开所在文件夹。\n"
    "- 所以产出文件后，在回答里用一句话说清楚**存到哪了、叫什么名字**，别让老师自己找。\n"
    "\n"
    "回答风格：\n"
    "- 用户用中文提问时用中文回答；结论先行，不写空话。\n"
    "- 使用 Markdown：小标题、列表、表格；代码与命令放在围栏代码块中。\n"
    "- 引用本机文件时写出 `路径:行号`；引用内网页面时写出页面标题与 URL。\n"
    "- 不确定的信息要说明不确定，不要编造文件内容、命令输出或外部链接。\n"
    "- 不要建议绕过企业邮件、杀毒、终端管控等安全措施。"
)

SUBAGENT_PROMPT = (
    "你是主代理派出的子代理，角色是：{{role}}。\n"
    "你只负责完成派给你的那一个子任务，完成后用中文给出简短结论（结论 + 关键依据 + 产出文件路径）。\n"
    "\n"
    "要求：\n"
    "- 直接动手做，不要反问主代理，也不要寒暄；信息不足时按最合理的假设继续，并在结论里说明假设。\n"
    "- 需要读写文件、看图片、执行命令时调用工具；路径用绝对路径，当前工作目录 {{workspace}}。\n"
    "- 结论控制在 200 字以内，不要复制大段原文，把细节留在生成的文件里。\n"
    "- 你不能再派子代理。\n"
)

WEBUI_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8770,
    # 单条消息最多注入几张图片（"把这一摞卷子照片都给它看"）：默认 256，可在设置里改。
    # 图片很吃 token，所以实际注入时还有总字节预算兜底，超了会如实说明。
    "max_images": 256,
    # 高风险命令（del / format / powershell / reg…）默认**不再逐条确认**，直接执行；
    # 想恢复"执行前先问我"就打开这一项。blocked_prefixes 的硬拦截不受它影响。
    "confirm_high_risk_commands": False,
    "open_browser": True,
    "access_token": "",
    "data_dir": "",
    "workspace_root": "",
    # 默认允许读写工作区之外的路径：办公场景下模型经常需要改 D 盘、桌面、共享盘上的文件。
    # 想收紧时在界面上关掉即可。
    "allow_outside_workspace": True,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "model": "",
    "endpoint": "",
    "temperature": 0.3,
    "max_tokens": 0,
    # 工具轮次上限：一笔一个轮次，画图/整理课件这类活很吃轮次，默认 1024
    # （可在设置界面改；真正兜底的是 max_run_seconds 与熔断）
    "max_tool_rounds": 1024,
    "tools_enabled": True,
    "reasoning_effort": "",
    # 已登记的工作区（侧栏按它分组）：[{"path": "D:\\课件", "title": "高一数学"}]，最近的在最前
    "workspaces": [],
    # 旧字段：早期版本只存路径列表，读取时会自动迁移成上面的 workspaces
    "recent_workspaces": [],
    # 交给模型的历史条数上限：这是"兜底的硬砍"，正常情况下由上下文自动压缩管理历史，
    # 所以默认放宽到 200（原来 40 会在长任务里悄悄丢掉较早的上下文）
    "trim_messages": 200,
    "context_max_items": 12,
    # 熔断：同样的动作重复这么多次就停下；单次运行最长秒数；单轮思考字符上限。
# 开关是 config 里的 loop_breaker（WebUI 设置里勾），**默认关闭**：
# 关掉时上限置 0 = 不做循环熔断，长任务不容易被误停。
    "loop_repeat_limit": 3,
    # 单次运行时长上限（秒）：0 = 不限制。长时间连续干活（画图、批量整理）默认不掐断，
    # 靠"自动压缩上下文 + 重复提醒 + 停止按钮"来管，不再用墙钟时间砍任务。
    "max_run_seconds": 0,
    # 上下文自动压缩（对照 DSH 的 compaction-basic）：压力到阈值的比例就压缩较早历史
    "auto_compact": True,
    "compact_threshold_ratio": 0.8,
    "max_round_reasoning_chars": 60000,
    # SSE 写超时：浏览器休眠/关页面后写不出去多久就放弃这次运行（秒）
    "sse_write_timeout": 30,
    # 思考模式："" 不改动接口参数；"enabled"/"disabled" 对应官方 thinking 参数
    "thinking": "",
    # 桌面操作（截屏、窗口、剪贴板、音量、鼠标键盘）与对外发消息
    # 默认都不弹确认：老师让 agent 干活时不希望每次点一次鼠标都要批一次。
    # 想收紧时在设置里把对应开关打开即可（两个开关互相独立）。
    "allow_desktop_control": True,
    "confirm_desktop_input": False,
    "confirm_messages": False,
    # 自我修改闭环：允许 agent 用 restart_service 重启自己（改完源码/配置后生效）。
    # 关掉以后 restart_service 只会给出"请手动重启"的说明，不会真的重启。
    "allow_self_restart": True,
    # 消息通知（QQ 邮箱 SMTP / webhook）
    "notify": {
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_user": "",
        "smtp_auth_code": "",
        "smtp_ssl": True,
        "default_to": "",
        "webhook_url": "",
        "webhook_kind": "auto",
    },
    # 子代理
    "max_subagents": 4,
    "subagent_max_rounds": 256,
    # 联网搜索
    "search": {
        "engine": "auto",
        "endpoint": "",
        "api_key": "",
        "max_results": 5,
        "timeout": 20,
    },
    "theme": "dark",
    # 开机自启：默认开。单文件版写 HKCU\...\Run（指向 exe 自己），在设置界面能关。
    # 目录版/开发环境没有 Study7Agent.exe，写了也没意义，会在界面上说明。
    "autostart": True,
    # 被老师**删掉过**的技能名：技能装在程序目录里，升级会整个换掉，
    # 不记下来的话删了也会"复原"（老师专门提过）。存在 config\webui.json，升级不会丢。
    "deleted_skills": [],
    "price": {
        "currency": "USD",
        "unit": 1000000,
        "cache_hit": 0.028,
        "cache_miss": 0.28,
        "output": 0.42,
    },
}


def _merge(base, override):
    """递归合并：override 覆盖 base，不修改入参。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_json(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("无法读取配置文件 %s：%s" % (path, exc))
    if not raw.strip():
        return None
    # 记事本"另存为 Unicode"会写成 UTF-16；扩展名判断不了，只能看 BOM
    text = decode_text(raw)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("配置文件格式错误 %s: %s（请检查是否少了引号、逗号或多写了注释）"
                         % (path, exc))


def _write_json(path, data):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def decode_bytes(data):
    """按中文环境的常见顺序解码文本，返回 (文本, 编码名)。

    老师电脑上的文件可能是记事本存的 “Unicode”(UTF-16)、WPS 导出的 GBK、
    或 VSCode 的 UTF-8，还可能是被截断的（读大文件只读前几 MB）。
    所以先看 BOM，再严格试 UTF-8 / GB18030；都不行时两种容错解码各来一次，
    选坏字符少的那个，避免一个坏字节把整段中文变成问号。
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data.startswith(b"\xff\xfe\x00\x00"):
        return data[4:].decode("utf-32-le", "replace"), "utf-32-le"
    if data.startswith(b"\x00\x00\xfe\xff"):
        return data[4:].decode("utf-32-be", "replace"), "utf-32-be"
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", "replace"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "replace"), "utf-16-be"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # 截断的 UTF-8 常常也能被 GB18030 解出来（GBK 几乎接受任何字节），直接交给它会整段变乱码。
    # 所以先看 UTF-8 容错解码的坏字符：只坏一两处 = 典型的“读到一半截断”，认 UTF-8。
    utf8 = data.decode("utf-8", "replace")
    bad_utf8 = utf8.count("\ufffd")
    if bad_utf8 <= 2 and len(utf8) > 64:
        return utf8, "utf-8(replace)"
    try:
        return data.decode("gb18030"), "gb18030"
    except UnicodeDecodeError:
        pass
    gb = data.decode("gb18030", "replace")
    if gb.count("\ufffd") < bad_utf8:
        return gb, "gb18030(replace)"
    return utf8, "utf-8(replace)"


def decode_text(data):
    """只要文本，不要编码名（tools / classroom 都用这一个，避免两套解码规则）。"""
    return decode_bytes(data)[0]


# 老版本随包发出去、后来被改掉的默认值。
#
# 为什么必须迁移：设置界面"保存"时会把**所有**字段一起写进 config\webui.json，
# 于是老师哪怕只是改了个模型名，也会把当时的默认值（8 轮、900 秒）一起焊死在配置里。
# 后来我们把默认改成 1024 轮 / 不限制时长，**对这些机器完全不起作用**——
# 表现就是"干一会儿就停""动不动就熔断"，而且怎么改代码都复现不了。
# 只在"存着的值正好等于老默认值"时才升级（那基本等于"从没动过"）。
LEGACY_WEBUI_DEFAULTS = {
    "max_tool_rounds": ((8, 40), 1024),
    "trim_messages": ((40,), 200),
    "max_run_seconds": ((900,), 0),
    "max_images": ((6,), 256),
}


def migrate_webui(raw):
    """把老配置里"其实是旧默认值"的字段升级成新默认值，返回 (新配置, [(键, 旧, 新)])。"""
    if not isinstance(raw, dict):
        return {}, []
    data = dict(raw)
    migrated = []
    for key, (old_values, new) in LEGACY_WEBUI_DEFAULTS.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, bool) or value is None:
            continue
        for old in old_values:
            try:
                same = float(value) == float(old)
            except (TypeError, ValueError):
                same = str(value).strip() == str(old)
            if same:
                data[key] = new
                migrated.append((key, old, new))
                break
    return data, migrated


#: 开机自启：写 HKCU 的 Run 键（不需要管理员），值名固定，指向 exe 自己。
#: 注意写进去的是**带引号的命令行**（见 autostart_command）：老师那台机器的程序装在
#: 带空格的目录里，裸路径写进 Run 键之后 Windows 只把空格前那一段当程序名，开机就静默失败。
AUTOSTART_VALUE = "Study7Agent"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
#: msconfig / 任务管理器「启动」页把这一项禁用时会在这里留一条二进制值：
#: REG_BINARY 首字节 & 1 == 1 就是"已禁用"（诊断里会翻译成中文）
AUTOSTART_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
#: 启动文件夹里的兜底快捷方式：注册表那条被还原卡/管家清掉时，靠它还能起来
AUTOSTART_LNK_NAME = "Study7Agent.lnk"
#: 开机自启拉起来的那一份要安静：只显示悬浮栏，不开界面窗口、不抢前台
AUTOSTART_ARG = "--autostart"


def autostart_exe(base_dir, exe_path=""):
    """开机自启该指向哪个程序。

    单文件发布版的 exe 名字带着版本号（study7agent-1.1.0-beta11.exe），**不一定**叫
    Study7Agent.exe，所以启动器会把正在跑的 exe 完整路径用 --exe-path 传进来，优先用它；
    只有目录版 / 老外壳版才退回 base_dir 下的 Study7Agent.exe。
    两者都没有 → 返回空串，界面上就说"这一版不用设"。
    """
    candidate = str(exe_path or "").strip()
    if candidate:
        try:
            candidate = os.path.abspath(candidate)
        except (OSError, ValueError):
            candidate = ""
        if candidate and candidate.lower().endswith(".exe") and os.path.isfile(candidate):
            return candidate
    path = os.path.join(base_dir, "Study7Agent.exe")
    return path if os.path.isfile(path) else ""


def autostart_command(exe):
    """注册表/快捷方式里该写的那一条命令行（路径**必须带引号**）。

    老师那台机器把程序装在带空格的目录里（桌面上「开学第一课」那种）。裸路径写进 Run 键
    之后，Windows 只把空格前面那一段当程序名，开机时一声不响地什么都不发生 ——
    "没法开机自启"最常见的就是这个原因。
    后面再带一个 --autostart，让启动器知道这一份是开机拉起来的（只显示悬浮栏）。
    """
    if not exe:
        return ""
    return '"%s" %s' % (os.path.abspath(exe), AUTOSTART_ARG)


def _autostart_target(command):
    """从一条启动命令行里抠出 exe 路径（带不带引号、带不带 --autostart 都认）。

    老版本写进去的是裸路径（没有引号），必须能认出来；否则每次启动都会当成"指向别处"
    再重写一遍，日志里天天刷。
    """
    text = str(command or "").strip()
    if not text:
        return ""
    if text[:1] == '"':
        end = text.find('"', 1)
        return (text[1:end] if end > 0 else text[1:]).strip()
    if text.lower().endswith(" " + AUTOSTART_ARG):
        text = text[:-len(AUTOSTART_ARG)].strip()
    return text.strip('"')


def _autostart_same(command, exe):
    """启动项里写的到底是不是这个 exe（不管有没有引号、有没有 --autostart）。"""
    if not command or not exe:
        return False
    return (os.path.normcase(_autostart_target(command))
            == os.path.normcase(os.path.abspath(exe)))


def _autostart_uptodate(command, exe):
    """注册表里那一条是不是**现在的写法**（带引号 + --autostart），不只是"指向同一个程序"。

    老版本写的是裸路径：路径一带空格就开不起来（Windows 只认到空格前那一段）。
    所以"指向的是本程序"还不够，写法旧了也要改写——这就是换版本之后的自愈。
    """
    if not _autostart_same(command, exe):
        return False
    return str(command or "").strip().lower().endswith(AUTOSTART_ARG)


def startup_folder():
    """当前用户的「启动」文件夹（开始菜单 → 程序 → 启动）。"""
    if os.name != "nt":
        return ""
    appdata = os.environ.get("APPDATA") or ""
    if not appdata:
        return ""
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def autostart_shortcut():
    """启动文件夹里那个兜底快捷方式的完整路径（没有启动文件夹就是空串）。"""
    folder = startup_folder()
    return os.path.join(folder, AUTOSTART_LNK_NAME) if folder else ""


def _shortcut_matches(path, exe):
    """.lnk 是不是指向这个 exe（只读字节，不起 PowerShell）。

    /api/state 每次都会问一遍开机自启状态，那里绝不能去起进程，所以这里靠字节判断：
    WScript.Shell 存的是绝对路径，Unicode 那一份在文件里是 UTF-16LE；万一系统只写了
    ANSI（GBK）那一份，就退一步按文件名认（名字一样、又就在这个文件夹里，够用了）。
    """
    if not path or not exe or not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return False
    if os.path.abspath(exe).encode("utf-16-le") in raw:
        return True
    try:
        name = os.path.basename(exe).encode("mbcs")
    except (LookupError, UnicodeError):
        return False
    return name in raw


def _powershell_exe():
    """系统自带的 powershell.exe（Win7 上是 2.0，建快捷方式足够）。"""
    root = os.environ.get("SystemRoot") or "C:\\Windows"
    path = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return path if os.path.isfile(path) else "powershell.exe"


#: 用 WScript.Shell 建快捷方式。路径/参数全走环境变量：命令行里一个中文、一个引号都不出现，
#: 免得中文路径在「Python -> 命令行 -> PowerShell」这一路上被当成 GBK 弄坏。
_LNK_WRITE_SCRIPT = (
    "$ErrorActionPreference = 'Stop';"
    "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { };"
    "$shell = New-Object -ComObject WScript.Shell;"
    "$link = $shell.CreateShortcut($env:W7A_LNK_PATH);"
    "$link.TargetPath = $env:W7A_LNK_TARGET;"
    "$link.Arguments = $env:W7A_LNK_ARGS;"
    "$link.WorkingDirectory = $env:W7A_LNK_DIR;"
    "$link.WindowStyle = 7;"
    "$link.Description = $env:W7A_LNK_DESC;"
    "$link.Save();"
    "Write-Output 'OK'"
)

_LNK_READ_SCRIPT = (
    "$ErrorActionPreference = 'Stop';"
    "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { };"
    "$shell = New-Object -ComObject WScript.Shell;"
    "$link = $shell.CreateShortcut($env:W7A_LNK_PATH);"
    "Write-Output ('TARGET=' + $link.TargetPath);"
    "Write-Output ('ARGS=' + $link.Arguments);"
    "Write-Output ('DIR=' + $link.WorkingDirectory)"
)


def _run_powershell(script, extra_env):
    """跑一小段 PowerShell（无窗口、参数走环境变量）。返回 (ok, 输出或中文错误)。"""
    if os.name != "nt":
        return False, "只有 Windows 上才有这个操作"
    import subprocess                                        # noqa: PLC0415
    environment = dict(os.environ)
    environment.update(extra_env)
    environment["PYTHONIOENCODING"] = "utf-8"
    command = [_powershell_exe(), "-NoProfile", "-NonInteractive",
               "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
               "-Command", script]
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env=environment,
            creationflags=0x08000000)                        # CREATE_NO_WINDOW：不闪黑框
    except OSError as exc:
        return False, "起不了 PowerShell：%s" % exc
    try:
        output = process.communicate(timeout=25)[0]
    except Exception:                                        # 超时 / 被杀
        try:
            process.kill()
        except OSError:
            pass
        return False, "PowerShell 25 秒没返回（被拦住或卡住了）"
    text = (output or b"").decode("utf-8", "replace").strip()
    if process.returncode != 0:
        return False, "PowerShell 报错：%s" % (text or ("退出码 %s" % process.returncode))
    return True, text


def write_autostart_shortcut(exe, path=None):
    """在启动文件夹里建（或改写）那个兜底快捷方式。返回 (ok, 中文说明)。"""
    if os.name != "nt":
        return False, "只有 Windows 上才有这个操作"
    target = path or autostart_shortcut()
    if not target:
        return False, "找不到当前用户的启动文件夹（环境变量 APPDATA 没设）"
    folder = os.path.dirname(target)
    try:
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
    except OSError as exc:
        return False, "建不了启动文件夹：%s" % exc
    absolute = os.path.abspath(exe)
    ok, output = _run_powershell(_LNK_WRITE_SCRIPT, {
        "W7A_LNK_PATH": target,
        "W7A_LNK_TARGET": absolute,
        "W7A_LNK_ARGS": AUTOSTART_ARG,
        "W7A_LNK_DIR": os.path.dirname(absolute),
        "W7A_LNK_DESC": "Study7Agent 开机自启（只显示悬浮栏）",
    })
    if not ok:
        return False, "建启动文件夹快捷方式失败：%s" % output
    if not _shortcut_matches(target, exe):
        return False, "快捷方式写完了但回读不对（可能被杀毒/还原软件拦住了）：%s" % target
    return True, "启动文件夹的快捷方式已写好：%s" % target


def read_autostart_shortcut(path=None):
    """读回启动文件夹里的快捷方式写的是什么（诊断用；读不到就返回空字段）。"""
    target = path or autostart_shortcut()
    info = {"path": target, "exists": bool(target) and os.path.isfile(target),
            "target": "", "arguments": "", "working_dir": "", "error": ""}
    if not info["exists"]:
        return info
    ok, output = _run_powershell(_LNK_READ_SCRIPT, {"W7A_LNK_PATH": target})
    if not ok:
        info["error"] = output
        return info
    for line in output.splitlines():
        name, _sep, value = line.partition("=")
        if name == "TARGET":
            info["target"] = value
        elif name == "ARGS":
            info["arguments"] = value
        elif name == "DIR":
            info["working_dir"] = value
    return info


def _remove_autostart_shortcut(path=None):
    """删掉启动文件夹里的快捷方式。返回 (ok, 中文说明)。"""
    target = path or autostart_shortcut()
    if not target or not os.path.isfile(target):
        return True, ""
    try:
        os.remove(target)
    except OSError as exc:
        return False, "删不掉启动文件夹里的快捷方式：%s" % exc
    return True, ""


def _startup_approved_disabled(key_path=AUTOSTART_APPROVED_KEY):
    """msconfig / 任务管理器有没有把这一项禁用掉。返回 (查得到吗, 被禁用吗, 原始字节)。"""
    if os.name != "nt":
        return False, False, b""
    try:
        import winreg                                       # noqa: PLC0415 - 非 Windows 上不该 import
    except ImportError:
        return False, False, b""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
    except OSError:
        return False, False, b""
    try:
        raw, _kind = winreg.QueryValueEx(key, AUTOSTART_VALUE)
    except OSError:
        return False, False, b""
    finally:
        winreg.CloseKey(key)
    data = bytes(bytearray(raw)) if isinstance(raw, (bytes, bytearray)) else b""
    return True, bool(data) and (data[0] & 1) == 1, data


def _clear_startup_approved(key_path=AUTOSTART_APPROVED_KEY):
    """清掉 msconfig / 任务管理器留下的「已禁用」记录（不清掉的话，启动项写了也不跑）。"""
    if os.name != "nt":
        return False, "只有 Windows 上才有这个操作"
    try:
        import winreg                                       # noqa: PLC0415
    except ImportError:
        return False, "这个 Python 里没有 winreg（不是 Windows 版）"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
    except OSError:
        return True, ""                                     # 没有这个键 = 从来没被禁用过
    try:
        winreg.DeleteValue(key, AUTOSTART_VALUE)
    except OSError:
        pass                                                # 本来就没有
    finally:
        winreg.CloseKey(key)
    return True, "已清掉任务管理器/msconfig 里的禁用记录"


def _run_registry_write(value, key_path=AUTOSTART_KEY):
    """写/删 Run 键。value 为空 = 删掉。返回 (ok, 说明)。

    key_path 可以换（自检用临时键，免得去动老师机器上真的启动项）。
    """
    if os.name != "nt":
        return False, "只有 Windows 上才有开机自启"
    try:
        import winreg                                     # noqa: PLC0415 - 非 Windows 上不该 import
    except ImportError:
        return False, "这个 Python 里没有 winreg（不是 Windows 版）"
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                 winreg.KEY_SET_VALUE)
    except OSError as exc:
        return False, "打不开注册表启动项：%s" % exc
    try:
        if value:
            winreg.SetValueEx(key, AUTOSTART_VALUE, 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_VALUE)
            except FileNotFoundError:
                pass                                      # 本来就没有，等于已经关掉了
    except OSError as exc:
        return False, "写注册表失败：%s" % exc
    finally:
        winreg.CloseKey(key)
    return True, "开机自启已开启" if value else "开机自启已关闭"


def _run_registry_read(key_path=AUTOSTART_KEY):
    """注册表里现在写的是什么（没有就是空串）。key_path 可换（自检用临时键）。"""
    if os.name != "nt":
        return ""
    try:
        import winreg                                     # noqa: PLC0415
    except ImportError:
        return ""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
    except OSError:
        return ""
    try:
        value, _kind = winreg.QueryValueEx(key, AUTOSTART_VALUE)
        return str(value or "")
    except OSError:
        return ""
    finally:
        winreg.CloseKey(key)


def autostart_state(base_dir, key_path=AUTOSTART_KEY, lnk_path=None, exe_path=""):
    """给界面用：(能不能设, 现在开着吗, 一句说明)。

    "开着"以**注册表和启动文件夹里实际写了什么**为准（那才是真正决定开机跑不跑的东西），
    配置里的 autostart 只表示"老师希望它开着"——两者不一致时，启动时会自己补齐。
    exe_path 是启动器传来的真实 exe 路径（单文件版必须用它，见 autostart_exe）。
    """
    if os.name != "nt":
        return False, False, "开机自启只在 Windows 上有效"
    exe = autostart_exe(base_dir, exe_path)
    if not exe:
        return False, False, "没找到本程序的 exe（目录版/开发环境），这一版不用设开机自启"
    run_ok = _autostart_same(_run_registry_read(key_path), exe)
    lnk_ok = _shortcut_matches(lnk_path or autostart_shortcut(), exe)
    if run_ok and lnk_ok:
        return True, True, "开机自启已开启（注册表和启动文件夹两处都设好了）"
    if run_ok:
        return True, True, "注册表那条启动项是好的；启动文件夹里的兜底快捷方式没有，点「开机自启动诊断」可以补齐"
    if lnk_ok:
        return True, True, "只有启动文件夹里的快捷方式；注册表那条没写上，点「开机自启动诊断」修一下更稳"
    return True, False, ("开机自启没设上：注册表和启动文件夹里都没有指向本程序的启动项，"
                         "点「开机自启动诊断」可以一键修复")


def set_autostart(base_dir, enabled, key_path=AUTOSTART_KEY, lnk_path=None, exe_path=""):
    """界面上的开关：注册表 + 启动文件夹一起写 / 一起删，写完回读验证。

    返回 (ok, 说明, 现在真开着吗)。key_path / lnk_path 可换（自检用临时位置，
    绝不去动老师机器上真的启动项）；exe_path 是启动器传来的真实 exe 路径。
    """
    exe = autostart_exe(base_dir, exe_path)
    if not exe:
        return False, "没找到本程序的 exe（目录版/开发环境），这一版不用设开机自启", False
    problems = []
    if enabled:
        ok, message = _run_registry_write(autostart_command(exe), key_path)
        if not ok:
            problems.append(message)
        elif not _autostart_same(_run_registry_read(key_path), exe):
            problems.append("注册表写进去了但读回来不对（可能被杀毒软件拦了）")
        ok, message = write_autostart_shortcut(exe, lnk_path)
        if not ok:
            problems.append(message)
        state = autostart_state(base_dir, key_path, lnk_path, exe_path)[1]
        if not state:
            return False, "开机自启没设上：%s" % "；".join(problems), False
        if problems:
            # 有一处能用就算开了，但没成的那处必须说清楚，不能糊弄过去
            return True, "开机自启已开启，但有一处没成：%s" % "；".join(problems), True
        return True, "开机自启已开启（注册表 + 启动文件夹都写好了）", True
    ok, message = _run_registry_write("", key_path)
    if not ok:
        problems.append(message)
    ok, message = _remove_autostart_shortcut(lnk_path)
    if not ok:
        problems.append(message)
    state = autostart_state(base_dir, key_path, lnk_path, exe_path)[1]
    if state:
        return False, "开机自启没关掉：%s" % ("；".join(problems) or "启动项还在"), True
    return True, "开机自启已关闭", False


def ensure_autostart(settings, exe_path=""):
    """启动时对齐一次：配置里说开着，就把注册表和启动文件夹都对齐到当前程序位置。

    只补"该开没开"，绝不在老师关掉之后偷偷开回来——关掉会把配置也写成 False。
    程序被搬到别的目录、或者换了新版本之后，旧值指向老路径，这里顺手改过来。
    exe_path 是启动器传来的真实 exe 路径（单文件版换了文件名也能对上）。
    一切正常时返回空串（启动日志里就不会每开一次都刷一行）。
    """
    if not settings.webui.get("autostart"):
        return ""
    exe = autostart_exe(settings.base_dir, exe_path)
    if not exe:
        return ""
    done = []
    failed = []
    current = _run_registry_read()
    if not _autostart_uptodate(current, exe):
        ok, message = _run_registry_write(autostart_command(exe))
        if ok:
            if not current:
                why = "原来没有"
            elif not _autostart_same(current, exe):
                why = "原来指向 %s" % current
            else:
                why = "原来是老写法（%s：裸路径没加引号、也没有 %s）" % (current, AUTOSTART_ARG)
            done.append("注册表启动项%s，已改成指向当前程序位置并带上引号" % why)
        else:
            failed.append("注册表没写成：%s" % message)
    shortcut = autostart_shortcut()
    if not _shortcut_matches(shortcut, exe):
        ok, message = write_autostart_shortcut(exe)
        if ok:
            done.append("启动文件夹里补上了快捷方式：%s" % shortcut)
        else:
            failed.append(message)
    if not done and not failed:
        return ""
    return "；".join(done + failed)


def _current_user_is_admin():
    """当前进程有没有管理员权限（诊断里要告诉老师"换账号登录不生效"这件事）。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


#: 会把启动项清掉或拦住的常见还原/管控/安全软件（只看安装目录名，粗查，不追求齐）
_AUTOSTART_GUARDS = (
    ("冰点还原 Deep Freeze", ("faronics", "deep freeze")),
    ("希沃管家", ("seewo",)),
    ("360 安全卫士", ("360safe",)),
    ("QQ 电脑管家", ("qqpcmgr",)),
)


def _autostart_guards():
    """粗查这台机器上有没有"会清掉启动项"的软件（只扫 Program Files 下的目录名）。"""
    found = []
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            names = [name.lower() for name in os.listdir(root)]
        except OSError:
            continue
        for title, keys in _AUTOSTART_GUARDS:
            if title in found:
                continue
            for key in keys:
                if any(key in name for name in names):
                    found.append(title)
                    break
    return found


def _last_autostart_log_line(base_dir):
    """日志里最后一次「开机自启」的记录（回答老师"到底有没有生效"这个问题）。"""
    path = os.path.join(base_dir, "logs", "exe.log")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 60000))
            raw = handle.read()
    except OSError:
        return ""
    found = ""
    for line in raw.decode("utf-8", "replace").splitlines():
        if "开机自启" in line:
            found = line.strip()
    return found


def autostart_diagnose(base_dir, key_path=AUTOSTART_KEY, lnk_path=None,
                       approved_key=AUTOSTART_APPROVED_KEY, exe_path=""):
    """开机自启体检：给老师一份逐条中文结论（界面上的「开机自启动诊断」就是它）。

    每条都是**这台机器上现在的事实**，不猜：程序在哪、路径有没有空格、注册表里写的是
    什么、启动文件夹里有没有快捷方式、msconfig 有没有禁用、是不是管理员、有没有还原卡。
    返回 {"ok": 现在开机真的会起来吗, "usable": 能不能设, "exe": 路径,
          "items": [{"level": "ok|warn|bad|info", "title": 中文, "detail": 中文}],
          "summary": 一句话结论, "fixable": 要不要点一键修复}
    """
    items = []

    def add(level, title, detail):
        items.append({"level": level, "title": title, "detail": detail})

    exe = autostart_exe(base_dir, exe_path)
    report = {"ok": False, "usable": os.name == "nt", "exe": exe, "items": items,
              "summary": "", "fixable": False}
    if os.name != "nt":
        add("bad", "当前系统", "开机自启只在 Windows 上有效；这台机器（或这个环境）不是 Windows。")
        report["summary"] = "不是 Windows，不需要设置开机自启。"
        return report
    if not exe:
        add("warn", "程序位置",
            "没找到 %s，也没收到启动器传来的 --exe-path。说明现在跑的是目录版 / 源码树"
            "（不是单文件 exe），这一版没有 exe，开机自启无从设起；用单文件版时再设。" %
            os.path.join(base_dir, "Study7Agent.exe"))
        report["summary"] = ("当前不是单文件 exe（没找到 Study7Agent.exe，也没有 --exe-path），"
                             "这一版不用设开机自启。")
        return report
    add("ok", "程序位置", "程序在：%s" % exe)
    if " " in exe:
        add("warn", "路径里有空格",
            "程序装在带空格的目录里。启动项**必须给路径加引号**，否则 Windows 只把空格前面"
            "那一段当成程序名，开机时一声不响地什么都不发生 —— 这正是「没法开机自启」最常见"
            "的原因。这个版本的写法已经自动带引号了，老版本留下的裸路径会被自动改写。")
    else:
        add("ok", "路径里没有空格", "路径里没有空格，不存在「被空格截断」的问题。")
    current = _run_registry_read(key_path)
    shortcut = lnk_path or autostart_shortcut()
    if not current:
        add("bad", "注册表启动项", "没有这一项（HKCU\\%s 下的 %s）。" % (key_path, AUTOSTART_VALUE))
    else:
        detail = "现在写的是：%s" % current
        if _autostart_uptodate(current, exe):
            add("ok", "注册表启动项", detail + "（指向本程序，带引号）")
        elif _autostart_same(current, exe):
            add("warn", "注册表启动项",
                detail + "（指向本程序，但这是老写法：没给路径加引号 / 少了 %s。"
                "路径一有空格，开机就一声不响地失败。点一键修复会改写成带引号的命令行）" %
                AUTOSTART_ARG)
        else:
            add("bad", "注册表启动项",
                detail + "（不指向本程序现在所在的位置，可能程序被搬过家）")
    info = read_autostart_shortcut(shortcut)
    if not info["exists"]:
        add("warn", "启动文件夹快捷方式",
            "没有 %s。注册表那条被还原卡 / 管家清掉时就没有兜底了，点一键修复会建一个。" % shortcut)
    elif _shortcut_matches(shortcut, exe):
        add("ok", "启动文件夹快捷方式", "有：%s%s" % (
            shortcut, ("，指向 %s" % info["target"]) if info["target"] else ""))
    else:
        add("bad", "启动文件夹快捷方式", "有 %s，但指向的不是本程序（%s）。点一键修复会改写。" % (
            shortcut, info["target"] or "读不出目标"))
    known, disabled, _raw = _startup_approved_disabled(approved_key)
    if not known:
        add("info", "任务管理器 / msconfig 的禁用状态", "注册表里没有禁用记录，通常就等于没被禁用。")
    elif disabled:
        add("bad", "被任务管理器 / msconfig 禁用了",
            "「任务管理器 → 启动」或 msconfig 里这一项被禁用过，禁用了就算写了启动项也不会跑。"
            "点一键修复会重新写一遍并清掉这条禁用记录。")
    else:
        add("ok", "任务管理器 / msconfig 的禁用状态", "没被禁用。")
    user = os.environ.get("USERNAME") or "（读不到）"
    if _current_user_is_admin():
        add("info", "当前用户", "当前登录用户：%s（管理员权限）。" % user)
    else:
        add("warn", "当前用户",
            "当前登录用户：%s（不是管理员）。开机自启写在**当前用户**的注册表里（HKCU），"
            "所以用哪个账号登录就只对哪个账号生效：如果程序当初是用管理员账号装的、老师平时"
            "用另一个账号登录，那边不会生效。给这台机器设的时候，请用老师平时登录的那个账号"
            "点一次「一键修复」。" % user)
    guards = _autostart_guards()
    if guards:
        add("warn", "可能清掉启动项的软件",
            "检测到：%s。这类还原 / 管控 / 安全软件经常把启动项清掉或拦住。如果一键修复之后"
            "重启还是不启动，请让管理员把 %s 加入白名单，或者把程序装到不受还原的分区"
            "（比如 D 盘）。" % ("、".join(guards), exe))
    else:
        add("info", "还原 / 管控软件",
            "没有在常见安装目录里看到还原卡、希沃管家、360 这类软件（只是粗查，没看到不代表没有）。")
    last = _last_autostart_log_line(base_dir)
    if last:
        add("info", "上一次开机自启的日志", last)
    else:
        add("info", "上一次开机自启的日志",
            "日志里还没有「开机自启」的记录：可能是这个版本还没走过开机自启那条路，或者还没重启过。"
            "设好之后重启一次，这里会出现时间。")
    # 裸路径只有在**不含空格**时 Windows 才能正确解析；带空格或没引号的写法开机必然失败，
    # 所以「写进去了」不等于「会生效」，这里分开算。
    run_same = _autostart_same(current, exe)
    run_uptodate = _autostart_uptodate(current, exe)
    run_bootable = run_uptodate or (run_same and " " not in exe)
    lnk_ok = _shortcut_matches(shortcut, exe)
    will_boot = (run_bootable or lnk_ok) and not disabled
    report["ok"] = will_boot
    report["fixable"] = (not will_boot or not (run_bootable and lnk_ok) or not run_uptodate)
    if will_boot:
        report["summary"] = "开机自启现在是生效的（%s）。开机后如果没看到程序，点悬浮栏的「显示界面」。" % (
            "注册表和启动文件夹都有" if run_bootable and lnk_ok else
            ("注册表那条在" if run_bootable else "只有启动文件夹的快捷方式"))
        if current and not run_uptodate:
            report["summary"] += "不过注册表那条还是老写法，点一次「一键修复」会更稳妥。"
    elif disabled:
        report["summary"] = "开机自启现在不会生效：被任务管理器 / msconfig 禁用了。点「一键修复」清掉。"
    else:
        report["summary"] = ("开机自启现在不会生效：注册表和启动文件夹里都没有指向本程序的启动项。"
                             "点「一键修复」就能补上。")
    return report


def repair_autostart(base_dir, key_path=AUTOSTART_KEY, lnk_path=None,
                     approved_key=AUTOSTART_APPROVED_KEY, exe_path=""):
    """一键修复：注册表 + 启动文件夹都写好，清掉 msconfig 的禁用记录，再回读验证。

    返回 (ok, 中文说明)。自检时 key_path / lnk_path / approved_key 都换成临时位置；
    exe_path 是启动器传来的真实 exe 路径（单文件版不叫 Study7Agent.exe）。
    """
    exe = autostart_exe(base_dir, exe_path)
    if os.name != "nt":
        return False, "只有 Windows 上才有这个操作"
    if not exe:
        return False, "没找到本程序的 exe（目录版/开发环境），这一版不用设开机自启"
    steps = []
    run_ok, message = _run_registry_write(autostart_command(exe), key_path)
    steps.append(message)
    if run_ok and not _autostart_same(_run_registry_read(key_path), exe):
        run_ok = False
        steps.append("注册表回读不一致（可能被杀毒软件拦了）")
    lnk_ok, lnk_message = write_autostart_shortcut(exe, lnk_path)
    steps.append(lnk_message)
    cleared, cleared_message = _clear_startup_approved(approved_key)
    if cleared_message:
        steps.append(cleared_message)
    if not cleared:
        steps.append("清不掉禁用记录：%s" % cleared_message)
    state = autostart_state(base_dir, key_path, lnk_path, exe_path)
    ok = bool(run_ok or lnk_ok) and state[1]
    return ok, "；".join(steps) + "。" + state[2]


class Settings(object):
    """agent.json（CLI 共用）+ webui.json（WebUI 专属）的合并视图。"""
    def __init__(self, base_dir, exe_path=""):
        self.base_dir = base_dir
        # 启动器传来的真实 exe 路径（单文件版文件名带版本号，开机自启必须指向它）
        self.exe_path = exe_path or ""
        self.agent_path = os.path.join(base_dir, "config", "agent.json")
        self.webui_path = os.path.join(base_dir, "config", "webui.json")
        self.reload()

    def reload(self):
        self.agent = _merge(AGENT_DEFAULTS, _read_json(self.agent_path) or {})
        raw = _read_json(self.webui_path) or {}
        raw, migrated = migrate_webui(raw)
        self.webui = _merge(WEBUI_DEFAULTS, raw)
        self.migrations = migrated
        if migrated:
            # 迁移结果写回磁盘：否则下次打开设置界面又会把旧值带回来
            try:
                _write_json(self.webui_path, self.webui)
            except OSError:
                pass
            try:
                log_dir = os.path.join(self.base_dir, "logs")
                if not os.path.isdir(log_dir):
                    os.makedirs(log_dir)
                with open(os.path.join(log_dir, "config-migration.log"), "a",
                          encoding="utf-8") as handle:
                    handle.write("%s 配置迁移：%s\n"
                                 % (_now(), "；".join("%s: %s → %s" % item for item in migrated)))
            except OSError:
                pass
        return self

    def save_webui(self, patch):
        self.webui = _merge(self.webui, patch or {})
        _write_json(self.webui_path, self.webui)
        return self.webui

    # -- 被删掉的技能 ------------------------------------------------------
    def deleted_skills(self):
        """老师删过的技能名（小写比较用原名）。技能在程序目录里，升级会换掉整棵树，
        不记住的话删掉的下次升级又回来了（老师专门提过这件事）。"""
        raw = self.webui.get("deleted_skills")
        out = []
        for item in (raw if isinstance(raw, list) else []):
            name = str(item or "").strip()
            if name and name not in out:
                out.append(name)
        return out

    def mark_skill_deleted(self, name):
        name = str(name or "").strip()
        if not name:
            return self.deleted_skills()
        names = self.deleted_skills()
        if name not in names:
            names.append(name)
            self.save_webui({"deleted_skills": names})
        return names

    def unmark_skill_deleted(self, name):
        """老师又自己建了同名技能时，把"删过"这条记录抹掉（不然新技能会被自己挡住）。"""
        name = str(name or "").strip()
        names = [item for item in self.deleted_skills() if item != name]
        if len(names) != len(self.deleted_skills()):
            self.save_webui({"deleted_skills": names})
        return names

    def workspaces(self):
        """已登记的工作区列表（侧栏就是按它分组的），统一成 [{"path", "title"}]。

        兼容早期只写路径列表的 recent_workspaces：读的时候自动迁移。
        """
        raw = self.webui.get("workspaces")
        if not isinstance(raw, list):
            raw = [{"path": item} for item in (self.webui.get("recent_workspaces") or [])]
        return self._clean_workspaces(raw)

    def save_workspaces(self, items):
        return self.save_webui({"workspaces": self._clean_workspaces(items)})

    @staticmethod
    def _clean_workspaces(items):
        keep = []
        seen = set()
        for entry in items or []:
            path = canonical_path(entry.get("path") if isinstance(entry, dict) else entry)
            if not path:
                continue
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            title = ""
            if isinstance(entry, dict):
                title = str(entry.get("title") or "").strip()
            keep.append({"path": path, "title": title})
        return keep[:12]

    def workspace_title(self, path):
        """侧栏显示的名字：起过名就用名字，否则用文件夹名。"""
        target = canonical_path(path)
        for entry in self.workspaces():
            if os.path.normcase(entry["path"]) == os.path.normcase(target):
                return entry["title"] or os.path.basename(target)
        return os.path.basename(target)

    def save_agent(self, patch):
        """写回 config/agent.json（模型、接口、API Key 这些 agent.exe 也要用的字段）。"""
        self.agent = _merge(self.agent, patch or {})
        _write_json(self.agent_path, self.agent)
        return self.agent

    def remember_workspace(self, path, title=""):
        """登记一个工作区（新建 / 切换 / 打开会话时调用），最近用过的排最前。"""
        target = canonical_path(path)
        if not target:
            return self.workspaces()
        rest = [entry for entry in self.workspaces()
                if os.path.normcase(entry["path"]) != os.path.normcase(target)]
        entry = {"path": target, "title": title or ""}
        if not entry["title"]:
            for old in self.workspaces():
                if os.path.normcase(old["path"]) == os.path.normcase(target):
                    entry["title"] = old["title"]
        return self.save_workspaces([entry] + rest)

    def rename_workspace(self, path, title):
        """给工作区起个名字（只改显示名，不动文件夹）。重名时报错，和 DSH 一样。"""
        target = canonical_path(path)
        name = str(title or "").strip()
        if not name:
            raise ValueError("工作区名称不能为空")
        for entry in self.workspaces():
            if os.path.normcase(entry["path"]) != os.path.normcase(target) and entry["title"] == name:
                raise ValueError("已存在名为“%s”的工作区。" % name)
        items = []
        for entry in self.workspaces():
            if os.path.normcase(entry["path"]) == os.path.normcase(target):
                items.append({"path": entry["path"], "title": name})
            else:
                items.append(entry)
        return self.save_workspaces(items)

    def forget_workspace(self, path):
        """从列表里移除（不删文件夹，也不删会话）。"""
        target = canonical_path(path)
        keep = [entry for entry in self.workspaces()
                if os.path.normcase(entry["path"]) != os.path.normcase(target)]
        return self.save_workspaces(keep)

    def create_workspace(self, name, parent=""):
        """新建一个工作区文件夹：在 parent（默认 <安装目录>\\workspaces）下建 name 目录并登记。"""
        safe = re.sub(r'[\\/:*?"<>|]', "", str(name or "").strip()).strip(". ")
        if not safe:
            raise ValueError("请填写工作区名称")
        root = canonical_path(parent) or os.path.join(self.base_dir, "workspaces")
        target = os.path.join(root, safe)
        if not os.path.isdir(target):
            os.makedirs(target)
        return target

    # -- 模型解析 ---------------------------------------------------------
    def model_names(self):
        return sorted(self.agent.get("models", {}).keys())

    def active_profile(self):
        return self.agent.get("active_model") or ""

    def resolve_model(self, name=None):
        """返回 {'name','model','endpoint','api_key','headers'}，语义与 Go ResolveModel 一致。"""
        profile_name = name if name is not None else (self.active_profile() or "")
        resolved = {
            "profile": profile_name,
            "model": self.webui.get("model") or self.agent.get("model") or "",
            "base_url": self.webui.get("endpoint") or self.agent.get("base_url") or "",
            "api_key": self.agent.get("api_key") or "",
            "headers": dict(self.agent.get("headers") or {}),
            "stream": self.agent.get("stream", True),
            "auth_header": self.agent.get("auth_header") or DEFAULT_AUTH_HEADER,
        }
        # 网关不认 Bearer 时（企业网关常用 api-key / x-api-key），这里可以换成自定义请求头
        if self.webui.get("auth_header"):
            resolved["auth_header"] = self.webui["auth_header"]
        profiles = self.agent.get("models") or {}
        profile = profiles.get(profile_name)
        if isinstance(profile, dict):
            if profile.get("base_url"):
                resolved["base_url"] = profile["base_url"]
            if profile.get("model"):
                resolved["model"] = profile["model"]
            if profile.get("api_key"):
                resolved["api_key"] = profile["api_key"]
            if profile.get("auth_header"):
                resolved["auth_header"] = profile["auth_header"]
            merged = dict(resolved["headers"])
            merged.update(profile.get("headers") or {})
            resolved["headers"] = merged
            if profile.get("stream") is not None:
                resolved["stream"] = bool(profile["stream"])
        resolved["endpoint"] = normalize_endpoint(resolved["base_url"])
        return resolved

    def workspace_root(self):
        """工作区：模型新建文件默认写在这里；相对路径也以它为基准。"""
        root = self.webui.get("workspace_root") or ""
        if not root:
            root = os.path.join(self.base_dir, "workspace")
        return canonical_path(root)

    def public_config(self):
        """给浏览器的配置：API Key 一律掩码，绝不外传明文。"""
        model = self.resolve_model()
        raw_base_url = self.webui.get("endpoint") or self.agent.get("base_url") or ""
        key = model["api_key"] or ""
        return {
            "version": VERSION,
            "baseDir": self.base_dir,
            # 开机自启：界面上的开关按它渲染（available 为假时开关置灰并说明原因）
            "autostart": dict(zip(("available", "on", "note"),
                                  autostart_state(self.base_dir, exe_path=self.exe_path))),
            "workspace": self.workspace_root(),
            "endpoint": model["endpoint"],
            "baseUrlRaw": raw_base_url,
            "model": model["model"],
            "profile": model["profile"],
            "profiles": self.model_names(),
            "models": _public_models(self.agent.get("models") or {}),
            "apiKeyMasked": mask_secret(key),
            "hasApiKey": bool(key),
            "apiKeyFromProfile": bool((self.agent.get("models") or {})
                                      .get(model["profile"], {}).get("api_key")) if model["profile"] else False,
            "authHeader": model.get("auth_header") or DEFAULT_AUTH_HEADER,
            "endpointOverridden": bool(self.webui.get("endpoint")),
            "temperature": self.webui.get("temperature"),
            "maxTokens": self.webui.get("max_tokens"),
            "maxToolRounds": self.webui.get("max_tool_rounds"),
            "toolsEnabled": self.webui.get("tools_enabled"),
            "systemPrompt": self.webui.get("system_prompt"),
            "trimMessages": self.webui.get("trim_messages"),
            "contextMaxItems": self.webui.get("context_max_items"),
            "theme": self.webui.get("theme"),
            "host": self.webui.get("host"),
            "port": self.webui.get("port"),
            "allowOutsideWorkspace": self.webui.get("allow_outside_workspace"),
            "command": {
                "enabled": bool(self.agent["command"].get("enabled")),
                "confirmPrefixes": self.agent["command"].get("confirm_prefixes") or [],
                "blockedPrefixes": self.agent["command"].get("blocked_prefixes") or [],
                "alwaysConfirm": bool(self.agent["command"].get("always_confirm")),
                "maxOutputBytes": self.agent["command"].get("max_output_bytes"),
            },
            "price": self.webui.get("price"),
            "maxContentChars": self.agent.get("max_content_chars"),
            "loopRepeatLimit": self.webui.get("loop_repeat_limit"),
            "loopBreaker": self.webui.get("loop_breaker") is True,
            "maxRunSeconds": self.webui.get("max_run_seconds"),
            # 长任务相关：上下文自动压缩（设置界面里要能开关与调阈值）
            "autoCompact": self.webui.get("auto_compact", True),
            "compactThresholdRatio": self.webui.get("compact_threshold_ratio") or 0.8,
            "reasoningEffort": self.webui.get("reasoning_effort") or "",
            "reasoningEfforts": list(REASONING_EFFORTS),
            "recentWorkspaces": [entry["path"] for entry in self.workspaces()],
            "workspaces": self.workspaces(),
            # 新建工作区默认建在这里（<安装目录>\workspaces）
            "workspaceParent": os.path.join(self.base_dir, "workspaces"),
            "maxSubagents": self.webui.get("max_subagents"),
            "subagentMaxRounds": self.webui.get("subagent_max_rounds"),
            "search": {
                "engine": (self.webui.get("search") or {}).get("engine"),
                "endpoint": (self.webui.get("search") or {}).get("endpoint"),
                "maxResults": (self.webui.get("search") or {}).get("max_results"),
                "hasApiKey": bool((self.webui.get("search") or {}).get("api_key")),
            },
            "desktop": desktop_dir(),
            "maxImages": self.webui.get("max_images") or 256,
            "confirmHighRiskCommands": bool(self.webui.get("confirm_high_risk_commands")),
            "allowSelfRestart": self.webui.get("allow_self_restart", True),
            "allowDesktopControl": self.webui.get("allow_desktop_control", True),
            "confirmDesktopInput": bool(self.webui.get("confirm_desktop_input")),
            "confirmMessages": bool(self.webui.get("confirm_messages")),
            "notify": notify_view(self),
            "thinking": self.webui.get("thinking") or "",
            # 刚做过的配置迁移（老默认值升级），界面弹一次提示，别让老师觉得"怎么自己变了"
            "migratedSettings": [{"key": key, "from": old, "to": new}
                                 for key, old, new in (getattr(self, "migrations", None) or [])],
        }


def canonical_path(path):
    """把工作区路径统一成一种写法：绝对 + 规范分隔符。

    D:\\课件、d:/课件/、D:\\课件\\. 都会被认成同一个目录，这样侧栏按工作区分组时
    不会把同一个文件夹显示成好几行（看起来像“重复的工作区”）。
    """
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    try:
        return os.path.normpath(os.path.abspath(os.path.expandvars(text)))
    except (OSError, ValueError):
        return text


def desktop_dir():
    """默认打开的文件位置：桌面（老师最常把课件、作业放在桌面）。"""
    home = os.path.expanduser("~")
    for name in ("Desktop", "桌面"):
        candidate = os.path.join(home, name)
        if os.path.isdir(candidate):
            return candidate
    return home


def _public_models(profiles):
    out = {}
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        out[name] = {
            "model": profile.get("model") or "",
            "base_url": profile.get("base_url") or "",
        }
    return out


def normalize_endpoint(base_url):
    """把 base_url 规范成可直接 POST 的 chat/completions 地址。

    兼容三种常见写法：裸域名、以 /v1 结尾、已经是完整地址。
    """
    url = (base_url or "").strip()
    if not url:
        return ""
    url = url.rstrip("/")
    lower = url.lower()
    if lower.endswith("/chat/completions"):
        return url
    if lower.endswith("/completions") and not lower.endswith("/chat/completions"):
        return url[: -len("/completions")] + "/chat/completions"
    if lower.endswith("/v1") or lower.endswith("/beta") or lower.endswith("/api"):
        return url + "/chat/completions"
    return url + "/chat/completions"


def endpoint_candidates(base_url):
    """POST 目标地址候选列表。

    配置写成 https://api.deepseek.com（裸域名）时，先试 /chat/completions，
    遇到 404/405 再退到 /v1/chat/completions，避免因为少写一段路径就整体报错。
    已经带 /v1、/beta、/api 或完整路径时不再追加，避免拼出 /v1/v1/... 这种地址。
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return []
    out = [normalize_endpoint(url)]
    lower = url.lower()
    if lower.endswith("/chat/completions"):
        return out
    if lower.endswith(("/v1", "/beta", "/api")):
        return out
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        candidate = url + suffix
        if candidate not in out:
            out.append(candidate)
    return out


def client_for(resolved, timeout=300, ca_file="", insecure=False):
    """按 resolve_model() 的结果构造客户端。

    必须传入原始 base_url（而不是规范化后的 endpoint），这样裸域名配置才能
    在 404/405 时自动退到 /v1/chat/completions。
    """
    base = resolved.get("base_url") or resolved.get("endpoint") or ""
    return ModelClient(base, resolved.get("api_key") or "", resolved.get("headers") or {},
                       timeout=timeout, ca_file=ca_file, insecure=insecure,
                       auth_header=resolved.get("auth_header") or DEFAULT_AUTH_HEADER)


def mask_secret(value):
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def make_console_safe():
    """让中文 Windows 上的控制台/重定向输出不会因为"某个字符编不出来"而中断。

    中文版 Windows 的默认编码是 GBK。把输出重定向到文件时（老师/IT 把自检输出存成
    txt 发过来是常事），Python 按 GBK 编码，遇到 GBK 里没有的字符（✓ ▸ • ⋯ 之类）
    就抛 UnicodeEncodeError，整个脚本当场中断、日志戛然而止。

    这里做两件事：
      * 编不出来的字符降级成 ?，绝不中断；
      * 如果输出被重定向（不是控制台）且用户没有自己设 PYTHONIOENCODING，
        就用 UTF-8 写文件，日志拿到别的机器上也不会乱码。
    控制台本身仍然沿用系统编码（PEP 528 已经把控制台处理好了）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            continue
        try:
            if not stream.isatty() and not os.environ.get("PYTHONIOENCODING"):
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            continue


def notify_view(settings):
    """通知配置给浏览器的视图：授权码不回传，Webhook 地址也只露头尾。"""
    view = notify.notify_settings_view(settings)
    url = view.get("webhookUrl") or ""
    view["hasWebhookUrl"] = bool(url)
    view["webhookUrlMasked"] = mask_secret(url)
    view["webhookUrl"] = ""
    view["smtpPasswordMasked"] = "已保存（留空表示不改）" if view.get("hasSmtpPassword") else ""
    return view


# --------------------------------------------------------------------------
# HTTP 客户端
# --------------------------------------------------------------------------

class ChatError(Exception):
    def __init__(self, message, status=0, code="", retry_after=None):
        Exception.__init__(self, message)
        self.message = message
        self.status = status
        self.code = code                  # RATE_LIMIT / SERVER / TIMEOUT / TRANSPORT / ...
        self.retry_after = retry_after

    def retryable(self):
        return self.code in RETRYABLE_CODES


class ModelClient(object):
    """OpenAI 兼容 + DeepSeek 扩展（reasoning_content / 缓存计费字段）客户端。"""

    def __init__(self, endpoint, api_key, headers=None, timeout=300,
                 ca_file="", insecure=False, auth_header=DEFAULT_AUTH_HEADER):
        self.endpoint = endpoint
        self.candidates = endpoint_candidates(endpoint)
        self.auth_header = auth_header or DEFAULT_AUTH_HEADER
        if self.candidates:
            self.endpoint = self.candidates[0]
        self.api_key = api_key or ""
        self.headers = dict(headers or {})
        self.timeout = timeout or 300
        self.context = _ssl_context(ca_file, insecure)
        # TLS 记录层被中途改坏（SSLV3_ALERT_BAD_RECORD_MAC 之类）时，退回 TLS 1.2 重试；
        # 一旦这样救回来过，就记住，后续请求直接走能用的那条路。
        self.tls12_pinned = False
        self.tls_retried = False

    # -- 低层 -------------------------------------------------------------
    def _open_once(self, url, payload=None, method="GET"):
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            # 有些企业网关不认 Authorization: Bearer，而是 api-key / x-api-key 之类；
            # auth_header 就是给这种网关用的（Bearer 前缀只在 Authorization 上加）
            name = (self.auth_header or DEFAULT_AUTH_HEADER).strip() or DEFAULT_AUTH_HEADER
            if name.lower() == "authorization":
                headers[name] = "Bearer " + self.api_key
            else:
                headers[name] = self.api_key
        headers.update(self.headers)
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            # 单次读取最多等 STREAM_IDLE_TIMEOUT：卡住的连接当作超时（可重试），
            # 而不是让老师对着转圈等 self.timeout（默认 300 秒）
            return urllib.request.urlopen(request, timeout=min(self.timeout, STREAM_IDLE_TIMEOUT),
                                          context=self.context)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            text = _api_error_message(exc.code, body, url)
            code = ""
            if exc.code == 429:
                code = RETRY_RATE_LIMIT
            elif exc.code >= 500:
                code = RETRY_SERVER
            elif is_context_overflow(exc.code, text) or is_context_overflow(exc.code, body):
                code = RETRY_CONTEXT
            raise ChatError(text, exc.code, code,
                            retry_after_seconds(getattr(exc, "headers", None)))
        except urllib.error.URLError as exc:
            reason = exc.reason if exc.reason is not None else exc
            if isinstance(reason, TRANSPORT_ERRORS):
                # 把 ssl.SSLError / 超时原样抛上去，交给 _open 的传输层重试处理
                raise reason
            raise ChatError("网络错误：%s（%s）" % (reason, url), 0, RETRY_TRANSPORT)
        except TRANSPORT_ERRORS:
            raise
        except Exception as exc:  # socket.timeout 等
            raise ChatError("请求失败：%s（%s）" % (exc, url), 0, RETRY_TRANSPORT)

    def _open(self, url, payload=None, method="GET"):
        """打开连接；传输层抖动（TLS 记录被改坏、连接被重置）自动换新连接重试。

        `[SSL: SSLV3_ALERT_BAD_RECORD_MAC]` 这类报错几乎都是"中途被改包/连接不干净"，
        不是密钥或余额问题：换一条新连接通常立刻就好，所以先用系统默认重试一次，
        再把上下文限制到 TLS 1.2 重试一次（杀毒软件/代理做 HTTPS 扫描时最吃这一套）。
        """
        attempts = 3
        last = None
        for attempt in range(attempts):
            try:
                return self._open_once(url, payload, method)
            except TRANSPORT_ERRORS as exc:
                last = exc
                if attempt >= attempts - 1:
                    break
                if not self.tls12_pinned and attempt == 1:
                    if pin_tls12(self.context):
                        self.tls12_pinned = True
                        self.tls_retried = True
                time.sleep(0.4 + 0.6 * attempt)
        raise ChatError("请求失败：%s（%s）\n%s" % (last, url, transport_hint(last)))

    def post_chat(self, payload):
        """按候选地址依次尝试 POST，只在地址形态不对（404/405）时换下一个。"""
        first_error = None
        for index, url in enumerate(self.candidates or [self.endpoint]):
            try:
                response = self._open(url, payload, method="POST")
                self.endpoint = url
                return response
            except ChatError as exc:
                if first_error is None:
                    first_error = exc
                if exc.status not in (404, 405) or index == len(self.candidates) - 1:
                    raise first_error if index > 0 else exc
        raise first_error or ChatError("没有可用的接口地址")

    def list_models(self):
        base = self.endpoint
        root = base[: -len("/chat/completions")] if base.endswith("/chat/completions") else base
        ids = []
        for url in (root + "/models", root + "/v1/models"):
            try:
                with self._open(url) as response:
                    payload = json.loads(response.read().decode("utf-8", "replace"))
            except ChatError as exc:
                if exc.status in (401, 403):
                    raise
                continue
            for item in payload.get("data") or []:
                name = item.get("id") if isinstance(item, dict) else None
                if name and name not in ids:
                    ids.append(name)
            if ids:
                break
        return ids

    def balance(self):
        base = self.endpoint
        root = base[: -len("/chat/completions")] if base.endswith("/chat/completions") else base
        with self._open(root + "/user/balance") as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    # -- 流式对话 ---------------------------------------------------------
    def stream(self, payload, on_event, cancel=None, on_retry=None):
        """POST chat/completions 并按块解析。返回聚合结果 dict。

        失败重试（对照 DSH 的 dsh-llm-retry）：限流、服务端错误、超时、传输错误、
        空响应都会在**同一个步骤边界**上重跑这一次请求，指数退避并尊重 Retry-After；
        对模型不可见，老师那边只会看到一条"正在重试"的提示。

        已经吐过字的情况：先把"作废这次的部分输出"告诉界面（on_retry 回调），
        再重跑——这样长任务里遇到一次网络抖动不会让整轮白干，也不会出现重复内容。
        """
        payload = dict(payload)
        payload["stream"] = True
        last = None
        attempt = 0
        while True:
            state = {"emitted": False}
            try:
                return self._stream_once(payload, on_event, cancel, state)
            except ChatError as exc:
                if exc.code == RETRY_CONTEXT:
                    raise                      # 上下文超长交给上层压缩后重试
                if not exc.retryable():
                    raise
                last = exc
            except TRANSPORT_ERRORS as exc:
                last = ChatError("传输出错：%s" % exc, 0, RETRY_TRANSPORT)
            except ChatError:
                raise
            except Exception as exc:               # noqa: BLE001
                last = ChatError("请求失败：%s" % exc, 0, RETRY_TRANSPORT)
            attempt += 1
            if attempt > MAX_RETRIES:
                break
            delay = retry_delay(attempt, getattr(last, "retry_after", None))
            if on_retry is not None:
                on_retry({"attempt": attempt, "max": MAX_RETRIES, "delay": delay,
                          "code": last.code, "emitted": state["emitted"],
                          "message": last.message})
            # 退避期间可以被取消打断
            deadline = time.time() + delay
            while time.time() < deadline:
                if cancel is not None and cancel.is_set():
                    raise ChatError("已取消")
                time.sleep(0.1)
        hint = transport_hint(last)
        raise ChatError("请求失败（已重试 %d 次）：%s\n%s" % (attempt - 1, last.message, hint))

    def _stream_once(self, payload, on_event, cancel, state):
        result = {"content": "", "reasoning": "", "tool_calls": [], "finish_reason": "", "usage": None}
        pieces = []
        calls = {}
        splitter = ThinkSplitter()

        def emit(kind, text):
            # 只要往界面送过东西，就不能再重发整个请求了
            state["emitted"] = True
            on_event(kind, text)

        response = self.post_chat(payload)
        try:
            for raw in response:
                if cancel is not None and cancel.is_set():
                    raise ChatError("已取消")
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    chunk = json.loads(body)
                except ValueError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    raise ChatError(_chunk_error(chunk["error"]))
                usage = chunk.get("usage") if isinstance(chunk, dict) else None
                if usage:
                    result["usage"] = usage
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                delta = choice.get("delta") or choice.get("message") or {}
                if choice.get("finish_reason"):
                    result["finish_reason"] = choice["finish_reason"]
                text = delta.get("content")
                if text:
                    # 有的模型把思考写在  thinking… 里，这里统一拆成思考与回答两条流
                    inner_thought, inner_text = splitter.feed(text)
                    if inner_thought:
                        result["reasoning"] += inner_thought
                        emit("reasoning", inner_thought)
                    if inner_text:
                        result["content"] += inner_text
                        pieces.append(inner_text)
                        if len(pieces) >= 8:
                            emit("content", "".join(pieces))
                            pieces = []
                thought = delta.get("reasoning_content")
                if thought is None:
                    thought = delta.get("reasoning")
                if thought:
                    result["reasoning"] += thought
                    emit("reasoning", thought)
                for call in delta.get("tool_calls") or []:
                    _accumulate_tool_call(calls, call)
        finally:
            try:
                response.close()
            except Exception:
                pass
        # 收尾：没闭合的 thinking 标签按思考内容处理，不留给回答
        rest_thought, rest_text = splitter.flush()
        if rest_thought:
            result["reasoning"] += rest_thought
            emit("reasoning", rest_thought)
        if rest_text:
            result["content"] += rest_text
            pieces.append(rest_text)
        if pieces:
            emit("content", "".join(pieces))
        result["tool_calls"] = [calls[key] for key in sorted(calls.keys())]
        return result


THINK_OPEN_TAGS = ("<think>", "<thinking>", "<reasoning>", "<thought>")
THINK_CLOSE_TAGS = ("</think>", "</thinking>", "</reasoning>", "</thought>")


class ThinkSplitter(object):
    """把回答流里的 ``<think>…</think>`` 拆成「思考」与「回答」两条流。

    两类模型都要兼容：
      - 原生支持 reasoning_content 的（DeepSeek 等）由上层单独取字段，这里不动；
      - 把思考写成 <think>…</think> 的，在这里解析标签。
    跨数据块的标签（"<thi" + "nk>"）也能正确处理；标签没闭合时，剩下的内容算思考；
    单独的结束标签（模型从思考中途续写）会被丢掉，不会残留到回答里。
    """

    def __init__(self):
        self.inside = False
        self.tail = ""          # 可能是半个标签的尾巴，留到下一块再判断

    def feed(self, text):
        """喂入一段流式文本，返回 (思考片段, 回答片段)。"""
        buffer = self.tail + (text or "")
        self.tail = ""
        thoughts = []
        answers = []
        while buffer:
            tag, index = self._find_tag(buffer)
            if tag is None:
                hold = self._partial_tag_length(buffer)
                body = buffer[:len(buffer) - hold] if hold else buffer
                self.tail = buffer[len(buffer) - hold:] if hold else ""
                if body:
                    (thoughts if self.inside else answers).append(body)
                break
            head = buffer[:index]
            if head:
                (thoughts if self.inside else answers).append(head)
            buffer = buffer[index + len(tag):]
            if tag in THINK_OPEN_TAGS:
                self.inside = True
            else:
                self.inside = False       # 结束标签（哪怕是多余的）一律丢掉
        return "".join(thoughts), "".join(answers)

    def flush(self):
        """流结束：没闭合的标签内容按当前状态收尾。"""
        rest, self.tail = self.tail, ""
        if not rest:
            return "", ""
        return (rest, "") if self.inside else ("", rest)

    def _find_tag(self, buffer):
        """找最早出现的思考标签（开闭都算，便于丢掉多余的结束标签）。"""
        best = None
        for tag in THINK_OPEN_TAGS + THINK_CLOSE_TAGS:
            index = buffer.find(tag)
            if index >= 0 and (best is None or index < best[1]):
                best = (tag, index)
        return best if best else (None, -1)

    def _partial_tag_length(self, buffer):
        """返回尾部「可能是半个标签」的长度，避免把 "<thi" 提前当正文发出去。"""
        for tag in THINK_OPEN_TAGS + THINK_CLOSE_TAGS:
            limit = min(len(tag) - 1, len(buffer))
            for length in range(1, limit + 1):
                if buffer.endswith(tag[:length]):
                    return length
        return 0


def split_think(text):
    """非流式场景：一次把整段文本拆成 (思考, 回答)。"""
    splitter = ThinkSplitter()
    thoughts, answers = splitter.feed(text or "")
    tail_thought, tail_answer = splitter.flush()
    return thoughts + tail_thought, answers + tail_answer


def _ssl_context(ca_file, insecure):
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if ca_file and os.path.isfile(ca_file):
        context = ssl.create_default_context(cafile=ca_file)
        return context
    return ssl.create_default_context()


def pin_tls12(context):
    """把 SSL 上下文限制在 TLS 1.2。

    用于 `SSLV3_ALERT_BAD_RECORD_MAC` 这类"握手成功、记录层被改坏"的报错：
    常见原因是杀毒软件/代理在做 HTTPS 扫描（中途改包），对 TLS 1.3 处理不好；
    退回 TLS 1.2 往往立刻恢复。只在重试时用，不做默认。
    """
    version = getattr(ssl, "TLSVersion", None)
    if version is None or context is None:
        return False
    try:
        context.maximum_version = version.TLSv1_2
        return True
    except (AttributeError, ValueError):
        return False


# 传输层抖动：这些错误重试一次基本都能过，不该让老师看到一句英文报错
TRANSPORT_ERRORS = (ssl.SSLError, ssl.CertificateError, ConnectionResetError,
                    ConnectionAbortedError, BrokenPipeError, TimeoutError,
                    http.client.RemoteDisconnected, http.client.IncompleteRead)

# ---- 失败重试策略（对照 DSH 的 dsh-llm-retry）---------------------------------
# 可重试的失败类别：空响应、限流、服务端错误、超时、传输错误。
# 每次重试都在**同一个步骤边界**上用同一份历史重跑，模型看不到重试过程。
RETRY_RATE_LIMIT = "RATE_LIMIT"
RETRY_SERVER = "SERVER"
RETRY_TIMEOUT = "TIMEOUT"
RETRY_TRANSPORT = "TRANSPORT"
RETRY_EMPTY = "EMPTY_RESPONSE"
RETRY_CONTEXT = "CONTEXT_OVERFLOW"
RETRYABLE_CODES = (RETRY_RATE_LIMIT, RETRY_SERVER, RETRY_TIMEOUT, RETRY_TRANSPORT, RETRY_EMPTY)
MAX_RETRIES = 5                 # DSH normal 模式同款上限
RETRY_INITIAL_DELAY = 0.5       # 500ms
RETRY_MAX_DELAY = 10.0          # 10s
RETRY_JITTER = 0.1              # 10% 抖动
# 单次请求内多久没有新数据就当超时（DSH 把 TIMEOUT 也列为可重试）
STREAM_IDLE_TIMEOUT = 120.0


def is_context_overflow(status, message):
    """判断是不是"上下文超长"（各家措辞不同，宽容匹配）。"""
    if status not in (400, 413):
        return False
    text = (message or "").lower()
    return any(marker in text for marker in
               ("context length", "context_length", "maximum context", "context window",
                "too many tokens", "reduce the length", "max_tokens is too large",
                "输入的 token", "上下文长度"))


def retry_delay(attempt, retry_after=None):
    """指数退避 + 抖动；服务端给了 Retry-After 就听它的。"""
    if retry_after:
        try:
            return max(1.0, min(60.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
    delay = min(RETRY_MAX_DELAY, RETRY_INITIAL_DELAY * (2 ** max(0, attempt - 1)))
    return delay * (1 + random.uniform(-RETRY_JITTER, RETRY_JITTER))


def retry_after_seconds(headers):
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    return value


def _retry_label(code):
    return {"RATE_LIMIT": "被限流（429）", "SERVER": "服务端错误（5xx）",
            "TIMEOUT": "响应超时", "TRANSPORT": "连接中断",
            "EMPTY_RESPONSE": "返回了空响应"}.get(code, "暂时性失败")


def is_tls_record_error(exc):
    """判断是不是"TLS 记录被改坏/连接被掐断"这一类。

    OpenSSL 的报错文案有几种写法（BAD_RECORD_MAC / UNEXPECTED_EOF_WHILE_READING /
    EOF occurred in violation of protocol），统一把下划线当空格再匹配。
    """
    text = str(exc).lower().replace("_", " ")
    return any(marker in text for marker in
               ("bad record mac", "unexpected eof", "sslv3 alert",
                "tlsv1 alert", "wrong version number",
                "eof occurred in violation of protocol", "record layer failure"))


def transport_hint(exc):
    """给这类网络/TLS 抖动配一句老师能照着做的话。"""
    if is_tls_record_error(exc):
        return ("这是 HTTPS 传输被中途干扰（不是密钥或余额问题）。常见原因：杀毒软件的"
                "「HTTPS 扫描 / 网页防护」在改包、公司代理、或网络本身不稳。"
                "已经自动换新连接重试过；若仍频繁出现，可以：关掉杀毒的 HTTPS 扫描、"
                "换一个网络（手机热点）试试，或稍后重试。")
    return "网络连接中断，已经重试过；请检查网络或代理设置，稍后再试。"


def _api_error_message(status, body, url=""):
    detail = ""
    try:
        payload = json.loads(body)
        error = payload.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or ""
        elif isinstance(error, str):
            detail = error
        if not detail:
            detail = payload.get("message") or ""
    except ValueError:
        detail = (body or "").strip()
    detail = _clean(detail)[:600]
    where = ("（%s）" % url) if url else ""
    return "接口返回 %s%s：%s" % (status, where, detail or "无详细信息")


def _chunk_error(error):
    if isinstance(error, dict):
        return _clean(error.get("message") or str(error))
    return _clean(str(error))


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def spec_name(spec):
    """从工具的 OpenAI 声明里取函数名。"""
    return ((spec or {}).get("function") or {}).get("name") or ""


def _call_name(call):
    return ((call or {}).get("function") or {}).get("name") or ""


def _call_args_ok(call):
    """工具调用的参数是不是**完整合法的 JSON**。

    流式响应被截断时 arguments 会停在半截（`{"path": "C:\\课`），这种调用一旦进入历史，
    之后每一次请求都会带着它——接口报错、重试循环卡死，或者干脆被当成"空参数"执行。
    （社区已确认的坑：hermes-agent #14443 / #35151 / #7680、opencode #18108）
    """
    raw = ((call or {}).get("function") or {}).get("arguments")
    if raw is None or raw == "":
        return True                      # 无参数工具（例如 display_inventory）是合法的
    if not isinstance(raw, str):
        return True
    try:
        json.loads(raw)
    except ValueError:
        return False
    return True


def repair_tool_args(raw):
    """尽力把被截断的工具参数 JSON 修成可用的 dict；修不了返回 None。

    社区做法（hermes-agent #35574「先尝试 JSON 修复再中止」）：被截断的参数通常只是
    最后一项没写完，丢掉不完整的尾巴、把括号补上就能用——比直接丢弃重发少花一次调用。
    只在"结构完整的位置"下刀，绝不猜内容。
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass

    def scan(source):
        """返回 (未闭合的括号栈, 是否停在字符串中间)。"""
        stack = []
        in_string = False
        escape = False
        for char in source:
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                stack.append(char)
            elif char in "}]":
                if stack:
                    stack.pop()
        return stack, in_string

    def attempt(prefix):
        prefix = prefix.rstrip().rstrip(",")
        stack, in_string = scan(prefix)
        if in_string:
            return None                  # 停在字符串中间：这一刀不行，再往前退
        closers = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
        try:
            value = json.loads(prefix + closers)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    # 从末尾往前走，遇到"像值结束"的位置就试着补齐括号解析一次。
    # 比手写状态机可靠（键的闭合引号、数组里的逗号都不会被误当成切点）。
    floor = max(0, len(text) - 800)
    for cut in range(len(text), floor, -1):
        if text[cut - 1] in '"}]\u4e00\uff0c0123456789abcdefghijklmnopqrstuvwxyz' or text[cut - 1].isspace():
            value = attempt(text[:cut])
            if value is not None:
                return value
    return attempt(text)


def _call_key(call):
    return (call or {}).get("id") or ("call_" + uuid.uuid4().hex[:8])


def _accumulate_tool_call(calls, delta):
    index = delta.get("index")
    if index is None:
        index = len(calls)
    slot = calls.get(index)
    if slot is None:
        slot = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        calls[index] = slot
    if delta.get("id"):
        slot["id"] = delta["id"]
    if delta.get("type"):
        slot["type"] = delta["type"]
    function = delta.get("function") or {}
    name = function.get("name")
    if name:
        if not slot["function"]["name"]:
            slot["function"]["name"] = name
        elif name != slot["function"]["name"]:
            slot["function"]["name"] += name
    arguments = function.get("arguments")
    if arguments:
        slot["function"]["arguments"] += arguments


def parse_tool_arguments(raw):
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {"__raw__": text}
    if isinstance(value, dict):
        return value
    return {"value": value}


# --------------------------------------------------------------------------
# 技能（SKILL.md）
# --------------------------------------------------------------------------

class Skill(object):
    def __init__(self, name, description, path, content, keywords=None, always=False):
        self.name = name
        self.description = description
        self.path = path
        self.content = content
        self.keywords = list(keywords or [])
        # always=True：不管老师选没选、输的是什么都注入（"硬性技能"）。
        # 用在"这条规矩必须一直生效，靠关键词命中不够保险"的地方——
        # 比如"画函数图像必须用 show_figure"，漏一次就会去开希沃白板。
        self.always = bool(always)

    def to_dict(self):
        return {"name": self.name, "description": self.description,
                "keywords": self.keywords, "always": self.always}


def load_skills(base_dir, skip=None):
    """读技能目录；skip 里是老师删过的技能名（删了就别再让它出现在提示词/侧栏里）。

    为什么要 skip：技能装在**程序目录**里，升级时整棵树被换掉——不记住删除的话，
    老师删掉的技能下次升级就回来了。删除记录在 config\\webui.json（升级不动它）。
    """
    root = os.path.join(base_dir, "skills")
    found = []
    if not os.path.isdir(root):
        return found
    skip = set(str(item or "").strip() for item in (skip or []))
    for current, _dirs, files in os.walk(root):
        for name in files:
            if name.lower() != "skill.md":
                continue
            path = os.path.join(current, name)
            if skip and os.path.basename(current) in skip:
                continue
            try:
                with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
                    raw = handle.read()
            except OSError:
                continue
            found.append(_parse_skill(path, raw))
    found.sort(key=lambda item: item.name)
    return found


def _parse_skill(path, raw):
    skill = Skill(os.path.basename(os.path.dirname(path)), "", path, raw)
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end > 0:
            for line in raw[3:end].splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip().strip("\"'")
                if key == "name" and value:
                    skill.name = value
                elif key == "description":
                    skill.description = value
                elif key == "keywords":
                    skill.keywords = [item.strip() for item in re.split(r"[,，、\s]+", value)
                                      if item.strip()]
                elif key == "always":
                    skill.always = value.lower() in ("1", "true", "yes", "on", "是", "常驻")
    if not skill.description:
        for line in raw.splitlines():
            text = line.strip()
            if text and not text.startswith("---") and not text.startswith("#"):
                skill.description = text
                break
    return skill


# 这些词在技能描述里太常见，按 2 字窗口匹配会产生误命中（例如“内容写”命中表格技能）
SKILL_STOPWORDS = frozenset((
    "内容", "说明", "要求", "规范", "标准", "相关", "一般", "常见", "建议", "可以",
    "需要", "应该", "使用", "保持", "处理", "支持", "提供", "生成", "输出", "整理",
    "检查", "设计", "字段", "用于", "以及", "并且", "如果", "按照", "根据",
))


def select_skills(all_skills, text):
    """选中要注入的技能。

    - 技能声明了 keywords 时：只按名称与关键词匹配，精确、可控。
    - 没声明 keywords 时：沿用 CLI 的规则（名称、@skill:name、$name、描述英文词、描述中文 2 字窗口）。
    """
    needle = (text or "").lower()
    selected = []
    for skill in all_skills:
        name = skill.name.lower()
        hit = ("@skill:" + name) in needle or ("$" + name) in needle
        if not hit and name and name in needle:
            hit = True
        if not hit and skill.keywords:
            for keyword in skill.keywords:
                if keyword and keyword.lower() in needle:
                    hit = True
                    break
        if hit:
            selected.append(skill)
            continue
        if skill.keywords:
            # 声明了关键词的技能只按名称与关键词命中，避免描述里的常见词造成误注入
            continue
        words = re.split(r"[\s,;:()\[\]{}]+", skill.description.lower())
        for word in words:
            word = word.strip(".,;:()[]{}")
            if len(word) >= 4 and word in needle:
                hit = True
                break
        if not hit:
            for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", skill.description or ""):
                for start in range(len(chunk) - 1):
                    pair = chunk[start:start + 2]
                    if pair in SKILL_STOPWORDS:
                        continue
                    if pair in needle:
                        hit = True
                        break
                if hit:
                    break
        if hit:
            selected.append(skill)
    return selected


def skills_prompt(selected):
    if not selected:
        return ""
    parts = ["当前启用的本地技能（相关时请遵循其要求）："]
    for skill in selected:
        parts.append("\n--- skill: %s ---\n%s" % (skill.name, skill.content.rstrip()))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 消息与计费
# --------------------------------------------------------------------------

MISSING_TOOL_RESULT = "[该工具调用的结果在历史中缺失，已自动占位；需要该结果请重新调用这个工具]"


def repair_tool_pairs(messages):
    """补齐/整理 assistant(tool_calls) 与 tool 消息的配对，返回 (消息列表, 修复说明)。

    接口的硬性要求（DeepSeek 与 OpenAI 兼容接口一致）：
      * 带 tool_calls 的 assistant 消息后面，必须紧跟**每一个** tool_call_id 对应的 tool 消息；
      * tool 消息前面必须有一条带对应 tool_calls 的 assistant 消息。

    旧版本会在这一轮的工具结果中间插一条"注入图片"的 user 消息（`AgentRun._inject_image`），
    一轮里有两个以上工具调用时，历史就成了
        assistant(tool_calls=[A,B]) → tool(A) → user(图片) → tool(B)
    于是接口直接 400：
        An assistant message with 'tool_calls' must be followed by tool messages
        responding to each 'tool_call_id'. (insufficient tool messages following tool_calls message)
    另外整轮被中断（按 Esc、熔断、网关报错）也会留下"只有调用没有结果"的历史。

    这里做的是**无损且幂等**的整理：
      * 结果缺失 → 补一条占位说明（模型知道这一步没结果，不会瞎猜）；
      * 有别的消息夹在工具块中间 → 那条消息先让路，真实工具结果照样按序收进工具块，
        整块收完（或确认缺失）之后再把让路的消息放回它后面——**真实结果一条都不丢**；
      * 无主的 tool 消息（前面根本没有对应调用）→ 丢掉，否则同样会被接口拒绝；
      * 调用没有 id（个别网关不给）→ 就地补一个稳定 id，工具结果才能对上（顺带修好存下来的历史）。
    """
    out = []
    notes = []
    pending = []
    deferred = []
    filled = 0
    dropped = 0
    moved = 0

    def settle():
        """补齐这一轮还缺的工具结果，然后把让路的那些消息按原顺序放回去。"""
        for pending_id in pending:
            out.append({"role": "tool", "tool_call_id": pending_id,
                        "content": MISSING_TOOL_RESULT})
        count = len(pending)
        del pending[:]
        if deferred:
            out.extend(deferred)
            del deferred[:]
        return count

    for message in messages or []:
        message = message or {}
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            filled += settle()
            out.append(message)
            for index, call in enumerate(message.get("tool_calls") or []):
                call = call or {}
                call_id = call.get("id") or ""
                if not call_id:
                    call_id = "call_auto_%d_%d" % (len(out), index)
                    call["id"] = call_id
                pending.append(call_id)
            continue
        if role == "tool":
            call_id = message.get("tool_call_id") or ""
            if pending and call_id and call_id in pending:
                pending.remove(call_id)
                out.append(message)
                if not pending:
                    settle()
                continue
            if pending and not call_id and len(pending) == 1:
                # 网关/历史里两边都没有 id 时，按顺序认领这一条
                del pending[:]
                out.append(message)
                settle()
                continue
            dropped += 1        # 无主的结果：接口会拒绝，只能从请求里去掉
            continue
        if pending:
            # 工具块还没收完：这条消息先让路，等结果齐了再放回去
            deferred.append(message)
            moved += 1
            continue
        out.append(message)
    filled += settle()

    if filled:
        notes.append("补齐了 %d 条缺失的工具结果（占位，不影响后续对话）" % filled)
    if moved:
        notes.append("把 %d 条夹在工具调用中间的消息挪到了后面" % moved)
    if dropped:
        notes.append("丢掉了 %d 条没有对应调用的工具结果" % dropped)
    return out, notes


def to_api_messages(system_prompt, messages, image_loader=None, on_repair=None,
                    max_images=MAX_IMAGES_PER_MESSAGE, image_budget=0, on_notice=None):
    """构造请求消息：只发送接口认识的字段。

    reasoning_content 的官方规则（api-docs.deepseek.com/guides/thinking_mode，已对着接口实测）：
      * thinking 模式下，历史里**每一条带 tool_calls 的 assistant 消息都必须带上
        reasoning_content 字段**——内容是空字符串也行，但漏掉字段直接 400：
          The `reasoning_content` in the thinking mode must be passed back to the API.
        这一点和"这次请求带不带 tools"无关：只要历史里有工具调用，就必须回传，
        所以这里不再按"本轮是否带工具"来决定要不要带（旧写法会漏字段，实测必 400）。
      * 不带 tool_calls 的普通回答不需要回传推理，传了也被忽略，所以不带，省 token。
      界面上思考仍然只显示在折叠栏里，不会混进回答。

    发出去之前一定会做一次 repair_tool_pairs：历史里工具配对不完整同样会被接口 400，
    修好再发，老师不会看到那句英文报错。on_repair 用于把修复情况告诉界面。
    """
    loader = image_loader or load_image_part
    out = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})
    source, notes = repair_tool_pairs(messages)
    if notes and on_repair is not None:
        on_repair(notes)
    # 只保留最近两次带图消息的图片，避免历史图片反复计费
    with_images = [index for index, item in enumerate(source)
                   if item.get("role") == "user" and item.get("images")]
    keep_images = set(with_images[-MAX_IMAGE_HISTORY:]) if with_images else set()
    for index, message in enumerate(source):
        role = message.get("role")
        if role == "assistant":
            item = {"role": "assistant", "content": message.get("content") or ""}
            if message.get("tool_calls"):
                item["tool_calls"] = message["tool_calls"]
                # 这个字段**不能省**：thinking 模式下漏掉就是 400（详见上面的说明）。
                # 历史里没存推理（旧会话、当时关了思考）时补空串，接口接受。
                item["reasoning_content"] = message.get("reasoning") or ""
            out.append(item)
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": message.get("tool_call_id") or "",
                "content": message.get("content") or "",
            })
        elif role == "user":
            images = (message.get("images") or [])[:max_images]
            text = message.get("content") or ""
            if images and index not in keep_images:
                images = []
                text = (text + "\n\n[此前的图片已省略，需要时请重新附加]").strip()
            if not images:
                out.append({"role": "user", "content": text})
                continue
            parts = []
            if text:
                parts.append({"type": "text", "text": text})
            skipped = []
            budget = image_budget or 0
            for entry in images:
                if budget:
                    try:
                        size = os.path.getsize(entry.get("path") if isinstance(entry, dict) else entry)
                    except OSError:
                        size = 0
                    if parts and len(parts) > 1 and size > budget:
                        # 图片总量预算：一次塞太多张会把请求撑爆（256 张 × 1MB 就是 256MB），
                        # 这里按预算截断并如实说明，而不是发一个必失败的请求
                        skipped.append("（后续图片超出单次请求预算，已省略）")
                        if on_notice is not None:
                            on_notice("单次请求的图片总量超过预算，后面的图片这次没发出去；"
                                      "可以分几次发，或让老师先压缩图片。")
                        break
                    budget -= size
                part = loader(entry)
                if part:
                    parts.append(part)
                else:
                    label = entry.get("name") if isinstance(entry, dict) else entry
                    skipped.append(str(label or entry))
            if not parts:
                parts.append({"type": "text", "text": text or "（图片不可用）"})
            if skipped:
                parts[0]["text"] = (parts[0].get("text", "") +
                                    "\n\n[以下图片未能附加：%s]" % "、".join(skipped)).strip()
            out.append({"role": "user", "content": parts})
        elif role == "system":
            out.append({"role": "system", "content": message.get("content") or ""})
    return out


def trim_messages(messages, limit):
    """裁剪历史，且保证不从 assistant(tool_calls)/tool 中间切断。"""
    if not limit or limit <= 0 or len(messages) <= limit:
        return list(messages or [])
    start = len(messages) - limit
    while start < len(messages) and messages[start].get("role") != "user":
        start += 1
    if start >= len(messages):
        return list(messages)
    return list(messages[start:])


def empty_usage():
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "reasoning_tokens": 0,
        "cost": 0.0,
        "rounds": 0,
    }


def normalize_usage(usage):
    usage = usage or {}
    details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = details.get("cached_tokens")
    hit = int(hit or 0)
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss = max(0, prompt - hit)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "cache_hit_tokens": hit,
        "cache_miss_tokens": int(miss or 0),
        "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
    }


def estimate_cost(usage, price):
    price = price or {}
    unit = float(price.get("unit") or 1000000)
    if unit <= 0:
        unit = 1000000
    hit = float(usage.get("cache_hit_tokens") or 0) / unit * float(price.get("cache_hit") or 0)
    miss = float(usage.get("cache_miss_tokens") or 0) / unit * float(price.get("cache_miss") or 0)
    out = float(usage.get("completion_tokens") or 0) / unit * float(price.get("output") or 0)
    return {"cost": hit + miss + out, "currency": price.get("currency") or "USD",
            "cache_hit": hit, "cache_miss": miss, "output": out}


def add_usage(total, usage):
    for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                "cache_hit_tokens", "cache_miss_tokens", "reasoning_tokens"):
        total[key] = int(total.get(key) or 0) + int(usage.get(key) or 0)
    return total


# --------------------------------------------------------------------------
# 会话存储
# --------------------------------------------------------------------------

class SessionStore(object):
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "sessions")
        self._lock = threading.Lock()
        if not os.path.isdir(self.dir):
            os.makedirs(self.dir)

    def _path(self, session_id):
        safe = re.sub(r"[^0-9A-Za-z_-]", "", session_id or "")
        if not safe:
            raise ValueError("非法会话 id")
        return os.path.join(self.dir, safe + ".json")

    def list_summaries(self):
        items = []
        for name in os.listdir(self.dir):
            if not name.endswith(".json"):
                continue
            try:
                data = _read_json(os.path.join(self.dir, name))
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            messages = data.get("messages") or []
            preview = ""
            for message in reversed(messages):
                if message.get("role") == "user":
                    preview = _clean(message.get("content") or "")[:80]
                    break
            items.append({
                "id": data.get("id") or name[:-5],
                "title": data.get("title") or "新会话",
                "updated": data.get("updated") or data.get("created") or "",
                "model": data.get("model") or "",
                "workspace": canonical_path(data.get("workspace")) if data.get("workspace") else "",
                "messages": len(messages),
                "preview": preview,
            })
        items.sort(key=lambda item: item.get("updated") or "", reverse=True)
        return items

    def load(self, session_id):
        data = _read_json(self._path(session_id))
        if not isinstance(data, dict):
            raise KeyError(session_id)
        data.setdefault("messages", [])
        data.setdefault("context", [])
        data.setdefault("skills", [])
        data.setdefault("workspace", "")
        data.setdefault("usage", empty_usage())
        return data

    def load_or_none(self, session_id):
        try:
            return self.load(session_id)
        except (KeyError, ValueError, OSError):
            return None

    def save(self, session):
        session["updated"] = _now()
        with self._lock:
            _write_json(self._path(session["id"]), session)
        return session

    def create(self, title=None, model="", workspace="", effort=""):
        session = {
            "id": uuid.uuid4().hex[:12],
            "title": title or "新会话",
            "created": _now(),
            "updated": _now(),
            "model": model,
            "workspace": workspace,
            "effort": effort,
            "messages": [],
            "context": [],
            "skills": [],
            "usage": empty_usage(),
        }
        return self.save(session)

    def delete(self, session_id):
        try:
            os.remove(self._path(session_id))
            return True
        except OSError:
            return False

    def rename(self, session_id, title):
        session = self.load(session_id)
        session["title"] = title or session.get("title") or "新会话"
        return self.save(session)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def resolve_effort(settings, session=None, override=""):
    """推理等级：本次指定 > 会话选择 > 全局默认；空值或非法值都表示“不改动接口参数”。"""
    candidates = (override, (session or {}).get("effort"),
                  (settings.webui if settings else {}).get("reasoning_effort"))
    for candidate in candidates:
        level = str(candidate or "").strip().lower()
        if level in REASONING_EFFORTS:
            return level
    return ""


def _continuation_nudge(finish_reason, content):
    """这一轮是不是“没写完、但也不是在调工具”？

    两种情况会让聊天看着像被硬生生切断：输出被 max_tokens 截断，以及一个字都没写。
    需要自动接着说时返回 {"notice": 给用户看的话, "prompt": 追加给模型的指令}，否则返回 None。
    """
    if finish_reason == "length":
        return {
            "notice": "回答被长度上限截断，已自动继续写（第 %d 次）。",
            "prompt": "（系统：上一条回答因为长度上限被截断了，请紧接着往下写，"
                      "不要重复已经说过的内容，也不要重新开场。）",
        }
    if not (content or "").strip() and finish_reason not in ("content_filter",):
        return {
            "notice": "模型刚才没有输出正文，已自动让它重写（第 %d 次）。",
            "prompt": "（系统：你上一条回复没有任何正文，用户只看到一个空的回答框。"
                      "请现在就把要说的话完整写出来；如果需要先调用工具，就直接调用。）",
        }
    return None


# --------------------------------------------------------------------------
# Agent 运行
# --------------------------------------------------------------------------

# 只读且幂等的工具：模型连着调几次是正常的（看完界面再点、点完再看一眼），不算"打转"。
# 注意 click / type / scroll / move / shortcut / app / draw_path / multi_* **不在**这里：
# 它们是真的改别人窗口的状态，重复做同一件事就该被拦。
IDEMPOTENT_TOOLS = frozenset((
    "snapshot", "screenshot", "display_inventory", "wait_for",
    "process", "registry", "clipboard", "list_dir", "read_file", "view_image",
    "inspect_media", "diagnose_pptx", "read_url", "web_search",
))

# 模型偶尔会把工具调用"写"在文本里而不是真正发起调用（网关不支持 tools、
# 或者上下文太长时常见）。出现这种文本就提醒它改用原生调用，而不是就此收工。
TOOL_TEXT_HINTS = ("tool_calls", "<tool_call>", "tool_call>", "function_call", "functions.")
TOOL_TEXT_NUDGE = ("你刚才把工具调用写成了普通文字，并没有真正发起调用（界面上只能看到这段文字）。"
                   "请直接发起工具调用：不要在回答里写出工具名和参数，也不要用 ```json 包起来。")
# 工具轮次用完后的收尾提示：必须是纯文字总结，否则模型会继续"写"工具调用
WRAP_UP_PROMPT = ("（系统：本次运行的工具轮次预算（%d 轮）已经用完，现在**不能再调用工具**了。"
                  "请只用文字总结：已经完成了什么、还剩什么没做完，以及下一步建议。"
                  "不要在回答里写工具名、参数或 JSON。）")


def _looks_like_tool_text(content, tool_names):
    """判断模型的回答是不是"把工具调用当文本写出来了"。

    宁可漏判也不要误判：正常回答里提到"我会用 screenshot 截屏"是合理的，
    只有出现接口字段名（tool_calls / {"name": ...}）或"工具名(参数)"这种调用写法才算。
    """
    text = content or ""
    if not text.strip():
        return False
    lowered = text.lower()
    if any(hint in lowered for hint in TOOL_TEXT_HINTS):
        return True
    named = [name for name in tool_names if name and name in text]
    if not named:
        return False
    if re.search(r'"\s*(name|arguments|tool_call_id|tool_calls)\s*"\s*:', text):
        return True
    for name in named[:3]:
        if re.search(r"(?m)^\s*[-*>]?\s*%s\s*[\(（:]" % re.escape(name), text):
            return True
        if re.search(r"```[a-zA-Z]*\s*\n?[^\n]*%s" % re.escape(name), text):
            return True
    return False


class AgentRun(object):
    """一次用户提问驱动的完整工具循环。通过 emit 回调把事件推给浏览器。"""

    def __init__(self, settings, session, client, tools, emit, confirmer, cancel, store=None):
        self.settings = settings
        self.session = session
        self.client = client
        self.tools = tools
        self.emit = emit
        self.confirmer = confirmer
        self.cancel = cancel
        self.store = store
        # 老师删过的技能不再进提示词（技能在程序目录里，升级会换掉，删除记录在 config）
        self.skills = load_skills(settings.base_dir, skip=settings.deleted_skills())
        self.active_skills = []
        self.has_images = False
        self.injected = []
        self.repair_notified = False
        self.repeat_reminders = 0
        self.reasoning_notice_sent = False   # "这一轮想得特别久"只提示一次，不因此停任务
        self._required_args = {}      # 工具的必填参数缓存（修复截断参数时用来判断能不能用）
        self.call_log = []            # 最近几轮工具调用的 (签名, 结果摘要, 是否出错)
        self.no_progress_streak = 0   # 连续"同样动作 + 同样结果"的次数（窗口只有 8 格装不下它）
        # 子代理相关：主代理 depth=0，子代理 depth=1（不能再派子代理）
        self.depth = 0
        self.role_prompt = ""
        self.allow_tools = None
        self.subagent_count = 0
        self.subagent_runner = None
        self.deadline = 0.0
        self.last_signature = ""
        self.repeat_count = 0
        self.incomplete = False      # 是否是被熔断/上限/停止打断的（界面据此给“继续”）
        self.effort = ""             # 本次运行实际使用的推理等级（空 = 不改动接口参数）
        self.points = KeyPoints()    # 右侧要点的实时提炼器（全量思考仍走 reasoning 事件）
        # 让工具能拿到会话状态（例如抽过谁）与事件出口（大屏、计时器）
        if tools is not None:
            try:
                tools.session = session
                tools.emit = emit
            except (AttributeError, TypeError):
                pass

    # -- 上下文管理（对照 DSH 的 compaction-basic / tool-result-pruner）--------
    def _context_window(self):
        """当前模型的上下文窗口大小（token）。模型档案里可写 context_window 覆盖。"""
        try:
            model = self.settings.resolve_model(self.session.get("model") or None)
            profile = (self.settings.agent.get("models") or {}).get(model.get("profile") or "") or {}
            value = profile.get("context_window") or self.settings.agent.get("context_window")
            if value:
                return int(value)
        except (TypeError, ValueError, KeyError):
            pass
        return context_module.DEFAULT_CONTEXT_WINDOW

    def context_pressure(self):
        """当前上下文压力：0~1。优先用接口报回来的真实 prompt_tokens，没有就估算。"""
        usage = self.session.get("usage") or {}
        tokens = int(usage.get("last_prompt_tokens") or 0)
        if not tokens:
            tokens = context_module.conversation_tokens(self.session.get("messages") or [])
        window = self._context_window()
        return (tokens / float(window) if window else 0.0), tokens, window

    def compact(self, force=False, notice="上下文接近上限，已把较早的对话压缩成摘要"):
        """压缩历史：先剪超大工具结果，再把最旧的一段总结成一条摘要。

        只有派生历史会被动，系统提示词与工具说明不碰；原始消息归档到 session["compacted"]。
        返回 True 表示真的压了。
        """
        messages = self.session.get("messages") or []
        window = self._context_window()
        keep_tokens = max(context_module.COMPACT_MIN_RETAIN_TOKENS,
                          int(window * context_module.COMPACT_RETAIN_RATIO))
        pruned, saved = context_module.prune_tool_results(messages)
        if pruned:
            self.emit({"t": "notice", "level": "info",
                       "message": "已把 %d 条过大的工具结果剪成「开头+结尾」（完整内容仍在会话文件里，"
                                  "共省下约 %d 字）。" % (pruned, saved)})
        count, cut = context_module.plan_compaction(messages, keep_tokens,
                                                   force_ratio=0.5 if force else 0)
        if count <= 0:
            return bool(pruned)
        head = messages[:cut]
        # 保留第一条 user 消息（原始任务），它会被重新插到摘要前面
        task_message = None
        for message in head:
            if message.get("role") == "user" and not message.get("autoContinue"):
                task_message = message
                break
        transcript = context_module.render_transcript(
            head, max_tokens=max(4000, int(window * context_module.SUMMARY_INPUT_RATIO)))
        if not transcript.strip():
            return bool(pruned)
        summary = self._summarize(transcript)
        if not summary:
            return bool(pruned)
        archive = self.session.setdefault("compacted", [])
        archive.append({"ts": _now(), "messages": head,
                        "summary": summary, "count": len(head)})
        del archive[:-context_module.MAX_COMPACT_ARCHIVES]
        replacement = [{
            "role": "user", "ts": _now(), "compacted": True, "autoContinue": True,
            "content": ("<compacted-summary>\n以下是本次会话较早部分的摘要"
                        "（原始消息已归档，需要细节可以重新问我）：\n%s\n</compacted-summary>"
                        % summary),
        }]
        if task_message is not None and task_message not in messages[cut:]:
            replacement.insert(0, task_message)
        self.session["messages"] = replacement + messages[cut:]
        self.session["compacted_count"] = int(self.session.get("compacted_count") or 0) + len(head)
        self._save()
        self.emit({"t": "compacted", "removed": len(head), "summary": summary[:200],
                   "kept": len(messages) - cut, "freed": sum(
                       context_module.estimate_tokens(context_module.message_text(m)) for m in head)})
        self.emit({"t": "notice", "level": "info",
                   "message": "%s：压掉 %d 条历史、保留最近 %d 条（摘要已放进对话，"
                              "原始消息仍保存在会话文件里）。"
                              % (notice, len(head), len(messages) - cut)})
        return True

    def _summarize(self, transcript):
        """让模型把一段历史写成摘要（单独一次请求，不带工具，不写入对话历史）。"""
        webui = self.settings.webui
        model = self.settings.resolve_model(self.session.get("model") or None)
        payload = {
            "model": model["model"],
            "messages": [
                {"role": "system", "content": "你是对话压缩器，只输出摘要正文。"},
                {"role": "user", "content": transcript + "\n\n" + context_module.COMPACT_INSTRUCTION},
            ],
            "max_tokens": int(context_module.SUMMARY_MAX_TOKENS),
            "stream": False,
        }
        try:
            result = self.client.stream(payload, lambda kind, text: None, self.cancel)
        except ChatError as exc:
            self.emit({"t": "notice", "level": "warn",
                       "message": "压缩历史时调用模型失败（%s），本次不压缩，继续用完整历史。"
                                  % exc.message[:120]})
            return ""
        summary = (result.get("content") or "").strip()
        usage = normalize_usage(result.get("usage"))
        self.session["usage"] = add_usage(self.session.get("usage") or empty_usage(), usage)
        return summary

    def _maybe_compact(self):
        """请求之前检查压力：超过阈值就压缩（对应 DSH 的 agent/pre-step 监听器）。"""
        if not self.settings.webui.get("auto_compact", True):
            return
        pressure, tokens, window = self.context_pressure()
        threshold = float(self.settings.webui.get("compact_threshold_ratio")
                          or context_module.COMPACT_THRESHOLD_RATIO)
        if pressure >= threshold:
            self.compact(notice="上下文已用到 %d/%d token（%.0f%%），已自动压缩较早的对话"
                                % (tokens, window, pressure * 100))

    # -- 工具循环 ---------------------------------------------------------
    def run(self, text, images=None, options=None):
        options = options or {}
        webui = self.settings.webui
        price = webui.get("price") or {}
        max_rounds = int(options.get("maxToolRounds") or webui.get("max_tool_rounds") or 1024)

        message = {"role": "user", "content": text, "ts": _now()}
        if images:
            message["images"] = list(images)[:int(webui.get("max_images") or 256)]
            self.has_images = True
        self.session["messages"].append(message)
        self.session["incomplete"] = False      # 新一轮开始，清掉上一次遗留的「继续生成」
        self._autotitle(text)
        self.active_skills = self._choose_skills(text)
        if self.active_skills:
            self.emit({"t": "skills", "names": [skill.name for skill in self.active_skills]})

        tools_on = bool(webui.get("tools_enabled", True)) and bool(self.tools)
        self.effort = resolve_effort(self.settings, self.session,
                                     options.get("reasoningEffort") or "")
        skip = set(options.get("skipTools") or [])
        limit_seconds = webui.get("max_run_seconds")
        # 0 = 不限制（默认）。老配置里可能留着 900 这种旧默认值，加载时会被 config 迁移改掉。
        limit_seconds = 0.0 if limit_seconds is None else float(limit_seconds)
        self.deadline = (time.time() + limit_seconds) if limit_seconds > 0 else 0.0
        # 工具层和桌面层都要能看见"老师点了停止"：长命令、长下载、PowerShell 据此立刻收手
        self.tools.cancel = self.cancel
        desktop_module.set_cancel(self.cancel)

        round_index = 0          # 展示用的轮次
        tool_rounds = 0          # 已执行的工具轮次
        continue_rounds = 0      # 因为输出被截断而自动续写的次数
        tool_text_rounds = 0     # 因为"把工具调用写成文字"而提醒重试的次数
        summary_asked = False    # 到轮次上限后只请模型收尾一次
        truncated_rounds = 0     # 工具参数被截断而重新生成的次数
        overflow_retries = 0     # 上下文超长后压缩重试的次数
        while True:
            if self.cancel.is_set():
                self.emit({"t": "notice", "level": "warn", "message": "已停止生成"})
                self.incomplete = True
                self.emit({"t": "stopped"})      # 前端据此立刻把界面恢复成"可以继续输入"
                break
            if self._timeout_reached():
                self.incomplete = True
                break
            self._maybe_compact()        # 上下文快到上限就先压缩，别等接口报错
            # 到轮次上限：最后请模型**只用文字**收尾一次（这一轮不带 tools，
            # 所以它不可能再发起调用）；收尾过就直接结束，把状态交回给老师。
            # 旧行为是"上限之后继续问、模型把工具调用写成文字"，界面上看着像罢工。
            summarizing = tool_rounds >= max_rounds
            if summarizing and summary_asked:
                break
            payload = self._payload(tools_on and not summarizing, skip)
            if summarizing:
                summary_asked = True
                self.session["messages"].append({
                    "role": "user", "ts": _now(), "autoContinue": True,
                    "content": WRAP_UP_PROMPT % max_rounds,
                })
                self._save()
            round_index += 1
            self.emit({"t": "round", "round": round_index})
            started = time.time()
            try:
                result = self.client.stream(payload, self._on_delta, self.cancel,
                                            on_retry=self._on_retry)
            except ChatError as exc:
                # 上下文超长：压缩一次再重试（对应 DSH 的 maxOverflowRetries）
                if exc.code == RETRY_CONTEXT and overflow_retries < 1:
                    overflow_retries += 1
                    self.emit({"t": "notice", "level": "warn",
                               "message": "接口提示上下文超长，正在压缩较早的历史后重试…"})
                    if self.compact(force=True, notice="上下文超长，已强制压缩"):
                        round_index -= 1
                        continue
                    self.emit({"t": "notice", "level": "warn",
                               "message": "已经没有可压缩的历史了：这次请求仍然过长，"
                                          "可以把任务拆小，或在设置里换上下文更大的模型。"})
                if self._retry_without(exc, payload, skip):
                    round_index -= 1
                    continue
                if self.cancel.is_set() or "已取消" in exc.message:
                    self.emit({"t": "notice", "level": "warn", "message": "已停止生成"})
                    self.emit({"t": "stopped"})
                    self.session["incomplete"] = True
                    self.session["messages"].append({
                        "role": "assistant", "content": "", "ts": _now(),
                        "stopped": True, "model": payload.get("model"),
                    })
                    self._save()
                    self.emit({"t": "done", "session": _session_summary(self.session),
                               "usage": self.session.get("usage"), "canContinue": True})
                    return self.session
                # 这一轮一个字都没写出来（网络中断、接口报错），没有“没写完的回答”可以继续，
                # 所以不给「继续生成」，界面会显示错误提示和「重新生成」。
                self.emit({"t": "error", "message": exc.message})
                self.session["incomplete"] = False
                self.session["messages"].append({
                    "role": "assistant", "content": "", "ts": _now(),
                    "error": exc.message, "model": payload.get("model"),
                })
                self._save()
                self.emit({"t": "done", "session": _session_summary(self.session),
                           "usage": self.session.get("usage"), "canContinue": False})
                return self.session

            elapsed = round(time.time() - started, 1)
            usage = normalize_usage(result.get("usage"))
            cost = estimate_cost(usage, price)
            self.session["usage"] = add_usage(self.session.get("usage") or empty_usage(), usage)
            self.session["usage"]["cost"] = round(
                float(self.session["usage"].get("cost") or 0) + cost["cost"], 6)
            self.session["usage"]["rounds"] = int(self.session["usage"].get("rounds") or 0) + 1
            # 记下接口报回来的真实 prompt_tokens：上下文压力用它判断，比估算准
            if usage.get("prompt_tokens"):
                self.session["usage"]["last_prompt_tokens"] = int(usage["prompt_tokens"])

            finish_reason = result.get("finish_reason") or ""
            message = {
                "role": "assistant",
                "content": result.get("content") or "",
                "ts": _now(),
                "model": payload.get("model"),
                "seconds": elapsed,
                "reasoning": result.get("reasoning") or "",
                "usage": usage,
                "cost": cost["cost"],
            }
            calls = result.get("tool_calls") or []
            # 社区已确认的坑（hermes-agent #14443 / #35151）：流式输出被截断时，
            # tool_call 的 arguments 会是半截 JSON。旧做法把它原样存进历史 →
            # 之后每次请求都带着这个坏调用，重试循环被卡死，或者被静默当成成功。
            # 这里：先把坏调用挑出来，**不写进历史**，而是把这一步重发/续写；
            # 实在不行才带着"参数不完整"的说明交回给老师。
            broken = [call for call in calls if not _call_args_ok(call)]
            repaired = 0
            for call in broken[:]:
                fixed = repair_tool_args(((call or {}).get("function") or {}).get("arguments"))
                if fixed is None or not self._args_complete(_call_name(call), fixed):
                    continue
                # 截断的参数能修好就用（社区做法 hermes #35574）：少跑一次模型调用，
                # 但必须告诉模型"参数被截断过"，让它自己核对
                call["function"]["arguments"] = json.dumps(fixed, ensure_ascii=False)
                broken.remove(call)
                repaired += 1
            if repaired:
                self.emit({"t": "notice", "level": "warn",
                           "message": "有 %d 个工具调用的参数被截断，已按可修复的部分继续执行"
                                      "（请核对参数是否完整）。" % repaired})
            if broken and truncated_rounds < MAX_TRUNCATED_RETRY:
                truncated_rounds += 1
                if result.get("content"):
                    self._on_delta("content", result["content"])
                length_case = is_length_truncation(result.get("finish_reason"), broken)
                if truncated_rounds == 1:
                    hint = TRUNCATED_HINT_LENGTH if length_case else TRUNCATED_HINT
                else:
                    hint = TRUNCATED_HINT_CONCISE
                self.emit({"t": "notice", "level": "warn",
                           "message": "这次工具调用的参数被截断了（%s），已丢弃这半截调用并让模型"
                                      "重新生成（第 %d 次）。"
                                      % ("超出单次输出长度上限" if length_case else "输出中途断了",
                                         truncated_rounds)})
                self.session["messages"].append({
                    "role": "user", "ts": _now(), "autoContinue": True, "content": hint,
                })
                self._save()
                continue
            if broken:
                calls = [call for call in calls if _call_args_ok(call)]
                self.emit({"t": "notice", "level": "warn",
                           "message": "工具参数连续 %d 次被截断，已跳过这些调用；"
                                      "可以把任务拆小一点，或把 max_tokens 调大。"
                                      % MAX_TRUNCATED_RETRY})
            for call in calls:
                if not call.get("id"):
                    call["id"] = "call_" + uuid.uuid4().hex[:8]
            # 收尾轮（已到上限、没带 tools）模型仍然"调用"了工具：保留调用但不执行，
            # 也不要把模型那句描述当成最终答复，直接交回状态并说明可以继续。
            if summarizing and calls:
                message["tool_calls"] = calls
                self.session["messages"].append(message)
                self._save()
                self.emit({"t": "turn", "role": "assistant", "message": message,
                           "totals": self.session["usage"], "cost": cost})
                self.emit({"t": "notice", "level": "warn",
                           "message": "已达到工具轮次上限（%d）：这一轮的工具调用**没有执行**。"
                                      "任务多半还没做完——点消息下面的「继续」就能接着做，"
                                      "也可以在设置里把「工具轮次上限」调大（画图这类多步任务建议 40 以上）。"
                                      % max_rounds})
                self.incomplete = True
                break
            if calls:
                message["tool_calls"] = calls
            self.session["messages"].append(message)
            self._save()
            self.emit({
                "t": "turn", "role": "assistant",
                "message": message,
                "totals": self.session["usage"],
                "cost": cost,
            })

            if not calls:
                self._check_image_support(message.get("content") or "")
                tool_names = [spec_name(spec) for spec in (self.tools.specs() if self.tools else [])]
                looks_like_text = _looks_like_tool_text(message.get("content"), tool_names)
                if summarizing:
                    # 收尾轮拿到了纯文字总结（正常情况）就收工；如果它还在"写"工具调用，
                    # 说明它没能收尾——同样收工，但把原因讲清楚，并把「继续」留给老师。
                    if looks_like_text:
                        self.emit({"t": "notice", "level": "warn",
                                   "message": "已达到工具轮次上限（%d），模型只能用文字描述下一步；"
                                              "任务多半还没做完——点下面的「继续」接着做，"
                                              "或在设置里把「工具轮次上限」调大。" % max_rounds})
                    self.incomplete = True
                    break
                if looks_like_text and tool_text_rounds < MAX_TOOL_TEXT_RETRY:
                    tool_text_rounds += 1
                    self.emit({"t": "notice", "level": "warn",
                               "message": "模型把工具调用写成了文字、并没有真正执行；"
                                          "已提醒它改用工具调用重试（第 %d 次）。" % tool_text_rounds})
                    self.session["messages"].append({
                        "role": "user", "ts": _now(), "autoContinue": True,
                        "content": TOOL_TEXT_NUDGE,
                    })
                    self._save()
                    continue
                elif looks_like_text:
                    self.emit({"t": "notice", "level": "warn",
                               "message": "模型一直只把工具调用写在文字里（没有真正发起调用），"
                                          "已停止重试。可以点「继续」再试一次，"
                                          "或换用 deepseek-flash 等支持工具调用的模型。"})
                    self.incomplete = True
                    break
                if self._check_round_guard(message):
                    self.incomplete = True
                    break
                # 被截断、或者干脆一个字都没写：自动接着写完，不让用户手动再发一句“继续”
                nudge = _continuation_nudge(finish_reason, message.get("content") or "")
                if nudge and continue_rounds < MAX_AUTO_CONTINUE:
                    continue_rounds += 1
                    self.emit({"t": "notice", "level": "warn",
                               "message": nudge["notice"] % continue_rounds})
                    self.session["messages"].append({
                        "role": "user", "ts": _now(), "autoContinue": True,
                        "content": nudge["prompt"],
                    })
                    self._save()
                    continue
                if nudge:
                    self.emit({"t": "notice", "level": "warn",
                               "message": "已经自动续写 %d 次仍未写完，已停止自动续写。"
                                          "可以点「继续」接着写，或把任务拆小一点。"
                                          % MAX_AUTO_CONTINUE})
                    self.incomplete = True
                break

            if self._check_round_guard(message):
                self.incomplete = True
                break

            if summarizing:
                # 收尾轮里模型仍然要求调用工具（只可能发生在它无视"不要再调工具"时）：
                # 保留调用、不执行，把状态交回给老师
                self.incomplete = True
                break

            before = len(self.session["messages"])
            self._run_tool_round(calls)
            tool_rounds += 1
            # 循环守卫：看这一轮和最近几轮的"签名 + 结果"，先提醒、屡劝不改才停
            round_signature = "|".join(sorted(
                "%s(%s)" % (_call_name(call), ((call.get("function") or {}).get("arguments") or ""))
                for call in calls))
            tool_messages = [message for message in self.session["messages"][before:]
                             if message.get("role") == "tool"]
            if self._loop_guard(round_signature, tool_messages):
                self.incomplete = True
                break

        self.session["incomplete"] = bool(self.incomplete)
        self._save()
        self.emit({"t": "done", "session": _session_summary(self.session),
                   "usage": self.session.get("usage"), "canContinue": self.incomplete})
        return self.session

    # -- 熔断 -------------------------------------------------------------
    def _record_tool_round(self, signature, tool_messages):
        """记录这一轮工具调用的"签名 + 结果摘要"，用于**结果感知**的循环检测。

        社区共识（langchain #36139「progress-aware termination」、zeroclaw #2152
        「result-aware loop detection」、oh-my-pi #3971「cross-turn loop guard」）：
        * 只看参数相同会误判——同样的调用可能每次都拿到不同结果，那是在推进；
        * 只看"连续相同"会漏掉 A-B-A-B 的交替循环，必须在**窗口内**统计；
        * 一直返回同样错误的调用最可疑（claude-code #30150），要更早提醒。
        返回 (同一签名在窗口内出现的次数, 连结果都一样的次数, 本轮是否全是错误)。
        """
        digest = hashlib.sha1("\n".join(
            str(message.get("content") or "") for message in tool_messages
        ).encode("utf-8", "replace")).hexdigest()[:16]
        errors = bool(tool_messages) and all(
            message.get("status") == "error" for message in tool_messages)
        self.call_log.append((signature, digest, errors))
        del self.call_log[:-CALL_LOG_WINDOW]
        window = self.call_log[-CALL_LOG_WINDOW:]
        same_signature = sum(1 for item in window if item[0] == signature)
        windowed = sum(1 for item in window if item[0] == signature and item[1] == digest)
        # 连续"同样动作+同样结果"的计数：窗口只有 8 格，光靠窗口计数永远到不了停止阈值，
        # 所以另外维护一个连续计数，两者取大（窗口负责抓 A-B-A-B 交替循环）。
        if len(self.call_log) >= 2 and window[-2][0] == signature and window[-2][1] == digest:
            self.no_progress_streak += 1
        else:
            self.no_progress_streak = 1
        return same_signature, max(windowed, self.no_progress_streak), errors

    def _loop_guard(self, signature, tool_messages):
        """工具轮结束后的循环守卫：先提醒、屡劝不改才停（对照 DSH 的 repeat-tool-reminder）。

        ★ 熔断开关（设置里的"熔断"）**默认关**：关着就直接放行，一句提醒都不发。
          老师在设置里把阈值改成多少，state 里就照原样报多少（否则设置界面会把它读成 0，
          一保存就把老师填的阈值冲掉）；"关掉"这件事只在这里生效。
        """
        if self.settings.webui.get("loop_breaker") is not True:
            return False
        same_signature, same_result, errors = self._record_tool_round(signature, tool_messages)
        start = max(2, int(self.settings.webui.get("loop_repeat_limit") or 3))
        # 出错又反复重试的，阈值收紧一档（同样错误连着来两三次就该换方法了）
        threshold = max(2, start - 1) if errors else start
        # 交替循环（A-B-A-B）不会让连续计数增长，靠"窗口几乎被同一两个签名占满 + 已经提醒过 3 次"来收尾
        alternating = (len(self.call_log) >= CALL_LOG_WINDOW
                       and same_signature >= CALL_LOG_WINDOW - 1
                       and self.repeat_reminders >= 3)
        if same_result < threshold and same_signature < threshold + 2 and not alternating:
            return False
        if same_result >= threshold + 22 or alternating:
            self.incomplete = True          # 停下来也要能接着做
            self.emit({"t": "circuit", "reason": "repeat",
                       "message": "同一组动作已经重复多次、结果也没有任何变化，已先停下来（提醒过 %d 次）。"
                                  "点「继续生成」可以接着做；也可以补充信息、换个说法，"
                                  "或把任务拆成更小的步骤再试。" % self.repeat_reminders})
            return True
        if same_result >= threshold:
            step = same_result - threshold
            if step < 3 and self.repeat_reminders <= step:
                self.repeat_reminders = step + 1
                detail = ("这次调用一直返回同样的错误" if errors
                          else "这次调用返回的结果和上次一模一样")
                self.emit({"t": "notice", "level": "warn",
                           "message": "检测到没有进展的重复（第 %d 次，%s）。已提醒模型换个做法。"
                                      % (same_result, detail)})
                self.session["messages"].append({
                    "role": "user", "ts": _now(), "autoContinue": True,
                    "content": ("（系统：你刚刚第 %d 次做了同一件事，而且%s。"
                                "请先停下来分析：如果是命令/工具用法不对，换参数或换工具；"
                                "如果是前提不成立，说明原因并结束；"
                                "不要再用同样的方式重试。重复内容：%s）"
                                % (same_result, detail, signature[:200])),
                })
                self._save()
        return False

    def _timeout_reached(self):
        if self.deadline and time.time() > self.deadline:
            self.cancel.set()
            self.incomplete = True
            self.emit({"t": "circuit", "reason": "timeout",
                       "message": "本次运行到了设置里的时长上限（%d 秒），已经先停下。"
                                  "这次不是出错，是时间到了：点「继续生成」可以接着做；"
                                  "想让长任务一口气跑完，就在设置里把「单次运行最长秒数」改成 0（不限制）。"
                                  % int(self.settings.webui.get("max_run_seconds") or 0)})
            return True
        return False

    def _check_round_guard(self, message):
        """检测思考打转：反复做同一件事时先提醒，屡劝不改才停。

        这里刻意**不因为"思考太长"就停下来**：开着思考模式做一整套课件，单轮思考
        上万字是正常的，"想得久"不等于"卡住了"。想得长只提示一次，并照实告诉老师
        这次思考被算进了上下文（真正管上下文的是自动压缩）。
        """
        webui = self.settings.webui
        limit_chars = int(webui.get("max_round_reasoning_chars") or 60000)
        reasoning = message.get("reasoning") or ""
        if len(reasoning) > limit_chars and not self.reasoning_notice_sent:
            self.reasoning_notice_sent = True
            self.emit({"t": "notice", "level": "warn",
                       "message": ("这一轮模型想了 %d 字（超过 %d 字的提醒线）。想得久不一定是卡住，"
                                   "先继续跑；如果确实一直在原地打转，重复动作的提醒会先出现。"
                                   % (len(reasoning), limit_chars))})

        # 熔断开关（设置里那一项）默认关：关着就只保留"想得太久"的提醒，不做重复判定。
        # 老师要的就是"别动不动就说我在打转"。
        if webui.get("loop_breaker") is not True:
            return False

        calls = message.get("tool_calls") or []
        if calls:
            names = [(call.get("function") or {}).get("name") or "" for call in calls]
            # 只读幂等工具重复调用是正常操作（画一笔、截屏看一眼），不当成打转
            if names and all(name in IDEMPOTENT_TOOLS for name in names):
                self.last_signature = ""
                self.repeat_count = 0
                return False
            signature = "|".join(sorted(
                "%s(%s)" % ((call.get("function") or {}).get("name"),
                            (call.get("function") or {}).get("arguments")) for call in calls))
        else:
            content = (message.get("content") or "").strip()
            # 短回答（"好的""继续""嗯"）在正常对话里会连着重现，不能据此熔断
            if len(content) < 15:
                self.last_signature = ""
                self.repeat_count = 0
                return False
            signature = "text:" + re.sub(r"\s+", "", content)[:200]
        if not signature or signature == "text:":
            return False

        if signature == self.last_signature:
            self.repeat_count += 1
        else:
            self.last_signature = signature
            self.repeat_count = 1
        start = max(2, int(webui.get("loop_repeat_limit") or 3))
        # 对照 DSH 的 dsh-repeat-tool-reminder：重复**先提醒**（建议性，不阻止）。
        # 真停下来要满足"很久都没变过"：默认 3 次提醒 + 再重复到 25 次。
        # 原先是 12 次就停，老师反馈"太容易莫名其妙熔断"——画图、批量改名这类活
        # 本来就会出现"同样的调用连着几次"，那是正常干活，不是打转。
        stop_at = start + 22
        if self.repeat_count >= stop_at:
            self.incomplete = True          # 停下来也要能一键接着做，不是死路
            self.emit({"t": "circuit", "reason": "repeat",
                       "message": "同一组动作已经连续重复 %d 次、结果也一直没变，已先停下来（已经提醒过 %d 次）。"
                                  "点「继续生成」可以接着做，或者换个说法/把任务拆小一点。"
                                  % (self.repeat_count, self.repeat_reminders)})
            return True
        if self.repeat_count >= start:
            step = self.repeat_count - start
            if step < 3 and self.repeat_reminders <= step:
                self.repeat_reminders = step + 1
                detail = "工具与参数：%s" % signature[:300] if step >= 1 else ""
                self.emit({"t": "notice", "level": "warn",
                           "message": "检测到连续重复同样的动作（第 %d 次）。已提醒模型先分析上一次结果，"
                                      "改用别的方法或结束任务。" % self.repeat_count})
                self.session["messages"].append({
                    "role": "user", "ts": _now(), "autoContinue": True,
                    "content": ("（系统：你刚刚第 %d 次用同样的参数调用同一个工具，可能陷入了循环。"
                                "请先分析上一次的结果：如果已经成功就继续下一步；如果失败就换一种方法"
                                "（换参数、换工具、或先检查前提）；如果确实无法完成就说明原因并结束。%s）"
                                % (self.repeat_count, ("重复内容：" + detail) if detail else "")),
                })
                self._save()
        return False

    # -- 工具执行 ---------------------------------------------------------
    def _run_tool_round(self, calls):
        """执行一轮工具调用：子代理并行跑，其余按顺序；工具消息仍按原顺序写入会话。"""
        # 先给每个调用定好 id：有的网关（小模型/自建代理）不给 id，
        # 而 _call_key 每次都会临时编一个，并行分支存/取的键就对不上，工具结果会被丢掉。
        for call in calls:
            if not call.get("id"):
                call["id"] = "call_" + uuid.uuid4().hex[:8]
        # 让工具层也能看到"老师点了停止"：长命令/长下载/PowerShell 会据此立刻收手
        self.tools.cancel = self.cancel
        desktop_module.set_cancel(self.cancel)
        subagents = [call for call in calls if _call_name(call) == "run_subagent"]
        results = {}
        pending_images = []
        if len(subagents) > 1:
            threads = []
            for call in subagents:
                thread = threading.Thread(target=lambda target=call: results.__setitem__(
                    _call_key(target), self._run_tool_call(target)))
                thread.daemon = True
                thread.start()
                threads.append(thread)
            for call in calls:
                if call not in subagents:
                    results[_call_key(call)] = self._run_tool_call(call)
            for thread in threads:
                thread.join()
        else:
            for call in calls:
                results[_call_key(call)] = self._run_tool_call(call)
        for call in calls:
            meta, tool_message = results.get(_call_key(call), (None, None))
            if tool_message is None:
                continue
            self.session["messages"].append(tool_message)
            self._save()
            pending_images.append(meta)
        # 图片要等这一轮的**所有**工具结果都写进历史之后再注入：
        # 接口要求 assistant(tool_calls) 后面紧跟每个 tool_call_id 的结果，
        # 中间插一条 user 附图会直接 400（insufficient tool messages following tool_calls message）。
        for meta in pending_images:
            if meta and meta.get("inject_image"):
                self._inject_image(meta)
                self._save()

    def _execute_tool(self, call):
        meta, tool_message = self._run_tool_call(call)
        if tool_message is not None:
            self.session["messages"].append(tool_message)
        return meta

    # -- 内部 -------------------------------------------------------------
    def _retry_without(self, error, payload, skip):
        """兼容不同网关：不支持工具或参数时报错，降级重试一次。"""
        message = (error.message or "").lower()
        if error.status == 400 and "tool_call_id" in message:
            # 历史里工具配对不完整（旧版本注入图片时插在了工具结果中间，或整轮被中断）。
            # 这不是"模型不支持工具"，降级成纯对话也治不好；to_api_messages 每次都会
            # 自动整理历史，所以直接重试一次即可（第一次撞上的是还没修过的历史）。
            self.emit({"t": "notice", "level": "warn",
                       "message": "历史里的工具调用配对不完整，已自动整理后重试（不影响后续对话）。"})
            return True
        if error.status == 400 and ("tool" in message or "function" in message):
            if payload.get("tools"):
                self.emit({"t": "notice", "level": "warn",
                           "message": "当前模型不支持工具调用，已切换为纯对话模式。"})
                skip.add("__tools__")
                return True
        if error.status == 400 and "temperature" in message:
            if "temperature" in payload:
                self.emit({"t": "notice", "level": "warn",
                           "message": "当前模型不支持 temperature 参数，已忽略该项设置。"})
                skip.add("__temperature__")
                return True
        if error.status == 400 and any(word in message for word in
                                       ("reasoning_effort", "reasoning", "effort", "thinking")):
            if "reasoning_effort" in payload:
                self.emit({"t": "notice", "level": "warn",
                           "message": "当前接口不支持 reasoning_effort 参数，已按“默认”推理等级重试。"})
                skip.add("__effort__")
                return True
        return False

    def _payload(self, tools_on, skip):
        webui = self.settings.webui
        model = self.settings.resolve_model(self.session.get("model") or None)
        messages = trim_messages(self.session["messages"], int(webui.get("trim_messages") or 200))
        with_tools = bool(tools_on and "__tools__" not in skip and self.tools)
        payload = {
            "model": model["model"],
            # 历史里带 tool_calls 的 assistant 消息必须回传 reasoning_content（漏了就 400），
            # 这件事由 to_api_messages 保证，和"本轮带不带工具"无关。
            "messages": to_api_messages(self._system_prompt(), messages,
                                        on_repair=self._notice_history_repair,
                                        max_images=int(webui.get("max_images") or 256),
                                        image_budget=IMAGE_PAYLOAD_BUDGET_BYTES,
                                        on_notice=self._notice_image_budget),
        }
        if self.effort and "__effort__" not in skip:
            payload["reasoning_effort"] = self.effort
        # 官方 thinking 开关：{"thinking": {"type": "enabled"/"disabled"}}
        thinking = webui.get("thinking")
        if thinking in ("enabled", "disabled") and "__thinking__" not in skip:
            payload["thinking"] = {"type": thinking}
        temperature = webui.get("temperature")
        if temperature is not None and temperature != "" and "__temperature__" not in skip:
            payload["temperature"] = float(temperature)
        max_tokens = int(webui.get("max_tokens") or 0)
        if max_tokens > 0:
            payload["max_tokens"] = max_tokens
        if with_tools:
            specs = [spec for spec in self.tools.specs() if self._tool_allowed(spec_name(spec))]
            if specs:
                payload["tools"] = specs
                payload["tool_choice"] = "auto"
        return payload

    def _tool_allowed(self, name):
        """子代理不能再派子代理；allow_tools 用于给子代理收窄工具集。"""
        if name == "run_subagent":
            return self.depth == 0
        if self.allow_tools is not None:
            return name in self.allow_tools
        return True

    def _choose_skills(self, text):
        """会话里手动启用的技能优先，加上标了 always 的硬性技能，再加上按输入自动匹配的。

        always 的技能**先放**：它是"这条规矩必须一直在"，
        不能被关键词漏命中或被后续顺序挤掉。
        """
        chosen = []
        seen = set()
        for skill in self.skills:
            if skill.always and skill.name not in seen:
                seen.add(skill.name)
                chosen.append(skill)
        forced_names = self.session.get("skills") or []
        if not forced_names:
            # 老师这台要"所有技能默认就启用"：会话里没手动选过就全用上
            forced_names = [skill.name for skill in self.skills]
        for name in forced_names:
            for skill in self.skills:
                if skill.name == name and name not in seen:
                    seen.add(name)
                    chosen.append(skill)
        for skill in select_skills(self.skills, text):
            if skill.name not in seen:
                seen.add(skill.name)
                chosen.append(skill)
        return chosen

    def _scope_note(self):
        """把"能不能改工作区外的文件"如实告诉模型，避免权限已放开却还在反复索要授权。"""
        if self.settings.webui.get("allow_outside_workspace"):
            return ("允许读写任意绝对路径，包括工作目录之外的盘符、桌面和共享盘；"
                    "用户已经给出明确路径时直接读写，不要反复索要授权。")
        return ("只允许读写工作区 %s 内的文件；工作区之外的路径会被工具拒绝，"
                "遇到这种情况要说明原因，不要反复重试。" % self.settings.workspace_root())

    def _system_prompt(self):
        webui = self.settings.webui
        if self.role_prompt:
            template = SUBAGENT_PROMPT.replace("{{role}}", self.role_prompt)
        else:
            template = webui.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
        system = template.replace("{{workspace}}", self.settings.workspace_root())
        system = system.replace("{{file_scope}}", self._scope_note())
        system = system.replace("{{os}}", "Windows 7 x64")
        system = system.replace("{{date}}", time.strftime("%Y-%m-%d"))
        parts = [system]
        if not self.role_prompt:
            parts.append("文件读写范围：%s" % self._scope_note())
        # run() 里已经算过一次就用缓存，保证与 skills 事件一致；单独调用时现算
        skills = self.active_skills or self._choose_skills(self._recent_user_text())
        prompt = skills_prompt(skills)
        if prompt:
            parts.append(prompt)
        context = self.session.get("context") or []
        if context:
            parts.append("本地上下文（用户已加载，可直接引用）：")
            for item in context:
                parts.append("\n--- %s: %s ---\n%s" % (
                    item.get("kind") or "context", item.get("source") or "", item.get("content") or ""))
        return "\n".join(parts)

    def _recent_user_text(self):
        for message in reversed(self.session["messages"]):
            if message.get("role") == "user":
                return message.get("content") or ""
        return ""

    def _on_delta(self, kind, text):
        self.emit({"t": kind, "d": text})
        if kind == "reasoning":
            # 右侧"思考链路"要的是要点，不是整篇思考：这里边流边提炼，单独发 trace_point 事件
            for point in self.points.feed(text):
                self.emit({"t": "trace_point", "d": point})

    def _run_tool_call(self, call):
        """执行一次工具调用，返回 (meta, 工具消息)。不写会话，便于子代理并行执行。"""
        name = (call.get("function") or {}).get("name") or ""
        args = parse_tool_arguments((call.get("function") or {}).get("arguments"))
        call_id = call.get("id") or ("call_" + uuid.uuid4().hex[:8])
        call["id"] = call_id
        self.emit({"t": "tool_start", "id": call_id, "name": name, "args": args})
        started = time.time()

        if not self._tool_allowed(name):
            output = ("子代理不能再派子代理。" if name == "run_subagent"
                      else "当前运行不允许调用工具 %s。" % name)
            self.emit({"t": "tool_end", "id": call_id, "name": name, "status": "error",
                       "output": output})
            return None, _tool_message(call, output, "error", None)

        if name == "run_subagent":
            meta, output = self._spawn_subagent(call, call_id, args, started)
            status = "ok" if not meta.get("error") else "error"
            self.emit({"t": "tool_end", "id": call_id, "name": name, "status": status,
                       "output": output, "meta": meta})
            return meta, _tool_message(call, output, status, meta)

        try:
            verdict = self.tools.authorize(name, args)
        except Exception as exc:
            verdict = {"allow": False, "output": "工具调用参数错误：%s" % exc, "status": "error"}
        if verdict.get("need_confirm"):
            self.emit({"t": "confirm", "id": call_id, "name": name, "args": args,
                       "reason": verdict.get("reason") or "该操作需要确认"})
            approved = bool(self.confirmer(call_id, name, args))
            if not approved:
                self.emit({"t": "tool_end", "id": call_id, "name": name, "status": "denied",
                           "output": "用户拒绝了该操作。"})
                return None, _tool_message(call, "用户拒绝了该操作。", "denied", None)
        if not verdict.get("allow"):
            output = verdict.get("output") or "工具被拒绝"
            self.emit({"t": "tool_end", "id": call_id, "name": name,
                       "status": verdict.get("status") or "error", "output": output})
            return None, _tool_message(call, output, verdict.get("status") or "error", None)
        try:
            output, meta = self.tools.run(name, args)
            status = "ok"
        except Exception as exc:
            output, meta, status = "执行失败：%s" % exc, None, "error"
        seconds = round(time.time() - started, 2)
        if self.cancel.is_set():
            # 老师在工具跑到一半时点了停止：结果不要再写进会话了（否则被停掉的
            # 那次运行会跟新的一轮抢着改同一份历史），界面给个明确的"已停止"。
            self.emit({"t": "tool_end", "id": call_id, "name": name, "status": "stopped",
                       "output": "已被老师停止，这个结果没有采用。"})
            return None, None
        if status == "ok" and meta is not None:
            meta = dict(meta)
            meta["seconds"] = seconds
        self.emit({"t": "tool_end", "id": call_id, "name": name, "status": status,
                   "output": output, "meta": meta})
        return meta, _tool_message(call, output, status, meta)

    def _spawn_subagent(self, call, call_id, args, started):
        """派一个子代理去干一件相对独立的事。"""
        webui = self.settings.webui
        limit = int(webui.get("max_subagents") or 4)
        task = str(args.get("task") or "").strip()
        role = str(args.get("role") or "通用助手").strip() or "通用助手"
        self.emit({"t": "sub_start", "id": call_id, "task": task, "role": role})

        if not task:
            self.emit({"t": "sub_done", "id": call_id, "status": "error"})
            return {"error": "缺少 task"}, "子代理失败：没有给出任务内容。"
        if self.subagent_runner is None:
            self.emit({"t": "sub_done", "id": call_id, "status": "error"})
            return {"error": "未配置"}, "当前运行环境不支持子代理。"
        if self.subagent_count >= limit:
            self.emit({"t": "sub_done", "id": call_id, "status": "denied"})
            return ({"error": "超出上限"},
                    "本次运行最多派 %d 个子代理，已达上限。请自己完成剩余工作。" % limit)

        self.subagent_count += 1
        try:
            max_rounds = int(args.get("max_rounds") or webui.get("subagent_max_rounds") or 256)
        except (TypeError, ValueError):
            max_rounds = int(webui.get("subagent_max_rounds") or 256)
        try:
            result = self.subagent_runner(task=task, role=role, max_rounds=max_rounds,
                                          parent_id=call_id)
        except Exception as exc:  # noqa: BLE001
            self.emit({"t": "sub_done", "id": call_id, "status": "error"})
            return {"error": str(exc)}, "子代理执行失败：%s" % exc

        seconds = round(time.time() - started, 2)
        usage = result.get("usage") or {}
        self.emit({"t": "sub_done", "id": call_id, "status": result.get("status") or "ok",
                   "summary": result.get("text") or "", "rounds": result.get("rounds"),
                   "usage": usage, "seconds": seconds})
        summary = (result.get("text") or "").strip() or "（子代理没有给出结论）"
        output = ("子代理「%s」已完成（%d 轮，用时 %.1fs）。\n\n结论：\n%s"
                  % (role, int(result.get("rounds") or 0), seconds, summary))
        meta = {"kind": "subagent", "role": role, "task": task,
                "rounds": result.get("rounds"), "usage": usage, "seconds": seconds,
                "summary": summary}
        return meta, output

    def _args_complete(self, name, values):
        """修复后的参数是否满足该工具的必填项（不满足就别用，交给重试）。"""
        if not isinstance(values, dict):
            return False
        required = self._required_args.get(name)
        if required is None:
            required = []
            for spec in (self.tools.specs() if self.tools else []):
                if spec_name(spec) == name:
                    required = ((spec.get("function") or {}).get("parameters") or {}).get("required") or []
                    break
            self._required_args[name] = required
        return all(key in values and values[key] not in (None, "") for key in required)

    def _on_retry(self, info):
        """接口抖动时的重试提示：已吐过字就先让界面把这次的部分输出作废（避免重复）。"""
        self.emit({"t": "retry", "attempt": info["attempt"], "max": info["max"],
                   "delay": round(info["delay"], 1), "code": info["code"],
                   "reset": bool(info["emitted"])})
        if info["emitted"]:
            self.emit({"t": "retry_reset"})
        self.emit({"t": "notice", "level": "warn",
                   "message": "接口%s（第 %d/%d 次重试，%.1f 秒后重试）%s"
                              % (_retry_label(info["code"]), info["attempt"], info["max"],
                                 info["delay"],
                                 "：这次已写出的内容会重来一遍，不是重复输出。" if info["emitted"] else "。")})

    def _notice_image_budget(self, message):
        """图片相关提醒（总量超预算 / 达到张数上限）本次运行只说一次，别刷屏。"""
        if getattr(self, "image_notified", False):
            return
        self.image_notified = True
        self.emit({"t": "notice", "level": "warn", "message": message})

    def _notice_history_repair(self, notes):
        """历史里工具配对不完整时，只在本次运行里提醒一次，别每轮都刷。"""
        if self.repair_notified:
            return
        self.repair_notified = True
        self.emit({"t": "notice", "level": "warn",
                   "message": "检测到历史里的工具调用配对不完整（旧版本留下的），已自动整理：%s"
                              % "；".join(notes)})

    def _inject_image(self, meta):
        """把工具读到的图片作为下一轮的用户附图注入，模型才能真正“看见”。"""
        path = meta.get("inject_image")
        if not path or path in self.injected:
            return
        limit = int(self.settings.webui.get("max_images") or 256)
        if len(self.injected) >= limit:
            # 只提醒一次：一摞照片可能超限几十次，逐张提醒会把对话刷满
            self._notice_image_budget("本轮附加的图片已达上限（%d 张），后面的图片不再注入。"
                                      "需要更多可以在设置里调大「单条消息最多注入几张图片」。" % limit)
            return
        self.injected.append(path)
        name = os.path.basename(path)
        self.session["messages"].append({
            "role": "user",
            "content": "（系统：以下是工具读取的图片 %s，请基于图片内容继续。）" % name,
            "images": [{"path": path, "name": name, "mime": image_mime(path)}],
            "ts": _now(),
            "injected": True,
        })
        self.has_images = True
        self.emit({"t": "image", "path": path, "name": name, "source": "tool"})

    def _check_image_support(self, answer):
        """模型收到图片却答“看不到”，基本可以判定它不支持图片输入。"""
        if not self.has_images or not looks_like_image_refusal(answer):
            return
        self.emit({"t": "notice", "level": "warn",
                   "message": "当前模型似乎不支持图片输入，请在顶部切换到 deepseek-flash 后重试。"})

    def _autotitle(self, text):
        title = self.session.get("title") or ""
        if title and title != "新会话":
            return
        clean = _clean(text)
        self.session["title"] = clean[:24] or "新会话"

    def _save(self):
        # 被停止之后不再落盘：此时老师在等界面恢复，可能已经在发下一条消息了；
        # 这条"僵尸运行"再写一次会话文件，会把新的一轮覆盖掉。
        if self.cancel is not None and self.cancel.is_set():
            return
        if self.store is not None:
            self.store.save(self.session)


def _tool_message(call, output, status, meta):
    """构造一条 tool 角色消息（与 assistant 的 tool_calls 配对）。"""
    name = (call.get("function") or {}).get("name") or ""
    return {
        "role": "tool",
        "tool_call_id": call.get("id") or "",
        "name": name,
        "content": output if output else "[空]",
        "status": status,
        "meta": meta,
        "ts": _now(),
    }


# --------------------------------------------------------------------------
# 思考要点提炼
# --------------------------------------------------------------------------

# 判断"这一句是决策/结论"的信号词：模型思考里的这些句子通常就是要点
POINT_MARKERS = ("先", "需要", "应该", "可以", "不能", "必须", "因为", "所以", "因此",
                 "发现", "结论", "方案", "下一步", "问题", "注意", "检查", "确认", "改用",
                 "建议", "原因", "导致", "修复", "风险", "假设", "计划", "如果", "那么",
                 "然而", "但是", "另外", "同时")


class KeyPoints(object):
    """把流式思考压成要点列表（右侧"思考链路"用），全量思考仍然照常送到折叠栏。

    规则很朴素，但都是可解释的：按句号/换行切句 → 去掉语气词 → 长度合适且带信号词、
    或明显是列表项的句子才算要点 → 去重 → 最多 8 条、每条截到 80 字。
    """

    MAX_POINTS = 8
    MAX_CHARS = 80
    MIN_CHARS = 6

    def __init__(self):
        self.buffer = ""
        self.points = []
        self.seen = set()

    def feed(self, text):
        """喂入一段思考增量，返回这次新产生的要点（0 或 1 条，避免刷屏）。"""
        self.buffer += text or ""
        produced = []
        while True:
            cut = self._split_at()
            if cut < 0:
                break
            sentence = self.buffer[:cut]
            self.buffer = self.buffer[cut:]
            point = self._pick(sentence)
            if point:
                produced.append(point)
        # 缓冲太长又没有句末标点（英文思考很常见）时也切一刀，避免要点一直不出来
        if len(self.buffer) > 400:
            point = self._pick(self.buffer)
            self.buffer = ""
            if point:
                produced.append(point)
        return produced

    def _split_at(self):
        for mark in ("。", "\n", "；", ";", "！", "!", "？", "?"):
            position = self.buffer.find(mark)
            if position >= 0:
                return position + 1
        for mark in (". ", ".\u201d"):
            position = self.buffer.find(mark)
            if position >= 0:
                return position + len(mark)
        return -1

    def _pick(self, sentence):
        text = sentence.strip().strip("-*•0123456789.、) ").strip()
        if len(text) < self.MIN_CHARS:
            return ""
        if len(self.points) >= self.MAX_POINTS:
            return ""
        if len(text) > self.MAX_CHARS:
            text = text[:self.MAX_CHARS - 1] + "…"
        key = re.sub(r"[\s，,。.、；;：:！!？?\"'“”‘’()（）\[\]【】]", "", text)[:40]
        if not key or key in self.seen:
            return ""
        # 带决策信号词的句子优先；纯叙述（"用户想要…"）不当作要点，除非还没有任何要点
        matched = any(marker in text for marker in POINT_MARKERS)
        if not matched and self.points and not text.startswith(("1", "2", "3", "4", "5")):
            return ""
        self.seen.add(key)
        self.points.append(text)
        return text


def _session_summary(session):    return {
        "id": session.get("id"),
        "title": session.get("title"),
        "updated": session.get("updated"),
        "model": session.get("model"),
        "workspace": session.get("workspace"),
        "usage": session.get("usage"),
        "incomplete": bool(session.get("incomplete")),
    }


# 子代理可用的工具（不含 run_subagent，避免无限套娃）
SUBAGENT_TOOLS = ("read_file", "write_file", "list_dir", "run_command",
                  "read_url", "view_image", "web_search")


def make_subagent_runner(settings, client, tools, emit, cancel, model="", effort=""):
    """构造“派子代理”的可调用对象，供 AgentRun.subagent_runner 使用。

    子代理是独立上下文的一次完整工具循环：事件带上 parent 标记，界面里嵌在主代理的卡片里。
    """

    def runner(task, role="通用助手", max_rounds=4, parent_id=""):
        session = {
            "id": "sub-" + uuid.uuid4().hex[:6],
            "title": role,
            "messages": [],
            "context": [],
            "skills": [],
            "usage": empty_usage(),
            "model": model,
        }

        def sub_emit(event):
            payload = dict(event)
            payload["parent"] = parent_id
            emit(payload)

        agent = AgentRun(settings, session, client, tools, sub_emit,
                         lambda *args: False, cancel)
        agent.depth = 1
        agent.role_prompt = role
        agent.allow_tools = SUBAGENT_TOOLS
        agent.run(task, options={"maxToolRounds": max_rounds, "reasoningEffort": effort})

        text = ""
        for message in reversed(session["messages"]):
            if message.get("role") == "assistant" and (message.get("content") or "").strip():
                text = message["content"].strip()
                break
        return {"text": text, "rounds": int(session["usage"].get("rounds") or 0),
                "usage": session["usage"], "status": "ok"}

    return runner


def session_to_markdown(session):
    lines = ["# %s" % (session.get("title") or "会话"),
             "",
             "- 会话 ID: %s" % session.get("id"),
             "- 模型: %s" % (session.get("model") or "-"),
             "- 更新时间: %s" % (session.get("updated") or "-"),
             ""]
    context = session.get("context") or []
    if context:
        lines.append("## 上下文")
        for item in context:
            lines.append("- %s: %s" % (item.get("kind"), item.get("source")))
        lines.append("")
    lines.append("## 对话")
    lines.append("")
    for message in session.get("messages") or []:
        role = message.get("role")
        if role == "user":
            lines.append("### 我")
            lines.append("")
            for item in message.get("images") or []:
                path = item if isinstance(item, str) else item.get("path")
                if path:
                    lines.append("- 图片：`%s`" % path)
            if message.get("images"):
                lines.append("")
            lines.append(message.get("content") or "")
            lines.append("")
        elif role == "assistant":
            if message.get("reasoning"):
                lines.append("<details><summary>思考过程</summary>")
                lines.append("")
                lines.append(message["reasoning"])
                lines.append("")
                lines.append("</details>")
                lines.append("")
            lines.append("### Study7Agent")
            lines.append("")
            lines.append(message.get("content") or "")
            lines.append("")
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                lines.append("- 工具 `%s` 参数 `%s`" % (function.get("name"), function.get("arguments")))
            lines.append("")
    return "\n".join(lines)
