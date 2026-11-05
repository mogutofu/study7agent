# Study7Agent Quickstart

This is the full EXE edition for Windows 7 x64.

## Install

1. Unzip `agent-win7-x64.zip` to a normal user-writable directory.
2. Copy `config\agent.example.json` to `config\agent.json`.
3. Edit `config\agent.json`:
   - `base_url`: internal OpenAI-compatible `/v1/chat/completions` endpoint
   - `model`: internal model name
   - `api_key`: leave empty if your gateway does not require it
4. Run:

```bat
agent.exe chat
```

## Common Commands

```bat
agent.exe chat
agent.exe skills
agent.exe model list
agent.exe model use fast
agent.exe memory show
agent.exe doc read report.docx
agent.exe doc ask report.docx "总结这个文档"
agent.exe doc rewrite report.docx --out report.rewrite.docx --instruction "改写得更正式"
agent.exe doc draft --type meeting --out meeting.md --topic "下周项目例会"
agent.exe doc create --format xlsx --out table.xlsx --prompt "生成三列表格：姓名、部门、备注"
agent.exe web read http://intranet/
agent.exe web ask http://intranet/ "这页讲了什么"
```

## Command Execution

Local command execution is enabled by default. Ordinary commands run directly; high-risk command prefixes ask for confirmation:

```json
"command": {
  "enabled": true,
  "confirm_prefixes": ["del", "reg", "powershell", "shutdown"],
    "blocked_prefixes": [],
    "always_confirm": false,
    "max_tool_rounds": 8
}
```

Commands in `blocked_prefixes` are refused.

When the model requests a command, the CLI sends stdout, errors, and cancellation results back to the model so it can continue the task. `max_tool_rounds` prevents an accidental infinite tool loop.

## Limitations

- Requires a Windows 7 x64 build of `agent.exe`.
- Static web pages are supported. JavaScript-rendered pages are not rendered.
- `docx` and `xlsx` support focuses on readable/generated content, not perfect Office layout preservation.

## Thinking fold (dsh-turn-fold style)

### Reasoning effort

Next to the model picker there is a reasoning-effort picker: 默认 / 最省 / 低 / 中 / 高. It is stored per
session and sent to the gateway as `reasoning_effort` (默认 sends nothing at all, leaving the endpoint's own
behaviour untouched). The settings dialog sets the default for new sessions, subagents inherit the level, and
the run inspector shows what was actually used. If the gateway rejects the parameter, the run drops it, retries
once and tells you inline.

Each turn renders as one collapsible step bar containing the model's reasoning and every consecutive
tool call; the final answer always sits outside it.

- While running the bar is expanded, the title reads 思考中… and a spinner animates; during a tool call
  the title becomes 调用 read_file….
- When the turn finishes the bar collapses itself: title 思考过程, summary "N 步 · 12.4s".
- Once you click the bar yourself it never auto-collapses again (your toggle wins).
- Intermediate commentary ("let me check the folder first") belongs to the answer itself: it is appended
  in order, and a tool call never clears or splits the message.
- One question = one continuous assistant message. If the model runs into the length cap or writes
  nothing at all, the server keeps writing on its own (up to 3 times) and says so inline.
- If the run really stops early (circuit breaker, tool-round limit, repeated truncation, Esc), a
  one-click 继续生成 button appears under that message — no need to type "continue" yourself. The flag
  is stored on the session, so it is still there after a page refresh, and it clears once you ask
  something new.

Reasoning is taken from either source: a native `reasoning_content` field (DeepSeek and friends), or
`<think>` / `<thinking>` / `<reasoning>` / `<thought>` tags parsed on the server (`core.ThinkSplitter`),
which is streaming-safe across chunk boundaries, tolerates unclosed tags, drops stray closing tags and
never mistakes a literal `a < b` for a tag.

## Launcher icon

`start-webui.cmd` creates a desktop shortcut using `agent.ico` and sets the folder icon on first run
(both best-effort, silent on failure). `agent.ico` holds seven sizes; regenerate it after replacing
`agent-icon.png` with:

```bat
python\python.exe scripts\make-icon.py agent-icon.png agent.ico
```

## Web UI Edition Quickstart

The Web UI edition is a local browser console that needs no installation: it runs on the bundled
`python\python.exe` (Python 3.8.10) and reuses the same `agent.exe`, with every asset shipped offline.

```bat
start-webui.cmd
```

The console prints the model, endpoint, workspace, and URL, then opens the browser
(default <http://127.0.0.1:8770>). Press `Ctrl+C` in the console window to stop it.

Useful options:

```bat
start-webui.cmd --port 8899
start-webui.cmd --no-open
start-webui.cmd --verbose
```

It provides streaming chat with a collapsible reasoning panel, native tool calling
(`list_dir`, `read_file`, `write_file`, `run_command`, `read_url`), per-tool cards with diffs,
high-risk command confirmation dialogs, file snapshots with one-click rollback, multiple isolated
sessions, and live context-cache statistics and cost estimates for DeepSeek.

For classroom all-in-one panels it also produces real Office files in-process:

| Ask for | Format | Notes |
| --- | --- | --- |
| 课件 (courseware) | `.pptx` | 16:9, 36pt titles / 28pt body; `#` per slide, `-` bullets, `|` tables, `>` speaker notes, optional school `template` |
| 教案 / 试卷 / 通知 | `.docx` | heading levels, real tables, lists, inline bold |
| 成绩表 | `.xlsx` | bold frozen header row, auto column widths |
| 讲稿 | `.md` | speaker notes are written to a companion markdown file |

The toolbar's screen button switches on touch mode (bigger type and hit targets, always-visible
action buttons) for finger-driven classroom panels.

## Classroom toolkit and learning loop

Built for teaching on an interactive panel:

| Tool | Use |
| --- | --- |
| `pick_students` | Draw student numbers (1-50 by default, no repeats within a round, auto-restart) or split the class into random groups; no roster file needed |
| `class_timer` | Countdown shown large on screen for group work or timed practice, with an end chime |
| `show_on_screen` | Full-screen large-type board for questions, answers and key points; `kind` selects the style, Esc closes |
| `mistake_book` | Accumulate wrong answers by topic, see the weakest topics, and generate a re-practice `.docx` (questions first, answers with error hints on a second page) |
| `score_stats` | Deterministic statistics — mean, median, std, pass/excellent rates, score bands — plus an optional `.xlsx` summary; individual scores stay hidden unless `show_names` is set |

Skills `classroom`, `flashcards` and `tiered-homework` cover lesson pacing, revision cards and
three-tier homework. One live example: 抽 2 个学号 → 大屏出题 → 3 分钟倒计时 → 错题记入错题本 →
生成重练卷 → 统计期中成绩（平均分 77.00、及格率 80.0%、优秀率 40.0%）.

## Media diagnosis: "the video in my slides will not play"

The most common classroom failure that a teacher cannot self-diagnose. Three independent causes are
checked, and every message is written so the teacher can verify it themselves:

| Stage | Tool | What it finds |
| --- | --- | --- |
| The file itself | `inspect_media` | Codec Win7 cannot play (HEVC / AV1 / Xvid), truncated file (missing `moov` index), 0-byte file, "not a video at all" |
| How it was inserted | `diagnose_pptx` | Per-slide media references, **embedded vs external link** (an external link breaks as soon as the folder or machine changes), missing media parts, embedded fonts |
| How to fix it | `extract_media` + `replace_media` | Export the clip → transcode with 格式工厂/剪映 to MP4(H.264)+AAC → put it back in place (PowerPoint does not need to re-insert it) |

Typical order: `diagnose_pptx` first (is it a link problem or a codec problem?), then `inspect_media`.
Both read only file headers — no ffmpeg, no network, nothing is modified; when ffmpeg *is* installed
the report prints the exact transcode command. The tools judge compatibility, they do **not** transcode.

## Computer control: screen, windows, mouse, keyboard, clipboard, volume

For "the projector shows nothing", "what is this dialog", "draw a line on the whiteboard":

| Tool | Notes |
| --- | --- |
| `snapshot` (screenshot + numbered list of actionable elements; the image is injected into the next round, so the model really sees the screen) | read-only |
| `screenshot` (plain picture, no element walk — much faster) | read-only |
| `display_inventory` (monitors, work areas, DPI) | read-only |
| `wait` / `wait_for` (wait for a window, for text to appear/gone, or for the screen to settle) | read-only |
| `clipboard` (get / set) | acts on the clipboard |
| `process` (list / kill), `registry` (get / set / delete / list) | act on the system |
| `click` (left/right/middle, single/double, hover with `clicks=0`) | synthesises input |
| `type` (Unicode injection, optional click-first / clear / caret position / Enter) | synthesises input |
| `scroll`, `move` (`drag=true` for drawing) | synthesises input |
| `shortcut` (`ctrl+s`, `alt+tab`, and the media keys: `play_pause`, `volume_up`, `mute`, …) | synthesises input |
| `app` (launch by name or path, switch/focus, close, resize, move, minimize) | acts on windows |
| `multi_select` / `multi_edit` (tick a run of checkboxes; fill a whole form cell by cell) | synthesises input |
| `draw_path` (many strokes in one call) | synthesises input |

**Addressing**: `snapshot` prints `[N]` for every actionable element and paints the same number in red
on the picture, so `click(label=N)` is the normal way to aim; the coordinates are recomputed against the
current UI and a renamed control is refused instead of clicked. Raw `loc=[x, y]` is screen-physical
pixels from the latest picture and is rejected as stale once the layout changes.

**Confirmation is off by default** for all of the above (and for `send_email`,
`notify_webhook`, `send_qq_message`): the teacher asks once and it happens. Two independent
switches in 设置 → 电脑操作 bring the prompts back — `confirm_desktop_input` for mouse/keyboard/
clipboard/window actions and `confirm_messages` for outgoing mail, group-bot and QQ messages.
Turn them on when the machine is shared or when the task is unfamiliar.

Honest limits: a user must be logged into the desktop session; high-DPI scaling is handled (the process
declares per-monitor DPI awareness, and screenshots/clicks share one physical-pixel space); **clicking,
typing, scrolling and dragging are real mouse/keyboard input** — this build has no "invoke the control
without touching the pointer" channel, so a click moves the teacher's cursor. Aiming by `label` avoids
clicking the wrong control, not the pointer movement itself.

Implementation note: the base layer is `ctypes` straight onto Win32 (`webui/winapi.py`: windows, GDI
screen capture, input, clipboard, processes, registry, launching), with a self-written image layer
(`webui/winimage.py`: BGRA buffer, numbered annotations, PNG encoder) and a resident PowerShell
UIA worker (`webui/uia.py` + `webui/uia_worker.ps1`) for the element tree — it is started once and
answers over a pipe (a query is milliseconds; a fresh PowerShell process would be ~0.5 s). Everything
runs on the stock Win7 runtime: Python 3.8 stdlib, PowerShell 2.0 and .NET 3.5, nothing installed.
(`webui/uia_worker.ps1` must stay pure ASCII — PowerShell 5.1 reads a BOM-less file as ANSI and would
feed mojibake to the C# compiler.)

## Messages: email, group webhooks, QQ

| Tool | Channel | Reliability |
| --- | --- | --- |
| `send_email` | SMTP (QQ Mail, 163, school mail), attachments supported | high |
| `notify_webhook` | WeCom / DingTalk / Feishu group bot | high |
| `send_qq_message` | the QQ client on this machine | medium — UI automation, screenshots itself afterwards |

QQ Mail requires the 16-digit **authorisation code** (设置 → 账户 → POP3/SMTP 服务), never the login
password. There is deliberately **no QQ bot**: a real one needs a registered bot account plus an
always-on network service, which cannot be installed or maintained on a teacher's Win7 machine. The
credentials live in `config\webui.json` and are masked in the UI, and "leave blank" means "keep".
Outgoing messages are sent directly by default; enable `confirm_messages` to review each one first.

## Network hiccups: TLS / bad record mac

`[SSL: SSLV3_ALERT_BAD_RECORD_MAC]` (and `UNEXPECTED_EOF_WHILE_READING`) mean the HTTPS stream was
corrupted in transit — almost always antivirus HTTPS scanning, a corporate proxy, or a flaky link —
and it is unrelated to the API key or balance. The client now absorbs it: a failed request is retried
on a fresh connection, and the second retry pins the TLS context to 1.2 (middleboxes handle 1.3 badly),
remembering that choice once it works. If content had already been streamed to the UI, the request is
*not* replayed (that would duplicate text) — the error says so and the 继续生成 button continues from
where it stopped. Requests that got a real HTTP answer (401/400/…) are never retried.

## Subagents, circuit breaker and web search

- **Subagents**: `run_subagent(task, role, max_rounds)` hands a self-contained subtask to a child agent
  with its own context and a restricted tool set (it cannot spawn further subagents). Two or more
  subagents requested in the same round run in parallel; the UI shows each one as a card with its
  steps and final conclusion. `max_subagents` (default 4) bounds a single run.
- **Circuit breaker**: the run stops automatically, with a visible explanation, when the model repeats
  the same action three times in a row, when a single round's reasoning exceeds 60k characters, or
  when the run exceeds `max_run_seconds` (default 900, 0 disables). All three knobs live in 设置.
- **Web search**: `web_search` uses Bing (RSS output first, HTML fallback) and DuckDuckGo, or a
  self-hosted endpoint configured as `http://intranet/search?q={query}` (JSON or HTML both parse).
  `fetch_top: 1-3` returns excerpts of the top pages in the same call; `read_url` takes up to three
  URLs at once and falls back to a built-in fetcher when `agent.exe` is missing. Baidu and Sogou are
  anti-bot protected, so they are not bundled; the model is required to say "not found" rather than
  invent links.

## Chinese Windows 7 edge cases (handled, with tests)

- **Encodings**: UTF-8 (with/without BOM), UTF-16 with BOM, GBK and GB18030 all decode correctly; a file that
  was cut mid-character (reading only the first few MB) no longer turns the whole text into question marks.
  The same decoder is used for score CSVs.
- **File names**: writing `CON.md`, `COM1`, `第三课:函数的单调性.md` and friends fails with a plain-Chinese
  explanation instead of a raw OS error.
- **Long paths**: paths over 260 characters are opened through the `\\?\` prefix (Win7 has no long-path
  support); reads/writes/snapshots all work there.
- **Files locked by Word/WPS**: reported as "close the program that has it open", not as a parameter error.
- **Commands**: a timeout kills the whole process tree, the confirmation gate can no longer be bypassed with
  `^` escapes or `start` / `for … do` / `call` wrappers, and a failing audit log is reported instead of
  silently swallowing the record.
- **One run per conversation**: a second window gets a clear "this conversation is generating" message
  instead of overwriting the history.
- **Sleeping/closing the browser**: the run cancels itself (30 s write timeout, configurable via
  `sse_write_timeout`) instead of blocking forever.
- **LAN access**: binding to `0.0.0.0` auto-generates an access token and prints a URL carrying it; requests
  without the token are rejected.

## Picking files

The file picker is meant to be used without typing paths: a **breadcrumb** at the top (`C:\ › Users ›
Teacher › Desktop › Lesson 3`, every segment clickable), quick-jump chips for Desktop / Documents /
Downloads / Pictures / workspace plus every drive letter, then a toolbar with 上一级 / 刷新 / type filters
(全部 · 文档 · 图片) / a live name filter / 从电脑选择文件 (uploads through the browser and puts the copy in the
workspace). In the list: single click selects, double click enters a folder or confirms a file, **Ctrl+click
multi-selects**, and the footer shows 已选：<name> or 已选 N 个. Keyboard: ↑/↓ move, Enter opens or confirms,
Backspace goes up. Small images get thumbnails under the 只看图片 filter, and a filtered-away folder says how
many entries were hidden instead of looking empty.

## Layout and workspace navigation

Only the conversation pane scrolls: the top bar, sidebar and inspector stay fixed, and a 最新 button
appears when you scroll up in a long thread.

The sidebar follows the DeepSeek Harness shape, top to bottom: brand row (name + version + collapse),
新会话, 技能中心, a 工作区 · <current folder> header whose right side holds three icons (search conversations /
show only this workspace / **new workspace**), then **conversations grouped by workspace**, then snapshots and
settings at the bottom.

**新建工作区 actually creates a folder.** Click the folder-plus icon in the 工作区 header to open the
**工作区 dialog**: the top half lists every registered workspace (name, full path, conversation count, the
current one badged 当前 — **click a row to switch to it**, the icon at the end opens it in Explorer); the bottom
half creates one — type a name (that is the folder name), the location defaults to `<install dir>\workspaces`
(换个位置… picks somewhere else) and a **live preview shows the exact folder that will be created**. Illegal
Windows characters are called out as they are stripped, the button is disabled while the name is invalid, a
same-named workspace elsewhere is pointed out, and a folder that is already a workspace says so. 新建并进入
creates the folder, registers it, switches to it and opens a fresh conversation. 添加已有文件夹… adopts an
existing folder (the picker's confirm button reads 用这个文件夹 in folder mode).

Every workspace row carries a **+** (new conversation *in that workspace*) and a **⋯** menu (重命名工作区 /
在资源管理器中打开 / 从工作区列表移除). Clicking a row selects that workspace and expands its conversations;
clicking the current row collapses it. A conversation row shows its title and a relative time (10 分钟 / 昨天 /
03-14); hovering reveals rename and delete (always visible in touch mode). Removing a workspace only drops the
registration — the folder and its sessions stay, and those conversations move under 未分组. Workspaces you
created or added keep a row even with no conversations; a folder that was never registered does not, and the
current one is always named in the 工作区 header. Same-name folders are disambiguated with their parent folder
(`高一\课件`), and different spellings of one directory are treated as a single workspace.

## Workspace, file picker and skills

- **Workspace**: the agent writes new files into `<install dir>\workspace` unless you name another
  location, and relative paths resolve there. The 工作区 button in the toolbar lets you switch the
  folder with a graphical picker, open it in Explorer, and open generated files with one click
  (executables such as `.exe/.bat/.ps1` are refused).
- **File picker**: the 文件 and 图片 buttons open an in-app browser with drive buttons and
  桌面/文档/下载/图片/工作区 shortcuts, a search box, and a "choose file from this computer" upload,
  so no path has to be typed.
- **Skills**: the 技能 button lists every `skills\<name>\SKILL.md`, lets you tick the ones to attach to
  this session, edit or delete them, and author new ones in the browser. Uningested skills are still
  matched automatically from your wording; declare `keywords:` in the front matter for precise matching.

## Image input (vision)

`deepseek-flash` accepts images. Paste a screenshot into the composer with `Ctrl+V`, pick a photo in
the file picker (盘符 / 桌面 / 文档 / 工作区), or upload one with the system file dialog (up to 8MB,
png/jpg/jpeg/gif/webp/bmp, 6 per message). The model can also open images it discovers itself by
calling the `view_image` tool, which injects the file into the next request so it really sees the picture.

Typical classroom uses: paste a photo of a student worksheet and ask for grading plus a 讲评 courseware;
paste a textbook page and ask for a matching quiz; send a scores screenshot and ask for an `.xlsx`
breakdown. Images are billed as image tokens, so the history keeps only the two most recent
image-bearing turns. Non-vision models such as `deepseek-v4-pro` answer "cannot view this image";
the Web UI detects that reply and suggests switching to `deepseek-flash`.

File tools read and write any absolute path by default (uncheck "允许读写工作区之外的路径" in the
settings dialog to restrict them to the workspace). The settings entry is the gear button at the
right end of the top toolbar, or the 设置 button at the bottom of the sidebar.

Use Chrome/Edge 109+, Firefox 115 ESR, or a Chromium dual-core browser (360, QQ, Sogou) in
极速模式. IE and the IE-based 兼容模式 cannot stream responses; the page detects that case and
tells the user how to switch.

Web UI settings are stored in `config\webui.json`; the model, API key, and command policy stay in the
shared `config\agent.json`. See [webui/README-zh.md](../webui/README-zh.md) for the full manual.

## VS Code Extension Quickstart

Use this when the Win7 office machine runs VS Code 1.70.x:

1. Install `win7-agent-vscode-0.4.4.vsix` with `Extensions -> ... -> Install from VSIX...`.
2. Open VS Code Settings JSON.
3. Configure:

```json
{
  "win7Agent.baseUrl": "http://你的内网地址/v1/chat/completions",
  "win7Agent.model": "你的模型名称",
  "win7Agent.apiKey": ""
}
```

4. Run `Study7Agent: Open Chat` from the command palette.

The VSIX includes the same office skills and can reuse the bundled `agent.exe` for `docx/xlsx` document features.

Useful VS Code chat references:

```text
@file:src/app.js       引用当前项目中的文件
@skill:report          引用某个 skill
```

In chat, click `Project` to load the current workspace into context. Sending any task now starts continuous work by default, without a fixed turn limit. Press `Esc` twice to abort the current model request or command immediately. File edits made by the agent save rollback snapshots, and `Win7 Agent: Rollback Last File Change` restores the previous state.

Sessions are isolated. Use `New`, `Sessions`, `Rename`, and `Delete` in the chat toolbar, or the matching command palette commands, to manage historical conversations. Opening or switching a session restores its visible chat history.
