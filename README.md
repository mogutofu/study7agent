# Study7Agent · 交接文档（给下一个 Harness 任务）

> 这份 README 是**给接手这个项目的下一个 agent 看的**：项目是什么、边界在哪、代码怎么放、
> 怎么跑、怎么验、以及**踩过的坑**（这一节最省时间）。
> 面向老师的上手说明在 `docs\user-manual-zh.md`；WebUI 功能清单在 `webui\README-zh.md`；
> 原英文 README 保留为 `README.en.md`（本文件取代它成为主 README）。
>
> 交接状态：**版本 1.1.0-beta11，45 个工具，自检 111 个测试类 / 1025 个用例全通过**，
> 产物在 `dist\`。下文所有数字都是从代码里现查的，不是回忆。
>
> ⚠️ **README 的历史段落里混着各版本的数字**（§2.3 讲的桌面底座是 1.1.0-beta9 之后的新形态，
> §2.1/§2.2 讲的是 0.6.0 那一批）。**判断"现在是什么样"一律以本文件顶部的交接状态、
> §2.3、§10 为准**；带版本号的段落是"当时做了什么"的历史记录，不是现状。
> 逐阶段的完整进度在 **`docs\roadmap-1.1.0.md`**（M0–M20，每完成一阶段就更新）。
>
> 1.1.0 三件事与当前状态：
> - **化学实验室**：数据驱动的反应引擎（配平 / 反应预测 / 热化学 / 可逆反应按 ΔrG°→K 二分求反应进度、
>   恒容反应釜真实停在平衡上）+ 有机物结构可视化。数据全在 `webui\chemdata\`（96 种物质、
>   202704 条结构数据、157 条热化学），**运行期完全离线**。见 §2.4。
> - **物理模拟**：物块轨道 / 电场 / 磁场 / 电路四大块 + 沙盒 + 练习模式 + 3 个内置 PhET。
>   见 §2.5。
> - **内置浏览器内核 + 单文件 exe**：`vendor\chromium-109`（ungoogled 109.0.5414.120），
>   老师侧只有一个 `dist\study7agent-<版本>.exe`（约 176 MB）。见 §2.3、§3.4。
>
> 1.1.0-beta9 换掉了一件大事：整套 computer use 从"Python 拼 PowerShell"换成
> **Windows-MCP 形状的新四层底座**（`winapi.py` / `winimage.py` / `uia.py` + 常驻 UIA 工作进程），
> 桌面工具从 12 个旧名整族换成 17 个新名（`snapshot` `screenshot` `click` `type` `scroll` `move`
> `shortcut` `wait_for` `clipboard` `process` `registry` `app` `multi_*` `draw_path` …），
> 工具总数 39 → 45。**旧名全库清零，拿旧名调会直接报错。**
> 1.1.0-beta10 修掉「停止」信号被当成回调调用（`TypeError: 'Event' object is not callable`）
> 导致整族桌面工具全挂的线上回归 —— 跨模块传进来的信号要先归一化再用。
>
> 1.0.0-beta2 补两处：**`mouse_action` 加 `wheel`**（先把鼠标移到 `x,y` 再滚 —— 翻课件、看网页
> 不再靠拖滚动条；老师原话"ai 无法执行鼠标滚轮操作，更不用说移动鼠标到目的位置再滚轮了"）、
> **上下文窗默认值 64K → 384K**（`context.py` 的 `DEFAULT_CONTEXT_WINDOW = 393216`，按接口自报的
> max_tokens 上限 [1, 393216] 来；想给某台机器调小，在 `config\agent.json` 里写
> `"context_window": 65536`，或按模型档写进 `models`）。工具 schema 预算相应从 24000 放到 32000
> （实测 30170），理由记在 `TestToolSchemaBudget.SCHEMA_CEILING` 上面。
>
> 1.0.0-beta1 相对 0.6.0 加了：**平面作图模块**（`webui\plane.py` + `ui\plane.js`，对象模型 /
> 依赖联动 / 精确值，「几何画板」面板；架构见 `docs\plane-architecture.md`）、
> **空间立体模块跟平面对齐**（选点累加、沿线/在面拖、触屏轻点、右栏代数区式列表）、
> 曲线"无限延伸"（跟着可见范围重采样，含隐式方程）、QQ/托盘窗口识别与**启动器把界面叫回来**、
> 随包节假日表 + 后台刷新。悬浮工具栏、四个面板等 0.6.0 的东西都在（**屏幕底部字幕条后来整族下掉了**，
> 见 §2.2 的删除线说明）。
>
> 0.6.0 相对 0.5.3 加了：**悬浮工具栏**（`scripts\study7agent-float.ps1`，纯 PowerShell 2.0 +
> WinForms，Win7 自带，不用装任何东西）、抽学号大屏上的**「完全重置 / 抽下一个」**两个按钮、
> 以及一轮缺陷排查（见 §8 坑 16-20）。
>
> 之后又加了一整批（同一个 0.6.0 版本号内，见 §2.2）：**整体改名 Study7Agent**、
> **白底 + DeepSeek 蓝**配色（网页、悬浮窗、图标、AI 头像全套）、屏幕底部**字幕条**、
> 悬浮窗新增**打开网页 / 翻译查词 / 备忘录 / 日历 / 天气**六个按钮、
> 网页里的**翻译查词 / 备忘录 / 日历**四个面板、**本次产物**显示、
> `read_file` **按内容识别**更多文件类型 + **zip 自动解压**、
> 联网搜索多引擎兜底、WebUI 里的**熔断开关（默认关）**、作者与版权署名、
> 借 5 个优质教学技能（共 28 个技能，默认全部启用）。
>
> ⚠️ **每次做完任务必须照做**：改动动代码才需要 `.\python\python.exe scripts\make-release.py`；
> 产物是 `dist\` 里**三样**：干净的 exe（自己试跑用）、**对外分发的 zip**、
> **带密钥的 zip**（密钥只在那份 `config\agent.json` 里）。**只改文档就不必重打包。**
> 固定约定写在 **`AGENTS.md`**，交付前逐条对一遍。

---

## 0. 一句话说清这是什么

一个**面向 Windows 7 教室一体机 / 办公电脑的老师助手**：老师在浏览器里用中文说一句话，
它就能读写本机文件、生成课件（pptx/docx/xlsx）、查音视频为什么播不出来、操作桌面（截屏、
切窗口、在白板上画图、模拟键鼠）、按界面元素点按钮、联网搜索与下载、收发邮件/群消息，
**做化学/物理/生物的可视化实验台与作图**（1.1.0 加的，见 §2.4/§2.5），
并且**能改自己的源码、跑自检、重启自己**。

它由两半组成：**这一半是 WebUI**（Python 3.8 标准库 + 原生 JS），另一半是随包的
`agent.exe`（Go 写的 CLI，源码在 GitHub 仓库的 `cmd\`/`internal\`，本目录只有预编译产物）。

---

## 1. 硬性约束（改代码前先读，违反了就不算完成）

| 约束 | 具体是什么 | 为什么 |
| --- | --- | --- |
| **只用随包 Python 3.8.10** | `.\python\python.exe`，**只用标准库**，不许 pip、不许自带 wheel、不许构建步骤 | 学校机器常常连不上外网；老师不会配环境 |
| **目标系统 Windows 7 SP1 x64** | 只能用 **PowerShell 2.0 + .NET 3.5** 自带的东西；不许 Node/Electron/npm；不许 Win8+ 才有的 API | 教室一体机就是 Win7 |
| **前端不许构建** | `webui\ui\*.js` 是直接跑的原生 JS（ES5 风格为主），没有打包器、没有 TypeScript | 一份 zip 解压就能用 |
| **浏览器基线** | Chrome 109 / Firefox 115 ESR / 360 极速模式（Chromium）；**没有 emoji 字体**，图标一律内联 SVG | 国产双核浏览器 + Win7 字体 |
| **中文优先** | 界面、提示、报错、文档都是中文；控制台可能是 GBK 代码页 | 用户是老师 |

**位置**：工作树 `D:\agent-win7-x64\agent-win7-x64`；
GitHub 镜像 `study7agent-src\study7agent-main`（完整上游仓库：多了 `cmd\`、`internal\`、
`vscode-extension\`、`go.mod`）。改完代码要**同步镜像**（见 §3.4 第 6 步）。

---

## 2. 目录结构（带行数，便于估工作量）

> 行数是**现在现查的**（`1.1.0-beta11`）。带版本号的子段落是历史记录。

```
agent-win7-x64\
├─ agent.exe                  Go 写的 CLI（预编译，本目录不含源码）
├─ Study7Agent.exe            ★ 老师双击的那个：一键启动器（.NET 3.5 / CLR 2.0 的 WinForms，188KB）
├─ launcher\
│   ├─ Study7Agent.cs  2352  它的源码（编译见 scripts\build-launcher.ps1）
│   └─ Payload.cs         尾  部解包器：读 exe 尾部 [戳][长度][魔数] 把程序树解出来
├─ vendor\
│   ├─ chromium-109\          随包内核 ungoogled-chromium 109.0.5414.120（Win7 天花板）
│   └─ chem-probe\            取化学数据时用的探针（不随包发布）
├─ agent.ico / agent-icon.png 启动器图标（16/24/32/48/64/128 DIB + 256 PNG，逐尺寸单独绘制）
├─ start-webui.cmd            Win7 控制台启动器：有 Study7Agent.exe 且没带参数时**转交给 exe**，
│                             否则照旧检查运行时/目录完整性、建桌面快捷方式、起 WebUI（兜底路径）
├─ README.md                  本文件（交接文档）    README.en.md  原英文 README
├─ checksums.txt              干净包的 sha256 清单（打包时重新生成）
├─ build\                     make-exe.py 的中间产物（payload-*.tar.gz）。可再生，
│                             只有改外壳时要 --payload 复用那一份；其余随时可删。
├─ dist\                      发布产物（make-release.py 写的三样，见 §3.4）。只留当前版本，
│                             历史 beta/alpha 包删掉——它们每份 170-180 MB，攒起来很占地方；
│                             需要旧包时用 git/备份重打，别在 dist\ 里堆着。
├─ AGENTS.md                  ★ 每次干活都要照做的固定动作 + 硬约束 + 客机坑
├─ docs\                      阶段地图（roadmap-1.1.0.md）+ 引擎说明 + 老师手册 + 自启动排查（autostart-win7.md）
├─ skills\                    28 个技能（每个一个目录 + SKILL.md）
│   app-entries cheap-read classroom courseware doc-to-questions email error-analysis
│   explain-simply figure-first flashcards general grade-analysis lesson-plan meeting
│   parent-report qq-image-open quiz report socratic spreadsheet tiered-homework
│   tool-discipline translator ui-idioms ui-verify web-summary win7-file-open work-loop
│   └─ 技能**默认全部启用**（会话里没手动选过 = 全用上）；figure-first 是**硬性技能**
│      （frontmatter 里 `always: true`）：不管老师选没选、输的是什么，每次都会注入。见 §3.3
├─ config\                    ★ 整个目录都不进包、不进镜像
│   ├─ agent.example.json     随包模板（**唯一**允许进包的 config）
│   ├─ agent.json             本机配置（API Key）——**永不进包**
│   ├─ webui.json             WebUI 设置（工作区、令牌）——**永不进包**
│   ├─ release-key.txt        发布用密钥（打包时读，不进包）
│   └─ holiday-<年>.json      节假日缓存
├─ python\                    随包 Python 3.8.10 x64（5059 个文件）
├─ scripts\
│   ├─ make-package.py   311  打包（干净包 / 预填 Key 包）；INCLUDE_DIRS / INCLUDE_FILES 规则表
│   ├─ make-exe.py      371  打单文件 exe（payload 打成 tar.gz 追加在 exe 尾部）
│   ├─ make-release.py  299  一条命令出发布三样（干净 exe + 干净 zip + 带密钥 zip）
│   ├─ make-keyed-full.py     带密钥整包（旧的目录版流程）
│   ├─ make-icon.py     847  纯标准库生成 ico/png（自研 ICO 编码器）
│   ├─ build-launcher.ps1  95 编译 Study7Agent.exe（csc 3.5；UTF-8 **带 BOM**）
│   ├─ study7agent-float.ps1 1686 悬浮工具栏（PowerShell 2.0 + WinForms，UTF-8 **带 BOM**）
│   ├─ vm-acceptance.ps1 392  Win7 客机验收（快检 / -Full / -Extract 三档）
│   ├─ win7-gate.py  1455     Win7 门禁（禁字、编码、API 泄漏等，0 错误才算过）
│   ├─ fetch-chem-data.py    抓化学数据（Wikidata / PubChem / NIST WebBook / ChEBI）
│   ├─ fetch-chromium.py     抓内置内核（GitHub 直连，SHA-256 已钉在脚本里）
│   ├─ fetch-molecule-libs.py / fetch-bio-media*.py  抓分子库 / 生物素材
│   └─ refresh-holidays.py   烤今年 ±1 年的节假日进 webui\holidays-bundled.json
├─ study7agent-src\           GitHub 镜像（study7agent-main 里还有 cmd\、internal\、go.mod）
└─ webui\                     ★ 本项目的主体
    ├─ server.py      2899  HTTP 服务：SSE 流式对话、设置、会话、文件、快照、静态资源（65 个 /api 路由）
    ├─ core.py        4035  设置/迁移、AgentRun 主循环、模型客户端、重试、压缩、熔断、子代理
    ├─ tools.py       5070  **45 个模型工具**的 schema + 实现 + 授权 + 落盘(spill)
    ├─ test_webui.py 20693  **自检（111 个类 / 1025 个用例）**：这就是本项目的验收标准
    │                   ├─ 桌面控制四层底座（1.1.0-beta9 重写，Windows-MCP 形状，见 §2.3）
    │                   │   winapi.py   1332  ctypes 直连 user32/gdi32/psapi/advapi32
    │                   │   winimage.py  303  BGRA 画布 + 手写 PNG 编码器（替代 Pillow）
    │                   │   uia.py       547  常驻 UIA 工作进程的 Python 侧
    │                   │   uia_worker.ps1 1203 常驻 PowerShell 侧（**必须纯 ASCII 无 BOM**）
    │                   └─ desktop.py  1628  把上面三层包成动作与只读查询（只给 tools.py 用）
    │                   ├─ 化学实验室（§2.4）
    │                   │   chem.py     2135  引擎：配平/预测/热化学/实验台仿真
    │                   │   molecule.py 1119  有机物结构（球棍/结构式/反应动画）
    │                   │   chemdata\          现查的真实条目数：
    │                   │     chem-species.json     96 种物质 / 16 KB
    │                   │     chem-common.json      84 条常用（颜色状态等）/ 10 KB
    │                   │     chem-rules.json       39 组规则（溶解性/活动性/酸/气体…）/ 73 KB
    │                   │     chem-thermo.json      157 条热化学数据 / 22 KB
    │                   │     chem-mechanisms.json  8 个反应机理 / 3 KB
    │                   │     chem-structures.json  202704 条结构数据 / 36 MB
    │                   ├─ 物理（§2.5）
    │                   │   plane.py    2147  几何画板对象模型（依赖联动）
    │                   │   plane_geom.py 2889  几何运算内核
    │                   │   plane_math.py 1691  精确值 / 根号 / 分数
    │                   │   plane_hub.py  501  撤销重做与事件
    │                   │   sim2d.py    2526  物块/电场/磁场 沙盒引擎 + 可动件（板/轮/圆弧/弹簧/绳杆）
    │                   │   physics.py   532  高考四大块模型
    │                   │   circuit.py  1076  电路
    │                   │   instruments.py 558  读数仪器
    │                   │   practice.py   405  练习模式 + 错题本
    │                   ├─ 生物（§2.5）
    │                   │   biology.py  2506  54 个课本过程的分步动画（PROCESSES / 6 章 / 25 个仿真）
    │                   ├─ 通用
    │                   │   plot.py     2177  作图引擎：表达式求值（自写递归下降解析器，绝不 eval）+ 几何小语法
    │                   │   office.py   1317  纯标准库生成/解析 docx/xlsx/pptx（含模板套用、讲稿备注）
    │                   │   calc.py      714 / units.py 349 / chart.py 386 / schematics.py 205
    │                   │   media.py     672  音视频/课件体检（容器、编码、时长、外链、字体）
    │                   │   classroom.py  584  抽人、计时、投屏、错题本、成绩统计
    │                   │   search.py    487  联网搜索（bing → duckduckgo → searxng 依次兜底，去重 + 相关性加权）
    │                   │   download.py  303  下载：有界、原子落盘、Win7 改名重试表
    │                   │   apps.py      357  外部应用查找与启动（白板/展台/QQ），悬浮工具栏用
    │                   │   notify.py    261  SMTP 邮件、企业微信/钉钉/飞书机器人
    │                   │   holiday.py 303 / lunar.py 186  节假日调休 / 农历（自算，1900-2100）
    │                   │   memo.py      131  备忘录，存在 config\memo.json（原子写，不进包）
    │                   │   context.py   200  token 估算、工具结果裁剪、上下文压缩规划
    │                   └─ ui\                  前端（无构建，ES5 风格原生 JS）
    │                       index.html 1449 / style.css 1797 / plane.css
    │                       app.js 7435 / plane.js 4700 / chem.js 2115 / sandbox.js 2641
    │                       circuit.js 1010 / panels.js 673 / extras.js 536 / complete.js 372
    │                       practice.js 332 / bio.js 281 / md.js 263 / chartui.js 233
    │                       icons.js 101 / phet.js 98 / compat.js 87（老浏览器兜底）
    │                       ※ 新加的 js **必须**补进 test_webui.py 的 UI_FILES 表，
    │                         否则禁用字符 / 引用 id / 重名函数这三项检查扫不到它
```

### 2.3 桌面控制的底座换了：ctypes 直连 + 常驻 UIA 工作进程

**这一版不再"Python 拼 PowerShell 再执行"了**（那套 `run_powershell` / `ps_quote` /
`SENDKEY_NAMES` 已经删掉）。现在是四层：

| 文件 | 干什么 |
| --- | --- |
| `webui\winapi.py` | ctypes 直连 user32/gdi32/kernel32：窗口枚举与信息、`BitBlt` 抓屏（可缩放）、`SetCursorPos` + 不带 `ABSOLUTE` 的按键事件（**规避 Win7 多屏绝对坐标归一化的坑**）、`KEYEVENTF_UNICODE` 逐字输入、剪贴板、Toolhelp32 进程表、注册表、ShellExecuteEx 启动 |
| `webui\winimage.py` | 纯标准库图像：BGRA 缓冲、`crop`/`resized`、画框、5×7 点阵字、编号标注、zlib + CRC32 手写 PNG |
| `webui\uia.py` + `webui\uia_worker.ps1` | **唯一**的 PowerShell：常驻一个进程，stdin/stdout 逐行收命令、行内用 `\x1f` 分隔字段、每条命令 try/catch 且**永远回一行**（一条命令抛异常不能把常驻进程带走），超时杀进程并在下一条命令时自动重启。C# 走 `Add-Type` 调 .NET 的 `UIAutomationClient` |
| `webui\desktop.py` | 把上面三层包成 Windows-MCP 形状的动作与只读查询（`snapshot`/`screenshot`/`click`/`type`/`scroll`/`move`/`shortcut`/`app`/`clipboard`/`process`/`registry`/`multi_*`/`draw_path`…），只给 `tools.py` 用 |

实测（开发机 1920×1080）：抓屏 **39–43 毫秒**、标注 +20 毫秒、PNG 编码 85 毫秒（≈200KB）；
UIA 常驻查询 ping 往返中位数 **0.13 毫秒**，一次 400 元素上限的快照 **0.26–0.45 秒**，
同等操作新起一个 PowerShell 进程要 **544 毫秒**——常驻是为了这个。

**两条硬约束（改这些文件之前先读）**：

1. `webui\uia_worker.ps1` **必须保持纯 ASCII、不带 BOM**。PS 5.1 读无 BOM 文件按 ANSI 解，
   在 C# 段里写中文会变成乱码，`Add-Type` 直接编译失败（报过
   `当前上下文中不存在名称"rawType"`）。文件里现在 0 个非 ASCII 字节，注释全是英文。
2. `Add-Type` 的 C# 只能用到 C# 3.0 / .NET 3.5 的 API（目标是客机的 PS 2.0）：
   没有 `GetCachedChildren`（那是**属性** `CachedChildren`），`ControlTypeProperty` 返回的是
   `ControlType` 对象而不是字符串（要取 `.ProgrammaticName`），UIA 交回的 COM 对象
   （如 Chromium 的 `AcceleratorKey`）不能直接 `Convert.ToString`——会得到
   `System.__ComObject` 噪声，非原始类型一律当空。

**已知未验证项**：这套底座在开发机（Windows 11，PS 5.1 / CLR 4.0）上逐项自检通过，
但**没有在真 Win7 客机上实跑过**（本轮的规矩是不动 VM）。客机上要盯的三件事：
`Add-Type` 能否在 PS 2.0 + .NET 3.5 下编译（UIAutomationClient 在 GAC 里，理论上可以）、
抓屏与输入的 DPI/多屏坐标、以及 `WebClient`/TLS 之外的网络无关部分（这套底座不联网）。
`python webui\winapi.py` / `python webui\winimage.py` / `python webui\uia.py` / `python webui\desktop.py`
四个文件都带自检，直接跑就能出结果。

### 2.2 0.6.0 第二批：字幕条 / 四个面板 / 产物显示 / 改名换色

| 能力 | 老师怎么用 | 落在哪 |
| --- | --- | --- |
| ~~**字幕条**~~ | **已下掉**（0.6.0 加的悬浮窗字幕条：蓝底白字置顶、字号 A-/A+ 直接调）。1.1.0 的工具栏按钮表 `$Script:Buttons` 里已没有它，`webui\subtitle.py` 与 `/api/float/subtitle` 都不存在了，`show_subtitle` 工具也没有了 | — |
| **翻译查词** | 悬浮窗「翻译查词」→ 网页面板，中英互译 / 词典两种模式，**等待时显示「思考中」** | `POST /api/translate` + `webui\ui\extras.js` |
| **备忘录** | 悬浮窗「备忘录」→ 随手记，存在 `config\memo.json`（**不进包**，升级不丢） | `/api/memo` |
| **日历** | 悬浮窗「日历」→ 月历 + **节假日 / 调休 / 农历**，联网取自 timor.tech 并缓存到 `config\holiday-<年>.json`；断网就只用本地农历并如实标注数据来源 | `webui\lunar.py` + `webui\holiday.py` + `/api/calendar` |
| **天气** | 悬浮窗「天气」→ 直接开和风天气公众站（逐小时 + 浏览器定位）；换站点改 `.ps1` 里的 `$Script:WeatherUrl` | `scripts\study7agent-float.ps1` |
| **打开网页** | 悬浮窗底部多了一个跟别的按钮一样的「打开网页」（以前只有顶部那条能点，老师根本不知道） | 同上 |
| **本次产物** | AI 写 / 下载 / 保存的文件在对话里直接列出来，能点开、能打开所在文件夹 | `webui\ui\app.js` + `extras.js` |
| **读更多文件** | `read_file` 不再只认后缀：不认识的扩展名按内容判断，UTF-8 / GBK 中文文件都能读；给 `.zip` 会**自动解压到工作区**并列出内容 | `webui\tools.py` |
| **熔断开关** | 设置里一项，**默认关**：关着的时候完全不判"你在打转"；阈值照原样存，不会被读成 0 冲掉 | `webui\core.py`（`_loop_guard` / `_repeat_guard`） |
| **改名换色** | 全仓改叫 **Study7Agent**（含系统提示词里 AI 的自我认知、桌面快捷方式名、窗口标题），配色统一成**白底 + DeepSeek 蓝 `#4d6bfe`**，图标与 AI 头像都是同一套蓝；侧栏署名「作者 Lv Chengyue · Created and Powered by "AI"」 | `webui\ui\*`、`scripts\make-icon.py`、`agent.ico` |

**改名时刻意保留的东西**（别手贱去改，改了真机验收直接挂）：
`W7A_*` 环境变量（`scripts\vm-acceptance.ps1` 和自检都在用）、`config\` 里的键名。

### 2.1 悬浮工具栏（0.6.0 新增，老师最常用的入口）

`Study7Agent.exe`（或没带 exe 时的 `start-webui.cmd`）拉起 `server.py` 之后，会顺手把
`scripts\study7agent-float.ps1` 启动起来（直接跑 `server.py` 时用 `--no-float` 可以关掉）。
它是屏幕右侧一条置顶的小工具条：

```
┌──────────────────────┐
│ ▣ Study7Agent  收起   │  ← 顶栏和图标只当拖动把手（点一下不跳前台）；开界面点「显示界面」；「收起」缩成右边一条
├──────────┬───────────┤
│ 希沃白板  │ 希沃展台   │
│ QQ       │ 切到PPT    │
│ 计时      │ 抽学号     │
│ 截图给AI  │ 板书保存   │
│ 课堂标记  │ 物理      │
│ 化学      │ PhET      │
│ 生物      │ 数学函数   │
│ 几何画板  │ 计算器     │
│ 翻译查词  │ 备忘录     │
│ 日历      │ 天气      │
│ 显示界面  │ 回到桌面   │
├──────────┴───────────┤
│ 状态（最多三行，悬停看全文）│
│ 退出（连服务一起关）    │
└──────────────────────┘
```

按钮表在 `scripts\study7agent-float.ps1` 的 `$Script:Buttons` 里，一行一个，
`Key` 直接就是 `/api/float/command` 的动作名（`ppt` / `desktop` / `page` / `weather` 是本地动作）。
自检 `TestFloatScript` 守着这张表——**加按钮要同时在服务端认这个动作**。

**折叠**：点右上角「收起」只剩右边一条窄条，**现在里面只有一个图标**（老师前后反馈三次：
46×138 带时钟 → 38×112 → 34×88 → 最后只留图标，退出按钮移到展开态右下角）。
尺寸**按控件实际需要的宽高算**，不硬编码（硬编码 34 宽时"退出"两个字会切掉）。
折叠态那一块**只能用图标本身**点开——图标文件读不出来时也有个"学"字兜底控件；
**点图标只展开工具条本身，不会把程序窗口切到前台**（1.1.0-beta11 起；要打开程序点展开后的「显示界面」），
客机快检就是靠 `collapsedCount=0` 抓到这一条的。
折叠状态记在 `logs\float-ui.txt`；**没记过时默认就是收起的**（一体机右边那一条本来就挡视线，展开该是老师主动的）。

**外观**：无边框窗口自己削了圆角和 1px 边框，标题栏是渐变，按钮是圆角卡片。
不这么做的话置顶小窗贴在一体机桌面上会糊成一块（见 §8 坑 23）。

**它不是第二套实现**：按钮只做三件事——`POST /api/float/command` 让服务端干活、
让网页执行一条排队的命令、或者就地做一个"切窗口"动作。抽学号、计时、作图、截图、板书、
课堂时间点用的全都是网页里原有的那套代码（见 §4 的"悬浮工具栏信箱"）。

**四个本地动作**（不走 HTTP，点下去立刻生效）：

- **切到PPT**：两段。先在本地找正在运行的 WPS 演示 / PowerPoint 并切到前台（快路，
  不碰硬盘、不启进程，点下去就是立刻切）；**一个都没开**时才让服务端用默认应用打开
  **QQ 下载目录里最新的那份课件**（找法见下），起来之后再切到前台。
- **打开白板 / 展台 / QQ**：**先切、切不到才启动**。先在本地找它的窗口——**包括收进托盘那种
  "隐藏"的窗口**（QQ 点右上角 X 是藏进托盘，`IsWindowVisible` 会变成 0，只枚举可见窗口就
  永远找不到它），找到就 `ShowWindow(SW_SHOW)` + 切到前台，**根本不去启动**；
  一个窗口都没有时才让服务端 `apps.py` 启动它，起来之后再切到前台。
  顺序反过来会出事：QQ 已经在托盘里时再 `os.startfile` 一次，轻则白启动，
  重则**弹出一个新的登录窗口**（真机上见过）。
- **回到桌面**：`Shell.Application.MinimizeAll()`，然后把工具栏自己恢复成正常状态
  （MinimizeAll 会把置顶的工具栏也最小化，不恢复的话老师就再也点不到它了）。
- **显示界面 / 天气**：只认**自带内核**那个窗口（进程 exe 路径带 `chromium-109`）并切前台；
  找不到就再起一次 exe 让在跑的实例把界面开出来，**绝不打开默认浏览器**
  （老师自己的 Edge 里开着同一个页面，标题一模一样，按标题找会把他那个浏览器切到前台，
  他报过两次）。天气是直接开和风天气公众站。

**切窗口为什么这么讲究**（三条都是真机上量出来的）：

1. `WScript.Shell.AppActivate` **对最小化的窗口直接返回 False 而且什么都不做**——
   这是"点了切到PPT没反应"的真正原因；
2. WPS 12 是**单 exe**（`wps.exe /wpp`，没有 `wpp.exe`），进程名名单里必须有 `wps`；
3. **收进托盘的窗口要先 `SW_SHOW` 再 `SW_RESTORE`**，而且枚举窗口时不能只看
   `IsWindowVisible`（QQ 托盘后全部窗口 vis=0）。挑候选时用窗口面积把
   `32x38` 那种辅助窗排除掉。三条都记在 §8 坑 21、22、24。

**生命周期**：网页每 2 秒 `POST /api/float/ping` 报活；关页面时 `beforeunload` 再补一个
`/api/float/bye`。工具栏轮询 `GET /api/float/state`，发现网页没了就
`POST /api/float/quit` → 服务端 `os._exit(0)` → `start-webui.cmd` 的黑窗口跟着关闭 → 工具栏自己退出。
老师按 F5 不会误伤（新页面 2 秒内重新报活）。

**为什么是 PowerShell**：Win7 自带 PowerShell 2.0 + .NET 3.5，里面就有 WinForms；
不需要 Node/Electron/.NET 4。代价是 PowerShell 2.0 的一堆限制，见 §8 坑 16。

**打开外部应用**：`webui\apps.py` 按三级顺序找——`config\float.json`（可选，老师自己填）
→ 常见安装目录（`%ProgramFiles%` + 一个 `*` 通配）→ 开始菜单/桌面快捷方式名字里的关键词。
找不到时按钮会明确告诉他"把 exe 路径填进 config\float.json"，格式就写在提示里。
`config\float.json` 不进包（`config` 目录本来就不在 `INCLUDE_DIRS` 里）。

**「切到PPT」找不到课件时去哪找**：`apps.presentation_dirs()` 按这个顺序——

1. `config\float.json` 里的 `"qq_download_dir"`（**填了就只看这一个目录**：
   老师既然说了"QQ 的文件在这"，再跑去 Downloads 翻一份不相干的旧课件打开比找不到更糟）；
2. 自动猜的位置：`文档\Tencent Files\<QQ号>\FileRecv`（经典 QQ）、
   `...\nt_qq\nt_file`（QQNT）、`文档\Tencent Files` 本身、最后是系统下载目录。

**QQNT 的下载路径存在它自己的数据库里，外面读不到**，所以第 2 步只是尽力而为。
这两项**在网页的「设置 → 悬浮工具栏」里就能填**（`GET/POST /api/float/config`），
也可以直接编辑 `config\float.json`：

```json
{
  "qq_download_dir": "D:\\QQ文件",
  "apps": {
    "whiteboard": "D:\\希沃\\EasiNote5\\EasiNote5.exe"
  }
}
```

`.ppt / .pptx / .pps / .ppsx / .pptm` 都认，`~$` 开头的临时文件跳过，最多往下翻 4 层。

### 2.2 作图面板（0.6.0 新增，替掉"在希沃白板里画"）

**数学函数 / 几何画板**以前是靠 `draw_path` 指挥鼠标在白板上画——慢、还容易画歪。
现在改成**自己的作图引擎**（`webui\plot.py`）：

- **老师**：顶栏「作图」按钮，或在悬浮工具栏点「数学函数 / 几何画板」，
  打开作图面板，敲表达式（`sin(x)`、`x^2 - 2x - 3`、`2x+1`）或几何图形
  （`A(0,0)` / `seg A B` / `circle O 3` / `poly A B C` / `angle A B C`），点「画出来」立刻出图。
- **AI**：`show_figure` 工具。老师只要说"画一个底 4 高 3 的直角三角形"，
  模型读懂题意后把表达式或图形描述交给同一个引擎，图画在**同一个面板**里。
- **坐标是本地算的**：表达式用自己写的递归下降解析器求值（**绝不 eval**），
  采样 480 点、按渐近线切成多段（`1/x` 两支不会连成一条竖线，`tan` 会断成 7 段）；
  几何部分解析一个一行一条的小语法，圆可以写半径也可以写过圆上一点，半径自动算。
- 这是 1.1.0 之前唯一加过工具 schema 预算的东西：`SCHEMA_CEILING` 20000 → 21000（约 870 字符），
  理由写在 `TestToolSchemaBudget` 的涨价记录里。**现在上限是 32000、实测 30170**（工具数涨到 45）。

**为什么值得单开一个引擎**：让模型吐几十上百个采样点，又贵又容易算错；让它给**表达式**
最省也最准，而且"图画错了"在课堂上是硬伤。

### 2.4 化学实验室（1.1.0 新增）

数据驱动的反应引擎 `webui\chem.py` + 有机物结构 `webui\molecule.py`，前端 `webui\ui\chem.js`，
接口 `/api/chem`（配平另走 `/api/balance`）。**数据全在 `webui\chemdata\`，运行期完全离线**，
没有一次网络请求；缺数据时如实说"资料未收录"，绝不编造。

| 能力 | 现状（现查） |
| --- | --- |
| 试剂架 | **201 瓶**：手写条目 + 按"阳离子 × 阴离子"成套生成（含 41 种有机物、50 种盐溶液、28 种固体试剂、9 种催化剂），按 `tag` 分货架 |
| 物质库 | 96 种物质（`chem-species.json`），带中文名、式量、颜色、状态 |
| 反应规则 | 39 组（`chem-rules.json`：溶解性 14 组、活动性、酸、气体 …） |
| 热化学 | 157 条（`chem-thermo.json`），ΔH/ΔS/ΔG |
| 反应机理 | 8 个（`chem-mechanisms.json`） |
| 结构数据 | **202704 条**（`chem-structures.json`，36 MB）供结构式与球棍模型 |
| 可逆反应 | 按 ΔrG°→K **二分求反应进度**，恒容反应釜真实停在平衡上 |

### 2.5 物理与生物（1.1.0 新增）

- **几何画板**（`plane.py` / `plane_geom.py` / `plane_math.py` / `plane_hub.py` + `ui\plane.js`，
  接口 `/api/plane/*`）：对象模型、依赖联动、精确值（分数 / 根号）、撤销重做，**已能导出图片**
  （`/api/plane/export`）。1.1.0-beta1 加（见上面的历史段落）。
  1.1.0-beta11 的工具条补齐并重排：**中点 / 重心 / 内心 / 垂心 / 垂线 / 中垂线（线段垂直平分线）/
  平行线 / 角平分线 / 外接圆**都在第一屏点得到；中垂线与角平分线画课本式记号（中点直角 + 等长小杠、
  等角双圆弧），三心拖动时客户端本地现算，不来回请求服务端。
- **物理沙盒 v2**（`sim2d.py` + `ui\sandbox.js`，1.1.0-beta11）：在原来的"物块贴静态轨道"之上加了
  **板块（可动小车）/ 轮子（纯滚动）/ 圆弧 / 弹簧 / 轻绳（可绕定滑轮）/ 刚性杆 / 悬挂点**，
  轨道可以挂在板上跟着板动，物块与板之间的正压力和摩擦力**等大反向**、相对滑动会生热并记进能量账。
  自带 6 条新例子，其中 `exam`「小车上的弯道（单摆撞物块）」就是老师那张大题（答案 6 m/s / 3 N·s /
  3√5/4 m/s，各项误差 ≤1%）；场景格式与装配口径写在 **`docs\sandbox-linked-bodies.md`**。
- **物理沙盒**（`sim2d.py`：物块 / 电场 / 磁场）、**高考四大块模型**（`physics.py`）、
  **电路**（`circuit.py`）、**读数仪器**（`instruments.py`）、**练习模式 + 错题本**（`practice.py`）、
  **3 个内置 PhET** 仿真（离线可用）；前端 `ui\sandbox.js` / `circuit.js` / `practice.js` / `chartui.js`。
- **生物**（`biology.py` + `ui\bio.js`，接口 `/api/bio`）：**54 个**课本过程的分步动画
  （`PROCESSES`，覆盖 6 章），另有 25 个互动仿真（`SIMS`）、24 项重点（`FOCUS`）。

---

## 3. 怎么跑、怎么验、怎么打包

### 3.1 跑起来

```bat
Study7Agent.exe                                  :: ★ 老师用这个：双击就完事（无黑窗口 + 应用窗口 + 托盘）
start-webui.cmd                                  :: 兜底：有 Study7Agent.exe 就转交给它，没有就走控制台那条老路
start-webui.cmd --port 8899                      :: 带参数时一律走控制台老路（VM 验收/调试用）
.\python\python.exe webui\server.py --port 8770 --no-open
.\python\python.exe webui\server.py --help       :: --base-dir --host --port --no-open --verbose
```

默认监听 `127.0.0.1:8770`；改成 `0.0.0.0` 会自动生成访问令牌（局域网平板遥控用）。
加 `--no-float` 可以不启动悬浮工具栏（调试前端时用得上）。

#### `Study7Agent.exe` 干了什么（老师双击的那个）

一次双击 = 起服务 + 起工具栏 + 开界面窗口 + 一个托盘图标，全程**不出现黑窗口**：

| 步骤 | 行为 |
| --- | --- |
| 单实例 | 有名互斥体 `Local\study7agent-exe`：第二次双击只把已经开着的界面窗口切到前台，然后自己退出 |
| 自检 | `python\python.exe`、`webui\server.py` 缺一个就弹中文提示框说清楚缺什么 |
| 服务在跑吗 | `HTTP GET http://127.0.0.1:<port>/api/state`（1.5 秒超时）；`port` 取自 `config\webui.json`，读不出来用 8770 |
| 起服务 | `python\python.exe webui\server.py --no-open --no-float`，`CreateNoWindow`，stdout/stderr 追加进 `logs\exe.log`，`PYTHONIOENCODING=utf-8` |
| 等就绪 | 无边框「正在启动 Study7Agent…」小窗，每 300 毫秒探一次端口，最多 40 秒；超时弹带 `logs\exe.log` 尾巴的错误框（只有一个「退出」按钮） |
| 界面窗口 | 用 Chromium 的 `--app=` 开一个**独立应用窗口**（360极速 → 360安全 → Chrome → Edge → 注册表 App Paths → 目录里扫一遍）。带自己的 `--user-data-dir=<exe 目录>\browser-profile`，**不碰老师的浏览器会话/历史/登录**，关掉它也不影响老师其它网页；`--app` 不被支持时 `--new-window` 兜底，最后才退回默认浏览器 |
| 托盘 | `NotifyIcon`（图标是 `agent.ico`）：**打开界面 / 打开工作区 / 重启服务 / 查看日志 / 退出（连服务一起关）**；双击托盘 = 打开界面 |
| 退出 | 只收**自己拉起来**的进程树（python 服务、悬浮工具栏、那个独立 profile 的浏览器实例）：`taskkill /PID <pid> /T /F`（.NET 3.5 没有 `Kill(true)`）；老师的浏览器、资源管理器一律不碰 |
| 服务自己没了 | python 一退出（老师从工具栏/网页退出、或服务崩了），exe 跟着收摊，不留一个点不动的空托盘图标 |

想不带界面验证一遍：`Study7Agent.exe --quit` 会让正在运行的那个实例走**和托盘「退出」完全一样**的清理流程。

编译它（Win7 和本机都自带这个编译器，不需要装 SDK）：

```bat
powershell -ExecutionPolicy Bypass -File scripts\build-launcher.ps1
:: C:\Windows\Microsoft.NET\Framework64\v3.5\csc.exe /noconfig /target:winexe /platform:anycpu
::   /codepage:65001 /optimize+ /out:Study7Agent.exe /reference:System.dll
::   /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /win32icon:agent.ico
::   launcher\Study7Agent.cs
```

`/noconfig` 是故意的：不带 `csc.rsp` 里那串默认引用（里面有 System.Core），产物只引用三个程序集，
CLR 头是 **v2.0.50727**（Win7 SP1 自带的 .NET 3.5 直接能跑，不需要 .NET 4）。
`/codepage:65001` 也不能少：源码是 UTF-8 无 BOM，少了它 csc 3.5 会按本地代码页（中文机器是 GBK）
解码，界面上的中文全变乱码而且**一个编译错误都不会报**。

### 3.2 自检（**每次改完必须跑，这是唯一的验收标准**）

```bat
.\python\python.exe webui\test_webui.py                    :: 全量 111 个类 / 1025 项，约 11 分钟
.\python\python.exe webui\test_webui.py TestHttpServer     :: 只跑某个类
.\python\python.exe webui\test_webui.py -v                 :: 详细
```

自检**不需要联网、不需要 API Key**（用内置假模型服务），并且**一开始就设 `W7A_NO_INPUT=1`**：
任何测试都不会去动使用者的鼠标键盘。测试类清单见 §7。

### 3.3 硬性技能：`always: true`

技能的常规玩法是"按名字/关键词命中，或老师在某次会话里手动勾上"。但有一类规矩是
**必须一直生效**的，靠关键词命中不够保险——典型就是"画函数图像必须用 `show_figure`，
不要开希沃白板"：漏一次老师就看到 AI 跑去开别的软件了。

所以 `SKILL.md` 的 frontmatter 支持一个 `always` 开关：

```markdown
---
name: figure-first
description: 画函数图像、几何图形一律用 show_figure 画进网页的作图面板。
always: true
---

正文……
```

标了 `always: true` 的技能：

- `core.AgentRun._choose_skills` 会**无条件**把它放进每次的 payload 最前面，
  不管老师选没选、输的是什么、有没有命中关键词；
- 会话里那个 `skills` 列表只决定"额外启用哪些"，**关不掉它**；
- 技能中心里不给复选框，只挂一个「常驻」牌子（`to_dict()` 里带 `always` 字段）。

**为什么不能用"写进系统提示词"代替**：老师在 `config\webui.json` 里改过 `system_prompt`
的话，`DEFAULT_SYSTEM_PROMPT` **整个失效**（`webui.get("system_prompt") or ...`）。
技能是独立拼进 payload 的，不受这个覆盖影响。见 §8 坑 26。

现在只有 `figure-first` 一个是硬性的。

### 3.4 打包与验收（发布流程，别省步骤）

**默认就用这一条**（打完 `dist\` 里是**三样**，见 `AGENTS.md` §1）：

```bat
.\python\python.exe scripts\make-release.py        :: exe + 干净 zip + 带密钥 zip
```

产出三个文件（当前 `1.1.0-beta11`，每个约 176 MB）：

| 文件 | 干什么用 | 能不能发出去 |
| --- | --- | --- |
| `dist\study7agent-<版本>.exe` | 同一份单文件 exe，**本机试跑用** | 不发 |
| `dist\study7agent-<版本>.zip` | 对外分发：exe + 中文「使用说明.txt」 | ✔ 这个是给老师的 |
| `dist\study7agent-<版本>-deepseek.zip` | 同一个 exe，**多一份预填好的 `config\agent.json`** | 只给指定机器 |

**为什么发 zip 不发裸 exe**：IM / 网盘 / 邮件对 `.exe` 基本都拦，zip 才发得出去；
zip 里还能顺手放一份中文使用说明。**两个 zip 里的 exe 字节完全相同**，差别只在那份配置。

单项工具（要指定版本，或者只想打中间产物时才用）：

```bat
.\python\python.exe scripts\make-release.py 1.1.0-alpha1                     :: 指定版本号
.\python\python.exe scripts\make-release.py --api-key sk-xxx                 :: 临时换一把发布密钥
.\python\python.exe scripts\make-exe.py                                       :: 只打 dist\study7agent-<版本>.exe
.\python\python.exe scripts\make-exe.py --payload build\payload-<版本>.tar.gz   :: 只改了外壳，复用上次的 payload（秒级）
.\python\python.exe scripts\make-exe.py --no-payload                         :: 顺手把用完的 payload 删掉
.\python\python.exe scripts\make-package.py                                  :: 目录版 zip（开发件，给客机 -Full 用）
```

`make-release.py` 负责"三样都打"+"密钥从哪来"（`config\release-key.txt` 第一行，
可用 `--api-key` / `W7A_RELEASE_KEY` 覆盖）+ **打完自己扫一遍**：exe 按字节扫、zip 逐个成员扫，
命中 `sk-` 就失败。1.1.0 起发布产物是单文件 exe：程序树打成 tar.gz 追加在 exe 尾部，
启动器首次解到「程序目录」或 `%ProgramData%\Study7Agent`（**不解到用户目录下**，
那儿内置内核起不来，见 `docs\embedded-browser.md` §6）。

`build\payload-<版本>.tar.gz` 是 `make-exe.py` 的中间产物，**默认留着**（下次改外壳秒级复用），
确认不想要了再 `--no-payload`。

"发哪些文件"的规则仍然只有一份，在 `make-package.py`：
`INCLUDE_DIRS = webui, skills, docs, python`；`EXCLUDE_DIRS` 含 `logs/data/study7agent-src/__pycache__`；
`EXCLUDE_FILES` 含 `agent.json / webui.json / desktop.ini / checksums.txt`。
**`config\` 整个目录不在打包范围里**，所以 `config\release-key.txt` 永远不会进包。

**验收配方**：

1. `make-release.py` 出 exe + 干净 zip + 带密钥 zip 三样；
2. **干净 exe 和干净 zip 里不能有任何 `sk-` 串，带密钥包里密钥只能出现在 `config/agent.json`**
   （两条命令在 `AGENTS.md` §1，`make-release.py` 自己也扫，`TestSecretScanner` / `TestPackagingScript` 盯着它）；
3. **上真 Win7 客机**：`powershell -File scripts\vm-acceptance.ps1`（快检，约 10 秒；客机没开机它自己
   `vmrun start ... nogui`），验内置内核在 Win7 上开得出界面；
4. 需要全量自检时：`-Full`（客机，约 6 分钟，会增量同步变化的文件），或在任意机器上让 exe 自己解包一次，
   用包内 python 跑 `webui\test_webui.py`；客机上没有解压好的树就加 `-Extract` 强制重灌；
5. 需要时在真 Win7 一体机上再看一遍版式（§9 的无头截图办法）；
6. 同步镜像：`robocopy <工作树>\webui study7agent-src\study7agent-main\webui /MIR /XD data __pycache__`
   （`docs`、`scripts`、`skills`、`README.md`、`AGENTS.md`、`agent.ico`、`agent-icon.png` 同理）；
7. 删除工作树里所有 `tmp-*`；
8. `dist\` 里**只留当前版本那三样**，历史 alpha/beta 包删掉（每份 170-180 MB，
   堆着纯占地方，见 §2 的目录说明；`build\` 里的历史 payload 同理）。

---

## 4. 架构与关键机制

```
浏览器(ui/app.js)
   │  POST /api/chat → SSE（text/event-stream，15 秒心跳注释保活）
   ▼
server.py ── App(设置 / SessionStore / run 登记表) ── ThreadingHTTPServer
   │  同一会话同时只能有一个运行；/api/stop 支持 force；停止后 20 秒宽限自动清场
   ▼
core.AgentRun.run()
   ├─ 每轮：_maybe_compact() → 组装 payload（系统提示 + 技能 + 历史 + 45 个工具）
   ├─ client.stream()  ← 流式解析 content / reasoning_content / tool_calls / usage
   │    └─ 出错分类重试：RATE_LIMIT/SERVER/TIMEOUT/TRANSPORT/EMPTY_RESPONSE，最多 5 次，
   │       500ms→10s 退避 + 10% 抖动，尊重 Retry-After；上下文超长单独压缩重试
   ├─ _run_tool_round() → tools.run(name, args)
   │    ├─ authorize()：桌面/消息/命令三组开关 + 硬拦截（blocked_prefixes 永远生效）
   │    ├─ 工具结果超长落盘（spill 24000/8000/4000 字符）
   │    └─ 取消贯穿：tools.cancel / desktop.set_cancel → 杀 PowerShell、杀命令树、断下载
   ├─ 循环守卫：先提醒（3 次）→ 重复到 loop_repeat_limit+22 才停，且一定置 incomplete
   └─ 状态落盘：cancel 之后**不再写会话文件**（防止僵尸运行覆盖新一轮）
```

**悬浮工具栏的信箱**（`/api/float/*`，0.6.0 新增）：

```
工具栏(PS)  --POST /api/float/command-->  服务端
                                        ├─ 外部应用/PPT/截图/板书/时间点：当场做掉
                                        └─ 其余塞进 float_queue
网页(JS)    --POST /api/float/ping----->  取走 float_queue（一次），并报活
            <--{commands:[...]}---------  网页执行：onBoard / onTimer / 填模版 / 附件图片
网页关掉    --POST /api/float/bye------>  把"最后见到网页"往前拨 4 秒
工具栏      --GET  /api/float/state--->  OK|page=1|age=2|ever=1（纯文本，PS 2.0 没有 JSON）
工具栏      --POST /api/float/quit---->  os._exit(0)
```

**前端（`ui/app.js`）的"回答时间线"**——老师明确要求的结构，别再改回去：

- 一条回答内部**按发生顺序**排：`思考一行 → 正文一段 → 工具卡片 → 思考一行 → …`；
- 每段思考用 `streamThink()` 生成**单独一行**（超宽自动右滚，点击展开全文，全文存 `step.raw`）；
- 每段正文用 `streamText()` 生成**单独的 Markdown 块**（不再是"一条到底的流水账"）；
- `streamBreak()` 在**每轮结束**和**开始调工具**时切开；历史渲染（`assistantTurnNode`）走同一条时间线；
- `keepLatestInView()` 保证内容变长时最新一条仍在视野内；
- 右侧栏 `#inspector` 在 **≤1280px 宽**时默认隐藏（CSS `@media`），顶栏柱状图按钮 `#btnInspector` 切换。

**关键常量**（改了要同步自检）：重试 5 次 / 空闲 120s；上下文窗口 `DEFAULT_CONTEXT_WINDOW = 393216`
（可在 `config\agent.json` 写 `"context_window": 65536` 调小）、压缩阈值 0.8 保留 0.16；
工具结果 8192 起裁剪（保留头 4096 + 尾 1024）；单条命令 60s；自检 900s；
图片总量预算 24MB；下载默认 200MB、硬顶 2GB；SSE 心跳 15s；停止宽限 20s。

---

## 5. 真 Win7 虚拟机验证（环境 + 套路）

| 项 | 值 |
| --- | --- |
| vmrun | `D:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe` |
| VMX | `D:\Windows 7 x64\Windows 7 x64.vmx` |
| 客机账号 | `seewo` / `123123qweqwe` |
| 客机系统 | Windows 7 SP1 6.1.7601 x64 + PowerShell 2.0，屏幕 1024×768 |
| 客机里已装好的版本 | 没有固定的旧版本了（2026-09 那次 `C:\w7a53b\` 被清掉了，见下面第 4 条） |

**套路（照抄即可，都是踩出来的）**：

> ⚠️ **别每次都跑全量**：只有"`.ps1` 在 PowerShell 2.0 上能不能跑"这件事非客机不可，
> 那一步只要 **10 秒**。用现成的脚本，别手工重灌包：
>
> ```powershell
> powershell -File scripts\vm-acceptance.ps1                  # 快检，约 10 秒
> powershell -File scripts\vm-acceptance.ps1 -Full            # 全量自检，约 6 分钟（发布前才跑）
> powershell -File scripts\vm-acceptance.ps1 -Full -Extract   # 连解压好的树一起重灌
> ```
>
> `-Full` 会增量同步（只拷变化的那几十个文件），每 20 秒报一次进度、跑完自己退出；
> 客机没开机时它自己 `vmrun start ... nogui`。下面是它内部干的事，手工排查时参考。

1. 宿主写一个**纯 ASCII + CRLF** 的 `.cmd`（原因见 §8 坑 3），`copyFileFromHostToGuest` 送进客机；
2. `runProgramInGuest -noWait "C:\Windows\System32\cmd.exe" /c C:\xxx.cmd`，脚本内部把输出
   重定向到文件（`runProgramInGuest` 拿不到 stdout）；
3. 等几十秒后 `copyFileFromGuestToHost` 把结果/截图捞回来（中文在宿主控制台可能显示成乱码，
   那是显示问题；用 `read` 工具看文件本身是好的）；
4. **客机里解压 zip**（Win7 的 PowerShell 2.0 **没有** `Expand-Archive`）——两条路，
   第二条不依赖客机上已经装了什么：
   - 客机上有随包 python 时（比如上一版解压出来的）：
     `python -c "import zipfile;zipfile.ZipFile(r'...').extractall(r'...')"`；
   - **客机上一个 python 都没有时，用资源管理器自己的解压能力**（COM，Win7 原生支持）：

     ```powershell
     $shell = New-Object -ComObject Shell.Application
     $dest = $shell.NameSpace($Dest); if ($dest -eq $null) { New-Item -ItemType Directory -Path $Dest }
     $dest = $shell.NameSpace($Dest)
     $dest.CopyHere($shell.NameSpace($Zip).Items(), 16)   # 16 = 全部覆盖
     # CopyHere 是异步的：轮询等目标文件出现，别 Sleep 一个固定秒数就往下跑
     while (-not (Test-Path "$Dest\...\webui\server.py")) { Start-Sleep -Seconds 3 }
     ```

   （2026-09 就栽在这：客机上原来的旧版本被清掉了，找不到 `python.exe` 解压。
   现在这套只要求客机有 PowerShell。）
5. **解压后先校验完整性再跑自检**：`webui\server.py`、`webui\ui\index.html`、`python\Lib\encodings`
   都要在（遇到过 VM 断电导致"半截解压"，33 个测试报"文件不存在"，白查半小时）；
6. **`.ps1` 在客机上单独验一遍最省事**：把 `scripts\study7agent-float.ps1` 单独 copy 进去
   （它不依赖别的文件），跑 `powershell -STA -ExecutionPolicy Bypass -File ... -SelfTest`，
   一次就能确认 PS 2.0 语法、BOM、闭包、折叠、本地动作全对，不用等整个包解压；
7. 想看界面时**别指望 `-interactive`**：它会落在 Session 0（没有可见桌面），从服务会话截屏是纯白；
   要在真机上看版式，用 §9 的"宿主无头浏览器"办法。

**已在真机（Win7 客机）验过**：被遮挡/最小化窗口截图、UIA 快照（7–14 个元素，330–420ms）、
`draw_path` 一次画完整幅图（4 笔 13 点 = 1.12 秒；逐段 `mouse_action` = 8.44 秒 / 9 次调用）、
外网下载（6617 字节 PNG）、全量自检通过。

> **但这几项验的是旧底座（"Python 拼 PowerShell"），它已经在 1.1.0-beta9 被整体换掉**（见 §2.3）。
> 新底座（`winapi.py` / `winimage.py` / `uia.py` / 常驻 UIA 工作进程）在开发机（Windows 11）上逐项
> 自检通过：抓屏 39–43ms、标注 +20ms、PNG 85ms、常驻 UIA 快照 0.26–0.45 秒（400 元素上限、130 行）、
> `_point(label)` 按当前界面重算坐标、`draw_path` 多笔一次画完、剪贴板/进程/注册表/启动程序各读一遍。
> **Win7 客机上还没实跑**——上表那几项对**新**底座不构成证据。

---

## 6. 代码地图：想改 X 就去改哪里

| 想做的事 | 去哪 |
| --- | --- |
| 加/改模型工具 | `tools.py`：`TOOL_SPECS`（schema）+ `_tool_xxx()` 实现 + `DESKTOP_TOOLS/MESSAGE_TOOLS` 分组 + `authorize()` 白名单 + `ui/app.js` 的 `iconFor`/`summarizeArgs` |
| 改系统提示词 | `core.py` 的 `DEFAULT_SYSTEM_PROMPT`（分段：工作方式 / 教学场景 / 识图 / 窗口操作 / 搜索 / 回答风格） |
| 改默认设置 | `core.py` 的 `WEBUI_DEFAULTS`（**同时**改 `ui/app.js` 里的兜底数字，见 §8 坑 9） |
| 加设置项 | `core.py`（默认值 + `public_config()`）→ `server.py`（`/api/settings` 白名单）→ `ui/index.html` + `ui/app.js`（读取/保存）→ `TestUISettingsWiring` |
| 桌面能力 | `desktop.py`（动作与查询，上面那层）+ 底下三层 `winapi.py`（ctypes 直连 Win32）/ `winimage.py`（图像与 PNG）/ `uia.py` + `uia_worker.ps1`（常驻 UIA 进程）；改 `uia_worker.ps1` 必须保持纯 ASCII **无 BOM**（§2.3 的两条硬约束） |
| 下载 | `download.py`（有界 + 原子写）；后台任务在 `tools.py` 的 `_start_download_job` |
| 悬浮工具栏 | 界面 `scripts\study7agent-float.ps1`；外部应用查找 `apps.py`；接口在 `server.py` 的 `_float_command` |
| 抽学号大屏按钮 | 前端 `ui/app.js` 的 `boardPick()` + `ui/index.html` 的 `#boardActions`；接口 `/api/classroom/pick` |
| 上下文/压缩 | `context.py` + `core.AgentRun._maybe_compact/compact` |
| **化学实验室** | 引擎 `chem.py`（配平/预测/热化学/实验台仿真）+ `molecule.py`（有机物结构）+ 数据 `chemdata\`（**运行期全离线**，改数据重新跑 `scripts\fetch-chem-data.py`）+ 工具在 `tools.py` + 接口 `/api/chem` |
| **几何画板** | 对象模型 `plane.py`、几何内核 `plane_geom.py`、精确值 `plane_math.py`、撤销重做与事件 `plane_hub.py`；前端 `ui\plane.js`；接口 `/api/plane/*` |
| **物理沙盒 / 模型** | `sim2d.py`（物块/电场/磁场）、`physics.py`、`circuit.py`、`instruments.py`、`practice.py`；前端 `ui\sandbox.js` / `circuit.js` / `practice.js` / `chartui.js` |
| **生物** | `biology.py` + 前端 `ui\bio.js`；接口 `/api/bio` |
| 前端版式 | `ui/app.js` 的 `streamThink/streamText/streamBreak/assistantTurnNode` + `ui/style.css` 的 `.stream` 段 |
| 图标 | `scripts\make-icon.py` → 生成 `agent.ico` / `agent-icon.png` |
| 自检 | `webui\test_webui.py`（加用例时**别动** `TestToolSchemaBudget` 上下界，除非有正当理由并写清注释） |

**HTTP 接口一览**（`server.py` 里一共 **65 个 `/api` 路由**，都在 `server.py`）：

| 组 | 路由 |
| --- | --- |
| 对话与运行 | `/api/chat`(SSE)、`/api/stop`、`/api/state`、`/api/confirm`、`/api/command`、`/api/context`(+`remove`)、`/api/models`、`/api/balance`、`/api/agent/test` |
| 设置与会话 | `/api/settings`、`/api/session`(+`new`/`rename`/`delete`/`clear`/`export`/`rewind`/`model`/`effort`/`skills`) |
| 技能与工作区 | `/api/skills`(+`save`/`delete`)、`/api/workspace`(+`add`/`create`/`open`/`remove`/`rename`) |
| 文件与图片 | `/api/file`、`/api/image`、`/api/image_info`、`/api/upload`、`/api/upload_image`、`/api/open`、`/api/browse`、`/api/snapshots/restore` |
| 悬浮工具栏信箱 | `/api/float/`（`ping`/`bye`/`state`/`command`/`quit`/`config`） |
| 课堂 | `/api/classroom/pick`（抽学号大屏按钮）、`/api/classroom/materials`（工作区素材） |
| **化学实验室** | `/api/chem`（试剂架/配平/反应/热化学/实验台）、`/api/balance` |
| **物理** | `/api/physics`（物块/电场/磁场/沙盒/练习）、`/api/instruments`（读数仪器）、`/api/circuit`（电路）、`/api/practice`（练习模式/错题本） |
| **生物** | `/api/bio`（课本过程动画） |
| **几何画板** | `/api/plane/`（`state`/`apply`/`undo`/`redo`/`events`/`export`/`window`） |
| 计算 | `/api/calc`、`/api/units`、`/api/translate`、`/api/memo`、`/api/calendar`、`/api/figure` |

> 面板路由的对应关系在 `webui\ui\*.js` 里（`plane.js` / `chem.js` / `sandbox.js` / `circuit.js` /
> `bio.js` / `practice.js` / `chartui.js` / `extras.js`），加路由时前端也要跟着改。

---

## 7. 自检里都测了什么（111 个类 / 1025 项）

数字都是现查的（类名 + 该类下的 `test*` 方法数）。关键类：

**骨架与服务**

- `TestBasics`（7）/ `TestSettingsRoundTrip`（2）/ `TestApiKeyFlow`（7）：配置合并、密钥掩码与写回、设置往返；
- `TestHttpServer`（36）：真实 HTTP 端到端——`/api/state`、会话 CRUD、`/api/chat` SSE、
  确认门、`/api/command`、静态资源、路径穿越防护；
- `TestAgentLoop`（13）/ `TestLongRunRobustness`（13）/ `TestNoSilentStops`（10）/ `TestTransportRetry`（9）：
  工具循环、重试退避、上下文压缩、重复提醒、轮次用尽可继续；
- `TestStopActuallyStops`（5）/ `TestStopIsImmediate`（2）：**停止要能打断正在跑的长命令**、
  停止后同一对话立刻可用、僵尸运行不覆盖新一轮；
- `TestDownloads`（16）/ `TestParallelDownload`（3）/ `TestBigFileGuards`（3）：真 HTTP 下载、限量/截断、
  原子落盘、后台任务与取消、文件名与类型判定。

**桌面控制四层底座（1.1.0-beta9 之后）**

- `TestUiaProtocol`（34）：常驻 UIA 工作进程协议——字段分隔、令牌失效、只读约束、
  失败不静默降级、超时自动重启；
- `TestDesktopTools`（51）：`winapi`/`winimage` 动作层（截屏、窗口、键鼠、剪贴板、PNG 编码…）；
- `TestDesktopPolicy`（7）/ `TestDesktopLive`（7）：桌面授权与 `W7A_NO_INPUT` 拦截；
- `TestClickPointFreshness`（7）：点之前必须重新取快照，点位不能过期。

**前端静态检查**

- `TestUIFiles`（32）/ `TestUISettingsWiring`（6）：元素 id 对齐、图标存在、**Win7 不可见字符**、
  时间线结构、设置接线；
- `TestNoEncodingDamage`（3）/ `TestChineseEncoding`（4）：全仓扫描 U+FFFD 与 GBK 乱码特征（见 §8 坑 1）；
- `TestDshOneLineOutput`（4）/ `TestLongChatWindow`（1）/ `TestTurnProcessFold`（5）：回答时间线的单行结构；
- `TestWin7Gate`（2）：`scripts\win7-gate.py` 本身不许空跑。

**工具与技能**

- `TestWorkspaceAndSkills`（21）/ `TestSessionSkillsDefaultOn`（2）：工作区切换、技能增删改查、
  关键词命中不误伤，以及**硬性技能**（`always: true`）——不管输什么都注入、会话关不掉、
  `/api/skills` 带 `always` 字段、`figure-first` 里必须写明 show_figure / draw_path / 希沃白板；
- `TestToolSchemaBudget`（3）：工具数 ≥28（`TOOL_FLOOR`）、schema ≤32000 字符
  （`SCHEMA_CEILING`，当前 **45 个工具 / 30170 字符**，涨价记录写在用例注释里）；
- `TestFileTools`（14）/ `TestOfficeFormats`（9）/ `TestCommandPolicy`（6）/ `TestCommandPolicyBypasses`（3）/
  `TestWindowsPathHazards`（12）/ `TestReadFileBroadening`（6）：文件、课件、命令授权与 Win7 路径雷区；
- `TestSelfModificationAndLimits`（21）：自我修改闭环（`edit_file` → `self_check` → `restart_service`）与限额。

**化学实验室**

- `TestChemEngine`（18）/ `TestChemThermo`（5）/ `TestChemSimulate`（26）/ `TestChemToolAndApi`（6）：
  配平、反应预测、热化学、实验台仿真（含可逆反应停在平衡上）；
- `TestChemDataExport`（4）/ `TestChemStructures`（4）/ `TestChemCommonStructures`（5）/ `TestChemPanelWiring`（7）；
- `TestMolecule`（14）/ `TestChemLab`（16）：有机物结构与实验台面板接线。

**物理 / 生物 / 几何画板**

- `TestPlaneCore`（19）/ `TestPlaneCurves`（11）/ `TestPlaneGeometry`（17）/ `TestPlaneTools`（17）/
  `TestPlaneReplacesOldFigure`（12）/ `TestPlaneHitTest`（1）/ `TestPlaneShapeStrokeWidth`（1）/
  `TestPlaneConstructions`（15）/ `TestSnapDetent`（1）：几何画板对象模型、依赖联动、曲线、
  中垂线/平行线/角平分线/三心（含工具条顺序守卫）、M17 的线宽修复；
- `TestSim2D`（10）/ `TestSim2DLinked`（14）/ `TestSandboxBodiesUI`（10）/ `TestForceVectors`（7）/ `TestPhysics`（7）/ `TestCircuit`（6）/ `TestCircuitMore`（6）/
  `TestInstruments`（7）/ `TestPractice`（4）/ `TestPracticeFromExamples`（7）/ `TestPhetBundle`（4）：
  沙盒、力矢量、各物理模型、电路、读数、练习模式与错题本、随包 PhET；
- `TestBiology`（10）/ `TestImplicitGliderAndPointDrag`（13）：生物模块与作图交互。

**作图与图表**

- `TestPlot`（54）：表达式求值表（含 `2x`、`-x^2`、`2^3^2`、全角括号、`y = ...`）、
  报错要说得清、**源码里不许出现 eval/exec**、`1/x` 必须断成两支、
  几何语法与中文别名、几何报错要指到具体那个点、`/api/figure`、
  `show_figure` 推事件到网页、`/api/classroom/materials` 只列工作区里的三个目录；
- `TestChart`（6）/ `TestCircuitChart`（8）/ `TestSim2DChart`（6）/ `TestMultiCurveAndExport`（5）/
  `TestKeyPoints`（6）/ `TestTracePointsStream`（1）。

**工具栏 / 打包 / 杂项**

- `TestFloatToolbar`（17）：心跳报活、`bye` 把时钟往前拨又能被刷新收回、命令只发一次、
  动作不认识时回 `ERR`、失败的动作不排命令、`/api/float/quit` 真的会去结束进程、
  拉起工具栏的命令行在没有令牌时**不能带 `-Token`**、
  **AI 画完图不许抢焦点**（`/api/float/focus` 这条链路已经删掉，用例盯着它别回来）、
  `GET/POST /api/float/config` 能改 QQ 下载目录、目录不存在要报 400；
- `TestFloatModuleButtons`（3）/ `TestFloatScript`（5）/ `TestFloatTopBarOnlyDrags`（4）：`.ps1` 必须随包 + **UTF-8 带 BOM**（扫 `scripts\*.ps1`
  全部文件）、不许出现 PowerShell 3 才有的语法、
  **不许出现"参数名被同名赋值覆盖"**（PowerShell 变量名不区分大小写，见 §8 坑 27）、
  按钮覆盖服务端认识的全部动作、**切窗口不许再用 `AppActivate`**（对最小化窗口直接返回 False）、
  进程名里必须有 `wps`（WPS 12 单 exe，没有 wpp.exe）、打开外部应用**先切后启动**、
  **真跑一遍 `-SelfTest`**（客机上就是 PS 2.0 把关：能建出窗体、`GetNewClosure` 抓对了
  每个按钮的 Key、两个本地动作接对了、折叠/展开的宽度对、时钟有值、
  `find=True`（真的去调 `Find-AppWindow` 认窗口）、中文字面量的码点没被解码错）；
- `TestExternalApps`（5）/ `TestClassroomPickButtons`（5）/ `TestClassroom`（14）：
  `config\float.json` 的 `apps` 段覆盖优先、坏配置不致命、
  找不到应用时给可操作说法、**扫工作区找 pptx 的那套死代码必须删干净**（PPT 改成切窗口了）、
  抽学号两个按钮的接口（不重复、重置只清空不抽、生成中拒绝改动 409）；
- `TestPackagingScript`（7）/ `TestExePackaging`（8）/ `TestLauncherKernel`（14）/ `TestAutostart`（8）/
`TestAutostartApi`（2）/ `TestAutostartSingleFile`（6）/ `TestSecretScanner`（4）/
  `TestFrontendStartup`（4）/ `TestFetchScripts`（8）：干净包无密钥、预填密钥只落在一处、
  **`config\` 不许进 `INCLUDE_DIRS`**（否则 `release-key.txt` 会跟着干净包出厂）、
  单文件 exe 尾部的戳/魔数、内置内核、**发布脚本自己要随包发布**（不然包内自检第一步就
  FileNotFoundError）、两条 `sk-[A-Za-z0-9]{16,}` 扫描；
- `TestCalc`（8）/ `TestCalcApi`（3）/ `TestMemoCalendarAndTranslate`（11）/ `TestNotify`（7）/
  `TestMailHardening`（4）/ `TestWebSearch`（8）/ `TestMediaReport`（13）/ `TestAudit`（4）/
  `TestCommunityHardening`（19）/ `TestThinkSplitter`（9）/ `TestConsoleSafety`（3）/ `TestSubAgent`（3）。

> 类名和每类的用例数会随改动变，**以 `webui\test_webui.py` 里现查的为准**：
> `.\python\python.exe -c "import sys,unittest; sys.path.insert(0,'webui'); import test_webui as t; print(unittest.defaultTestLoader.loadTestsFromModule(t).countTestCases())"`
> 这里写的是 1.1.0-beta11 那一刻的数字（111 个类 / 1025 项）。

---

## 8. ★ 踩过的坑（**这一节最值钱，逐条都付过代价**）

1. **绝对不要用 PowerShell 改源码文本**。`Get-Content -Raw` 在中文机器上按 GBK 读、
   `Set-Content -Encoding UTF8` 再写回 → 整个文件的中文变乱码且**不可逆**
   （`core.py` 丢过 1736 个字符，最后从已打包的 zip 里恢复）。用能正确处理 UTF-8 的编辑器/工具。
   仓库里有 `TestNoEncodingDamage` 专门盯这个。
2. **`webui\ui\*` 里不许出现 `→`(U+2192)、emoji、几何符号**：Win7 没有对应字形，会显示成空白/方框。
   `TestUIFiles.test_no_win7_invisible_glyphs` 会挡住（连续踩过三次，注释里也算）。
3. **给客机写的 `.cmd` 必须是纯 ASCII**：cmd.exe 按 OEM 代码页（中文机器是 GBK）解析批处理，
   文件里的 UTF-8 中文注释会让脚本**一开始就崩**（`start-webui.cmd` 顶部就写着这条）。
4. **PowerShell 2.0 的限制**：没有 `ConvertTo-Json`/`ConvertFrom-Json`、没有 `::new()`、
   没有 `[pscustomobject]`、没有 `-in`/`-notin`；**整段脚本先解析**，所以 `Add-Type` 必须写在
   任何 `[System.Windows.Automation.*]` 之类类型字面量**之前**（否则报"找不到类型"，而且错误以
   CLIXML 出现在 stderr，很难看出真因）。脚本统一用 `-EncodedCommand`（UTF-16LE+base64）传参，
   约定最后打印 `OK|...` / `ERR|...`，Python 只解析这一行。
5. **UIA 的 `BoundingRectangle` 可能是 `Rect.Empty`（坐标 ∞）**：直接 `[int]` 转换会抛
   `Cannot convert value "∞" to type "System.Int32"`。先判 `IsEmpty/IsNaN/IsInfinity`。
6. **元素名字里可能有换行**：会让"按行解析"的输出错位；`W7A-CleanText` 负责压平与截断。
7. **`SetForegroundWindow` 对后台进程会被静默拒绝**：不验证就会出现"报告成功但什么都没输入"。
   `focus_window()` 会验证前台窗口并把实际前台窗口报回来。
8. **Windows 路径陷阱**：`os.path.abspath(r"C:\x\CON.md")` 会变成 `\\.\CON`（设备路径）！
   统一走 `safe_abspath`/`long_path`（加 `\\?\`）+ 保留设备名校验；`list_dir` 里每个条目也要过一遍，
   否则 `CON.md` 这类文件会被静默丢掉。
9. **设置界面"保存"会把所有字段一起写进 `config\webui.json`**，于是**当时的默认值被永久钉死**——
   后来改默认值对这些机器完全无效（老师反馈"动不动就熔断""干一会儿就停"，根因就是配置里钉着
   `max_tool_rounds: 8` / `max_run_seconds: 900`）。现在 `core.LEGACY_WEBUI_DEFAULTS` 会在加载时
   把"正好等于旧默认值"的字段迁移成新默认值，并写 `logs\config-migration.log` + 界面提示一次。
   **加新设置项时，`ui/app.js` 里的兜底数字必须与 `WEBUI_DEFAULTS` 一致。**
10. **取消（停止按钮）必须贯穿到工具层**：只在"轮次之间"检查的话，正在跑的长命令/下载/界面查询
    根本不理它，界面就会"停了还在跑、下一条消息被 409 挡住"。现在的链路是
    `server /api/stop(force)` → `AgentRun.cancel` → `tools.cancel` + `desktop.set_cancel`
    （线程局部）→ `_run_shell(cancel=)` 杀进程树、`uia.set_cancel` 让常驻工作进程放弃当前查询。
11. **`W7A_NO_INPUT=1` 是自检/测试的硬开关**：它拦鼠标键盘/快捷键（含多媒体键）/拖动/
    `multi_*`/`draw_path`/`type`/`clipboard` 写入。
    但**参数校验要放在它前面**（否则"坐标超范围""不认识的键名"这类真实报错会被它盖住）。
12. **图片注入必须发生在整个工具轮之后**：插在工具结果中间，DeepSeek 会 400
    `assistant message with 'tool_calls' must be followed by tool messages`；
    另有 `repair_tool_pairs()` 兜历史。**思考模式下每条带 tool_calls 的 assistant 消息都要回传
    `reasoning_content`**（空字符串也要有，字段缺失就 400）。
13. **工具 schema 每轮都会重发**：加工具要算进"提示词预算"（当前 **30170 字符** ≈ 12k token）。
    上界在 `TestToolSchemaBudget.SCHEMA_CEILING = 32000`，涨价要在注释里写清"为什么值这个钱"。
14. **打包件自检是发布验收**：`make-package.py` 会把 `scripts\make-package.py` 自己也打进包
    （包内自检要读它）；少打一个文件，解压后跑自检就直接 `FileNotFoundError`。
15. **密钥纪律**：`config\agent.json`（含 API Key）、`config\webui.json`、`webui\data`、`logs`
    永不进包；`-deepseek` 包**故意**只含一个 `config\agent.json`，用来给特定机器预填。
16. **`.ps1` 必须存成 UTF-8 带 BOM，而且"能编辑 UTF-8"还不够**：PowerShell 2.0（和 5.1）
    读**不带 BOM** 的 `.ps1` 会按本地代码页（中文机器是 GBK）解码。这时脚本里的中文会变乱码，
    更糟的是**乱码字节里可能出现引号或花括号**，于是整份脚本报一堆
    "Unexpected token '}'"/"Missing closing '}'"，错误行号还会指向完全无关的地方
    （踩过：一个只在头部加了图标的改动把 BOM 吃掉了，报错却在 113 行的函数上）。
    **很多编辑器/工具保存时会静默丢掉 BOM**——改完 `.ps1` 一定要重新确认前三个字节，
    最快的办法是跑 `python\python.exe webui\test_webui.py TestFloatScript`；
    补 BOM 的一行命令：
    ```bat
    python\python.exe -c "p=r'scripts\study7agent-float.ps1';t=open(p,'rb').read().decode('utf-8-sig');open(p,'wb').write(b'\xef\xbb\xbf'+t.encode('utf-8'))"
    ```
    这和 §8 坑 1（别用 PowerShell 改源码）是同一件事的两面。
17. **PowerShell 2.0 没有 `ConvertFrom-Json`**：所以 `/api/float/*` 一律返回
    `OK|消息|focus=0/1` 纯文本，沿用 `desktop.py` 已经用了很久的那套约定。
    消息本身**不能含 `|` 或换行**，否则工具栏按 `|` 一切就错位（服务端会先把它们替换掉）。
    反过来，PowerShell 的 `-File` 绑定器**不接受空的参数值**：`-Token ""` 会让整个脚本
    以 "Missing an argument for parameter 'Token'" 启动失败。本机模式本来就没有令牌，
    所以那个参数必须**整个省掉**（`server.float_toolbar_command` 就是这么拼的，
    有专门的用例盯着；这个坑在宿主上真踩过一次）。
18. **PowerShell 事件处理块不要靠循环变量**：`foreach` 里给按钮挂
    `{ 用 $key }` 会全部指向最后一次循环的值。必须 `.GetNewClosure()`
    （PS 2.0 就有），或者把处理逻辑写成脚本级函数、事件里只留一行调用。
    拖动条那三个处理函数就是这么拆的（`Start-Drag` / `Move-Drag` / `End-Drag`）。
19. **`_authorized()` 以前看的是配置文件里的 host，不是真正绑定的地址**：
    `start-webui.cmd --host 0.0.0.0` 会覆盖配置，于是服务监听在局域网上、
    界面上还打印了带令牌的地址，但**任何请求都不用带令牌**——同网段谁都能
    `POST /api/command` 执行本机命令。现在 `build_server` 把真实绑定地址记在
    `server.bind_host` 上，`_authorized` 只看它；而且非本机监听 + 空令牌直接 401。
20. **界面改会话必须避开正在跑的 AgentRun**：`AgentRun` 手里攥着一份会话副本，
    运行结束时**整份写回**。这期间 `/api/context`（"文件"按钮）、`/api/session/clear`、
    `rewind`、`/api/session/skills` 改的东西会被它冲掉，老师看到的就是"刚加的文件不见了"。
    现在这些接口在 `session_busy()` 时直接回 409 并说明原因（`_reject_if_busy`）。
    另外 `/api/chat` 的 `new_run` 登记改成了"写响应头失败也要 `end_run`"，
    否则会留下一个 `stopping_at=0` 的僵尸运行，那个对话会永远被判"正在生成中"。
21. **`WScript.Shell.AppActivate` 对最小化的窗口直接返回 False 而且什么都不做**。
    真机上量过：把 WPS 演示最小化以后 `AppActivate($pid)` 返回 `False`、窗口纹丝不动，
    老师看到的就是"点了切到PPT没反应"。正确做法是 `ShowWindow(hwnd, SW_RESTORE)` +
    `SetForegroundWindow`，配 `AttachThreadInput` 解前台锁（只 attach 自己没用，
    得把**当前前台线程**和目标线程一起接上），而且 `ShowWindow` 是异步的，
    所以要**重试并验证 `GetForegroundWindow() == hwnd`**。
    全部实现在 `scripts\study7agent-float.ps1` 的 `W7AWindow` 类里，`TestFloatScript` 挡着不让 AppActivate 回来。
    实测结论（宿主）：同一个最小化窗口，`AppActivate` → False，上面这套 → True。
22. **WPS 12 是单 exe**：`.pptx` 的打开命令是
    `"...\WPS Office\12.x\office6\wps.exe" /prometheus /wpp "%1"` —— **根本没有 `wpp.exe`**。
    进程名是 `wps`，跟 WPS 文字、表格是同一个 exe。所以"切到PPT"的进程名名单里
    必须同时有 `wps` 和 `wpp`，只写 `wpp` 的话老师在 WPS 里打开的课件永远切不过来
    （`$Script:PresentationNames`）。标题命中优先、进程名兜底，能少认错人。
23. **无边框 WinForms 窗口没有轮廓**：`FormBorderStyle=None` 的置顶小窗如果不自己画一圈边框、
    不做圆角，贴在浅色背景上会糊成一片、看着很"低级"。全部用 `Region` + `GraphicsPath`
    手工削（`New-RoundRegion`），窗体尺寸变了要重算 Region（`Set-FormRegion`）。
24. **"最小化"有两种，托盘的比任务栏的难缠得多**。QQ 点右上角 X 是**藏进托盘**：
    `IsWindowVisible` 全变 0，只枚举可见窗口的话**根本找不到它**，老师看到的就是
    "点了打开QQ没反应"。两处都要改：枚举时**不筛可见性**（挑候选时用窗口面积把
    `32x38` 那种辅助窗排除），激活时先 `ShowWindow(h, SW_SHOW)` 再 `SW_RESTORE`。
    实测：托盘里的 QQ，这套 → `True`；只 `SW_RESTORE` → 找不到窗口。
25. **`os.startfile` 一个已经在跑（且收在托盘里）的程序会再启动一次**——QQ 这种单实例
    程序会**弹出一个新的登录窗口**。所以"打开某某程序"必须**先切、切不到才启动**，
    不能先启动再切。这个顺序反了，老师看到的是"我要的是那个开着的 QQ，你给我弹个登录框"。
26. **老师在 `config\webui.json` 里改过 `system_prompt` 的话，`DEFAULT_SYSTEM_PROMPT` 整个失效**
    （`webui.get("system_prompt") or DEFAULT_SYSTEM_PROMPT`）。所以"这条规矩必须一直生效"
    的东西**不能只写在默认提示词里**——真机上就栽在这：提示词里写了"画图用 show_figure"，
    老师机器上还是去开希沃白板。要一直生效就写成**硬性技能**（`skills\*\SKILL.md` 的
    frontmatter 加 `always: true`），技能是独立拼进 payload 的，不受这个覆盖影响。
27. **PowerShell 变量名不区分大小写，参数会被同名局部变量一声不吭地清空**：

    ```powershell
    function Find-AppWindow {
      param([string[]]$Names)      # 参数 $Names
      $names = @()                 # ★ 这就是同一个变量！参数被清空了
      foreach ($name in $Names) { $names += $name.ToLower() }   # 遍历的是空的自己
    ```

    结果这个函数**永远返回 0**：窗口明明在、进程名也对，就是找不到。
    后果是「打开QQ」走到"没开着"分支又启动一次（**弹出新的登录窗口**），
    「切到PPT」也从来没找到过 WPS——只是它接着去打开了最新课件，**看起来像是切成功了**。
    界面照建、按钮照点、`-SelfTest` 照样 OK，光读代码根本看不出来。
    两个守卫拦它：`TestFloatScript.test_no_powershell_case_collision`（静态扫描
    `scripts\*.ps1` 里"参数名 = 同名赋值"），以及 `-SelfTest` 里的 `find=True`
    ——**拿工具栏自己的窗口当靶子实测 `Find-AppWindow`**，只看代码里有这个函数不算数。
    教训：这种"函数返回 0 但不报错"的 bug，必须有一个**会真的去调它**的检查，
    否则静态检查再全也白搭。
28. **`AttachThreadInput` 是有毒的，别顺手拿来解前台锁**。把两个线程的输入队列接在一起之后，
    **没拆干净（或者 attach 到"当前前台线程"再出点岔子），对方的输入队列会被挂住**——
    表现就是"**那个窗口点不动了、最小化和关闭都没反应**"。真机上出过。
    现在的写法：先走**完全不碰输入队列**的正常路径（工具栏被点的时候它自己就是前台窗口，
    `SetForegroundWindow` 本来就允许），只有这条走不通才 attach，而且
    **只 attach 自己和目标线程**（不碰前台线程）、**`try/finally` 保证一定拆开**。
    守卫：`TestFloatScript` 里禁止 `fgThread` 出现。
29. **切到前台的窗口可能一大半在屏幕外，老师连关闭按钮都点不到**。真机上：QQ 恢复到
    1536x864，却摆在 `(48,21)`——右边缘 x=1584、底边 y=885，而屏幕只有 1536x864，
    于是"打开的 QQ 窗口无法最小化和关闭"。所以 `Focus-WindowHandle` 切完**顺手
    `Clamp()` 一下**：超出屏幕就缩进屏幕内，位置超出就挪回来（最大化/最小化的不动）。
    这类"窗口在、但够不着"的问题，光看日志和 API 状态是发现不了的——
    `IsWindowVisible=1`、`IsHungAppWindow=0`，一切正常，就是点不到。
30. **"切成功"不能只看 `GetForegroundWindow() == h`，日志更不能只说"成功"**。
    真机上出现过最坑的一种：日志一路写 `ppt -> 已切到 PPT`（判定全过），老师那边
    **什么变化都没有**——切到的可能是个助手窗、0 尺寸窗、或者恢复没生效的最小化残影。
    三条一起改才治得住：
    1. **成功判定要硬**：`Usable()` = 前台是它 **且** 没最小化 **且** 可见 **且**
       尺寸 ≥160x120 **且** 不在 `-10000` 那个最小化停放点；
    2. **候选要挨个试**：一个程序往往有好几个顶层窗口（`Find-AppWindows` 按
       标题命中 → 可见窗口 → 隐藏窗按面积排序），
       用第一个真的能用的，而不是赌第一个；
    3. **日志要写清切到的是哪个窗口**：`[W7AWindow]::Describe($handle)` 打出
       hwnd/pid/vis/min/size/位置/标题。没有这一行，上面那个 bug 根本没法定位。
31. **注册表 Run 值必须带引号，裸路径遇到空格就起不来**。老师那台一体机"开不了机自启"的根因就是这个：
    `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 里写的是 `D:\...\Study7Agent.exe`，
    Windows 启动时按空格断词成 `"D:\Program"` 之类，当然失败。现在写 **`"完整路径" --autostart`**，
    并往 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\` 放一个 .lnk 当第二保险；
    "任务管理器里被禁用了"要看 `...\Explorer\StartupApproved\Run` 的二进制首位，不能只看 Run 里有没有值。
    写完**一律回读验证**；测试用临时键 `Software\Study7AgentSelfCheck2`，不许碰真键（`TestAutostart`）。
32. **自启动档不许抢前台，也不许弹主窗口**：`--autostart` 分支只起悬浮工具栏；已经有实例在跑时
    **只记一行日志就退出**，绝不再发一次 Show 信号；悬浮栏要等桌面就绪（explorer + `Shell_TrayWnd`）
    再起，否则开机瞬间会起在任务栏前面、老师找不到。这些都是老师"开机自启之后别自己跳出来"的落地。
33. **SVG 画布必须显式写 width/height，只给 `viewBox` 会被裁**。浏览器按 viewBox 比例自算高度（514px），
    而容器只有 264px 且 `overflow:hidden`，下半截（地面/轨道/小车）**悄悄被裁掉**，看起来像"画错了"。
    画之前先量容器尺寸再写宽高，缩放时按容器宽高比补边。`TestSandboxBodiesUI` 盯着这条。
34. **"引擎支持"不等于"界面能点到"**：中垂线/平行线/角平分线服务端早就实现了，工具条上没有按钮，
    老师的结论就是"没有这个功能"。加/审功能时先把全仓 grep 一遍，看引擎层已经有什么，再决定改哪一层。
35. **用 `edit` 工具改 `.ps1` 会吃掉 UTF-8 BOM**：改完必须用 Python 按字节补回（`codecs.BOM_UTF8`），
    否则 Win7 的 PowerShell 2.0 按 GBK 读中文，按钮文字全变乱码；`TestFloatScript` 会扫 `scripts\*.ps1`
    的 BOM，别等它报警。
36. **单文件发布版的 exe 名字带版本号，开机自启不能死找 `Study7Agent.exe`**。启动器原来只把
    `--base-dir` 传给服务，`core.autostart_exe` 就找 `base_dir\Study7Agent.exe`；可发布件里只有
    `study7agent-1.1.0-beta11.exe`，于是「开机自启」永远显示"这一版不用设"，**发布说明却写着已打开**
    ——功能还没生效就先被文档宣告完成。现在启动器加 `--exe-path "<Application.ExecutablePath>"`，
    `server.py` 一路透传到 `core.Settings(exe_path=...)`，`autostart_exe / autostart_state /
    set_autostart / ensure_autostart / autostart_diagnose / repair_autostart` 全部带上 `exe_path`
    （目录版才回退到固定名）。`TestAutostartSingleFile`（6）用带版本号的文件名盯着，改自启先跑它。

---

## 9. 宿主侧小技巧（不碰鼠标也能验界面）

想验证前端版式又不想动鼠标、也不想上虚拟机，用这个办法（本项目用过，很灵）：

1. 写一个临时 `tmp-ui-serve.py`：把 `webui\ui\` 用 HTTP 提供出来，并且**在真实 `index.html` 的
   `<script src="app.js">` 前面插一行 `<script src="/__stub.js"></script>`**；
2. `__stub.js` 里覆盖 `window.fetch`：`/api/state` 返回最小配置（模型已配置、一个空会话），
   `/api/chat` 返回**脚本化 SSE**（思考 → 工具 → 再思考 → 正文 → 最后一次总结），
   并反复点 `#btnSend` 直到真的发出去；
3. 用宿主已有浏览器无头截图：
   `msedge.exe --headless=new --window-size=1505,1400 --virtual-time-budget=40000 --screenshot=out.png http://127.0.0.1:PORT/`；
4. 想**精确核对 DOM 顺序**，就在桩里把 `#thread .stream` 的子节点打成清单（`[思考]/[正文]/[工具]`）
   显示在页面上，截图一看便知——比肉眼猜哪一行是哪一段可靠得多。

---

## 10. 当前状态、已知短板、建议的下一步

**当前版本：1.1.0-beta11**（`webui\core.py` 的 `VERSION`）。已交付并自检通过的能力，
按阶段编号记在 **`docs\roadmap-1.1.0.md`**（M0–M20，做完一阶段就更新那一份）。三件事：

1. **化学实验室**（M3–M5 + M12）：试剂架 **201 瓶**（含 41 种有机物、9 种催化剂、50 种盐溶液，
   按"阳离子 × 阴离子"成套生成）、可逆反应按 ΔrG°→K 二分求反应进度、
   恒容反应釜真实停在平衡上；`chemdata\` 运行期全离线。
2. **物理**（M6–M8 + M13.4）：几何画板 + 物块轨道 / 电场 / 磁场 / 电路四大块 + 沙盒 + 练习模式与错题本
   + 读数仪器 + 3 个内置 PhET 仿真（离线可用）。
3. **生物**（M10）：**54 个**课本过程的分步动画（`biology.PROCESSES`，覆盖 6 章、25 个互动仿真），
   悬浮工具栏有「生物」按钮直接开。

再加上 M18/M19 换掉的桌面控制底座（Windows-MCP 形状四层，`§2.3`）和内置 chromium-109 内核。
自检 **111 个类 / 1025 项**（`§7`）。

**M20（1.1.0-beta11）按老师报的四件事改的**：①Win7 开机自启动（注册表启动项带引号 + 启动文件夹 .lnk
双保险，设置里加「开机自启动诊断 / 一键修复」，自启动档只起悬浮栏不弹主窗口）；②点悬浮栏图标/顶栏
**只展开，不切前台**，唯一入口是「显示界面」；③物理沙盒加了板块/轮子/圆弧/弹簧/绳杆，能跑老师那张
小车 + 1/4 圆弧 + 单摆撞物块的整道大题（逐项误差 ≤1%）；④作图面板补齐并重排中垂线/平行线/角平分线/三心。
细节见 `docs\roadmap-1.1.0.md` 的 M20 分节。

**M20a 的补充（beta11 发布后才发现，已改）**：上面①的自启在**单文件发布版**里其实一次都没设上——
启动器只传 `--base-dir`，`core.autostart_exe` 死找 `base_dir\Study7Agent.exe`，而发布件叫
`study7agent-1.1.0-beta11.exe`，于是诊断一直说"这一版不用设"。修法：启动器加
`--exe-path "<Application.ExecutablePath>"`，`server.py`（`App`/`build_server`/`main`）透传，
`core` 的六个自启函数与 `Settings` 都认 `exe_path`，目录版才回退固定名；新增
`TestAutostartSingleFile`（6）用带版本号的 exe 名验证写注册表/写 .lnk/自愈/诊断。见 §8 坑 36。

**更早的历史版本**（0.6.0 悬浮工具栏与作图面板、0.5.3 时间线版式与自我修改闭环）都已合入上面这套，
它们的细节留在 `§2.1`/`§2.2`/`§8` 各段里当历史记录看。

**已知短板（诚实清单）**：

- `agent.exe` 内嵌的仍是旧图标（换 `agent.ico` 不会改 exe 内部资源，要重建 Go 产物）；
- 悬浮工具栏的按钮是**中文文字**，不是应用那套内联 SVG 图标（.NET 3.5 没有 SVG 渲染器）；
  只有窗口图标用的是 `agent.ico`；
- 「切到PPT」只认 `POWERPNT` / `wps`（WPS 12 是单 exe，见 §8 坑 22）/`wpp` 这几个进程名和几个标题关键词；
  学校用别的演示软件（比如某些定制播放器）时要往 `$Script:PresentationNames` 里加一行；
- 几何画板已能导出图片（`/api/plane/export`，`TestMultiCurveAndExport` 盯着），但**单个表达式图**
  （旧作图面板 `show_figure` 那条路）仍没有导出按钮；老面板的取舍见 §2.2 的历史段落；
- 几何画板现在有中垂线/平行线/角平分线与三心（带课本式记号），但还没有「只画不参与联动」的参考辅助线；
  外接圆的拖动仍走服务端，没有像三心那样在客户端本地现算；
- 「课堂标记」只负责**记录**（时间 + 当屏截图 + `课堂时间点.json`），还没有界面来回看时间线；
- 前端版式只在**宿主机的 Chromium 引擎**里渲染核对过，**没有**在 Win7 的浏览器里亲眼看过
  （`runProgramInGuest -interactive` 落到 Session 0，服务会话截屏是纯白）；
- `TestHttpServer` 在宿主满负荷时有**偶发**失败（单独重跑必过），怀疑是并发连接时序，未根治；
- 下载**不支持断点续传**（Range），后台任务只活在进程内（重启即丢）；
- 桌面控制的**新底座**（`winapi.py` + `winimage.py` + `uia.py` + 常驻 UIA 工作进程）只在
  **开发机**（Win11 / PS 5.1 / CLR 4.0）上逐项自检过，**没在 Win7 客机上实跑**：客机上第一件要确认的
  是 `Add-Type` 能不能在 PS 2.0 + .NET 3.5 下编译 `UIAutomationClient`（§2.3），以及多屏/DPI 坐标；
- 这一版点鼠标/打字/滚动/拖动是**真输入**（`SetCursorPos` + `mouse_event` / `keybd_event`），
  **没有**"不碰指针也能操作界面"的通道（旧底座走的是 UIA Invoke/Value，已在 1.1.0-beta9 换掉）；
- 会话存储是"整份 JSON 快照"（`core.SessionStore`），断电可能丢最后一段，没有"中断工具结果修复"；
- **开机自启动只在开发机上验过**（写注册表 + 写启动文件夹 .lnk + 诊断/修复的接口与测试都过；
  单文件版认 exe 名字这一条 beta11 发布后才发现、已补 `--exe-path` 并加测试），
  真 Win7 重启一次、C 盘还原卡/冰点、管理员安装但老师用别的账号登录，这三类只有到教室里才知道；
- **这一轮连 Win7 客机都没上去**：本机 VMware Workstation 授权失效（`vmware.log` 里
  `msg.License.no_valid_license_see_website` + `Module 'License' power on failed`），
  `vmrun start` 直接返回"操作被取消"，`scripts\vm-acceptance.ps1` 快检只能报"客机没开机"。
  所以 `-Exe` / `-Full` / 真机自启 / PS 2.0 下建 .lnk 全部没验，恢复授权后第一件事就是跑它们；
- 物理沙盒 v2 的新对象是**多次点击**画出来的（板两次定对角、圆弧三次），倾角/半径/弹簧参数
  只能在右侧输入框里改、画布上没有把手，弹簧和绳的挂点不能拖动改挂，新对象没有网格吸附；
- 作图面板 M20 的交互（中垂线/平行线/角平分线/三心的鼠标点击）只过了 ES5 门禁、DOM 桩和 `node --check`，
  **没有在浏览器里真手点过**。

**M20 之后必须先做的真机验证**（代码都改完了，下面四条只能到机器上验）：

1. 老师自己账号下打开设置 → 点「开机自启动诊断」→ 有红点就点「一键修复」→ **重启一次**，
   再看诊断里「上一次开机自启的日志」有没有内容（判断口径见 `docs\autostart-win7.md`）；
2. 物理沙盒装载「小车上的弯道（单摆撞物块）」，对照 `docs\sandbox-linked-bodies.md` 第 6 节那 7 个期望值；
3. 悬浮栏：点顶栏 / 点收起后的图标**只应当展开**，点「显示界面」才打开程序（这一版改的就是这个）；
4. 真 Win7 客机跑 `scripts\vm-acceptance.ps1 -Exe`（单文件 exe 解包 + 服务起来 + 悬浮栏 + 界面窗口）。

**建议的下一步**（按性价比排序，来自对 DeepSeek Harness 的调研）：

1. **JSONL 仅追加会话日志 + 中断修复**：加载时丢弃撕裂的末行、给"有调用无结果"补
   `TOOL_OUTCOME_UNKNOWN` 合成结果，并明确**只重试只读/幂等工作，有副作用先核实**
   （DSH 全线"只修结果、绝不自动重做"）；
2. **会话内定时提醒**（`schedule_create/list/delete`，重复间隔下限 5 分钟，只追赶最新一次、不回放）；
3. **工具调用超时码化**（`TOOL_TIMEOUT` + 说明是谁超时），只发信号不强杀；
4. **per-model 上下文窗口覆盖**（`models.<name>.context_window`）已在设置层面支持，可继续做自动探测；
5. **下载断点续传**与**后台任务落盘恢复**（老师下大视频时最有用）。

---

## 11. 交接约定（请下一位遵守）

- **准确性 > 让用户开心**：不确定就说不确定；没验证就写"没验证"，不要用"应该可以"糊过去。
- **README 里的数字会过期**：改代码顺手改本文档对应的那一处（目录行数、工具数、自检项数、
  接口清单）。判断现状以本文件顶部 / §2 / §10 为准，逐阶段细节看 `docs\roadmap-1.1.0.md`。
- **先规划要加哪些工具再动手**：工具数量只能增不能明显减少（`TestToolSchemaBudget.TOOL_FLOOR = 28`，
  当前 45）。
- **优先遵循项目已有的框架/风格/设计**，不做无关重构；改动要小而集中。
- **改完必跑自检**（`.\python\python.exe webui\test_webui.py`，111 个类 / 1025 项）；
  发布前按 §3.4 的验收配方（解压包内自检 + 起服务 + 真机抽验），并跑 `scripts\win7-gate.py`。
- **不要在宿主机上操作鼠标键盘**：所有输入类验证放到 Win7 虚拟机里做
  （自检里的 `W7A_NO_INPUT=1` 就是这条的自动化版本）。
- **改完前端要提醒老师刷新页面**（页面常年开着跑的是旧 JS，这个坑发生过三次）。
- **`.ps1` 改完必须确认还是 UTF-8 带 BOM**（见 §8 坑 16）；`uia_worker.ps1` 反过来——
  必须**纯 ASCII 不带 BOM**（§2.3）；`.cmd` 必须纯 ASCII + CRLF。
- 交付时说明：改了哪些文件、怎么验的、**哪些没验**、以及遗留问题。

---

## 12. 许可证

- 本仓库里**自己写的代码**（`webui\`、`skills\`、`scripts\`、`launcher\`、`internal\`、`cmd\`、
  `vscode-extension\` 等）用 **MIT**，全文见仓库根目录 [`LICENSE`](LICENSE)；
  第三方资源的摘要另见根目录 [`NOTICE`](NOTICE)。
- **随包内置的第三方资源不适用 MIT**，各按自己的许可证；来源与授权逐条列在
  [`docs\third-party-licenses.md`](docs/third-party-licenses.md) 和
  [`webui\ui\vendor\README.txt`](webui/ui/vendor/README.txt)。要特别注意：
  - `webui\ui\vendor\phet\` 里三个 PhET 仿真（电路组建实验 / 分子形状 / pH）是
    **CC BY-NC 4.0（署名—非商业性使用）**，只能用于课堂教学等非商业用途；
  - `webui\ui\vendor\bio\` 里的生物插图来自 Wikimedia Commons，多为
    CC BY / CC BY-SA / 公有领域（CC BY-SA 的再分发要保持同样许可）；
  - `webui\ui\vendor\jsme\` 是 BSD-3-Clause。
- 因为内置了 CC BY-NC 的 PhET 资源，**整个仓库/整个程序不能用于商业用途**；
  只想拿代码去做商业项目的话，先把 `webui\ui\vendor\phet\` 和 `webui\ui\vendor\bio\` 删掉，
  剩下的代码按 MIT 用。
