---
name: figure-first
description: 平面（函数、方程、圆、直线、交点、动点、轨迹）一律用 plane / plane_batch；立体才用 show_figure(kind=solid)。不要开希沃白板，也不要用 draw_path 在屏幕上描。
keywords: 画函数,函数图像,几何图形,作图,坐标系,抛物线,三角函数图像
always: true
---

# 画图：平面用 `plane`，立体用 `show_figure`

**这条是硬性规矩，每次都要遵守。**

## 做什么

| 画什么 | 用什么 | 怎么给参数 |
| --- | --- | --- |
| 平面：函数图像 | `plane` | `action=plot_function`，给 `expr`（关于 x 的表达式） |
| 平面：方程 / 隐式曲线 | `plane` | `action=plot_implicit`，给 `expr`（例如 `x^2+y^2=1`） |
| 平面：圆 / 直线 / 线段 / 交点 / 动点 / 多边形 / 角 / 距离 | `plane` | `action=create_object`（`type` + `definition`）或 `action=execute_command` |
| 平面：一次建一整组对象 | `plane_batch` | 给 `ops` 数组，一条 op 一个对象 |
| 立体（空间） | `show_figure` | `kind=solid`，给 `shapes` |

图画在网页的**作图面板**里，老师那边直接就能看到。

**`show_figure` 只剩立体了**：它现在只认 `kind=solid`，收到函数 / 平面几何会返回一条
结构化错误让你改用 `plane`。**看到那条错误就直接换 plane，不要反复试别的写法。**

## 不要做什么

- **不要**为了画数学图去打开或使用**希沃白板 / 几何画板**这类软件。老师要的是图，
  不是让你去操作另一个程序。
- **不要**用 `draw_path` 在屏幕上描。那是"老师已经打开了画板，让你在上面圈重点"用的，
  慢，而且曲线会画歪。
- **不要**只用文字描述图长什么样，也不要贴一段代码或 ASCII 图——要真的把图画出来。

## 平面的写法（`plane`）

一次建多个对象（推荐：整批原子，撤销也是一步）：

```
plane_batch ops=[
  {"op":"create","type":"point","id":"A","definition":{"x":0,"y":0}},
  {"op":"create","type":"point","id":"B","definition":{"x":4,"y":0}},
  {"op":"create","type":"segment","id":"sAB","definition":{"start":"A","end":"B"}},
  {"op":"create","type":"point","id":"M","definition":{"on":"sAB","parameter":0.5}},
  {"op":"create","type":"circle","id":"cM","definition":{"center":"M","through":"A"}},
  {"op":"create","type":"point","id":"P","definition":{"intersect":["cM","sAB"],"index":0}}
]
```

- 动点：`{"on": "对象id", "parameter": 0..1}`（线段上 0.5 就是中点，圆上是按角度比例）；
- 交点：`{"intersect": ["对象1","对象2"], "index": 0}`，`index` 按 x 再按 y 升序数；
- 函数：`{"expr": "x^2-2x-3", "xmin": -5, "xmax": 5}`；
- **父对象一改，依赖它的对象全跟着变**（交点会跟着走）。要挪点就
  `plane action=update_object id=A definition={"x": 2}`，**不要**删了重建。

`execute_command` 认这些 GeoGebra 式命令（一次可以写多条，换行或分号隔开）：

```
Point(3, 4)            建点（自动命名 A、B、C…）
Segment(A, B)          线段        Line(A, B)        直线
Circle(A, B)           圆心 A、过点 B 的圆
Midpoint(A, B)         中点（内部就是线段上 parameter=0.5 的动点）
Intersect(k1, k2, 1)   交点（第三个参数是第几个，从 1 数）
Function("x^2-2x-3")   函数        Polygon(A, B, C)  多边形
Angle(A, B, C)         角（顶点在 B）  Distance(A, B)  距离
Slider(1, -5, 5)       滑动条（值、下界、上界）
```

平面里的 `angle` / `poly` / `circle` / `point` / `segment` 这些类型都在 `plane` 的类型表里
（写错类型时返回的错误会带上可用类型表）。

## 立体图形的写法（`show_figure`，`kind=solid`）

**立体图形必须用 `kind=solid`。** 题目里出现 **棱柱 / 棱锥 / 长方体 / 正方体 / 圆柱 / 圆锥 / 球 /
旋转体 / 空间 / 异面直线 / 二面角 / 线面角** 这些字眼时，画成平面图就是错的——要在空间里画。

```
A(0,0,0)          定义空间点（三个坐标，缺一不可）
seg A B           连一条棱
face A B C        画面（要求这几个点共面）
prism A B C 3     三棱柱：底面 ABC + 高 3
pyramid A B C 3   三棱锥：底面 ABC + 高 3
box A B C D 2     长方体：底面 ABCD + 高 2
cylinder O 3 5    圆柱：底面圆心 O、半径 3、高 5
cone O 3 5        圆锥：底面圆心 O、半径 3、高 5
sphere O 3        球：球心 O、半径 3
sphere O A        球：球心 O、过点 A（不用自己算半径）
circle3 A B C     过 A B C 三点的圆（空间里手画圆就用它）
lineangle A B C D       线线角：直线 AB 与直线 CD
lineplane A B C D E     线面角：直线 AB 与平面 CDE
planeangle A B C D E F  面面角：平面 ABC 与平面 DEF
```

**圆柱 / 圆锥 / 球也是立体图形**：题目里有"圆柱""圆锥""球""旋转体"这些字眼时同样用
`solid`，不要退化成平面里的一个圆。曲面是线框画法——球不给表面积和体积（那是公式算的，
不是面拼出来的），圆柱和圆锥会给。

面可以**整圈点下来**（正方体一个面 4 个顶点），分组用 `|` 隔开：
`面面角 A B C D | A B B1 A1`。角的 sin/cos 会给出分数 / 根号形式的**精确值**。

- 表达式里可以用 `sin cos tan asin acos atan sinh cosh tanh exp ln log log10 log2 sqrt abs
  floor ceil round sign min max pow` 和常量 `pi e tau`；
  `2x`、`3sin(x)`、`x(x+1)` 这种省掉乘号的写法也认。

## 算错了比画不出来更糟

题目给了边长、角度、坐标就**按题目算**，不要自己改数字，也不要把"看起来差不多"的坐标写上去。
坐标是本地精确算出来的，你只要把"画什么"说清楚就行。

## 画完怎么说

一句话说清画的是什么、在哪儿（"已经画在网页的作图面板里了"），
然后可以直接讲这道题的要点。

**画完就停，这是硬性要求：**

- **不要**截屏验收，**不要**调 `snapshot` / `screenshot` 去"看看画得对不对"；
- **不要**接着去开白板、往屏幕上描，或者再补几个对象"完善一下"；
- 这一轮就结束：把图讲清楚（题目问什么答什么），需要的话说一句"图在作图面板里"；
- 老师觉得图不对会直接说。**在他说话之前，不要再动任何工具。**
