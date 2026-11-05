# 给接手这个项目的 agent 的固定约定

> 这份文件**不随包发布**（不在 `scripts\make-package.py` 的 `INCLUDE_FILES` 里），
> 但它会同步到 `study7agent-src` 镜像，所以**里面不许出现任何密钥**。
> 详细的项目交接说明在 `README.md`，这里只放"每次做完活都要照做"的固定动作。

> **接手第一件事**：读 `docs\roadmap-1.1.0.md`（1.1.0 阶段地图：M0–M9 各是什么、做到哪了、
> 下一步第一件事、验收命令）。那份文件每完成一个阶段就更新，是防"上下文耗尽后不记得 M 几"用的。

---

## 1. 每次完成任务后：`dist\` 里必须同时有干净 exe 和带密钥的 zip

```bat
.\python\python.exe scripts\make-release.py
```

产物（版本号取自 `webui\core.py` 的 `VERSION`）：

| 文件 | 给谁 | 里面有密钥吗 |
| --- | --- | --- |
| `dist\study7agent-<版本>.exe` | 对外分发（群、网盘、共享盘）**就这一个文件** | **没有**，一个 `sk-` 串都没有 |
| `dist\study7agent-<版本>-deepseek.zip` | **只给指定的那台机器** | 有，且**只有一处**：`config\agent.json` |

1.1.0 起老师侧只有**一个单文件 exe**（1.0 那套"解压出 python\、webui\、再双击 cmd"的目录版不再发布）。
exe 由 `scripts\make-exe.py` 打（程序树打成 tar.gz 追加在 exe 尾部，启动器 `launcher\Payload.cs`
负责解包）；"发哪些文件"仍然复用 `scripts\make-package.py` 的规则表，不重复实现。

**两个变体的 exe 字节完全相同**：带密钥那台只是多了一个 `config\agent.json` 放在 exe 旁边。
所以密钥永远不进二进制——好审计、好换。`config\agent.json` 的位置在 exe 所在文件夹下，
而**不是**解包出来的运行环境里（那个目录升级时会被清掉）。

**验收（每次打完都跑一遍，两条命令）**：

```bat
:: 1) 干净 exe 里不能有任何 sk- 串（exe 不是 zip，只能按字节扫）
.\python\python.exe -c "import re;p=re.compile(rb'sk-[A-Za-z0-9]{16,}');print(p.findall(open(r'dist\study7agent-1.1.0-alpha6.exe','rb').read()))"

:: 2) 带密钥包里密钥只能出现在 config/agent.json
.\python\python.exe -c "import zipfile,re;p=re.compile(rb'sk-[A-Za-z0-9]{16,}');z=zipfile.ZipFile(r'dist\study7agent-1.1.0-alpha6-deepseek.zip');print([n for n in z.namelist() if not n.endswith('/') and p.search(z.read(n))])"
```

（版本号换成当前版本。两条命令的期望结果：第一条 `[]`，第二条 `['config/agent.json']`。
`make-release.py` 自己也会把这两遍扫一遍并在不合规时直接失败，这几条命令是复核。）

---

## 2. 密钥放在哪

- 发布用的密钥写在 **`config\release-key.txt`**（第一行；`#` 开头的行会被跳过）。
- `config\` **整个目录**都不在打包范围里（`INCLUDE_DIRS` 只有 `webui / skills / docs / python`，
  里面只额外点名了 `config\agent.example.json`），也不在工作树 → 镜像的同步清单里。
  所以它永远不会进 zip，也不会被推到 GitHub。
- 也可以临时用 `--api-key sk-xxx` 或环境变量 `W7A_RELEASE_KEY` 覆盖，但**不要**把密钥写进
  任何会被打包的文件（`README.md`、`docs\`、`skills\`、`webui\`、以及在 `INCLUDE_FILES`
  里被点名的那些 `scripts\` 文件）。
- `make-package.py` 打包时会扫 `sk-[A-Za-z0-9]{16,}`，命中就直接**打包失败**并打印文件名。
  这是故意的兜底，看到这个报错说明密钥漏进了会发布的地方，去把它挪到 `config\` 下面。

---

## 3. 打完包之后的固定动作

1. **发版前刷新随包节假日表**：`.\python\python.exe scripts\refresh-holidays.py`
   （把今年 ±1 年的放假安排烤进 `webui\holidays-bundled.json`；拿不到就保持原样。
   老师那台机器可能整年连不出去，而"明年放不放假"是天天要看的 —— 每年至少要更新一次，
   否则跨年后断网机器上没有新一年的休班标记。在线机器会自己联网补，不依赖这一步。）
2. 跑全量自检：`powershell -File scripts\vm-acceptance.ps1 -Full`（客机上一遍过，最省事）。
   要在本机跑也行：双击一次 exe 让它自己解包（首次会解到「程序目录\runtime\<戳>」或
   `%ProgramData%\Study7Agent\runtime\<戳>`），再到那个目录里用包内 python 跑
   `webui\test_webui.py`（期望 500+ OK，1–5 项环境性跳过）；
   验收单文件 exe 本身用 `powershell -File scripts\vm-acceptance.ps1 -Exe`；
3. 需要时上真 Win7 虚拟机再跑一遍（**见第 5 节：别再每次都跑全量**）；
4. 同步镜像：`webui`、`docs`、`scripts`、`skills`、`README.md`、`README.en.md`、`AGENTS.md`、
   `LICENSE`、`agent.ico`、`agent-icon.png`、`start-webui.cmd`；
   镜像目录 `study7agent-src\study7agent-main` 同时是 **GitHub 仓库**
   （<https://github.com/mogutofu/study7agent>，私有）：同步完在**那个目录**里
   `git add -A && git commit -m "..." && git push origin main`。推之前先 `git status`
   看一眼——`config/agent.json`、`config/release-key.txt`、`webui\data\`、`dist\`、`build\`
   这些都已被 `.gitignore` 挡住，但**别把新的密钥文件放进去**；token 只放在命令行/环境变量里，
   不许写进任何文件。仓库里不含 Python 运行时、内置内核、发布产物（克隆下来是源码）。
5. 删掉工作树里所有 `tmp-*`；
6. 报告时写清楚：改了哪些文件、怎么验的、**哪些没验**、以及遗留问题。

---

## 4. 这个项目的硬性约束（违反了就不算完成）

- 只用随包 Python 3.8.10（`.\python\python.exe`）+ **标准库**，不许 pip、不许构建步骤；
- 目标系统 Windows 7 SP1 x64 + **PowerShell 2.0** + .NET 3.5，不许 Win8+ 才有的 API；
- 前端不许构建（`webui\ui\*.js` 是直接跑的原生 JS），图标一律内联 SVG（Win7 没有 emoji 字体）；
- 界面、提示、报错、文档一律中文；
- **绝对不要用 PowerShell 读写源码文本**（`Get-Content -Raw` + `Set-Content` 会把中文按 GBK
  读坏、且不可逆）；`.ps1` 必须 **UTF-8 带 BOM**，很多编辑器保存时会静默吃掉 BOM；
- 改完必跑自检：`.\python\python.exe webui\test_webui.py`。

---

## 6. 绝对不要杀 msedge / 用户正在用的程序

**老师的网页就是他自己开着的 Edge。** 曾经为了跑无头截图干净一点，写过

```powershell
Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force   # ← 禁止
```

结果把老师正在用的浏览器窗口（也是他跟 agent 说话的那个窗口）一起关掉了，
而且后面几轮的无头截图时好时坏——因为一直在杀自己所在的浏览器进程。

要跑无头浏览器时：

```powershell
# 用**独立的用户目录**，别碰老师的会话
msedge.exe --headless=new --user-data-dir="$env:TEMP\w7a-headless" `
           --screenshot=... http://127.0.0.1:PORT/
```

同理，任何 `Stop-Process` / `taskkill` 都要先确认目标 pid 不是老师在用的东西：
`explorer`、`msedge`（默认实例）、`QQ`、`WPS`/`wpp`、`vmware` 一律不动。
自己起的临时 python 服务可以按端口精确杀（`Get-NetTCPConnection -LocalPort N`）。

**改完 `webui\ui\` 下的东西，老师那儿不会自己变**：他的页面常年开着，不刷新就一直跑旧 JS
（这个误会已经发生过三次，最近一次是"球棍模型没有氢"）。现在有心跳指纹提示（`server.ui_stamp()`
+ `noticeUiUpdate()`），指纹变了侧栏会显示"界面已更新 · 点我刷新"。所以**改完前端要跟老师说一句
"刷新一下（或点侧栏那个提示）"**，别说成"已经好了"就完事。

**自己跑浏览器自检时的规矩**（2026-09-25 踩过：老师看到桌面上多了个
"127.0.0.1 拒绝访问"的窗口，还以为是他的 Study7Agent 挂了）：

- 自检一律用**无头**，不要在老师桌面上弹窗：
  `chrome.exe --headless=new --remote-debugging-port=9333 --user-data-dir=%TEMP%\w7a-headless-chem http://127.0.0.1:8899/`
- 临时服务用**自己的端口**（8899 起，别用 8770——那是老师 Study7Agent 的默认端口；
  8897 是历史上自检用的，也尽量避开以免和老师手动开的服务撞车），
  杀的时候**只按这个端口杀**，不要 `Get-Process python | Stop-Process`；
- 自检完把自己起的服务和无头浏览器一起收掉（按端口 + 按 `vendor\chromium-109` 路径过滤）。

---

## 5. Win7 客机验收：**分三档，默认别跑全量**（这条是拿时间换来的）

以前每验一次都是"重灌 35MB 包 + 解压 4219 个文件 + 跑几百项自检"，
一次十几分钟——改一行 `.ps1` 也要等这么久。其实**只有三件事非 Win7 不可**：

```powershell
powershell -File scripts\vm-acceptance.ps1                  # 快检，约 10 秒
powershell -File scripts\vm-acceptance.ps1 -Exe             # 单文件 exe 真机验收，几分钟
powershell -File scripts\vm-acceptance.ps1 -Full            # 全量自检，约 6-8 分钟（发布前才跑）
powershell -File scripts\vm-acceptance.ps1 -Full -Extract   # 连解压好的树一起重灌
```

| 档 | 验什么 | 多久 | 什么时候跑 |
| --- | --- | --- | --- |
| 快检（默认） | 把 `study7agent-float.ps1` 单独送进客机跑 `-SelfTest`：PowerShell 2.0 能建窗口、闭包抓对 Key、**五个本地动作**（切到PPT/回到桌面/打开网页/天气/字幕）接对、折叠/时钟正常、字幕字号能存能读、BOM 正确 | **~10 秒** | 每次动过 `.ps1` |
| `-Exe` | 把 `dist\study7agent-<版本>.exe` 送进客机整跑一遍：**内置 Chromium 109 在 Win7 上开得出界面**（这条开发机上验不了）、服务起来、日志里没有被迫升到 `--no-sandbox` | 几分钟（首次要在客机上解包 300 多兆） | **1.1.0 起的主验收**，动过 exe/内核/启动器就跑 |
| `-Full` | 整包解压后跑全部自检（当前 539 项） | ~8 分钟（自检本身 7 分半，省不掉） | 发布前 |

`-Exe` 判定读的是客机写回来的报告（`C:\Users\seewo\w7a-exe-report.txt`）：日志里有
`Study7Agent WebUI` 和"使用内置内核"、窗口标题里有 `Study7Agent`。写报告的那条 `.cmd`
**必须纯 ASCII**，所以中文锚点拆成两半：`.cmd` 里只等 ASCII 的 `Study7Agent WebUI`，
中文判定放在主机侧做。客机上已经有同一个 exe（版本+字节数）时会跳过 137MB 的传输。

`-Full` 会**增量同步**：客机上已经有解压好的树时只拷变化的文件（`webui\*.py`、`ui\*`、`scripts\*.ps1`），
不再重新解压那 4219 个文件（光解压就要 2-3 分钟）。客机没开机时脚本会自己
`vmrun start ... nogui`。它用的目录版 zip 现在是开发件（发布产物是单文件 exe），
没有就用 `make-package.py` 现打一个。

**别再写 `Start-Sleep -Seconds 480` 那种傻等**：全量那一档脚本每 20 秒报一次进度、跑完自己退出；
真要自己起就起成后台任务，别堵着不动。

**客机上的环境性坑（桌面这一族，1.1.0-beta9 起）**：
- `desktop.available()` 现在探的是**常驻 UIA 工作进程**（`uia.worker()` 起 PowerShell 跑
  `webui\uia_worker.ps1` 再 ping 一次），不是旧的 `GetSystemMetrics`，判断口径变了。
  没有交互式桌面的客机（Session 0 / 没登录）里，上客机第一件事是单独跑四个自检：
  `python webui\winapi.py`、`webui\winimage.py`、`webui\uia.py`、`webui\desktop.py`，
  先看 `Add-Type` 能不能在客机的 PowerShell + .NET 上编译 UIAutomationClient。
- `TestDesktopLive` 和 `test_screenshot_through_chat_needs_no_confirmation`
  （旧名 `test_screen_capture_through_chat_needs_no_confirmation`）都在
  `desktop.available()` 为假时 `skipTest`；`TestDesktopLive` 里窗口表为空也是单条 skip。
  所以客机上多半是"跳过"而不是失败 —— 要看跳过原因，别把跳过当成通过。
- 自检往 GBK 控制台打真实窗口标题会崩（`UnicodeEncodeError: '\u2713'`，U+2713 不在 GBK 里）。
  四个自检入口都已把 stdout/stderr 换成 UTF-8 + `errors="replace"`；新写的自检要照做。
- 客机上的 `.cmd` 必须**纯 ASCII + CRLF**；`.ps1` 必须 **UTF-8 带 BOM**（`TestFloatScript`
  现在会扫 `scripts\*.ps1` 全部文件）。另外**别用 PowerShell 拼字符串再喂给 python 写文件**：
  `|`、`>`、引号会被 PowerShell 先吃一遍，写出来的脚本是坏的。要写文件就用能写字节的工具。
