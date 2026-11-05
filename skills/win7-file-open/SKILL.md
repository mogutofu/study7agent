---
name: win7-file-open
description: Win7 下用命令行打开本地文件、网页链接的可靠写法（绕开 start 的引号失败）
keywords: 打开文件,打开这个文件,start,rundll32,命令行打开,bat,批处理,路径有空格,引号,删除文件,改名,重命名,explorer
---

# Win7 File / URL Open Skill

## 症状与根因
- 症状：`start "" "C:\xxx\页.html"` 这类命令在 Win7 上报错，例如
  `'"C:\xxx\页.html"' 不是内部或外部命令，也不是可运行的程序或批处理文件`，或静默 exit=0 但什么都没打开。
- 根因：run_command 会把整条命令包进一层引号交给 `cmd /c`，Win7 的旧式引号解析（start 把第一个引号串当窗口标题）就崩了。
  这不是 start 本身的问题——同样的 `start "" "路径"` 写进 .bat 文件里能正常执行。
- 结论：带引号、带空格的路径是主要雷区；**不要靠加更多引号去修，换写法**。

## 可用写法

### A. 同目录、文件名无空格：for + start
```
cd /d C:\Users\seewo\Desktop\test\test && for %f in (*.html) do start %f
```
批量打开同类文件时最省事，也不受引号影响。缺点是只能按通配符匹配，不能精确指定文件名。

### B. 通用、最稳：先生成 .bat 再执行（路径含空格、中文、任意扩展名都用它）
1. 用 write_file 生成脚本（文件名不要带空格）：
```
@echo off
start "" "C:\Users\seewo\Desktop\test\test\my folder\open me.html"
```
2. 执行时**不要给 bat 路径加引号**，先 cd 到它所在目录：
```
cd /d C:\Users\seewo\Desktop\test\test && open_test.bat
```
   写成 `run_command('"C:\...\open_test.bat"')` 会失败（同样被引号坑）。
   若工作目录本身含空格，用 `cd /d 短路径` 或不带空格的中转目录。

### C. 单个本地 html 或网址：rundll32
```
cd /d C:\Users\seewo\Desktop\test\test && rundll32 url.dll,FileProtocolHandler launch_test.html
```
用相对路径最保险；对本地 html、http 链接都能唤起默认浏览器。注意：**它即使没打开任何页面也返回 exit=0**，必须用 `snapshot` / `screenshot` 核实。

## 不可靠的写法（别浪费时间）
- `start "" "C:\带空格的路径.html"`：如上，被外层引号解析搞坏。
- `explorer "C:\Users\seewo\Desktop\test\test\my folder\explorer ok.html"`：实测 exit=1，且只弹出一个「我的文档」窗口，页面没打开。
- 用引号包裹 .bat 路径执行：实测报 `'"C:\...\x.bat"' 不是内部或外部命令`。

## 删除 / 改名带空格的路径：同样有坑
- `rmdir /s /q "my folder"` 实测报 `系统找不到指定的文件`（exit=2）；`rmdir /s /q my*` 报 `文件名、目录名或卷标语法不正确`（exit=123）。
- 可行做法：先 `dir /x /ad` 读出 8.3 短名（例如 `MYFOLD~1`），再 `rmdir /s /q MYFOLD~1` —— 无引号无空格即成功。
- `del` / `move` / `copy` 同理：带空格路径优先用 8.3 短名，或 cd 到父目录后走相对路径。
- 多个目标写在一起时（`del /q a b`），只要有一个文件不存在就 exit=2 且 `&&` 后面的命令不再执行；**分批执行、每步单独核对**更省事。

## 验证与收尾
- 只看 exit code 会误判；执行后用 `snapshot` 看窗口标题（窗口清单在快照文字里），必要时 `screenshot` 看浏览器标签。
- 打开的窗口标题一般就是页面 `<title>`，测试时给不同文件写不同 title，一眼就能分辨哪个写法成功了。
- 打开网址：`start http://内网地址/`（不加引号）通常可行；URL 含 `&`、`?` 等需要引号时，改走写法 B 的 bat。
- 测试用的临时文件（bat、测试 html、含空格目录）用完即删，别留在用户工作区里。

## 编码注意
- cmd 默认 GBK，而 write_file 写的是 UTF-8。**bat 里尽量只用英文路径和英文文件名**；
  必须用中文时，在 bat 第一行加 `chcp 936 >nul`，并提醒用户该文件需按 ANSI/GBK 保存，否则中文会显示成乱码。
