PhET 互动仿真程序（随包内置 3 个）
===================================

这三个文件是从 PhET 官网直接下载的单文件版（自包含，离线可用），原样放进包里：

  circuit-construction-kit-dc.html   电路组建实验:直流（3.3 MB）
  molecule-shapes.html               分子形状（2.5 MB）
  ph-scale.html                      PH值 / pH 酸碱（2.7 MB）

来源
----
  https://phet.colorado.edu/sims/html/circuit-construction-kit-dc/latest/circuit-construction-kit-dc_zh_CN.html
  https://phet.colorado.edu/sims/html/molecule-shapes/latest/molecule-shapes_zh_CN.html
  https://phet.colorado.edu/sims/html/ph-scale/latest/ph-scale_zh_CN.html

版权与授权
----------
  PhET Interactive Simulations, University of Colorado Boulder
  Copyright 2002-2026 Regents of the University of Colorado
  **CC BY-NC 4.0**（署名 - 非商业性使用 4.0 国际）
  https://creativecommons.org/licenses/by-nc/4.0/
  https://phet.colorado.edu/en/licensing

  ⚠ 授权是 **非商业** 的：只能用于课堂教学等非商业用途，不能拿去卖钱或做商业产品。
  界面里的 PhET 标志属于 PhET 自己（我们不用它做本程序的图标或品牌）。

本程序做的改动（只有这一处）
----------------------------
  删掉了文件里两处第三方统计脚本（Google Tag Manager / Cloudflare Insights）：
  `https://www.googletagmanager.com/...` 换成了打不开的 `about:blank#`，
  `static.cloudflareinsights.com` 的 <script> 整段删除。
  原因：教室机器不该把这些数据发给第三方，离线机器也打不开；
  删掉之后模拟本身的功能、画面、参数一点没动。

自检
----
  在随包内核（Chromium 109，Win7 上能跑的最高版本）里实测过三个模拟都能正常打开：
  中文标题、SVG 画布、按钮都在（`TestPhetBundle`）。
