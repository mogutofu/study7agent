# Study7Agent

This repository builds three Windows 7 x64 deliverables:

- Full EXE edition: Go 1.20 source for a Windows 7 x64 `agent.exe`.
- Web UI edition: an offline browser console served by the bundled Python 3.8.10 (`webui\server.py`).
- VS Code extension edition: offline `.vsix` for Windows 7 VS Code 1.70.x, bundled with `agent.exe` for full document support.

中文用户请先阅读：[中文操作手册](docs/user-manual-zh.md)。当前版本 **0.5.0**。

## Packaging the release (1.1.0 and later: one single-file exe)

Double-click distribution is now **one exe** — no installer, no dependencies, nothing to unzip:

```bat
python\python.exe scripts\make-release.py
```

It writes two artifacts, and the two exes are **byte-identical** — they differ only by a side-car
config file, so the key never ends up inside a binary:

| File | Audience | Key inside? |
| --- | --- | --- |
| `dist\study7agent-<version>.exe` | public distribution | no `sk-` string at all |
| `dist\study7agent-<version>-deepseek.zip` | one designated machine | only in `config\agent.json` |

The exe is assembled by `scripts\make-exe.py`: the runtime tree (`python\`, `webui\`, `skills\`,
`docs\`, bundled Chromium 109) is tar.gz-ed and appended to the launcher exe. On first run the
launcher unpacks it next to itself or under `%ProgramData%\Study7Agent` (**never inside the user
profile** — the bundled kernel cannot start there, see `docs\embedded-browser.md` §6); later runs
reuse it and older unpacked versions are removed automatically.

Which files ship is still decided in one place, `scripts\make-package.py`: it never includes
`config\agent.json` (your API key), `config\webui.json`, `webui\data\` (conversation history),
`logs\`, or the `study7agent-src` source mirror, and it does include `config\agent.example.json`.
That script can still build a directory-edition zip, which is now a development artifact (used by
`vm-acceptance.ps1 -Full`) rather than something published.

`make-release.py` scans both artifacts before reporting success: the exe by raw bytes, the zip
member by member.

To hand someone a machine that works without typing a key, ship the keyed zip:

```bat
python\python.exe scripts\make-release.py --api-key sk-xxxxxxxx
```

That produces `dist\study7agent-<version>-deepseek.zip`; its only difference is a generated
`config\agent.json` holding the key (top level and in every model profile), built from the example file so
no other local setting leaks, and your own `config\agent.json` is left alone. Anyone who receives that zip
can spend your quota — keep it off group chats, public shares and file hosts, and rotate the key at
`platform.deepseek.com` if it leaks. Use the default build for distribution.

## Full EXE Edition

Build on a machine with Go 1.20:

```bat
scripts\build-win7-exe.cmd
```

The EXE edition supports:

- OpenAI-compatible `POST /v1/chat/completions`
- Optional streaming
- local `SKILL.md` loading
- `txt/md/csv/json/log/docx/xlsx` reading
- `txt/md/csv/docx/xlsx` creation as new files
- static intranet web page reading with cleaner article/table extraction
- model profile switching
- persistent local conversation/context memory
- command execution enabled by default, with high-risk prefixes requiring confirmation
- multi-step command tool calls that feed results back to the model, with a configurable round limit

The full EXE edition (sometimes called the single-file edition because it is distributed as one `agent.exe`) is actively maintained. Contributions for either edition are welcome; open pull requests against `main`.

## Web UI Edition

A modern local console for the same agent, with no installation and no build step:

```bat
start-webui.cmd
```

It runs on the Python 3.8.10 runtime shipped in the release package and reuses `agent.exe`, so it
works on a fully offline intranet machine. The console prints the model, endpoint, workspace, and
URL, then opens the browser (default `http://127.0.0.1:8770`).

### Hardened for real Chinese Windows 7 machines

Audited for the things that actually break on a school PC:

- **Text encodings**: BOM-aware decoding (UTF-8/UTF-16) plus GBK/GB18030, with a tolerant fallback that keeps
  Chinese readable when a file was truncated mid-character (GB18030 happily decodes half a UTF-8 character,
  which used to turn a whole page into `?`).
- **Windows path rules**: `CON`/`COM1`/`LPT9`-style names and `:*?"<>|` are rejected with a readable message;
  paths over 260 characters are opened through the `\\?\` prefix; `%SystemRoot%` (except `Temp`),
  `Program Files` and the bundled `python` folder are refused as write targets (prompt-injection insurance —
  reads are unaffected).
- **Locked files** (Word/WPS has the document open) are reported as such instead of as a parameter error.
- **Command safety**: the confirmation gate survives `^` escapes and `start` / `for … do` / `call` wrappers,
  timeouts kill the whole process tree, and an unwritable audit log is surfaced rather than swallowed.
- **Concurrency**: one run per conversation (a second window gets a clear 409), `sse_write_timeout` cancels a
  run whose client stopped reading, and the SSE emit lock can no longer block forever on a dead socket.
- **LAN mode**: binding to a non-loopback host auto-generates an access token, prints a URL containing it and
  the UI carries the token on every request (before this, LAN mode could not reach its own API at all).
- **Win7 runtime**: the launcher verifies the bundled Python can start and explains the missing
  Universal C Runtime (VC++ 2015-2022 redist / KB2999226) instead of flashing a window.

It supports:

- **dsh-turn-fold style step bar**: a turn's reasoning and its consecutive tool calls merge into one
  collapsible bar — auto-expanded with a spinner while running, auto-collapsed to 思考过程 when done,
  and left alone once the user toggles it; the final answer always renders outside the bar
- **one continuous message per question**: tool calls never clear or split the answer text, and the bar
  only finishes when the whole turn is really over
- **auto-continue and a one-click 继续生成**: a truncated (length cap) or empty reply is continued
  automatically up to 3 times with an inline note; if the run still stops early (circuit breaker,
  tool-round limit, repeated truncation, Esc) a continue button appears under the message and the flag
  is stored on the session, so it survives a page refresh and clears on your next question
- **both reasoning sources**: native `reasoning_content`, or `<think>` / `<thinking>` / `<reasoning>` /
  `<thought>` tags parsed streaming-safe on the server (tags split across chunks, unclosed tags, stray
  closing tags and literal `<` in prose are all handled; tags never leak into the answer or the API history)
- **reasoning effort picker (DSH-style)**: 默认 / 最省 / 低 / 中 / 高 next to the model picker, remembered per
  session, with a default for new sessions in the settings dialog; subagents inherit it. The value is sent as
  `reasoning_effort` — and if a gateway rejects the parameter (400), the run drops it, retries once and says so
  inline instead of failing. The run inspector always shows the level actually used.
- a launcher icon: `agent.ico` (7 sizes — 16/24/32/48/64/128 as uncompressed 32bpp DIB and 256 as PNG,
  every size drawn separately at 4x supersampling; `scripts/make-icon.py` regenerates it with the
  standard library only) plus an automatic desktop shortcut and folder icon on first run

- a **reasoning key-points panel**: the right-hand inspector shows short 思考要点 extracted from the chain of
  thought (marker-aware, deduplicated, capped) instead of dumping the whole reasoning stream
- **API key management inside the UI**: the settings dialog writes the key back to `config\agent.json`
  (blank = keep, with an explicit clear button), supports a non-`Bearer` auth header for corporate
  gateways, and a **test-connection** button that performs a real `GET /models` and turns 401/402/429/404
  into a plain-Chinese explanation of what is actually wrong
- **media diagnosis for courseware** (`inspect_media`, `diagnose_pptx`, `extract_media`, `replace_media`):
  finds why "the video in my slides will not play" — incompatible codec (HEVC/AV1/Xvid), a truncated file
  with no `moov` index, externally linked media that breaks when the folder changes, missing media parts,
  embedded fonts — and can swap a transcoded clip back in place. Header-only parsing: no ffmpeg, no network
- **computer control** (`snapshot`, `screenshot`, `display_inventory`, `wait`, `wait_for`, `clipboard`,
  `process`, `registry`, `click`, `type`, `scroll`, `move`, `shortcut`, `app`, `multi_select`, `multi_edit`,
  `draw_path`): the model can look at the screen (the annotated screenshot is injected into the next round,
  so it really sees it), find and focus windows such as 希沃白板, draw by dragging the mouse, type Chinese
  by Unicode injection (no IME switching, clipboard untouched), and control playback and volume through
  media keys. DPI-aware and multi-monitor aware. **No confirmation prompts by default** — ask once and it
  is done; two independent switches (`confirm_desktop_input`, `confirm_messages`) bring them back
- **UI automation by snapshot number, not by pixel** (`snapshot` → `label`): a snapshot returns the picture
  with numbered red boxes *and* the element list, where `[N]` on screen is exactly `label=N` in the tool
  call, together with control type, name, centre coordinates, the current value and what the element
  supports (`fill`/`toggle`/`select`/`slide`/`scroll`/`click`). `click(label=N)`, `type(label=N, …)` and
  `scroll(label=N)` re-resolve the coordinates **against the live UI** before acting and refuse when the
  element no longer matches, so a stale snapshot cannot silently click the wrong thing. Honest caveat:
  this version drives **real** mouse and keyboard events (`SetCursorPos` + `mouse_event` / `keybd_event`)
  — there is no "operate without touching the pointer" path any more, so the system prompt tells the model
  to prefer shell/file/clipboard/`app` routes and to say so before it takes over the mouse. Base layers:
  `winapi.py` (ctypes straight to user32/gdi32, GDI `BitBlt` capture), `winimage.py` (BGRA buffer,
  numbered annotation, hand-rolled PNG), `uia.py` + `uia_worker.ps1` (one resident PowerShell process that
  answers UIA queries over a line pipe — a fresh process costs ~544 ms, the resident one answers in
  fractions of a millisecond), `desktop.py` (the action/query surface). The `.ps1` must stay pure ASCII:
  PowerShell 5.1 reads a BOM-less file as ANSI, and Chinese text breaks `Add-Type`
- **messages** (`send_email`, `notify_webhook`, `send_qq_message`): QQ Mail/school SMTP with attachments,
  WeCom/DingTalk/Feishu group bots, or the local QQ client via UI automation that screenshots itself.
  Deliberately no fake "QQ bot API"; credentials are stored only in `config\webui.json` and never returned
  to the browser unmasked
- **transient TLS/network errors are absorbed**: `SSLV3_ALERT_BAD_RECORD_MAC` and friends retry on a fresh
  connection, then with the TLS context pinned to 1.2 (antivirus HTTPS scanning and proxies handle 1.3
  badly), and the working choice is remembered. A request is never replayed after text has already been
  streamed — the answer continues via 继续生成 instead of duplicating output
- streaming chat with Markdown, tables, code highlighting, and per-message copy / regenerate
- a collapsible `reasoning_content` panel that is never sent back to the API
- native function calling (`list_dir`, `read_file`, `write_file`, `run_command`, `read_url`) with per-tool cards and colored diffs
- high-risk command confirmation dialogs inside the UI, plus the same blocked-prefix policy and audit log as the CLI
- `txt/md/csv/json/log/docx/xlsx` read and `docx/xlsx` write handled in-process (no Office required)
- automatic snapshots before every overwrite, with one-click rollback
- a **classroom toolkit** for teaching on the panel: `pick_students` (draws student numbers 1-50 with
  no repeats within a round — no roster file needed — plus random grouping), `class_timer` (on-screen
  countdown with an end chime), and `show_on_screen` (full-screen large-type board for questions,
  answers and key points)
- a **mistake notebook**: accumulate wrong answers by topic (`mistake_book`), see which topics are
  weakest, and generate a targeted re-practice `.docx` with answers and error hints on a second page
- **deterministic score statistics** (`score_stats`): mean, median, standard deviation, pass and
  excellent rates, score bands and an optional `.xlsx` summary — computed in code instead of by hand
- bundled teaching skills: courseware, lesson-plan, quiz, grade-analysis, classroom, flashcards,
  tiered-homework
- **subagents**: the model can delegate self-contained subtasks through `run_subagent`; several
  subagents spawned in one round run in parallel, each with its own context, a restricted tool set
  (no nesting), a round budget, and a nested progress card in the UI
- **loop circuit breaker**: repeated identical actions, runaway single-round reasoning, or a
  whole-run timeout stop the run with an explanation instead of burning tokens
- **web search**: `web_search` (Bing RSS-first with HTML fallback, DuckDuckGo, or a self-hosted
  intranet endpoint with a `{query}` template), optional `fetch_top` to pull result excerpts in one
  call, multi-URL `read_url`, and a built-in fetch fallback when `agent.exe` is absent
- **fixed shell layout**: only the conversation pane scrolls; the top bar and sidebars stay put
- **DSH-shaped sidebar with workspace registry**: brand row, 新会话 / 技能中心, a 工作区 · <current folder>
  header with search / filter / **new-workspace** icons, and conversations grouped by workspace — 新建工作区
  creates a real folder, registers it and opens a conversation inside it; every row has a **+** for a new
  conversation in that workspace and a **⋯** menu (rename / open folder / remove from list, sessions fall back
  to 未分组); rows show title plus a relative time, and different spellings of one directory collapse into a
  single group (same-name folders get a parent-folder prefix)
- a graphical file picker (drives, Desktop/Documents/Downloads/Pictures/workspace shortcuts, search,
  and a native "choose file from this computer" upload) so nobody has to type a Windows path
- a settable **workspace**: new files the agent creates land there by default, and relative paths
  resolve inside it; the toolbar can change it, open it in Explorer, and open generated files
- a **skills manager** in the UI: enable skills per session, view/edit/delete them, or author new ones
  (`skills\<name>\SKILL.md` with `keywords:` for precise auto-matching)
- image input for vision-capable models (`deepseek-flash`): paste a screenshot with Ctrl+V, pick a
  photo in the file picker, or let the model open local images through the `view_image` tool
- `pptx` courseware generation with a plain-text outline (`#` = slide, `-` = bullets, `|` = table,
  `>` = speaker notes), optional school-template inheritance, and a companion 讲稿 Markdown file
- `docx` with heading levels, real tables, lists, and inline bold; `xlsx` with a bold frozen header row
- an OOXML package self-check that catches missing parts and mismatched relationship IDs
- one-click file snapshots with rollback, and a touch mode for classroom all-in-one panels
- bundled teaching skills: courseware, lesson-plan, quiz, grade-analysis
- multiple isolated sessions with search, rename, delete, and Markdown export
- DeepSeek context-cache statistics (`prompt_cache_hit_tokens`) and a configurable cost estimate
- file tools that read and write any absolute path by default (restrictable to the workspace in settings), loopback-only binding, and masked API keys
- inline SVG icons instead of emoji, because Windows 7 has no emoji font
- dual-core browser support: `renderer=webkit` meta hints plus an in-page engine check that tells the
  user to switch 360 / QQ / Sogou browsers to 极速模式 when the page lands in the IE compatibility mode
- `base_url` accepted as a bare host, `/v1`, or a full `/chat/completions` URL, with automatic
  `/v1/chat/completions` fallback on 404/405 and the effective request URL shown in settings

The implementation is Python 3.8 standard library only (`webui\server.py`, `webui\core.py`,
`webui\tools.py`, `webui\media.py`, `webui\desktop.py`, `webui\notify.py`, and `webui\ui\`), with an
end-to-end self-check that needs no API key:

```bat
python\python.exe webui\test_webui.py
```

Screen capture, window handling, clipboard access, media keys and mouse/keyboard synthesis use the
PowerShell 2.0 + .NET 3.5 that Windows 7 already ships with, driven from Python with `-EncodedCommand`
scripts and `CREATE_NO_WINDOW`. No extra runtime is installed, and the generated scripts are
syntax-checked by PowerShell's own parser in the test suite (the tests never touch your mouse).

See [webui/README-zh.md](webui/README-zh.md) for the full manual.

## Configuration

Copy `config\agent.example.json` to `config\agent.json`, then edit:

- `base_url`
- `model`
- `api_key` if required
- optional `headers` or `auth_profiles` for intranet pages
- optional `models` profiles for `/model <name>` switching

Do not rename, encrypt, or disguise EXE files to bypass email controls. Use IT review, signing, or internal software distribution.

## VS Code Extension Edition

Windows 7 can only run the legacy VS Code 1.70.x line. The extension is therefore plain CommonJS JavaScript with no npm runtime dependencies and `engines.vscode` set to `^1.70.0`.

Package the VSIX after the EXE has been built:

```sh
node scripts/package-vscode-extension.js
```

The output is:

```text
dist/win7-agent-vscode-0.4.4.vsix
```

The VSIX includes:

- VS Code Webview chat
- continuous project work mode
- model switching
- bundled and workspace `SKILL.md` loading
- persistent memory
- workspace/project context, referenced files, text file and intranet URL context
- project file create/write/replace/delete with rollback snapshots
- command execution with high-risk confirmation
- office document drafting
- bundled `agent.exe` reuse for `docx/xlsx` read/write features
