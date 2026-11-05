# 「平面」（Plane）模块架构与迁移映射（冻结版 v1）

> 这份文档是**并行开发的接口冻结件**：多个 agent 同时改这个仓库时，凡是这里写死的名字、
> 字段、返回值形状，谁都不许单方面改；要改先改这份文档。
>
> 适用仓库：`D:\agent-win7-x64\agent-win7-x64`（Study7Agent 1.0.0-beta1）
> 最高约束：**Windows 7 SP1 x64 + PowerShell 2.0 + .NET 3.5 + 随包 Python 3.8.10 纯标准库**。

---

## 0. 先把提示词里不适用的部分标注清楚

原任务书写的是 Electron / Node / TypeScript 技术栈，**本项目不是**。逐条对应关系：

| 任务书要求 | 本项目的真实情况 | 结论 |
| --- | --- | --- |
| Electron 锁 22.x，禁 23+ | 项目**没有 Electron**，界面是浏览器窗口（`Study7Agent.exe` 用 `--app=` 开 360/Chromium） | **N/A** |
| Node.js 锁 16.x，禁 18+ | **没有 Node 运行时**；启动器是 C#/.NET 3.5，服务端是随包 Python | **N/A** |
| TypeScript 转译到 ES2019/ES2017 | **没有构建步骤**，`webui/ui/*.js` 是直接跑的原生 JS | 等价门禁：**新 JS 必须是 Chrome 109 / 360 内核直接能跑的语法**（ES5 为主，不用需要转译的新语法） |
| 原生模块按 Electron ABI 重建 | 无原生模块；只用 Python 标准库 | **N/A** |
| NSIS 安装器 / 自动更新器 | 交付形态是**便携 zip + `Study7Agent.exe`**（Win7 双击即用），无安装器 | 等价物：zip 解压即用、中文/空格路径、非管理员 |
| 禁 WebGPU | 项目本来就没用 | ✅ 已满足 |
| WebGL 可选、必须能退回 Canvas2D/SVG | 作图面板**本来就是 SVG**（无 WebGL 依赖），天然满足 | ✅ 已满足 |
| 禁 Win10/11 专属 API | 项目本来就把 Win8+ API 列为禁区（见 `AGENTS.md` §4） | ✅ 已满足 |
| TLS 1.2 兼容 | Python 3.8 的 ssl 默认 TLS 1.2+；不支持 TLS 1.3 的系统上会自动降级 | ✅ 已满足 |
| contextIsolation / 禁 nodeIntegration | 无 Electron，但等价要求是：**Agent 不得直接操作 DOM**，只能走结构化接口 | 本设计遵守（见 §5） |

---

## 1. 现状（已经有什么，别重写）

| 模块 | 规模 | 已有的能力 |
| --- | --- | --- |
| `webui/plot.py` | 91 KB | 手写词法+递归下降解析器（**绝不 eval**）、符号求导 `_diff`、表达式化简 `render`、隐式/显式函数采样、平面几何小语法（点/线段/直线/圆/多边形/角）、立体几何（柱/锥/球/截面/线线角·线面角·面面角）、`surfaceHit` 射线求交 |
| `webui/calc.py` | 26 KB | `Exact` 精确有理数与根式运算（`sqrt`、化简、`SQUAREFREE_LIMIT` 保护） |
| `webui/ui/app.js` | ~7500 行 | SVG 作图面板：函数/方程/几何/立体四种 kind、几何工具条、立体工具条、动点与约束（`constraintOfPoint`/`pointOnCurve`/`pointOnSurface`）、拖拽（`setupSolidGestures`/`solidMovePoint`）、右栏坐标可编辑 |
| `webui/tools.py` | 41 个工具 | 其中与作图相关的只有 `show_figure`（`kind=function|geometry|solid`）和 `draw_path`（白板画笔） |
| `webui/test_webui.py` | 534 项 | `TestPlot` 等已覆盖解析器、求导、几何、立体 |

**结论：`plot.py` 是数学内核，`app.js` 的 figure 面板是渲染与交互，缺的是"对象模型 + 依赖图 + 命令层 + 轨迹/动画 + Agent 接口 + 导入导出"。**

---

## 2. 目标架构（七层，对应任务书 §6）

```
webui/plane.py        对象模型 + 依赖图 + 门面（唯一入口）
webui/plane_math.py   数值/符号计算（复用 plot.py 的解析器与求导、calc.py 的 Exact）
webui/plane_cmd.py    命令注册表：GeoGebra 风格命令 → 对象操作
webui/plane_dyn.py    动点 / 轨迹采样 / 动画 / 滑动条
webui/ui/plane.js     渲染层（SVG 基线 + Canvas2D 兜底），取代旧 figure 面板
webui/ui/plane_ui.js  代数区 / 输入栏 / 属性面板
```

**分层纪律**：上层只调用层接口，不许跨层直接摸内部状态。`plane.py` 之外任何模块都不许直接改
`Plane.objects`。

---

## 3. 数据模型（冻结）

```python
PlaneObject = {
    "id": "A",                 # 唯一标识，也是标签；重名会被拒绝
    "type": "point",           # 见 §4 类型表
    "definition": {...},       # 类型相关，见 §4
    "parents": ["O"],          # 依赖的父对象 id（依赖图的边）
    "children": ["c"],         # 反向边，由 Plane 维护，调用方不要自己写
    "value": {...},            # 计算出来的当前值（点坐标、圆的半径…），只读
    "style": {"color": "#4d6bfe", "size": 4, "visible": True, "layer": 0,
              "lineStyle": "solid", "fill": None, "label": True},
    "meta": {"source": "agent", "at": "2026-09-20 23:00", "frozen": False},
    "seq": 42                  # 创建序号，用于稳定排序与撤销
}
```

整张平面：

```python
PlaneState = {
    "version": 42,             # 每次成功变更 +1（对应任务书的 stateVersion）
    "view": {"xmin": -10, "ymin": -6, "xmax": 10, "ymax": 6,
             "grid": True, "axes": True, "polar": False},
    "window": {"xmin": -10, "xmax": 10},   # 网页端"现在看得见的横轴范围"，见下
    "objects": {id: PlaneObject},
    "order": ["O", "A", "c"],  # 创建顺序
    "undo": [...], "redo": [...]   # 内部用，不对外暴露内容
}
```

**`window` = 视野同步（让曲线"无限"）**：网页端每次把可见范围用
`POST /api/plane/window {xmin, xmax, ymin, ymax}` 报上来（`ymin/ymax` 可以不报）。
服务端据此把范围往两边各扩 `AUTO_WINDOW_MARGIN`（2）屏再采样，所以屏幕外也画着线，
拖出去、放大都不会断。这条路径**不加版本、不进撤销栈**（拖视野不该变成一次可撤销的操作）。

- 显函数按 `xmin/xmax` 采，采样点数跟着跨度等比放大（`samples`）。
- **隐式曲线 / 不等式区域**是二维采样框，要 x、y **都报上来**才跟着视野走：
  只报 x 时保持服务端 `view` 那一块（只把 x 拉长会让框又扁又稀、圆上的点变少），
  两个方向都有时才按可见范围扩，并把网格密度 `grid` 一起放大（上限 `MAX_IMPLICIT_GRID`）。
  老师看到的"方程只画了一小截"就是这里没跟着视野走。

**依赖图的纪律**：
- 创建时自动从 `definition` 抽出 `parents`（引用别的对象的字段）；
- **拓扑排序执行**：父对象先算；
- 删除对象时**级联删除依赖它的对象**，并在结果里列出被连带删除的 id（`removed` 字段），不许静默；
- 检测环：形成环的创建请求要拒绝，返回结构化错误。

---

## 4. 类型表（v1 必须实现的）

`definition` 的字段名在这里冻结，Agent 与前端共用：

| type | definition 字段 | 说明 |
| --- | --- | --- |
| `point` | `{x, y}` 或 `{on: <id>, parameter: 0..1}` 或 `{intersect: [id1, id2], index: 0}` | 自由点 / 对象上的动点 / 交点 |
| `segment` `ray` `line` `vector` | `{start: id, end: id}` | 两点定线 |
| `perpendicular` `parallel` | `{through: id, to: id}` | 垂线 / 平行线 |
| `perpendicular_bisector` | `{a: id, b: id}` | 中垂线 |
| `angle_bisector` | `{a: id, b: id, c: id}` | 角平分线（顶点在 b） |
| `tangent` | `{to: id, at: id}` | 切线 |
| `circle` | `{center: id, radius: number}` 或 `{center: id, through: id}` 或 `{through: [a,b,c]}` | 三种构造 |
| `arc` `sector` | `{center: id, from: id, to: id}` | 弧 / 扇形 |
| `polygon` | `{points: [id...]}` | 任意多边形 |
| `regular_polygon` | `{a: id, b: id, n: 5}` | 正多边形 |
| `angle` | `{a: id, b: id, c: id}` | ∠ABC（顶点 b） |
| `distance` `length` `area` `slope` `radius` | `{of: id}` 或 `{of: [id1, id2]}` | 度量 |
| `function` | `{expr: "x^2+sin(x)", var: "x", xmin, xmax}` | 显函数。**xmin/xmax 可以不写** = 自动区间：按 `window`（§3）采样，两边各多采 `AUTO_WINDOW_MARGIN`（2）屏宽，屏幕外也有线 |
| `implicit` | `{expr: "x^2+y^2-1"}` | 隐式曲线 F(x,y)=0 |
| `parametric` | `{x: "cos(t)", y: "sin(t)", tmin, tmax}` | 参数曲线 |
| `polar` | `{expr: "1+cos(θ)", tmin, tmax}` | 极坐标曲线 |
| `inequality` | `{expr: "y > x^2"}` 或 `{exprs: ["x^2+y^2<=1", "y>x^2"], op: "and"}` | 不等式区域 |
| `conic` | 见下方「圆锥曲线字段」 | 圆锥曲线（焦点/准线/离心率等导出） |
| `slider` | `{value, min, max, step, animating: false}` | 滑动条（本质是一个数） |
| `number` `text` `boolean` | `{value}` / `{text}` / `{value}` | 基本量 |
| `locus` | `{point: id, driver: id, samples: 120}` | 轨迹 |

**关于「中点」**：类型表里**故意没有** `midpoint` 这个类型。中点用
`point {on: <线段id>, parameter: 0.5}` 表达 —— 几何含义和依赖关系跟"中点"完全一样
（父对象是那条线段，线段动它就动），GeoGebra 内部也是这个等价构造。
**命令层**（P3 的 `plane_cmd.py`）再提供 `Midpoint(A, B)` 命令把它展开成这个构造：
老师敲命令的习惯保住了，模型里却不用多一个特例类型。

**拖动点的整数磁吸**（老师要求：在整数坐标"顿一下"，别太明显）：前端统一用
`window.W7Snap.axis(prev, next, perUnit)`，实现在 `webui/ui/app.js`。
旧的平面几何面板与立体（空间）面板已经接上，新的 `plane.js` 也用**同一份**，不要各写一套。
阈值按**屏幕像素**算（进 6px、出 14px），"顿一下"就是这 8px 迟滞带来的；
**刻意不做任何视觉提示**。改它必须同步过 `TestSnapDetent` ——
那条测试是从 app.js 里抽出真函数交给 node 跑的，不是副本。

| `trace` | `{of: id}` | 追踪（运行时累积点） |
### 圆锥曲线字段（冻结）

内核 agent 按文档实现到 conic 时卡住了：原文只冻结了 `kind`，后面是"…"，
于是它**只登记不求值**（创建时给 warning）。现在把字段名定死：

| kind | definition | value（算出来的） |
| --- | --- | --- |
| `ellipse` | `{kind:"ellipse", focus1: id, focus2: id, a: number}` 或 `{center: id, a, b}` | `{center:{x,y}, a, b, c, foci:[[x,y],[x,y]], vertices:[[x,y]…], directrices:[…], eccentricity, segments:[[x,y]…]}` |
| `hyperbola` | `{kind:"hyperbola", focus1: id, focus2: id, a: number}` | 同上，另加 `asymptotes:[{point,direction}…]` |
| `parabola` | `{kind:"parabola", focus: id, directrix: id}` | `{vertex:{x,y}, focus:{x,y}, axis:{point,direction}, directrix, p, segments:[…]}` |

两条纪律：
1. **`value` 一律带 `segments`**（采样折线，和函数曲线同形状），前端才能用同一段代码画；
2. 求值不出来的（比如焦点重合、a≤0）**返回结构化错误**，不要给个空对象让前端画不出来还不报错。

### 交点 `index` 的顺序（冻结）

`{"intersect": [id1, id2], "index": 0}` 里的 `index` 指的是**按 x 再按 y 升序**排好之后的第几个
（x 相同再比 y）。理由：老师看到的是屏幕上的"左边那个/右边那个"，按坐标排序最符合直觉，
也不会因为求交算法的内部顺序变化而漂移。实现方必须**先把交点排序再取 index**，
并且当交点数量少于 `index+1` 时返回结构化错误（`no_such_intersection`），
不要静默返回第一个。

### 对象 `value` 必须是"能直接画"的成品（冻结）

前端**不做数学**。每个对象的 `value` 里必须已经算好可直接喂给 SVG 的东西：
点 `{x,y}`；圆 `{center,radius}`；线段/直线 `{start,end,direction,a,b,c,length}`；
多边形 `{points,area,perimeter}`；弧/扇形 `{center,radius,startAngle,endAngle,path}`；
函数/隐式/参数/极坐标 `{kind,segments:[[x,y]…],bbox,points,truncated}`；
不等式 `{regions:[[x0,y0,x1,y1]…],cell,cols,rows}`。
`truncated=true` 表示采样被上限截断了，前端要如实提示（不能假装曲线画全了）。

| `measure` | `{kind: "distance"\|"angle"\|"area"\|"slope", args: [...]}` | 通用度量（返回数值对象） |

---

## 5. Agent 接口（冻结）

### 5.1 工具收敛：**只加两个工具**

原任务书列了 32 个 `plane.*` 工具。**这里不能那么做**：工具 schema 每轮都要重发，
当前 41 个工具已经 22098 字符，天花板 `SCHEMA_CEILING = 24000`（见 `test_webui.py`）。
再加 32 个工具会直接爆掉预算，等于每一轮都多烧几千 token。

**冻结方案**：两个工具承载全部 32 种能力。

```
plane        { "action": <枚举>, ...该动作的参数 }
plane_batch  { "operations": [ {op, type, id, definition, style}, ... ] }
```

`action` 枚举（**能力一个不少**，只是入口收敛）：

```
create_object create_objects update_object delete_object list_objects get_object
query_state execute_command plan apply_plan solve
plot_function plot_implicit plot_parametric plot_polar
add_slider add_animation add_locus add_trace animate
set_style zoom pan reset_view
export import undo redo snapshot restore
```

> `natural_language` 不作为独立 action：自然语言由模型自己解析成上面这些结构化调用
> （这正是 Agent 该做的事）。工具返回里带 `explanation` 与 `warnings` 供模型复述。

### 5.2 返回值（冻结）

成功：

```json
{"ok": true, "stateVersion": 42, "created": ["A", "c"], "updated": [], "removed": [],
 "warnings": [], "errors": [], "explanation": "已创建…", "handle": "undo:41"}
```

失败：

```json
{"ok": false, "stateVersion": 42, "errors": [{"code": "missing_field",
  "message": "circle 缺少 radius", "path": "definition.radius", "hint": "补一个数字半径，或改用 through 构造"}],
 "warnings": [], "explanation": ""}
```

**错误必须是结构化的**（`code` / `message` / `path` / `hint`），Agent 据此重试；
不许只回一句中文就完事（那是给老师看的，不是给 Agent 用的）。

### 5.3 审计与回滚

每个对象带 `meta.source`（`agent` / `user` / `import`）；所有变更进 undo 栈；
`plane snapshot` / `plane restore` 支持 Agent 回滚到任意 `stateVersion`。

---

## 6. 迁移映射表（旧 → 新）

| 旧入口 | 旧行为 | 新入口 |
| --- | --- | --- |
| `show_figure(kind="function", expression=...)` | 画一条显函数曲线 | `plane.plot_function`（内部建 `function` 对象） |
| `show_figure(kind="geometry", shapes=["A(0,0)","circle O 3",...])` | 画平面几何，字符串小语法 | `plane.create_objects`（结构化）；小语法字符串继续支持，由 `plane_cmd.py` 解析 |
| `show_figure(kind="solid", ...)` | 立体几何（柱/锥/球/截面/角） | **保留独立**：立体不在"平面"范围内，仍走 `show_figure(kind="solid")`。`plane` 不碰立体 |
| `draw_path`（白板画笔） | 在希沃白板上画线 | 不变（那是屏幕画笔，不是数学对象） |
| `calc.py` 的 `Exact` | 精确值 | `plane_math.exact()`，函数图像与度量都用它 |
| `plot.py` 的 `_tokenize/_diff/render` | 解析与求导 | `plane_math` 直接复用（**不重写**） |
| 前端 figure 面板（`openFigurePanel` 等） | 四种 kind 共用一块 SVG | `plane.js` 接管 function/geometry；**solid 仍留在 app.js 的旧面板**，两条路并存不打架 |
| 会话里的 `figure` 事件（SSE） | 推给面板 | 增加 `plane` 事件（增量 diff：`{t:"plane", ops:[...], version}`）；旧的 `figure` 事件继续发，兼容旧前端 |

---

## 7. Win7 快速兼容性门禁（开发阶段每次提交前跑）

`scripts/win7-gate.py`（**待实现**）检查：

1. **语法**：`webui/ui/*.js` 不许出现 Chrome 109 不支持的语法
   （可选链 `?.`、空值合并 `??`、`**`、类字段、私有字段 `#x`、`at()`、顶层 await、`structuredClone`）。
2. **禁用 API 扫描**：`WebGPU`、`navigator.gpu`、`createImageBitmap` 之外的 Win10+ 专有 API、
   `OffscreenCanvas` 作为必需路径、CSS `:has()`/`color-mix()`/`@container` 作为核心布局。
3. **Python**：`os.replace`/`subprocess`/`ctypes` 里不许出现 Win8+ 才有的调用；
   标准库以外 import 一律报错（本项目硬约束）。
4. **字体**：不许出现 `Segoe UI Variable`、`Segoe Fluent Icons` 等 Win10+ 字体。
5. **数学内核**：`plot.py` / `plane*.py` 只允许标准库 + 本项目模块。
6. **特性回退**：`plane.js` 必须有 WebGL 探测与 SVG/Canvas2D 回退分支（本项目基线是 SVG，探测只为将来）。

门禁输出 `OK/FAIL + 行号`，退出码非 0 即失败。

---

## 8. 分阶段实施

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P1 | `plane.py` 对象模型 + 依赖图 + create/update/delete/list/get/query_state + 快照/撤销/重做 | `TestPlaneCore` 全绿 |
| P2 | `plane_math.py` 复用解析器/求导/Exact；`function`/`implicit`/`parametric`/`polar`/`inequality` 采样 | `TestPlaneCurves` |
| P3 | `plane_cmd.py` 命令层（任务书 §4.6 那批命令） | `TestPlaneCommands` |
| P4 | `plane_dyn.py` 动点/轨迹/动画/滑动条 + 依赖传播 | `TestPlaneDynamic` |
| P5 | `plane.js` 渲染 + 代数区/输入栏/属性面板 | 无头断言 + 截图 |
| P6 | Agent 工具 `plane` / `plane_batch` + SSE `plane` 事件 | `TestPlaneTools` |
| P7 | 导入导出 JSON/SVG/PNG；GGB 兼容读取 | `TestPlaneIO` |
| P8 | `scripts/win7-gate.py` + 真机最终验收矩阵 | 门禁 + Win7 报告 |

---

## 9. 缺口预告（现在就能看出来的）

1. **GeoGebra 完整脚本引擎（GGBScript/JS 沙箱）**：本项目禁 `eval`，实现 GGBScript 要写解释器；
   v1 只做"命令序列 + 事件绑定"的子集，完整脚本列为后续。
2. **隐式曲线求交、包络、极线极点、反演圆**：需要数值几何求解器，v1 只做采样求交与近似，
   精确解列为缺口。
3. **PDF 导出**：纯标准库写 PDF 可行但工作量大，v1 先做 SVG/PNG，PDF 列为后续。
4. **GGB 文件读写**：`.ggb` 是 zip+XML，可解但字段极多，v1 只读基本几何与函数，其余忽略并警告。
5. **多显示器 / Aero / 代理网络**的真机矩阵：靠最终 Win7 验收覆盖，开发阶段用门禁代替。
