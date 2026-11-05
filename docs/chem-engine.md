# 化学引擎：录物种和规则，反应由引擎推

## 1. 为什么不录"反应"

搜过 GitHub 和英文源，能拿到的化学反应数据集全是机器学习口径的：USPTO 专利反应集、
反应模板抽取工具、逆合成规则——有机合成、英文、几十万到几百万条，和中学化学
（离子方程式、复分解、金属活动性、选修五官能团）完全不重合。成品"中学化学方程式库"
只有各校自制网页，没有许可、不可校验。

所以数据只录两样：

1. **物种**（元素、离子、化合物、常见有机物）：名称、别名、分子式、分子量、SMILES、
   状态、颜色、溶解性；
2. **规则**：溶解性（Ksp）、金属活动性顺序、标准电极电势、电离常数、气体逸出条件、
   络合常数、有机反应的官能团规则。

反应由 `webui\chem.py` 现场推：按反应类型枚举（复分解 / 置换 / 化合 / 分解 / 中和 /
氧化还原 / 络合 / 电解 / 原电池 / 有机官能团），**配平用原子守恒矩阵高斯消元**，
可行性用 Ksp / Ka / E° 半定量、有热力学数据时用 ΔrG° 定量。举例：一条"碳酸盐 + 钙盐
若生成物 Ksp 极小则沉淀"就覆盖了 Na₂CO₃/CaCl₂、K₂CO₃/Ca(NO₃)₂ 之类全部组合，
不需要人工写任何一个方程式。

## 2. 数据来源与许可（实测 2026-09-25）

| 来源 | 许可 | 取什么 | 通道 |
| --- | --- | --- | --- |
| Wikidata SPARQL | CC0 | 中文名、别名、分子式、SMILES、CAS | 通（突发请求会 429，要限速重试） |
| PubChem PUG-REST | free-to-use（少数第三方数据例外） | 分子式、SMILES、分子量 | 通 |
| NIST-JANAF（SRD 13） | 美国政府作品 | ΔfH°、ΔfG°、S°、Cp（物种页是 HTML 表） | 通 |
| NIST WebBook | 免费 | JANAF 没收录的物种热化学 | 通 |
| ChEBI 本体（chebi.obo） | CC BY 4.0 | 化合物本体、别名（补中文名） | 通（data-version 255） |
| ATcT（阿贡实验室） | 美国政府作品 | 更高精度的热化学值 | **403**，只当备选 |

取数脚本：`scripts\fetch-chem-data.py`（`--probe` 探通道，`--sample` 存样本到
`vendor\chem-probe\`）。**构建期联网、运行期离线**：包内不含任何抓取逻辑。

## 3. 已经踩到的坑

- **Wikidata 的分子式带 Unicode 下标**（实测 `水` 返回的是 `H₂O`），入库前要统一
  转成 ASCII 的 `H2O`，否则引擎的原子计数会认不出来。
- **不能靠猜 QID**：探针里我随手写的 Q408 实际是"澳大利亚"、Q8063 是"岩石"。
  正式导出要按化学键（P231 CAS / P233 SMILES / P274 分子式）过滤，不按固定 QID 列表。
- Wikidata 公共端点会限流，脚本里已加重试；正式导出要串行 + 间隔。
- 事实性表格（溶解性、电极电势、活动性顺序）**不受版权保护**，但仍要交叉核对，
  来源与口径写进 `docs\third-party-licenses.md`；维基百科正文（CC BY-SA）不直接抄。

## 5. 物种表怎么来（M3 起）

`scripts\fetch-chem-data.py`：

```bat
.\python\python.exe scripts\fetch-chem-data.py --probe        :: 只探通道
.\python\python.exe scripts\fetch-chem-data.py --sample       :: 顺便存样本到 vendor\chem-probe\
.\python\python.exe scripts\fetch-chem-data.py --seed         :: 按 CAS 种子表出物种表（快、稳）
.\python\python.exe scripts\fetch-chem-data.py --export       :: 全表扫（慢，容易撞限流，备用）
.\python\python.exe scripts\fetch-chem-data.py --reformat     :: 离线重排（补规范式、繁简归一）
.\python\python.exe scripts\fetch-chem-data.py --thermo       :: 补热化学（ΔfH°、S°）
.\python\python.exe scripts\fetch-chem-data.py --structures   :: 拉 ChEBI 本体解析批量结构（258 MB，只下一次）
```

**数据都在 `webui\chemdata\`，不能放 `webui\data\`**：后者是老师的会话/上传目录，
打包时按目录名整个排除（`EXCLUDE_DIRS` 里有 `data`），放那儿等于不随包发布。自检里有专门一条盯着。

| 文件 | 内容 | 来源 |
| --- | --- | --- |
| `chem-species.json` | 种子物种：中文名、原始分子式、**规范式**、SMILES、CAS | Wikidata（CC0） |
| `chem-rules.json` | 规则：溶解性、活动性、离子电荷、拆离子、沉淀颜色、气体/液体清单、繁简表、受热分解、器材与试剂清单、预设实验、装置检查 | 教材与 NIST/CRC 口径的事实 |
| `chem-thermo.json` | 298.15 K 的 ΔfH° 与 S°（按相态分列） | NIST WebBook（免费） |
| `chem-structures.json` | 20 万条结构（ChEBI 号、名称、分子式、SMILES） | ChEBI（CC BY 4.0） |

**种子表按 CAS 号点名，不按 QID**：CAS 写错只会少一条、日志里会列出来；QID 猜错会**静默**
拿到别的物质（探针阶段我随手写的 Q408 实际是"澳大利亚"、Q8063 是"岩石"，就是这么踩的）。
被限流（429）打断也没关系：脚本每批都落盘、重跑只补缺的那些。

`--structures` 那条路上踩到的坑，一起记着：ChEBI 255 的 OBO 用的是**带命名空间**的属性名
（`chemrof:generalized_empirical_formula`、`chemrof:smiles_string`），不是老的裸 `formula`/`smiles`。
按前缀写死就会一条都收不到（我第一版就是这样，258 MB 下来解析出 0 条），所以按**后缀**判断。

## 6. 热化学与"能不能自发"
`--thermo` 按 **CAS 号**去 NIST WebBook 取（URL 是确定性的，不用猜 JANAF 那种 `H-064` 编号——
我试过从它的索引推，推不出来）：

* 每页有两张表（气相 Mask=1、凝聚相 Mask=2），取 **ΔfH°** 与 **S°**，**相态分开存**：
  水的液态 -285.83、气态 -241.826，差 44 kJ/mol；氯化钠的固相 -411.12、液相 -385.92，差 25 kJ/mol。
  取错相态就是教错人，所以 `chem-rules.json` 里点名了 `gas_species` / `liquid_species`，其余按固相。
* 页面上同一行会重复多次（CODATA 评价值在最前，后面是逐年文献值），**取第一条**；
  甲烷那页还有**燃烧焓 ΔcH°** 的行，它的备注里写着 "Corresponding ΔfHº gas = …"，
  按"行首必须是 ΔfH°/S°"才拦得住（宽松匹配会抓到燃烧焓 -890.16 当成生成焓）。
* 单质的 ΔfH° **按定义是 0**，WebBook 里这一行根本不列，不能当成缺数据（S° 仍要查表）。
* `chem.py` 用这些算 ΔrH°、ΔrS°、ΔrG°(298 K)：**甲烷燃烧 ΔrH° = -890.3 kJ/mol**，和教材对得上。
  ΔrG° 只是**标准态判据**，返回值里写明"实际能否观察到还要看动力学与实际浓度、温度"，不含糊。

## 7. 界面怎么把"现象"画出来（visual 契约）

实验台要画的是"看得见的现象"，但**前端不该懂化学**。所以引擎在每个反应结果里多给一个
`visual` 字段，界面照着画就行：

```json
"visual": {
  "gas": ["CO2"],                                   // 冒气泡（界面做上浮动画）
  "precipitate": {"formula": "Cu(OH)2", "color": "#2f8fd8"},   // 沉淀（界面画沉降层）
  "solids": [{"formula": "Cu", "color": "#b45b3e"}],           // 析出的固体/剩余固体
  "solution_before": "#2f8fd8",                     // 溶液原来的颜色
  "solution_after": "#7fbf8f"                       // 反应后的颜色（界面做渐变过渡）
}
```

判断全在 `chem.py`：气体看 `gas_species`、沉淀看不溶（溶解度表）、颜色看
`solution_color` / `solid_color` 两张表。所以**换一张色卡或补一条溶解性规则，界面就跟着变**，
不用改前端代码。

界面那边（`webui\ui\chem.js` 的实验台）：

* 器皿是**按形状画的 SVG**（试管/烧杯/锥形瓶/集气瓶/水槽/酒精灯/铁架台），不是小图标；
  液体用 clipPath 裁在器皿内，液面画一道亮椭圆当"水面"；
* 动画全在 CSS 里：气泡 `lab-rise`、沉淀 `lab-settle`、火焰 `lab-flicker`（加热时器皿底部
  自己出火，不必非摆酒精灯）、液体颜色 `transition: fill 1.2s`；
* 药瓶上标了本色的圆点（溶液按溶液颜色、固体按固体颜色）——实验室里就是靠颜色认瓶子的；
* 拖动只是很薄的一层：`W7Chem.lab.place / addReagent / heat` 才是操作本体，自检直接调它们
  （拖动那层另外用合成鼠标事件验一次，见 `tmp-chem-check.js`）。

### 装置组装与检查装置

* 每件器材在 `PORTS` 里登记**接口点**（SVG 局部坐标）；点「导管」进连接模式，接口点浮出来，
  点两个点就接上一段导管（`LAB.links`）。导管画在台面上面那层 `#chemLabWires`（1 单位 = 1 CSS 像素，
  和器材坐标同一套），器材一挪就重画；
* 出气的器皿接上导管后，`labFlow()` 把 `link.flowing` 打开：气泡沿导管飘（SMIL `animateMotion`
  + `mpath`，Chromium 109 上实测能动），另一端能盛液体的器皿跟着冒泡，6 秒后一起停；
* **检查装置**（`op=check` -> `chem.lab_check`）只判几何：加热的试管有没有倾斜、加热锥形瓶/烧杯
  有没有铁架台、摆了收集装置却没用导管接上。规则表里**判不了**的条目（"导管伸入集气瓶不宜过长"，
  界面没有管长这个概念）走 `tips` 列出来当检查要点，不混进 `problems`——判过且做到了的规则
  也不该再冒出来（`JUDGEABLE` 白名单管这条）。化学判断仍然只在 `lab_mix` / `lab_heat` 里做。

### 实验台的仿真：加进去就算，按限度停

老师的原话是"反应应该是加入药品就开始计算模拟真实的反应限度，而不是点一下就反应一下"。
所以实验台的核心不是"点一次反应一次"，而是**一个状态 + 一条轨迹**：

* `lab_simulate(rules, thermo, contents, temperature, seconds, volume)` 收"容器里现在有什么"，
  一次返回：会发生哪些反应、每个反应的反应进度、随时间的组成轨迹（20 个采样点）、
  每种物质该怎么画（`appearance`）、现象、以及**反应限度**的说明。
* 限度：ΔrG°(T) = ΔrH° - TΔrS°（NIST WebBook 的 ΔfH°/S°）-> K = exp(-ΔrG°/RT)，
  再用 Q(x) = K 二分求反应进度 x。反应商里**只算溶在溶液里的物种**：气体逸出、沉淀、
  金属/氧化物按活度 1 处理——所以"生成气体或沉淀就反应完全"是自然结果，
  而溶液里的可逆反应（酯化那类）会真的停在中间。实测乙酸+乙醇+浓硫酸 @443 K：
  ΔrG° = -11.97 kJ/mol、K ≈ 26、转化率约 84%。
* 速率：`rate_tau()` 给一个**演示口径**的时间常数（复分解瞬时 0.6 s、置换 1.4 s、
  分解/有机 6 s），条件不够时按 Arrhenius（Ea≈60 kJ/mol）折算成"几乎不动"，
  并且明确告诉老师**缺什么**（要加热 / 要浓硫酸 / 要二氧化锰）。
* 界面只按 `appearance` 与轨迹画（`labLook`）：颜色、液面、固体堆、沉淀、气泡，
  每一帧都来自引擎给的组成。**"每点一次加热就多堆一层黑固体"这类问题由此不可能再出现。**
* 条件在数据里（`rules.reaction_conditions`）：点燃 873 K、加热 673 K、高温 1273 K、
  浓硫酸+加热 443 K（要 `H2SO4` 在场）、铜催化、溴化铁催化、常温。

### 试剂架：手写的那批 + 成套生成的

老师要"高中常见的盐成套都在"，一百多条手打既打不全也容易错，所以分两半：

* `chem-rules.json` 的 `reagents.list` 是**手写**的那批：标签颜色最讲究的（大理石、稀盐酸、
  浓硫酸、银氨溶液、新制氢氧化铜…）和所有有机物；
* `reagent_grid` 是**成套生成**的：阳离子（Na/K/NH4/Ca/Ba/Mg/Al/Zn/Fe2/Fe3/Cu/Ag）×
  阴离子（Cl/NO3/SO4/CO3/HCO3/SO3/OH/S/SiO3/CH3COO）。中文名 = 阴离子名 + 阳离子名，
  固体还是溶液按 `solubility` 判，`skip` 里是**不该摆上架子**的组合
  （Al2(CO3)3 双水解、Fe2S3 遇水分解、NH4OH 不存在、AgOH 不稳定…）。

`split_salt()` 是"查表 + 电荷守恒反推"：表里没有的式子（FeS、CuSO3、Na2SiO3）会用
所有离子组合算一遍化学式，和它一模一样的那个组合就是答案（FeSO4 得 Fe2、Fe2(SO4)3 得 Fe3，
不用猜铁几价）；反推不出来才说"资料未收录"。所以**新增一瓶药不用改代码**，
标了 `generated: "阳离子|阴离子"` 便于自检核对。

反应表也分两张：`organic_reactions`（有机，含教材简式与条件）与 `special_reactions`
（离子模型算不出来的无机经典：铝/锌与强碱、SiO2 与碱、氯气与碱、Fe3+ 氧化 Fe/Cu 等）。
两张表里的 `H2O` 可以省——容器里只要有溶液，引擎就按"水是溶剂、大量过量"补一份。

## 8. 硬红线

- 数据不足时输出"**资料未收录**"，绝不编造现象或方程式。
- 引擎推出来的反应在界面上与教材事实**视觉区分**（标注"理论预测"，老师端与学生端
  都要标）。
- 多来源冲突不自动合并，进 `chem-conflicts.json` 等人工裁决。取值优先级：
  教材口径 → ChEBI → Wikidata → PubChem。
