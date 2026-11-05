# 内置浏览器内核（Chromium 109）

这份文档回答三个问题：为什么必须是 109、从哪里取、取不到怎么办。

## 1. 为什么锁死 109

Chromium 110 起不再支持 Windows 7 / 8 / 8.1（woolyss 版本表、chromium 109.0.5414.120 tag）。
教室一体机是 Win7 SP1 x64，所以内置内核只能是 109 一系，没有更新余地。

109 这一版必须在两个"109"之间挑清楚，实测差别如下：

| 来源 | 实际版本 | 证据 | 许可 |
| --- | --- | --- | --- |
| ungoogled-chromium `109.0.5414.120-1.1` | **109.0.5414.120（最终稳定版）** | 发布页公布的 tag 与 SHA-256 | BSD-3 |
| Google 官方快照桶 `Win_x64/10700xx` | 109.0.5414.0（分支早期） | 包内文件名 `chrome-win/109.0.5414.0.manifest` | BSD-3 |

**取最终稳定版**：`ungoogled-chromium` 那条。它顺带还有两个好处——剥掉了
Google 的账号/同步/遥测组件，而且不带 Chrome 商标（Chrome 品牌版不允许再分发，
Chromium 允许）。

## 2. 通道实测（2026-09-25 在开发机上跑 `scripts\fetch-chromium.py --probe`）

| 通道 | 结果 |
| --- | --- |
| GitHub 发行包直连 | 通，前 64 KB 用时 2.0 s |
| `ghproxy.net` 代理同一个包 | 通，1.9 s（离线机器的备选） |
| `ghfast.top` 代理 | 不通（SSL 握手超时） |
| Google 官方快照桶（4 个修订号） | 通，1.0-1.5 s |
| `registry.npmmirror.com` 的 chromium 镜像 | 404（该路径没有镜像） |

包大小实测 **111.2 MB**（`Content-Range` 报的总长）。

持续下载速率是这条路上真正的坑：同一个包，**单连接只有 2 KB/s**（12 MB 磨了十几分钟），
换成 6 段并行后稳定在 **300 KB/s 左右**（峰值 700 KB/s），全程 6 分钟左右：

```bat
.\python\python.exe scripts\fetch-chromium.py --parallel 6
```

所以脚本两个都留着：单连接那条带**断点续传**（重跑从 `.part` 接着下），
分段并行那条是限速时的开关。两边最后都要过 SHA-256，并行只影响快慢。

## 3. 取不到怎么办（人工兜底）

脚本的第一原则是"哈希对不上就整包丢弃"，所以任何镜像都能用，包括人工下载：

```bat
.\python\python.exe scripts\fetch-chromium.py --archive vendor\你下好的.zip
```

ungoogled 那条的官方 SHA-256 写在 `scripts\fetch-chromium.py` 里；Google 快照桶
没有公布哈希，第一次拉下来之后写进 `scripts\vendor-lock.json`，以后每次都要对上。

## 4. 拉下来之后

```bat
.\python\python.exe scripts\fetch-chromium.py          :: 下载 + 校验 + 解包到 vendor\chromium-109
.\python\python.exe scripts\fetch-chromium.py --trim   :: 裁剪
.\python\python.exe scripts\fetch-chromium.py --check  :: 打印版本
```

裁剪只删明确不需要的旁支：更新器、代理快捷方式、崩溃上报、widevine、`log\` 目录，
以及除 `zh-CN` / `en-US` 之外的 `locales\*.pak`。**保留 `swiftshader`**——教室一体机
多半是 Win7 上的老 Intel 驱动，WebGL 要靠它软件兜底。

实测数字（2026-09-25，ungoogled 109.0.5414.120）：

| | 文件数 | 体积 |
| --- | --- | --- |
| 压缩包 | | 111.2 MB |
| 解包后 | 74 | 249.5 MB |
| 裁剪后 | 22 | 222.4 MB |

大头就一个：`chrome.dll` 181.5 MB，其余是 `icudtl.dat` 10 MB、`resources.pak` 7.1 MB、
`libGLESv2.dll` 6.2 MB、`D3DCompiler_47.dll` 4.7 MB、`vk_swiftshader.dll` 4.1 MB。
所以单文件 exe 的体积基本由内核决定，约 130-140 MB。

`--check` 读的是 PE 文件里的版本资源（`GetFileVersionInfo`，ctypes 直接调），
**不是** `chrome.exe --version`：Windows 上 chrome.exe 是 GUI 子系统程序，版本号
不往 stdout 打，直接调用会一直挂着不退出（实测 30 秒超时）。

`vendor\` 不进仓库、不进镜像、不进 `dist`：它只是打包时的原料，最终产物里
是内嵌好的一段 payload。

**打包必须从 zip 重新解包再裁剪**，不能拿"跑过一次的目录"直接打：内核目录会被
本机环境改脏（见 §8）。

## 5. 启动方式

内核用 `--app=` 起一个不带地址栏和标签页的应用窗口：

```
chrome.exe --app=http://127.0.0.1:<port>/ ^
           --user-data-dir=<profile> ^
           --no-first-run --no-default-browser-check --start-maximized ^
           --disable-background-networking --disable-component-update ^
           --disable-sync --noerrdialogs
```

启动器（`launcher\Study7Agent.cs`）优先用随包内核，找不到才退回老师机器上的浏览器
（那条老路一个字没改）。内核有**自己的 profile 目录**（`kernel-profile`）：老师以前
用 360/Chrome 跑过 1.0，那些浏览器写的 profile 比 109 新，共用会报"配置来自更新的版本"。

`--remote-debugging-port` 在自检时会临时加上：现在这套浏览器自检工具链（CDP）
是验证画布/拖动行为的主要手段，换成 Miniblink 之类的自绘内核就全废了，
这也是选便携 Chromium 而不是更小内核的原因之一。

## 6. 内核起不来时的重试阶梯（实测逼出来的）

内核起来不到 10 秒就退出（老师自己关窗口不算）时，自动升档重试，最多两级：

| 档 | 参数 | 治什么 |
| --- | --- | --- |
| 1 | `--disable-gpu` | 教室一体机的老 Intel 驱动 |
| 2 | `--disable-gpu --no-sandbox` | 见下面的"用户目录"问题，最后一招 |

**不要解到用户目录下面（`C:\Users\<用户>\`）。** 这是 2026-09-25 在本机实测出来的：
同一份 Chromium 109，放在 `C:\ProgramData`、`C:\其它目录`、`D:\` 下都正常；只要在
`AppData\Local`、`AppData\Roaming`、甚至 `%TEMP%` 下，GPU 进程就以
`STATUS_DLL_INIT_FAILED(0xC0000142)` 连崩三次，然后浏览器直接

```
FATAL:gpu_data_manager_impl_private.cc(440)] GPU process isn't usable. Goodbye.
```

试过都没用：`--disable-gpu`、`--in-process-gpu`、`--disable-gpu-sandbox`、
`--disable-software-rasterizer`；**只有 `--no-sandbox` 能跑**。触发原因和本机装的
"注入型"软件（同 §8 的那两个 DLL）有关，但产品不能赌教室机器上没有，于是启动器按
这个顺序选解包位置：

1. 程序所在目录（可写且不在用户目录下）——U 盘/独立文件夹，最干净
2. `%ProgramData%\Study7Agent` ——机器级标准位置，实测能跑
3. 用户目录 ——实在没地方了才用，此时靠上面第 2 档兜底

## 7. 兼容预检（启动器负责）

| 检查 | 不通过时的行为 |
| --- | --- |
| Win7 SP1（版本号 6.1.7601） | 中文提示，直接退出 |
| SHA-2 代码签名支持补丁 | 提示装 KB4474419；我们带的是未签名便携版，理论上不依赖它，但要给老师一个方向 |
| 显示适配器 / GPU 初始化失败 | 自动重试一次并追加 `--disable-gpu`（`compat.ini` 里 `gpu=0` 可永久关掉） |
| 端口占用 | 换端口，不报错 |
| 解包目录被杀软拦住 | 写日志 `%LOCALAPPDATA%\Study7Agent\log\launcher.log`，中文提示加白名单 |

## 8. 已经踩到的坑

**注入型软件会改脏内核目录。** 开发机上跑一次 `--check`（那会儿还是用
`chrome.exe --version` 起的内核）之后，内核目录里多出一个 `log\`，里面是
`chrome.KSoftLaunch64.dll.log`、`chrome.kmagiccube64.dll.log`——本机装的那些
"管家 / 加速"类软件往浏览器进程里挂 DLL 留下的。两件事：

1. 那个 `log\` 目录已经进了裁剪名单（`DROP_DIRS`），而且是**目录级**删除；
2. 打包一律从 zip 重新解包，不拿跑过的目录打。

同一件事在教室一体机上更值得警惕：**注入型软件如果挂了内核的渲染进程，浏览器
会崩或者白屏**，而老师只会觉得"这个软件不好用"。M1 的客机验收要专门看一眼
这类软件在不在。真出问题的兜底是 `compat.ini` 里换启动参数（先试无 GPU，
再试禁用扩展与后台网络）。

**无头冒烟测试可以证明内核能起。** 不弹窗口、不碰老师会话：

```bat
chrome.exe --headless --disable-gpu --no-first-run ^
  --user-data-dir=%TEMP%\w7a-chromium-smoke --dump-dom about:blank
```

实测输出 `<html><head></head><body></body></html>`，说明内核、V8、渲染都正常。
（注意用 `--headless`，不是 `--headless=new`：那是 112 以后才有的。）
