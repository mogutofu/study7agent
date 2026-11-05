param([string]$Zip, [string]$Dest, [string]$Wait)
# 客机上没有 Python 时怎么解压：Win7 的 PowerShell 2.0 没有 Expand-Archive，
# 但资源管理器自己认识 zip，用 Shell.Application COM 就能解（不需要装任何东西）。
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Dest)) { New-Item -ItemType Directory -Path $Dest | Out-Null }
$shell = New-Object -ComObject Shell.Application
$zipNs = $shell.NameSpace($Zip)
if ($zipNs -eq $null) { Write-Output 'ERR cannot open zip'; exit 1 }
$destNs = $shell.NameSpace($Dest)
$destNs.CopyHere($zipNs.Items(), 16)
# CopyHere 是异步的：等目标文件出现，别 Sleep 一个固定秒数就往下跑
$deadline = (Get-Date).AddMinutes(20)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 3
  if (Test-Path -LiteralPath $Wait) { Write-Output 'OK'; exit 0 }
}
Write-Output 'ERR timeout'
exit 1
