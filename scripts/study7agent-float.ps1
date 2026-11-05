# Study7Agent 悬浮工具栏（Windows 7 / PowerShell 2.0 专用）
#
# 老师双击 start-webui.cmd 之后，网页旁边会出现这个常驻置顶的小工具条：
#   - 点最上面那条 = 只是按住拖动工具条；**点一下不再把界面切到前台**（老师要求：
#     别在误点的时候突然跳出程序窗口）。要打开界面请点中间的「显示界面」按钮
#   - 点「显示界面」= 把界面窗口切回前台，窗口被关掉了就重新开出来
#   - 中间两列按钮 = 打开白板/展台/QQ/切到PPT/回到桌面，或者用这个 agent 自己的课堂功能
#     （计时、抽学号、截图给AI、板书保存、课堂时间点、作图）
#   - 右上角「收起」= 缩成右边一条，只剩图标 + 当前时间 + 展开 + 退出
#   - 网页关了**只是把窗口状态记为已关闭**：服务照跑、工具栏照在，点「显示界面」能开回来
#
# 为什么是 PowerShell 而不是别的：Win7 自带 PowerShell 2.0 + .NET 3.5，里面就有 WinForms，
# 不需要装 Node / .NET 4 / Python 之外的东西（见 README §1 的硬性约束）。
#
# ★ 这个文件必须存成 **UTF-8 带 BOM**：PowerShell 2.0 读不带 BOM 的 .ps1 会按本地代码页
#   （中文机器是 GBK）解码，脚本里的中文会全变乱码，还可能因为乱码里蹦出引号/花括号
#   而报一堆莫名其妙的语法错误（错误行号还会指向完全无关的地方）。
#   很多编辑器保存时会静默丢掉 BOM，**改完务必确认前三个字节是 EF BB BF**，最省事的办法：
#       python\python.exe webui\test_webui.py TestFloatScript
#
# ★ PowerShell 2.0 的雷区（本文件刻意避开了，改的时候别踩回去）：
#   没有 ConvertFrom-Json / ConvertTo-Json（所以 /api/float/* 全返回 OK|... 纯文本）
#   没有 ::new()、没有 [pscustomobject]、没有 -in / -notin、没有 $PSScriptRoot
#   整段脚本先解析：Add-Type 必须排在用到那些类型的字面量前面
#   宿主的输出会按控制台宽度折行 —— 机器要读的那一行必须用 [Console]::Out 写，不能用 Write-Output

param(
  [string]$Api = "http://127.0.0.1:8770",
  [string]$Url = "",
  [string]$Token = "",
  [string]$Exe = "",
  [int]$PollSeconds = 2,
  [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'

# 只允许一个工具栏：老师双击两次启动器不该得到两个叠在一起的窗口。
# 用有名互斥体（Local\ = 只在当前登录会话内有效，正好是"这台机器的这个桌面"）。
# 自检模式跳过：跑自检的时候真工具栏可能正开着。
if (-not $SelfTest) {
  $Script:Single = New-Object System.Threading.Mutex -ArgumentList $false, "Local\study7agent-float"
  if (-not $Script:Single.WaitOne(0)) { exit 0 }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# 自检模式：不碰真实窗口（切 PPT / 最小化全部窗口这些动作在自检里只走通逻辑，不真做）
$Script:DryRun = [bool]$SelfTest
# 按钮的处理块登记表：New-ToolButton 在下面就要用到，必须先建好
$Script:Handlers = @{}
$Script:LastAction = ""
# 界面窗口被"叫到前台"的次数：自检靠它证明点顶栏 / 点图标**没有**再切前台
$Script:ShowAgentCalls = 0

# --------------------------------------------------------------------------
# Windows 窗口操作（P/Invoke）
#
# 为什么不直接用 WScript.Shell.AppActivate：**它对最小化的窗口直接返回 False 而且什么都不做**。
# 真机上实测：把 WPS 演示最小化之后 AppActivate 返回 False、窗口还停在最小化；
# 换成 ShowWindow(SW_RESTORE) + SetForegroundWindow（配合 AttachThreadInput 解前台锁）才切得动。
# 另外 ShowWindow 是异步的，所以下面要**重试并验证**"现在前台到底是不是它"。
# --------------------------------------------------------------------------
Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class W7AWindow
{
    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32.dll")] private static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] private static extern bool IsZoomed(IntPtr h);
    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint a, uint b, bool f);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr h, IntPtr after,
        int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] private static extern int GetWindowLong(IntPtr h, int index);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    [DllImport("user32.dll")] private static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);

    // 模拟老师自己按一组组合键（例如 QQ 的 Ctrl+Alt+Z = 提取消息）。
    // 为什么要有这个：收在托盘里的 QQ 用 ShowWindow 硬拽出来的窗口，QQ 自己仍然认为
    // "我还藏着"，老师看到的是一张点不动的空壳（真机反馈"打开的是个完全无法交互的假窗口"）。
    // 按它自己的热键 = 让 QQ 自己把真面板弹出来，跟老师用手点托盘图标是一回事。
    // 没注册这个热键的程序收到这几个键什么也不做，所以按错了没有副作用。
    public static void Hotkey(int[] keys)
    {
        if (keys == null || keys.Length < 2) return;
        for (int i = 0; i < keys.Length; i++)
        {
            keybd_event((byte)keys[i], 0, 0, IntPtr.Zero);
            System.Threading.Thread.Sleep(25);
        }
        for (int i = keys.Length - 1; i >= 0; i--)
        {
            keybd_event((byte)keys[i], 0, 2, IntPtr.Zero);            // KEYEVENTF_KEYUP
            System.Threading.Thread.Sleep(25);
        }
    }

    // "长得像应用程序主窗口"：有标题栏、能拉扯大小。
    // 进程里的辅助窗 / 隐藏壳（Electron 的 Chrome_WidgetWin_0、IME 窗）都没有这两样，
    // 靠它把"切成功了但老师什么也看不到"那种窗口挡在外面。
    public static bool IsAppWindow(IntPtr h)
    {
        int style = GetWindowLong(h, -16);              // GWL_STYLE
        const int CAPTION = 0x00C00000;
        const int THICKFRAME = 0x00040000;
        return (style & CAPTION) == CAPTION && (style & THICKFRAME) == THICKFRAME;
    }

    public static string Title(IntPtr h)
    {
        StringBuilder sb = new StringBuilder(512);
        GetWindowText(h, sb, 512);
        return sb.ToString();
    }

    public static bool Minimized(IntPtr h) { return IsIconic(h); }
    public static bool IsForeground(IntPtr h) { return GetForegroundWindow() == h; }

    // "这个窗口现在真的能用了"——**不能只看"前台是不是它"**。
    // 真机栽过：SetForegroundWindow 成功、GetForegroundWindow() 也等于它，
    // 但那是个 0 尺寸 / 最小化残影 / 挪到屏幕外的窗口，老师那边什么变化都看不到，
    // 日志里却一路"已切到 PPT"。所以这里把"看得见、没最小化、尺寸正常、
    // 不在 -25600 那个最小化停放点"全验一遍。
    public static bool Usable(IntPtr h)
    {
        if (h == IntPtr.Zero) return false;
        if (GetForegroundWindow() != h) return false;
        if (IsIconic(h) || !IsWindowVisible(h)) return false;
        RECT r;
        if (!GetWindowRect(h, out r)) return false;
        int w = r.R - r.L;
        int hh = r.B - r.T;
        if (w < 160 || hh < 120) return false;
        if (r.L <= -10000 || r.T <= -10000) return false;
        return true;
    }

    public static uint PidOf(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }

    // 给日志用的一句话：**"切到的是哪个窗口"是排查这类问题的唯一线索**——
    // 真机上就是靠它才发现"日志一路说切成功、老师什么也没看到"。
    public static string Describe(IntPtr h)
    {
        if (h == IntPtr.Zero) return "(没有窗口)";
        RECT r;
        if (!GetWindowRect(h, out r)) return "(取不到位置)";
        return "hwnd=" + h + " pid=" + PidOf(h) +
               " vis=" + (IsWindowVisible(h) ? 1 : 0) +
               " min=" + (IsIconic(h) ? 1 : 0) +
               " size=" + (r.R - r.L) + "x" + (r.B - r.T) +
               " at=(" + r.L + "," + r.T + ")" +
               " title=[" + Title(h) + "]";
    }

    // 所有有标题的顶层窗口，**包括收进托盘那种"隐藏"的**：
    // QQ 点右上角 X 是藏进托盘（IsWindowVisible 变 0），只枚举可见窗口就永远找不到它。
    // 没标题的窗口只放行"长得像主窗口"的（老版 QQ 收进托盘后主面板标题会变空，
    // 一条都不放行的话它永远找不到；全放行又会混进一堆看不见的辅助窗）。
    // 格式：pid|hwnd|visible|iconic|面积|像不像主窗口|标题
    public static string[] Windows()
    {
        List<string> rows = new List<string>();
        EnumWindows(delegate(IntPtr h, IntPtr p)
        {
            string t = Title(h);
            RECT r;
            GetWindowRect(h, out r);
            long area = (long)(r.R - r.L) * (r.B - r.T);
            bool main = IsAppWindow(h);
            if (t.Length == 0 && !(main && area >= 60000)) return true;
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            rows.Add(pid.ToString() + "|" + h.ToString() + "|" +
                     (IsWindowVisible(h) ? "1" : "0") + "|" +
                     (IsIconic(h) ? "1" : "0") + "|" + area.ToString() + "|" +
                     (main ? "1" : "0") + "|" + t);
            return true;
        }, IntPtr.Zero);
        return rows.ToArray();
    }

    public static bool Activate(IntPtr h, int tries)
    {
        if (h == IntPtr.Zero) return false;
        // 第一段：正常路径，**不碰 AttachThreadInput**。
        // 老师点工具栏按钮时工具栏自己就是前台窗口，Windows 允许它把别人叫到前台。
        // 有意不去 attach 输入队列：attach 没拆干净会把目标的输入队列挂住，
        // 表现就是"那个窗口点不动了、最小化和关闭都没反应"（真机上出过）。
        for (int i = 0; i < tries; i++)
        {
            if (Usable(h)) return true;
            ShowWindow(h, 5);                                  // 收进托盘的先"显示"出来
            ShowWindow(h, 9);                                  // SW_RESTORE
            BringWindowToTop(h);
            SetForegroundWindow(h);
            System.Threading.Thread.Sleep(120);
        }
        if (Usable(h)) return true;
        // 第二段：实在不行才动输入队列解前台锁，而且 try/finally 保证一定拆开
        uint dummy;
        uint target = GetWindowThreadProcessId(h, out dummy);
        uint mine = GetCurrentThreadId();
        if (target == mine) return false;
        for (int i = 0; i < 4; i++)
        {
            bool attached = false;
            try
            {
                attached = AttachThreadInput(mine, target, true);
                ShowWindow(h, 5);
                ShowWindow(h, 9);
                BringWindowToTop(h);
                SetForegroundWindow(h);
            }
            finally
            {
                if (attached) AttachThreadInput(mine, target, false);
            }
            System.Threading.Thread.Sleep(150);
            if (Usable(h)) return true;
        }
        return false;
    }

    // 窗口一大半在屏幕外时，老师连最小化按钮都点不到——真机出过：QQ 恢复到
    // 1536x864 却摆在 (48,21)，右边缘(x=1584)和底边(y=885)全在 1536x864 的屏幕外面，
    // 于是"打开的 QQ 窗口无法最小化和关闭"。切到前台之后顺手把它拉回可见区域。
    public static void Clamp(IntPtr h)
    {
        if (h == IntPtr.Zero || IsIconic(h) || IsZoomed(h)) return;
        RECT r;
        if (!GetWindowRect(h, out r)) return;
        int screenW = GetSystemMetrics(0);
        int screenH = GetSystemMetrics(1);
        if (screenW <= 0 || screenH <= 0) return;
        int width = r.R - r.L;
        int height = r.B - r.T;
        if (width <= 0 || height <= 0) return;
        int x = r.L;
        int y = r.T;
        if (width > screenW - 40) width = screenW - 40;
        if (height > screenH - 80) height = screenH - 80;
        if (x + width > screenW - 8) x = screenW - width - 8;
        if (y + height > screenH - 8) y = screenH - height - 8;
        if (x < 4) x = 4;
        if (y < 4) y = 4;
        if (x == r.L && y == r.T && width == (r.R - r.L) && height == (r.B - r.T)) return;
        SetWindowPos(h, IntPtr.Zero, x, y, width, height, 0x0004 | 0x0010);   // NOZORDER | NOACTIVATE
    }
}
"@

# --------------------------------------------------------------------------
# 日志：老师那边出问题时，logs\float.log 是唯一能事后查的东西
# --------------------------------------------------------------------------
$Script:Root = ""
$Script:ScriptPath = ""
try {
  # 用 .Path 而不是 .Definition：后者在某些宿主里返回的是脚本正文而不是路径
  $Script:ScriptPath = $MyInvocation.MyCommand.Path
  $Script:Root = Split-Path -Parent (Split-Path -Parent $Script:ScriptPath)
} catch {
  $Script:Root = (Get-Location).Path
}
$Script:LogFile = Join-Path $Script:Root "logs\float.log"

function Write-Log {
  param([string]$Text)
  try {
    $dir = Split-Path -Parent $Script:LogFile
    if (-not (Test-Path -LiteralPath $dir)) {
      New-Item -ItemType Directory -Path $dir | Out-Null
    }
    $line = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss") + "  " + $Text + "`r`n"
    [System.IO.File]::AppendAllText($Script:LogFile, $line, [System.Text.Encoding]::UTF8)
  } catch {
    # 日志写不进去绝不能连累工具栏本身
  }
}

# --------------------------------------------------------------------------
# HTTP：只用 .NET 3.5 就有的 WebRequest，响应按 UTF-8 读（不指定的话中文会乱码）
# --------------------------------------------------------------------------
$Script:ApiBase = $Api.TrimEnd("/")
$Script:AuthToken = $Token
$Script:Url = $Url

# 「显示界面」要重新启动的那个 exe：启动器（Study7Agent.exe）起工具栏时会用 -Exe 传进来。
# 老调用（server.py 的 -Api/-Url/-Token，或者老师手敲命令行）**不带这个参数也必须能跑**：
# 参数有默认值不会报错，这里再猜一下 —— 脚本在 `程序目录\runtime\<戳>\scripts\` 下，
# 所以 exe 在**往上三层**（scripts -> 戳 -> runtime -> 程序目录）；目录版则在一层。
$Script:Exe = $Exe
if ($Script:Exe -eq "" -and $Script:ScriptPath -ne "") {
  $cursor = $Script:ScriptPath
  for ($level = 0; $level -lt 4; $level++) {
    $cursor = Split-Path -Parent $cursor
    if ([string]::IsNullOrEmpty($cursor)) { break }
    $guess = Join-Path $cursor "Study7Agent.exe"
    if (Test-Path -LiteralPath $guess) { $Script:Exe = $guess; break }
  }
}
# 界面窗口认哪些进程名 / 标题关键词：**只认自带内核**（ungoogled-chromium 109，进程名 chrome）。
# 老师的 Edge/360 里开着同一个页面时标题一模一样，把它们放进名单就会"点显示界面把老师的浏览器
# 切到前台"（他报了两次）。所以名单里不再有 msedge/360*，并且下面找窗口还要再核一次 exe 路径。
$Script:AgentNames = @("chrome", "chromium")
$Script:AgentTitles = @("Study7Agent")
# 内核进程的身份证：**只有它才是"自带内核的界面窗口"**。
# 老师自己浏览器里也会开着 Study7Agent 的页面（标题一模一样），
# 光按"进程名 + 标题"找会把那个窗口切到前台 —— 老师看到的就是"打开了前台的浏览器页面"。
# 所以下面优先找 exe 路径里带 chromium-109 的那个窗口，找不到才退回按名字找。
$Script:KernelMark = "chromium-109"
# 界面窗口是不是"已经被关掉了"：只记状态，**绝不据此收服务**（见 Update-Status）
$Script:WindowClosed = $false

# 「天气」按钮打开的网站：要**逐小时 + 能定位到本地**，所以默认用和风天气的公众站
# （打开后浏览器会问定位，允许一次以后就直接是本地的逐小时预报）。
# 想换站点只改这一行，比如中国气象局 https://weather.cma.cn/ （更权威，但定位要自己点）。
$Script:WeatherUrl = "https://www.qweather.com/"

function Invoke-Api {
  param([string]$Method, [string]$Path, [string]$Body = "", [int]$TimeoutMs = 2000)
  $request = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($Script:ApiBase + $Path)
  $request.Method = $Method
  $request.Timeout = $TimeoutMs
  $request.ReadWriteTimeout = $TimeoutMs
  $request.ContentType = "application/json; charset=utf-8"
  if ($Script:AuthToken -ne "") {
    $request.Headers.Add("X-Auth-Token", $Script:AuthToken)
  }
  if ($Body -ne "") {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
    $request.ContentLength = $bytes.Length
    $stream = $request.GetRequestStream()
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Close()
  }
  $response = $request.GetResponse()
  $reader = New-Object System.IO.StreamReader -ArgumentList $response.GetResponseStream(), ([System.Text.Encoding]::UTF8)
  $text = $reader.ReadToEnd()
  $reader.Close()
  $response.Close()
  return $text
}

# "OK|消息|focus=1" -> @(成功?, 消息, 要不要把网页切到前台)
function Split-Reply {
  param([string]$Text)
  $parts = ([string]$Text).Split("|")
  $ok = ($parts.Length -gt 0 -and $parts[0] -eq "OK")
  $message = ""
  if ($parts.Length -gt 1) { $message = $parts[1] }
  $focus = $false
  if ($parts.Length -gt 2 -and $parts[2] -eq "focus=1") { $focus = $true }
  return @($ok, $message, $focus)
}

# --------------------------------------------------------------------------
# 配色：跟网页的**浅色**主题对齐（白底 + DeepSeek 蓝 #4d6bfe）。
# 这几个值就是 webui\ui\style.css 里 html[data-theme="light"] 那一组，改要一起改。
# --------------------------------------------------------------------------
$ColorBg = [System.Drawing.Color]::FromArgb(247, 249, 255)
$ColorPanel = [System.Drawing.Color]::FromArgb(255, 255, 255)
$ColorPanelHi = [System.Drawing.Color]::FromArgb(242, 245, 255)
$ColorHover = [System.Drawing.Color]::FromArgb(232, 237, 253)
$ColorLine = [System.Drawing.Color]::FromArgb(221, 228, 245)
$ColorText = [System.Drawing.Color]::FromArgb(27, 32, 48)
$ColorMuted = [System.Drawing.Color]::FromArgb(95, 107, 134)
$ColorAccent = [System.Drawing.Color]::FromArgb(77, 107, 254)
$ColorAccent2 = [System.Drawing.Color]::FromArgb(53, 80, 240)
$ColorOk = [System.Drawing.Color]::FromArgb(23, 166, 115)
$ColorErr = [System.Drawing.Color]::FromArgb(226, 59, 69)
$ColorExit = [System.Drawing.Color]::FromArgb(226, 59, 69)

$FontButton = New-Object System.Drawing.Font -ArgumentList "Microsoft YaHei", 10
$FontSmall = New-Object System.Drawing.Font -ArgumentList "Microsoft YaHei", 8.5
$FontTiny = New-Object System.Drawing.Font -ArgumentList "Microsoft YaHei", 8

# 圆角：WinForms 没有现成的，用 Region + GraphicsPath 自己削
function New-RoundRegion {
  param([int]$Width, [int]$Height, [int]$Radius)
  if ($Width -le 1 -or $Height -le 1) { return $null }
  if ($Radius * 2 -ge $Width) { $Radius = [int]($Width / 2) - 1 }
  if ($Radius * 2 -ge $Height) { $Radius = [int]($Height / 2) - 1 }
  if ($Radius -lt 1) { return $null }
  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $d = $Radius * 2
  $path.AddArc(0, 0, $d, $d, 180, 90)
  $path.AddArc($Width - $d - 1, 0, $d, $d, 270, 90)
  $path.AddArc($Width - $d - 1, $Height - $d - 1, $d, $d, 0, 90)
  $path.AddArc(0, $Height - $d - 1, $d, $d, 90, 90)
  $path.CloseFigure()
  $region = New-Object System.Drawing.Region -ArgumentList $path
  $path.Dispose()
  return $region
}

# --------------------------------------------------------------------------
# 找窗口 / 切窗口
# --------------------------------------------------------------------------
function Get-ProcessNameMap {
  $map = @{}
  foreach ($item in (Get-Process -ErrorAction SilentlyContinue)) {
    $map[[int]$item.Id] = ([string]$item.ProcessName).ToLower()
  }
  return $map
}

function Get-WindowList {
  $rows = @()
  foreach ($line in [W7AWindow]::Windows()) {
    $parts = $line.Split("|")
    if ($parts.Length -lt 6) { continue }
    $main = 0
    $title = $parts[5]
    if ($parts.Length -ge 7) { $main = [int]$parts[5]; $title = $parts[6] }
    $rows += @{ Pid = [int]$parts[0]; Handle = [IntPtr][long]$parts[1];
                Visible = [int]$parts[2]; Iconic = [int]$parts[3];
                Area = [long]$parts[4]; Main = $main; Title = $title }
  }
  return $rows
}

# 标题命中优先（最准），其次"看得见的窗口按进程名"，最后才是收进托盘的那个（挑最大的，
# 32x38 那种是辅助窗口，恢复出来也没用）。
# 按可信度返回**一串**候选窗口（不是只给一个）：
# 一个程序往往有好几个顶层窗口（主窗口、辅助窗、隐藏窗），
# 挑错了就会"切成功了但老师什么也看不到"。调用方挨个试，用第一个真的能用的。
function Find-AppWindows {
  # -TitleOnly：只认"标题像这个东西"的窗口。PPT 必须用它——
  # WPS 常年挂着一个 wpp.exe "server widget" 后台窗口，按进程名会命中它，
  # 于是浮窗以为"已经有 PPT 了"，永远不去打开课件（老师反馈过这个）。
  # -VisibleOnly：只要"此刻真的看得见"的。用来判断"这东西是不是收在托盘里了"
  # （收在托盘里就该先按它自己的热键叫一声，而不是硬 ShowWindow 拽出来）。
  param([string[]]$Names, [string[]]$TitleWords, [switch]$TitleOnly,
        [string[]]$Skip = @(), [string[]]$Prefer = @(), [switch]$VisibleOnly)
  $map = Get-ProcessNameMap
  $rows = @(Get-WindowList)
  # ★ QQ 的坑：图片查看器、腾讯文档、小世界都是同一个 qq.exe，光按进程名匹配会切到
  #   老师并不想要的那个窗口（他反馈"点 QQ 不切聊天窗"就是这么来的）。Skip 把这些排除。
  $kept = @()
  foreach ($row in $rows) {
    $drop = $false
    foreach ($word in $Skip) {
      if ($word -ne "" -and $row.Title -like ("*" + $word + "*")) { $drop = $true; break }
    }
    if (-not $drop) { $kept += $row }
  }
  $rows = $kept
  if ($VisibleOnly) {
    # 只看"此刻真的看得见"的：隐藏窗口那一档自然就空了（不用改下面的分档逻辑）
    $vis = @()
    foreach ($row in $rows) { if ($row.Visible -eq 1) { $vis += $row } }
    $rows = $vis
  }
  $byTitle = @()
  if ($Prefer.Count -gt 0) {
    # Prefer 的窗口排在最前面（同一次调用里先被试到）
    foreach ($word in $Prefer) {
      foreach ($row in $rows) {
        if ($word -ne "" -and $row.Title -like ("*" + $word + "*") -and $row.Visible -eq 1) {
          $byTitle += $row
        }
      }
    }
  }
  foreach ($row in $rows) {
    $hit = $false
    foreach ($word in $TitleWords) {
      if ($word -ne "" -and $row.Title -like ("*" + $word + "*")) { $hit = $true; break }
    }
    if (-not $hit) { continue }
    if ($Prefer.Count -gt 0 -and $byTitle -contains $row) { continue }
    # 标题对了还不够：进程也得是这一类（浏览器标签页写着 xxx.pptx 不算）
    if ($Names.Count -gt 0) {
      $processName = ""
      if ($map.ContainsKey($row.Pid)) { $processName = $map[$row.Pid] }
      $known = $false
      foreach ($name in $Names) {
        if ($processName -eq ([string]$name).ToLower()) { $known = $true; break }
      }
      if (-not $known) { continue }
    }
    $byTitle += $row
  }
  $out = @()
  foreach ($row in $byTitle) { $out += $row.Handle }
  if ($TitleOnly) { return $out }
  # 按 Names 的**先后顺序**取候选：调用方用这个表达优先级
  # （例如演示程序里 WPS 排在 PowerPoint 前面）。同一个进程里"看得见的"优先，
  # 收进托盘的要够大才要（32x38 那种辅助窗恢复出来什么也看不见）。
  foreach ($name in $Names) {
    $want = ([string]$name).ToLower()
    # 三档，按可信度排：看得见的有标题窗口 -> 收进托盘的有标题窗口 -> 没标题但像主窗口的。
    # ★ 没标题那一档是给老版 QQ 留的：它收进托盘之后主面板的标题会变空，一条都不放行
    #   就永远找不到 —— 老师那边是"QQ 明明在右下角，点按钮却去启动一个新的"。
    #   但它排最后、而且只认 WS_CAPTION + WS_THICKFRAME + 面积够大：进程里那些看不见的
    #   辅助窗 / 隐藏壳（Electron 的 Chrome_WidgetWin_0、IME 窗）被当成"切到了"，
    #   老师那边就是"日志说已切到、屏幕上什么也没发生"。
    foreach ($pass in @("visible", "hidden", "untitled")) {
      foreach ($row in $rows) {
        $processName = ""
        if ($map.ContainsKey($row.Pid)) { $processName = $map[$row.Pid] }
        if ($processName -ne $want) { continue }
        if ($pass -eq "untitled") {
          if ($row.Title -ne "") { continue }
          if ($row.Main -ne 1 -or $row.Area -lt 60000) { continue }
          $out += $row.Handle
          continue
        }
        if ($row.Title -eq "") { continue }
        if ($pass -eq "visible") {
          if ($row.Visible -eq 1) { $out += $row.Handle }
        } else {
          if ($row.Visible -ne 1 -and $row.Area -ge 60000) { $out += $row.Handle }
        }
      }
    }
  }
  return $out
}

# 老名字保留：只要第一个候选（有的地方只想知道"在不在"）
# 从按钮的 spec 里取可选字段（Skip / Prefer）：没写就是空表。
# 没有这个助手的话，其它按钮的 spec 里没有这两个键，直接 $spec.Skip 会是 $null，
# 传进 [string[]] 参数在 PowerShell 2.0 上会报错。
function Get-SpecList {
  param($Spec, [string]$Key)
  if ($Spec -eq $null) { return @() }
  if ($Spec.ContainsKey($Key)) {
    $value = $Spec[$Key]
    if ($value -ne $null) { return @($value) }
  }
  return @()
}

function Find-AppWindow {
  param([string[]]$Names, [string[]]$TitleWords, [switch]$TitleOnly,
        [string[]]$Skip = @(), [string[]]$Prefer = @())
  $list = @(Find-AppWindows -Names $Names -TitleWords $TitleWords -TitleOnly:$TitleOnly `
                            -Skip $Skip -Prefer $Prefer)
  if ($list.Count -eq 0) { return [IntPtr]::Zero }
  return $list[0]
}

function Focus-WindowHandle {
  param([IntPtr]$Handle)
  if ($Handle -eq [IntPtr]::Zero) { return $false }
  if (-not [W7AWindow]::Activate($Handle, 8)) { return $false }
  # 切过来之后顺手把跑到屏幕外的窗口拉回来：
  # 窗口一大半在屏幕外时，老师连右上角的最小化/关闭都点不到（真机上出过）
  try { [W7AWindow]::Clamp($Handle) } catch { }
  return $true
}

# 只找**自带内核**那个界面窗口并切到前台（找不到返回 Zero）。
#
# 为什么要单独认一遍：老师自己的浏览器里也可能开着 Study7Agent 的页面，标题一模一样 ——
# 光按"进程名 chrome/msedge/360 + 标题含 Study7Agent"找，会把老师那个浏览器窗口切到前台，
# 老师看到的就是"点了显示界面，结果打开的是前台的浏览器页面"。
# 判定办法：这个窗口的进程 exe **路径里带 chromium-109**（我们随包发的那份内核），
# 别人的 Chrome/Edge/360 都不可能有这个目录名。PS 2.0 上 Get-Process 的 Path 就能读到。
function Focus-KernelWindow {
  $rows = @(Get-WindowList)
  foreach ($row in $rows) {
    if ($row.Handle -eq [IntPtr]::Zero) { continue }
    if ($row.Title -notlike ("*" + $Script:AgentTitles[0] + "*")) { continue }
    $path = ""
    try {
      $process = Get-Process -Id $row.Pid -ErrorAction Stop
      $path = [string]$process.Path
    } catch {
      $path = ""
    }
    if ($path -eq "" -or $path -notlike ("*" + $Script:KernelMark + "*")) { continue }
    if (Focus-WindowHandle $row.Handle) {
      Write-Log ("显示界面 -> 认出自带内核的窗口（" + $path + "）")
      return $row.Handle
    }
  }
  return [IntPtr]::Zero
}

# 挨个试候选窗口，用第一个**真的能用了**的（返回它的句柄，全不行回 Zero）。
# 只看第一个候选是不够的：一个程序往往有好几个顶层窗口，挑错了就是
# "日志说切成功了、老师那边什么也没发生"。
function Focus-AppWindows {
  param([string[]]$Names, [string[]]$TitleWords, [switch]$TitleOnly,
        [string[]]$Skip = @(), [string[]]$Prefer = @())
  $candidates = @(Find-AppWindows -Names $Names -TitleWords $TitleWords -TitleOnly:$TitleOnly `
                                  -Skip $Skip -Prefer $Prefer)
  foreach ($handle in $candidates) {
    if (Focus-WindowHandle $handle) { return $handle }
  }
  return [IntPtr]::Zero
}

# 按一组组合键，模拟老师自己按（把收在托盘里的程序"叫"出来，见 AppWindows 里 QQ 那段）。
# 自检（DryRun）里绝不真按：那会在老师没点任何按钮的时候把 QQ 弹出来。
function Send-Hotkey {
  param([int[]]$Keys)
  if ($Keys.Count -lt 2) { return $false }
  if ($Script:DryRun) { return $true }
  try {
    [W7AWindow]::Hotkey($Keys)
    return $true
  } catch {
    Write-Log ("按热键失败：" + $_.Exception.Message)
    return $false
  }
}

# 这个东西现在有没有"看得见的窗口"？收在托盘里（点 X 藏起来）就是没有 ——
# 那种情况下该按它自己的热键把它叫出来，而不是 ShowWindow 硬拽（见 QQ 那段说明）。
function Test-AppVisible {
  param($Spec)
  if ($Spec -eq $null) { return $false }
  $rows = @(Find-AppWindows -Names (Get-SpecList $Spec "Names") `
                            -TitleWords (Get-SpecList $Spec "Titles") `
                            -Skip (Get-SpecList $Spec "Skip") `
                            -Prefer (Get-SpecList $Spec "Prefer") -VisibleOnly)
  return ($rows.Count -gt 0)
}

# 这东西是不是"开着、但收在托盘里"？= 找得到窗口，可一个都不是看得见的。
# 这种时候该按它自己的热键叫出来；**根本没开着就别按**（按了没用，还可能碰到别人的热键）。
function Test-AppInTray {
  param($Spec)
  if ($Spec -eq $null) { return $false }
  $any = Find-AppWindow -Names (Get-SpecList $Spec "Names") `
                        -TitleWords (Get-SpecList $Spec "Titles") `
                        -Skip (Get-SpecList $Spec "Skip") `
                        -Prefer (Get-SpecList $Spec "Prefer")
  if ($any -eq [IntPtr]::Zero) { return $false }
  return (-not (Test-AppVisible $Spec))
}

# 刚启动的程序要等窗口出来：轮询，而且每次都要 Focus 成功才算数
function Focus-AppByNames {
  param([string[]]$Names, [string[]]$TitleWords, [int]$Seconds = 6, [switch]$TitleOnly,
        [string[]]$Skip = @(), [string[]]$Prefer = @())
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if ((Focus-AppWindows -Names $Names -TitleWords $TitleWords -TitleOnly:$TitleOnly `
                          -Skip $Skip -Prefer $Prefer) -ne [IntPtr]::Zero) { return $true }
    Start-Sleep -Milliseconds 400
  }
  return $false
}

# 各外部应用的进程名 / 窗口标题关键词。
# 可选字段：Skip = 标题里有这些词的一律不要；Prefer = 标题里有这些词的排最前。
# 「QQ」这种两个字的关键词太宽（会命中 QQ音乐、QQ浏览器），所以 QQ 只认进程名，
# 再用 Skip / Prefer 把"聊天主窗口"挑出来。
$Script:AppWindows = @{
  "whiteboard" = @{ Label = "希沃白板";
                    Names = @("easinote5", "easinote", "easinote5ex", "easinote5helper");
                    Titles = @("希沃白板", "EasiNote") }
  "display"    = @{ Label = "希沃展台";
                    Names = @("seewodisplay", "seewovideo", "seeworemote", "seewolink");
                    Titles = @("视频展台", "希沃展台") }
  # ★ 老师反馈："开了 QQ 图片或腾讯文档在前台、聊天窗最小化时，点 QQ 不切聊天窗。"
  #   根因：图片查看器 / 腾讯文档 / 小世界 全都是同一个 qq.exe，光按进程名匹配会切错。
  #   Skip 把这些窗口排除，Prefer 让标题就是「QQ」的聊天主窗口排到第一个被试。
  #   聊天窗最小化时它照样在候选里（Find-AppWindows 会带上隐藏但够大的窗口），
  #   Focus-WindowHandle 会把它 SW_RESTORE 出来。
  #   tim 也在名单里：学校装腾讯 TIM 的一样是"那个 QQ"。
  #   Hotkey = QQ 自己的"提取消息"热键（默认 Ctrl+Alt+Z）。收在托盘里时先用它把 QQ
  #   **叫**出来，而不是 ShowWindow 硬拽：硬拽出来的窗口 QQ 自己认为"还藏着"，
  #   老师看到的是一张点不动的空壳（真机反馈"打开的 qq 窗口是个完全无法交互的假窗口"）。
  "qq"         = @{ Label = "QQ";
                    Names = @("qq", "qqsc", "qqsclauncher", "qqnt", "tim"); Titles = @();
                    Hotkey = @(0x11, 0x12, 0x5A);
                    Skip = @("图片", "照片", "文档", "小世界", "影音", "视频", "音乐",
                             "漫画", "游戏", "浏览器", "空间", "邮箱", "直播",
                             "Image", "Photo", "Docs", "Video", "Music", "Tencent");
                    Prefer = @("QQ") }
}

# 「切到PPT」认哪些程序：WPS 12 是**单 exe**（wps.exe 带 /wpp 参数），根本没有 wpp.exe，
# 所以 "wps" 必须在名单里——只写 wpp 的话，老师在 WPS 里打开的课件永远切不过来。
$Script:PresentationNames = @("powerpnt", "wpp", "wps", "pptview", "wpsppt")
$Script:PresentationTitles = @("PowerPoint", "WPS 演示", "WPS演示", "演示文稿",
                               ".ppt", ".pptx", ".pps", ".ppsx")

# --------------------------------------------------------------------------
# UI 骨架
# --------------------------------------------------------------------------
$Script:Form = New-Object System.Windows.Forms.Form
$Script:Form.Text = "Study7Agent"
$Script:Form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$Script:Form.TopMost = $true
$Script:Form.ShowInTaskbar = $false
$Script:Form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$Script:Form.BackColor = $ColorBg
$Script:Form.Font = $FontButton

$Script:FormWidth = 184
$Script:FormHeight = 0
$Script:StatusHeight = 48
# 折叠起来只有这么窄：老师反馈"太挡视野"，收起来就剩右边一条
# 折叠态尺寸。老师两次反馈"太大"：46x138 -> 38x112 -> 34x88（这次把时钟去掉，少一段）。
# 它常驻置顶在屏幕右边，越窄越不挡课件；现在只剩 图标 / 展开 / 退出 三段。
# **具体宽高在控件建好后按 GetPreferredSize 算**（见下面的 $Script:CollapsedWidth/Height）：
# 硬编码会把"退出"两个字切掉（老师反馈"退出显示不全，竖着还是不全"）。
$Script:CollapsedWidth = 34
$Script:CollapsedHeight = 34
$Script:FormRadius = 9
$Script:Collapsed = $false
$Script:CollapsedControls = @()
$Script:ExpandedControls = @()
$Script:ExpandedLocation = $null
$Script:CollapseFile = Join-Path $Script:Root "logs\float-ui.txt"

function Set-FormRegion {
  $region = New-RoundRegion -Width $Script:Form.Width -Height $Script:Form.Height -Radius $Script:FormRadius
  if ($region -ne $null) { $Script:Form.Region = $region }
}

$iconPath = Join-Path $Script:Root "agent.ico"
if (Test-Path -LiteralPath $iconPath) {
  try { $Script:Form.Icon = New-Object System.Drawing.Icon -ArgumentList $iconPath } catch { }
}

# 边框：无边框窗体默认没有轮廓，贴在浅色背景上会糊成一片
$Script:Form.Add_Paint({
  try {
    $g = $Script:Form.CreateGraphics()
    $pen = New-Object System.Drawing.Pen -ArgumentList $ColorLine, 1
    $g.DrawRectangle($pen, 0, 0, $Script:Form.Width - 1, $Script:Form.Height - 1)
    $pen.Dispose()
    $g.Dispose()
  } catch { }
})

# --- 顶部：只是拖动条 + 收起（点一下不再回网页：界面只能从「显示界面」按钮打开）---
$Header = New-Object System.Windows.Forms.Panel
$Header.Location = New-Object System.Drawing.Point -ArgumentList 0, 0
$Header.Size = New-Object System.Drawing.Size -ArgumentList $Script:FormWidth, 32
$Header.BackColor = $ColorAccent
$Header.Cursor = [System.Windows.Forms.Cursors]::SizeAll
$Script:Form.Controls.Add($Header)
# 渐变 + 底部一条高光，比纯色块看着精神一点
$Header.Add_Paint({
  try {
    $g = $Header.CreateGraphics()
    $rect = New-Object System.Drawing.Rectangle -ArgumentList 0, 0, $Header.Width, $Header.Height
    $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush -ArgumentList `
      $rect, $ColorAccent, $ColorAccent2, ([System.Drawing.Drawing2D.LinearGradientMode]::Horizontal)
    $g.FillRectangle($brush, $rect)
    $brush.Dispose()
    $pen = New-Object System.Drawing.Pen -ArgumentList ([System.Drawing.Color]::FromArgb(60, 255, 255, 255)), 1
    $g.DrawLine($pen, 0, $Header.Height - 1, $Header.Width, $Header.Height - 1)
    $pen.Dispose()
    $g.Dispose()
  } catch { }
})

$HeaderText = New-Object System.Windows.Forms.Label
$HeaderText.Text = "Study7Agent"
$HeaderText.ForeColor = [System.Drawing.Color]::White
$HeaderText.BackColor = [System.Drawing.Color]::Transparent
$HeaderText.Font = $FontSmall
$HeaderText.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
$HeaderText.Location = New-Object System.Drawing.Point -ArgumentList 26, 0
$HeaderText.Size = New-Object System.Drawing.Size -ArgumentList ($Script:FormWidth - 66), 32
$HeaderText.Cursor = [System.Windows.Forms.Cursors]::Hand
$Header.Controls.Add($HeaderText)

# 右上角的「收起」：点一下只剩右边一条窄条，再点一下就展开
$CollapseLabel = New-Object System.Windows.Forms.Label
$CollapseLabel.Text = "收起"
$CollapseLabel.ForeColor = [System.Drawing.Color]::FromArgb(230, 236, 255)
$CollapseLabel.BackColor = [System.Drawing.Color]::Transparent
$CollapseLabel.Font = $FontSmall
$CollapseLabel.TextAlign = [System.Drawing.ContentAlignment]::MiddleRight
$CollapseLabel.Location = New-Object System.Drawing.Point -ArgumentList ($Script:FormWidth - 44), 0
$CollapseLabel.Size = New-Object System.Drawing.Size -ArgumentList 38, 32
$CollapseLabel.Cursor = [System.Windows.Forms.Cursors]::Hand
$Header.Controls.Add($CollapseLabel)

# 左上角放应用自己的图标（agent.ico 里的 16x16 那张）：工具栏才像是这个程序的一部分，
# 而不是一个来路不明的小条。图标文件不在就只是没有图标，不影响使用。
$Script:Icon = $null
$HeaderIcon = $null
if (Test-Path -LiteralPath $iconPath) {
  try {
    $Script:Icon = New-Object System.Drawing.Icon -ArgumentList $iconPath, 16, 16
    $HeaderIcon = New-Object System.Windows.Forms.PictureBox
    $HeaderIcon.Image = $Script:Icon.ToBitmap()
    $HeaderIcon.SizeMode = [System.Windows.Forms.PictureBoxSizeMode]::AutoSize
    $HeaderIcon.Location = New-Object System.Drawing.Point -ArgumentList 6, 8
    $HeaderIcon.BackColor = [System.Drawing.Color]::Transparent
    $HeaderIcon.Cursor = [System.Windows.Forms.Cursors]::Hand
    $Header.Controls.Add($HeaderIcon)
  } catch {
    $HeaderIcon = $null
  }
}

# --- 折叠态：**只剩一个图标**（按住拖动，点一下展开）---
# 老师第三次反馈"折叠后还是太大"：原来那条上有 图标 + 展开 + 退出 三段，现在全去掉，
# 只留图标本身 —— 退出在展开后的窗口右下角，折叠时不需要。
$CollapsedIcon = $null
if ($HeaderIcon -ne $null) {
  $CollapsedIcon = New-Object System.Windows.Forms.PictureBox
  $CollapsedIcon.Image = $Script:Icon.ToBitmap()
  $CollapsedIcon.SizeMode = [System.Windows.Forms.PictureBoxSizeMode]::AutoSize
  $CollapsedIcon.Location = New-Object System.Drawing.Point -ArgumentList 4, 4
  $CollapsedIcon.BackColor = [System.Drawing.Color]::Transparent
} else {
  # ★ 图标文件读不出来时（例如客机上只送了这一个脚本、程序目录里没有 ico）**也必须有个能点的东西**：
  #   折叠态现在只有这一个控件，它不存在的话老师对着一个空方块既点不开、也认不出是什么。
  #   客机快检就是靠 collapsedCount=0 抓到这一条的（PowerShell 2.0 真机）。
  $CollapsedIcon = New-Object System.Windows.Forms.Label
  $CollapsedIcon.Text = "学"
  $CollapsedIcon.AutoSize = $false
  $CollapsedIcon.Size = New-Object System.Drawing.Size -ArgumentList 34, 34
  $CollapsedIcon.Location = New-Object System.Drawing.Point -ArgumentList 4, 4
  $CollapsedIcon.Font = New-Object System.Drawing.Font -ArgumentList "Microsoft YaHei", 14
  $CollapsedIcon.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
  $CollapsedIcon.BackColor = $ColorAccent
  $CollapsedIcon.ForeColor = [System.Drawing.Color]::White
}
$CollapsedIcon.Cursor = [System.Windows.Forms.Cursors]::Hand
$Script:Form.Controls.Add($CollapsedIcon)
$Script:CollapsedControls += $CollapsedIcon

foreach ($control in $Script:CollapsedControls) { $control.Visible = $false }

# ★ 折叠条的尺寸**按控件自己需要的宽高算出来**，不再硬编码（老师两次反馈被切：
#   先是"退出显示不全"（竖着放不下），加宽过一版还是不全 —— 因为"退出"两个字在
#   别的 DPI / 字体下需要的宽度不一样。硬编码的 34 宽，字横着排就是放不下。
#   现在折叠态只剩一个图标，尺寸 = 图标 + 四边留白（老师第三次反馈"还是太大"）。
$CollapsedPadX = 4
$iconWidth = 34
$iconHeight = 34
if ($CollapsedIcon -ne $null) {
  if ($CollapsedIcon.Width -gt 0) { $iconWidth = $CollapsedIcon.Width }
  if ($CollapsedIcon.Height -gt 0) { $iconHeight = $CollapsedIcon.Height }
}
$Script:CollapsedWidth = $iconWidth + 2 * $CollapsedPadX
if ($Script:CollapsedWidth -lt 34) { $Script:CollapsedWidth = 34 }
$Script:CollapsedHeight = $iconHeight + 2 * $CollapsedPadX + $Script:FormRadius
if ($Script:CollapsedHeight -lt 34) { $Script:CollapsedHeight = 34 }

if ($CollapsedIcon -ne $null) {
  $CollapsedIcon.Location = New-Object System.Drawing.Point -ArgumentList `
    ([int](($Script:CollapsedWidth - $CollapsedIcon.Width) / 2)), $CollapsedPadX
}

# --------------------------------------------------------------------------
# 折叠 / 展开（状态记在 logs\float-ui.txt，老师收起来一次就一直是收着的）
# --------------------------------------------------------------------------
function Save-Collapsed {
  try {
    $dir = Split-Path -Parent $Script:CollapseFile
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $value = "0"
    if ($Script:Collapsed) { $value = "1" }
    [System.IO.File]::WriteAllText($Script:CollapseFile, $value, [System.Text.Encoding]::ASCII)
  } catch {
    # 记不住状态不算错误
  }
}

function Load-Collapsed {
  try {
    if (Test-Path -LiteralPath $Script:CollapseFile) {
      return ([System.IO.File]::ReadAllText($Script:CollapseFile)).Trim() -eq "1"
    }
  } catch { }
  # 没记过状态 = 第一次启动：默认**收起来**。
  # 一体机上右边那一竖条本来就挡视线，展开是老师主动要的，不该是默认。
  return $true
}

function Set-Collapsed {
  param([bool]$Collapsed)
  $Script:Collapsed = $Collapsed
  $area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
  if ($Collapsed) {
    $Script:ExpandedLocation = $Script:Form.Location
    $Script:Form.Size = New-Object System.Drawing.Size -ArgumentList $Script:CollapsedWidth, $Script:CollapsedHeight
    # 折叠态默认贴右上角，但**要往下让开一点**：全屏窗口的关闭按钮就在右上角，
    # 原来贴 y=8 正好压住它（老师点不到关闭，第三次反馈）。现在从 1/6 屏高起，最少 96 像素。
    $x = $area.Right - $Script:CollapsedWidth - 6
    $y = $area.Top + [int]($area.Height / 6)
    if ($y -lt ($area.Top + 96)) { $y = $area.Top + 96 }
    if ($y -gt ($area.Bottom - $Script:CollapsedHeight - 8)) {
      $y = $area.Bottom - $Script:CollapsedHeight - 8
    }
    if ($x -lt $area.Left) { $x = $area.Left }
    if ($y -lt $area.Top) { $y = $area.Top }
    $Script:Form.Location = New-Object System.Drawing.Point -ArgumentList $x, $y
  } else {
    $Script:Form.Size = New-Object System.Drawing.Size -ArgumentList $Script:FormWidth, $Script:FormHeight
    $x = $area.Right - $Script:FormWidth - 12
    $y = $area.Top + [int](($area.Height - $Script:FormHeight) / 2)
    if ($Script:ExpandedLocation -ne $null) {
      $x = $Script:ExpandedLocation.X
      $y = $Script:ExpandedLocation.Y
    }
    if ($x -lt $area.Left) { $x = $area.Left }
    if ($y -lt $area.Top) { $y = $area.Top }
    if ($x -gt ($area.Right - $Script:FormWidth)) { $x = $area.Right - $Script:FormWidth }
    if ($y -gt ($area.Bottom - $Script:FormHeight)) { $y = $area.Bottom - $Script:FormHeight }
    $Script:Form.Location = New-Object System.Drawing.Point -ArgumentList $x, $y
  }
  Set-FormRegion
  foreach ($control in $Script:ExpandedControls) { $control.Visible = -not $Collapsed }
  foreach ($control in $Script:CollapsedControls) { $control.Visible = $Collapsed }
  $Script:Form.TopMost = $true
  Save-Collapsed
}

$Script:Dragging = $false
$Script:Dragged = $false
$Script:DragX = 0
$Script:DragY = 0
$Script:StartX = 0
$Script:StartY = 0

# 三个拖动处理函数写成脚本级函数，事件里只留一行调用：
# 同一个 script block 字面量不会因为循环/多个控件而丢掉引用（PowerShell 2.0 没有 lambda 捕获）
function Start-Drag {
  $Script:Dragging = $true
  $Script:Dragged = $false
  $Script:StartX = $Script:Form.Left
  $Script:StartY = $Script:Form.Top
  $position = [System.Windows.Forms.Cursor]::Position
  $Script:DragX = $position.X - $Script:Form.Left
  $Script:DragY = $position.Y - $Script:Form.Top
}

function Move-Drag {
  if (-not $Script:Dragging) { return }
  $position = [System.Windows.Forms.Cursor]::Position
  $nextX = $position.X - $Script:DragX
  $nextY = $position.Y - $Script:DragY
  # 不许拖出屏幕：这个窗口没有标题栏，真拖丢了老师找不回来
  $area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
  if ($nextX -lt $area.Left) { $nextX = $area.Left }
  if ($nextY -lt $area.Top) { $nextY = $area.Top }
  if ($nextX -gt ($area.Right - $Script:Form.Width)) { $nextX = $area.Right - $Script:Form.Width }
  if ($nextY -gt ($area.Bottom - $Script:Form.Height)) { $nextY = $area.Bottom - $Script:Form.Height }
  if ([System.Math]::Abs($nextX - $Script:StartX) -gt 4 -or [System.Math]::Abs($nextY - $Script:StartY) -gt 4) {
    $Script:Dragged = $true
  }
  $Script:Form.Location = New-Object System.Drawing.Point -ArgumentList $nextX, $nextY
}

function End-Drag {
  $Script:Dragging = $false
  # 顶栏 / 图标只是**拖动把手**：松开鼠标什么都不做。
  # 老师原话："改成点悬浮窗图标只展开悬浮窗，不把程序切到前台，只能通过悬浮窗里的按钮打开。"
  # 以前这里会调 Show-AgentWindow，于是"想拖一下"结果把界面弹到前台，误触非常烦。
  # 现在要打开界面**只有一条路**：下面那个「显示界面」按钮（Show-Page）。
  # 折叠态也一样不在这里处理：点图标展开是图标自己的 Add_Click（见下面的绑定）。
}

$Header.Add_MouseDown({ Start-Drag })
$Header.Add_MouseMove({ Move-Drag })
$Header.Add_MouseUp({ End-Drag })
$HeaderText.Add_MouseDown({ Start-Drag })
$HeaderText.Add_MouseMove({ Move-Drag })
$HeaderText.Add_MouseUp({ End-Drag })
if ($HeaderIcon -ne $null) {
  $HeaderIcon.Add_MouseDown({ Start-Drag })
  $HeaderIcon.Add_MouseMove({ Move-Drag })
  $HeaderIcon.Add_MouseUp({ End-Drag })
}
$CollapseLabel.Add_Click({ Set-Collapsed $true })
# 折叠起来也要能拖：老师把窄条挪到一个不挡事的位置是刚需
# （默认贴右上角，但希沃白板右上角也有按钮）。图标和时间这两块都能按住拖。
if ($CollapsedIcon -ne $null) {
  $CollapsedIcon.Add_MouseDown({ Start-Drag })
  $CollapsedIcon.Add_MouseMove({ Move-Drag })
  $CollapsedIcon.Add_MouseUp({ End-Drag })
  # 点一下图标 = 展开；刚拖过就不算点击（不然拖完位置顺手就展开了）
  $CollapsedIcon.Add_Click({ if (-not $Script:Dragged) { Set-Collapsed $false } })
}

# --------------------------------------------------------------------------
# 「显示界面」：把界面窗口切到前台；窗口被关掉了就重新开出来
#
# 关掉界面窗口**不等于退出程序**：WebUI 服务和这个工具栏都还在跑（老师要的"最小化到托盘"）。
# 所以这里的两条路就是"回来"的路：
#   ① 窗口还在（可能最小化了 / 收到托盘里 / 跑到屏幕外）-> 用 Find-AppWindows 找到并切到前台，
#      这套已经处理了最小化、托盘、屏幕外三种情况；
#   ② 窗口真被关掉了 -> 重新启动 Study7Agent.exe：第二次启动经单实例信号让已经在跑的
#      那个实例把界面开出来，**还是原来那个 profile**（不会变成老师浏览器里的新标签页）。
# --------------------------------------------------------------------------
function Start-AgentApp {
  # 重新启动界面。返回"这条路走不走得通"，不管窗口有没有出来（出来要等几秒，调用方去轮询）
  if ($Script:Exe -eq "") { return $false }
  if (-not (Test-Path -LiteralPath $Script:Exe)) {
    Write-Log ("重新打开界面：exe 不在（" + $Script:Exe + "）")
    return $false
  }
  if ($Script:DryRun) { return $true }        # 自检里绝不真启动（会在客机上弹出界面窗口）
  try {
    Write-Log ("重新打开界面：" + $Script:Exe)
    Start-Process -FilePath $Script:Exe
    return $true
  } catch {
    Write-Log ("重新打开界面失败：" + $_.Exception.Message)
    return $false
  }
}

function Show-AgentWindow {
  # 只该由「显示界面」按钮（Show-Page）和模块按钮的 focus 回包调用；
  # 顶栏 / 图标的点击路径不许走到这里（§End-Drag），自检会盯着这个计数器。
  $Script:ShowAgentCalls = $Script:ShowAgentCalls + 1
  # ① 先找**自带内核**那个窗口（进程 exe 路径带 chromium-109）并切到前台。
  #    只认内核：老师自己的浏览器里开着同一个页面时标题一模一样，按标题找就会切错
  #    （他看到的："只是默认浏览器界面移动到前台"）。
  if (-not $Script:DryRun) {
    $handle = Focus-KernelWindow
    if ($handle -ne [IntPtr]::Zero) {
      $Script:WindowClosed = $false
      Write-Log ("显示界面 -> 切到自带内核的窗口 " + [W7AWindow]::Describe($handle))
      return @($true, "已切到界面")
    }
  }
  # ② 内核窗口真没了（被关掉）：再启动一次 exe，让已经在跑的实例把界面开出来。
  #    启动器那边现在也只认内核窗口（`FindAppWindow` 认 exe 路径带 chromium-109 的），
  #    所以它不会再"再开一个黑屏内核窗口"。
  if (Start-AgentApp) {
    if ($Script:DryRun) { return @($true, "（自检）显示界面") }
    Set-Status "界面窗口关着，正在重新打开…" $ColorMuted
    # **只等自带内核那个窗口**（进程 exe 路径带 chromium-109）。
    # 这里原来是 `Focus-AppByNames -Names $Script:AgentNames`，名单里含 msedge/360 ——
    # 新窗口还没出来的那几秒里，它会把老师**最小化的 Edge**（标题也含 Study7Agent）
    # 当成界面窗口切到前台。老师连着报了两次"成功打开程序窗口时又把 Edge 切到前台"，就是这条。
    $handle = [IntPtr]::Zero
    $deadline = (Get-Date).AddSeconds(20)
    while ($handle -eq [IntPtr]::Zero -and (Get-Date) -lt $deadline) {
      $handle = Focus-KernelWindow
      if ($handle -eq [IntPtr]::Zero) { Start-Sleep -Milliseconds 500 }
    }
    if ($handle -ne [IntPtr]::Zero) {
      $Script:WindowClosed = $false
      Write-Log "显示界面 -> 重新开出来了（自带内核的窗口）"
      return @($true, "已重新打开界面")
    }
    return @($false, "已经让界面重新打开了，等几秒再点一次")
  }
  # ② 没有 exe 路径（老调用）：只把**自带内核**那个窗口切前台；绝不打开默认浏览器
  if (-not $Script:DryRun) {
    $handle = Focus-KernelWindow
    if ($handle -ne [IntPtr]::Zero) {
      $Script:WindowClosed = $false
      Write-Log ("显示界面 -> 切到自带内核的窗口 " + [W7AWindow]::Describe($handle))
      return @($true, "已切到界面")
    }
  }
  # ③ 既没有窗口、也没拿到 exe：**绝不去开默认浏览器**（老师看到的就是
  #    "打开了前台浏览器页面"）。直接说清楚该怎么办。
  Write-Log "显示界面 -> 没拿到程序位置（-Exe 为空且猜不到），不打开浏览器"
  return @($false, "找不到程序位置，请双击桌面上的 Study7Agent 图标")
}

# --------------------------------------------------------------------------
# 两个本地动作：不走 HTTP，点下去立刻生效
# --------------------------------------------------------------------------
# 「切到PPT」：先把已经在跑的 WPS 演示 / PowerPoint 拿到最前面（快路，不碰硬盘）；
# 任务栏里一个都没有时，才让服务端用默认应用打开最近收到的那份课件。
function Show-Presentation {
  if ($Script:DryRun) { return @($true, "（自检）切到 PPT") }
  # 挨个试候选：WPS 有好几个顶层窗口，只有那个真正的演示窗才切得出效果
  $handle = Focus-AppWindows -Names $Script:PresentationNames -TitleWords $Script:PresentationTitles -TitleOnly
  if ($handle -ne [IntPtr]::Zero) {
    Write-Log ("ppt -> 切到窗口 " + [W7AWindow]::Describe($handle))
    return @($true, "已切到 PPT")
  }
  # 只有"真的在放课件"的窗口才算数（-TitleOnly）：
  # WPS 的后台窗口、首页、文字、表格都不算，否则会误判成"已经有 PPT 了"
  if ((Find-AppWindows -Names $Script:PresentationNames -TitleWords $Script:PresentationTitles -TitleOnly).Count -gt 0) {
    # 找得到窗口但一个都切不动：别装作成功，也别偷偷去开课件
    Write-Log "ppt -> 找到窗口但切不到前台"
    return @($false, "找到了 PPT 窗口，但切不到前台；先点一下任务栏里的 WPS，再回来点这里")
  }
  Write-Log "ppt -> 没有正在运行的演示程序，去打开最新课件"
  Set-Status "没找到正在运行的 PPT，正在打开最近的课件…" $ColorMuted
  $reply = ""
  try {
    $reply = Invoke-Api "POST" "/api/float/command" '{"action":"open_ppt"}' 20000
  } catch {
    return @($false, "没有正在运行的 PPT，也连不上 WebUI 服务：" + $_.Exception.Message)
  }
  $parsed = Split-Reply $reply
  if (-not $parsed[0]) { return @($false, [string]$parsed[1]) }
  if (Focus-AppByNames -Names $Script:PresentationNames -TitleWords $Script:PresentationTitles -Seconds 20 -TitleOnly) {
    return @($true, [string]$parsed[1])
  }
  return @($true, [string]$parsed[1] + "（窗口还没出来，稍等一下再点一次）")
}

# 「回到桌面」：MinimizeAll 把所有窗口收下去
function Show-Desktop {
  if ($Script:DryRun) { return @($true, "（自检）回到桌面") }
  try {
    $shell = New-Object -ComObject Shell.Application
    $shell.MinimizeAll()
    # MinimizeAll 会把工具栏自己也最小化掉，那样老师就再也点不到它了：立刻恢复
    $Script:Form.WindowState = [System.Windows.Forms.FormWindowState]::Normal
    $Script:Form.TopMost = $true
    return @($true, "已回到桌面")
  } catch {
    return @($false, "回不到桌面：" + $_.Exception.Message)
  }
}

# --------------------------------------------------------------------------
# 状态栏与按钮
# --------------------------------------------------------------------------
$Script:StatusLabel = $null
$Script:Timer = $null
$Script:HoldUntil = [datetime]::MinValue
$Script:Quitting = $false
$Script:MissCount = 0
$Script:EverSeen = $false
$Script:LastAction = ""

function Set-Status {
  param([string]$Text, [System.Drawing.Color]$Color)
  if ($Script:StatusLabel -eq $null) { return }
  $Script:StatusLabel.Text = $Text
  $Script:StatusLabel.ForeColor = $Color
  # 报错经常比这一小块地方长：完整内容挂到悬停提示上，鼠标一放就能看全
  try { $Script:Tip.SetToolTip($Script:StatusLabel, $Text) } catch { }
  $Script:StatusLabel.Refresh()
}

# --------------------------------------------------------------------------
# 「显示界面」「天气」：两个不依赖服务端的本地动作
# --------------------------------------------------------------------------
# 「显示界面」：**打开程序界面的唯一入口**。老师要求"只能通过悬浮窗里的按钮打开"，
# 所以顶栏 / 图标点一下不再切前台，改由这个按钮负责：窗口还在就切到前台，
# 被关掉了就重新开出来。别的按钮（物理沙盒、计时……）若服务端回 focus=1，
# 也会顺着同一条路把界面叫出来 —— 那同样是"按了悬浮窗里的按钮"，不是误触。
function Show-Page {
  if ($Script:DryRun) { return @($true, "（自检）显示界面") }
  return @(Show-AgentWindow)
}

function Show-Weather {
  if ($Script:DryRun) { return @($true, "（自检）天气") }
  try {
    Start-Process $Script:WeatherUrl
    return @($true, "已打开天气（浏览器问定位就点允许，之后是本地的逐小时预报）")
  } catch {
    return @($false, "打不开天气网页：" + $_.Exception.Message)
  }
}

# 按钮动作 -> 请求体。ppt / desktop / page / weather 是本地动作
# （见 Invoke-ToolbarAction），不走这里。
function Get-RequestBody {
  param([string]$Action)
  if ($Action -eq "whiteboard" -or $Action -eq "display" -or $Action -eq "qq") {
    return '{"action":"app","app":"' + $Action + '"}'
  }
  if ($Action -eq "math") { return '{"action":"figure","kind":"function"}' }
  if ($Action -eq "geometry") { return '{"action":"figure","kind":"geometry"}' }
  if ($Action -eq "calc") { return '{"action":"calc","tab":"math"}' }
  return '{"action":"' + $Action + '"}'
}

function Invoke-ToolbarAction {
  param([string]$Action)
  # 记下这次是哪个动作：自检靠它证明 GetNewClosure 真的把每个按钮的 Key 分开捕获了
  # （不分开的话 12 个按钮会全指向最后一个）。
  $Script:LastAction = $Action
  if ($Action -eq "ppt" -or $Action -eq "desktop" -or $Action -eq "page" -or
      $Action -eq "weather") {
    Set-Status ("正在执行：" + $Action + " …") $ColorMuted
    $result = @($false, "")
    if ($Action -eq "ppt") {
      $result = Show-Presentation
    } elseif ($Action -eq "desktop") {
      $result = Show-Desktop
    } elseif ($Action -eq "page") {
      $result = Show-Page
    } else {
      $result = Show-Weather
    }
    if ($result[0]) {
      Set-Status ([string]$result[1]) $ColorOk
    } else {
      Set-Status ([string]$result[1]) $ColorErr
    }
    Write-Log ($Action + " -> " + [string]$result[1])
    $Script:HoldUntil = (Get-Date).AddSeconds(8)
    return
  }
  Set-Status ("正在执行：" + $Action + " …") $ColorMuted

  # 打开外部应用：**先在本地找它的窗口（含收进托盘的），找到就切到前台，不再启动**。
  # 顺序反了会出事：QQ 已经在托盘里的时候再 os.startfile 一次，
  # 轻则白启动一遍，重则弹出一个新的登录窗口（真机上见过）。
  if ($Script:AppWindows.ContainsKey($Action)) {
    $spec = $Script:AppWindows[$Action]
    $hotkey = Get-SpecList $spec "Hotkey"
    # 一个"看得见的窗口"都没有、但它确实开着（QQ 收在托盘里）而它自己有热键
    # -> 先按热键叫它出来，别去 ShowWindow 硬拽：拽出来的那张 QQ 自己认为"还藏着"，
    # 点不动（真机反馈"打开的 qq 窗口是个完全无法交互的假窗口"）。
    # 等它自己弹（最多 1.2 秒）；热键没生效（老师改过）也没关系，下面那条老路接着走。
    if ($hotkey.Count -ge 2 -and (Test-AppInTray $spec)) {
      Write-Log ($Action + " -> 没有看得见的窗口，先按热键叫它出来")
      Send-Hotkey $hotkey | Out-Null
      for ($try = 0; $try -lt 4; $try++) {
        Start-Sleep -Milliseconds 300
        if (Test-AppVisible $spec) { break }
      }
    }
    $handle = Focus-AppWindows -Names $spec.Names -TitleWords $spec.Titles `
                               -Skip (Get-SpecList $spec "Skip") -Prefer (Get-SpecList $spec "Prefer")
    if ($handle -ne [IntPtr]::Zero) {
      Set-Status ("已切到" + $spec.Label) $ColorOk
      Write-Log ($Action + " -> 切到已经开着的窗口 " + [W7AWindow]::Describe($handle))
      $Script:HoldUntil = (Get-Date).AddSeconds(8)
      return
    }
    Set-Status ($spec.Label + " 没开着，正在启动…") $ColorMuted
    # 记下"当时有几个候选窗口"：学校那台点 QQ 没反应时，日志里这一行是唯一线索
    # （候选 0 个 = 窗口没认出来；候选好几个 = 挑错了窗口）。
    Write-Log ($Action + " -> 没找到能切的窗口，准备启动（候选 " +
      (@(Find-AppWindows -Names $spec.Names -TitleWords $spec.Titles `
                         -Skip (Get-SpecList $spec "Skip") -Prefer (Get-SpecList $spec "Prefer"))).Count +
      " 个）")
  }

  try {
    $reply = Invoke-Api "POST" "/api/float/command" (Get-RequestBody $Action) 20000
    $parsed = Split-Reply $reply
    $ok = $parsed[0]
    $message = [string]$parsed[1]
    $focus = [bool]$parsed[2]
    if ($ok) {
      Set-Status $message $ColorOk
      Write-Log ($Action + " -> " + $message)
    } else {
      Set-Status $message $ColorErr
      Write-Log ($Action + " 失败：" + $message)
    }
    $Script:HoldUntil = (Get-Date).AddSeconds(8)
    # 打开外部应用之后要把它**切到前台**：光 os.startfile 一下，
    # 已经在跑的 QQ 只会闪一下任务栏，老师看不到任何变化。
    if ($ok -and $Script:AppWindows.ContainsKey($Action)) {
      $spec = $Script:AppWindows[$Action]
      Set-Status ($message + "，正在切到前台…") $ColorMuted
      if (Focus-AppByNames -Names $spec.Names -TitleWords $spec.Titles -Seconds 6 `
                          -Skip (Get-SpecList $spec "Skip") -Prefer (Get-SpecList $spec "Prefer")) {
        Set-Status $message $ColorOk
      } else {
        Set-Status ($message + "（没找到它的窗口，可能还在启动）") $ColorMuted
      }
      $Script:HoldUntil = (Get-Date).AddSeconds(8)
    }
    if ($focus) { Show-AgentWindow | Out-Null }
  } catch {
    Set-Status ("连不上 WebUI 服务：" + $_.Exception.Message) $ColorErr
    Write-Log ($Action + " 异常：" + $_.Exception.Message)
    $Script:HoldUntil = (Get-Date).AddSeconds(8)
  }
}

function New-ToolButton {
  param([string]$Text, [string]$Key, [string]$Tip, [int]$X, [int]$Y)
  $button = New-Object System.Windows.Forms.Button
  $button.Text = $Text
  $button.Size = New-Object System.Drawing.Size -ArgumentList 84, 31
  $button.Location = New-Object System.Drawing.Point -ArgumentList $X, $Y
  $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
  $button.FlatAppearance.BorderSize = 0
  $button.FlatAppearance.MouseOverBackColor = $ColorHover
  $button.FlatAppearance.MouseDownBackColor = $ColorAccent
  $button.BackColor = $ColorPanel
  $button.ForeColor = $ColorText
  $button.Font = $FontButton
  $button.TabStop = $false
  $button.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
  $region = New-RoundRegion -Width 84 -Height 31 -Radius 6
  if ($region -ne $null) { $button.Region = $region }
  if ($Tip -ne "") { $Script:Tip.SetToolTip($button, $Tip) }
  # GetNewClosure 是 PowerShell 2.0 就有的：不加的话循环里每个按钮拿到的都是最后一个 Key
  $handler = { Invoke-ToolbarAction $Key }.GetNewClosure()
  $button.Add_Click($handler)
  $Script:Handlers[$Key] = $handler
  return $button
}

$Script:Tip = New-Object System.Windows.Forms.ToolTip
$Script:Tip.AutoPopDelay = 20000
$Script:Tip.SetToolTip($Header, "按住可以拖动这个工具条（点一下不会打开界面）；要打开界面点下面的「显示界面」")
$Script:Tip.SetToolTip($HeaderText, "按住可以拖动这个工具条（点一下不会打开界面）；要打开界面点下面的「显示界面」")
$Script:Tip.SetToolTip($CollapseLabel, "收起来，只剩右边一条（带当前时间）；再点「展开」就回来")

# --- 按钮表：Key 直接就是 /api/float/command 的动作名（ppt / desktop 是本地动作）---
$Script:Buttons = @(
  @{ Key = "whiteboard"; Text = "希沃白板"; Tip = "打开希沃白板，并切到前台" },
  @{ Key = "display";    Text = "希沃展台"; Tip = "打开视频展台，并切到前台" },
  @{ Key = "qq";         Text = "QQ";       Tip = "打开 QQ，并切到前台" },
  @{ Key = "ppt";        Text = "切到PPT";  Tip = "把正在运行的 WPS 演示 / PowerPoint 切到前台；一个都没开就打开最近收到的那份课件" },
  @{ Key = "timer";      Text = "计时";     Tip = "课堂倒计时（时长在网页上选）" },
  @{ Key = "pick";       Text = "抽学号";   Tip = "随机抽一个还没抽到的学号，结果显示在大屏上" },
  @{ Key = "shot";       Text = "截图给AI"; Tip = "截屏，并放进网页「待发送的图片」里，接着问它就行" },
  @{ Key = "board_save"; Text = "板书保存"; Tip = "把当前屏幕存成板书图片（工作区\板书）" },
  @{ Key = "mark";       Text = "课堂标记"; Tip = "记一个课堂时间点：当前时间 + 当屏截图" },
  @{ Key = "physics";    Text = "物理";     Tip = "打开物理沙盒 / 练习面板：画轨道、摆物块、看受力矢量和图像" },
  @{ Key = "chem";       Text = "化学";     Tip = "打开化学实验台：选试剂、看现象、算反应（也能直接问 AI）" },
  @{ Key = "phet";       Text = "PhET";     Tip = "打开 PhET 互动仿真：电路组建、分子形状、酸碱、神经元、扩散、自然选择、基因表达（随包内置，离线可用）" },
  @{ Key = "bio";        Text = "生物";     Tip = "打开生物模拟：神经元电位、扩散、自然选择、基因表达（转录/翻译）" },
  @{ Key = "math";       Text = "数学函数"; Tip = "打开作图面板画函数图像（也可以让它按题画）" },
  @{ Key = "geometry";   Text = "几何画板"; Tip = "打开作图面板画几何图形（也可以让它按题画）" },
  @{ Key = "calc";       Text = "计算器";   Tip = "计算器 / 单位换算（分数、根号、215 个单位可搜索）" },
  @{ Key = "desktop";    Text = "回到桌面"; Tip = "把所有窗口最小化，回到桌面" },
  @{ Key = "page";       Text = "显示界面"; Tip = "把界面窗口切到前台；窗口被关掉了就用它重新开出来（服务一直在后台跑，关窗只是收进托盘）" },
  @{ Key = "translate";  Text = "翻译查词"; Tip = "打开翻译 / 词典面板：中英互译、生词解释（会显示「思考中」）" },
  @{ Key = "memo";       Text = "备忘录";   Tip = "打开备忘录：随手记的东西存在工作区，下节课打开还在" },
  @{ Key = "calendar";   Text = "日历";     Tip = "打开日历：节假日 / 调休 / 农历，联网获取" },
  @{ Key = "weather";    Text = "天气";     Tip = "打开天气预报网站（逐小时、可定位到本地）" }
)

$gridTop = 38
$rowHeight = 36
$columnX = @(6, 95)
$Script:ButtonControls = @()
for ($index = 0; $index -lt $Script:Buttons.Count; $index++) {
  $spec = $Script:Buttons[$index]
  $column = $index % 2
  $row = [int][System.Math]::Floor($index / 2)
  $button = New-ToolButton $spec.Text $spec.Key $spec.Tip $columnX[$column] ($gridTop + $row * $rowHeight)
  $Script:Form.Controls.Add($button)
  $Script:ButtonControls += $button
}
$rows = [int][System.Math]::Ceiling($Script:Buttons.Count / 2)
$gridBottom = $gridTop + $rows * $rowHeight

# --- 分隔线 + 状态三行 + 退出 ---
$Separator = New-Object System.Windows.Forms.Panel
$Separator.Location = New-Object System.Drawing.Point -ArgumentList 8, ($gridBottom + 2)
$Separator.Size = New-Object System.Drawing.Size -ArgumentList ($Script:FormWidth - 16), 1
$Separator.BackColor = $ColorLine
$Script:Form.Controls.Add($Separator)

$Script:StatusLabel = New-Object System.Windows.Forms.Label
$Script:StatusLabel.Text = "正在连接…"
$Script:StatusLabel.ForeColor = $ColorMuted
$Script:StatusLabel.BackColor = $ColorBg
$Script:StatusLabel.Font = $FontSmall
$Script:StatusLabel.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
$Script:StatusLabel.Location = New-Object System.Drawing.Point -ArgumentList 9, ($gridBottom + 6)
$Script:StatusLabel.Size = New-Object System.Drawing.Size -ArgumentList ($Script:FormWidth - 18), $Script:StatusHeight
$Script:Form.Controls.Add($Script:StatusLabel)

$ExitButton = New-Object System.Windows.Forms.Button
$ExitButton.Text = "退出（连服务一起关）"
$ExitButton.Size = New-Object System.Drawing.Size -ArgumentList ($Script:FormWidth - 18), 28
$ExitButton.Location = New-Object System.Drawing.Point -ArgumentList 9, ($gridBottom + 10 + $Script:StatusHeight)
$ExitButton.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
$ExitButton.FlatAppearance.BorderSize = 0
$ExitButton.FlatAppearance.MouseOverBackColor = $ColorPanelHi
$ExitButton.FlatAppearance.MouseDownBackColor = $ColorAccent
$ExitButton.BackColor = $ColorPanel
$ExitButton.ForeColor = $ColorExit
$ExitButton.Font = $FontSmall
$ExitButton.TabStop = $false
$exitRegion = New-RoundRegion -Width ($Script:FormWidth - 18) -Height 28 -Radius 6
if ($exitRegion -ne $null) { $ExitButton.Region = $exitRegion }
$ExitButton.Add_Click({ Stop-Everything })
$Script:Form.Controls.Add($ExitButton)

$Script:FormHeight = $gridBottom + 10 + $Script:StatusHeight + 34
$Script:Form.Size = New-Object System.Drawing.Size -ArgumentList $Script:FormWidth, $Script:FormHeight
Set-FormRegion

# 展开态要显示/隐藏哪些控件（折叠时就全部藏起来）
$Script:ExpandedControls = @($Header, $Separator, $ExitButton, $Script:StatusLabel) + $Script:ButtonControls

# 默认贴在屏幕右侧、垂直居中；比主屏还小的分辨率下也能整个露出来
$workArea = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$defaultX = $workArea.Right - $Script:FormWidth - 12
$defaultY = $workArea.Top + [int](($workArea.Height - $Script:FormHeight) / 2)
if ($defaultX -lt $workArea.Left) { $defaultX = $workArea.Left }
if ($defaultY -lt $workArea.Top) { $defaultY = $workArea.Top }
$Script:Form.Location = New-Object System.Drawing.Point -ArgumentList $defaultX, $defaultY

# --------------------------------------------------------------------------
# 看门狗：网页没了就把整个程序收掉
# --------------------------------------------------------------------------
function Stop-Everything {
  if ($Script:Quitting) { return }
  $Script:Quitting = $true
  Write-Log "退出：请求服务端一起关闭"
  # 让服务端也退出（它是 start-webui.cmd 里那个黑窗口，它一退黑窗口就跟着没了）
  try { Invoke-Api "POST" "/api/float/quit" '{"delay":0.4}' 2000 | Out-Null } catch { }
  if ($Script:Timer -ne $null) { $Script:Timer.Stop() }
  $Script:Form.Close()
}

function Update-Status {
  $reply = ""
  try {
    $reply = Invoke-Api "GET" "/api/float/state" "" 1500
  } catch {
    # 服务连不上了：偶尔一次可能是正在重启，连着几次就一起收掉
    $Script:MissCount = $Script:MissCount + 1
    Set-Status ("连不上 WebUI 服务（" + $Script:MissCount + "/4）：" + $_.Exception.Message) $ColorErr
    if ($Script:MissCount -ge 4) { Stop-Everything }
    return
  }
  $parts = ([string]$reply).Split("|")
  if ($parts.Length -lt 4 -or $parts[0] -ne "OK") {
    Set-Status ("服务返回了看不懂的内容：" + $reply) $ColorErr
    return
  }
  $pageAlive = ($parts[1] -eq "page=1")
  $everSeen = ($parts[3] -eq "ever=1")
  if ($pageAlive) {
    $Script:MissCount = 0
    $Script:EverSeen = $true
    if ($Script:WindowClosed) {
      $Script:WindowClosed = $false
      Write-Log "界面窗口又回来了"
    }
    if ((Get-Date) -ge $Script:HoldUntil) { Set-Status "已连接" $ColorOk }
    return
  }
  if ($everSeen -or $Script:EverSeen) {
    # ★ 这里以前是"连着几次没心跳就把服务和自己一起收掉"（老师报的"关掉网页整个程序就没了"）。
    #   现在**只把窗口状态记为已关闭**：服务不动、工具栏不动、托盘图标不动，
    #   老师点「显示界面」就能把界面重新开出来（顶栏只是拖动把手，不再负责开界面）。
    #   真要退只有「退出」这一个入口。
    if (-not $Script:WindowClosed) {
      $Script:WindowClosed = $true
      Write-Log "界面窗口关掉了：只记状态，服务与工具栏继续跑（点「显示界面」可以重新打开）"
    }
    Set-Status "界面窗口已关闭，服务还在跑（点「显示界面」重新打开）" $ColorMuted
    return
  }
  # 界面一直没连上来（浏览器还没起来、或者被拦了）：不退出，点「显示界面」可以手动把它叫出来
  if ((Get-Date) -ge $Script:HoldUntil) { Set-Status "还没连上界面，点「显示界面」打开" $ColorMuted }
}

# --------------------------------------------------------------------------
# 自检：只在命令行跑，用来确认"这份脚本在 PowerShell 2.0 上能建出界面、能触发按钮"
# --------------------------------------------------------------------------
if ($SelfTest) {
  $handler = $Script:Handlers["pick"]
  $ok = ($handler -ne $null)
  if ($ok) {
    # 指向一个绝对不会有人监听的端口：自检绝不能真去抽学号或者截屏。
    # 这里只要能跑完（不抛未捕获的异常）就说明闭包抓到了 Key、脚本函数也解析得到。
    $Script:ApiBase = "http://127.0.0.1:1"
    & $handler
  }
  # 这个要趁早取：下面跑本地动作时 $Script:LastAction 会被改写
  $pickedAction = $Script:LastAction
  # 中文对不对，用码点报出来：PowerShell 2.0 读不带 BOM 的 .ps1 会按本地代码页解码，
  # 这段中文字面量就会变成完全不同的码点。输出全是 ASCII，绕开控制台代码页的干扰
  # （中文 Win7 的 stdout 是 GBK，直接比对中文反而会误判）。
  $probe = "连不上"
  $codes = @()
  foreach ($character in $probe.ToCharArray()) { $codes += ([int]$character).ToString() }
  # 两个本地动作也走一遍（DryRun 模式不会真去切窗口/最小化桌面）：
  # 能跑通就说明它们被正确地接进了分发逻辑，而不是只会报"不认识这个动作"。
  Invoke-ToolbarAction "ppt"
  $pptResult = $Script:StatusLabel.Text
  Invoke-ToolbarAction "desktop"
  $desktopResult = $Script:StatusLabel.Text
  Invoke-ToolbarAction "page"
  $pageResult = $Script:StatusLabel.Text
  Invoke-ToolbarAction "weather"
  $weatherResult = $Script:StatusLabel.Text
  # 四个本地动作一个都不能少：少一个就说明它没被接进分发逻辑，
  # 真机上点下去只会得到一句"不认识这个动作"。
  $localOk = ($pptResult -like "（自检）*") -and ($desktopResult -like "（自检）*") -and `
             ($pageResult -like "（自检）*") -and ($weatherResult -like "（自检）*")
  # ★ 点顶栏 / 点图标**不许把界面切到前台**（老师原话："点悬浮窗图标只展开悬浮窗，
  #   不把程序切到前台，只能通过悬浮窗里的按钮打开"）。
  #   这里直接调鼠标抬起时走的那两个函数，用 Show-AgentWindow 的计数器证明它们没再调；
  #   同时证明折叠态点图标仍然只是"展开"（不是打开界面）。
  $savedCollapsed = $Script:Collapsed
  $savedDragged = $Script:Dragged
  $callsBefore = $Script:ShowAgentCalls
  $Script:Collapsed = $false
  $Script:Dragged = $false
  End-Drag
  $headerQuiet = ($Script:ShowAgentCalls -eq $callsBefore)
  $Script:Collapsed = $true
  $Script:Dragged = $false
  End-Drag
  if (-not $Script:Dragged) { Set-Collapsed $false }
  $iconQuiet = ($Script:ShowAgentCalls -eq $callsBefore) -and (-not $Script:Collapsed)
  $Script:Collapsed = $savedCollapsed
  $Script:Dragged = $savedDragged
  $clickOk = ($headerQuiet -and $iconQuiet)
  # ★ 「显示界面」是"界面窗口关掉之后回来"的唯一入口，三条路都要走对：
  #   ① 有可用的 exe -> 走"重新启动 exe"（DryRun 下不真启动、也不等窗口）；
  #   ② exe 路径不存在 -> Start-AgentApp 必须说 false，不许抛异常、更不许瞎启动；
  #   ③ 没有 exe 又没有界面地址 -> 如实报错，不能装成"已打开"。
  #   自检里绝不真启动 exe（那会在客机上弹出界面窗口），所以①点的是一个一定存在的文件。
  $probeFile = $Script:ScriptPath
  if ($probeFile -eq "") { $probeFile = (Get-Process -Id $PID).Path }
  $savedExe = $Script:Exe
  $savedUrl = $Script:Url
  $Script:Exe = $probeFile
  $reopen = Show-AgentWindow
  $reopenOk = ($reopen.Count -ge 2 -and $reopen[0] -eq $true -and $reopen[1] -like "（自检）*")
  $Script:Exe = Join-Path $Script:Root "w7a-没有这个文件\Study7Agent.exe"
  $missingOk = (-not (Start-AgentApp))
  $Script:Url = ""
  $noAddr = Show-AgentWindow
  $noAddrOk = ($noAddr[0] -eq $false -and ([string]$noAddr[1]).Length -gt 0)
  $Script:Exe = ""
  $exeOk = ($reopenOk -and (-not (Start-AgentApp)))
  $showOk = ($reopenOk -and $missingOk -and $noAddrOk)
  $Script:Exe = $savedExe
  $Script:Url = $savedUrl
  # 折叠/展开也过一遍：控件该藏的藏、该露的露
  Set-Collapsed $true
  $collapsedSize = $Script:Form.Width
  # ★ 折叠态现在**只有一个图标**：宽度要正好装得下它（老师三次反馈"太大"，
  #   最后把 展开/退出/时钟 全删了）。所以这里量：图标有没有落在圆角之内、
  #   窗口宽度有没有比"图标 + 留白"多出来。
  $collapsedFit = $true
  if ($CollapsedIcon -eq $null) {
    $collapsedFit = $false
  } else {
    if (($CollapsedIcon.Left + $CollapsedIcon.Width) -gt $Script:Form.Width) { $collapsedFit = $false }
    if (($CollapsedIcon.Top + $CollapsedIcon.Height) -gt ($Script:Form.Height - $Script:FormRadius)) {
      $collapsedFit = $false
    }
    if ($CollapsedIcon.Left -lt 0 -or $CollapsedIcon.Top -lt 0) { $collapsedFit = $false }
  }
  foreach ($control in $Script:CollapsedControls) {
    if (($control.Top + $control.Height) -gt ($Script:Form.Height - $Script:FormRadius)) {
      $collapsedFit = $false
    }
    if (($control.Left + $control.Width) -gt $Script:Form.Width) { $collapsedFit = $false }
    if ($control.Left -lt 0 -or $control.Top -lt 0) { $collapsedFit = $false }
  }
  # 折叠态控件数必须是 1（只有图标）：多一个就说明又有东西被加回那条窄条上了
  $collapsedCount = @($Script:CollapsedControls).Count
  # 老师反馈"折叠后挡住全屏窗口的关闭按钮"：折叠态默认位置必须让开右上角
  $collapsedTop = $Script:Form.Top
  $needTop = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Top + 96
  $collapsedClear = ($collapsedTop -ge $needTop)
  # 试过把折叠/展开画成 PNG 存盘（DrawToBitmap）：这个窗口是分层窗口，画出来是空白，
  # 留着只会误导人。所以只留**数字**：折叠态控件数、图标有没有超出圆角、默认位置让没让开右上角。
  $collapsedShot = ""
  Set-Collapsed $false
  $expandedSize = $Script:Form.Width
  # ★ Find-AppWindow 真的能认出窗口吗——**必须实测**，不能只看代码。
  #   这个函数曾经因为"累加变量和参数同名（$names vs $Names，PowerShell 不区分大小写）"
  #   而永远返回 0：界面照建、按钮照点，只是"打开QQ"变成了又启动一个（弹登录窗）。
  #   拿这个工具栏自己的窗口当靶子：进程名是 powershell，标题是 Study7Agent，标题命中就够。
  [void]$Script:Form.Handle
  $probeHandle = Find-AppWindow -Names @("powershell") -TitleWords @("Study7Agent")
  $probeOk = ($probeHandle -ne [IntPtr]::Zero)
  # ★ QQ 过滤器必须**真的**生效：老师反馈"开了 QQ 图片/腾讯文档在前台，点 QQ 不切聊天窗"。
  #   拿一组假窗口喂进去（图片查看器在最前、腾讯文档在前、聊天窗最小化），
  #   断言结果只剩聊天窗那一个。这是那条 bug 唯一能在没有 QQ 的机器上复现的办法。
  $Script:W7A_FakeRows = @(
    @{ Handle = [IntPtr]101; Pid = 101; Visible = 1; Area = 300000; Title = "图片查看器" },
    @{ Handle = [IntPtr]102; Pid = 102; Visible = 1; Area = 300000; Title = "会议记录 - 腾讯文档" },
    @{ Handle = [IntPtr]103; Pid = 103; Visible = 1; Area = 300000; Title = "QQ音乐" },
    @{ Handle = [IntPtr]104; Pid = 104; Visible = 0; Area = 300000; Title = "QQ" }
  )
  function Get-WindowList { return $Script:W7A_FakeRows }
  function Get-ProcessNameMap {
    return @{ 101 = "qq"; 102 = "qq"; 103 = "qq"; 104 = "qq" }
  }
  $qqSpec = $Script:AppWindows["qq"]
  $qqPicked = @(Find-AppWindows -Names $qqSpec.Names -TitleWords $qqSpec.Titles `
                                -Skip (Get-SpecList $qqSpec "Skip") -Prefer (Get-SpecList $qqSpec "Prefer"))
  # 只剩最小化那个聊天窗（104）——它 Visible=0 但面积够大，照样要被当成候选恢复出来
  $qqOk = ($qqPicked.Count -eq 1 -and $qqPicked[0] -eq [IntPtr]104)
  # ★ 老版 QQ 收进托盘之后主面板**没有标题**：这种窗口必须还能被认出来，
  #   否则"QQ 就在右下角、点按钮却去启动一个新的"。只放行"像主窗口"的那些，
  #   看不见的辅助窗（105 没有 CAPTION/THICKFRAME）永远不许进来。
  $Script:W7A_FakeRows = @(
    @{ Handle = [IntPtr]105; Pid = 105; Visible = 0; Area = 300000; Main = 0; Title = "" },
    @{ Handle = [IntPtr]106; Pid = 106; Visible = 0; Area = 300000; Main = 1; Title = "" }
  )
  function Get-WindowList { return $Script:W7A_FakeRows }
  function Get-ProcessNameMap { return @{ 105 = "qq"; 106 = "qq" } }
  $untitled = @(Find-AppWindows -Names $qqSpec.Names -TitleWords $qqSpec.Titles `
                                -Skip (Get-SpecList $qqSpec "Skip") -Prefer (Get-SpecList $qqSpec "Prefer"))
  $untitledOk = ($untitled.Count -eq 1 -and $untitled[0] -eq [IntPtr]106)
  # ★ "收在托盘里"要判得出来，而且判出来之后是**按它自己的热键叫**，不是硬 ShowWindow 拽。
  #   老师反馈："打开的 qq 窗口是一个完全无法交互的假窗口" —— 硬拽出来的那张，
  #   QQ 自己认为"还藏着"，所以点不动。这里用假窗口把两种情形都钉住。
  $hotkeyKeys = @(Get-SpecList $qqSpec "Hotkey")
  $Script:W7A_FakeRows = @(
    @{ Handle = [IntPtr]104; Pid = 104; Visible = 0; Area = 300000; Title = "QQ" }
  )
  function Get-WindowList { return $Script:W7A_FakeRows }
  function Get-ProcessNameMap { return @{ 104 = "qq" } }
  $trayOk = (-not (Test-AppVisible $qqSpec))          # 收在托盘里 -> 该按热键
  $Script:W7A_FakeRows = @(
    @{ Handle = [IntPtr]104; Pid = 104; Visible = 1; Area = 300000; Title = "QQ" }
  )
  $shownOk = Test-AppVisible $qqSpec                  # 已经看得见 -> 直接切，别多按
  # DryRun 下 Send-Hotkey 必须**只报成功、不真按键**（自检时把老师的 QQ 弹出来就糟了）
  $hotkeyOk = ($hotkeyKeys.Count -eq 3 -and $hotkeyKeys[0] -eq 0x11 -and (Send-Hotkey $hotkeyKeys))
  # 反向验一下：不配过滤器时它一定会把图片查看器也选进来（证明这条断言不是恒真）
  $Script:W7A_FakeRows = @(
    @{ Handle = [IntPtr]101; Pid = 101; Visible = 1; Area = 300000; Title = "图片查看器" },
    @{ Handle = [IntPtr]102; Pid = 102; Visible = 1; Area = 300000; Title = "会议记录 - 腾讯文档" },
    @{ Handle = [IntPtr]103; Pid = 103; Visible = 1; Area = 300000; Title = "QQ音乐" },
    @{ Handle = [IntPtr]104; Pid = 104; Visible = 0; Area = 300000; Title = "QQ" }
  )
  function Get-WindowList { return $Script:W7A_FakeRows }
  function Get-ProcessNameMap {
    return @{ 101 = "qq"; 102 = "qq"; 103 = "qq"; 104 = "qq" }
  }
  $qqRaw = @(Find-AppWindows -Names $qqSpec.Names -TitleWords $qqSpec.Titles)
  $qqRawCount = $qqRaw.Count

  # 用 [Console]::Out 而不是 Write-Output：PowerShell 2.0 的宿主会按控制台宽度
  # （默认 80 列）把输出**折行**，这一行会被从中间断开，解析方直接读不出来。
  # 真机上栽过：zh=36830,1998 / 1,19978 被拆成了两行。
  [Console]::Out.WriteLine("OK|buttons=" + $Script:Buttons.Count + "|handler=" + $ok +
                           "|action=" + $pickedAction +
                           "|local=" + $localOk +
                           "|click=" + (1 -eq 1 -and $clickOk) +
                           "|show=" + $showOk +
                           "|exe=" + $exeOk +
                           "|collapse=" + $collapsedSize + "/" + $expandedSize +
                           "|collapsedFit=" + $collapsedFit +
                           "|collapsedCount=" + $collapsedCount +
                           "|clear=" + $collapsedClear +
                           "|shot=" + $collapsedShot +
                           "|find=" + (1 -eq 1 -and $probeOk) +
                           "|qq=" + (1 -eq 1 -and $qqOk) + "/" + $qqRawCount +
                           "|untitled=" + (1 -eq 1 -and $untitledOk) +
                           "|zh=" + ($codes -join ",") +
                           "|hotkey=" + (1 -eq 1 -and $hotkeyOk -and $trayOk -and $shownOk) +
                           "|status=" + $Script:StatusLabel.Text)
  exit 0
}

# --------------------------------------------------------------------------
# 跑起来
# --------------------------------------------------------------------------
[System.Windows.Forms.Application]::EnableVisualStyles()
$Script:Timer = New-Object System.Windows.Forms.Timer
$Script:Timer.Interval = $PollSeconds * 1000
$Script:Timer.Add_Tick({ Update-Status })
$Script:Timer.Start()

$Script:Form.Add_Shown({
  # 老师上次把工具栏收起来了就一直收着：这东西挡视野是真实痛点
  if (Load-Collapsed) { Set-Collapsed $true }
  Update-Status
})
$Script:Form.Add_FormClosed({
  if ($Script:Timer -ne $null) { $Script:Timer.Stop() }
})

Write-Log ("启动：api=" + $Script:ApiBase + " url=" + $Script:Url)
[System.Windows.Forms.Application]::Run($Script:Form)
Write-Log "窗口已关闭"
exit 0
