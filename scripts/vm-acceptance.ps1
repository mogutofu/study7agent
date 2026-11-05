<#
.SYNOPSIS
  在真 Win7 客机里跑验收。默认只跑"必须上 Win7 才知道"的快检，十几秒出结果。

.DESCRIPTION
  为什么要单独一个脚本：以前每次验收都是"重灌 35MB 包 + 解压 4219 个文件 + 跑 435 项自检"，
  一次十几分钟——改一行 .ps1 也要等这么久。其实绝大多数改动**宿主上就能验**
  （Python 部分是跨平台的），真正只有 Win7 能验的就三件事：

    1. scripts\study7agent-float.ps1 在 PowerShell 2.0 上能不能跑起来
       —— 默认模式，只拷这一个脚本进去跑 -SelfTest，**约 10 秒**；
    2. dist\study7agent-<版本>.exe 能不能在 Win7 上整跑起来（1.1.0 起的主验收）
       —— -Exe：把 exe 送进去双击等价地跑一遍，验"内置 Chromium 109 在 Win7 上
       开得出界面"。首次要在客机解包 300 多兆，**几分钟起**；
    3. 整个包在客机上解压后自检
       —— -Full，约 6 分钟（自检本身就要 5 分半），**只在发布前跑**。

  -Full 也尽量**增量同步**：客机上已经有解压好的树时只拷变化的文件，
  不再重新解压那 4219 个文件（解压本身就要 2-3 分钟）。
  -Full 用的目录版 zip 现在是**开发件**（发布产物已经是单文件 exe），
  没现成的就用 scripts\make-package.py 现打一个。

.EXAMPLE
  .\python\python.exe 不参与；直接：
  powershell -File scripts\vm-acceptance.ps1              # 快检（默认，十几秒）
  powershell -File scripts\vm-acceptance.ps1 -Exe         # 单文件 exe 真机验收
  powershell -File scripts\vm-acceptance.ps1 -Full        # 全量自检
  powershell -File scripts\vm-acceptance.ps1 -Full -Extract   # 强制重新解压后再跑
#>
param(
  [switch]$Full,
  [switch]$Extract,
  [switch]$Exe,
  [int]$WaitMinutes = 60
)

$ErrorActionPreference = 'Stop'
$Vmrun = 'D:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe'
$Vmx = 'D:\Windows 7 x64\Windows 7 x64.vmx'
$User = 'seewo'
$Password = '123123qweqwe'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$GuestHome = 'C:\Users\seewo'
# 版本号**从 webui\core.py 读**：以前这里写死 0.6.0，改了版本号之后 -Full 会找不到 zip
# （报"没有 dist\study7agent-0.6.0.zip"，得先想起来改这里）。读文件用 UTF8，
# 免得 PowerShell 5.1 按 ANSI 读 core.py 里的中文；只取 ASCII 的那一行，读坏也没关系。
$Script:Version = '0.0.0'
try {
  $coreText = [System.IO.File]::ReadAllText((Join-Path $Root 'webui\core.py'),
                                            [System.Text.Encoding]::UTF8)
  $found = [regex]::Match($coreText, '(?m)^VERSION = "([^"]+)"')
  if ($found.Success) { $Script:Version = $found.Groups[1].Value }
} catch { }
$GuestDir = "$GuestHome\w7a-" + $Script:Version
$GuestPkg = "$GuestDir\study7agent-" + $Script:Version
# 送到客机上的 zip 名字。**先在变量里拼好**，不要写进 @(...) 数组元素里：
# 数组元素里用 `"…`" + $x + `"…`" 拼串时，PowerShell 会把那个 + 当成新的数组元素，
# 写出来的 .cmd 会在路径中间断成三行（客机上报 unzip_exit=123，什么都不做）。
# 真踩过：改版本号那次 -Full 就是卡在这里。
$GuestZip = "$GuestHome\w7a-" + $Script:Version + ".zip"
$Temp = Join-Path $env:TEMP 'w7a-vm'

function Invoke-Vmrun {
  param([string[]]$Arguments, [switch]$Quiet)
  $all = @('-T', 'ws', '-gu', $User, '-gp', $Password) + $Arguments
  $output = & $Vmrun @all 2>&1
  if (-not $Quiet -and $LASTEXITCODE -ne 0) {
    Write-Host ("  [vmrun] " + ($output -join ' ')) -ForegroundColor DarkYellow
  }
  return $output
}

function Send-File {
  param([string]$From, [string]$To)
  Invoke-Vmrun @('copyFileFromHostToGuest', $Vmx, $From, $To) -Quiet | Out-Null
}

function Get-File {
  param([string]$From, [string]$To)
  if (Test-Path -LiteralPath $To) { Remove-Item -LiteralPath $To -Force }
  Invoke-Vmrun @('copyFileFromGuestToHost', $Vmx, $From, $To) -Quiet | Out-Null
  if (Test-Path -LiteralPath $To) { return (Get-Content -LiteralPath $To -Encoding UTF8 -Raw) }
  return ''
}

function Write-AsciiCmd {
  param([string]$Path, [string[]]$Lines)
  # 客机上的 .cmd 必须纯 ASCII + CRLF：cmd.exe 按 OEM 代码页解析批处理
  $text = ($Lines -join "`r`n") + "`r`n"
  [System.IO.File]::WriteAllBytes($Path, [System.Text.Encoding]::ASCII.GetBytes($text))
}

function Assert-VmRunning {
  $running = (& $Vmrun list) -join "`n"
  if ($running -notmatch [regex]::Escape($Vmx)) {
    Write-Host "客机没开机，先启动它（nogui = 不弹窗口）..." -ForegroundColor Yellow
    & $Vmrun -T ws start $Vmx nogui | Out-Null
    Start-Sleep -Seconds 45
  }
}

if (-not (Test-Path $Temp)) { New-Item -ItemType Directory -Path $Temp | Out-Null }
Assert-VmRunning

# --------------------------------------------------------------------------
# 单文件 exe 真机验收（-Exe）：把 dist\study7agent-<版本>.exe 送进客机整跑一次
#
# 这里验的是**只有 Win7 才能验**的那一条：随包的内置 Chromium 109 能不能在 Win7 上
# 把界面开出来（开发机是 Win11，验不了）。判定不猜 vmrun 的退出码，读客机写回来的报告：
#   * logs\exe.log 里有 "Study7Agent WebUI"（服务起来了）
#   * 报告里有 chrome.exe 的进程数和标题（标题里要带 Study7Agent）
#   * 没有升到 --no-sandbox 那一档（升了不算失败，但要报出来：那是安全上的让步）
#
# 客机上的 .cmd 必须纯 ASCII：报告里那几处中文判定放到主机侧做，.cmd 里只出现
# ASCII 关键字（"Study7Agent WebUI" 就是 ASCII，正好当锚点）。
# --------------------------------------------------------------------------
if ($Exe) {
  # 变量名别叫 $exe：PowerShell 变量大小写不敏感，$exe 和上面那个 [switch]$Exe 是同一个，
  # 赋值会直接报"不能把字符串转成 SwitchParameter"（真踩过：脚本一行没跑就退出）。
  $exeFile = Join-Path $Root ('dist\study7agent-' + $Script:Version + '.exe')
  if (-not (Test-Path $exeFile)) { throw "没有 $exeFile，先跑 scripts\make-release.py" }
  $guestExeDir = "$GuestHome\w7a-exe"
  $guestExe = "$guestExeDir\study7agent.exe"
  $guestStampFile = "$guestExeDir\stamp.txt"
  $report = "$GuestHome\w7a-exe-report.txt"
  $runner = Join-Path $Temp 'exe-check.cmd'
  $stampLocal = Join-Path $Temp 'exe-stamp.txt'
  # 客机缓存的判据必须是**内容哈希**，不能是"版本 + 字节数"：重编一次大小常常一模一样，
  # 那样就会拿客机上那个旧 exe 跑完验收还报通过——等于白验。（Get-FileHash 是 PS 4+，
  # 这里按 PS 2.0 能跑的写法用 .NET 直接算。）
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $stream = [System.IO.File]::OpenRead($exeFile)
  try { $digest = $sha.ComputeHash($stream) } finally { $stream.Close(); $sha.Clear() }
  $localText = $Script:Version + '-' + ((($digest | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0, 16))
  [System.IO.File]::WriteAllText($stampLocal, $localText, [System.Text.Encoding]::ASCII)

  Invoke-Vmrun @('createDirectoryInGuest', $Vmx, $guestExeDir) -Quiet | Out-Null
  $guestText = (Get-File $guestStampFile (Join-Path $Temp 'guest-stamp.txt')).Trim()
  if ($guestText -ne $localText) {
    Write-Host ("送 exe 进客机（{0:N1} MB，慢一点是正常的）..." -f ((Get-Item $exeFile).Length / 1MB)) -ForegroundColor Cyan
    Send-File $exeFile $guestExe
    Send-File $stampLocal $guestStampFile
  } else {
    Write-Host "客机上已经是同一个 exe，跳过传输" -ForegroundColor Cyan
  }

  Write-AsciiCmd $runner @(
    '@echo off',
    'setlocal enabledelayedexpansion',
    'set "DIR=C:\Users\seewo\w7a-exe"',
    'set "OUT=C:\Users\seewo\w7a-exe-report.txt"',
    'set "LOG=%DIR%\logs\exe.log"',
    'echo STAGE=boot > "%OUT%"',
    'taskkill /IM study7agent.exe /F >nul 2>&1',
    'if exist "%LOG%" del /q "%LOG%"',
    'rem clear last run marker + old icon so this run really is a first start',
    'if exist "%DIR%\config\installed.txt" del /q "%DIR%\config\installed.txt"',
    'if exist "%USERPROFILE%\Desktop\Study7Agent.lnk" del /q "%USERPROFILE%\Desktop\Study7Agent.lnk"',
    'start "" "%DIR%\study7agent.exe" --debug-port 9333',
    'set READY=0',
    'for /L %%i in (1,1,150) do (',
    '  ping -n 11 127.0.0.1 >nul',
    '  if exist "%LOG%" (findstr /C:"Study7Agent WebUI" "%LOG%" >nul 2>&1 && set READY=1)',
    '  if "!READY!"=="1" goto :ready',
    ')',
    ':ready',
    'echo STAGE=ready >> "%OUT%"',
    'ping -n 31 127.0.0.1 >nul',
    'echo ---LOG--- >> "%OUT%"',
    'if exist "%LOG%" type "%LOG%" >> "%OUT%" 2>&1',
    'echo ---TITLE--- >> "%OUT%"',
    'powershell -NoProfile -Command "(Get-Process chrome -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle} | Select-Object -First 1).MainWindowTitle" >> "%OUT%" 2>&1',
    'echo ---COUNT--- >> "%OUT%"',
    'powershell -NoProfile -Command "(Get-Process chrome -ErrorAction SilentlyContinue).Count" >> "%OUT%" 2>&1',
    'echo ---SHORTCUT--- >> "%OUT%"',
    'if exist "%USERPROFILE%\Desktop\Study7Agent.lnk" (echo YES >> "%OUT%") else (echo NO >> "%OUT%")',
    'echo ---END--- >> "%OUT%"',
    '"%DIR%\study7agent.exe" --quit',
    'exit /b 0'
  )
  Send-File $runner "$GuestHome\w7a-exe-check.cmd"
  Invoke-Vmrun @('runProgramInGuest', $Vmx, 'C:\Windows\System32\cmd.exe', '/c', "$GuestHome\w7a-exe-check.cmd") -Quiet | Out-Null

  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  Write-Host "等客机把 exe 跑起来（首次解包要几分钟，最多 $WaitMinutes 分钟）..." -ForegroundColor Cyan
  $deadline = (Get-Date).AddMinutes($WaitMinutes)
  $text = ''
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 20
    $text = Get-File $report (Join-Path $Temp 'exe-report.txt')
    if ($text -match '---END---') { break }
    Write-Host ("  ...已等 {0:N0} 分钟" -f $sw.Elapsed.TotalMinutes) -ForegroundColor DarkGray
  }
  Write-Host ("用时 {0:N1} 分钟" -f $sw.Elapsed.TotalMinutes) -ForegroundColor Cyan
  if ($text -notmatch '---END---') {
    Write-Host "客机没在时限内跑完。报告片段：" -ForegroundColor Red
    Write-Host $text.Trim()
    exit 1
  }

  $log = ''
  $title = ''
  $count = ''
  $shortcut = ''
  if ($text -match '(?s)---LOG---(.*?)---TITLE---') { $log = $Matches[1] }
  if ($text -match '(?s)---TITLE---(.*?)---COUNT---') { $title = $Matches[1].Trim() }
  if ($text -match '(?s)---COUNT---(.*?)---SHORTCUT---') { $count = $Matches[1].Trim() }
  if ($text -match '(?s)---SHORTCUT---(.*?)---END---') { $shortcut = $Matches[1].Trim() }
  Write-Host ""
  Write-Host "客机上跑出来的关键几行：" -ForegroundColor Cyan
  foreach ($line in ($log -split "`r?`n")) {
    if ($line -match 'Study7Agent WebUI|服务已就绪|使用内置内核|级重试|no-sandbox|解包位置|解包完成') {
      Write-Host ("  " + $line.Trim())
    }
  }
  Write-Host ("  内核进程数：" + $count)
  Write-Host ("  窗口标题  ：" + $title)
  Write-Host ("  桌面快捷方式：" + $shortcut)

  $problems = @()
  if ($log -notmatch 'Study7Agent WebUI') { $problems += "服务没起来（日志里没有 Study7Agent WebUI）" }
  if ($log -notmatch '使用内置内核') { $problems += "没有用内置内核（退回老师机器上的浏览器了）" }
  if ($title -notmatch 'Study7Agent') { $problems += "没看到内核窗口（标题里没有 Study7Agent）" }
  if ($shortcut -ne 'YES') { $problems += "第一次启动没在桌面建出快捷方式" }
  if ($log -match 'no-sandbox') { Write-Host "  注意：这次升到了 --no-sandbox（说明这台机器上内核没法带沙箱跑，见 docs\embedded-browser.md §6）" -ForegroundColor Yellow }
  if ($log -match '级重试') { Write-Host "  注意：内核重试过（老显卡驱动或沙箱问题，看上面那几行）" -ForegroundColor Yellow }
  if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Host "exe 真机验收没过：" -ForegroundColor Red
    foreach ($item in $problems) { Write-Host ("  - " + $item) -ForegroundColor Red }
    exit 1
  }
  Write-Host ""
  Write-Host "exe 真机验收通过：Win7 上服务起来了、用的是内置 Chromium 109、界面窗口也开出来了" -ForegroundColor Green
  exit 0
}

# --------------------------------------------------------------------------
# 快检：只有这一步是"非 Win7 不可"的
# --------------------------------------------------------------------------
if (-not $Full) {
  $scriptPath = Join-Path $Root 'scripts\study7agent-float.ps1'
  $runner = Join-Path $Temp 'float-check.cmd'
  Write-AsciiCmd $runner @(
    '@echo off',
    'set "OUT=C:\Users\seewo\w7a060-float.txt"',
    'del /q "%OUT%" 2>nul',
    'powershell -NoProfile -Command "$PSVersionTable.PSVersion.ToString()" > "%OUT%" 2>&1',
    'powershell -NoProfile -STA -ExecutionPolicy Bypass -File "C:\Users\seewo\study7agent-float.ps1" -SelfTest >> "%OUT%" 2>&1',
    'echo FLOATEXIT=%ERRORLEVEL% >> "%OUT%"'
  )
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  Write-Host "快检：把悬浮工具栏脚本送进客机跑 -SelfTest ..." -ForegroundColor Cyan
  Send-File $scriptPath "$GuestHome\study7agent-float.ps1"
  Send-File $runner "$GuestHome\w7a-float.cmd"
  Invoke-Vmrun @('runProgramInGuest', $Vmx, 'C:\Windows\System32\cmd.exe', '/c', "$GuestHome\w7a-float.cmd") -Quiet | Out-Null
  $text = Get-File "$GuestHome\w7a060-float.txt" (Join-Path $Temp 'float.txt')
  Write-Host $text.Trim()
  Write-Host ("用时 {0:N1} 秒" -f $sw.Elapsed.TotalSeconds) -ForegroundColor Cyan
  # 机器要读的那一行必须是 OK|...：闭包抓对了 Key、本地动作接对了、折叠正常、
  # Find-AppWindow 真的能认出窗口（find=True）、QQ 过滤器真的把图片/文档窗口排掉了（qq=True/N，
  # 后面的 N 是不配过滤器时会选中几个，用来证明这条断言不是恒真）、
  # 没标题但像主窗口的窗口也认得出（untitled=True）、zh= 的码点对（少了 BOM 就不对）、
  # "收在托盘里"判得出来、而且是按它自己的热键叫出来而不是硬拽（hotkey=True）、
  # 折叠态的控件落在圆角之内（collapsedFit=True，贴到底边会被切掉一截）、
  # 「显示界面」的三条路都对（show=True：窗口关掉后能重新启动 exe 把界面开回来）、
  # exe 那条路走得通（exe=True）
  if ($text -match 'OK\|buttons=\d+\|handler=True\|action=\w+\|local=True\|show=True\|exe=True\|collapse=\d+/\d+\|collapsedFit=True\|collapsedCount=1\|clear=True\|(?:[A-Za-z]+=[^|]*\|)*find=True\|qq=True/\d+\|untitled=True\|zh=36830,19981,19978\|hotkey=True') {
    Write-Host "快检通过：PS 2.0 上能建窗口、闭包对、四个本地动作接对、「显示界面」能把关掉的界面重新开出来、折叠只剩一个图标且让开右上角、窗口识别正常、QQ 只切聊天窗、没标题的主窗口也认得出、托盘里的 QQ 走热键、BOM 正确" -ForegroundColor Green
    exit 0
  }
  Write-Host "快检没过——上面那行不是预期的 OK|...，逐项看是哪一段不对" -ForegroundColor Red
  exit 1
}

# --------------------------------------------------------------------------
# 全量：只在发布前跑
# --------------------------------------------------------------------------
$zip = Join-Path $Root ('dist\study7agent-' + $Script:Version + '.zip')
if (-not (Test-Path $zip)) {
  # 发布产物已经是单文件 exe（make-package.py 打的目录版 zip 现在只用于客机全量自检），
  # 所以没现成的就现打一个，别让 -Full 因为"没有 zip"直接挂掉。
  Write-Host "没有目录版 zip，先用 make-package.py 现打一个（仅供客机全量自检）..." -ForegroundColor Yellow
  & (Join-Path $Root 'python\python.exe') (Join-Path $Root 'scripts\make-package.py')
  if (-not (Test-Path $zip)) { throw "还是没打出 $zip" }
}

# ★ 这一段有两个坑，都是真机上量出来的，别改回去：
#   1) **不要**用 `cmd /c "x.cmd > out.txt"` 这种带重定向的内联命令：vmrun 会把参数按空格
#      拆开，`>` 根本没被当成重定向，输出文件永远是上一次的旧内容（客机上那份
#      w7a-probe.txt 停在 9/18，于是每次都误判成"没有解压好的树"）。
#   2) **不要**用 `cmd /c "a & b"` 串两条命令：同样被拆开，客机直接报"系统找不到指定的路径"，
#      exit=1 —— 自动解压这条路以前从来没真正跑通过（能过只是因为客机上已经有解压好的树）。
#   改法就是跟下面那个 w7a-full.cmd 一样：把命令写成**客机上的 .cmd 文件**，
#   重定向写在文件里，再 runProgramInGuest 跑这个文件。
$needExtract = $Extract
if (-not $needExtract) {
  $probe = Join-Path $Temp 'probe.cmd'
  Write-AsciiCmd $probe @(
    '@echo off',
    "set `"OUT=$GuestHome\w7a-probe.txt`"",
    'del /q "%OUT%" 2>nul',
    "if exist `"$GuestPkg\webui\server.py`" (echo HAVE> `"%OUT%`") else (echo MISSING> `"%OUT%`")"
  )
  Send-File $probe "$GuestHome\w7a-probe.cmd"
  Invoke-Vmrun @('runProgramInGuest', $Vmx, 'C:\Windows\System32\cmd.exe', '/c',
                 "$GuestHome\w7a-probe.cmd") -Quiet | Out-Null
  $answer = (Get-File "$GuestHome\w7a-probe.txt" (Join-Path $Temp 'probe.txt')).Trim()
  if ($answer -notmatch 'HAVE') { $needExtract = $true }
}

if ($needExtract) {
  Write-Host "客机上没有解压好的树（或用 -Extract 强制），重新解压整个包（约 2-3 分钟）..." -ForegroundColor Yellow
  $unzip = Join-Path $Temp 'unzip.ps1'
  Copy-Item (Join-Path $Root 'scripts\vm-unzip.ps1') $unzip -Force
  Send-File $unzip "$GuestHome\w7a-unzip.ps1"
  Send-File $zip $GuestZip
  $unzipCmd = Join-Path $Temp 'unzip.cmd'
  Write-AsciiCmd $unzipCmd @(
    '@echo off',
    "set `"OUT=$GuestHome\w7a-unzip.txt`"",
    'del /q "%OUT%" 2>nul',
    "if exist `"$GuestDir`" rmdir /s /q `"$GuestDir`"",
    "powershell -NoProfile -ExecutionPolicy Bypass -File `"$GuestHome\w7a-unzip.ps1`" `"$GuestZip`" `"$GuestDir`" `"$GuestPkg\webui\server.py`" >> `"%OUT%`" 2>&1",
    'echo unzip_exit=%errorlevel% >> "%OUT%"'
  )
  Send-File $unzipCmd "$GuestHome\w7a-unzip.cmd"
  Invoke-Vmrun @('runProgramInGuest', $Vmx, 'C:\Windows\System32\cmd.exe', '/c',
                 "$GuestHome\w7a-unzip.cmd") -Quiet | Out-Null
  # 解压到底成没成，看客机写回来的报告，而不是猜 vmrun 的退出码（它是 cmd 的，不代表解压结果）
  $report = (Get-File "$GuestHome\w7a-unzip.txt" (Join-Path $Temp 'unzip.txt')).Trim()
  if ($report -notmatch 'OK') {
    Write-Host ("  解压没成功，客机回报：" + $report) -ForegroundColor Red
    throw "客机上解压失败（见上面客机回报）。可以手动在客机上解压 $zip 到 $GuestDir。"
  }
  Write-Host "  解压完成" -ForegroundColor Green
} else {
  # 增量同步：只拷会变的那些文件，几秒钟的事
  Write-Host "客机上已有解压好的树，只同步变化的文件..." -ForegroundColor Cyan
  $files = @()
  foreach ($name in (Get-ChildItem (Join-Path $Root 'webui') -Filter '*.py' -File)) {
    $files += "webui\$($name.Name)"
  }
  # 化学数据（chemdata\*.json）必须一起同步：漏了它客机跑的还是旧数据，
  # 于是"新加的试剂/焰色/常见化工品"在客机上全是失败（2026-09-28 就是这么栽的）。
  foreach ($name in (Get-ChildItem (Join-Path $Root 'webui\chemdata') -Filter '*.json' -File)) {
    $files += "webui\chemdata\$($name.Name)"
  }
  foreach ($name in (Get-ChildItem (Join-Path $Root 'webui\ui') -File)) {
    $files += "webui\ui\$($name.Name)"
  }
  foreach ($name in (Get-ChildItem (Join-Path $Root 'scripts') -Filter '*.ps1' -File)) {
    $files += "scripts\$($name.Name)"
  }
  # scripts\*.py 也要同步：自检里有直接 import 它们的用例（打包流程、抓取脚本），
  # 不同步的话客机上跑的是旧脚本，测试期望值对不上（同一天栽过第二次）。
  foreach ($name in (Get-ChildItem (Join-Path $Root 'scripts') -Filter '*.py' -File)) {
    $files += "scripts\$($name.Name)"
  }
  foreach ($relative in $files) {
    Send-File (Join-Path $Root $relative) "$GuestPkg\$relative"
  }
  Write-Host ("  同步了 {0} 个文件" -f $files.Count)
}

$runner = Join-Path $Temp 'full-run.cmd'
Write-AsciiCmd $runner @(
  '@echo off',
  'set "U=C:\Users\seewo"',
  "set `"PKG=$GuestPkg`"",
  'set "OUT=%U%\w7a060-test.txt"',
  'del /q "%OUT%" 2>nul',
  'set PYTHONIOENCODING=utf-8',
  'set W7A_TEST_TIMEOUT=120',
  'pushd "%PKG%"',
  '"%PKG%\python\python.exe" webui\test_webui.py > "%OUT%" 2>&1',
  'popd',
  'echo __DONE__ >> "%OUT%"'
)
Send-File $runner "$GuestHome\w7a-full.cmd"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Invoke-Vmrun @('runProgramInGuest', $Vmx, '-noWait', 'C:\Windows\System32\cmd.exe', '/c', "$GuestHome\w7a-full.cmd") -Quiet | Out-Null
Write-Host "客机上开始跑全量自检（约 6 分钟）。每 20 秒报一次进度，跑完自己停：" -ForegroundColor Cyan

$last = ''
while ($sw.Elapsed.TotalMinutes -lt $WaitMinutes) {
  Start-Sleep -Seconds 20
  $text = Get-File "$GuestHome\w7a060-test.txt" (Join-Path $Temp 'test.txt')
  if (-not $text) { continue }
  if ($text -match '__DONE__') {
    $tail = ($text -split "`n" | Where-Object { $_ -match '\S' } | Select-Object -Last 6) -join "`n"
    Write-Host $tail
    Write-Host ("全量用时 {0:N0} 秒" -f $sw.Elapsed.TotalSeconds) -ForegroundColor Cyan
    # 判定要看**unittest 的汇总行**，别拿"某一行恰好是 OK"当通过：
    # 客机上明明跑出 FAILED (failures=1, errors=5) 却报过"全量通过"——测试输出里
    # 随便哪个用例打印一个单独的 OK 就被这条松正则骗了（真踩过）。
    if ($text -match '(?m)^FAILED\b') {
      Write-Host "全量有失败项，往上翻 FAIL/ERROR" -ForegroundColor Red
      exit 1
    }
    if ($text -match '(?m)^OK( \(skipped=\d+\)| \(skips=\d+\))?\s*$') {
      Write-Host "全量通过" -ForegroundColor Green
      exit 0
    }
    Write-Host "全量没跑完或没有汇总行，请人工确认" -ForegroundColor Red
    exit 1
  }
  $line = ($text -split "`n" | Where-Object { $_ -match '\.\.\. ' } | Select-Object -Last 1)
  if ($line -and $line -ne $last) {
    $last = $line
    Write-Host ("  [{0:N0}s] {1}" -f $sw.Elapsed.TotalSeconds, $line.Trim())
  }
}
Write-Host "等超时了（$WaitMinutes 分钟）。客机上的进程还在跑，过一会儿再拷 $GuestHome\w7a060-test.txt 看" -ForegroundColor Yellow
exit 2
