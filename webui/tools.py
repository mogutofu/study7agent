# -*- coding: utf-8 -*-
"""Study7Agent WebUI 工具层。

包含：
  - 工作区/任意路径文件读写（写入前自动快照，可回滚）
  - docx / xlsx / pptx 读写，统一交给 office.py（OOXML 实现）
  - 命令执行策略（internal/safety 的等价 Python 实现：分段、拦截、确认、审计日志）
  - 内网页面读取：直接复用同目录 agent.exe 的 web read（自带认证配置与正文提取）

只使用 Python 3.8.10 标准库。
"""

import difflib
import html
import json
import ntpath
import os
import re
import shutil
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
import zipfile

import classroom
import core
import desktop as desktop_module
import download as download_module
import media as media_module
import notify as notify_module
import office
import plane_hub
import plot as plot_module
import search as search_module

TEXT_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".ini", ".yaml", ".yml",
             ".xml", ".html", ".htm", ".bat", ".cmd", ".ps1", ".py", ".js", ".sql",
             ".java", ".c", ".cpp", ".h", ".cs", ".go", ".ts", ".vue", ".properties",
             # 老师电脑上真会出现的源码 / 标记 / 字幕 / 配置文件。
             # 这里只是"一眼就知道是文本"的快路径——认不出来的扩展名由 sniff_text()
             # 按内容兜底，所以这个名单漏了什么也不会读不了，只是多读 8KB 做嗅探。
             # 注意 .env / .gitignore / .htaccess 这种"整个文件名就是扩展名"的，
             # ntpath.splitext 给出的 ext 是空串，靠的是嗅探那条路。
             ".tsv", ".rst", ".tex", ".lua", ".pl", ".rb", ".php", ".sh", ".psm1", ".psd1",
             ".toml", ".cfg", ".conf", ".diff", ".patch", ".srt", ".ass", ".vtt", ".reg",
             ".m", ".mm", ".kt", ".swift", ".dart", ".r", ".jl", ".asm", ".makefile",
             ".gradle", ".env", ".gitignore", ".htaccess")
DOC_EXTS = (".docx", ".xlsx", ".pptx")

MAX_COMMAND_SECONDS = 60
# 自检（self_check 工具）最长跑多久：全量约 5 分钟，留足余量
SELF_CHECK_TIMEOUT = 900
# 一次性读进内存的文本上限：更大的文件走流式按行读，避免学校机器（2-4G 内存）被一个大日志拖死
MAX_TEXT_BYTES = 4 * 1024 * 1024
# 扩展名不认识时，读这么多字节来判断"这到底是不是文本"
SNIFF_BYTES = 8192
# zip 自动解压的安全边界。老师发来的多半是课件压缩包（正常几十 MB），
# 但网上下载的压缩包可能是"zip 炸弹"：解压后几百 GB，直接把教室机器的小硬盘写满。
# 超过任何一条就整包放弃，并把已经解出来的半截目录删干净。
MAX_ZIP_TOTAL_BYTES = 300 * 1024 * 1024
MAX_ZIP_MEMBERS = 5000
# 音频旁边可能有同名的时间轴字幕 / 歌词：这才是课堂里"音频→文字"真正靠谱的来源
TRANSCRIPT_EXTS = (".srt", ".vtt", ".txt", ".lrc")
# 桌面控制类工具：全部受 webui.allow_desktop_control 开关控制；
# 其中会真的动鼠标键盘、关窗口、改剪贴板、改注册表、发快捷键的，还要走一次确认
# （webui.confirm_desktop_input，默认开）。
DESKTOP_TOOLS = {"snapshot", "screenshot", "display_inventory", "wait", "wait_for",
                 "clipboard", "process", "registry",
                 "click", "type", "scroll", "move", "shortcut", "app",
                 "multi_select", "multi_edit", "draw_path"}
# multi_select / multi_edit 也算"动手"：会真的按住 Ctrl 连点、逐格点开再输入。
# snapshot / screenshot / display_inventory / wait / wait_for 不在这一组：它们只读
# （看画面、列元素、看显示器、等一等），既不动鼠标也不抢焦点，W7A_NO_INPUT=1 时照样能用。
DESKTOP_INPUT_TOOLS = {"clipboard", "process", "registry",
                       "click", "type", "scroll", "move", "shortcut", "app",
                       "multi_select", "multi_edit", "draw_path"}
# 会「替老师对外说话」的工具：一定先让老师确认一遍再发
MESSAGE_TOOLS = {"send_email", "notify_webhook", "send_qq_message"}
# Windows 保留设备名：写成 CON.md 之类会直接失败，提前给出可读的提示
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                  "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
                  "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
# Windows 把 CON / CON.md / CON.tar.gz 都当作设备名（大小写无关）
RESERVED_NAME_RE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$", re.I)

OUTLINE_HELP = (
    "pptx 大纲写法：# 标题 = 新的一页；## 小标题；- 要点 或 1. 步骤（缩进 2 空格降一级）；"
    "| 表头 | 表头 | = 页内表格；``` 围栏 ``` = 等宽文本块；> 文字 = 演讲者备注（另存讲稿文件）；"
    "--- = 强制分页。"
)

# --------------------------------------------------------------------------
# 「平面」（plane）工具：**平面作图只有这一个入口**
#
# 为什么收敛成一个工具、而不是 32 个：工具 schema 每轮请求都要重发，等于常驻的提示词
# 预算（见 test_webui.TestToolSchemaBudget.SCHEMA_CEILING）。冻结方案是
# `plane {action, …}` + `plane_batch {ops}` 两个工具承载全部能力，见
# docs/plane-architecture.md §5.1。
#
# 为什么旧的 show_figure(function/geometry) 必须从模型的可达面里拿掉：老师明确要求
# "ai 调用工具只准用新加的这个平面的"——同一个能力留着两条入口，模型就会挑那条老的，
# 新平面里的依赖图（改父对象、交点跟着动）就永远用不上。立体（solid）不在平面范围内，
# 所以留在 show_figure 里（文档 §6）。
# --------------------------------------------------------------------------

# 动作枚举（冻结件 §5.1）。**一个都不能少**：还没做的动作也留在表里、返回
# not_implemented + 替代写法，模型才知道这个能力存在、只是现在得换个做法。
PLANE_ACTIONS = (
    "create_object", "create_objects", "update_object", "delete_object",
    "list_objects", "get_object", "query_state",
    "execute_command", "plan", "apply_plan", "solve",
    "plot_function", "plot_implicit", "plot_parametric", "plot_polar",
    "add_slider", "add_animation", "add_locus", "add_trace", "animate",
    "set_style", "zoom", "pan", "reset_view",
    "export", "import", "undo", "redo", "snapshot", "restore",
)

# 还没做的动作 -> 现在该用什么代替。这些功能在 plane.py / plane_math.py 里都还没有
# 落地（轨迹要动态层、solve 要符号求解器），**不能假装成功**：silent success 会让模型
# 以为图已经画好了，老师在屏幕上什么也看不到。
PLANE_NOT_YET = {
    "plan": "直接调用要做的动作即可（这个动作是给「先出计划再执行」的流程预留的）",
    "apply_plan": "直接调用要做的动作即可",
    "solve": "现在只能画图与度量：用 plot_implicit 画方程、用 create_object 建 "
             "distance/angle/area/slope/radius 这类度量对象（要精确值也用它们）",
    "add_locus": "轨迹还没做：把动点对象和它的父对象建出来，再让老师按住拖动，"
                 "用 add_trace 的思路先用 trace 对象记录",
    "add_animation": "动画还没做：用 add_slider 建滑动条，或直接 update_object 改 parameter",
    "add_trace": "trace 只能由前端拖动累积，暂时建不出来",
    "animate": "动画播放还没做：用 update_object 直接改参数，或让老师拖动滑动条",
}

# 动作说明（拼进 plane 的 description）。写得很短是故意的：schema 是每轮都重发的预算。
PLANE_ACTION_HELP = (
    "动作：" + "；".join([
        "create_object/create_objects 建、update_object/delete_object 改删（删会级联）",
        "list_objects/get_object/query_state 看现状",
        "execute_command 敲 GeoGebra 式命令",
        "plot_function/plot_implicit/plot_parametric/plot_polar 画曲线（给 expr；"
        "**别写 xmin/xmax** = 无限延伸）",
        "add_slider 滑动条；set_style 样式；zoom/pan/reset_view 视图",
        "export/import/snapshot/restore/undo/redo 存取与回滚",
    ]))

PLANE_COMMANDS = "、".join(plane_hub.COMMANDS)

# 「默认视图」的口径（和 plane.DEFAULT_VIEW 一致）。**故意不 import plane 拿它**：
# tools.py 在模块顶部 import plane，等于把"plane.py 缺失/写了一半"变成"整个工具层起不来"
# （见 plane_hub 的说明）。reset_view 只用得到这几个数，抄一份比引一个模块便宜。
PLANE_DEFAULT_VIEW = {"xmin": -10.0, "ymin": -6.0, "xmax": 10.0, "ymax": 6.0,
                      "grid": True, "axes": True, "polar": False}

# 这些动作只读：不该动版本号、不该进撤销栈
PLANE_READ_ACTIONS = frozenset((
    "list_objects", "get_object", "query_state", "export", "snapshot",
))

# 这些动作不碰"对象集合"，但确实改状态（视图 / 撤销 / 整块换掉），返回的话要广播给网页
PLANE_STATE_ACTIONS = frozenset((
    "update_object", "delete_object", "create_object", "create_objects",
    "execute_command", "apply_plan", "add_slider", "set_style", "zoom", "pan",
    "reset_view", "import", "undo", "redo", "restore",
    "plot_function", "plot_implicit", "plot_parametric", "plot_polar",
))


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录内容，返回名称、类型、大小、修改时间。用于了解项目结构。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录绝对路径，例如 C:\\\\work"},
                    "pattern": {"type": "string", "description": "可选的通配符过滤，例如 *.md"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": ("读取文件内容。txt/md/csv/json/log/代码等文本按行返回并带行号；"
                            "docx 提取正文；xlsx 提取各工作表表格；pptx 还原成可再编辑的课件大纲"
                            "（# 标题 / - 要点）；zip 自动解压到工作区并列出目录树；"
                            "音频返回格式、时长与 ID3 标签（并读同名的 .srt/.vtt 字幕）。"
                            "长文件可用 start_line/max_lines 分段读取。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件绝对路径"},
                    "start_line": {"type": "integer", "description": "起始行号，从 1 开始"},
                    "max_lines": {"type": "integer", "description": "最多读取行数，默认 400"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": ("写入或新建文件，按扩展名自动选择格式：\n"
                            "- .pptx 生成演示文稿（课件）。" + OUTLINE_HELP + "\n"
                            "- .docx 生成 Word 文档，支持 # 标题层级、- 列表、Markdown 表格、**加粗**。\n"
                            "- .xlsx 生成表格，Markdown 表格或 CSV 内容均可，首行自动加粗并冻结。\n"
                            "- 其它扩展名按 UTF-8 文本保存。\n"
                            "覆盖已有文件前会自动保存快照，可回滚。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件绝对路径，例如 D:\\\\课件\\\\分数的意义.pptx"},
                    "content": {"type": "string", "description": "完整文件内容（覆盖写入，不是补丁）"},
                    "create_only": {"type": "boolean", "description": "为 true 时若文件已存在则拒绝写入"},
                    "title": {"type": "string", "description": "pptx 首张幻灯片的标题（可选）"},
                    "template": {"type": "string",
                                 "description": ("pptx 可选：学校统一模板 .pptx 的绝对路径。"
                                                 "填写后新幻灯片会追加到模板里，沿用模板的主题配色与版式。")},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_image",
            "description": ("查看本机图片文件的内容（截图、相机照片、扫描件），支持 png/jpg/jpeg/gif/webp/bmp，"
                            "单张不超过 8MB。调用后图片会在下一轮真正提供给你，然后你就能描述图里的内容。"
                            "用户直接发来的图片不需要调用本工具。"),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "图片的绝对路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_media",
            "description": ("体检一个音频/视频文件：识别容器与编码、时长、分辨率，并判断它在"
                            "Windows 7 + PowerPoint 上能不能播放（H.264+AAC 的 MP4 最稳）。"
                            "老师说「视频放不了 / 课件里的视频播不出来」时先用这个查文件本身。"),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "音视频文件绝对路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_pptx",
            "description": ("体检一个 pptx 课件：文件是否损坏、每一页引用了哪些视频/音频、"
                            "媒体是内嵌还是外链（外链换电脑就播不了）、媒体编码兼不兼容、"
                            "有没有缺失的媒体部件或嵌入字体。修课件之前先跑这个，再决定用 "
                            "extract_media / replace_media 怎么修。"),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "pptx 文件绝对路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_media",
            "description": "把 pptx 里内嵌的视频/音频导出到一个目录，方便老师试播、送修或转码后再替换回去。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "pptx 文件绝对路径"},
                    "out_dir": {"type": "string", "description": "导出目录，留空 = 课件同目录下的 media 子目录"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_media",
            "description": ("把 pptx 里某个内嵌媒体部件换成新的文件（保持引用不变，PowerPoint 无需重新插入）。"
                            "典型用法：extract_media 取出 → 转成 H.264 的 MP4 → replace_media 放回去。"
                            "会先自动快照，可回滚。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "pptx 文件绝对路径"},
                    "part": {"type": "string", "description": "媒体部件名，例如 media1.mp4（diagnose_pptx 会列出）"},
                    "new_file": {"type": "string", "description": "替换成的新媒体文件绝对路径"},
                },
                "required": ["path", "part", "new_file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snapshot",
            "description": ("看一眼现在的屏幕，拿到「画面 + 元素清单 + 窗口表」——桌面操作都从它开始。"
                            "元素清单每行形如「[12] (640,380) 按钮 \"确定\" [按下]」，方括号里的数字就是 label；"
                            "要点击/输入/滚动，把 label 编号交给 click / type / scroll / move 就行"
                            "（推荐，界面变了会按当前界面重算坐标）。"
                            "翻页、切窗口、弹对话框之后编号会重排，动手前必须重新 snapshot。"
                            "元素太少或为空（Chromium、Qt、自绘界面常见）说明这个窗口不暴露控件，"
                            "那就改用 screenshot 看画面、再用 click 的 loc=[x,y] 按坐标点。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {"type": "string",
                               "description": "只看哪个窗口：标题里的一段字 / pid:1234 / handle:12345。省略 = 整个桌面。"},
                    "find": {"type": "string",
                             "description": "只列名字或文字里含这个关键字的元素，用来在一大堆元素里快速找那个按钮。"},
                    "use_vision": {"default": True, "type": "boolean",
                                   "description": "要不要截图，默认 true。只要元素清单、不想生成图片文件时设 false。"},
                    "use_annotation": {"default": True, "type": "boolean",
                                       "description": "截图时要不要在元素上画红框和编号，默认 true，看图时更容易对上 label。"},
                    "use_ui_tree": {"default": True, "type": "boolean",
                                    "description": "要不要读界面元素，默认 true。设 false 就只截图（这个窗口读不出元素时也快）。"},
                    "read_text": {"default": False, "type": "boolean",
                                  "description": "要不要额外附上窗口里的文字正文。默认 false（文档类窗口很慢很啰嗦），确实要读文字时再开。"},
                    "max_elements": {"default": 400, "type": "integer",
                                     "description": "元素最多列几个，默认 400。元素特别多时可以调小，比如 80。"},
                    "max_depth": {"default": 14, "type": "integer",
                                  "description": "界面树最多往下走几层，默认 14。控件层级很深的程序可以调到 20。"},
                    "region": {"type": "array", "items": {"type": "integer"},
                               "description": "只看屏幕上一块区域：[x, y, 宽, 高]，屏幕物理像素。"},
                    "display": {"type": "integer",
                                "description": "看第几块屏幕（1 是第一块，编号同 display_inventory）。省略 = 所有屏幕拼一张。"},
                    "path": {"type": "string",
                             "description": "截图存到哪个文件。留空 = 工作区「截图」目录里按时间命名。"},
                    "scale": {"default": 1.0, "type": "number",
                              "description": "缩放，默认 1.0。屏幕大导致图片超过 8MB（模型看不到）时用 0.5。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": ("给屏幕拍张照，图片会直接送到你眼前（同时给你文件路径）。用在："
                            "要确认某个东西现在长什么样、要看图表/画布/自绘界面（这类界面 snapshot 读不出元素）、"
                            "或者要按坐标点击前先看清位置。它只给画面、不给元素编号：要操作元素优先用 snapshot。"
                            "按这张图算出来的 loc=[x, y] 是屏幕物理像素；窗口在两次调用之间被移动过，"
                            "这个坐标会被拒绝（那时重新截一张即可，别拿旧坐标硬点）。"
                            "只截某个窗口给 window（最小化的窗口截不到，先用 app 的 switch 恢复）；"
                            "只截一块用 region；多块屏幕用 display 选第几块。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "图片存到哪个文件。留空 = 工作区「截图」目录里按时间命名。"},
                    "display": {"type": "integer",
                                "description": "截第几块屏幕（1 是第一块）。省略 = 所有屏幕拼一张。"},
                    "region": {"type": "array", "items": {"type": "integer"},
                               "description": "只截一块区域：[x, y, 宽, 高]，屏幕物理像素。"},
                    "window": {"type": "string",
                               "description": "只截这个窗口：标题里的一段字 / pid:1234 / handle:12345。省略 = 整屏。"},
                    "scale": {"default": 1.0, "type": "number", "description": "缩放，默认 1.0；图超过 8MB 时用 0.5。"},
                    "cursor": {"default": False, "type": "boolean",
                               "description": "要不要在图上画个红圈标出鼠标现在的位置，默认 false。看不清鼠标在哪时打开。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "display_inventory",
            "description": ("列出这台电脑的显示器和桌面情况：几块屏幕、编号与分辨率、哪块是主屏、"
                            "整个虚拟桌面的范围、DPI 缩放、当前前台窗口，以及桌面控制能不能用"
                            "（老师有没有关掉开关、是不是禁止输入）。"
                            "不确定「第 2 块屏」是哪块、或者按坐标点击前怀疑缩放/多屏偏移时，先看它。"),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": ("先等一会儿再继续。用在：点完按钮要给它反应时间、等动画或加载过去、"
                            "刚启动的程序要缓一下。最多等 30 秒（写多了也按 30 秒算），"
                            "只想歇一下就给 1~3 秒。要等某个明确的界面变化出现，用 wait_for 更靠谱。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"default": 1.0, "type": "number", "description": "等多少秒，默认 1.0，最多 30。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for",
            "description": ("等某个界面变化出现再往下做，比死等固定秒数靠谱。condition 四选一："
                            "window = 等到某个窗口出现；gone = 等到某个窗口消失（关掉了）；"
                            "text = 等到窗口里出现某段文字；stable = 等到画面不再变化（动画结束、渲染完）。"
                            "text 参数：window/gone 时填窗口标题里的一段字，text 时填要等出现的文字。"
                            "window 参数可以限定只在某个窗口里找。等到了会告诉你是第几秒等到的；"
                            "等不到会报错说明等了多久、最后看到什么——那时别硬往下点，先 snapshot 看看现在的界面。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string", "enum": ["window", "gone", "text", "stable"],
                                  "description": "window 等窗口出现 / gone 等窗口消失 / text 等文字出现 / stable 等画面不再变"},
                    "text": {"type": "string",
                             "description": "condition=window/gone 时是窗口标题关键字；condition=text 时是要等出现的文字。"},
                    "window": {"type": "string",
                               "description": "只在哪个窗口里找（可省略）：标题关键字 / pid:1234 / handle:12345。"},
                    "timeout": {"default": 10.0, "type": "number", "description": "最多等几秒，默认 10。"},
                    "interval": {"default": 0.25, "type": "number", "description": "每隔几秒查一次，默认 0.25。"},
                },
                "required": ["condition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard",
            "description": ("读或写系统剪贴板里的**文字**（图片、文件读不出来）。"
                            "action=get 把剪贴板里的文字拿给你看（老师刚复制的内容、要核对或转写的东西）；"
                            "action=set 把一段文字放进剪贴板，老师自己按 Ctrl+V 就能粘贴，"
                            "也可以配合 shortcut 的 ctrl+v 直接粘进某个窗口。"
                            "注意：set 会覆盖老师原来复制的内容，动手前想清楚；"
                            "要把一段长文字交给老师粘贴，也常用 set。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "set"],
                               "description": "get 读剪贴板 / set 写剪贴板"},
                    "text": {"type": "string", "description": "action=set 时要放进剪贴板的文字。"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process",
            "description": ("看电脑上正在跑哪些程序，或者结束一个卡死的进程。"
                            "action=list（默认）按内存/名字/pid 排序列出进程，用 name 按进程名过滤"
                            "（例如 name=\"chrome\"），limit 控制列几个。"
                            "action=kill 结束进程：给 pid（最准）或 name（进程名，关不掉再加 force=true 强杀）。"
                            "杀之前一定先确认那个 pid 是老师要关的东西——explorer、浏览器、QQ、WPS/PPT "
                            "这些千万别碰，杀错了老师的活儿就丢了。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"default": "list", "type": "string", "enum": ["list", "kill"],
                               "description": "list 列进程（默认）/ kill 结束进程"},
                    "name": {"type": "string", "description": "按进程名过滤（list）或按名字找目标（kill），例如 notepad。"},
                    "pid": {"type": "integer", "description": "kill 时指定进程号，最准。"},
                    "sort_by": {"default": "memory", "type": "string", "enum": ["memory", "name", "pid"],
                                "description": "排序方式，默认 memory（占内存最多的在前）。"},
                    "limit": {"default": 20, "type": "integer", "description": "列几个，默认 20。"},
                    "force": {"default": False, "type": "boolean", "description": "kill 时是否强杀，默认 false。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "registry",
            "description": ("读或改 Windows 注册表。path 要带根：HKCU\\Software\\... / HKLM\\Software\\... / "
                            "HKCR\\... / HKU\\...。action=get 读一个值（name 省略就是默认值）、"
                            "list 列出某个键下所有值、set 写一个值（给 value，type 选 string/expand/dword/qword/multi）、"
                            "delete 删掉一个值。注册表会影响系统行为（开机自启、文件关联、右键菜单…）："
                            "动手前先 get 记下旧值，改完再 get 复查，并跟老师说清楚改了什么、怎么改回去。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "set", "delete", "list"],
                               "description": "get 读一个值 / list 列全部值 / set 写 / delete 删"},
                    "path": {"type": "string",
                             "description": "注册表路径，例如 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run。"},
                    "name": {"type": "string", "description": "值名；省略 = 读/写那个键的默认值。"},
                    "value": {"type": "string", "description": "action=set 时要写入的值。"},
                    "type": {"default": "string", "type": "string", "enum": ["string", "expand", "dword", "qword", "multi"],
                             "description": "值的类型，默认 string。"},
                },
                "required": ["action", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": ("在屏幕上点一下鼠标。定位两种：label=snapshot 元素清单里方括号里的编号（推荐），"
                            "或者 loc=[x, y] 屏幕物理像素（按最近一次截图算的；窗口在两次调用之间动过会被拒绝，"
                            "那时重新 screenshot 再算，别拿旧坐标硬点）。"
                            "button 选 left/right/middle；clicks=1 单击、2 双击、0 只把鼠标移过去"
                            "（悬停出菜单、看提示）。点完记得再 snapshot 一次确认真的点到了。"
                            "要点窗口按钮、任务栏图标这类不在元素清单里的东西，直接给 loc。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "loc": {"type": "array", "items": {"type": "integer"},
                            "description": "屏幕物理像素坐标 [x, y]（按最近一次截图算）。"},
                    "label": {"type": "integer",
                              "description": "snapshot 元素清单里方括号里的编号，例如 12。"},
                    "button": {"default": "left", "type": "string", "enum": ["left", "right", "middle"],
                               "description": "哪个键，默认 left。"},
                    "clicks": {"default": 1, "type": "integer",
                               "description": "点几下，默认 1；0 = 只把鼠标移过去不点，2 = 双击。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type",
            "description": ("往窗口里输入文字（模拟键盘，不是改文件）。给了 loc 或 label 就先点一下那里再输入"
                            "（输入框、表格单元格最常用）。中文也能输：内部按 Unicode 逐个字符注入，"
                            "不切输入法也不占剪贴板。clear=true 会先把框里原有的内容全选删掉"
                            "（改旧数据用得上）；caret_position 选 start/idle/end 决定点进去之后光标放哪；"
                            "press_enter=true 输入完顺手回车（搜索框、聊天框、命令行常用）。"
                            "输入完建议再 snapshot 一次：有的输入框要失去焦点才真正生效。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要输入的文字（中文也行）。"},
                    "loc": {"type": "array", "items": {"type": "integer"},
                            "description": "先点一下这儿再输入：[x, y] 屏幕物理像素。"},
                    "label": {"type": "integer",
                              "description": "先点一下 snapshot 里这个编号的输入框再输入。"},
                    "clear": {"default": False, "type": "boolean", "description": "输入前先把原有的内容全选删掉，默认 false。"},
                    "caret_position": {"default": "idle", "type": "string", "enum": ["start", "idle", "end"],
                                       "description": "光标放哪，默认 idle（点哪儿算哪儿）。"},
                    "press_enter": {"default": False, "type": "boolean", "description": "输入完按一下回车，默认 false。"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": ("滚动鼠标滚轮——翻页用这个，别去拖滚动条（又细又容易拖飞）。"
                            "先给位置：label=编号（推荐，比如那个列表/正文区域）或 loc=[x, y]；"
                            "都没给就用鼠标现在的位置。type 选 vertical（上下）/horizontal（左右），"
                            "direction 选 down/up/left/right，wheel_times 是滚几格、一格大约三行。"
                            "翻一段滚几格、然后 snapshot 看看到哪了；别一次滚几十格，内容会滚过头反而找不到。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "loc": {"type": "array", "items": {"type": "integer"},
                            "description": "在哪儿滚 [x, y] 屏幕物理像素（要落在那块能滚的区域里）。"},
                    "label": {"type": "integer", "description": "滚这个编号的元素（snapshot 里的编号）。"},
                    "type": {"default": "vertical", "type": "string", "enum": ["vertical", "horizontal"],
                             "description": "上下滚还是左右滚，默认 vertical。"},
                    "direction": {"default": "down", "type": "string", "enum": ["down", "up", "left", "right"],
                                  "description": "往哪个方向滚，默认 down。"},
                    "wheel_times": {"default": 1, "type": "integer", "description": "滚几格，默认 1（一格大约三行）。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move",
            "description": ("移动鼠标，或者按住拖动。只移动：给 loc=[x, y] 或 label=编号。"
                            "拖动（拖文件、拖窗口边、拖选、拖滑块）：drag=true、from_loc=[起点x, 起点y]、"
                            "loc=[终点x, 终点y]，duration 给几秒——慢慢拖更容易触发拖放高亮，急急忙忙反而失败。"
                            "拖完再 snapshot 一眼确认东西到没到位。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "loc": {"type": "array", "items": {"type": "integer"},
                            "description": "到哪儿去 [x, y]（拖动时是终点）。"},
                    "label": {"type": "integer", "description": "移到 snapshot 里这个编号的元素上。"},
                    "drag": {"default": False, "type": "boolean", "description": "true = 按住拖动（需要 from_loc），默认 false 只移动。"},
                    "from_loc": {"type": "array", "items": {"type": "integer"},
                                 "description": "拖动的起点 [x, y]。"},
                    "duration": {"type": "number", "description": "拖动用几秒，默认自动（大约 0.4 秒）。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shortcut",
            "description": ("按一组快捷键。写成一个字符串、用加号连起来：ctrl+s 保存、ctrl+c / ctrl+v 复制粘贴、"
                            "ctrl+z 撤销、alt+tab 切窗口、ctrl+shift+esc 任务管理器；单键也行："
                            "enter / esc / tab / f5 / 方向键。多媒体键也走它：play_pause、next、prev、stop、"
                            "volume_up、volume_down、mute（一次一个键，要按几次就调几次）。"
                            "快捷键发给当前前台窗口，发之前确认焦点在哪——发错窗口会触发别的操作"
                            "（比如 ctrl+w 关掉的是当前那个程序的窗口）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "shortcut": {"type": "string",
                                 "description": "例如 ctrl+s、ctrl+shift+esc、alt+tab、enter、f5、volume_up。"},
                },
                "required": ["shortcut"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app",
            "description": ("管窗口和程序：启动、切换、关闭、改位置和大小。mode 选："
                            "launch 按名字启动（开始菜单里的名字，例如 \"记事本\"、\"画图\"）、"
                            "launch_executable 按完整路径启动（给 executable，可带 args 参数与 cwd 工作目录）、"
                            "switch 把某个窗口切到前台（动手点它之前先切过来）、info 看窗口位置和大小、"
                            "resize 改大小（size=[宽, 高]）、move 挪位置（loc=[x, y]）、"
                            "close / minimize / maximize / restore / hide / show 关掉或改变窗口状态。"
                            "name 给窗口标题里的一段字、pid:1234 或 handle:12345；launch 时给程序名。"
                            "关窗口前想清楚：没保存的文档会弹「是否保存」，有些新版程序 close 只是把窗口藏起来"
                            "（那还得用 process 的 kill）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string",
                             "enum": ["launch", "launch_executable", "switch", "info", "resize", "move",
                                      "close", "minimize", "maximize", "restore", "hide", "show"],
                             "description": "要做什么（见描述）。"},
                    "name": {"type": "string",
                             "description": "launch 用程序名；其它 mode 用窗口标题关键字 / pid:1234 / handle:12345。"},
                    "executable": {"type": "string", "description": "launch_executable 用的 exe 完整路径。"},
                    "args": {"type": "string", "description": "启动参数（给新程序），可省略。"},
                    "cwd": {"type": "string", "description": "新程序的工作目录，可省略。"},
                    "loc": {"type": "array", "items": {"type": "integer"},
                            "description": "move 用：窗口左上角挪到哪儿 [x, y]。"},
                    "size": {"type": "array", "items": {"type": "integer"},
                             "description": "resize 用：窗口新大小 [宽, 高]。"},
                    "window": {"type": "string", "description": "和 name 同义（窗口标题关键字），谁给了用谁。"},
                },
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_select",
            "description": ("一次选中多个东西（列表、文件、表格里的多行）。给 labels=编号列表"
                            "（推荐，就是 snapshot 里方括号的数字）或 locs=[[x,y], …] 坐标列表，"
                            "它会依次点过去；press_ctrl=true（默认）点的时候按住 Ctrl（不连续多选），"
                            "press_ctrl=false 时不按 Ctrl。选之前先 snapshot 确认编号，"
                            "选完再 snapshot 看一眼有没有真的选上。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "labels": {"type": "array", "items": {"type": "integer"},
                               "description": "要选的元素编号列表，例如 [4, 7, 9]。"},
                    "locs": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}},
                             "description": "要点的坐标列表，例如 [[100,200],[100,240]]。"},
                    "press_ctrl": {"default": True, "type": "boolean", "description": "点的时候按住 Ctrl，默认 true。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_edit",
            "description": ("按顺序给多个格子填内容（批量填表、逐个填空）。"
                            "labels=[[编号, \"文字\"], …] 或 locs=[[x, y, \"文字\"], …]，每个位置先点开再输入。"
                            "它不会自动清空格子里原有的内容：旧数据要用 type 的 clear=true 先清掉，"
                            "或者改用 type 一个一个来。填完务必 snapshot 复查一遍，别假设每一格都填进去了。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "labels": {"type": "array", "items": {"type": "array"},
                               "description": "[[编号, \"要填的文字\"], …]，编号来自 snapshot。"},
                    "locs": {"type": "array", "items": {"type": "array"},
                             "description": "[[x, y, \"要填的文字\"], …]，屏幕物理像素。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draw_path",
            "description": ("在画图/白板类窗口里画线（讲题时圈画、画示意图、连辅助线）。strokes 是笔画列表，"
                            "每一笔是一串屏幕坐标点：[[[100,200],[300,200]], [[100,200],[100,400]]] "
                            "就是一条横线加一条竖线；duration 是每笔用几秒（默认 0.45，慢一点更稳）。"
                            "它只画你按画面自己算出来的坐标：要画精确的函数图像、几何图形、带标注的图形，"
                            "别用它——那类图用作图工具（坐标是算出来的、还带标注）。"
                            "画之前确认画布窗口在前台，画完用 screenshot 看一眼画得对不对。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "strokes": {"type": "array", "items": {"type": "array"},
                                "description": "笔画列表：[[[x,y],[x,y],…], …]。"},
                    "duration": {"default": 0.45, "type": "number", "description": "每一笔用几秒，默认 0.45。"},
                    "button": {"default": "left", "type": "string", "enum": ["left", "right"],
                               "description": "用哪个键画，默认 left。"},
                },
                "required": ["strokes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download",
            "description": ("下载图片 / 文件 / 视频到本机（一次最多 5 个网址）。"
                            "图片会自动给你看；视频/音频会顺手做一次编码体检（判断 Win7 能不能播）。"
                            "大文件（几百 MB 的视频）用 background=true 放后台下，别把对话卡住，"
                            "然后过一会儿用 download_status 看进度。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "下载地址（http/https）"},
                    "urls": {"type": "array", "items": {"type": "string"},
                             "description": "一次下多个（最多 5 个）"},
                    "path": {"type": "string",
                             "description": "保存目录或文件名；只给目录就按原文件名存。留空默认放「下载」文件夹"},
                    "max_mb": {"type": "integer",
                               "description": "本次允许的最大 MB，默认 200（硬顶 2048）"},
                    "background": {"type": "boolean",
                                   "description": "true = 放到后台下（适合大视频），立刻返回任务号"},
                    "referer": {"type": "string", "description": "有些网站防盗链，需要带上来源页地址"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_status",
            "description": ("看后台下载任务的进度/结果，或取消它们。"
                            "不带 job_id = 列出全部；action=cancel 可以停掉一个任务。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "任务号，例如 dl-1"},
                    "action": {"type": "string", "enum": ["list", "cancel"],
                               "description": "list（默认）或 cancel"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": ("给老师或同事发邮件（QQ 邮箱、163、学校邮箱都行），可以带附件。"
                            "老师和家长沟通、把成绩单/课件/通知发出去时用它。"
                            "需要老师在「设置 → 通知」里先填好邮箱账号和授权码；"
                            "QQ 邮箱必须用 16 位授权码，不能用登录密码。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "邮件主题"},
                    "body": {"type": "string", "description": "邮件正文（纯文本）"},
                    "to": {"type": "string", "description": "收件人，多个用分号隔开；留空 = 用设置里的默认收件人"},
                    "attach": {"type": "array", "items": {"type": "string"},
                               "description": "附件文件绝对路径列表（可选，合计不超过 20MB）"},
                },
                "required": ["subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_webhook",
            "description": ("把一条消息推送到企业微信 / 钉钉 / 飞书群机器人（群里能直接看到，比邮件更快）。"
                            "需要老师在「设置 → 通知」里粘贴群机器人的 Webhook 地址。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要推送的文字"},
                    "url": {"type": "string", "description": "临时换一个推送地址（留空 = 用设置里的）"},
                    "kind": {"type": "string",
                "enum": ["wecom", "dingtalk", "feishu", "custom"], "description": "wecom / dingtalk / feishu / custom，默认按设置里的"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_qq_message",
            "description": ("用电脑上已经登录的 QQ 给当前聊天窗口（或指定联系人）发一条消息，发完自动截图。"
                            "注意：这不是 QQ 官方接口，是靠模拟操作完成的，必须先打开 QQ 并登录；"
                            "如果老师在 QQ 里设置了「Ctrl+Enter 发送」，要把 send_shortcut 设成 ctrl+enter。"
                            "只能用于老师明确要求的消息，发完请看截图确认有没有真的发出去。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要发送的消息内容"},
                    "contact": {"type": "string", "description": "联系人/群名称（会在 QQ 里搜索），留空 = 用当前打开的聊天窗口"},
                    "send_shortcut": {"type": "string",
                "enum": ["enter", "ctrl+enter"], "description": "enter（默认）或 ctrl+enter"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": ("按「查找 → 替换」修改已有文件里的某一段：改代码、改长文档里的一两条，"
                            "用这个比 write_file 重写整篇更省、更不容易改坏别的地方。\n"
                            "- find 要写清楚被替换的原文，建议带上前后各一行保证唯一；"
                            "缩进和行尾空白会做容错匹配（匹配方式会在结果里说明）。\n"
                            "- 同一段出现多次时必须写长一点，或用 replace_all=true。\n"
                            "- 改前自动快照可回滚；是 .py/.json 时还会顺手做语法检查并把错误报给你。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件绝对路径"},
                    "find": {"type": "string", "description": "要被替换的原文（尽量唯一）"},
                    "replace": {"type": "string", "description": "替换成的新内容；删除就传空字符串"},
                    "replace_all": {"type": "boolean", "description": "为 true 时替换所有出现的位置"},
                },
                "required": ["path", "find", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_check",
            "description": ("跑一遍本程序自带的自检（约 5 分钟，不需要 API Key、不联网），"
                            "用来确认改动没有把程序改坏。agent 修改自己的源码或配置以后**必须**先跑这个，"
                            "通过了再重启服务；失败时按输出里的报错去修。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string",
                               "description": "可选的测试类名（例如 TestCommunityHardening），留空 = 全部"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": ("重启本 WebUI 服务，让改过的源码/配置生效。"
                            "会先启动一个独立的小脚本，等当前进程退出后再拉起 start-webui.cmd，"
                            "所以对话可能会断几秒，刷新页面即可恢复。改完代码务必先跑 self_check。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "为什么要重启（会写进日志，便于事后追溯）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": ("在本机执行 Windows 命令（cmd.exe /C）并返回输出，例如 dir、findstr、git status。"
                            "高风险前缀（del/reg/powershell 等）需要用户在界面上确认后才执行。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令原文"},
                    "cwd": {"type": "string", "description": "可选的工作目录绝对路径"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": ("读取静态页面的正文（脚本渲染的页面不支持），返回 Markdown 正文。"
                            "可一次给 1-3 个网址（urls 数组），用于打开搜索结果里的多个链接。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整 URL"},
                    "urls": {"type": "array", "items": {"type": "string"},
                             "description": "要一起读取的多个 URL（最多 3 个）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": ("联网搜索，返回标题、网址与摘要。\n"
                            "- 关键词：用具体词，必要时加引号锁定短语（如 \"分数的初步认识\" 教学设计 教案）。\n"
                            "- 结果不相关时：换关键词，或用 engine 参数换引擎（bing / duckduckgo）再试。\n"
                            "- fetch_top：直接读取前几条的正文摘要（1-3，默认 0），一次调用就能判断相关性，"
                            "比先搜再逐个 read_url 更省 token。\n"
                            "- 不要编造链接：确实搜不到就说明搜不到。内网可在设置里配置单位自建的搜索接口。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回条数，默认 5"},
                    "engine": {"type": "string",
                               "description": "指定引擎，可选 auto / bing / duckduckgo / custom"},
                    "fetch_top": {"type": "integer",
                                  "description": "读取前 N 条结果的正文摘要（0-3，默认 0）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pick_students",
            "description": ("课堂抽学号 / 随机分组，结果会全屏投到大屏上。"
                            "默认在 1-50 号里不重复地抽：抽过的学号本轮不会再抽到，抽完一轮自动重开；"
                            "max_number 可以改成班级人数；reset=true 手动重开一轮。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "description": "pick=抽学号（默认），group=随机分组"},
                    "count": {"type": "integer", "description": "抽几个学号，默认 1"},
                    "max_number": {"type": "integer", "description": "班级人数 / 学号上限，默认 50"},
                    "groups": {"type": "integer", "description": "分几组（mode=group 时用）"},
                    "per_group": {"type": "integer", "description": "每组几人（与 groups 二选一）"},
                    "reset": {"type": "boolean", "description": "重新开始一轮，清空已抽记录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_figure",
            "description": ("**只画立体（3D）图形，kind=solid**：棱柱/棱锥/长方体/正方体/圆柱/"
                            "圆锥/球/旋转体/空间/异面直线/二面角这些字眼时用它（画成平面图是"
                            "错的）。平面上的函数、方程、圆、直线、交点、动点、轨迹**一律用 "
                            "plane**，show_figure 已经不管平面了。" + plot_module.SOLID_HELP),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["solid"]},
                    "shapes": {"type": "array", "items": {"type": "string"},
                               "description": "空间图形，每条一个"},
                    "title": {"type": "string", "description": "面板标题"},
                    "note": {"type": "string", "description": "图下的说明"},
                },
                "required": ["kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plane",
            "description": ("平面作图（2D）的**唯一入口**：函数、方程、圆、直线、交点、动点、"
                            "轨迹都走这里。"
                            "常用类型：point 点、segment 线段、line 直线、circle 圆、polygon 多边形、"
                            "function 函数、implicit 方程、slider 滑动条。" + PLANE_ACTION_HELP),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(PLANE_ACTIONS)},
                    "ops": {"type": "array", "items": {"type": "object"},
                            "description": "对象操作 {op,type,id,definition,style}，"
                                           "op=create/update/delete"},
                    "id": {"type": "string", "description": "对象 id，也是标签（A、c1）"},
                    "type": {"type": "string", "description": "对象类型"},
                    "definition": {"type": "object",
                                   "description": "按类型给：点 {x,y}，或对象上的动点 "
                                                  "{on:对象id, parameter:0..1}；线段 {start,end}、"
                                                  "圆 {center,through}、函数 {expr}、"
                                                  "方程 {expr:\"x^2+y^4=16\"}"},
                    "style": {"type": "object", "description": "color/size/lineStyle/label"},
                    "ids": {"type": "array", "items": {"type": "string"}
                            , "description": "对象 id 列表"},
                    "command": {"type": "string",
                                "description": "GeoGebra 式命令：" + PLANE_COMMANDS},
                    "expr": {"type": "string", "description": "表达式，例如 x^2-2x-3"},
                    "xmin": {"type": "number",
                             "description": "**别写**：不写 = 无限延伸（按视野自动多画）"},
                    "xmax": {"type": "number",
                             "description": "**别写**：老师说了范围才写"},
                    "view": {"type": "object",
                             "description": "范围：xmin/ymin/xmax/ymax/grid/axes；"
                                            "zoom factor、pan dx/dy"},
                    "version": {"type": "integer", "description": "restore 的目标版本号"},
                    "compact": {"type": "boolean", "description": "query_state 精简输出，默认 true"},
                    "limit": {"type": "integer", "description": "query_state 最多返回几个"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plane_batch",
            "description": ("一次执行多个平面对象操作（原子：有一条不过就一条都不落盘，"
                            "撤销也是一步）。ops 每项 {op,type,id,definition,style}，"
                            "可以先写子对象、后写父对象，内部按依赖排序。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "ops": {"type": "array", "items": {"type": "object"},
                            "description": "操作列表，例如 "
                                           "[{\"op\":\"create\",\"type\":\"point\",\"id\":\"A\","
                                           "\"definition\":{\"x\":0,\"y\":0}}]"},
                },
                "required": ["ops"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "class_timer",
            "description": ("课堂倒计时，大屏上显示：小组讨论、随堂练习、限时答题。"
                            "seconds 是秒数（300=5 分钟），label 是显示的名称；stop=true 结束计时。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "integer", "description": "倒计时秒数"},
                    "label": {"type": "string", "description": "显示名称，例如“小组讨论”"},
                    "stop": {"type": "boolean", "description": "为 true 时停止当前计时"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_on_screen",
            "description": ("把内容全屏投到大屏（板书要点、题目、答案、主题句），字号按教室后排可读设计，"
                            "适合讲评和当堂练习。action=hide 关闭展示。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "大标题"},
                    "body": {"type": "array", "items": {"type": "string"},
                             "description": "正文条目（每项一行）"},
                    "kind": {"type": "string",
                             "description": "points=要点 / question=题目 / answer=答案 / title=主题 / notice=提醒"},
                    "footer": {"type": "string", "description": "底部小字（例如要求或时间）"},
                    "action": {"type": "string", "description": "show（默认）/ hide"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mistake_book",
            "description": ("错题本：把学生的错题按知识点积累下来，之后可以生成针对性的重练卷。\n"
                            "action=add 记录错题（items 数组：question/answer/topic/reason/student/subject）；"
                            "action=list 查看；action=stats 看知识点分布找薄弱点；"
                            "action=practice 生成重练 docx（题目在前、参考答案另起一页）；action=remove 删除。\n"
                            "注意：topic 要用统一的学科知识点名（例如一律用“通分”），不要每题换一个说法，"
                            "否则统计不出真正的薄弱点；一次把要记的题都放进 items 里，不要反复增删。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                "enum": ["add", "list", "stats", "practice", "remove"],
                               "description": "add / list / stats / practice / remove"},
                    "items": {"type": "array", "items": {"type": "object"},
                              "description": "错题数组，每项含 question（必填）、answer、topic、reason、student、subject"},
                    "question": {"type": "string", "description": "只记一道题时可直接用"},
                    "answer": {"type": "string"}, "topic": {"type": "string"},
                    "reason": {"type": "string"}, "student": {"type": "string"},
                    "subject": {"type": "string"},
                    "limit": {"type": "integer", "description": "list 返回条数，默认 20"},
                    "count": {"type": "integer", "description": "practice 出几道题，默认全部"},
                    "out": {"type": "string", "description": "practice 的输出文件路径"},
                    "ids": {"type": "array", "items": {"type": "string"},
                            "description": "remove 要删除的错题 id"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "score_stats",
            "description": ("统计成绩表：平均分、中位数、标准差、最高/最低、及格率、优秀率、分数段分布。"
                            "由代码精确计算，避免心算出错；结果同时可以存成 xlsx 汇总表。"
                            "默认不列学生个人分数（show_names=true 才列前 3 后 3）。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "成绩表路径（xlsx/csv/txt）"},
                    "score_column": {"type": "string",
                                     "description": ("分数列的列名或第几列（从 1 开始）。"
                                                     "默认自动识别：先排除「学号/序号」等整列数字的列，"
                                                     "优先列名写着「分数/成绩」的列，不会拿学号当分数")},
                    "name_column": {"type": "string", "description": "姓名列的列名或序号，默认自动识别"},
                    "full_marks": {"type": "number", "description": "满分，默认 100"},
                    "pass_ratio": {"type": "number", "description": "及格线占满分比例，默认 0.6"},
                    "excellent_ratio": {"type": "number", "description": "优秀线比例，默认 0.85"},
                    "show_names": {"type": "boolean", "description": "是否列出前 3 / 后 3 名学生姓名"},
                    "out": {"type": "string", "description": "可选：把统计汇总与明细存成 xlsx"},
                },
                "required": ["file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_subagent",
            "description": ("把一个相对独立的子任务派给子代理去做（例如“把这份成绩表整理成统计表并给出结论”、"
                            "“按这一页知识点出 10 道题并保存为 docx”）。子代理有独立上下文，只做你交代的事，"
                            "完成后返回简短结论。可以在一轮里同时派多个子代理并行干活，主代理继续做其它部分。"
                            "适合任务能拆成几块、或单次探索会占用大量上下文的情况；简单任务不要用。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string",
                             "description": "交给子代理的完整任务描述，要写清目标、输入文件路径与产出要求"},
                    "role": {"type": "string",
                             "description": "子代理角色，例如“成绩统计助手”“出题老师”“课件排版助手”，默认通用助手"},
                    "max_rounds": {"type": "integer", "description": "子代理最多几轮工具调用，默认 4"},
                },
                "required": ["task"],
            },
        },
    },
]


# --------------------------------------------------------------------------
# 命令安全策略（internal/safety 的等价实现）
# --------------------------------------------------------------------------

def split_command_segments(command):
    segments = []
    start = 0
    quote = ""
    escaped = False
    for index, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char == "^":
            # cmd 里的转义符：只在引号外面生效（引号里的 ^ 是普通字符）
            escaped = True
            continue
        if char == '"':
            quote = char
            continue
        if char in "&|;\r\n()":
            segment = command[start:index].strip()
            if segment:
                segments.append(segment)
            start = index + 1
    tail = command[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def unwrap_caret_escapes(command):
    """去掉 cmd 的 ^ 转义，用来判断“实际会执行什么”。

    ``p^o^w^e^r^s^h^e^l^l`` 在 cmd 里就是 powershell；
    安全策略必须按去掉 ^ 之后的文本判断，否则确认框会被绕过。
    """
    result = []
    quote = ""
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            result.append(char)
            continue
        if quote:
            result.append(char)
            if char == quote:
                quote = ""
            continue
        if char == "^":
            escaped = True
            continue
        if char == '"':
            quote = char
        result.append(char)
    return "".join(result)


# 会把后面的文本当命令再执行一次的“发射器”：这些段落的参数也要按前缀检查
LAUNCHER_PREFIXES = ("start", "call", "for", "do", "if", "cmd", "command", "runas", "wmic",
                     "powershell", "pwsh", "bash", "sh", "wscript", "cscript", "mshta",
                     "rundll32", "regsvr32")


def matches_command_start(command, prefix):
    if command == prefix:
        return True
    if not command.startswith(prefix) or len(command) <= len(prefix):
        return False
    return command[len(prefix)] in ". \t"


def first_command(command):
    command = command.strip()
    if not command:
        return "", ""
    if command[0] in "'\"":
        end = command.find(command[0], 1)
        if end >= 0:
            return command[1:end], command[end + 1:].strip()
        return command[1:], ""
    parts = re.split(r"[ \t]", command, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1].strip()


def nested_windows_command(executable, rest):
    name = executable.lower()
    for ext in (".exe", ".com", ".bat", ".cmd"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    value = rest.strip()
    if name == "call":
        return strip_outer_double_quotes(value)
    if name not in ("cmd", "command", "%comspec%"):
        return ""
    while value:
        option, remaining = first_command(value)
        option = option.lower()
        if option in ("/c", "/k"):
            return strip_outer_double_quotes(remaining)
        if not option.startswith("/"):
            return ""
        value = remaining
    return ""


def strip_outer_double_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def command_segment_has_prefix(segment, prefix):
    command = segment.strip().lower().lstrip("@").strip()
    if not command:
        return False
    if matches_command_start(command, prefix):
        return True
    executable, rest = first_command(command)
    if not executable:
        return False
    executable = executable.replace("\\", "/")
    if "/" in executable:
        executable = executable.rsplit("/", 1)[-1]
    if matches_command_start(executable, prefix):
        return True
    nested = nested_windows_command(executable, rest)
    if nested:
        return has_command_prefix(nested, prefix)
    # start / for … do / call 这类会再执行一次参数的“发射器”：参数里出现目标前缀也算命中，
    # 否则 “start /min powershell …”“for /l %i in (1,1,1) do @powershell …” 就溜过去了
    name = executable.lower()
    for ext in (".exe", ".com", ".bat", ".cmd"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    if name in LAUNCHER_PREFIXES and rest:
        body = unwrap_caret_escapes(rest).lower()
        if body.strip().startswith(prefix) or (" " + prefix) in body or ("\\" + prefix) in body:
            return True
        return has_command_prefix(body, prefix)
    return False


def has_command_prefix(command, prefix):
    prefix = (prefix or "").strip().lower()
    if not prefix:
        return False
    # 先按原样判断；再去掉 caret 转义判断一次（p^o^w^e^r^s^h^e^l^l 等价于 powershell）
    for candidate in (command, unwrap_caret_escapes(command)):
        for segment in split_command_segments(candidate):
            if command_segment_has_prefix(segment, prefix):
                return True
    return False


def decode_bytes(data):
    """统一走 core 的解码规则（tools 里到处都在用，这里保留同名入口）。"""
    return core.decode_bytes(data)


def decode_text(data):
    """只要文本，不要编码名（大多数调用点用这个）。"""
    return core.decode_text(data)


def long_path(path):
    """Windows 上超过 260 字符的路径需要 ``\\\\?\\`` 前缀（Win7 没有长路径支持）。

    只在调用系统接口时用这个路径，给模型和界面看的仍然是原样路径。
    深度共享目录（\\\\server\\share\\年级\\班级\\...）在教室机器上是常见情况。

    另外：**保留设备名也必须加前缀**。老师和同事从别处（U 盘、Linux 服务器、压缩包）
    拷来的课件里可能真有 `CON.md` 这种名字，加 `\\\\?\\` 才能按普通文件打开；
    不加的话 Windows 会把 `CON` 当成控制台设备去打开（`COM1` 甚至会挂到串口上，
    直接卡住）。实测 Win7 上 `os.stat(r"...\\CON.md")` 就是走设备这条路。
    """
    if os.name != "nt":
        return path
    absolute = safe_abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if len(absolute) < 240 and not reserved_component(absolute):   # 留出余量给 .tmp / 子文件
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def safe_abspath(path):
    """把路径变成绝对路径，但**不**经过 Windows 的 GetFullPathNameW。

    实测（真 Windows 7）：`os.path.abspath(r"C:\\课件\\CON.md")` 返回的是 `\\\\.\\CON`
    ——Windows 把保留设备名解析成了设备路径。后果有两个，都很糟：

      1. 后面的名字检查拿到空 basename，"这是保留设备名"的提示永远不触发，
         makedirs / stat 转而去操作**设备**，报一句莫名其妙的"找不到文件或目录"；
      2. 读文件时 `os.stat("\\\\.\\COM1")` 会去打开串口，可能直接卡住。

    这里只用纯字符串运算（ntpath 的 join / normpath 都不碰系统调用），
    保留路径原样，再交给 check_windows_name 和 long_path 处理。
    顺带补回 Windows 会做的一件好事：结尾的句点和空格等于没有
    （`报告.` 和 `报告` 是同一个文件），这样模型写 `报告.` 不会白报错。
    """
    text = str(path or "")
    if os.name != "nt":
        return os.path.abspath(text)
    try:
        if not ntpath.isabs(text):
            text = ntpath.join(os.getcwd(), text)
        absolute = ntpath.normpath(text)
        head, tail = ntpath.split(absolute)
        if tail and tail not in (".", ".."):
            cleaned = tail.rstrip(" .")
            if cleaned and cleaned != tail:
                absolute = ntpath.join(head, cleaned)
        return absolute
    except (OSError, ValueError):
        return os.path.abspath(text)


def reserved_component(path):
    """返回路径里第一个 Windows 保留设备名（没有则返回空串）。

    Windows 的规则：`CON`、`CON.md`、`CON.tar.gz`、`CON ` 都算设备名。
    """
    for part in str(path or "").replace("/", "\\").split("\\"):
        if part and RESERVED_NAME_RE.match(part.rstrip(" .")):
            return part
    return ""


def _run_shell(command, cwd, creationflags, timeout, cancel=None):
    """执行一条 shell 命令，超时后连子孙进程一起结束。

    返回 (原始输出字节, 退出码, 附加说明文本)。附加说明用 str 返回、由调用方在
    解码之后再拼接：直接往输出字节里塞 UTF-8 中文，遇到 GBK 输出的机器会变乱码。

    cancel 被置位时（老师点了"停止"）立刻杀掉命令，不再等它跑完——教室里
    一条 `ping -n 60` 或者一个卡住的安装程序，不能让"停止"看起来没用。
    """
    if os.name == "nt":
        if cwd and cwd.startswith("\\\\"):
            # cmd.exe 不支持把 UNC 路径当工作目录（会静默改用 C:\Windows），pushd 会临时映射盘符
            command = 'pushd "%s" && %s' % (cwd, command)
            cwd = None
        argv = ["cmd.exe", "/C", command]
    else:
        argv = ["/bin/sh", "-c", command]
    process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, creationflags=creationflags)
    if cancel is None:
        try:
            output, _ = process.communicate(timeout=timeout)
            return output or b"", process.returncode, ""
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            try:
                output, _ = process.communicate(timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                output = b""
            return (output or b""), -1, "\n[命令超过 %d 秒已强制结束（含它的子进程）]" % timeout
    deadline = time.time() + timeout
    while True:
        try:
            output, _ = process.communicate(timeout=0.25)
            return output or b"", process.returncode, ""
        except subprocess.TimeoutExpired:
            if cancel.is_set():
                _kill_tree(process)
                # ★ 这里原来是 timeout=10：进程杀掉了、但子进程还攥着管道的话，
                #   老师点完停止还要再等 10 秒才看到反应。停止之后剩下的输出没有价值，
                #   2 秒收不到就直接返回（真正的"停不下来"是这条造成的）。
                try:
                    output, _ = process.communicate(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    output = b""
                return (output or b""), -1, "\n[已被停止：命令连同它的子进程已结束]"
            if time.time() > deadline:
                _kill_tree(process)
                try:
                    output, _ = process.communicate(timeout=10)
                except (subprocess.TimeoutExpired, OSError):
                    output = b""
                return (output or b""), -1, "\n[命令超过 %d 秒已强制结束（含它的子进程）]" % timeout


def write_bytes_file(path, data, opener=None):
    """原子写二进制文件（课件、成绩表都走这里）：先写 .part 再换名。

    理由同 write_text_file：写到一半被打断（杀毒、文件被占用、老师直接关机）时，
    旧文件必须还是完整的旧文件。
    """
    target = long_path(path)
    part = target + ".part"
    try:
        if opener is not None:
            with opener(part, "wb") as handle:
                handle.write(data)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        else:
            with open(part, "wb") as handle:
                handle.write(data)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
    except OSError:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    error = download_module.atomic_replace(part, target)
    if error:
        try:
            os.remove(part)
        except OSError:
            pass
        raise OSError("写文件失败：%s" % error)
    return path


def write_text_file(path, text, encoding="utf-8"):
    """原子写文本文件：先写同目录的 .part，再改名替换。

    为什么不能直接 open(...,'w')：教室里常见的两种情况——杀毒软件正扫这个文件、
    或老师正好在资源管理器里打开着它——会让写一半失败，**原文件就变成半截的**。
    改成"写完再换名"以后，要么是旧内容、要么是新内容，不会出现半截文件。
    Win7 上改名也常撞 EACCES/EBUSY/EPERM，所以复用下载那套重试表。
    """
    target = long_path(path)
    part = target + ".part"
    try:
        with open(part, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
    except OSError:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    error = download_module.atomic_replace(part, target)
    if error:
        try:
            os.remove(part)
        except OSError:
            pass
        raise OSError("写文件失败：%s" % error)
    return path


def _kill_tree(process):
    """结束进程及其子孙（Win7 有 taskkill，Linux 用进程组）。"""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=0x08000000, timeout=15)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def protected_write_reason(path, base_dir=""):
    """系统关键目录不允许被写：网页/搜索结果里的提示注入可能让模型去覆盖系统文件。

    读、以及工作区/桌面/D 盘等正常位置都不受影响；只挡住 Windows 与 Program Files，
    SystemRoot 下的 Temp 仍然允许（很多程序往那里写临时文件）。

    本程序自己的目录（base_dir）反过来是**允许写**的——agent 要能改自己的
    webui\\*.py、config\\*.json、skills\\，改完跑 self_check 再重启生效。
    只有两处例外：随包的 python\\ 运行时（改坏就起不来了）与 webui\\data
    （老师的会话历史），跟程序装在哪个盘无关，一律禁写。
    """
    if os.name != "nt":
        return None
    target = os.path.normcase(os.path.abspath(path))
    python_dir = os.path.join(base_dir, "python") if base_dir else ""
    locked = []
    if python_dir:
        locked.append((python_dir, "随包的 Python 运行时"))
    if base_dir:
        locked.append((os.path.join(base_dir, "webui", "data"), "本程序的会话与运行数据"))
    for root, label in locked:
        if _inside(target, root):
            return ("%s 是%s，不允许改写（改它会让程序起不来，或者丢掉已有的对话记录）。"
                    "自我修改请改 webui\\*.py、config\\*.json 或 skills\\ 里的文件。" % (path, label))
    roots = []
    for key in ("SystemRoot", "windir"):
        value = os.environ.get(key)
        if value:
            roots.append((value, os.path.join(value, "Temp")))
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(key)
        if value:
            roots.append((value, None))
    if python_dir:
        roots.append((python_dir, None))
    for root, allowed in roots:
        if not root:
            continue
        root_key = os.path.normcase(os.path.abspath(root))
        if target == root_key or target.startswith(root_key.rstrip("\\/") + os.sep):
            if allowed and _inside(target, allowed):
                return None
            # 例外：**本程序自己的源码/配置/技能目录**允许写入——这是"自我修改"能力的基础
            # （agent 要能改自己的 webui\*.py、config\*.json、skills\*，改完重启生效）。
            # 随包的 python\ 运行时仍然禁写（那是解释器，不是项目源码）。
            if base_dir and _inside(target, base_dir) and not (
                    python_dir and _inside(target, python_dir)):
                return None
            return ("%s 属于系统目录，出于安全考虑不允许写入（防止误覆盖系统文件）。"
                    "请改写到工作区、桌面或其它数据目录。" % path)
    return None


def check_windows_name(path):
    """写入前检查 Windows 不允许的文件名，返回给模型看得懂的说明（None 表示没问题）。"""
    name = os.path.basename(str(path).rstrip("\\/"))
    if not name:
        return None
    stem = name.split(".")[0].upper()
    if stem in RESERVED_NAMES:
        return ("Windows 不允许把文件命名为 %s（保留设备名），请换一个名字，"
                "例如 “%s-课件”。" % (name, stem.lower()))
    bad = [char for char in name if char in ':*?"<>|']
    if bad:
        return ("文件名 %s 里含有 Windows 不允许的字符 %s，请改成中文全角冒号“：”"
                "或去掉这些字符后重试。" % (name, " ".join(sorted(set(bad)))))
    if name.endswith(".") or name.endswith(" "):
        return "文件名不能以句点或空格结尾：%s" % name
    return None


def friendly_os_error(exc, path=""):
    """把 OSError 变成老师看得懂、模型能据此改错的中文说明。"""
    where = "（%s）" % path if path else ""
    winerror = getattr(exc, "winerror", None)
    if winerror == 32:      # ERROR_SHARING_VIOLATION：Word/WPS 正开着这个文件
        return "文件正在被其它程序占用%s：请先关闭打开它的 Word / WPS / 播放器再试。" % where
    if winerror == 206:     # ERROR_FILENAME_EXCED_RANGE
        return "路径太长%s：Windows 7 的路径上限是 260 个字符，请把文件放到浅一点的目录。" % where
    if winerror == 112:
        return "磁盘空间不足，无法写入%s。" % where
    if winerror == 123:
        return "路径写法不合法%s，请检查是否有多余的引号或非法字符。" % where
    if isinstance(exc, PermissionError):
        return "没有权限访问%s。换个位置（例如工作区或桌面），或先用管理员身份运行。" % (path or "")
    if isinstance(exc, FileNotFoundError):
        return "找不到文件或目录%s，请先用 list_dir 确认路径。" % where
    if isinstance(exc, IsADirectoryError):
        return "%s 是目录，不是文件。" % (path or "")
    text = str(exc).strip() or exc.__class__.__name__
    return "操作失败%s：%s" % (where, text)


def _python_fetch(url, timeout=60, insecure=False, max_bytes=3000000):
    """不依赖 agent.exe 的兜底抓取：取 HTML 后粗暴去标签，得到可读正文。"""
    request = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    context = ssl.create_default_context()
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            raw = response.read(max_bytes)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise ToolError("抓取失败：HTTP %s" % exc.code)
    except urllib.error.URLError as exc:
        raise ToolError("抓取失败：网络不可达（%s）" % exc.reason)
    except Exception as exc:  # noqa: BLE001
        raise ToolError("抓取失败：%s" % exc)
    try:
        page = raw.decode(charset, "replace")
    except LookupError:
        page = decode_text(raw)
    page = re.sub(r"<(script|style|noscript)[\s\S]*?</\1>", " ", page, flags=re.I)
    page = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>|</table>", "\n", page, flags=re.I)
    page = re.sub(r"<[^>]+>", "", page)
    text = html.unescape(page)
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------
# 工具集合
# --------------------------------------------------------------------------

class ToolError(Exception):
    pass


def _brief_args(args, limit=40):
    """把工具参数压成一行给确认框看，老师一眼能看懂要点就够了。"""
    args = args or {}
    bits = []
    for key in ("window", "action", "key", "keys", "x", "y", "to_x", "to_y", "text", "times"):
        value = args.get(key)
        if value is None or value == "":
            continue
        text = str(value).replace("\n", " ").replace("\r", " ")
        if len(text) > limit:
            text = text[:limit] + "…"
        bits.append("%s=%s" % (key, text))
    return "，".join(bits) or "无参数"


class Tools(object):
    def __init__(self, settings, data_dir, agent_exe=None):
        self.settings = settings
        self.data_dir = data_dir
        self.agent_exe = agent_exe or os.path.join(settings.base_dir, "agent.exe")
        self.snapshot_dir = os.path.join(data_dir, "snapshots")
        self.root = settings.workspace_root()
        self.command = settings.agent.get("command") or {}
        self.max_chars = int(settings.agent.get("max_content_chars") or 120000)
        # 会话与事件出口由 AgentRun 注入；用 threading.local 是因为子代理可能并行执行
        self._local = threading.local()
        # 取消事件：AgentRun 每轮开始前注入。长任务（命令、下载、PowerShell）要定期看它，
        # 否则老师点了"停止"还要傻等，界面看起来就是"停不下来"。
        self.cancel = None
        # 后台下载任务（对应 DSH 的 jobs）：id -> 状态，供 download_status 查询
        self._jobs = {}
        self._jobs_lock = threading.Lock()
        self._job_seq = 0

    def session_id(self):
        session = self.session or {}
        return session.get("id") or ""

    def cancelled(self):
        cancel = self.cancel
        return cancel is not None and cancel.is_set()

    def check_cancel(self, what="这个操作"):
        """长任务开工前/中途调用：被停止就抛一个说明清楚的错误。"""
        if self.cancelled():
            raise ToolError("已被停止：%s还没开始做。老师说停就停，别继续了。" % what)

    @property
    def session(self):
        return getattr(self._local, "session", None)

    @session.setter
    def session(self, value):
        self._local.session = value

    @property
    def emit(self):
        return getattr(self._local, "emit", None)

    @emit.setter
    def emit(self, value):
        self._local.emit = value

    def _classroom(self, function, args):
        """课堂类工具：把 ValueError 转成对模型友好的工具错误。"""
        try:
            return function(self, args)
        except ValueError as exc:
            raise ToolError(str(exc))

    # -- 沙箱 -------------------------------------------------------------
    def resolve(self, path, must_exist=False, for_write=False):
        if not path or not str(path).strip():
            raise ToolError("路径不能为空")
        candidate = os.path.expandvars(os.path.expanduser(str(path).strip().strip('"')))
        if not os.path.isabs(candidate):
            candidate = os.path.join(self.root, candidate)
        # 用 safe_abspath：Win7 上 abspath 会把 CON/COM1 这类名字变成设备路径，
        # 后面的名字检查就失效了（详见 safe_abspath 的说明）
        absolute = safe_abspath(candidate)
        if not self.settings.webui.get("allow_outside_workspace"):
            if not _inside(absolute, self.root):
                raise ToolError(
                    "路径 %s 在工作区 %s 之外。请改用工作区内的路径，"
                    "或在 WebUI 设置中开启“允许读写工作区之外的路径”。" % (absolute, self.root))
        if must_exist:
            try:
                os.stat(long_path(absolute))
            except OSError as exc:
                # os.path.exists 会把“拒绝访问”也当成不存在，这里区分开：
                # 只有 winerror 2/3（找不到路径/文件）才说“不存在”
                if getattr(exc, "winerror", None) in (2, 3):
                    raise ToolError("文件或目录不存在：%s" % absolute)
                raise ToolError(friendly_os_error(exc, absolute))
        if for_write:
            bad = check_windows_name(absolute)
            if bad:
                raise ToolError(bad)
            protected = protected_write_reason(absolute, self.settings.base_dir)
            if protected:
                raise ToolError(protected)
            parent = os.path.dirname(absolute)
            if parent and not os.path.isdir(long_path(parent)):
                try:
                    os.makedirs(long_path(parent), exist_ok=True)
                except OSError as exc:
                    raise ToolError(friendly_os_error(exc, parent))
        return absolute

    def open_path(self, path, mode="rb"):
        """统一走 long_path() 的 open：长路径在 Win7 上必须加 \\\\?\\ 前缀。"""
        try:
            return open(long_path(path), mode)
        except OSError as exc:
            raise ToolError(friendly_os_error(exc, path))

    def specs(self):
        return TOOL_SPECS

    # -- 授权 -------------------------------------------------------------
    def authorize(self, name, args):
        """返回 {'allow':bool, 'need_confirm':bool, 'reason':str, 'output':str, 'status':str}。"""
        if name == "run_command":
            command = str((args or {}).get("command") or "").strip()
            if not self.command.get("enabled", True):
                return {"allow": False, "output": "命令执行已在配置中禁用（command.enabled=false）。",
                        "status": "denied"}
            if not command:
                return {"allow": False, "output": "命令为空。", "status": "error"}
            lower = command.lower()
            for prefix in self.command.get("blocked_prefixes") or []:
                if has_command_prefix(lower, prefix):
                    return {"allow": False, "status": "denied",
                            "output": "命令前缀 %s 已被配置拦截，无法执行。" % prefix.strip()}
            if self.settings.webui.get("confirm_high_risk_commands"):
                confirm = bool(self.command.get("always_confirm"))
                reason = "配置要求所有命令都必须确认"
                if not confirm:
                    for prefix in self.command.get("confirm_prefixes") or []:
                        if has_command_prefix(lower, prefix):
                            confirm = True
                            reason = "命令包含高风险前缀 %s，需要确认" % prefix.strip()
                            break
                if confirm:
                    return {"allow": True, "need_confirm": True, "reason": reason}
            # 默认：高风险命令也直接执行（老师要的是"说一句就干完"）；
            # blocked_prefixes 的硬拦截在上面已经处理，不受这个开关影响。
            return {"allow": True}
        if name in MESSAGE_TOOLS:
            labels = {"send_email": "发送邮件", "notify_webhook": "推送到群机器人",
                      "send_qq_message": "用 QQ 发消息（会模拟键鼠操作）"}
            detail = ""
            if name == "send_email":
                detail = "收件人 %s，主题「%s」" % (str((args or {}).get("to") or "（默认收件人）")[:60],
                                                 str((args or {}).get("subject") or "")[:40])
            elif name == "send_qq_message":
                detail = "发给 %s" % (str((args or {}).get("contact") or "当前聊天窗口")[:40])
            else:
                detail = str((args or {}).get("text") or "")[:40]
            # 发消息默认也是直接发（confirm_messages 打开后才弹确认）
            if not self.settings.webui.get("confirm_messages"):
                return {"allow": True}
            return {"allow": True, "need_confirm": True,
                    "reason": "%s：%s" % (labels[name], detail)}
        if name in DESKTOP_TOOLS:
            if not self.settings.webui.get("allow_desktop_control", True):
                return {"allow": False, "status": "denied",
                        "output": "桌面控制已在 WebUI 设置里关闭（设置 → 电脑操作）。"
                                  "需要截屏/鼠标键盘这类操作时，请老师先打开这个开关。"}
            if name not in DESKTOP_INPUT_TOOLS:
                return {"allow": True}
            # 只读的部分不打扰老师：读剪贴板、列进程、读注册表、切窗口都在此列
            action = str((args or {}).get("action") or "").lower()
            if name == "clipboard" and action in ("", "get", "read"):
                return {"allow": True}
            if name == "process" and action in ("", "list"):
                return {"allow": True}
            if name == "registry" and action in ("", "get", "list"):
                return {"allow": True}
            if name == "app" and str((args or {}).get("mode") or "").lower() != "close":
                return {"allow": True}
            # 默认不弹确认：老师要的是"说一句就干完"，每次批一次鼠标很烦。
            # 想收紧时把设置里的「动手前先问我」打开。
            if not self.settings.webui.get("confirm_desktop_input"):
                return {"allow": True}
            labels = {"app": "关闭窗口", "clipboard": "改写剪贴板",
                      "process": "结束进程", "registry": "改注册表",
                      "click": "模拟鼠标：会真的点在屏幕上",
                      "type": "模拟键盘：会把文字输入到窗口里",
                      "scroll": "模拟滚轮", "move": "移动鼠标 / 拖动",
                      "shortcut": "发送快捷键（多媒体键也在内）",
                      "multi_select": "多选：会按住 Ctrl 连点",
                      "multi_edit": "批量填表：会逐格点开再输入",
                      "draw_path": "在白板/画图里画图：会真的移动鼠标"}
            return {"allow": True, "need_confirm": True,
                    "reason": "%s（%s）" % (labels.get(name, "桌面操作"), _brief_args(args))}
        if name not in ("read_file", "write_file", "edit_file", "list_dir", "read_url", "view_image",
                        "web_search", "pick_students", "class_timer", "show_on_screen",
                        "show_figure", "mistake_book", "score_stats",
                        # 平面作图（只改平面状态，不碰文件/键鼠，所以不需要确认）
                        "plane", "plane_batch",
                        # 音视频/课件体检（replace_media 会先自动快照）
                        "inspect_media", "diagnose_pptx", "extract_media", "replace_media",
                        # 自我修改闭环：自检 + 重启（重启前会写独立小脚本，见 _tool_restart_service）
                        "self_check", "restart_service",
                        # 联网下载（受 allow_outside_workspace 与 blocklist 约束的普通写文件）
                        "download", "download_status"):
            return {"allow": False, "output": "未知工具：%s" % name, "status": "error"}
        return {"allow": True}

    # -- 执行 -------------------------------------------------------------
    def run(self, name, args):
        args = args or {}
        if self.cancelled():
            raise ToolError("已被停止：这个工具没有被执行（老师点了停止）。"
                            "如果还想做，请老师重新说一次。")
        if "__raw__" in args:
            raise ToolError("工具参数不是合法 JSON（多半是被长度上限截断了，请把这次的"
                            "工具调用拆小一点重发）：%s" % args["__raw__"][:200])
        handler = getattr(self, "_tool_" + name, None)
        if handler is None:
            raise ToolError("未知工具：%s" % name)
        output = handler(args)
        if name in SPILL_TOOLS:
            output = self._spill(name, output)
        return output

    def _tool_list_dir(self, args):
        path = self.resolve(args.get("path"), must_exist=True)
        if not os.path.isdir(long_path(path)):
            raise ToolError("不是目录：%s" % path)
        pattern = args.get("pattern") or ""
        entries = []
        try:
            names = sorted(os.listdir(long_path(path)))
        except OSError as exc:
            raise ToolError(friendly_os_error(exc, path))
        if len(names) > 20000:      # 超大目录（例如整个 D 盘）先提醒，避免 stat 几万次卡住
            names = names[:20000]
            entries.append(("…… 目录项过多，只列出前 20000 个 ……", False, 0, ""))
        for name in names:
            if pattern and not _wildcard_match(name, pattern):
                continue
            # 每一项都要过 long_path：保留设备名（CON.md）与超长路径都必须带 \\?\ 前缀，
            # 否则 Win7 会把 os.stat 导向**设备**——实测这种条目会被 except 悄悄丢掉
            # （list_dir 里看不到文件），COM1 之类的还可能挂在串口上。
            probe = long_path(os.path.join(path, name))
            try:
                stat = os.stat(probe)
                is_dir = os.path.isdir(probe)
            except OSError:
                # 还是读不到就如实列出来并说明，不要让文件凭空消失
                entries.append((name, False, 0, "读不到属性"))
                continue
            entries.append((name, is_dir, stat.st_size,
                            time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))))
        if not entries:
            text = "目录 %s 下没有匹配的条目。" % path
        else:
            lines = ["目录 %s 共 %d 项：" % (path, len(entries))]
            for name, is_dir, size, mtime in entries[:400]:
                lines.append("%s %-40s %10s  %s" % ("[目录]" if is_dir else "[文件]",
                                                    name, "" if is_dir else _human(size), mtime))
            if len(entries) > 400:
                lines.append("…… 其余 %d 项已省略" % (len(entries) - 400))
            text = "\n".join(lines)
        return text, {"path": path, "count": len(entries), "kind": "list"}

    def _tool_read_file(self, args):
        path = self.resolve(args.get("path"), must_exist=True)
        if os.path.isdir(long_path(path)):
            raise ToolError("这是目录，请使用 list_dir：%s" % path)
        ext = os.path.splitext(path)[1].lower()
        encoding = ""
        try:
            if ext == ".docx":
                text, kind = office.read_docx(long_path(path)), "docx"
            elif ext == ".xlsx":
                text, kind = office.read_xlsx(long_path(path)), "xlsx"
            elif ext == ".pptx":
                text, kind = office.read_pptx(long_path(path)), "pptx"
            elif core.is_image_path(path):
                raise ToolError("这是图片文件，请改用 view_image 查看：%s" % path)
            elif ext == ".zip":
                # 模型没法从压缩字节里读出内容：替它解开，并把目录树报回去
                return self._read_zip(path)
            elif media_module.is_media_path(path):
                # 音视频一律不吐二进制，改吐"这份媒体是什么" + 同名字幕（见 _read_media_text）
                return self._read_media_text(path)
            elif ext in TEXT_EXTS or sniff_text(path)[0]:
                text, encoding = self._read_text(path)
                kind = "text"
            else:
                raise ToolError(self._unreadable_reason(path, ext))
        except ToolError:
            raise
        except OSError as exc:
            raise ToolError(friendly_os_error(exc, path))
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise ToolError("文件已损坏或不是标准的 Office 文件（%s）：%s" % (path, exc))
        all_lines = text.split("\n")
        start = max(1, int(args.get("start_line") or 1))
        max_lines = int(args.get("max_lines") or 400)
        window = all_lines[start - 1:start - 1 + max_lines]
        meta = {"path": path, "kind": kind, "lines": len(all_lines),
                "shown": len(window), "truncated": False,
                "bytes": len(text.encode("utf-8", "replace"))}
        if encoding:
            meta["encoding"] = encoding
        if not window:
            # 起始行超出文件范围：明确告诉模型，别丢一个空的工具结果回去
            return ("起始行 %d 超出文件范围（共 %d 行）。" % (start, len(all_lines))), meta
        truncated = (start - 1 + len(window)) < len(all_lines) or len(text) > self.max_chars
        meta["truncated"] = truncated
        numbered = "\n".join("%d\t%s" % (start + index, line) for index, line in enumerate(window))
        if truncated:
            numbered += ("\n\n[只显示第 %d-%d 行，共 %d 行；可调整 start_line/max_lines 继续读取]"
                         % (start, start + len(window) - 1, len(all_lines)))
        if len(numbered) > self.max_chars:
            numbered = numbered[:self.max_chars] + "\n\n[内容已被截断]"
        return numbered, meta

    def _read_text(self, path):
        """读文本：小文件整读（行号、总行数准确），大文件只读到上限，避免把内存吃满。"""
        size = os.path.getsize(long_path(path))
        with self.open_path(path, "rb") as handle:
            raw = handle.read(MAX_TEXT_BYTES)
        text, encoding = decode_bytes(raw)
        if size > MAX_TEXT_BYTES:
            text += ("\n\n[文件较大（%.1f MB），只读取了前 %d MB；"
                     "如需后面内容请用支持流式读取的命令行工具，或先拆分文件]"
                     % (size / 1048576.0, MAX_TEXT_BYTES // 1048576))
        return text, encoding

    def _unreadable_reason(self, path, ext):
        """读不了的文件要说清楚"它到底是什么"。

        模型只有知道这是什么，才知道该换哪个工具（图片→view_image、音视频→inspect_media），
        或者该让老师先转个格式——比一句"不支持"有用得多。
        """
        kind = binary_kind(path)
        if kind and kind != "压缩包（zip / docx / xlsx / pptx 都是这个结构）":
            advice = MAGIC_ADVICE.get(kind, "图片用 view_image，音视频用 inspect_media。")
            return ("读不了 %s：里面的内容是%s，不是文本（当文本读会把二进制垃圾灌进对话）。%s"
                    % (path, kind, advice))
        if kind:
            return ("读不了 %s：里面的内容是%s，但这个包的扩展名不是 .zip，"
                    "本程序只自动解压 .zip。请把它改名成 .zip 再让我读。" % (path, kind))
        return ("读不了 %s：%s 这个类型不在支持范围里，内容也不像文本"
                "（可能是加密文件或厂商私有格式）。"
                "支持文本、docx/xlsx/pptx、zip；音视频用 inspect_media，图片用 view_image。"
                % (path, ext or "它没有扩展名"))

    def _read_zip(self, path):
        """把 .zip 解压到工作区，返回「解压到哪 + 目录树」，模型随后就能逐个 read_file。

        老师给的作业包、课件包几乎都是 zip，而模型自己读不出压缩字节，所以这里替它解开。
        安全边界一条都不能省：zip-slip 拒整包、zip 炸弹限总量与条目数、
        任何失败都把已经解出来的半截目录删干净（老师看不到"半份作业"）。
        """
        if not zipfile.is_zipfile(long_path(path)):
            raise ToolError("%s 不是有效的 zip 压缩包（文件可能已损坏，或者扩展名被改过）。"
                            % path)
        try:
            archive = zipfile.ZipFile(long_path(path))
        except (zipfile.BadZipFile, OSError) as exc:
            raise ToolError("打开压缩包失败：%s" % friendly_os_error(exc, path))
        with archive:
            members = archive.infolist()
            if not members:
                raise ToolError("这个压缩包里没有任何文件：%s" % path)
            if len(members) > MAX_ZIP_MEMBERS:
                raise ToolError("压缩包里有 %d 个条目，超过上限 %d 个（防 zip 炸弹），没有解压。"
                                % (len(members), MAX_ZIP_MEMBERS))
            # 先把整包"能不能安全解开"查完再动手：宁可一个都不解，也不要解一半再删
            declared = 0
            for info in members:
                if info.flag_bits & 0x1:
                    raise ToolError("压缩包里的 %s 是加密的（需要密码），本程序解不开。"
                                    "请在电脑上解压后把文件直接给我。" % info.filename[:80])
                reason = _zip_member_reason(info)
                if reason:
                    raise ToolError("%s。为了安全不跳过可疑条目，整个压缩包没有解压。" % reason)
                name_problem = check_windows_name(info.filename)
                if name_problem:
                    raise ToolError("压缩包里有 Windows 建不出来的条目名（%s），整个包没有解压。"
                                    "请在电脑上解压后改名再给我。" % info.filename[:80])
                declared += max(0, info.file_size)
                if declared > MAX_ZIP_TOTAL_BYTES:
                    raise ToolError("压缩包解压后超过 %d MB（防 zip 炸弹），没有解压。"
                                    "确实需要的话请在电脑上解压后，把要用的那几个文件单独给我。"
                                    % (MAX_ZIP_TOTAL_BYTES // 1048576))
            dest = self._zip_destination(path)
            written = 0
            try:
                for info in members:
                    target = os.path.join(dest, *_zip_parts(info.filename))
                    if not _inside(target, dest):   # 双保险：上面已挡过绝对路径与 ..
                        raise ToolError("压缩包里的 %s 会写到目标目录之外，已中止。"
                                        % info.filename[:80])
                    if info.is_dir() or info.filename.endswith(("/", "\\")):
                        os.makedirs(long_path(target), exist_ok=True)
                        continue
                    parent = os.path.dirname(target)
                    if parent and not os.path.isdir(long_path(parent)):
                        os.makedirs(long_path(parent), exist_ok=True)
                    with archive.open(info) as source:
                        with open(long_path(target), "wb") as handle:
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                written += len(chunk)
                                # 条目声明的大小可以撒谎，所以边写边算，超了立刻停手
                                if written > MAX_ZIP_TOTAL_BYTES:
                                    raise ToolError("解压出来的内容超过 %d MB（防 zip 炸弹），已中止。"
                                                    % (MAX_ZIP_TOTAL_BYTES // 1048576))
                                handle.write(chunk)
            except ToolError:
                _remove_tree(dest)
                raise
            except Exception as exc:    # BadZipFile / zlib.error / 磁盘出错都归这里
                _remove_tree(dest)
                raise ToolError("解压到一半失败了（压缩包可能已损坏或加密），"
                                "已经解出来的那部分也删掉了：%s"
                                % friendly_os_error(exc, path))
        lines, files = _zip_tree_lines(dest)
        text = "\n".join(
            ["已把压缩包解压到工作区：%s" % dest,
             "共 %d 个文件，合计 %.1f MB（老师在网页的「工作区」里就能看到它）。"
             % (files, written / 1048576.0),
             "", "解压出来的内容（相对路径）："] + lines +
            ["", "下一步：用 read_file 打开上面任意一个文件——它们已经在工作区里，"
                 "可以直接改，也可以用来生成课件。"])
        return text, {"path": path, "kind": "zip", "dir": dest, "files": files, "bytes": written}

    def _zip_destination(self, path):
        """解压目标：工作区下以压缩包命名的目录；重名就加 -2 / -3。

        不覆盖上一次的结果：同一个包连着读两次，第二次不该把第一次解出来的
        （可能已经被改过的）文件清掉。
        """
        base = re.sub(r'[\\/:*?"<>|]+', "_",
                      os.path.splitext(os.path.basename(path))[0]).strip(" .")
        if not base or RESERVED_NAME_RE.match(base):
            base = (base or "解压") + "-解压"
        dest = os.path.join(self.root, base)
        index = 1
        while os.path.exists(long_path(dest)):
            index += 1
            dest = os.path.join(self.root, "%s-%d" % (base, index))
        return dest

    def _read_media_text(self, path):
        """音视频文件：不吐二进制，改吐「这份媒体到底是什么」的说明书。

        模型把 read_file 指到 mp3/wav 上是很自然的事（"这是这节课的录音，帮我整理一下"），
        所以这里给真实有用的东西：容器/编码/时长 + 音频参数 + 标签，以及**同名字幕文件**
        ——课堂里"音频→文字"真正靠谱的来源。离线纯标准库做不了语音识别，
        这一点必须明说，不能假装转了。
        """
        info = media_module.inspect_media(long_path(path))
        lines = [media_module.media_report_text(info)]
        kind = media_module.MEDIA_EXTS.get(media_module.media_ext(path)) or "media"
        meta = {"path": path, "kind": kind, "container": info["container"],
                "audio": info["audio"], "video": info["video"],
                "duration": info["duration"], "bytes": info["bytes"]}
        if kind == "audio":
            tags, params = audio_details(long_path(path))
            meta["tags"] = tags
            lines.append("")
            lines.append("音频参数：" + (" ｜ ".join(params) if params
                                        else "（这个格式的比特率/采样率没读到；"
                                             "wav / mp3 / flac 一般能读出来）"))
            lines.append("标签：" + (" ｜ ".join("%s：%s" % item for item in tags.items()) if tags
                                    else "（文件里没有 ID3 / Vorbis / MP4 标签）"))
        transcript = find_transcript(path)
        if transcript:
            meta["transcript"] = transcript
            content, encoding = self._read_text(transcript)
            if len(content) > self.max_chars:
                content = content[:self.max_chars] + "\n\n[字幕太长，已截断]"
            lines += ["",
                      "同名的字幕文件：%s（%s，共 %d 行）"
                      % (transcript, encoding, len(content.split("\n"))),
                      "**下面是这份字幕文件本来就有的内容，不是把语音转成文字的结果**"
                      "——录音/录像带字幕是课堂里最常见的情况，直接拿它当讲稿用。",
                      "----- 字幕内容开始 -----", content, "----- 字幕内容结束 -----"]
        else:
            meta["transcript"] = ""
            lines += ["",
                      "先如实说明：把语音转成文字（语音识别）需要额外的模型或者"
                      "联网服务，本程序只有 Python 标准库、还要在离线的教室机器上跑，"
                      "两样都没有，所以不会假装转写。",
                      "",
                      "这次能做的：",
                      "1) 录音/录像旁边通常有一份同名的时间轴字幕（.srt / .vtt / .lrc / .txt）："
                      "把它放在同一个文件夹里，再让我读一次这个音频，我就能把字幕内容读出来；",
                      "2) 只要画面：用悬浮工具栏的「截图给AI」把关键画面拍给我，我能看图讲解；",
                      "3) 要逐字稿：先在手机/电脑上用「讯飞听见 / 剪映 / QQ影音」这类工具"
                      "转成文本，再把文本文件给我（读文本我完全没问题）。"]
        return "\n".join(lines), meta

    # -- 超大工具结果：完整内容落盘，只回一段预览 ------------------------------
    def _spill(self, name, output):
        """工具结果太大时，完整内容写进文件，返回「开头 + 结尾 + 路径 + 原始大小」。

        社区通行做法（pydantic-harness 的 ToolOutputLimits、hermes-agent 的
        "recoverable truncation: spill full output"）：不要把整份输出塞进上下文，
        也不要直接丢掉——留一个能再读的文件，并如实说明截断前的原始大小。
        """
        text, meta = output if isinstance(output, tuple) else (output, None)
        if not isinstance(text, str) or len(text) <= SPILL_THRESHOLD_CHARS:
            return output
        folder = os.path.join(self.root, "工具输出")
        try:
            if not os.path.isdir(long_path(folder)):
                os.makedirs(long_path(folder), exist_ok=True)
            target = os.path.join(folder, "%s-%s.txt" % (name, time.strftime("%Y%m%d-%H%M%S")))
            with open(long_path(target), "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            return output            # 落盘失败就照原样返回：绝不把一个成功的结果变成失败
        preview = ("%s\n\n[... 中间省略：完整输出共 %d 字符，已保存到 %s，"
                   "需要细节请用 read_file 分段读取 ...]\n\n%s"
                   % (text[:SPILL_HEAD_CHARS], len(text), target, text[-SPILL_TAIL_CHARS:]))
        if isinstance(meta, dict):
            meta = dict(meta)
            meta["spilled"] = target
            meta["chars"] = len(text)
            return preview, meta
        return preview

    def _tool_edit_file(self, args):
        """按「查找 → 替换」改文件：比整篇重写省 token，也不会顺手改掉没提到的内容。

        查找失败时给出最接近的位置提示——社区经验（aider）：报错必须告诉模型"差在哪"，
        否则它会一遍遍重复同一个替换，把整轮耗光。
        """
        path = self.resolve(args.get("path"), must_exist=True, for_write=True)
        if os.path.isdir(long_path(path)):
            raise ToolError("这是目录，不能编辑：%s" % path)
        find = args.get("find")
        replace = args.get("replace")
        if find is None or str(find) == "":
            raise ToolError("find 不能为空：写清楚要被替换的原文（建议带上前后几行，保证唯一）")
        if replace is None:
            raise ToolError("replace 不能为空；要删除内容就把 replace 传成空字符串")
        try:
            with open(long_path(path), "r", encoding="utf-8", errors="replace") as handle:
                original = handle.read()
        except OSError as exc:
            raise ToolError(friendly_os_error(exc, path))
        # 只有"整份文件都是 CRLF"时才把结果转回 CRLF。混用换行的文件里一律转换，
        # 会把这次没碰过的那些行也一起改掉——模型在 diff 里看不到，老师却会发现整篇都变了。
        newline = "\r\n" if ("\r\n" in original and "\n" not in original.replace("\r\n", "")) else "\n"
        find_text = str(find).replace("\r\n", "\n")
        replace_text = str(replace).replace("\r\n", "\n")
        haystack = original.replace("\r\n", "\n")
        count = haystack.count(find_text)
        used = "exact"
        if count == 0:
            match = re.search(_loose_pattern(find_text), haystack)
            if match:
                find_text = match.group(0)
                count = 1
                used = "loose"
        if count == 0:
            raise ToolError("在 %s 里找不到要替换的内容。请先用 read_file 确认原文"
                            "（注意缩进、全角半角，以及文件是不是已经被改过了）。%s"
                            % (path, _closest_hint(haystack, find_text)))
        if count > 1 and not args.get("replace_all"):
            raise ToolError("这段原文在文件里出现了 %d 次，无法确定改哪一处：把 find 写长一点"
                            "（带上前后各一行），或者用 replace_all=true 全部替换。" % count)
        if args.get("replace_all"):
            updated = haystack.replace(find_text, replace_text)
            replaced = count
        else:
            updated = haystack.replace(find_text, replace_text, 1)
            replaced = 1
        if newline == "\r\n":
            updated = updated.replace("\n", "\r\n")
        snapshot = self._snapshot(path)
        try:
            write_text_file(path, updated)
        except OSError as exc:
            raise ToolError(friendly_os_error(exc, path))
        lines = ["已替换 %d 处：%s" % (replaced, path),
                 "匹配方式：%s" % ("精确" if used == "exact" else "忽略空白差异后匹配（请核对改动）")]
        if snapshot:
            lines.append("快照：%s（可用快照回滚）" % snapshot)
        check, syntax_ok = syntax_check(path)
        if check:
            lines.append(check)
        _added, _removed, diff = _diff(original, updated, limit=40)
        if diff:
            lines.append("")
            lines.append(diff)
        return "\n".join(lines), {"path": path, "kind": "edit", "replaced": replaced,
                                  "match": used, "snapshot": snapshot,
                                  "syntax": "ok" if syntax_ok else "error"}

    def _tool_write_file(self, args):
        path = self.resolve(args.get("path"), for_write=True)
        content = args.get("content")
        if content is None:
            raise ToolError("缺少 content 参数")
        content = str(content)
        ext = os.path.splitext(path)[1].lower()
        create_only = bool(args.get("create_only"))
        existed = os.path.exists(long_path(path))
        if existed and create_only:
            raise ToolError("文件已存在，且 create_only=true：%s" % path)
        old_text = ""
        snapshot = ""
        if existed:
            snapshot = self._snapshot(path)
            if ext in TEXT_EXTS or ext == "":
                try:
                    with self.open_path(path, "rb") as handle:
                        old_text = decode_text(handle.read(MAX_TEXT_BYTES))
                except (ToolError, OSError):
                    old_text = ""

        meta = {"path": path, "created": not existed, "snapshot": snapshot}
        try:
            if ext == ".docx":
                data = office.build_docx(content)
            elif ext == ".xlsx":
                data = office.build_xlsx(office.parse_rows(content))
            elif ext == ".pptx":
                template = args.get("template") or ""
                template_path = ""
                if template:
                    template_path = self.resolve(template, must_exist=True)
                data, slides = office.build_pptx(content, title=args.get("title"),
                                                 template=long_path(template_path) if template_path else "")
                problems = office.check_package(data)
                if problems:
                    raise ToolError("生成的课件结构异常：%s" % "；".join(problems[:3]))
                meta["slides"] = len(slides)
                meta["template"] = template_path
                meta["kind"] = "pptx"
                notes = office.slides_to_notes(slides, title=args.get("title"))
                if notes:
                    notes_path = os.path.splitext(path)[0] + ".讲稿.md"
                    if os.path.exists(long_path(notes_path)):
                        self._snapshot(notes_path)
                    with self.open_path(notes_path, "wb") as handle:
                        handle.write(notes.encode("utf-8"))
                    meta["notes_path"] = notes_path
            else:
                data = content.encode("utf-8")
        except ToolError:
            raise
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ToolError("模板文件已损坏或不是标准的 Office 文件：%s" % exc)
        except OSError as exc:
            raise ToolError(friendly_os_error(exc, path))

        try:
            write_bytes_file(path, data, self.open_path)
        except OSError as exc:
            raise ToolError(friendly_os_error(exc, path))

        added = removed = 0
        diff_text = ""
        if ext in TEXT_EXTS or ext == "":
            added, removed, diff_text = _diff(old_text, content)
            meta["diff"] = _diff_payload(old_text, content)
        elif existed:
            added = len(content.split("\n"))

        summary = ["已写入 %s（%d 字节）" % (path, len(data))]
        if ext == ".pptx":
            summary.append("生成幻灯片 %d 张（16:9，标题 36pt / 正文 28pt，中文字体 微软雅黑）"
                           % meta.get("slides", 0))
            if meta.get("template"):
                summary.append("已套用模板：%s" % meta["template"])
            if meta.get("notes_path"):
                summary.append("演讲者备注已单独保存为讲稿：%s" % meta["notes_path"])
        if existed:
            summary.append("已覆盖原文件，快照：%s" % (snapshot or "无"))
            if diff_text:
                summary.append("变更：+%d 行 / -%d 行" % (added, removed))
        else:
            summary.append("新建文件")
        meta["added"] = added
        meta["removed"] = removed
        meta["bytes"] = len(data)
        text = "\n".join(summary)
        if diff_text:
            text += "\n\n" + diff_text
        check, syntax_ok = syntax_check(path)
        if check:
            text += "\n" + check
            meta["syntax"] = "ok" if syntax_ok else "error"
        else:
            meta["syntax"] = "ok"
        return text, meta

    def _tool_view_image(self, args):
        """把本机图片附加给模型：工具本身只返回说明，真正的图片在下一次请求里注入。"""
        path = self.resolve(args.get("path"), must_exist=True)
        if os.path.isdir(long_path(path)):
            raise ToolError("这是目录，不是图片：%s" % path)
        mime = core.image_mime(path)
        if not mime:
            raise ToolError("不是支持的图片格式（支持 png/jpg/jpeg/gif/webp/bmp）：%s" % path)
        size = os.path.getsize(long_path(path))
        if size > core.MAX_IMAGE_BYTES:
            raise ToolError("图片过大（%.1f MB），请先压缩或裁剪到 %d MB 以内：%s"
                            % (size / 1048576.0, core.MAX_IMAGE_BYTES // 1048576, path))
        try:
            with open(path, "rb") as handle:
                head = handle.read(262144)
        except OSError as exc:
            raise ToolError("无法读取图片：%s" % exc)
        width, height = core.image_size(head)
        dimension = "%dx%d" % (width, height) if width else "尺寸未知"
        text = ("已附加图片 %s（%s，%s，%.0f KB）。图片内容会在下一轮提供给你，"
                "收到后请描述并分析图中的内容。" % (path, mime, dimension, size / 1024.0))
        return text, {"kind": "image", "path": path, "mime": mime, "bytes": size,
                      "width": width, "height": height, "inject_image": path}

    # -- 桌面控制 ---------------------------------------------------------
    def _desktop(self):
        """桌面控制的入口：受 webui.allow_desktop_control 开关控制。"""
        if not self.settings.webui.get("allow_desktop_control", True):
            raise ToolError("桌面控制已在 WebUI 设置里关闭（设置 → 电脑操作）。需要时请老师打开。")
        return desktop_module

    def _desktop_shot_path(self, given):
        """截图存哪儿：给了就用给的；留空 = 工作区「截图」目录里按时间命名。"""
        text = str(given or "").strip()
        if text:
            return self.resolve(text, for_write=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return self.resolve(os.path.join(self.root, "截图", "界面-%s.png" % stamp), for_write=True)

    @staticmethod
    def _desktop_int(value, default, label):
        """模型给的数字可能是字符串；给得离谱就说人话，别把原始异常抛出去。"""
        if value in (None, ""):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ToolError("%s 要是个整数，收到的是 %r。" % (label, value))

    @staticmethod
    def _desktop_float(value, default, label):
        if value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ToolError("%s 要是个数字，收到的是 %r。" % (label, value))

    @staticmethod
    def _desktop_region(region):
        """region 允许 [x, y, 宽, 高]，也容忍 "x,y,w,h" 这种写法；没给就是 None。"""
        if region in (None, ""):
            return None
        if isinstance(region, str):
            region = [item.strip() for item in region.replace("，", ",").split(",") if item.strip()]
        if isinstance(region, (list, tuple)):
            try:
                return [int(float(item)) for item in region]
            except (TypeError, ValueError):
                raise ToolError("region 要写成 [x, y, 宽, 高]（屏幕物理像素）。")
        raise ToolError("region 要写成 [x, y, 宽, 高]（屏幕物理像素）。")

    @staticmethod
    def _desktop_points(locs):
        """[[x, y], …] 或 [[x, y, 文字], …] → [(x, y), …]；形状不对就返回空的（交给 desktop 报错）。"""
        points = []
        if not isinstance(locs, (list, tuple)):
            return points
        for item in locs:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                return []
            try:
                points.append((int(item[0]), int(item[1])))
            except (TypeError, ValueError):
                return []
        return points

    def _desktop_stale(self, locs):
        """动手之前比一遍：这些坐标还按上次截图那样吗？不按就返回一句给模型看的人话。

        （`_stale_click_point` / `_shot_layout` / `_window_at` / `_desktop_layout` 在下面。）
        """
        for point_x, point_y in self._desktop_points(locs):
            stale = self._stale_click_point(point_x, point_y)
            if stale:
                return stale
        return ""

    def _desktop_mark_shot(self):
        """把此刻的窗口布局记成"上次看到的样子"，下次按 loc 动手前拿它比。"""
        try:
            self._shot_layout = self._desktop_layout()
        except Exception:                                 # noqa: BLE001 - 记不住就当没记
            pass

    @staticmethod
    def _desktop_note(info):
        """desktop 用 label 重算坐标时会带一句 note（说明界面变了），原样转达。"""
        note = str((info or {}).get("note") or "").strip()
        return ("\n（%s）" % note) if note else ""

    @staticmethod
    def _desktop_too_big(size, limit):
        """图太大（模型拿不到画面）时的统一提示：得调小 scale 重来。"""
        return ("\n（这张图 %.1f MB，超过一次能给模型的 %.0f MB，这次看不到画面；"
                "把 scale 调小重来一次，例如 scale=0.5。）"
                % (float(size or 0) / 1048576.0, float(limit or 0) / 1048576.0))

    @staticmethod
    def _desktop_image_meta(path, info=None):
        """截图类工具统一的 meta：只给文件路径，绝不把 base64 塞给模型。"""
        size = int((info or {}).get("bytes") or 0)
        if not size and path and os.path.isfile(path):
            size = os.path.getsize(path)
        limit = int(getattr(core, "MAX_IMAGE_BYTES", 8 * 1024 * 1024))
        meta = {"kind": "image", "path": path, "mime": core.image_mime(path), "bytes": size}
        if path and size and size <= limit:
            meta["inject_image"] = path
        return meta, size, limit

    def _tool_snapshot(self, args):
        """看一眼现在的屏幕：画面 + 元素清单（label 编号）+ 窗口表。"""
        module = self._desktop()
        path = self._desktop_shot_path(args.get("path"))
        info, error = module.snapshot(
            use_vision=bool(args.get("use_vision", True)),
            use_annotation=bool(args.get("use_annotation", True)),
            use_ui_tree=bool(args.get("use_ui_tree", True)),
            window=str(args.get("window") or "").strip() or "desktop",
            find=str(args.get("find") or "").strip(),
            read_text=bool(args.get("read_text")),
            max_elements=self._desktop_int(args.get("max_elements"), 400, "max_elements"),
            max_depth=self._desktop_int(args.get("max_depth"), 14, "max_depth"),
            region=self._desktop_region(args.get("region")),
            display=args.get("display"),
            save_path=path,
            scale=self._desktop_float(args.get("scale"), 1.0, "scale"))
        if error:
            raise ToolError(error)
        path = str(info.get("path") or "")
        self._desktop_mark_shot()
        elements = info.get("elements") or []
        if path:
            text = "画面：%s（宽 %s x 高 %s）\n" % (path, info.get("width"), info.get("height"))
        else:
            text = "这次没截图（use_vision=false），只有元素清单。\n"
        text += str(info.get("text") or "")
        if elements:
            text += ("\n要动手就用 click / type / scroll / move 的 label=N（上面方括号里的编号）；"
                     "界面变了（翻页、切窗口、弹对话框）要重新 snapshot，编号会重排。")
        else:
            text += ("\n这次一个可操作元素都没有：可能是 Chromium / Qt / 自绘界面，或者窗口没在前台。"
                     "改用 screenshot 看画面，再按图上的位置 click loc=[x, y]；"
                     "也可以加 window= 指定窗口重新 snapshot。")
        meta = {"kind": "snapshot"}
        size = limit = 0
        if path:
            meta, size, limit = self._desktop_image_meta(path, info)
            if size > limit:
                text += self._desktop_too_big(size, limit)
                meta.pop("inject_image", None)
        rows = []
        for element in elements[:30]:
            rows.append({"label": element.get("label"), "kind": element.get("kind"),
                         "name": str(element.get("name") or "")[:60],
                         "action": element.get("action")})
        meta["elements"] = rows
        meta["count"] = len(elements)
        meta["window"] = info.get("window")
        if info.get("note"):
            text += self._desktop_note(info)
        return text, meta

    def _tool_screenshot(self, args):
        """给屏幕拍张照：只给画面，不给元素编号（要元素用 snapshot）。"""
        module = self._desktop()
        path = self._desktop_shot_path(args.get("path"))
        display = args.get("display")
        region = self._desktop_region(args.get("region"))
        window = str(args.get("window") or "").strip()
        cursor = bool(args.get("cursor"))
        scale = self._desktop_float(args.get("scale"), 1.0, "scale")
        info, error = module.screenshot(save_path=path, display=display, region=region,
                                        window=window, scale=scale, cursor=cursor)
        if error:
            raise ToolError(error)
        path = str(info.get("path") or path)
        limit = int(getattr(core, "MAX_IMAGE_BYTES", 8 * 1024 * 1024))
        degraded = ""
        if int(info.get("bytes") or 0) > limit:
            # 这台机器只有纯标准库的 PNG 编码器（写 .jpg 也还是 PNG 内容），
            # 要变小只能缩小重截：依次 0.6 / 0.4 / 0.25，成功了把原来那张大图删掉。
            for fallback in (0.6, 0.4, 0.25):
                retry_path = "%s-小%s.png" % (os.path.splitext(path)[0], int(fallback * 100))
                if window:
                    retry, retry_error = module.capture_window(retry_path, window, scale=fallback)
                else:
                    retry, retry_error = module.capture(retry_path, display=display, region=region,
                                                        scale=fallback, cursor=cursor)
                if retry_error or not retry:
                    continue
                if int(retry.get("bytes") or 0) > limit:
                    try:
                        os.remove(str(retry.get("path") or retry_path))
                    except OSError:
                        pass
                    continue
                try:
                    os.remove(path)
                except OSError:
                    pass
                info = retry
                path = str(retry.get("path") or retry_path)
                limit_mb = limit / 1048576.0
                limit_text = ("%d" % round(limit_mb) if abs(limit_mb - round(limit_mb)) < 0.05
                              else "%.1f" % limit_mb)
                degraded = ("（原图超过 %s MB，已按 scale=%s 重截。）"
                            % (limit_text, fallback))
                break
        self._desktop_mark_shot()
        text = "已截屏：%s（宽 %s x 高 %s，%.0f KB）" % (path, info.get("width"), info.get("height"),
                                                        int(info.get("bytes") or 0) / 1024.0)
        if info.get("window"):
            text += "，窗口「%s」" % info.get("window")
        text += "。"
        if degraded:
            text += degraded
        origin = (int(info.get("origin_x") or 0), int(info.get("origin_y") or 0))
        if origin != (0, 0):
            text += "\n（这张图的原点是 %s：图上的位置加上原点才是屏幕坐标。）" % (origin,)
        text += ("\n图上的坐标可以直接给 click 的 loc=[x, y]（屏幕物理像素）；"
                 "窗口在两次调用之间被移动过的话这个坐标会被拒绝，重新截一张再算就行。"
                 "要看元素编号（label）请用 snapshot。")
        meta, size, unused_limit = self._desktop_image_meta(path, info)
        if size > unused_limit:
            text += self._desktop_too_big(size, unused_limit)
            meta.pop("inject_image", None)
        return text, meta

    def _tool_display_inventory(self, args):
        """这台机器有几块屏、各自多大、桌面控制能不能用。"""
        module = self._desktop()
        info, error = module.display_inventory()
        if error:
            raise ToolError(error)
        return str(info.get("text") or ""), {"kind": "display",
                                             "count": info.get("count"),
                                             "primary": info.get("primary"),
                                             "input_blocked": info.get("input_blocked")}

    def _tool_wait(self, args):
        """等一小会儿（最多 30 秒，别让模型睡太久）。"""
        module = self._desktop()
        seconds = self._desktop_float(args.get("seconds"), 1.0, "seconds")
        seconds = min(30.0, max(0.0, seconds))
        info, error = module.wait(seconds)
        if error:
            raise ToolError(error)
        seconds = float(info.get("seconds") or seconds)
        return "已等待 %.1f 秒。" % seconds, {"kind": "wait", "seconds": seconds}

    def _tool_wait_for(self, args):
        """等某个界面变化出现：窗口来了/走了、文字出现了、画面稳定了。"""
        module = self._desktop()
        condition = str(args.get("condition") or "").strip().lower()
        if condition not in ("window", "gone", "text", "stable"):
            raise ToolError('condition 只能是 "window" / "gone" / "text" / "stable"。')
        find = str(args.get("text") or "").strip()
        if condition in ("window", "gone", "text") and not find:
            raise ToolError("condition=%s 时要给出 text（要找的标题关键字或要出现的文字）。" % condition)
        info, error = module.wait_for(condition, text=find or None,
                                      window_name=str(args.get("window") or "").strip(),
                                      timeout=self._desktop_float(args.get("timeout"), 10.0, "timeout"),
                                      interval=self._desktop_float(args.get("interval"), 0.25, "interval"))
        if error:
            raise ToolError(error)
        info = info or {}
        seconds = float(info.get("seconds") or 0)
        what = {"window": "窗口出现", "gone": "窗口消失",
                "text": "文字出现", "stable": "画面稳定"}[condition]
        text = "等到了：%s（用了 %.1f 秒）。" % (info.get("matched") or what, seconds)
        text += "\n接着用 snapshot 看它现在长什么样、有哪些按钮，再动手。"
        return text, {"kind": "wait_for", "condition": condition,
                      "matched": info.get("matched"), "seconds": seconds}

    def _tool_clipboard(self, args):
        """读或写剪贴板里的文字。"""
        module = self._desktop()
        action = str(args.get("action") or "").strip().lower()
        if action in ("", "get", "read"):
            info, error = module.clipboard(mode="get")
            if error:
                raise ToolError(error)
            text = str((info or {}).get("text") or "")
            if not text.strip():
                return ("剪贴板里现在没有文字（可能是空的，或者放的是图片/文件）。",
                        {"kind": "clipboard", "chars": 0})
            return ("剪贴板里的文字（%d 字）：\n%s" % (len(text), text),
                    {"kind": "clipboard", "chars": len(text), "text": text[:2000]})
        if action in ("set", "write", "copy"):
            data = str(args.get("text") or "")
            if not data:
                raise ToolError("action=set 时要给出 text（要放进剪贴板的文字）。")
            info, error = module.clipboard(mode="set", text=data)
            if error:
                raise ToolError(error)
            return ("已把 %d 个字放进剪贴板：老师接着自己按 Ctrl+V 就能粘贴"
                    "（也可以用 shortcut 的 ctrl+v 粘到某个窗口里）。" % len(data),
                    {"kind": "clipboard", "chars": len(data)})
        raise ToolError('action 只能是 "get" 或 "set"（旧的 read/write 也认）。')

    def _tool_process(self, args):
        """看进程列表，或者结束一个进程。"""
        module = self._desktop()
        action = str(args.get("action") or "list").strip().lower()
        if action in ("", "list", "info"):
            sort_by = str(args.get("sort_by") or "memory").strip().lower()
            if sort_by not in ("memory", "name", "pid"):
                raise ToolError('sort_by 只能是 "memory" / "name" / "pid"。')
            info, error = module.process(mode="list", name=str(args.get("name") or "").strip(),
                                         sort_by=sort_by,
                                         limit=self._desktop_int(args.get("limit"), 20, "limit"))
            if error:
                raise ToolError(error)
            info = info or {}
            rows = info.get("processes") or []
            order = {"memory": "内存", "name": "名字", "pid": "pid"}[sort_by]
            lines = ["按%s排序前 %d 个（一共 %s 个进程）：" % (order, len(rows), info.get("count"))]
            for item in rows:
                lines.append("  pid %-6s %-30s 内存 %.1f MB"
                             % (item.get("pid"), str(item.get("name") or "")[:30],
                                float(item.get("memory") or 0) / 1048576.0))
            lines.append('要结束某个进程：process(action="kill", pid=上面那个 pid)。'
                         "动手前先确认它不是老师正在用的东西（浏览器、QQ、WPS/PPT、explorer）。")
            return "\n".join(lines), {"kind": "process", "mode": "list", "count": info.get("count"),
                                      "processes": rows[:40]}
        if action in ("kill", "stop"):
            name = str(args.get("name") or "").strip()
            pid = args.get("pid")
            if not name and pid in (None, ""):
                raise ToolError("结束进程要给 pid，或者给 name（进程名，不用带 .exe）。")
            info, error = module.process(mode="kill", name=name or None, pid=pid,
                                         force=bool(args.get("force")))
            if error:
                raise ToolError(error)
            killed = (info or {}).get("killed") or []
            good = [item for item in killed if item.get("ok")]
            bad = [item for item in killed if not item.get("ok")]
            lines = []
            if good:
                lines.append("已结束进程 %s。" % "、".join(
                    "%s（pid %s）" % (name or "目标进程", item.get("pid")) for item in good))
            for item in bad:
                lines.append("pid %s 没能结束：%s。可以加 force=true 再试一次。"
                             % (item.get("pid"), item.get("error")))
            lines.append('想确认真的没了：process(action="list", name=…) 再看一眼。')
            return "\n".join(lines), {"kind": "process", "mode": "kill", "killed": killed}
        raise ToolError('action 只能是 "list" 或 "kill"。')

    def _tool_registry(self, args):
        """读写注册表。"""
        module = self._desktop()
        action = str(args.get("action") or "get").strip().lower()
        path = str(args.get("path") or "").strip()
        name = str(args.get("name") or "").strip()
        if not path:
            raise ToolError("path 要给出注册表路径，例如 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run。")
        if action in ("", "get", "read"):
            info, error = module.registry(mode="get", path=path, name=name or None)
            if error:
                raise ToolError(error)
            info = info or {}
            return ("%s 下的值「%s」现在是：%s" % (info.get("path"), info.get("name"), info.get("value")),
                    {"kind": "registry", "mode": "get", "path": info.get("path"),
                     "name": info.get("name"), "value": info.get("value")})
        if action == "list":
            info, error = module.registry(mode="list", path=path)
            if error:
                raise ToolError(error)
            info = info or {}
            values = info.get("values") or {}
            lines = ["%s 下共 %s 个值：" % (info.get("path") or path, info.get("count", len(values)))]
            for key, value in list(values.items())[:60]:
                lines.append("  %s = %s" % (key, value))
            return "\n".join(lines), {"kind": "registry", "mode": "list",
                                      "path": info.get("path") or path, "values": values}
        if action in ("set", "write"):
            value = args.get("value")
            if value is None or str(value) == "":
                raise ToolError("action=set 时要给出 value。")
            info, error = module.registry(mode="set", path=path, name=name or None,
                                          value=str(value),
                                          value_type=str(args.get("type") or "string").strip().lower())
            if error:
                raise ToolError(error)
            info = info or {}
            text = ("已写入注册表：%s 的值「%s」= %s（类型 %s）。"
                    % (info.get("path") or path, info.get("name") or "(默认)",
                       info.get("value"), str(args.get("type") or "string").strip().lower()))
            text += "\n改注册表会影响系统行为，建议再 get 一次复查，并跟老师说清楚改了什么、怎么改回去。"
            return text, {"kind": "registry", "mode": "set", "path": info.get("path") or path,
                          "name": info.get("name")}
        if action in ("delete", "del", "remove"):
            info, error = module.registry(mode="delete", path=path, name=name or None)
            if error:
                raise ToolError(error)
            info = info or {}
            return ("已删除注册表值：%s 的「%s」。删之前的值如果是老师要留的，请自己再 get 一次确认。"
                    % (info.get("path") or path, info.get("name") or "(默认)"),
                    {"kind": "registry", "mode": "delete", "path": info.get("path") or path,
                     "name": info.get("name")})
        raise ToolError('action 只能是 "get" / "set" / "delete" / "list"。')

    def _tool_click(self, args):
        """在屏幕上点一下（label 编号或 loc 坐标）。"""
        module = self._desktop()
        loc = args.get("loc")
        label = args.get("label")
        if loc in (None, "") and label in (None, ""):
            raise ToolError("loc 和 label 至少给一个：label=snapshot 里的编号，loc=[x, y] 屏幕坐标。")
        stale = self._desktop_stale([loc] if loc not in (None, "") else [])
        if stale:
            raise ToolError(stale)
        button = str(args.get("button") or "left").strip().lower()
        if button not in ("left", "right", "middle"):
            raise ToolError('button 只能是 "left" / "right" / "middle"。')
        clicks = self._desktop_int(args.get("clicks"), 1, "clicks")
        info, error = module.click(loc=loc, label=label, button=button, clicks=clicks)
        if error:
            raise ToolError(error)
        info = info or {}
        self._desktop_mark_shot()
        target = ("，目标是「%s」" % info.get("target")) if info.get("target") else ""
        if str(info.get("action")) == "hover" or clicks == 0:
            text = "已把鼠标移到 (%s, %s)（clicks=0，只悬停、没有按下去）。" % (info.get("x"), info.get("y"))
        elif clicks == 2:
            text = "已在 (%s, %s) 双击%s%s。" % (info.get("x"), info.get("y"),
                                               {"left": "左键", "right": "右键", "middle": "中键"}.get(
                                                   str(info.get("button") or button), "左键"), target)
        else:
            text = "已点 %s 下 (%s, %s)%s。" % (info.get("clicks", clicks), info.get("x"), info.get("y"), target)
        text += self._desktop_note(info)
        text += "\n接着用 snapshot 看一眼结果（点下去不等于事情办成了），别拿旧编号或旧坐标接着点。"
        return text, {"kind": "desktop", "action": info.get("action"), "x": info.get("x"),
                      "y": info.get("y"), "button": info.get("button"),
                      "clicks": info.get("clicks"), "target": info.get("target")}

    def _tool_type(self, args):
        """往窗口里输入文字（模拟键盘）；中文按 Unicode 逐个字符注入，不占剪贴板。"""
        module = self._desktop()
        data = "" if args.get("text") is None else str(args.get("text"))
        clear = bool(args.get("clear"))
        if not data and not clear:
            raise ToolError("text 是空的：要输入的文字不能为空（只想清空原来的内容就配 clear=true）。")
        loc = args.get("loc")
        stale = self._desktop_stale([loc] if loc not in (None, "") else [])
        if stale:
            raise ToolError(stale)
        caret = str(args.get("caret_position") or "idle").strip().lower()
        if caret not in ("start", "idle", "end"):
            raise ToolError('caret_position 只能是 "start" / "idle" / "end"。')
        info, error = module.type_text(data, loc=loc, label=args.get("label"), clear=clear,
                                       caret_position=caret,
                                       press_enter=bool(args.get("press_enter")))
        if error:
            raise ToolError(error)
        info = info or {}
        self._desktop_mark_shot()
        text = "已输入 %s 个字%s。" % (info.get("chars"), "，并按了回车" if info.get("pressed_enter") else "")
        if info.get("cleared"):
            text += "（输入前先清空了框里原来的内容。）"
        text += "中文是逐个字符按 Unicode 注入的，不切输入法、也没动老师原来的剪贴板。"
        text += self._desktop_note(info)
        text += "\n输入完再 snapshot 一次确认真的进去了：有的输入框要失去焦点（比如点一下别处）才生效。"
        return text, {"kind": "desktop", "action": "type", "chars": info.get("chars"),
                      "target": info.get("target"), "x": info.get("x"), "y": info.get("y"),
                      "cleared": info.get("cleared"), "pressed_enter": info.get("pressed_enter")}

    def _tool_scroll(self, args):
        """滚动鼠标滚轮：翻页用这个，别去拖滚动条。"""
        module = self._desktop()
        kind = str(args.get("type") or "vertical").strip().lower()
        if kind not in ("vertical", "horizontal"):
            raise ToolError('type 只能是 "vertical"（上下）或 "horizontal"（左右）。')
        direction = str(args.get("direction") or "down").strip().lower()
        if direction not in ("down", "up", "left", "right"):
            raise ToolError('direction 只能是 "down" / "up" / "left" / "right"。')
        info, error = module.scroll(loc=args.get("loc"), label=args.get("label"), type=kind,
                                    direction=direction,
                                    wheel_times=self._desktop_int(args.get("wheel_times"), 1, "wheel_times"))
        if error:
            raise ToolError(error)
        info = info or {}
        self._desktop_mark_shot()
        text = "已向%s滚动 %s 格（%s，一格大约三行）。" % (
            {"down": "下", "up": "上", "left": "左", "right": "右"}[direction],
            info.get("times"), "上下滚" if kind == "vertical" else "左右滚")
        text += self._desktop_note(info)
        text += "\n滚动之后画面变了：要接着操作就重新 snapshot 或 screenshot，别拿旧坐标点。"
        return text, {"kind": "desktop", "action": "scroll", "type": info.get("type"),
                      "direction": info.get("direction"), "times": info.get("times")}

    def _tool_move(self, args):
        """移动鼠标，或者按住拖动。"""
        module = self._desktop()
        loc = args.get("loc")
        from_loc = args.get("from_loc")
        label = args.get("label")
        drag = bool(args.get("drag"))
        if loc in (None, "") and from_loc in (None, "") and label in (None, ""):
            raise ToolError("要给出 loc=[x, y]（到哪儿）或 label=编号；拖动再给 from_loc=[起点x, 起点y]。")
        points = []
        if loc not in (None, ""):
            points.append(loc)
        if drag and from_loc not in (None, ""):
            points.append(from_loc)
        stale = self._desktop_stale(points)
        if stale:
            raise ToolError(stale)
        info, error = module.move(loc=loc, label=label, drag=drag, from_loc=from_loc,
                                  duration=args.get("duration"))
        if error:
            raise ToolError(error)
        info = info or {}
        self._desktop_mark_shot()
        if str(info.get("action")) == "drag":
            start = info.get("from") or [None, None]
            text = "已从 (%s, %s) 拖到 (%s, %s)。" % (start[0], start[1], info.get("x"), info.get("y"))
        else:
            text = "已把鼠标移到 (%s, %s)。" % (info.get("x"), info.get("y"))
        text += self._desktop_note(info)
        text += "\n拖完用 snapshot 确认结果（拖放经常要目标位置先露出来）。"
        return text, {"kind": "desktop", "action": info.get("action"), "x": info.get("x"),
                      "y": info.get("y"), "from": info.get("from")}

    def _tool_shortcut(self, args):
        """按快捷键（多媒体键也走它）。"""
        module = self._desktop()
        combo = str(args.get("shortcut") or "").strip()
        if not combo:
            raise ToolError("要给出 shortcut，例如 ctrl+s、alt+tab、enter、f5、volume_up。")
        info, error = module.shortcut(combo)
        if error:
            raise ToolError(error)
        info = info or {}
        self._desktop_mark_shot()
        text = "已发送快捷键 %s。" % (info.get("keys") or combo)
        if combo.lower() in ("play_pause", "next", "prev", "stop",
                             "volume_up", "volume_down", "mute"):
            text += "（多媒体键作用在当前前台程序上，发之前先确认焦点在哪个播放器。）"
        text += "快捷键发给的是当前前台窗口：要是这个键会改界面（比如 ctrl+w 关窗口、alt+f4 退出），用 snapshot 复查一下。"
        return text, {"kind": "desktop", "action": "shortcut", "keys": info.get("keys")}

    def _tool_app(self, args):
        """管窗口和程序：启动 / 切换 / 关闭 / 改大小位置。"""
        module = self._desktop()
        mode = str(args.get("mode") or "").strip().lower()
        if not mode:
            raise ToolError("要给出 mode：launch / launch_executable / switch / info / resize / move / "
                            "close / minimize / maximize / restore / hide / show。")
        name = str(args.get("name") or "").strip() or str(args.get("window") or "").strip()
        executable = str(args.get("executable") or "").strip()
        loc = args.get("loc")
        size = args.get("size")
        if mode == "info":
            info, error = module.window_action(name, "info")
            if error:
                raise ToolError(error)
            info = info or {}
            rect = list(info.get("rect") or [0, 0, 0, 0])
            while len(rect) < 4:
                rect.append(0)
            text = ("窗口「%s」：handle=%s，pid=%s，位置 (%s, %s)，大小 %s x %s%s。"
                    % (info.get("window"), info.get("handle"), info.get("pid"), rect[0], rect[1],
                       int(rect[2]) - int(rect[0]), int(rect[3]) - int(rect[1]),
                       "，现在在前台" if info.get("foreground") else ""))
            return text, {"kind": "window", "action": "info", "window": info.get("window"),
                          "handle": info.get("handle"), "pid": info.get("pid"), "rect": rect}
        launching = mode in ("launch", "open", "start", "launch_executable", "exec", "run")
        if mode in ("launch", "open", "start") and not name:
            raise ToolError('launch 要给出 name（开始菜单里的程序名，例如 "记事本"、"画图"）。')
        if mode in ("launch_executable", "exec", "run") and not executable:
            raise ToolError("launch_executable 要给出 executable（exe 的完整路径）。")
        if not launching and not name:
            raise ToolError("%s 要给出 name（窗口标题里的一段字、pid:1234 或 handle:12345），或者用 window=。"
                            % mode)
        # 注意：下面 args= 是"启动参数"（给新程序的），不是本工具的入参
        info, error = module.app(mode=mode, name=name or None, window_loc=loc, window_size=size,
                                 executable=executable or None,
                                 args=str(args.get("args") or "").strip() or None,
                                 cwd=str(args.get("cwd") or "").strip() or None)
        if error:
            raise ToolError(error)
        info = info or {}
        title = info.get("window") or name or ""
        if launching:
            text = "已启动「%s」" % (title or info.get("path") or executable)
            if info.get("pid"):
                text += "（pid %s）" % info.get("pid")
            text += "。"
            if info.get("window"):
                text += "窗口已经出来了。"
            else:
                text += "现在还没看到它的窗口：等一两秒，用 wait_for(condition=\"window\", text=…) 或 snapshot 看一眼。"
        elif mode in ("switch", "focus", "front", "activate"):
            text = "已把窗口「%s」切到前台（handle=%s）。" % (title, info.get("handle"))
            text += "接着可以 snapshot 看它现在长什么样，再动手点。"
        elif mode == "close":
            text = "已请窗口「%s」关闭。" % title
            text += ("它可能会弹「是否保存」的对话框：用 wait_for 等一等，再 snapshot 看有没有弹窗没处理。"
                     "要是它还赖着不走（新版记事本/商店应用常见），可以用 process 的 kill 结束进程。")
        elif mode in ("minimize", "maximize", "restore", "hide", "show"):
            text = "已%s窗口「%s」（handle=%s）。" % (
                {"minimize": "最小化", "maximize": "最大化", "restore": "还原",
                 "hide": "隐藏", "show": "显示"}[mode], title, info.get("handle"))
        else:
            text = "已调整窗口「%s」" % title
            if loc not in (None, ""):
                text += "，位置 %s" % (list(loc),)
            if size not in (None, ""):
                text += "，大小 %s" % (list(size),)
            text += "。"
        if info.get("note"):
            text += self._desktop_note(info)
        return text, {"kind": "window", "mode": mode, "window": info.get("window"),
                      "handle": info.get("handle"), "pid": info.get("pid")}

    def _tool_multi_select(self, args):
        """一次选中多个（按住 Ctrl 连点）。"""
        module = self._desktop()
        labels = args.get("labels")
        locs = args.get("locs")
        if not labels and not locs:
            raise ToolError("要给出 labels（snapshot 里的编号列表）或 locs（[[x, y], …]）。")
        stale = self._desktop_stale(locs)
        if stale:
            raise ToolError(stale)
        info, error = module.multi_select(locs=locs, labels=labels,
                                          press_ctrl=bool(args.get("press_ctrl", True)))
        if error:
            raise ToolError(error)
        info = info or {}
        self._desktop_mark_shot()
        text = "已依次点过 %s 个位置%s。" % (info.get("count"),
                                           "（全程按住 Ctrl，做的是多选）" if info.get("ctrl") else "（没有按 Ctrl）")
        text += "\n选完用 snapshot 看一眼有没有真的选上（有的列表点快了会变成单选）。"
        return text, {"kind": "desktop", "action": "multi_select", "count": info.get("count"),
                      "ctrl": info.get("ctrl"), "items": info.get("items")}

    def _tool_multi_edit(self, args):
        """按顺序给多个格子填内容（批量填表）。"""
        module = self._desktop()
        labels = args.get("labels")
        locs = args.get("locs")
        if not labels and not locs:
            raise ToolError('要给出 labels（[[编号, "文字"], …]）或 locs（[[x, y, "文字"], …]）。')
        stale = self._desktop_stale(locs)
        if stale:
            raise ToolError(stale)
        info, error = module.multi_edit(locs=locs, labels=labels)
        if error:
            raise ToolError(error)
        info = info or {}
        self._desktop_mark_shot()
        text = "已按顺序填了 %s 个位置。" % info.get("count")
        notes = [str(item.get("note")) for item in (info.get("items") or []) if item.get("note")]
        if notes:
            text += "\n（%s）" % "；".join(notes[:3])
        text += ("\n它不会自动清空格子里的旧内容，也不保证每一格都填进去了："
                 "填完务必 snapshot 复查一遍。")
        return text, {"kind": "desktop", "action": "multi_edit", "count": info.get("count"),
                      "items": info.get("items")}

    def _tool_draw_path(self, args):
        """在白板/画图窗口里按坐标画线（讲题圈画、画示意图）。"""
        module = self._desktop()
        strokes = args.get("strokes")
        if not isinstance(strokes, (list, tuple)) or not strokes:
            raise ToolError("strokes 要写成笔画列表，例如 [[[100,200],[300,200]], [[100,200],[100,400]]]。")
        button = str(args.get("button") or "left").strip().lower()
        if button not in ("left", "right"):
            raise ToolError('button 只能是 "left" 或 "right"。')
        info, error = module.draw_path(strokes,
                                       duration=self._desktop_float(args.get("duration"), 0.45, "duration"),
                                       button=button)
        if error:
            raise ToolError(error)
        info = info or {}
        self._desktop_mark_shot()
        text = ("已画完 %s 笔（共 %s 个点，用了 %.1f 秒）。"
                % (info.get("strokes"), info.get("points"), float(info.get("seconds") or 0)))
        if info.get("note"):
            text += self._desktop_note(info)
        text += ("\n画得对不对用 screenshot 看一眼。要画精确的函数图像、几何图形别用它——"
                 "那类图用作图工具画（坐标是算出来的，还带标注）。")
        return text, {"kind": "desktop", "action": "draw_path", "strokes": info.get("strokes"),
                      "points": info.get("points"), "seconds": info.get("seconds")}

    def _stale_click_point(self, x, y):
        """这个点还按上次截图那样吗？窗口动过/换了人就返回一句人话，否则返回 ""。

        只用**屏幕布局**判断，不猜内容：上次截图时记下每块屏幕上每个窗口的句柄与矩形
        （`_shot_layout`），点击前重新列一遍，看"这个坐标上的那个窗口"是不是还在原位。
        这样老师自己把窗口拖走、或者系统弹了个新窗口盖住，都能挡住，而不是点空。
        """
        layout = getattr(self, "_shot_layout", None)
        if not layout or x is None or y is None:
            return ""
        try:
            point = (int(x), int(y))
        except (TypeError, ValueError):
            return ""
        try:
            current = self._desktop_layout()
        except Exception:                                 # noqa: BLE001 - 判不了就照旧放行
            return ""
        was = self._window_at(layout, point)
        now = self._window_at(current, point)
        if was == now:
            return ""
        if not was:
            return ("这个坐标 (%d, %d) 上次截图时不属于任何窗口，现在落到了「%s」上。"
                    "先用 screenshot 重新截一张再点。" % (point[0], point[1], now or "?"))
        if not now:
            return ("上次截图里 (%d, %d) 在「%s」上，现在那儿没有窗口了（窗口被关掉或挪走了）。"
                    "先用 screenshot 重新截一张再点。" % (point[0], point[1], was))
        return ("上次截图里 (%d, %d) 在「%s」上，现在这个位置是「%s」——"
                "截图之后窗口动过或换了窗口。**先用 screenshot 重新截一张，按新截图上的位置点**，"
                "别用旧坐标。" % (point[0], point[1], was, now))

    @staticmethod
    def _window_at(layout, point):
        """这个坐标上最前面那个窗口的标题（没有就返回 ""）。layout 是从前往后排的。"""
        for window in layout:
            left, top, right, bottom = window["rect"]
            if left <= point[0] <= right and top <= point[1] <= bottom:
                return window["title"]
        return ""

    def _desktop_layout(self):
        """当前屏幕布局（最前面的窗口在前）：[{title, handle, rect}]，判"窗口动没动"用。

        注意 `desktop.list_windows()` 返回的是 **(窗口列表, 错误)** 元组，窗口字段是
        x/y/width/height —— 按 dict 列表读会静默什么都判不出来（这里踩过一次）。
        """
        module = self._desktop()
        result = module.list_windows(limit=40)
        if isinstance(result, tuple):
            windows, error = result
            if error:
                return []
        else:
            windows = result or []
        rows = []
        for item in windows or []:
            if not isinstance(item, dict) or item.get("minimized"):
                continue
            try:
                left = int(item.get("x", 0))
                top = int(item.get("y", 0))
                width = int(item.get("width", 0))
                height = int(item.get("height", 0))
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            rows.append({"title": str(item.get("title") or ""),
                         "handle": item.get("handle") or 0,
                         "rect": (left, top, left + width, top + height)})
        return rows

    # -- 下载（后台任务）---------------------------------------------------
    def _tool_download(self, args):
        """下载图片/文件/视频：大批量或大文件走后台任务，别把对话卡住。"""
        urls = []
        if isinstance(args.get("urls"), list):
            urls.extend(args["urls"])
        if args.get("url"):
            urls.append(args["url"])
        urls = [str(item).strip() for item in urls if str(item).strip()][:5]
        if not urls:
            raise ToolError("请给出 url 或 urls（最多 5 个）")
        self.check_cancel("下载")
        try:
            max_mb = int(args.get("max_mb") or 200)
        except (TypeError, ValueError):
            max_mb = 200
        max_bytes = max(1, min(2048, max_mb)) * 1024 * 1024
        given = str(args.get("path") or "").strip()
        referer = str(args.get("referer") or "").strip()
        background = bool(args.get("background"))

        folder = ""
        single_path = ""
        if given:
            resolved = self.resolve(given, for_write=True)
            # "C:\课件\下载" 这种没有扩展名、也还不存在的路径，按目录处理：
            # 否则会把文件存成一个叫"下载"的文件，老师打开一看就懵。
            looks_like_dir = (os.path.isdir(resolved) or given.endswith(("/", "\\"))
                              or not os.path.splitext(os.path.basename(resolved))[1])
            if looks_like_dir:
                folder = resolved
            elif len(urls) == 1:
                single_path = resolved
                folder = os.path.dirname(resolved)
            else:
                folder = resolved
        if not folder:
            folder = self.resolve(os.path.join(self.root, "下载"), for_write=True)
        if not os.path.isdir(folder):
            try:
                os.makedirs(folder)
            except OSError as exc:
                raise ToolError("建下载目录失败：%s" % exc)

        if background:
            jobs = []
            for url in urls:
                job = self._start_download_job(url, folder, max_bytes, referer)
                jobs.append(job)
            lines = ["已经把 %d 个下载放到后台（不会卡住对话）：" % len(jobs)]
            for job in jobs:
                lines.append("- %s：%s" % (job["id"], job["url"]))
            lines.append("过一会儿用 download_status 看进度；下完我会主动告诉你。")
            return "\n".join(lines), {"kind": "download", "background": True,
                                      "jobs": [job["id"] for job in jobs]}

        results = []
        images = []
        paths = []
        for url in urls:
            self.check_cancel("下载")
            target = single_path or os.path.join(
                folder, download_module.filename_from(url, "", ""))
            target = self._unique_path(target)
            info, error = download_module.download(
                url, target, max_bytes=max_bytes, cancel=self.cancel, referer=referer)
            if error:
                results.append("× %s：%s" % (url, error))
                continue
            results.append(self._download_line(info))
            final_path = self._apply_server_filename(info, folder, explicit=single_path)
            paths.append({"path": final_path, "bytes": info["bytes"],
                          "kind": info["kind"], "truncated": info["truncated"]})
            if info["kind"] == "image" and info["bytes"] <= int(
                    getattr(core, "MAX_IMAGE_BYTES", 8 * 1024 * 1024)):
                images.append(final_path)
        text = "\n".join(results)
        if images:
            text += ("\n\n图片会在下一轮提供给你（%d 张），收到后请说明看到的内容。" % len(images))
        meta = {"kind": "download", "count": len(paths), "paths": paths, "images": images}
        if images:
            # 只注入第一张，避免一次塞太多；其余用 view_image 看
            meta["inject_image"] = images[0]
        if not images and all(line.startswith("×") for line in results):
            raise ToolError(text)
        return text, meta

    def _apply_server_filename(self, info, folder, explicit=""):
        """服务端在 Content-Disposition 里给了文件名时，按它改名。

        为什么不在下载前就定名字：那时候还没连上服务器，只能从 URL 猜（URL 常常是
        `download?id=123` 这种）。下完拿到真名再改一次，跟浏览器的行为一致。
        老师明确指定了文件名（explicit）时不改，尊重他。
        """
        if explicit:
            return info["path"]
        server_name = str(info.get("name") or "").strip()
        current = os.path.basename(info["path"])
        if not server_name or server_name == current:
            return info["path"]
        target = os.path.join(folder, server_name)
        if not os.path.splitext(target)[1]:
            target += os.path.splitext(current)[1]
        target = self._unique_path(target)
        try:
            os.replace(long_path(info["path"]), long_path(target))
        except OSError:
            return info["path"]          # 改不了名就用原来的名字，不影响文件本身
        info["path"] = target
        return target

    def _download_line(self, info):
        kind_name = {"image": "图片", "video": "视频", "audio": "音频",
                     "doc": "文档", "other": "文件"}.get(info["kind"], "文件")
        line = "√ %s（%s，%s，用时 %s 秒）" % (
            info["path"], kind_name, download_module.human_size(info["bytes"]), info["seconds"])
        if info.get("truncated"):
            line += "\n   注意：" + info["note"]
        if info.get("html_warning"):
            line += "\n   " + info["html_warning"]
        return line

    def _unique_path(self, path):
        """同名文件不覆盖：加 -2、-3 后缀（老师的资料比我们的方便重要）。"""
        if not os.path.exists(long_path(path)):
            return path
        stem, ext = os.path.splitext(path)
        for index in range(2, 200):
            candidate = "%s-%d%s" % (stem, index, ext)
            if not os.path.exists(long_path(candidate)):
                return candidate
        return path

    def _start_download_job(self, url, folder, max_bytes, referer):
        """把一次下载丢到后台线程里跑（对照 DSH 的 jobs：有 id、有状态、能取消）。

        注意：session/emit 是**按线程**存的（子代理会并行跑），所以必须在这里
        （主线程里）先把它们抓下来，后台线程才拿得到——否则下完了没人通知界面。
        """
        emit = self.emit
        session = self.session
        with self._jobs_lock:
            self._job_seq += 1
            job_id = "dl-%d" % self._job_seq
            cancel = threading.Event()
            job = {"id": job_id, "kind": "download", "url": url, "status": "running",
                   "cancel": cancel, "started": time.time(), "path": "", "bytes": 0,
                   "error": "", "note": "", "finished": 0.0,
                   "owner": (session or {}).get("id") or ""}
            self._jobs[job_id] = job

        def worker():
            target = self._unique_path(os.path.join(
                folder, download_module.filename_from(url, "", "")))
            info, error = download_module.download(
                url, target, max_bytes=max_bytes, cancel=cancel, referer=referer)
            with self._jobs_lock:
                job["finished"] = time.time()
                if error:
                    job["status"] = "cancelled" if cancel.is_set() else "failed"
                    job["error"] = error
                else:
                    job["status"] = "completed"
                    job["path"] = info["path"]
                    job["bytes"] = info["bytes"]
                    job["kind"] = info["kind"]
                    job["note"] = info.get("note") or ""
            self._notify_job_done(job, emit)
            if job["status"] == "completed" and session is not None:
                # 记进会话，这样"下完了"这件事在历史里也留得下痕迹
                try:
                    session.setdefault("messages", []).append({
                        "role": "user", "ts": _now(), "autoContinue": True,
                        "content": ("（系统：后台下载已完成 —— %s（%s）已保存到 %s。"
                                    "需要的话用 inspect_media 看它能不能在 Win7 上播，"
                                    "或者用 view_image 看图。）"
                                    % (job["url"][:120], download_module.human_size(job["bytes"]),
                                       job["path"])),
                    })
                except Exception:
                    pass

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        return job

    def _notify_job_done(self, job, emit=None):
        """下完主动说一声（不靠模型轮询，对应 DSH 的 completionDelivery）。"""
        if not callable(emit):
            return
        try:
            if job["status"] == "completed":
                emit({"t": "notice", "level": "ok",
                      "message": "后台下载完成：%s（%s）→ %s"
                                 % (job["url"][:80], download_module.human_size(job["bytes"]),
                                    job["path"])})
            else:
                emit({"t": "notice", "level": "warn",
                      "message": "后台下载结束（%s）：%s —— %s"
                                 % (job["status"], job["url"][:80], job["error"][:120])})
        except Exception:
            pass

    def _tool_download_status(self, args):
        action = str(args.get("action") or "list").lower()
        job_id = str(args.get("job_id") or "").strip()
        if action == "cancel":
            if not job_id:
                raise ToolError("取消下载要给出 job_id（用 download_status 不带参数可以列出全部）")
            with self._jobs_lock:
                job = self._jobs.get(job_id)
                if not job:
                    raise ToolError("没有这个下载任务：%s" % job_id)
                job["cancel"].set()
                if job["status"] == "running":
                    job["status"] = "cancelling"
            return "已请求取消 %s（正在下的那一段会立刻断开，半截文件会删掉）。" % job_id, {
                "kind": "job", "id": job_id, "action": "cancel"}
        with self._jobs_lock:
            jobs = [dict(item) for item in self._jobs.values()]
        if not jobs:
            return "现在没有后台下载任务。", {"kind": "job", "count": 0}
        lines = ["后台下载任务（共 %d 个）：" % len(jobs)]
        for job in sorted(jobs, key=lambda item: item["started"]):
            if job["status"] == "running":
                detail = "正在下…（已用时 %.0f 秒）" % (time.time() - job["started"])
            elif job["status"] == "cancelling":
                detail = "正在取消…"
            elif job["status"] == "completed":
                detail = "已完成：%s（%s，用时 %.0f 秒）" % (
                    job["path"], download_module.human_size(job["bytes"]),
                    job["finished"] - job["started"])
            elif job["status"] == "cancelled":
                detail = "已取消"
            else:
                detail = "失败：%s" % job["error"]
            lines.append("- %s %s %s" % (job["id"], job["url"][:70], detail))
        return "\n".join(lines), {"kind": "job", "count": len(jobs),
                                  "jobs": [{k: job[k] for k in ("id", "status", "url", "path")}
                                           for job in jobs[:10]]}

    # -- 消息通知 ---------------------------------------------------------
    def _tool_send_email(self, args):
        subject = str(args.get("subject") or "").strip()
        body = str(args.get("body") or "")
        if not subject and not body:
            raise ToolError("邮件主题和正文都是空的")
        attach = args.get("attach") or []
        if isinstance(attach, str):
            attach = [item.strip() for item in attach.split(";") if item.strip()]
        paths = [self.resolve(item, must_exist=True) for item in attach]
        result, error = notify_module.send_email(
            self.settings, subject, body, to=str(args.get("to") or "").strip(), attach=paths)
        if error:
            raise ToolError(error)
        return result, {"subject": subject, "to": str(args.get("to") or "").strip(),
                        "attachments": len(paths)}

    def _tool_notify_webhook(self, args):
        text = str(args.get("text") or "").strip()
        if not text:
            raise ToolError("要推送的内容是空的")
        result, error = notify_module.send_webhook(
            self.settings, text, url=str(args.get("url") or "").strip(),
            kind=str(args.get("kind") or "").strip())
        if error:
            raise ToolError(error)
        return result, {"chars": len(text)}

    def _tool_send_qq_message(self, args):
        module = self._desktop()
        text = str(args.get("text") or "")
        if not text.strip():
            raise ToolError("要发的消息是空的")
        result, error = module.send_qq_message(
            text, contact=str(args.get("contact") or "").strip(),
            send_shortcut=str(args.get("send_shortcut") or "enter").strip().lower())
        if error:
            raise ToolError(error)
        lines = ["QQ 消息已发出（模拟操作）："]
        lines.extend("- %s" % item for item in result.get("steps", []))
        lines.append("请根据上面的截图确认消息有没有真的发出去；如果 QQ 设了「Ctrl+Enter 发送」，"
                     "回车不会发送，需要改用 send_shortcut=ctrl+enter 重发。")
        if result.get("searched"):
            lines.append("（搜索联系人只是尽力而为：如果搜索结果点错了人，请让老师手动打开正确的聊天窗口后再发。）")
        meta = {"kind": "qq_message", "window": result.get("window"),
                "chars": result.get("chars"), "contact": result.get("contact")}
        shot = result.get("screenshot")
        if shot and os.path.isfile(shot):
            meta["inject_image"] = shot
            meta["kind"] = "image"
            meta["path"] = shot
            meta["mime"] = core.image_mime(shot)
            meta["bytes"] = os.path.getsize(shot)
        return "\n".join(lines), meta

    # -- 自我修改（自检 + 重启）--------------------------------------------
    def _tool_self_check(self, args):
        """跑自带自检：agent 改完自己的代码/配置后用它确认没改坏。

        实现在 tools 层（而不是让 agent 自己拼命令），这样输出、超时和路径都由程序掌握。
        """
        runner = os.path.join(self.settings.base_dir, "webui", "test_webui.py")
        if not os.path.isfile(runner):
            raise ToolError("找不到自检脚本：%s" % runner)
        python = os.path.join(self.settings.base_dir, "python", "python.exe")
        if not os.path.isfile(python):
            python = sys.executable or "python"
        target = str(args.get("target") or "").strip()
        command = [python, "-u", runner] + ([target] if target else [])
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["W7A_NO_INPUT"] = "1"          # 自检期间禁止模拟键鼠（不许动使用者的鼠标）
        try:
            process = subprocess.Popen(command, cwd=self.settings.base_dir,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, env=env,
                                       creationflags=(0x08000000 if os.name == "nt" else 0))
            out, _err = process.communicate(timeout=SELF_CHECK_TIMEOUT)
            code = process.returncode
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            raise ToolError("自检超过 %d 秒还没跑完，已中止。可以只跑一部分：target=TestXxx"
                            % SELF_CHECK_TIMEOUT)
        except OSError as exc:
            raise ToolError("启动自检失败：%s" % exc)
        text = decode_text(out or b"")
        tail = "\n".join(text.strip().splitlines()[-25:])
        head = "自检通过（退出码 0）" if code == 0 else "自检**没有通过**（退出码 %s）" % code
        lines = [head + ("（只跑了 %s）" % target if target else "（全量）"), "", tail]
        if code != 0:
            lines.append("")
            lines.append("请按上面的失败信息修好再重启服务；不要带着失败的自检重启。")
        return "\n".join(lines), {"kind": "self_check", "exit": code, "target": target}

    def _tool_restart_service(self, args):
        """重启 WebUI：先起一个独立脚本，等本进程退出后再拉起启动器。"""
        reason = str(args.get("reason") or "").strip()
        if not self.settings.webui.get("allow_self_restart", True):
            raise ToolError("设置里关闭了「允许 agent 重启服务」，重启被拒绝。")
        launcher = os.path.join(self.settings.base_dir, "start-webui.cmd")
        if not os.path.isfile(launcher):
            raise ToolError("找不到启动器：%s" % launcher)
        helper = os.path.join(self.settings.base_dir, "logs", "restart-helper.cmd")
        try:
            if not os.path.isdir(os.path.dirname(helper)):
                os.makedirs(os.path.dirname(helper), exist_ok=True)
            # 这个 .cmd 只写 ASCII：cmd.exe 按 OEM 代码页解析批处理，
            # 往里写中文（尤其 UTF-8 字节）在部分机器上会直接报错或乱码。
            with open(long_path(helper), "w", encoding="ascii", newline="\r\n") as handle:
                handle.write("@echo off\r\n")
                handle.write("rem generated by the agent's restart_service tool\r\n")
                handle.write("ping -n 4 127.0.0.1 >nul\r\n")
                handle.write('start "" "%s"\r\n' % launcher)
        except OSError as exc:
            raise ToolError("写重启脚本失败：%s" % exc)
        if reason:
            try:
                with open(long_path(os.path.join(self.settings.base_dir, "logs", "self-modify.log")),
                          "a", encoding="utf-8") as handle:
                    handle.write("%s 重启服务：%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), reason))
            except OSError:
                pass
        try:
            subprocess.Popen([helper], shell=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=(0x00000008 | 0x08000000 if os.name == "nt" else 0))
        except OSError as exc:
            raise ToolError("启动重启脚本失败：%s" % exc)
        return ("已安排重启：当前进程退出后会自动重新启动服务（几秒后刷新页面即可）。"
                "%s" % ("原因：%s" % reason if reason else "")), {
            "kind": "restart", "helper": helper, "reason": reason}

    # -- 课堂与学习 -------------------------------------------------------
    def _tool_pick_students(self, args):
        return self._classroom(classroom.pick_students, args)

    def _tool_class_timer(self, args):
        return self._classroom(classroom.class_timer, args)

    def _tool_show_on_screen(self, args):
        return self._classroom(classroom.show_board, args)

    def _tool_show_figure(self, args):
        """AI 作立体图：算好坐标推给网页作图面板，和老师自己敲表达式走的是同一条路。

        **平面（函数 / 方程 / 平面几何）不走这里了**：老师明确要求"ai 调用工具只准用
        新加的这个平面的"。要是这里默默把 function 画了，模型就永远学不会用 plane，
        新平面里的依赖图（改父对象、交点跟着动）也就永远用不上——所以这里返回
        **结构化错误**指路，而不是兜底画一张。
        """
        kind = str(args.get("kind") or "").strip().lower()
        if kind != "solid":
            return self._plane_only(kind)
        figure, error = plot_module.build(args)
        if error:
            raise ToolError(error)
        classroom.emit(self, {"t": "figure", "figure": figure})
        # 每种 kind 的 payload 字段不一样，**不能想当然读某个键**：
        # 立体没有 "shapes"，以前这里无条件读它，画立体必 KeyError，
        # 结果模型每次都收到报错、换个写法再试，对话就停不下来了。
        body = ("已把立体图形画到网页的作图面板里（%d 个点、%d 条棱、%d 个面%s）。"
                % (len(figure.get("points") or []), len(figure.get("edges") or []),
                   len(figure.get("faces") or []),
                   "、%d 个圆" % len(figure.get("curves") or [])
                   if figure.get("curves") else ""))
        angles = figure.get("angles") or []
        if angles:
            body += " 图上有 %d 个角，右栏给了精确值。" % len(angles)
        body += " 图上的坐标是本地算出来的，直接照着讲就行。"
        return body, {"kind": "figure", "figure_kind": "solid",
                      "title": figure.get("title", ""),
                      "points": len(figure.get("points") or []),
                      "shapes": len(figure.get("shapes") or []),
                      "edges": len(figure.get("edges") or []),
                      "faces": len(figure.get("faces") or []),
                      "segments": len(figure.get("segments") or [])}

    def _plane_only(self, kind):
        """show_figure 收到非 solid 的返回：结构化错误 + 指路到 plane 工具。"""
        result = _plane_envelope(None, {
            "ok": False,
            "errors": [{"code": "wrong_tool", "path": "kind",
                        "message": "show_figure 只剩立体（kind=solid）了，不再画平面上的"
                                   "函数 / 方程 / 几何（收到的是 %s）"
                                   % (kind or "（空）"),
                        "hint": "改用 plane 工具：函数 plot_function、方程 plot_implicit、"
                                "圆/直线/交点/动点用 create_object 或 execute_command；"
                                "整批一起建用 plane_batch"}],
            "explanation": "平面作图已经统一到 plane 工具，show_figure 只在画立体时用。",
        })
        return _plane_text(result), _plane_meta("show_figure", result)

    # -- 平面（plane）-----------------------------------------------------
    #
    # 返回一律是文档 §5.2 冻结的信封：{ok, stateVersion, created, updated, removed,
    # warnings, errors, explanation}（+ handle）。动作语义在这一层，
    # 平面本身只管"对象怎么存、怎么算值"（webui/plane.py）。
    @staticmethod
    def _as_formula_list(value):
        """把 contents 统一成数组：本来就该是数组，但 AI 常写成字符串。

        写成 "HCl、NaOH" / "HCl,NaOH" / "HCl + NaOH" / "HCl NaOH" 都能认；
        不这么归一化的话，下游 dict(contents) 会抛
        dictionary update sequence element #0 has length 1; 2 is required。
        """
        if isinstance(value, (list, tuple)):
            items = value
        elif isinstance(value, str):
            items = re.split(r"[、,，+;；/\s]+", value)
        else:
            return []
        out = []
        for item in items:
            name = str(item).strip()
            if name:
                out.append(name)
        return out

    def _tool_plane(self, args):
        action = str(args.get("action") or "").strip()
        if action not in PLANE_ACTIONS:
            return _plane_fail("unknown_action", "不认识的动作：%s" % (action or "（空）"),
                               "action", "可用动作：" + "、".join(PLANE_ACTIONS))
        if action in PLANE_NOT_YET:
            return _plane_not_yet(action)
        plane = _plane_shared()
        try:
            result = _plane_dispatch(action, plane, args)
        except Exception as exc:                       # noqa: BLE001 - 不能把栈泄给模型
            return _plane_fail("internal_error", "执行 %s 时出错：%s" % (action, exc),
                               "action", "换个动作或先用 query_state 看看现状")
        result = _plane_envelope(plane, result)
        if action in PLANE_STATE_ACTIONS and result.get("ok"):
            # 顺手把新状态推给网页：老师开着平面面板时，AI 建的对象当场出现
            # （app.js 收 {"t":"plane"}，plane.js 的 renderPlane 直接吃这份快照）。
            classroom.emit(self, {"t": "plane", "version": result.get("stateVersion"),
                                  "state": plane.snapshot()})
        return _plane_text(result), _plane_meta(action, result)

    def _tool_plane_batch(self, args):
        plane = _plane_shared()
        result = _plane_envelope(plane, plane.create_many(args.get("ops")))
        if result.get("ok"):
            classroom.emit(self, {"t": "plane", "version": result.get("stateVersion"),
                                  "state": plane.snapshot()})
        return _plane_text(result), _plane_meta("create_objects", result)

    def _tool_mistake_book(self, args):
        return self._classroom(classroom.mistake_book, args)

    def _tool_score_stats(self, args):
        return self._classroom(classroom.score_stats, args)

    def _tool_inspect_media(self, args):
        path = self.resolve(args.get("path"), must_exist=True)
        if os.path.isdir(long_path(path)):
            raise ToolError("这是目录：%s" % path)
        if not media_module.is_media_path(path):
            raise ToolError("这不像音视频文件（%s）。支持的扩展名：%s"
                            % (os.path.splitext(path)[1] or "无扩展名",
                               " ".join(sorted(media_module.MEDIA_EXTS))))
        info = media_module.inspect_media(long_path(path))
        text = media_module.media_report_text(info)
        return text, {"path": path, "kind": "media", "container": info["container"],
                      "video": info["video"], "audio": info["audio"],
                      "playable": info["playable"], "problems": info["problems"],
                      "duration": info["duration"], "bytes": info["bytes"]}

    def _tool_diagnose_pptx(self, args):
        path = self.resolve(args.get("path"), must_exist=True)
        if os.path.splitext(path)[1].lower() != ".pptx":
            raise ToolError("这个工具只用于 .pptx 课件；读内容用 read_file，生成用 write_file。")
        report = media_module.diagnose_pptx(long_path(path))
        text = media_module.pptx_report_text(report)
        meta = {"path": path, "kind": "pptx-diagnose", "slides": report["slides"],
                "media": len(report["media"]), "external": len(report["external"]),
                "problems": len(report["problems"]), "ok": report["ok"]}
        if report["problems"]:
            meta["firstProblem"] = report["problems"][0]
        return text, meta

    def _tool_extract_media(self, args):
        path = self.resolve(args.get("path"), must_exist=True)
        if os.path.splitext(path)[1].lower() != ".pptx":
            raise ToolError("只能从 .pptx 里导出媒体。")
        out_dir = args.get("out_dir") or os.path.join(os.path.dirname(path), "media")
        out_dir = self.resolve(out_dir, for_write=True)
        try:
            written = media_module.extract_media(long_path(path), long_path(out_dir))
        except (OSError, zipfile.BadZipFile) as exc:
            raise ToolError("导出失败：%s" % friendly_os_error(exc, path))
        if not written:
            return "这个课件里没有内嵌的视频/音频（可能是外链，或者本来就没插媒体）。", {
                "path": path, "kind": "extract", "count": 0}
        lines = ["已导出 %d 个媒体文件到 %s：" % (len(written), out_dir)]
        for item in written:
            size = os.path.getsize(item) / 1048576.0
            lines.append("- %s（%.1f MB）" % (os.path.basename(item), size))
        lines.append("")
        lines.append("下一步：用 inspect_media 逐个体检；需要转码时转成 MP4(H.264)+AAC，"
                     "再用 replace_media 替换回课件。")
        return "\n".join(lines), {"path": path, "kind": "extract", "count": len(written),
                                  "dir": out_dir}

    def _tool_replace_media(self, args):
        path = self.resolve(args.get("path"), must_exist=True)
        new_file = self.resolve(args.get("new_file"), must_exist=True)
        part = str(args.get("part") or "").strip()
        if not part:
            raise ToolError("请给出要替换的媒体部件名，例如 media1.mp4（先用 diagnose_pptx 看）")
        snapshot = self._snapshot(path)
        ok, message = media_module.replace_media(long_path(path), part, long_path(new_file))
        if not ok:
            raise ToolError(message)
        return ("%s\n（已覆盖原课件，快照：%s，可用快照回滚）" % (message, snapshot or "无")), {
            "path": path, "kind": "replace-media", "snapshot": snapshot, "part": part}

    @staticmethod
    def _verdict_line(code, note, seconds):
        """把 exit code / 超时说明翻成一句人话结论，避免 AI 靠猜。"""
        low = (note or "").lower()
        if "超时" in (note or "") or "timeout" in low:
            return ("[结果] 超时：命令没在限定时间内结束，已经结束整棵进程树"
                    "（可能是个带界面的程序在等，或者命令本身卡住了）。")
        if code == 0:
            return ("[结果] 成功：命令执行完了（exit=0，用时 %.1f 秒）。"
                    "注意 exit=0 只代表命令自己认为没事，"
                    "如果它是去关窗口/杀进程，请务必再用 process 或 snapshot 确认目标真的没了。"
                    % seconds)
        if code in (9009, 127):
            return ("[结果] 失败：找不到这个命令（exit=%s）—— 多半是程序名或路径写错了，"
                    "先确认它在不在 PATH 里。" % code)
        if code == 1:
            return ("[结果] 失败（exit=1）：命令报错了。常见原因：参数写错、目标不存在、"
                    "没有权限；把完整输出往下看。" )
        if code == 5:
            return "[结果] 失败（exit=5）：拒绝访问，需要管理员权限。"
        return ("[结果] 失败（exit=%s）：命令没有正常结束，具体看下面的输出。" % code)

    @staticmethod
    def _looks_like_launch(command):
        """判断这条命令是不是"启动一个带界面的程序"（这种不能等它退出）。"""
        text = (command or "").strip()
        if not text:
            return False
        for bad in ("|", ">", "<", "&&", "&", "taskkill", "stop-process", "kill"):
            if bad in text.lower():
                return False
        low = text.lower().strip(chr(34)).strip()
        if low.startswith(("start ", "explorer ", "cmd /c start", "cmd.exe /c start")):
            return True
        tokens = [item.strip(chr(34)) for item in text.split() if item.strip()]
        if len(tokens) == 1:
            token = tokens[0].lower()
            if token.endswith(".exe"):
                return True
            if token in ('notepad', 'calc', 'mspaint', 'explorer', 'winword', 'excel', 'powerpnt', 'wps', 'wpp', 'et', 'chrome', 'msedge', 'firefox', 'mstsc', 'regedit', 'control', 'taskmgr', 'osk', 'magnify', 'snippingtool'):
                return True
        return False

    def _launch_detached(self, command, cwd):
        """分离启动，不等它退出（打开记事本这类命令用它，否则会一直卡住）。"""
        import subprocess
        text = command.strip().strip(chr(34))
        low = text.lower()
        if low.startswith("cmd /c start") or low.startswith("cmd.exe /c start"):
            text = text.split("start", 1)[1].strip()
        elif low.startswith("start "):
            text = text[6:].strip()
        elif low.startswith("explorer "):
            text = text[9:].strip()
        flags = 0
        if os.name == "nt":
            flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | NEW_PROCESS_GROUP
        try:
            subprocess.Popen(text, cwd=cwd, shell=False, close_fds=True,
                             creationflags=flags)
        except Exception as error:                    # noqa: BLE001 - 起不来要说人话
            try:
                subprocess.Popen(text, cwd=cwd, shell=True, close_fds=True,
                                 creationflags=flags)
            except Exception:
                raise ToolError("启动失败：%s" % error)
        time.sleep(1.2)
        note = ("\n（这是「打开窗口」的命令，已改成不等它退出、直接返回；"
                "要关掉它请用 app（mode 传 close，name 传标题关键字），"
                "关完用 snapshot 复查。）")
        try:
            windows, error = self._desktop().list_windows(limit=60)
            if not error and windows:
                names = [item for item in windows
                         if str(item.get("process") or "").lower().replace(".exe", "")
                         in self._close_targets(command)]
                if names:
                    note += "\n[复查] 已经开出来了：%s" % "；".join(
                        "%s（pid %s）" % (item.get("title"), item.get("pid"))
                        for item in names[:3])
                else:
                    note += "\n[复查] 没在窗口列表里看到它，可能还在打开中；"
                    note += "可以用 snapshot 看一眼窗口列表。"
        except Exception as error:                    # noqa: BLE001
            note += "\n[复查失败：%s]" % error
        return "[结果] 已启动（启动类命令，未等待它退出）\n$ %s\n%s" % (command, note), {
            "command": command, "exitCode": 0, "kind": "launch", "cwd": cwd}

    @staticmethod
    def _looks_like_close(command):
        text = (command or "").lower()
        for word in ("taskkill", "stop-process", "kill", "关闭", "关掉", "退出", "close",
                     "terminate", "wm_close", "alt+f4"):
            if word in text:
                return True
        return False

    def _close_targets(self, command):
        """从命令里猜要关哪个程序（.exe 名、进程名、窗口标题关键字）。"""
        names = set()
        for token in re.findall(r"([A-Za-z0-9_\-]+\.exe)", command or "", re.I):
            names.add(token[:-4].lower())
        for token in re.findall(r"(?:/im|/IM|-[Nn]ame|-Name|/pid|/PID)\s+([A-Za-z0-9_.\-]+)",
                                command or ""):
            names.add(token.lower().replace(".exe", ""))
        if "notepad" in (command or "").lower():
            names.add("notepad")
        return names

    def _close_check(self, command):
        """关窗口类命令跑完后的复查：目标窗口还在不在。"""
        try:
            module = self._desktop()
            windows, error = module.list_windows(limit=60)
        except Exception as error:                     # noqa: BLE001 - 复查失败不该影响命令结果
            return "\n\n[复查失败：%s]" % error
        if error:
            return "\n\n[复查失败：%s]" % error
        targets = self._close_targets(command)
        alive = []
        for item in windows or []:
            name = str(item.get("process") or "").lower().replace(".exe", "")
            title = str(item.get("title") or "")
            hit = name in targets if targets else True
            if not hit and targets:
                for target in targets:
                    if target and target in title.lower():
                        hit = True
                        break
            if hit:
                alive.append("%s（进程 %s，pid %s）" % (title, item.get("process"), item.get("pid")))
        if not targets:
            return ("\n\n[复查] 这条命令看着像在关窗口，但没看出要关哪个：请用 app "
                    "（mode 传 close，name 传标题关键字），关完再用 snapshot 复查。")
        if alive:
            return ("\n\n[复查] ⚠ 窗口还在，没关掉：%s\n"
                    "试试 app（mode 传 close，name 传标题关键字或 pid:1234）；"
                    "带界面的新版记事本/商店应用用 taskkill、Stop-Process 经常关不掉。"
                    "关完请用 snapshot 复查，别再干等。" % "；".join(alive[:4]))
        return "\n\n[复查] 目标窗口已经不在，确认关掉了。"

    def _tool_run_command(self, args):
        command = str(args.get("command") or "").strip()
        verdict = self.authorize("run_command", {"command": command})
        if not verdict.get("allow"):
            raise ToolError(verdict.get("output") or "命令被拒绝")
        cwd = self.root
        if args.get("cwd"):
            candidate = self.resolve(args.get("cwd"), must_exist=True)
            if os.path.isdir(long_path(candidate)):
                cwd = candidate
        if self._looks_like_launch(command):
            return self._launch_detached(command, cwd)
        started = time.time()
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        raw, code, note = _run_shell(command, cwd, creationflags, MAX_COMMAND_SECONDS,
                                      cancel=self.cancel)
        seconds = round(time.time() - started, 2)
        limit = int(self.command.get("max_output_bytes") or 65536)
        truncated = len(raw) > limit
        text = decode_text(raw[:limit])
        if truncated:
            text += "\n[输出已截断]"
        audit_error = self._audit(command, code)
        header = "%s\n$ %s\n(exit=%s, %.2fs%s)\n" % (
            self._verdict_line(code, note, seconds), command, code, seconds,
            "，输出已截断" if truncated else "")
        if not text.strip():
            text = "(无输出)"
        if note:
            text += note
        if audit_error:
            text += "\n[审计日志写入失败：%s]" % audit_error
        if self._looks_like_close(command):
            text += self._close_check(command)
        return header + text, {"command": command, "exitCode": code, "seconds": seconds,
                               "cwd": cwd, "truncated": truncated, "kind": "command"}

    def _tool_web_search(self, args):
        """联网搜索：优先用配置的自定义接口，否则退回内置引擎解析。"""
        config = dict(self.settings.webui.get("search") or {})
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("请给出搜索关键词")
        requested_engine = args.get("engine") or config.get("engine") or "auto"
        try:
            results = search_module.search(
                query,
                engine=requested_engine,
                endpoint=config.get("endpoint") or "",
                api_key=config.get("api_key") or "",
                max_results=args.get("max_results") or config.get("max_results") or 5,
                timeout=int(config.get("timeout") or 20),
                insecure=bool(self.settings.agent.get("insecure_skip_verify")),
            )
        except search_module.SearchError as exc:
            raise ToolError("搜索失败：%s" % exc)
        if not results:
            raise ToolError("没有搜索到结果，请换关键词再试。")
        lines = ["搜索“%s”共 %d 条结果：" % (query, len(results))]
        for index, item in enumerate(results, start=1):
            lines.append("%d. %s\n   %s\n   %s"
                         % (index, item.get("title") or "(无标题)",
                            item.get("url") or "", item.get("snippet") or ""))

        # fetch_top：顺手读前几条的正文摘要，减少“搜完再逐个打开”的来回
        try:
            fetch_top = int(args.get("fetch_top") or 0)
        except (TypeError, ValueError):
            fetch_top = 0
        fetched = []
        for item in results[:max(0, min(3, fetch_top))]:
            try:
                text, source = self._read_one(item["url"])
            except ToolError as exc:
                lines.append("\n--- %s\n（读取失败：%s）" % (item["url"], exc))
                continue
            excerpt = text.strip()[:800]
            fetched.append({"url": item["url"], "source": source, "chars": len(text)})
            lines.append("\n--- %s（%s，正文 %d 字，节选）\n%s"
                         % (item["url"], source, len(text), excerpt))
        if not fetched and fetch_top:
            lines.append("\n（未能读取结果正文，可稍后用 read_url 单独打开。）")

        lines.append("")
        lines.append("需要更多正文时用 read_url（可一次传 urls 数组，最多 3 个）。")
        return "\n".join(lines), {"kind": "search", "query": query, "engine": requested_engine,
                                  "count": len(results), "results": results,
                                  "fetched": fetched}

    def _tool_read_url(self, args):
        urls = []
        if isinstance(args.get("urls"), list):
            urls.extend(args["urls"])
        if args.get("url"):
            urls.append(args["url"])
        urls = [str(item).strip() for item in urls if str(item).strip()][:3]
        if not urls:
            raise ToolError("请提供 url 或 urls（最多 3 个）")
        sections = []
        sources = []
        for url in urls:
            if not re.match(r"^https?://", url, re.I):
                raise ToolError("URL 必须以 http:// 或 https:// 开头：%s" % url)
            text, source = self._read_one(url)
            sections.append("## %s\n\n%s" % (url, text))
            sources.append({"url": url, "source": source,
                            "bytes": len(text.encode("utf-8", "replace"))})
        body = "\n\n".join(sections)
        if len(body) > self.max_chars:
            body = body[:self.max_chars] + "\n\n[内容已截断]"
        return body, {"kind": "url", "urls": urls, "sources": sources}

    def _read_one(self, url):
        """单个页面：优先复用 agent.exe 的正文提取，失败或缺失时用内置抓取兜底。"""
        text = ""
        source = ""
        if os.path.isfile(self.agent_exe):
            creationflags = 0x08000000 if os.name == "nt" else 0
            try:
                completed = subprocess.run([self.agent_exe, "web", "read", url],
                                           cwd=self.settings.base_dir, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, timeout=180,
                                           creationflags=creationflags)
                if completed.returncode == 0:
                    text = decode_text(completed.stdout or b"")
                    source = "agent.exe"
            except (subprocess.TimeoutExpired, OSError):
                text = ""
        if not text.strip():
            text = _python_fetch(url, insecure=bool(
                self.settings.agent.get("insecure_skip_verify")))
            source = "内置抓取"
        if not text.strip():
            raise ToolError("没有取到正文（可能是脚本渲染的页面）：%s" % url)
        return text.strip(), source

    # -- 快照 -------------------------------------------------------------
    def _snapshot(self, path):
        if not os.path.isdir(self.snapshot_dir):
            os.makedirs(self.snapshot_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "%s-%s" % (stamp, os.path.basename(path))
        target = os.path.join(self.snapshot_dir, name)
        index = 1
        while os.path.exists(target):
            target = os.path.join(self.snapshot_dir, "%s-%d-%s" % (stamp, index, os.path.basename(path)))
            index += 1
        try:
            with open(long_path(path), "rb") as source, open(long_path(target), "wb") as destination:
                shutil.copyfileobj(source, destination)
        except OSError as exc:
            raise ToolError(friendly_os_error(exc, path))
        self._prune_snapshots(200)
        return target

    def _prune_snapshots(self, keep):
        try:
            items = [os.path.join(self.snapshot_dir, name) for name in os.listdir(self.snapshot_dir)]
        except OSError:
            return
        if len(items) <= keep:
            return
        items.sort(key=lambda item: os.path.getmtime(item))
        for item in items[: len(items) - keep]:
            try:
                os.remove(item)
            except OSError:
                pass

    def snapshots(self, limit=50):
        if not os.path.isdir(self.snapshot_dir):
            return []
        items = []
        for name in os.listdir(self.snapshot_dir):
            full = os.path.join(self.snapshot_dir, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            items.append({"file": name, "path": full, "size": stat.st_size,
                          "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))})
        items.sort(key=lambda item: item["time"], reverse=True)
        return items[:limit]

    def restore(self, name):
        safe = os.path.basename(name)
        source = os.path.join(self.snapshot_dir, safe)
        if not os.path.isfile(source):
            raise ToolError("快照不存在：%s" % safe)
        match = re.match(r"^\d{8}-\d{6}(?:-\d+)?-(.+)$", safe)
        target = match.group(1) if match else ""
        if not target:
            raise ToolError("无法从快照名推断原文件：%s" % safe)
        return source, target

    # -- 审计 -------------------------------------------------------------
    def _audit(self, command, code):
        """写审计日志；写不进去时返回原因（调用方会告诉用户），不再静默吞掉。"""
        log = self.command.get("audit_log") or ""
        if not log:
            return ""
        path = log if os.path.isabs(log) else os.path.join(self.settings.base_dir, log)
        directory = os.path.dirname(path)
        status = "ok" if code == 0 else ("exit=%s" % code)
        line = "%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), _audit_field(status),
                                 _audit_field(command))
        try:
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError as exc:
            return str(exc)
        return ""


def _audit_field(value):
    return (str(value).replace("\\", "\\\\").replace("\r", "\\r")
            .replace("\n", "\\n").replace("\t", "\\t"))


# --------------------------------------------------------------------------
# 平面（plane）：动作实现
#
# 每个函数都返回文档 §5.2 冻结的信封，只由 _plane_envelope 补键 + 统一失败形状。
# --------------------------------------------------------------------------

def _plane_shared():
    """拿到**共享**的那块平面（和网页 /api/plane/* 是同一个实例）。

    plane.py 缺失或有语法错时抛 ValueError 带中文说明——这类环境问题不该让模型
    以为"动作写错了"去反复改参数。
    """
    try:
        return plane_hub.get()
    except ImportError as exc:
        raise ValueError("平面模块 webui\\plane.py 还不能用（%s）。请老师先重新打包或"
                         "把 plane.py 放回 webui\\ 目录。" % exc)
    except Exception as exc:                       # noqa: BLE001 - 写了一半的 plane.py
        raise ValueError("平面模块 webui\\plane.py 起不来：%s" % exc)


def _plane_envelope(plane, result):
    """补全冻结信封的八个键（plane 的失败结构少了 created/updated/removed）。"""
    if result is False:
        result = {"ok": False, "errors": [{"code": "bad_definition", "message": "操作没有完成",
                                           "path": "", "hint": "先用 query_state 看看现状"}]}
    if not isinstance(result, dict):
        result = {"ok": False, "errors": [{"code": "internal_error",
                                           "message": "平面没有返回结果",
                                           "path": "", "hint": "重试一次；还不行就用 query_state"}]}
    version = result.get("stateVersion")
    if not isinstance(version, int) and plane is not None:
        version = getattr(plane, "state", {}).get("version", 0)
    out = {"ok": bool(result.get("ok")), "stateVersion": version if isinstance(version, int) else 0,
           "created": list(result.get("created") or []),
           "updated": list(result.get("updated") or []),
           "removed": list(result.get("removed") or []),
           "warnings": list(result.get("warnings") or []),
           "errors": [_plane_error(item) for item in (result.get("errors") or [])],
           "explanation": str(result.get("explanation") or ""),
           "handle": str(result.get("handle") or "")}
    if not out["ok"] and not out["errors"]:
        out["errors"] = [_plane_error({"code": "internal_error", "message": "操作没有完成",
                                       "hint": "先用 query_state 看看现状"})]
    # 信封之外的自有载荷（get_object 的 object、query_state 的 counts/total、
    # export/snapshot 的 state…）原样带上：模型要的就是它们，丢了就等于没查。
    for key, value in result.items():
        if key not in out:
            out[key] = value
    return out


def _plane_error(item):
    """错误必须是结构化的（code / message / path / hint），少一个键就给模型补上。"""
    if isinstance(item, str):
        item = {"code": "bad_value", "message": item}
    if not isinstance(item, dict):
        item = {"code": "bad_value", "message": str(item)}
    out = {"code": str(item.get("code") or "bad_value"),
           "message": str(item.get("message") or "操作没有完成"),
           "path": str(item.get("path") or ""),
           "hint": str(item.get("hint") or "")}
    if not out["hint"]:
        out["hint"] = "先 query_state 看看现状，再按 definition 的形状重试"
    return out


def _plane_fail(code, message, path="action", hint=""):
    result = _plane_envelope(None, {"ok": False,
                                    "errors": [{"code": code, "message": message,
                                                "path": path, "hint": hint}]})
    return _plane_text(result), _plane_meta("error", result)


def _plane_not_yet(action):
    """还没做的动作：明确说不，并给出**现在该怎么绕过去**——绝不静默成功。"""
    result = _plane_envelope(None, {
        "ok": False,
        "errors": [{"code": "not_implemented",
                    "message": "%s 还没有实现" % action,
                    "path": "action", "hint": PLANE_NOT_YET.get(action, "换个动作")}],
        "explanation": "这个动作现在还不能用，别当成已经做完了。",
    })
    return _plane_text(result), _plane_meta(action, result)


def _plane_text(result):
    """给模型看的正文：紧凑 JSON（冻结信封原样），不夹散文。"""
    return json.dumps(result, ensure_ascii=False)


def _plane_meta(action, result):
    errors = result.get("errors") or []
    return {"kind": "plane", "action": action, "ok": bool(result.get("ok")),
            "version": result.get("stateVersion"), "created": len(result.get("created") or []),
            "updated": len(result.get("updated") or []),
            "removed": len(result.get("removed") or []),
            "code": errors[0]["code"] if errors else "",
            "error": errors[0]["message"] if errors else ""}


def _plane_number(value, code, message, path, hint):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, _plane_fail(code, message, path, hint)
    return float(value), None


def _check_ops(args):
    """plane_batch 的参数检查：ops 必须是非空数组（空数组会静默什么都没做）。"""
    ops = args.get("ops")
    if isinstance(ops, dict):
        ops = [ops]
    if not isinstance(ops, list) or not ops:
        return None, [{"code": "missing_field",
                       "message": "ops 必须是非空数组，例如 "
                                  "[{\"op\": \"create\", \"type\": \"point\", \"id\": \"A\", "
                                  "\"definition\": {\"x\": 0, \"y\": 0}}]",
                       "path": "ops", "hint": "把要建的每个对象写成一项 op=create"}]
    return ops, []


def _plane_module():
    """延迟 import plane.py：这个模块缺一块时也别让整个工具层起不来。"""
    import plane as plane_module
    return plane_module


def _plane_create(plane, args):
    if args.get("ops"):
        # 别猜：两个写法同时给会写出哪一批是说不清的，直接让模型挑一个
        return {"ok": False, "errors": [{"code": "bad_value", "path": "ops",
                                         "message": "create_object 不要再给 ops 了",
                                         "hint": "一次建多个请用 create_objects（或 plane_batch）"}]}
    kind = str(args.get("type") or "").strip()
    if not kind:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "type",
                                         "message": "create_object 缺少 type",
                                         "hint": "可用类型：" + "、".join(_plane_module().TYPE_NAMES)}]}
    return plane.create_many([{"op": "create", "type": kind, "id": args.get("id"),
                               "definition": args.get("definition"),
                               "style": args.get("style"), "meta": args.get("meta")}])


def _plane_create_many(plane, args):
    ops, errors = _check_ops(args)
    if errors:
        return {"ok": False, "errors": errors}
    return plane.create_many(ops)


def _plane_update(plane, args):
    oid = str(args.get("id") or "").strip()
    if not oid:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "id",
                                         "message": "update_object 缺少 id",
                                         "hint": "说明改哪一个对象（id 就是标签，例如 A）"}]}
    fields = {key: args[key] for key in ("definition", "style", "meta") if key in args}
    if not fields:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "definition",
                                         "message": "update_object 没有要改的内容",
                                         "hint": "给 definition / style / meta 里的至少一个，"
                                                 "例如 definition={\"x\": 3}"}]}
    return plane.update(oid, **fields)


def _plane_delete_ids(args):
    """把 ``id`` / ``ids`` / ``ops`` 三种写法统一成一份去重后的 id 列表。"""
    collected = []
    ids = args.get("ids")
    if isinstance(ids, str) and ids.strip():
        ids = [ids]
    if isinstance(ids, (list, tuple)):
        collected.extend(ids)
    ops = args.get("ops")
    if isinstance(ops, (str, dict)):
        ops = [ops]
    if isinstance(ops, (list, tuple)):
        for item in ops:
            collected.append(item.get("id") if isinstance(item, dict) else item)
    oid = args.get("id")
    if oid is not None and str(oid).strip():
        collected.append(oid)
    out, seen = [], set()
    for item in collected:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _plane_delete(plane, args):
    ids = _plane_delete_ids(args)
    if not ids:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "id",
                                         "message": "delete_object 缺少 id",
                                         "hint": "给 id=\"A\"，或 ids=[\"A\",\"B\"] 一次删多个；"
                                                 "删父对象（线段/圆）会自动级联删挂在它上面的点"}]}
    if len(ids) == 1:
        return plane.delete(ids[0])
    # 多个一起删：走同一批（原子 + 一步撤销），别挨个删留下半张图。
    # 同一批里同时给「父对象 + 它的后代」也没关系：后面的那条按"已经被级联删掉"跳过，
    # 不会像以前那样整批回滚。
    return plane.create_many([{"op": "delete", "id": oid} for oid in ids])


def _plane_list(plane, args):
    # 列表里的曲线/函数只给"认得出这是什么"的 value：一个显函数 2400 个采样点 ≈ 4 万字符，
    # 逐条列出来会把上下文一次吃光（要看完整采样点用 get_object）。
    compact_value = _plane_module().compact_value
    objects = plane.list_objects(type=args.get("type"))
    rows = [{"id": item["id"], "type": item["type"], "value": compact_value(item["value"]),
             "visible": bool(item["style"].get("visible", True))} for item in objects]
    total = len(plane.state["objects"])
    return {"ok": True, "stateVersion": plane.state["version"], "objects": rows,
            "counts": plane.counts(), "total": total,
            "explanation": "共 %d 个对象%s（曲线只给大致形状，采样点用 get_object 取）"
                           % (total, "，下面按创建顺序列出" if total else "")}


def _plane_get(plane, args):
    oid = str(args.get("id") or "").strip()
    if not oid:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "id",
                                         "message": "get_object 缺少 id",
                                         "hint": "id 就是标签，例如 A、c1"}]}
    obj = plane.get(oid)
    if obj is None:
        return {"ok": False, "errors": [{"code": "not_found", "path": "id",
                                         "message": "没有这个对象：%s" % oid,
                                         "hint": "先用 query_state 或 list_objects 看看有哪些"}]}
    return {"ok": True, "stateVersion": plane.state["version"], "object": obj,
            "explanation": "对象 %s 的完整内容（含算出来的 value）" % oid}


def _plane_query(plane, args):
    return plane.query_state(ids=args.get("ids"), type=args.get("type"),
                             tag=args.get("tag"), visible=args.get("visible"),
                             limit=args.get("limit"), compact=args.get("compact", True))


def _plane_command(plane, args):
    text = str(args.get("command") or "").strip()
    if not text:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "command",
                                         "message": "execute_command 缺少 command",
                                         "hint": "可用命令：" + PLANE_COMMANDS}]}
    ops, errors = plane_hub.run_command(plane, text)
    if errors:
        return {"ok": False, "errors": errors}
    return plane.create_many(ops)


def _plane_plot_function(plane, args):
    return _plane_plot(plane, args, "function")


def _plane_plot_implicit(plane, args):
    return _plane_plot(plane, args, "implicit")


def _plane_plot_parametric(plane, args):
    return _plane_plot(plane, args, "parametric")


def _plane_plot_polar(plane, args):
    return _plane_plot(plane, args, "polar")


def _plane_plot(plane, args, kind):
    """plot_* 四个动作：内部就是建一个曲线对象（函数的 value 直接带采样点）。

    ``expr`` 之外只透传该类型认识、而且真的给了的字段：``plane_math`` 只认
    definition 里那几个键，多塞一个空值反而会触发"要非空字符串"的校验。
    """
    definition = {"expr": args.get("expr")}
    if kind == "parametric":
        definition = {"x": args.get("x") or args.get("expr"), "y": args.get("y")}
    for extra in ("var", "xmin", "xmax", "tmin", "tmax"):
        if args.get(extra) is not None:
            definition[extra] = args.get(extra)
    return plane.create_many([{"op": "create", "type": kind, "id": args.get("id"),
                               "definition": definition, "style": args.get("style")}])


def _plane_style(plane, args):
    oid = str(args.get("id") or "").strip()
    if not oid:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "id",
                                         "message": "set_style 缺少 id",
                                         "hint": "样式是加在某个对象上的，先给 id"}]}
    style = args.get("style")
    if not isinstance(style, dict) or not style:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "style",
                                         "message": "set_style 缺少 style",
                                         "hint": "例如 {\"color\": \"#e5484d\", \"size\": 6, "
                                                 "\"lineStyle\": \"dashed\"}"}]}
    return plane.update(oid, style=style)


def _plane_view(plane, args, action):
    wanted = dict(plane.state["view"])
    if action == "reset_view":
        wanted = dict(PLANE_DEFAULT_VIEW)
    given = args.get("view")
    if isinstance(given, dict):
        for key in PLANE_DEFAULT_VIEW:
            if given.get(key) is not None:
                wanted[key] = given[key]
    if action == "zoom":
        # 等比缩放：横纵轴必须同一个比例（老师明确要求过），缩完还围着中心
        raw = args.get("view") if isinstance(args.get("view"), dict) else {}
        factor = args.get("factor", raw.get("factor"))
        factor, problem = _plane_number(factor if factor is not None else 2.0, "bad_value",
                                        "zoom 的 factor 要是数字", "view.factor",
                                        "例如 factor=2 放大 2 倍，factor=0.5 缩小一半")
        if problem:
            return problem
        if factor <= 0:
            return {"ok": False, "errors": [{"code": "bad_value", "path": "view.factor",
                                             "message": "factor 要大于 0",
                                             "hint": "放大用 2，缩小用 0.5"}]}
        cx, cy = (wanted["xmin"] + wanted["xmax"]) / 2.0, (wanted["ymin"] + wanted["ymax"]) / 2.0
        half_x = (wanted["xmax"] - wanted["xmin"]) / 2.0 * factor
        half_y = (wanted["ymax"] - wanted["ymin"]) / 2.0 * factor
        wanted.update({"xmin": cx - half_x, "xmax": cx + half_x,
                       "ymin": cy - half_y, "ymax": cy + half_y})
    if action == "pan":
        raw = args.get("view") if isinstance(args.get("view"), dict) else {}
        shifts = []
        for key in ("dx", "dy"):
            value = args.get(key, raw.get(key, 0))
            value, problem = _plane_number(value, "bad_value", "pan 的 %s 要是数字" % key,
                                           "view." + key, "dx 向右为正，dy 向上为正")
            if problem:
                return problem
            shifts.append(value)
        dx, dy = shifts
        wanted.update({"xmin": wanted["xmin"] + dx, "xmax": wanted["xmax"] + dx,
                       "ymin": wanted["ymin"] + dy, "ymax": wanted["ymax"] + dy})
    return plane.set_view(**wanted)


def _plane_export(plane, args):
    return {"ok": True, "stateVersion": plane.state["version"], "state": plane.snapshot(),
            "explanation": "这块平面的完整状态（JSON 可存可发）"}


def _plane_import(plane, args):
    state = args.get("state")
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError:
            # JSON 字符串里套 JSON 是模型常见的写法（导出时 dumps 过一次），再试一层
            try:
                state = json.loads(json.loads(state))
            except ValueError:
                state = None
    if not isinstance(state, dict) or not isinstance(state.get("objects"), dict):
        return {"ok": False, "errors": [{"code": "bad_value", "path": "state",
                                         "message": "import 要一份完整的平面状态",
                                         "hint": "给 state：{version, view, objects, order}，"
                                                 "可以用 export 或 snapshot 先取出来"}]}
    fresh = plane_hub.fresh_state(state)
    # 换掉**共享**的那一个：不然网页还画着旧的那块平面，老师以为导入没成功
    plane_hub.set(fresh)
    version = fresh.state["version"]
    return {"ok": True, "stateVersion": version, "updated": list(fresh.state["order"]),
            "explanation": "已导入 %d 个对象" % len(fresh.state["objects"])}


def _plane_snapshot(plane, args):
    return {"ok": True, "stateVersion": plane.state["version"], "state": plane.snapshot(),
            "history": plane.history(),
            "explanation": "当前是第 %d 版（restore 可以回到 history 里的任一版）"
                           % plane.state["version"]}


def _plane_restore(plane, args):
    if args.get("version") is None:
        return {"ok": False, "errors": [{"code": "missing_field", "path": "version",
                                         "message": "restore 缺少 version",
                                         "hint": "先 snapshot 看 history 里有哪些版本号"}]}
    return plane.restore(args.get("version"))


def _plane_undo(plane, args):
    return plane.undo()


def _plane_redo(plane, args):
    return plane.redo()


def _plane_add_slider(plane, args):
    """滑动条：本质是一个数，definition 要 {value, min, max, step}。"""
    definition = {key: args[key] for key in ("value", "min", "max", "step") if key in args}
    return _plane_create(plane, {"type": "slider", "id": args.get("id"),
                                 "definition": definition, "style": args.get("style")})


def _plane_dispatch(action, plane, args):
    """按动作名调实现。动作名 -> 实现的对应关系在 _PLANE_ACTIONS 里（一一对得上）。"""
    return _PLANE_ACTIONS[action](plane, args)


# 动作 -> 实现。**枚举里的每个动作都必须出现在这里**（not_implemented 的那批走
# PLANE_NOT_YET，在 _tool_plane 里提前拦掉），少一个就是"模型叫了却没反应"。
_PLANE_ACTIONS = {
    "create_object": _plane_create, "create_objects": _plane_create_many,
    "update_object": _plane_update, "delete_object": _plane_delete,
    "list_objects": _plane_list, "get_object": _plane_get, "query_state": _plane_query,
    "execute_command": _plane_command,
    "plot_function": _plane_plot_function, "plot_implicit": _plane_plot_implicit,
    "plot_parametric": _plane_plot_parametric, "plot_polar": _plane_plot_polar,
    "add_slider": _plane_add_slider,
    "set_style": _plane_style,
    "zoom": lambda plane, args: _plane_view(plane, args, "zoom"),
    "pan": lambda plane, args: _plane_view(plane, args, "pan"),
    "reset_view": lambda plane, args: _plane_view(plane, args, "reset_view"),
    "export": _plane_export, "import": _plane_import,
    "undo": _plane_undo, "redo": _plane_redo,
    "snapshot": _plane_snapshot, "restore": _plane_restore,
}


def _inside(path, root):
    try:
        path_key = os.path.normcase(os.path.abspath(path))
        root_key = os.path.normcase(os.path.abspath(root))
    except (OSError, ValueError):
        return False
    if path_key == root_key:
        return True
    return path_key.startswith(root_key.rstrip("\\/") + os.sep)


def _wildcard_match(name, pattern):
    regex = "^" + re.escape(pattern).replace("\\*", ".*").replace("\\?", ".") + "$"
    return re.match(regex, name, re.I) is not None


def sniff_bytes(sample):
    """按内容判断一段字节"能不能当文本读"，返回 (是否文本, 编码名)。

    老师塞过来的文件什么都有（.dat、.log1、没扩展名、.s21 这种厂商私有格式），
    靠扩展名永远列不全，所以真正兜底的是这里。三条都要过：

      1. **没有 NUL 字节**——普通文本文件不会有（记事本存的 UTF-16 例外，先认 BOM）；
      2. **原始字节里 C0 控制符占比很低**——压缩/可执行文件里到处都是。
         这一条比"能不能解码"可靠得多：GB18030 几乎接受任何字节序列，
         一段二进制解出来会变成一串看着像汉字的乱码，光看解码成功会被骗过去；
      3. **能按 UTF-8 / GB18030 严格解码**——中文机器上 GBK 记事本文件很常见。

    宁可漏判也不误判：误判的代价是把二进制垃圾灌进模型上下文，整轮对话都废了。
    """
    if not sample:
        return True, "utf-8"        # 空文件当文本读，读出来就是空的
    for bom, name in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32-le"),
                      (b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe", "utf-16-le"),
                      (b"\xfe\xff", "utf-16-be")):
        if sample.startswith(bom):
            return True, name
    if b"\x00" in sample:
        return False, ""
    control = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 13)) + \
        sample.count(127)
    if control > max(1, len(sample) // 32):        # 超过约 3% 就不是文本
        return False, ""
    for name in ("utf-8", "gb18030"):
        try:
            sample.decode(name)
            return True, name
        except UnicodeDecodeError:
            continue
    return False, ""


def sniff_text(path):
    """读文件开头一小段做嗅探（读不出来就当作不可读的二进制）。"""
    try:
        with open(long_path(path), "rb") as handle:
            sample = handle.read(SNIFF_BYTES)
    except OSError:
        return False, ""
    return sniff_bytes(sample)


# 常见文件签名 → 中文说法。拒绝读某个文件时用它说清"这到底是什么"，
# 模型据此才知道该换哪个工具（图片→view_image、音视频→inspect_media）。
MAGIC_HINTS = (
    (b"\x89PNG\r\n\x1a\n", "PNG 图片"),
    (b"\xff\xd8\xff", "JPEG 图片"),
    (b"GIF8", "GIF 图片"),
    (b"BM", "BMP 图片"),
    (b"PK\x03\x04", "压缩包（zip / docx / xlsx / pptx 都是这个结构）"),
    (b"%PDF", "PDF 文档"),
    (b"\xd0\xcf\x11\xe0", "老版 Office 文件（doc / xls / ppt）"),
    (b"\x7fELF", "Linux 可执行文件"),
    (b"MZ", "Windows 可执行文件"),
    (b"\x1aE\xdf\xa3", "Matroska 视频（mkv / webm）"),
    (b"RIFF", "RIFF 容器（wav 音频 / avi 视频）"),
    (b"ID3", "MP3 音频（带 ID3 标签）"),
    (b"fLaC", "FLAC 音频"),
    (b"OggS", "Ogg 音频 / 视频"),
    (b"Rar!", "RAR 压缩包"),
    (b"7z\xbc\xaf", "7-Zip 压缩包"),
    (b"\x1f\x8b", "gzip 压缩文件"),
)
# 读不了时该"下一步怎么办"：模型只有知道换成什么工具（或请老师转个格式）才救得回来
MAGIC_ADVICE = {
    "PDF 文档": "PDF 读不了，请用 WPS/Word 打开后另存为 .docx 再给我；"
                "或者用「截图给AI」把要讲的那几页拍给我。",
    "老版 Office 文件（doc / xls / ppt）": "请在 Office/WPS 里另存为 .docx / .xlsx / .pptx 再给我。",
}


def binary_kind(path):
    """看一眼文件头，返回"这是个什么文件"的中文说法（认不出来返回空串）。"""
    try:
        with open(long_path(path), "rb") as handle:
            head = handle.read(16)
    except OSError:
        return ""
    for magic, label in MAGIC_HINTS:
        if head.startswith(magic):
            return label
    return ""


# --------------------------------------------------------------------------
# zip 自动解压（read_file 遇到 .zip）
# --------------------------------------------------------------------------

def _zip_member_reason(info):
    """压缩包成员名是否安全，返回中文原因（安全时返回空串）。

    zip-slip：绝对路径、盘符、``..`` 往上跳、软链接——这几种都会把文件写到目标目录
    外面去（覆盖系统文件或者老师的工作资料）。一律**整包拒绝**而不是跳过可疑条目：
    跳过等于"解压成功但不全"，老师找不到文件时根本不知道少了什么。
    """
    name = info.filename or ""
    if not name.strip() or name in (".", ".."):
        return "压缩包里有一个空名字的条目"
    if "\x00" in name:
        return "条目名里有非法字符（%s）" % name.replace("\x00", "\\0")[:80]
    if (info.external_attr >> 16) & 0xF000 == 0xA000:
        return "压缩包里有软链接（%s），解出来会指向别的位置" % name
    if name.startswith(("/", "\\")) or ntpath.isabs(name):
        return "压缩包里有绝对路径条目（%s）" % name
    if ntpath.splitdrive(name)[0]:
        # "a:b.txt" 是"盘符 + 相对路径"：解出来会落到别的盘上，一律拒绝
        return "压缩包里有带盘符的条目（%s），解出来会落到别的盘上" % name
    parts = [part for part in name.replace("\\", "/").split("/") if part not in ("", ".")]
    if ".." in parts:
        return "压缩包里有往上跳目录的条目（%s）" % name
    return ""


def _zip_parts(name):
    """条目名 → 相对路径片段（已经过 _zip_member_reason 检查，这里只做拆分）。"""
    return [part for part in name.replace("\\", "/").split("/") if part not in ("", ".")]


def _remove_tree(root):
    """删掉一个（可能超长的）目录树；删不掉也不再抛异常，免得盖住真正的错误原因。"""
    for candidate in (long_path(root), root):
        try:
            shutil.rmtree(candidate)
            return
        except OSError:
            continue


def _zip_tree_lines(root, limit=300):
    """把解压出来的目录整理成"相对路径 + 大小"的清单（给模型看，也方便老师核对）。"""
    entries = []
    # 两边都过 long_path：超长路径会带 \\?\ 前缀，只给一边加前缀 relpath 就算不出相对路径了
    base_root = long_path(root)
    for base, dirs, files in os.walk(base_root):
        relative_base = os.path.relpath(base, base_root)
        for name in sorted(dirs) + sorted(files):
            full = os.path.join(base, name)
            relative = name if relative_base == "." else os.path.join(relative_base, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            entries.append((os.path.isdir(full), relative, size))
    lines = []
    for is_dir, relative, size in entries[:limit]:
        lines.append("%s %s%s" % ("[目录]" if is_dir else "[文件]", relative,
                                  "" if is_dir else "  " + _human(size)))
    if len(entries) > limit:
        lines.append("…… 其余 %d 项已省略" % (len(entries) - limit))
    return lines, len([item for item in entries if not item[0]])


# --------------------------------------------------------------------------
# 音频元信息（read_file 遇到音频文件）
# --------------------------------------------------------------------------

# ID3v2 文本帧 → 中文标签名（只要老师真正关心的那几个）
ID3_FRAMES = {"TIT2": "标题", "TPE1": "艺术家", "TALB": "专辑", "TYER": "年份",
              "TDRC": "年份", "TCON": "流派", "TPE2": "专辑艺术家", "TCOM": "作曲",
              "COMM": "备注", "TXXX": "备注"}
VORBIS_KEYS = {"TITLE": "标题", "ARTIST": "艺术家", "ALBUM": "专辑", "DATE": "年份",
               "YEAR": "年份", "COMMENT": "备注", "DESCRIPTION": "备注",
               "GENRE": "流派", "TRACKNUMBER": "音轨", "ALBUMARTIST": "专辑艺术家",
               "LYRICS": "歌词"}
MP4_KEYS = {b"\xa9nam": "标题", b"\xa9ART": "艺术家", b"\xa9alb": "专辑",
            b"\xa9day": "年份", b"\xa9cmt": "备注", b"desc": "备注"}
# MPEG 音频帧头的比特率表：(版本, 层) -> 15 档 kbps（0 = 自由格式，15 = 非法）
MP3_BITRATES = {
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
}
MP3_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 25: (11025, 12000, 8000)}


def _id3_text(payload, offset=1):
    """ID3 文本帧的正文：首字节是编码，UTF-16 的描述与正文之间是两个 NUL。"""
    encodings = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    text = payload[offset:].decode(encodings.get(payload[0], "latin-1"), "replace")
    parts = [part.strip("\x00").strip() for part in text.split("\x00")]
    return " ".join(part for part in parts if part).strip()[:200]


def _syncsafe(data):
    """ID3v2 的长度字段是每字节 7 位的"同步安全"格式。"""
    value = 0
    for byte in data:
        value = (value << 7) | (byte & 0x7F)
    return value


def _id3v2_tags(head):
    """ID3v2 文本帧（v2.2 / v2.3 / v2.4 都认；只读前面这段就够）。"""
    tags = {}
    if len(head) < 10 or head[:3] != b"ID3":
        return tags
    major = head[3]
    body = head[10:10 + _syncsafe(head[6:10])]
    offset = 0
    while offset + 6 <= len(body):
        if major == 2:
            frame_id = body[offset:offset + 3].decode("latin-1")
            size = int.from_bytes(body[offset + 3:offset + 6], "big")
            payload = body[offset + 6:offset + 6 + size]
            offset += 6 + size
        else:
            frame_id = body[offset:offset + 4].decode("latin-1")
            raw_size = body[offset + 4:offset + 8]
            size = _syncsafe(raw_size) if major >= 4 else int.from_bytes(raw_size, "big")
            payload = body[offset + 10:offset + 10 + size]
            offset += 10 + size
        if not frame_id.strip("\x00") or size <= 0:
            break
        label = ID3_FRAMES.get(frame_id)
        if label and label not in tags:
            value = _id3_text(payload, 4 if frame_id in ("COMM", "TXXX") else 1)
            if value:
                tags[label] = value
    return tags


def _id3v1_tags(path):
    """ID3v1：最后 128 字节，老 mp3 只有这个（中文多半是 GBK，交给 decode_text 认）。"""
    try:
        with open(long_path(path), "rb") as handle:
            handle.seek(-128, os.SEEK_END)
            tail = handle.read(128)
    except OSError:
        return {}
    if len(tail) < 128 or tail[:3] != b"TAG":
        return {}
    tags = {}
    for label, raw in (("标题", tail[3:33]), ("艺术家", tail[33:63]), ("专辑", tail[63:93]),
                       ("年份", tail[93:97]), ("备注", tail[97:127])):
        value = decode_text(raw).strip("\x00 ").strip()
        if value:
            tags[label] = value[:200]
    return tags


def _mp3_params(head, size):
    """从第一个 MPEG 帧头读出比特率 / 采样率 / 声道，并按 CBR 估时长。"""
    offset = 10 + _syncsafe(head[6:10]) if head[:3] == b"ID3" and len(head) >= 10 else 0
    index = head.find(b"\xff", offset)
    while 0 <= index and index + 4 <= len(head):
        if head[index + 1] & 0xE0 == 0xE0:       # 帧同步：11 个 1
            break
        index = head.find(b"\xff", index + 1)
    if index < 0 or index + 4 > len(head):
        return []
    version_bits = (head[index + 1] >> 3) & 0x03
    layer_bits = (head[index + 1] >> 1) & 0x03
    bitrate_index = (head[index + 2] >> 4) & 0x0F
    rate_index = (head[index + 2] >> 2) & 0x03
    mode = (head[index + 3] >> 6) & 0x03
    version = {3: 1, 2: 2, 0: 25}.get(version_bits)
    layer = 4 - layer_bits
    if not version or layer not in (1, 2, 3) or rate_index > 2 or not 0 < bitrate_index < 15:
        return []
    table = MP3_BITRATES.get((1 if version == 1 else 2, layer))
    bitrate = table[bitrate_index] if table else 0
    rate = MP3_RATES[version][rate_index]
    if not bitrate or not rate:
        return []
    seconds = max(0, size - index) * 8 / float(bitrate * 1000)
    return ["比特率：约 %d kbps（CBR 估算）" % bitrate,
            "采样率：%d Hz" % rate,
            "声道：%s" % ("单声道" if mode == 3 else "双声道（立体声）"),
            "时长：约 %.1f 秒" % seconds]


def _wave_params(path):
    """wav：直接用标准库 wave 读，最准。"""
    try:
        with wave.open(long_path(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            channels = handle.getnchannels()
            width = handle.getsampwidth()
    except (wave.Error, OSError, EOFError):
        return []
    if not rate or not width:
        return []
    return ["采样率：%d Hz" % rate,
            "声道：%s" % ("单声道" if channels == 1 else "%d 声道" % channels),
            "位深：%d 位" % (width * 8),
            "比特率：约 %d kbps" % (rate * channels * width * 8 // 1000),
            "时长：约 %.1f 秒" % (frames / float(rate))]


def _vorbis_items(data, at):
    """Vorbis comment 的 KEY=value 列表（ogg 与 flac 的这块布局是一样的）。"""
    tags = {}
    try:
        vendor = struct.unpack("<I", data[at:at + 4])[0]
        at += 4 + vendor
        count = struct.unpack("<I", data[at:at + 4])[0]
        at += 4
        for _ in range(min(count, 64)):
            length = struct.unpack("<I", data[at:at + 4])[0]
            at += 4
            item = data[at:at + length].decode("utf-8", "replace")
            at += length
            key, _, value = item.partition("=")
            label = VORBIS_KEYS.get(key.strip().upper())
            if label and value.strip() and label not in tags:
                tags[label] = value.strip()[:200]
    except (struct.error, IndexError):
        return tags
    return tags


def _vorbis_tags(data):
    """Ogg（含 ogg 里的 vorbis/opus）：先找到 \\x03vorbis 标识，再读 KEY=value 列表。"""
    at = data.find(b"\x03vorbis")
    return _vorbis_items(data, at + 7) if at >= 0 else {}


def _flac_tags(head):
    """FLAC 的元数据块链：type=4 的 VORBIS_COMMENT 块里就是 TITLE/ARTIST 这些。

    FLAC 的注释块**没有** ogg 那个 \\x03vorbis 标识，所以不能直接套 _vorbis_tags。
    """
    at = 4
    while at + 4 <= len(head):
        block_type = head[at] & 0x7F
        last = bool(head[at] & 0x80)
        length = int.from_bytes(head[at + 1:at + 4], "big")
        if block_type == 4:
            return _vorbis_items(head, at + 4)
        if last or length <= 0:
            break
        at += 4 + length
    return {}


def _flac_params(head):
    """FLAC 的 STREAMINFO：采样率 20 位 / 声道 3 位 / 位深 5 位 / 总采样数 36 位。"""
    if len(head) < 26:
        return []
    packed = int.from_bytes(head[18:26], "big")
    rate = packed >> 44
    channels = ((packed >> 41) & 0x07) + 1
    bits = ((packed >> 36) & 0x1F) + 1
    samples = packed & 0xFFFFFFFFF
    if not rate:
        return []
    return ["采样率：%d Hz" % rate,
            "声道：%s" % ("单声道" if channels == 1 else "%d 声道" % channels),
            "位深：%d 位" % bits,
            "时长：约 %.1f 秒" % (samples / float(rate)) if samples else "时长：未知"]


def _mp4_tags(path):
    """m4a / mp4 的 ilst 标签（moov > udta > meta > ilst），复用 media.py 的 box 遍历。"""
    tags = {}
    try:
        size = os.path.getsize(path)
        moov = media_module._find_box(path, 0, size, (b"moov",))
        udta = media_module._find_box(path, moov[1], moov[2], (b"udta",)) if moov else None
        meta = media_module._find_box(path, udta[1], udta[2], (b"meta",)) if udta else None
        if not meta:
            return tags
        # meta 比别的容器多 4 个字节的 version/flags，子 box 从 +4 开始
        for kind, body, stop in media_module._boxes(path, meta[1] + 4, meta[2]):
            if kind != b"ilst":
                continue
            for key, item_body, item_stop in media_module._boxes(path, body, stop):
                label = MP4_KEYS.get(key)
                if not label or label in tags:
                    continue
                for sub, data_body, _ in media_module._boxes(path, item_body, item_stop):
                    if sub != b"data":
                        continue
                    raw = media_module._read(path, 256, data_body + 8)   # 类型 4 字节 + 语言 4 字节
                    value = raw.decode("utf-8", "replace").strip("\x00").strip()
                    if value:
                        tags[label] = value[:200]
    except (OSError, struct.error, ValueError):
        return tags
    return tags


def audio_details(path):
    """音频的"便宜就能拿到"的信息，返回 (标签 dict, 参数行 list)。

    只解析老师最常见的这几种，认不出来就少说一句——绝不猜、也不编：
    mp3（ID3v1/v2 + 帧头），wav（wave 模块），flac（STREAMINFO + Vorbis comment），
    ogg（Vorbis comment），m4a/mp4（ilst）。
    """
    tags = {}
    params = []
    try:
        with open(long_path(path), "rb") as handle:
            head = handle.read(256 * 1024)
        size = os.path.getsize(long_path(path))
    except OSError:
        return tags, params
    if head[:4] == b"fLaC":
        tags.update(_flac_tags(head))
        params.extend(_flac_params(head))
    elif head[:4] == b"OggS":
        tags.update(_vorbis_tags(head))
    elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        params.extend(_wave_params(path))
    elif head[4:8] == b"ftyp":
        tags.update(_mp4_tags(path))
    elif head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"):
        # ID3v1 先放进去、ID3v2 覆盖它：老标签信息少，新标签优先
        tags.update(_id3v1_tags(path))
        tags.update(_id3v2_tags(head))
        params.extend(_mp3_params(head, size))
    return tags, params


def find_transcript(path):
    """找音频/视频旁边的同名时间轴字幕（.srt/.vtt/.txt/.lrc）。

    课堂里的真实情况：录音、网课录像一般会带一份字幕或讲稿，**这才是"音频→文字"
    靠谱的来源**——离线纯标准库做不了语音识别，谁也不能假装能。
    """
    stem = os.path.splitext(path)[0]
    for ext in TRANSCRIPT_EXTS:
        candidate = stem + ext
        if os.path.isfile(long_path(candidate)):
            return candidate
    return ""


def _human(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "%.0f%s" % (value, unit) if unit == "B" else "%.1f%s" % (value, unit)
        value /= 1024
    return "%.1fGB" % value


def _diff(old, new, context=2, limit=80):
    old_lines = old.split("\n") if old else []
    new_lines = new.split("\n")
    added = removed = 0
    output = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=context,
                                     fromfile="原内容", tofile="新内容"):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
        if len(output) < limit:
            output.append(line)
    if len(output) >= limit:
        output.append("…… 差异过长，仅显示前 %d 行" % limit)
    return added, removed, "\n".join(output)


SPILL_THRESHOLD_CHARS = 24000      # 工具结果超过这个长度就落盘、只回预览
SPILL_HEAD_CHARS = 8000
SPILL_TAIL_CHARS = 4000
# 这些工具的输出可能非常大（命令输出、读大文件、抓网页），统一走"落盘 + 预览"
SPILL_TOOLS = frozenset(("run_command", "read_file", "read_url", "web_search", "list_dir"))


def _loose_pattern(find_text):
    """忽略行尾空白与缩进差异的查找正则（模型抄代码常常差几个空格）。"""
    lines = [re.escape(line.rstrip()) for line in find_text.strip("\n").split("\n")]
    body = r"[ \t]*\n[ \t]*".join(lines)
    return re.compile(r"^[ \t]*" + body + r"[ \t]*$", re.M)


def _closest_hint(haystack, find_text):
    """找不到原文时，指出文件里最接近的一段（帮模型自己发现问题）。"""
    first = (find_text.strip().split("\n") or [""])[0].strip()
    if not first or len(first) < 4:
        return ""
    for number, line in enumerate(haystack.split("\n"), 1):
        if first[:12] in line or line.strip()[:12] == first[:12]:
            return "文件里第 %d 行有一段很像：%s" % (number, line.strip()[:80])
    return "文件里连相似的一行都没有，可能这个文件已经被改过或路径不对。"


def syntax_check(path):
    """写完代码顺手做个语法检查，把错误塞回给模型（社区做法：编辑后立即 lint）。

    返回 (给模型看的一句话, 是否通过)；不是 .py/.json 就返回 ("", True)。
    只检查能可靠判断的：.py 用内置 compile（不执行）、.json 用 json.loads。
    JavaScript 不检查（Win7 上不一定有 node）。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".py", ".json"):
        return "", True
    try:
        with open(long_path(path), "rb") as handle:
            raw = handle.read(2 * 1024 * 1024)
    except OSError:
        return "", True
    text = decode_text(raw)
    try:
        if ext == ".py":
            compile(text, path, "exec")
        else:
            json.loads(text)
    except SyntaxError as exc:
        return ("⚠ 语法检查没通过（已写入，但请立刻修正）：第 %s 行 %s"
                % (exc.lineno, exc.msg)), False
    except ValueError as exc:
        return "⚠ JSON 格式检查没通过（已写入，但请立刻修正）：%s" % exc, False
    return "", True


def _diff_payload(old, new, limit=400):
    old_lines = old.split("\n") if old else []
    new_lines = new.split("\n")
    rows = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=1,
                                     fromfile="", tofile=""):
        if line.startswith(("---", "+++")):
            continue
        kind = "context"
        if line.startswith("+"):
            kind = "add"
        elif line.startswith("-"):
            kind = "del"
        elif line.startswith("@@"):
            kind = "hunk"
        rows.append({"kind": kind, "text": line})
        if len(rows) >= limit:
            rows.append({"kind": "hunk", "text": "…… 差异过长，已省略"})
            break
    return rows
