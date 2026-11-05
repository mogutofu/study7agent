# 第三方资源与许可

这份程序本身只依赖**随包 Python 3.8.10 的标准库**和 Windows 7 自带的东西
（PowerShell 2.0 / .NET 3.5 / 浏览器），不装任何依赖、没有构建步骤。
下面这些是**借用过数据或算法**的第三方项目，按它们的许可在这里声明。

---

## convert-units（单位换算表的英制/美制部分）

- 项目：https://github.com/ben-ng/convert-units
- 许可：MIT
- 用在哪：`webui/units.py` 里**英制 / 美制单位相对基准单位的换算系数**
  （英寸、英尺、英里、英亩、加仑、液量盎司、磅、盎司、psi 这些又琐碎又容易记错的值）。
  具体做法见 `README.md` §2.6：把它的单位定义文件按它的锚点模型算出"相对基准单位"的系数，
  再用 43 条已知换算逐条校验；英制↔公制的锚点比例换成了我们自己写的**精确值**
  （它的定义文件里是四舍五入过的，例如英尺写成 0.3047999902464003）。
- 其余单位、中文名、搜索别名、"高中常用"标记、排序全部是自己写的。

```
Copyright (c) 2013 Ben Ng, http://benng.me

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## JSME（分子编辑器）

- 项目：https://www.npmjs.com/package/jsme-editor （主页 https://jsme-editor.github.io/ ，
  仓库 https://github.com/jsme-editor/jsme-editor.github.io ）
- 许可：**BSD-3-Clause**（npm 包 `package.json` 里声明的 `license`；作者栏写的是
  Peter Ertl、Bruno Bienfait）
- 用在哪：化学面板「自己画分子」页——学生/老师手画分子式，画完导出 SMILES，
  再交给 `webui/molecule.py` 画结构式、球棍模型和反应动画。
- 怎么带的：整包放在 `webui/ui/vendor/jsme/`（67 个文件，逐个的 SHA-256 记在
  `webui/ui/vendor/jsme/manifest.json`），由 `scripts/fetch-molecule-libs.py` 从
  jsDelivr 取；**运行期不联网**，也不做构建。
- 没有改它的源码，也没有把它并进我们自己的代码，只是按原样随包分发。
- npm 包里**没有**单独的 LICENSE 文件，所以下面按 BSD-3-Clause 的标准条文照录一份
  （署名权归上面那两位作者）：

```
Copyright (c) Peter Ertl, Bruno Bienfait. All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted
provided that the following conditions are met:

  * Redistributions of source code must retain the above copyright notice, this list of
    conditions and the following disclaimer.
  * Redistributions in binary form must reproduce the above copyright notice, this list of
    conditions and the following disclaimer in the documentation and/or other materials provided
    with the distribution.
  * Neither the name of the copyright holder nor the names of its contributors may be used to
    endorse or promote products derived from this software without specific prior written
    permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY
WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## 没有引入的东西（说明一下免得以后有人重复找）

- **SmilesDrawer**：本来打算用它画 2D 结构式，取回来实测三件事都不顺：
  ① 它的 `draw()` 签名和文档不一致（2.1.7 是 `draw(data, target, theme, weights, infoOnly)`，
  照文档传第 4 个参数会内部空指针）；② 画到 canvas 是**异步**的，画完立刻取像素是空的，
  自检和截图都抓不住；③ 产物用了 `??` 与 `**`，和"老内核也跑"这条冲突（门禁会拦）。
  结构式/球棍模型/官能团本来就要共用同一张原子-键图，所以自己写进了 `webui/molecule.py`。
- **three.js / 任何 3D 库**：体积几百 KB，而项目要的只是"把一个三维点投影到二维、
  按深度排序、画透明面"，几十行就够，引入反而多一个要跟着升级的东西。
- **math.js / big.js / fraction.js**：精确算术用 Python 标准库的 `fractions.Fraction` 就够了；
  根号（`√2`、`2√3/3`）它们也不直接支持，还是得自己写一层，所以没引入。
- **GeoGebra / JSXGraph**：GeoGebra 是专有许可；JSXGraph 体积和改动面都太大，
  和本项目"直接跑原生 JS、能离线、Win7 上不折腾"的约束不划算。
