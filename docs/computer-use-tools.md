# computer use 工具面（新）—— 实现契约

这一版把「桌面控制」的底座换成了 ctypes 直连 Win32（`webui/winapi.py`）、自写图像层
（`webui/winimage.py`）、常驻 UIA 工作进程（`webui/uia.py` + `webui/uia_worker.ps1`），
工具名与形状照 Windows-MCP（https://github.com/CursorTouch/Windows-MCP）重写。
本文是 tools.py / core.py / app.js / test_webui.py 四个文件的共同契约，改之前先读它。

行号是写这份文档时的位置，改动后就当「附近」用。

## 1. 工具清单（18 个，旧名字全部删除）

| 新工具 | 替掉的旧工具 | 读/写 |
|---|---|---|
| `snapshot` | `ui_snapshot`（+ `ui_find`/`ui_read` 的能力） | 读 |
| `screenshot` | `screen_capture` | 读 |
| `display_inventory` | 无（新） | 读 |
| `wait` | 无（新） | 读 |
| `wait_for` | `ui_wait` | 读 |
| `clipboard` | `clipboard` | 读/写 |
| `process` | 无（新） | 读/写 |
| `registry` | 无（新） | 读/写 |
| `click` | `mouse_action`(click/double/right/middle) | 写 |
| `type` | `type_text` | 写 |
| `scroll` | `mouse_action`(wheel) | 写 |
| `move` | `mouse_action`(move/drag) | 写 |
| `shortcut` | `send_keys` + `media_key` | 写 |
| `app` | `window_action` + 启动程序 | 写 |
| `multi_select` | 无（新） | 写 |
| `multi_edit` | 无（新） | 写 |
| `draw_path` | `draw_path`（保留） | 写 |
| `send_qq_message` | `send_qq_message`（保留，走 `desktop.send_qq_message`） | 写 |

删除：`screen_capture`、`list_windows`、`window_action`、`ui_snapshot`、`ui_act`、`ui_find`、
`ui_read`、`ui_wait`、`mouse_action`、`type_text`、`send_keys`、`media_key`。
（`desktop.list_windows()` 作为**模块函数**保留，`_desktop_layout()` / `_close_check()` 还在用它。）

## 2. 每个工具的 parameters（写进 TOOL_SPECS）

描述文字风格照旧：称呼「你」、说清什么时候用、给坑。下面只定**名字/类型/默认值/必填**，
文字自己按同样口吻写。

### snapshot（读）
- `window` string——标题关键字 / `pid:1234` / `handle:12345`；省略=整个桌面
- `find` string——只列命中这个关键字的元素（替代 ui_find）
- `use_vision` boolean 默认 true——false = 不截图，只要元素清单
- `use_annotation` boolean 默认 true——截图时画红框和编号
- `use_ui_tree` boolean 默认 true——false = 只截图
- `read_text` boolean 默认 false——额外附上窗口里的文字（文档正文很慢，默认关）
- `max_elements` integer 默认 400；`max_depth` integer 默认 14
- `region` array[4] integer——`[x, y, w, h]` 屏幕物理像素（只想看一块区域时）
- `display` integer——第几块屏（**1 基**：`1` 是第一块，省略=所有屏拼一张；编号同 display_inventory）
- `path` string——截图存哪儿，留空 = 工作区 `截图/界面-<时间戳>.png`
- `scale` number 默认 1.0——屏幕大/图超过 8MB 时用 0.5

### screenshot（读）
- `path` string、`display` integer、`region` array[4]、`window` string、`scale` number 默认 1.0、
  `cursor` boolean 默认 false（是否画个红圈标出鼠标位置）

### display_inventory（读）
无参数。

### wait（读）
- `seconds` number 默认 1.0（最多 30）

### wait_for（读）
- `condition` string **必填**，enum `["window","gone","text","stable"]`
- `text` string——condition=window/gone 时是标题关键字，condition=text 时是要出现的文字
- `window` string——只在这个窗口里找（可省）
- `timeout` number 默认 10.0；`interval` number 默认 0.25

### clipboard（读/写）
- `action` string **必填**，enum `["get","set"]`（旧的 read/write 也接受，映射过来）
- `text` string——action=set 时必填

### process（读/写）
- `action` string 默认 `list`，enum `["list","kill"]`
- `name` string（按进程名过滤 / kill 时按名字找）、`pid` integer
- `sort_by` string 默认 `memory`（`memory`/`name`/`pid`）、`limit` integer 默认 20
- `force` boolean 默认 false——kill 时是否强杀

### registry（读/写）
- `action` string **必填**，enum `["get","set","delete","list"]`
- `path` string **必填**——`HKCU\Software\...`（HKCR/HKCU/HKLM/HKU 前缀）
- `name` string——值名，省略=默认值
- `value` string——action=set 时必填
- `type` string 默认 `string`，enum `["string","expand","dword","qword","multi"]`

### click（写）
- `loc` array[2] integer——屏幕物理像素 `[x, y]`
- `label` integer——`snapshot` 元素清单里方括号里的编号
- `button` string 默认 `left`，enum `["left","right","middle"]`
- `clicks` integer 默认 1；**0 = 只把鼠标移过去（hover）**，2 = 双击
- `loc` 和 `label` 至少给一个

### type（写）
- `text` string **必填**
- `loc` array[2]、`label` integer——给了就先点一下那里再输入
- `clear` boolean 默认 false——先把输入框里原来的内容全选删掉
- `caret_position` string 默认 `idle`，enum `["start","idle","end"]`——点进去之后光标放哪
- `press_enter` boolean 默认 false

### scroll（写）
- `loc` array[2]、`label` integer
- `type` string 默认 `vertical`，enum `["vertical","horizontal"]`
- `direction` string 默认 `down`，enum `["down","up","left","right"]`
- `wheel_times` integer 默认 1（一次一格 ≈ 三行）

### move（写）
- `loc` array[2]、`label` integer
- `drag` boolean 默认 false——true 时从 `from_loc` 拖到 `loc`
- `from_loc` array[2]——drag 的起点
- `duration` number——拖动用几秒，默认自动

### shortcut（写）
- `shortcut` string **必填**——`ctrl+s`、`alt+tab`、`enter`、`f5`、`ctrl+shift+esc`；
  **多媒体键也走这里**：`play_pause`、`next`、`prev`、`stop`、`volume_up`、`volume_down`、`mute`
  （旧的 `media_key` 工具并进它了，一次一个键、要按几次就调几次）

### app（写）
- `mode` string **必填**，enum
  `["launch","launch_executable","switch","info","resize","move","close","minimize","maximize","restore","hide","show"]`
- `name` string——`launch` 用显示名（开始菜单里的名字）/ 其它 mode 用窗口标题关键字或 `pid:1234`
- `executable` string——`launch_executable` 用完整路径；`args` string；`cwd` string
- `loc` array[2] + `size` array[2]——`resize`/`move` 用
- `window` string——和 `name` 同义（谁给了用谁）

### multi_select（写）
- `labels` array of integer、`locs` array of array[2]、`press_ctrl` boolean 默认 true（多选）

### multi_edit（写）
- `labels` array of array——`[[label, "文字"], ...]`；`locs` array of array——`[[x, y, "文字"], ...]`

### draw_path（写）
- `strokes` 必填（`[[[x,y],...], ...]`）、`duration` number 默认 0.45、`button` enum `["left","right"]`

### send_qq_message（写）
- `text` 必填、`contact` string、`send_shortcut` enum `["enter","ctrl+enter"]`

## 3. 门控（tools.py 顶部集合 + authorize）

```python
DESKTOP_TOOLS = {"snapshot", "screenshot", "display_inventory", "wait", "wait_for",
                 "clipboard", "process", "registry",
                 "click", "type", "scroll", "move", "shortcut", "app",
                 "multi_select", "multi_edit", "draw_path"}
# 会真的改别人窗口/系统状态的：
DESKTOP_INPUT_TOOLS = {"clipboard", "process", "registry",
                       "click", "type", "scroll", "move", "shortcut", "app",
                       "multi_select", "multi_edit", "draw_path"}
```

`authorize()`（tools.py:1696-1721 附近）里的免确认快路：
- `clipboard` + action in ("", "get", "read") → 直接放行
- `process` + action in ("", "list") → 直接放行
- `registry` + action in ("", "get", "list") → 直接放行
- `app` + mode != "close" → 直接放行
- 其余走 `confirm_desktop_input`；确认文案 labels 改成：

```python
labels = {"app": "关闭窗口", "clipboard": "改写剪贴板",
          "process": "结束进程", "registry": "改注册表",
          "click": "模拟鼠标：会真的点在屏幕上",
          "type": "模拟键盘：会把文字输入到窗口里",
          "scroll": "模拟滚轮", "move": "移动鼠标 / 拖动",
          "shortcut": "发送快捷键（多媒体键也在内）",
          "multi_select": "多选：会按住 Ctrl 连点",
          "multi_edit": "批量填表：会逐格点开再输入",
          "draw_path": "在白板/画图里画图：会真的移动鼠标"}
```

`send_qq_message` 不进 DESKTOP_TOOLS（它归 MESSAGE_TOOLS，走 `confirm_messages`）。

## 4. 处理器（tools.py `_tool_*`）的硬规则

- 一律 `module = self._desktop()`（它管 `allow_desktop_control` 开关），出错 `raise ToolError(error)`，
  返回 `(文字, meta)`。
- **截图类**（snapshot/screenshot）：路径留空 → `os.path.join(self.root, "截图", "界面-<YYYYmmdd-HHMMSS>.png")`
  经 `self.resolve(..., for_write=True)`；`meta["kind"]="image"`，`meta["path"]`，`meta["mime"]=core.image_mime(path)`，
  `meta["bytes"]`，图 ≤ `core.MAX_IMAGE_BYTES`（8MB）时 `meta["inject_image"] = path`，超了就提示用 `scale=0.5`
  重截（screenshot 还要保留旧 `screen_capture` 的自动降级重截：超 8MB 依次按 scale 0.6/0.4/0.25
  重截成更小的 **PNG**——`winimage` 只有 PNG 编码器、`desktop._image_path()` 会把后缀统一成 `.png`，
  所以没有"转 jpg"这一步；重截成功就删掉原图）。
- **坐标陈旧保护**：`_shot_layout` / `_stale_click_point(x, y)` / `_window_at` / `_desktop_layout`
  （tools.py:2875-2946）**原样保留**。snapshot/screenshot 成功后刷新 `self._shot_layout`；
  `click`（给了 loc 时）、`move`（给了 loc/from_loc 时）、`multi_select`/`multi_edit`（给了 locs 时）
  先 `stale = self._stale_click_point(x, y)`，非空就 `raise ToolError(stale)`。用 `label` 时不需要
  （desktop 会按当前界面重算坐标），但若 desktop 返回的 info 里带 `note`，要写进返回文字里。
- `snapshot` 的文字直接用 `info["text"]`（desktop 已经排版好，含元素 `[N]` 清单、窗口表、截断提示），
  前面补一行「画面：<path>（宽x高）」和一句「要动手就用 click/type/scroll 里的 label=N」；
  `find` 时 desktop 自己会换标题。元素多时把前 30 个写进 meta：
  `meta["elements"] = [{"label","kind","name","action"}]`（name 截 60 字）。
- `meta` 里别塞图片 base64，只给路径。
- `process`/`registry`/`app`/`clipboard` 的结果文字要说人话（例如 kill 成功说「已结束进程 X（pid N）」，
  列进程要说「按内存排序前 N 个」）。
- `wait(seconds)` 要 `min(30, max(0, seconds))`，别让模型睡太久。

## 5. core.py 要改的地方

1. `IDEMPOTENT_TOOLS`（core.py:2239-2243）：去掉 `screen_capture`/`list_windows`/`ui_snapshot`/`ui_find`/
   `ui_read`/`ui_wait`，换成 `snapshot`、`screenshot`、`display_inventory`、`wait_for`、`process`、`registry`、
   `list_dir` 等原有只读项照旧。`click`/`type`/`scroll`/`move`/`shortcut`/`app`/`draw_path`/`multi_*`
   一律**不**进（会改状态）。注释同步改（core.py:2237-2238）。
2. 系统提示里「操作窗口与界面」一节（core.py:279-292）改写成新工具：
   - 先 `snapshot` 拿「画面 + 元素清单（方括号里的编号就是 label）+ 窗口表」；
   - 动手用 `click`/`type`/`scroll`/`move` 的 `label`；label 每次 snapshot 重新编号，界面变了就重新 snapshot；
   - 也可以直接给 `loc=[x,y]`（按最近一次截图算的屏幕物理像素），但窗口在两次调用之间动过会被拒绝，
     那时要重新 `screenshot`，不要拿旧坐标硬点；
   - 做完动作要**再 snapshot 一次**从新状态验证（动作返回的读回只是线索）；
   - 元素太少/为空（Chromium/Qt/自绘）：如实说明，改用 `screenshot` 看坐标 + `click loc=...`；
   - 翻页用 `scroll`（先移鼠标到那块能滚的区域再滚；`wheel_times` 一格约三行），别拖滚动条；
   - 键盘用 `type`（中文按 **Unicode 逐个字符注入**，不切输入法、不占剪贴板）和 `shortcut`；
   - `wait_for` 等对话框出现，`app` 切窗口/关窗口/改大小，`clipboard` 读写剪贴板；
   - 别的程序共用同一块桌面，操作前先确认现状；取消/超时不会撤销已发出的操作。
3. 提到旧工具名的注释（如 core.py:2259 的举例）顺手改成新名。

## 6. app.js（前端显示）要改的地方

- 工具中文名表（app.js:1577-1580）：`snapshot:'看界面'`、`screenshot:'截屏'`、
  `display_inventory:'显示器'`、`wait:'等待'`、`wait_for:'等界面'`、`clipboard:'剪贴板'`、
  `process:'进程'`、`registry:'注册表'`、`click:'鼠标'`、`type:'打字'`、`scroll:'滚轮'`、
  `move:'移动/拖动'`、`shortcut:'快捷键'`、`app:'窗口/程序'`、`multi_select:'多选'`、
  `multi_edit:'批量填写'`、`draw_path:'画笔'`、`send_qq_message:'发QQ'`。
- `ARTIFACT_SKIP_TOOLS`（app.js:1782-1783）：`screen_capture`→`screenshot`、`ui_snapshot`→`snapshot`。
- 图标映射（app.js:2021-2024）与参数摘要（app.js:2049-2083）按新参数名重写：
  `snapshot`→window/find、`screenshot`→display/region、`click`→label 或 (x,y)+button、
  `type`→文字摘要、`scroll`→方向+格数、`move`→label/坐标、`shortcut`→keys、
  `app`→mode + name、`registry`→action + path、`process`→action + name、`multi_*`→个数。
  **旧的 `mouse_action`/`ui_act`/`send_keys` 等分支要删掉**，别留死代码。

## 7. test_webui.py 要改的地方（详见任务卡片）

- `TestDesktopScripts`（6267-6538）：整套 PowerShell 打桩（`run_powershell`、`ps_quote`、
  `SENDKEY_NAMES`、`_payload`）已随旧实现删除 —— 删掉这些用例，换成新 `TestDesktopTools`：
  用假的 `tools_module.desktop_module`（一个同名不同实现的替身）验证每个新工具的
  文字/meta/`inject_image`/坐标陈旧拒绝；再验证 `DESKTOP_TOOLS`/`DESKTOP_INPUT_TOOLS` 成员与
  `authorize()` 的免确认快路。
- `TestUIAutomation`（13871-14242）：旧用例喂的是假 PowerShell 输出，整类删掉，换成
  `TestUiaProtocol`：把造出来的工作进程行（`seq<US>status<US>payload`，行内 `<US>` 分字段）
  直接喂给 `uia.py` 的解析函数，验证行解析、`label` 编号（先 `i` 后 `s`、跳过离屏/零尺寸）、
  `path` 拼接与 `resolve()` 的名字比对。
- 钉在旧工具名上的散落断言：test_webui.py:1233、1271、5084、5114-5120、5165-5175、5854-5859、
  7163-7165、8522、8537-8561、11351、11744-11748、12019、12056-12078、12241、12563、12586、
  14797-14815 —— 全部改成新工具名/新 prompt 文字。`8522`/`8537-8561` 那两个「点之前先看一眼
  截图还算不算数」的用例**必须保留同等覆盖**（改成 `click` 工具 + `_stale_click_point`）。
- `TestDesktopLive`（约 6645-6692）：改成新模块的真机只读用例（`available`/`snapshot`/
  `screenshot`/`clipboard`），仍受 `W7A_NO_INPUT` 保护。
- 顶部注释 test_webui.py:44 的旧工具名同步改。

## 8. 不许动的约定

- `desktop.input_blocked()` / `NO_INPUT_MESSAGE` / `set_cancel` / `capture` / `send_qq_message`
  的外部形状不变（`core.py:2467`、`core.py:2904`、`server.py:1992`、`tools.py:2997` 在用）。
- 工具调用仍然走 `getattr(self, "_tool_" + name)`（tools.py:1745），所以**函数名就是工具名**。
- 不给模型看到 base64；截图一律走文件 + `meta["inject_image"]`。
