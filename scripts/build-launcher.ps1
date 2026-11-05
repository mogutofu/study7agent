# Study7Agent.exe 的编译脚本（Windows 7 SP1 x64 自带的东西就能跑，不需要装任何 SDK）
#
#   powershell -ExecutionPolicy Bypass -File scripts\build-launcher.ps1
#
# 用 .NET Framework 3.5 的 C# 编译器把 launcher\ 下的所有 .cs（Study7Agent.cs 主流程 +
# Payload.cs 单文件解包）编成仓库根目录的 Study7Agent.exe。产物是 CLR 2.0 的
# （Win7 SP1 自带 .NET 3.5 就能跑，不依赖 .NET 4）。
#
# 三个必须记住的点：
#   1. /noconfig —— 不用 csc.rsp 里那串默认引用（里面有 System.Core），引用只有三个：
#      System.dll / System.Windows.Forms.dll / System.Drawing.dll
#   2. /codepage:65001 —— 源码是 UTF-8 无 BOM；不加这个，csc 3.5 会按本地代码页（中文机器
#      上是 GBK）解码源文件，界面上的中文全变乱码（而且不会有任何编译错误提示你）
#   3. /win32icon:agent.ico —— exe 自己的图标；托盘图标是运行时从 agent.ico 读的
#
# 这个文件必须存成 **UTF-8 带 BOM**：PowerShell 2.0 读不带 BOM 的 .ps1 会按本地代码页解码。
# 没有 BOM 时脚本开头的注释就变成乱码，紧接着就是一堆看不懂的语法错误。

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$sourceDir = Join-Path $root 'launcher'
$sources = @(Get-ChildItem $sourceDir -Filter *.cs | Sort-Object Name | ForEach-Object { $_.FullName })
$icon = Join-Path $root 'agent.ico'
$output = Join-Path $root 'Study7Agent.exe'

if ($sources.Count -eq 0) {
  Write-Host "[error] $sourceDir 下没有 .cs 源码" -ForegroundColor Red
  exit 1
}

# 32 位和 64 位的编译器都在同一个 .NET 3.5 安装里，产出的 exe 都是 AnyCPU，用哪个都行。
$candidates = @(
  "$env:SystemRoot\Microsoft.NET\Framework64\v3.5\csc.exe",
  "$env:SystemRoot\Microsoft.NET\Framework\v3.5\csc.exe"
)
$csc = $null
foreach ($item in $candidates) {
  if (Test-Path $item) { $csc = $item; break }
}
if ($null -eq $csc) {
  Write-Host "[error] 找不到 .NET Framework 3.5 的 C# 编译器（csc.exe）。" -ForegroundColor Red
  Write-Host "        找过这些位置：" -ForegroundColor Red
  foreach ($item in $candidates) { Write-Host "          $item" -ForegroundColor Red }
  Write-Host "        Win7 上请先装 .NET Framework 3.5（控制面板 - 程序和功能 - 打开或关闭 Windows 功能）。" -ForegroundColor Red
  exit 1
}

$arguments = @(
  '/noconfig',
  '/target:winexe',
  '/platform:anycpu',
  '/codepage:65001',
  '/optimize+',
  '/warnaserror-',
  "/out:$output",
  "/reference:System.dll",
  "/reference:System.Windows.Forms.dll",
  "/reference:System.Drawing.dll"
)
if (Test-Path $icon) { $arguments += "/win32icon:$icon" }
$arguments += $sources

Write-Host "csc : $csc"
foreach ($item in $sources) { Write-Host "src : $item" }
Write-Host "out : $output"
Write-Host ""
Write-Host ("& '{0}' {1}" -f $csc, ($arguments -join ' '))
Write-Host ""

& $csc $arguments
if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "[error] 编译失败，退出码 $LASTEXITCODE" -ForegroundColor Red
  exit $LASTEXITCODE
}

if (-not (Test-Path $output)) {
  Write-Host "[error] 编译器报告成功，但没有产出 $output" -ForegroundColor Red
  exit 1
}

$file = Get-Item $output
# 不用 Get-FileHash：那是 PowerShell 4.0 才有的，这个脚本要能在 Win7 的 PS 2.0 上跑
$sha = [System.Security.Cryptography.SHA256]::Create()
$stream = [System.IO.File]::OpenRead($output)
try { $digest = $sha.ComputeHash($stream) } finally { $stream.Close() }
$hash = (($digest | ForEach-Object { $_.ToString('x2') }) -join '')
Write-Host ""
Write-Host ("[ok] {0}" -f $file.FullName)
Write-Host ("     大小   ：{0} 字节（{1:N1} KB）" -f $file.Length, ($file.Length / 1KB))
Write-Host ("     时间   ：{0}" -f $file.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
Write-Host ("     sha256 ：{0}" -f $hash)
Write-Host ""
Write-Host "提示：这个 exe 是 CLR 2.0 的（.NET 3.5 自带），Win7 SP1 x64 双击即可；不需要 .NET 4。"
exit 0
