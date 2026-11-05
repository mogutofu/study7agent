// ============================================================================
// Study7Agent.exe —— 老师双击就能用的启动器（Windows 7 SP1 x64 / .NET Framework 3.5）
//
// 为什么要有它：以前老师得先双击 start-webui.cmd，屏幕上多一个黑窗口，再自己去浏览器
// 标签页里找界面。这个 exe 把整条链路收进一次双击：
//   1. 单实例（有名互斥体）：双击第二次只是把已经开着的界面窗口切到前台
//   1b. 开机自启档（注册表 Run 项 / 启动文件夹快捷方式传 --autostart）：
//       先等桌面（explorer + 任务栏 Shell_TrayWnd）就绪再起服务，
//       **只显示悬浮工具栏，不打开界面窗口、不抢前台**；发现已经有一个在跑就
//       记一笔日志直接退出（绝不发 Show 信号把界面弹出来）。失败时也不弹错误框，
//       只写 logs\exe.log —— 开机那一刻老师根本不在电脑前，设置里的
//       「开机自启动诊断」会把最近一次结果读出来。
//   2. 检查随包文件（python\python.exe、webui\server.py）
//   3. 服务已经在跑吗（HTTP GET /api/state，1.5 秒超时）——在跑就直接开界面
//   4. 没有就**无窗口**地起 python webui\server.py --no-open --no-float，
//      stdout/stderr 追加进 logs\exe.log，然后再无窗口地起悬浮工具栏
//      （scripts\study7agent-float.ps1，参数跟 webui\server.py 里那份一模一样）
//   5. 起一个无边框的「正在启动 Study7Agent…」小窗，每 300 毫秒探一次端口，最多 40 秒
//   6. 用 Chromium 的 --app= 打开界面（应用窗口，不是浏览器标签页），失败才退回默认浏览器
//   7. 托盘图标：打开界面 / 打开工作区 / 重启服务 / 查看日志 / 退出（连服务一起关）
//   8. 退出时只收拾**自己拉起来的**进程树（taskkill /PID <pid> /T /F）
//
// 编译期约束（必须一直是 CLR 2.0 兼容）：
//   C:\Windows\Microsoft.NET\Framework\v3.5\csc.exe /noconfig /target:winexe
//       /reference:System.dll /reference:System.Windows.Forms.dll /reference:System.Drawing.dll
//   所以这里**不能**用 async / dynamic / 内插字符串 / nameof / ?. / LINQ（System.Core 不引用）。
//   源码是 UTF-8：csc 3.5 不带 BOM 时按本地代码页解码，编译时**必须**加 /codepage:65001
//   （scripts\build-launcher.ps1 里已经带了）。中文一变乱码，界面上就是一堆问号。
// ============================================================================

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Program
{
    // 单实例：Local\ 只在当前登录会话内有效，正好是「这台机器的这个桌面」
    const string MutexName = "Local\\study7agent-exe";
    // 第二个实例打招呼的两个信号：一个让它把界面切到前台，一个让它退出
    const string ShowEventName = "Local\\study7agent-exe-show";
    const string QuitEventName = "Local\\study7agent-exe-quit";

    const int DefaultPort = 8770;
    const int ReadyTimeoutMs = 40000;      // 服务起不来时的总等待上限
    const int PollIntervalMs = 300;
    // 开机自启：最多等桌面这么久（开机瞬间 explorer / 托盘 / 网络盘都可能还没好）。
    // 等不到也照样起服务：有的教室用希沃管家接管桌面，套的壳不一定叫 Shell_TrayWnd。
    const int AutostartDesktopTimeoutMs = 90000;

    static string baseDir = "";
    // 代码目录（python\ webui\ scripts\ 内置内核）与数据目录分开：
    //   目录版：两者是同一个，就是 exe 所在文件夹（老行为，一个字没变）
    //   单文件版：程序树解到 %LOCALAPPDATA%\Study7Agent\runtime\<戳>，而 config\ data\ logs\
    //             仍然留在 exe 旁边 —— 密钥不进二进制，这是 AGENTS.md §2 的硬规矩，
    //             顺带升级只要换 exe，会话与密钥都不动。
    static string codeDir = "";
    static string debugPort = "";           // 开发/自检用：给内核开远程调试端口
    static bool gpuDisabled;                // compat.ini 里 gpu=0，或者内核崩过一次后自动置位
    static int kernelRetry;                 // 内核"起来就崩"的重试级别：0 正常 / 1 关 GPU / 2 关沙箱
    static string uiUrl = "";
    static int port = DefaultPort;
    static bool autostartMode;              // --autostart：开机自启档（只显示悬浮栏、不抢前台）

    static Mutex instanceMutex;
    static EventWaitHandle showEvent;
    static EventWaitHandle quitEvent;

    static Process serverProcess;          // 只有**我们自己**起的服务才留着 pid（退出时要收）
    static Process floatProcess;           // 悬浮工具栏同理
    static volatile Process browserProcess; // 我们自己起的那个浏览器实例（独立 profile，退出时一起关）
    static DateTime browserStartedAt;      // 上一次拉起浏览器的时刻（首次运行要建 profile，慢）
    static volatile bool stopping;         // 正在退出：别再响应「服务没了」
    static volatile bool restarting;       // 正在重启：服务退出是预期内的
    static volatile bool windowClosed;     // 界面窗口被老师关掉了：只记状态，程序照跑（见 WatchAppWindowExit）
    static volatile bool pendingQuit;      // 消息循环还没起来时收到的退出请求
    static volatile bool pendingShow;      // 同上：把界面叫到前台的请求

    static NotifyIcon tray;
    static Form pump;                      // 隐藏宿主窗体：跑消息循环 + 跨线程调度
    static FileStream logStream;
    static readonly object logLock = new object();

    // ------------------------------------------------------------------ 入口

    [STAThread]
    static void Main(string[] args)
    {
        try
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Environment.ExitCode = Run(args);
        }
        catch (Exception ex)
        {
            // 9. 出意外也要说人话，不能一声不响地消失。
            //    开机自启档例外：那一刻老师根本不在电脑前，弹个错误框只会挡住登录界面，
            //    把原因写进 logs\exe.log（设置里的「开机自启动诊断」会读最近一条）。
            if (HasArg(Environment.GetCommandLineArgs(), "--autostart"))
            {
                try
                {
                    baseDir = Path.GetDirectoryName(Application.ExecutablePath);
                    OpenLog();
                    LogLine("开机自启时出意外：" + ex.ToString());
                    CloseLog();
                }
                catch (Exception)
                {
                    // 日志写不了也不能再弹框
                }
                Environment.ExitCode = 1;
                return;
            }
            MessageBox.Show("Study7Agent 启动失败：\r\n\r\n" + ex.Message,
                            "Study7Agent", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Environment.ExitCode = 1;
        }
    }

    static int Run(string[] args)
    {
        bool wantQuit = HasArg(args, "--quit");
        autostartMode = HasArg(args, "--autostart");
        debugPort = ArgValue(args, "--debug-port");

        bool created;
        instanceMutex = new Mutex(true, MutexName, out created);
        if (!created)
        {
            // 1. 已经有一个在跑了：把界面切到前台（或让它退出），然后本进程立刻收工
            if (autostartMode)
            {
                // 开机自启只是"确保它在跑"：**不许**发 Show 信号。
                // 老师刚登录，桌面还没稳，这时候突然弹一个界面窗口出来最讨人嫌
                // （老师原话："点悬浮窗图标只展开悬浮窗，不把程序切到前台"）。
                LogAutostartSkip();
                return 0;
            }
            Signal(wantQuit ? QuitEventName : ShowEventName);
            return 0;
        }

        if (wantQuit)
        {
            return 0;                       // 自己是唯一实例，没有可退出的对象
        }

        baseDir = Path.GetDirectoryName(Application.ExecutablePath);
        if (string.IsNullOrEmpty(baseDir))
        {
            if (autostartMode)
            {
                // 开机自启时不许弹框：开机那一刻没人看着屏幕，弹个窗口反而把登录界面顶掉
                return 1;
            }
            MessageBox.Show("读不到本程序所在的文件夹。", "Study7Agent",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        baseDir = EnsureWritableBaseDir(baseDir);
        gpuDisabled = ReadCompatGpuDisabled();
        // 日志要**现在**就开：解包那几分钟里的每一行（解到哪、清掉了什么）恰恰是
        // 首次启动出问题时最想看的东西。以前日志是在起服务那一步才开的，
        // 这一段全丢了（在 Win7 客机上排查时才发现）。OpenLog 自己是幂等的。
        try
        {
            OpenLog();
        }
        catch (Exception)
        {
            // 日志开不了不是致命问题
        }

        // 0. 单文件版：程序树以 tar.gz 追加在本 exe 尾部，首次先解出来
        codeDir = baseDir;
        Payload.Info payload = Payload.Read(Application.ExecutablePath);
        if (payload != null)
        {
            string runtimeRoot = Path.Combine(RuntimeRoot(), "runtime");
            codeDir = Path.Combine(runtimeRoot, payload.Stamp);
            LogLine("单文件版：程序树在 " + codeDir);
            if (!Payload.IsExtracted(codeDir))
            {
                ExtractPayload(payload);
            }
            PurgeOldRuntimes(runtimeRoot, payload.Stamp);
        }

        // 2. 随包文件齐不齐（半个 zip 拖出来的情况：python\ 在、webui\ 不在）
        List<string> missing = new List<string>();
        if (!File.Exists(Path.Combine(codeDir, Path.Combine("python", "python.exe"))))
        {
            missing.Add(@"python\python.exe");
        }
        if (!File.Exists(Path.Combine(codeDir, Path.Combine("webui", "server.py"))))
        {
            missing.Add(@"webui\server.py");
        }
        if (missing.Count > 0)
        {
            string what = "这个文件夹里缺少随程序发布的运行文件：" + string.Join("、", missing.ToArray())
                          + "（目录：" + codeDir + "）";
            if (autostartMode)
            {
                // 开机自启不弹框，写进日志；设置里的「开机自启动诊断」会读到
                LogLine("开机自启失败：" + what);
                return 1;
            }
            MessageBox.Show(what + "\r\n\r\n请把整个 zip **完整解压**后再双击 Study7Agent.exe；"
                            + "不要只把里面几个文件拖出来。",
                            "Study7Agent 无法启动", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 1;
        }

        EnsureAgentConfig();
        EnsureDesktopShortcut();
        // 开始菜单里放一份（老师想固定到任务栏就拖这个，不是拖浏览器内核窗口）
        EnsureStartMenuShortcut();

        port = ReadPort();
        uiUrl = UiUrl(port, JsonString("access_token"));

        // 单实例的第二个实例靠这两个信号跟本进程说话；要在开界面之前建好，
        // 否则第二次双击正好落在这段空档里就只会静静地什么都不发生。
        showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName);
        quitEvent = new EventWaitHandle(false, EventResetMode.AutoReset, QuitEventName);
        Thread listener = new Thread(WaitForSignals);
        listener.IsBackground = true;
        listener.Start();

        // 2b. 开机自启：先等桌面就绪再起服务。开机瞬间 explorer / 托盘 / 网络盘
        //     都可能还没好，抢在前面起服务会碰到"配置读不到、托盘图标不见了"这类怪事。
        if (autostartMode)
        {
            LogLine("开机自启模式：先等桌面（explorer / 任务栏）就绪，再起服务；"
                    + "起来之后只显示悬浮工具栏，不打开界面窗口、不抢前台。");
            WaitForDesktopReady();
        }

        // 3. 服务已经在跑吗？在跑就别再起一个（Windows 上能重复绑同一端口，起重复会写坏会话）
        if (!ServerAnswers(port, 1500))
        {
            // 4. + 5. 起服务、起工具栏、等它就绪
            if (!StartServerAndWait())
            {
                return 1;                   // 错误框在 StartServerAndWait 里已经弹过了
            }
        }
        // 服务可能刚给自己生成了访问令牌（监听局域网时），所以这里重新取一次
        uiUrl = UiUrl(port, JsonString("access_token"));

        // 6. 应用窗口（不是浏览器标签页）
        if (autostartMode)
        {
            // 开机自启**不开界面窗口**：老师要的是"开机之后只有一个悬浮栏"。
            // 正常双击 exe 时另有一个第二个实例发 Show 信号，那条路照旧切前台。
            if (floatProcess == null)
            {
                try
                {
                    floatProcess = StartFloatToolbar();
                }
                catch (Exception ex)
                {
                    LogLine("开机自启：悬浮工具栏没能启动（不影响使用）：" + ex.Message);
                }
            }
            LogLine("开机自启模式：已跳过界面窗口，只显示悬浮工具栏"
                    + "（要用界面请点悬浮栏里的「显示界面」）。");
        }
        else
        {
            OpenAppWindow(uiUrl, false);
        }

        // 7. 托盘
        CreateTray();

        // 隐藏窗体只用来跑消息循环：界面本体是浏览器的 --app 窗口
        pump = new PumpForm();
        pump.FormBorderStyle = FormBorderStyle.None;
        pump.ShowInTaskbar = false;
        pump.StartPosition = FormStartPosition.Manual;
        pump.Location = new Point(-32000, -32000);
        pump.Size = new Size(1, 1);

        // 启动那一两秒里收到的信号在这里补做（那时 pump 还没建好，BeginInvoke 无处可去）
        if (pendingQuit)
        {
            LogLine("启动过程中收到退出请求，直接收摊");
            ExitApplication();
            return 0;
        }
        if (pendingShow)
        {
            OpenAppWindow(uiUrl, true);
        }
        Application.Run(pump);

        CloseLog();
        return 0;
    }

    static bool HasArg(string[] args, string name)
    {
        if (args == null)
        {
            return false;
        }
        foreach (string item in args)
        {
            if (string.Equals(item, name, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }
        return false;
    }

    static void Signal(string name)
    {
        try
        {
            using (EventWaitHandle handle = EventWaitHandle.OpenExisting(name))
            {
                handle.Set();
            }
        }
        catch (Exception)
        {
            // 对方刚好在退出：什么都不用做
        }
    }

    static void WaitForSignals()
    {
        WaitHandle[] handles = new WaitHandle[] { showEvent, quitEvent };
        while (true)
        {
            int index;
            try
            {
                index = WaitHandle.WaitAny(handles);
            }
            catch (Exception)
            {
                return;
            }
            if (index == 0)
            {
                // 消息循环还没建好（第二次双击正好落在启动那一两秒里）就先记下来：
                // 直接丢掉的话，老师那边就是"双击了没反应"
                if (!Invoke(delegate { OpenAppWindow(uiUrl, true); }))
                {
                    pendingShow = true;
                }
            }
            else
            {
                if (!Invoke(delegate { ExitApplication(); }))
                {
                    pendingQuit = true;
                }
            }
        }
    }

    static bool Invoke(MethodInvoker action)
    {
        try
        {
            if (pump != null && pump.IsHandleCreated)
            {
                pump.BeginInvoke(action);
                return true;
            }
        }
        catch (Exception)
        {
            // 正在关闭：忽略
        }
        return false;                  // 消息循环还没起来：调用方自己记下来（见 pendingQuit/pendingShow）
    }

    // -------------------------------------------------------- config\ 读取

    static string ConfigPath()
    {
        return Path.Combine(baseDir, Path.Combine("config", "webui.json"));
    }

    static string ConfigText()
    {
        try
        {
            if (!File.Exists(ConfigPath()))
            {
                return "";
            }
            byte[] raw = File.ReadAllBytes(ConfigPath());
            int offset = (raw.Length >= 3 && raw[0] == 0xEF && raw[1] == 0xBB && raw[2] == 0xBF) ? 3 : 0;
            return Encoding.UTF8.GetString(raw, offset, raw.Length - offset);
        }
        catch (Exception)
        {
            return "";
        }
    }

    /// <summary>port：config\webui.json 的 "port"，读不出来就用 8770。</summary>
    static int ReadPort()
    {
        Match match = Regex.Match(ConfigText(), "\"port\"\\s*:\\s*([0-9]+)");
        if (match.Success)
        {
            int value;
            if (int.TryParse(match.Groups[1].Value, NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                && value > 0 && value < 65536)
            {
                return value;
            }
        }
        return DefaultPort;
    }

    /// <summary>取一个字符串项（"key": "value"）；JSON 里的转义按需还原，读不到就返回 ""。</summary>
    static string JsonString(string key)
    {
        Match match = Regex.Match(ConfigText(),
                                  "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");
        if (!match.Success)
        {
            return "";
        }
        return Unescape(match.Groups[1].Value);
    }

    static string Unescape(string text)
    {
        StringBuilder builder = new StringBuilder(text.Length);
        for (int index = 0; index < text.Length; index++)
        {
            char current = text[index];
            if (current != '\\' || index + 1 >= text.Length)
            {
                builder.Append(current);
                continue;
            }
            index++;
            char next = text[index];
            switch (next)
            {
                case 'n': builder.Append('\n'); break;
                case 't': builder.Append('\t'); break;
                case 'r': builder.Append('\r'); break;
                case 'b': builder.Append('\b'); break;
                case 'f': builder.Append('\f'); break;
                case 'u':
                    if (index + 4 < text.Length)
                    {
                        int code;
                        if (int.TryParse(text.Substring(index + 1, 4), NumberStyles.HexNumber,
                                         CultureInfo.InvariantCulture, out code))
                        {
                            builder.Append((char)code);
                            index += 4;
                        }
                    }
                    break;
                default: builder.Append(next); break;      // \" \\ \/ 都落到这里
            }
        }
        return builder.ToString();
    }

    /// <summary>界面地址：服务只在本机监听，所以永远走 127.0.0.1（避免 0.0.0.0 那种写法）。</summary>
    static string UiUrl(int targetPort, string token)
    {
        string url = "http://127.0.0.1:" + targetPort.ToString(CultureInfo.InvariantCulture) + "/";
        if (!string.IsNullOrEmpty(token))
        {
            url += "?token=" + Uri.EscapeDataString(token);
        }
        return url;
    }

    /// <summary>start-webui.cmd 里那步「没有 agent.json 就从示例复制一份」的等价实现。</summary>
    static void EnsureAgentConfig()
    {
        try
        {
            string folder = Path.Combine(baseDir, "config");
            // 目录必须先建出来：单文件版的 baseDir 只有一个 exe，File.Copy 到不存在的目录会
            // 抛 DirectoryNotFoundException，被下面的 catch 静默吞掉——老师那边就是
            // "没有 config\agent.json、模型是空的"（目录版因为包里本来就有 config\ 才没暴露）。
            if (!Directory.Exists(folder))
            {
                Directory.CreateDirectory(folder);
            }
            string target = Path.Combine(folder, "agent.json");
            // 示例在代码目录里（单文件版的 config\ 只有老师自己的东西，示例随程序走）
            string example = Path.Combine(folder, "agent.example.json");
            if (!File.Exists(example))
            {
                example = Path.Combine(codeDir, Path.Combine("config", "agent.example.json"));
            }
            if (!File.Exists(target) && File.Exists(example))
            {
                File.Copy(example, target);
                LogLine("已按示例生成 config\\agent.json");
            }
        }
        catch (Exception ex)
        {
            LogLine("准备 config 目录失败（服务端会自己报错）：" + ex.Message);
        }
    }

    /// <summary>
    /// 首次运行建一个桌面快捷方式：老师那边的习惯是"装完就该有个图标"，
    /// 而 zip 里放一个 .lnk 是不靠谱的（快捷方式里存的是绝对路径，解压到别处就断了）。
    /// 用 config\installed.txt 当"已经装过"的标记：老师自己删掉图标后不会再被反复种回来。
    /// </summary>
    static void EnsureDesktopShortcut()
    {
        try
        {
            string marker = Path.Combine(baseDir, Path.Combine("config", "installed.txt"));
            if (File.Exists(marker))
            {
                return;
            }
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            string link = Path.Combine(desktop, "Study7Agent.lnk");
            if (!File.Exists(link))
            {
                CreateShortcut(Application.ExecutablePath, link, "", baseDir);
                LogLine("已创建桌面快捷方式：" + link);
            }
            string folder = Path.GetDirectoryName(marker);
            if (!Directory.Exists(folder))
            {
                Directory.CreateDirectory(folder);
            }
            File.WriteAllText(marker,
                "首次运行：" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture)
                + "\r\nexe：" + Application.ExecutablePath + "\r\n", Encoding.UTF8);
        }
        catch (Exception ex)
        {
            // 建不了快捷方式绝不该拦住启动（只读的桌面目录、组策略禁用等等）
            LogLine("创建桌面快捷方式失败（不影响使用）：" + ex.Message);
        }
    }

    /// <summary>
    /// 用 WScript.Shell 建 .lnk。走 COM 反射而不是声明 IShellLink：
    /// 少 60 行接口声明，Win7 上 WScript.Shell 一定有。
    /// </summary>
    static void CreateShortcut(string exePath, string linkPath, string arguments, string workDir)
    {
        Type shellType = Type.GetTypeFromProgID("WScript.Shell");
        if (shellType == null)
        {
            throw new InvalidOperationException("系统里没有 WScript.Shell");
        }
        object shell = Activator.CreateInstance(shellType);
        object link = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod,
                                             null, shell, new object[] { linkPath });
        Type linkType = link.GetType();
        linkType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link,
                              new object[] { exePath });
        linkType.InvokeMember("Arguments", BindingFlags.SetProperty, null, link,
                              new object[] { arguments ?? "" });
        linkType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link,
                              new object[] { workDir ?? "" });
        linkType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, link,
                              new object[] { exePath + ",0" });
        linkType.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
    }

    /// <summary>
    /// 开始菜单快捷方式（一份就够，方便老师自己拖到任务栏）。
    ///
    /// 为什么不做"直接往任务栏固定目录里塞快捷方式"：试过，太糙 —— 那个目录归 Explorer 管，
    /// 塞进去要重启 explorer 才显示（我们不会去动老师的桌面），显示出来的行为也不可控。
    /// 老师一句"干脆还是别做了"，所以只留这一份开始菜单快捷方式：想固定就把桌面或开始菜单里
    /// 的 Study7Agent 拖到任务栏（那个才是程序本身，不是浏览器内核窗口）。
    /// </summary>
    static void EnsureStartMenuShortcut()
    {
        try
        {
            string programs = Environment.GetFolderPath(Environment.SpecialFolder.Programs);
            string menuLink = Path.Combine(programs, "Study7Agent.lnk");
            if (!File.Exists(menuLink))
            {
                CreateShortcut(Application.ExecutablePath, menuLink, "", baseDir);
                LogLine("已创建开始菜单快捷方式：" + menuLink);
            }
        }
        catch (Exception ex)
        {
            LogLine("创建开始菜单快捷方式失败（不影响使用）：" + ex.Message);
        }
    }
    /// <summary>形如 --debug-port 8788 的取值参数；没有就返回 ""。</summary>
    static string ArgValue(string[] args, string name)
    {
        if (args == null)
        {
            return "";
        }
        for (int index = 0; index + 1 < args.Length; index++)
        {
            if (string.Equals(args[index], name, StringComparison.OrdinalIgnoreCase))
            {
                return args[index + 1];
            }
        }
        return "";
    }

    // ------------------------------------------- 单文件版：解包 / 可写目录 / 兼容开关

    /// <summary>
    /// 单文件版把程序树解到哪个根的下面。这里有一条**实测踩出来的硬规则**：
    /// 不要解到用户目录（C:\Users\&lt;用户&gt;\）下面。
    ///
    /// 现象：同一份 Chromium 109，放在 C:\ProgramData、C:\其它目录、D:\ 下都正常；
    /// 只要在 AppData\Local、AppData\Roaming、甚至 Temp 下，GPU 进程就会以
    /// STATUS_DLL_INIT_FAILED(0xC0000142) 连崩三次，然后浏览器 FATAL 退出：
    ///     FATAL:gpu_data_manager_impl_private.cc(440)] GPU process isn't usable. Goodbye.
    /// 试过都无效：--disable-gpu、--in-process-gpu、--disable-gpu-sandbox、
    /// --disable-software-rasterizer；只有 --no-sandbox 能跑（那是安全上的让步，只当最后手段）。
    /// 触发原因和本机装的"注入型"软件有关，但产品不能赌教室机器上没有 —— 所以：
    ///   1) 程序所在目录可写、且不在用户目录下：就解在它旁边（U 盘/独立文件夹，最干净）
    ///   2) 否则放 ProgramData（机器级，标准位置）
    ///   3) 都不行才退回用户目录（此时靠内核重试阶梯里的 --no-sandbox 兜底）
    /// </summary>
    static string RuntimeRoot()
    {
        string exeFolder = Path.GetDirectoryName(Application.ExecutablePath);
        // SpecialFolder.UserProfile 是 .NET 4.0 才有的枚举值，3.5 上只能用环境变量
        string userFolder = Environment.GetEnvironmentVariable("USERPROFILE") ?? "";
        if (IsWritable(exeFolder) && !IsUnder(exeFolder, userFolder))
        {
            LogLine("解包位置：程序目录（" + exeFolder + "）");
            return exeFolder;
        }
        string programData = Path.Combine(Environment.GetFolderPath(
            Environment.SpecialFolder.CommonApplicationData), "Study7Agent");
        if (EnsureFolder(programData) && IsWritable(programData))
        {
            LogLine("解包位置：ProgramData（程序目录不可用或落在用户目录下）");
            return programData;
        }
        string local = Path.Combine(Environment.GetFolderPath(
            Environment.SpecialFolder.LocalApplicationData), "Study7Agent");
        EnsureFolder(local);
        LogLine("解包位置：用户目录（前两个位置都不行，内核可能要退到 --no-sandbox）");
        return local;
    }

    static bool IsUnder(string folder, string parent)
    {
        if (string.IsNullOrEmpty(folder) || string.IsNullOrEmpty(parent))
        {
            return false;
        }
        string left = Path.GetFullPath(folder).TrimEnd('\\').ToLowerInvariant();
        string right = Path.GetFullPath(parent).TrimEnd('\\').ToLowerInvariant();
        return left == right || left.StartsWith(right + "\\", StringComparison.Ordinal);
    }

    static bool EnsureFolder(string folder)
    {
        try
        {
            if (!Directory.Exists(folder))
            {
                Directory.CreateDirectory(folder);
            }
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    static bool IsWritable(string folder)
    {
        if (string.IsNullOrEmpty(folder) || !Directory.Exists(folder))
        {
            return false;
        }
        try
        {
            string probe = Path.Combine(folder, ".write-test");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>
    /// 清掉旧版本解出来的 runtime\&lt;旧戳&gt;（外加上次没解完的 .tmp）。
    /// 一份 300 多兆，升几次级就能把教室机器的 C 盘塞满。这个目录完全是我们自己的，
    /// 删起来没有顾虑；正被占用的（老师还开着旧版本的窗口）删不掉就跳过，下次再说。
    /// </summary>
    static void PurgeOldRuntimes(string runtimeRoot, string keep)
    {
        try
        {
            if (!Directory.Exists(runtimeRoot))
            {
                return;
            }
            foreach (string folder in Directory.GetDirectories(runtimeRoot))
            {
                string name = Path.GetFileName(folder);
                if (string.Equals(name, keep, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                try
                {
                    Directory.Delete(folder, true);
                    LogLine("清掉旧的运行环境：" + name);
                }
                catch (Exception)
                {
                    // 被占用/没权限：留着不影响这次启动
                }
            }
        }
        catch (Exception)
        {
            // 列目录失败就当没这回事
        }
    }

    /// <summary>首次启动把尾部 payload 解出来（位置由 RuntimeRoot 决定），期间显示进度窗。</summary>
    static void ExtractPayload(Payload.Info payload)
    {
        LogLine("单文件版首次启动：解包到 " + codeDir);
        bool done = false;
        Exception failure = null;
        using (ProgressForm form = new ProgressForm(payload.Stamp))
        {
            form.Show();
            Application.DoEvents();
            Thread worker = new Thread(delegate()
            {
                try
                {
                    Payload.Extract(Application.ExecutablePath, payload, codeDir,
                                    delegate(long current, long total)
                                    { form.Report(current, total); });
                }
                catch (Exception ex)
                {
                    failure = ex;
                }
                finally
                {
                    done = true;
                }
            });
            worker.IsBackground = true;
            worker.Start();
            // 这里还没有消息循环（Application.Run 在后面），自己抽消息把进度条画出来
            while (!done)
            {
                Application.DoEvents();
                Thread.Sleep(30);
            }
            form.Close();
        }
        if (failure != null)
        {
            throw new InvalidOperationException("解包运行环境失败：" + failure.Message, failure);
        }
        LogLine("解包完成：" + codeDir);
    }

    /// <summary>
    /// config\ data\ logs\ 要写在 exe 旁边；写在 Program Files 或只读共享盘上会失败，
    /// 这时退到 %LOCALAPPDATA%\Study7Agent，别让老师看到一堆"拒绝访问"。
    /// </summary>
    static string EnsureWritableBaseDir(string folder)
    {
        try
        {
            string probe = Path.Combine(folder, ".write-test");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
            return folder;
        }
        catch (Exception ex)
        {
            string fallback = Path.Combine(Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData), "Study7Agent");
            LogLine("程序目录不可写（" + ex.Message + "），配置与数据改放 " + fallback);
            try
            {
                if (!Directory.Exists(fallback))
                {
                    Directory.CreateDirectory(fallback);
                }
            }
            catch (Exception)
            {
                // 连这里都写不了就只能照旧，后面会自己报错
            }
            return fallback;
        }
    }

    /// <summary>compat.ini 里 gpu=0 表示这台机器的显卡驱动跑不动内核的硬件加速。</summary>
    static bool ReadCompatGpuDisabled()
    {
        try
        {
            string path = Path.Combine(baseDir, "compat.ini");
            if (!File.Exists(path))
            {
                return false;
            }
            foreach (string raw in File.ReadAllLines(path, Encoding.UTF8))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line[0] == '#' || line[0] == ';')
                {
                    continue;
                }
                int split = line.IndexOf('=');
                if (split <= 0)
                {
                    continue;
                }
                if (line.Substring(0, split).Trim().ToLowerInvariant() != "gpu")
                {
                    continue;
                }
                string value = line.Substring(split + 1).Trim().ToLowerInvariant();
                return value == "0" || value == "false" || value == "no";
            }
        }
        catch (Exception)
        {
            // 读不了就当没配
        }
        return false;
    }

    /// <summary>
    /// 内核"起来就崩"时按级别重试，最多两级。
    ///   第 1 级：关 GPU（教室一体机的老 Intel 驱动最常见）
    ///   第 2 级：关 GPU + 关沙箱（用户目录下跑内核时的 STATUS_DLL_INIT_FAILED，见 RuntimeRoot）
    ///
    /// 判"是不是崩了"看**退出码**，不看活了多久：正常退出（0，点叉关掉最后一个窗口就是这条）
    /// 绝不重开。以前两边都用"活了不到 10 秒就当崩"这个**时间**判据，是错的 ——
    /// 重试把窗口拉回来之后计时会重置，老师再点一次叉（离重开不到 10 秒）又被当成崩溃，又重开一次；
    /// 老师的原话："关一次打开一次要关三次才能关上"（重试阶梯正好两级，所以要三次）。
    /// </summary>
    static int ExitCodeOf(Process process)
    {
        if (process == null)
        {
            return 0;
        }
        try
        {
            return process.ExitCode;
        }
        catch (Exception)
        {
            return 0;                      // 拿不到就按"正常退出"处理：宁可不重开，也不能关不掉
        }
    }

    static void ArmKernelRetry(Process launched, string url)
    {
        if (launched == null || kernelRetry >= 2)
        {
            return;
        }
        DateTime startedAt = DateTime.UtcNow;
        try
        {
            launched.EnableRaisingEvents = true;
        }
        catch (Exception)
        {
            return;
        }
        launched.Exited += delegate
        {
            double alive = (DateTime.UtcNow - startedAt).TotalSeconds;
            if (stopping || restarting)
            {
                return;
            }
            int code = ExitCodeOf(launched);
            if (code == 0)
            {
                return;                    // 老师点叉关的（正常退出）：不是崩溃，绝不重开
            }
            kernelRetry++;
            gpuDisabled = true;
            LogLine("内核启动 " + alive.ToString("0.0", CultureInfo.InvariantCulture)
                    + " 秒就退出了（退出码 " + code.ToString(CultureInfo.InvariantCulture)
                    + "），第 " + kernelRetry + " 级重试"
                    + (kernelRetry >= 2 ? "（关 GPU + 关沙箱）" : "（关 GPU）"));
            Invoke(delegate { OpenAppWindow(url, true); });
        };
    }

    /// <summary>
    /// 界面窗口被关掉 = **收进托盘**，不是退出程序：服务照跑、托盘图标还在，
    /// 老师点托盘「打开界面」或悬浮窗的「显示界面」就能把它开回来
    /// （走 OpenAppWindow，还是原来那个 profile，不会变成新标签页）。
    ///
    /// 判据：**正常退出（退出码 0）= 老师点叉关的**（不管开了多久都算数）；
    /// 只有"非 0 退出码 + 很早就没了"那种才交给 ArmKernelRetry 去重试。
    /// 这里原来只看"活了 10 秒以上"，于是关得快一点就被当成崩溃、窗口又自己弹回来。
    /// </summary>
    static void WatchAppWindowExit(Process launched)
    {
        if (launched == null)
        {
            return;
        }
        DateTime startedAt = DateTime.UtcNow;
        try
        {
            launched.EnableRaisingEvents = true;
        }
        catch (Exception)
        {
            return;
        }
        launched.Exited += delegate
        {
            if (stopping || restarting)
            {
                return;
            }
            double alive = (DateTime.UtcNow - startedAt).TotalSeconds;
            int code = ExitCodeOf(launched);
            if (code != 0 && alive <= 10.0)
            {
                return;                        // 起来就崩：交给 ArmKernelRetry，这不是老师关窗
            }
            windowClosed = true;
            LogLine("界面窗口已关闭（老师关窗）：WebUI 服务继续在后台跑、托盘图标还在，"
                    + "点托盘「打开界面」或悬浮窗「显示界面」可以再开出来。");
            Invoke(delegate { NotifyWindowClosed(); });
        };
    }

    /// <summary>
    /// 界面窗口刚被关掉时说一声。Win7 默认把托盘图标藏在"隐藏的图标"里，
    /// 不主动冒个气泡，老师那边看着就是"程序被关掉了"。
    /// </summary>
    static void NotifyWindowClosed()
    {
        if (tray == null || !windowClosed)
        {
            return;                            // 这中间老师已经自己把界面开回来了：别再说"关掉了"
        }
        try
        {
            tray.Text = "Study7Agent（界面已关闭，双击打开界面）";
            tray.BalloonTipTitle = "Study7Agent 还在后台跑";
            tray.BalloonTipText = "界面窗口关掉了，服务没有停。点托盘图标或悬浮窗的「显示界面」就能开回来。";
            tray.BalloonTipIcon = ToolTipIcon.Info;
            tray.ShowBalloonTip(5000);
        }
        catch (Exception)
        {
            // 气泡提示失败不影响使用
        }
    }

    /// <summary>界面窗口又开出来了：托盘提示改回正常那句。</summary>
    static void MarkWindowOpen()
    {
        windowClosed = false;
        if (tray == null)
        {
            return;
        }
        try
        {
            tray.Text = "Study7Agent（双击打开界面）";
        }
        catch (Exception)
        {
            // 改不了就保持原样
        }
    }

    // ------------------------------------------------------------ 服务与日志
    /// <summary>3. HTTP GET /api/state；只有真的答上来（而且是本项目的 JSON）才算在跑。</summary>
    static bool ServerAnswers(int targetPort, int timeoutMs)
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(
                "http://127.0.0.1:" + targetPort.ToString(CultureInfo.InvariantCulture) + "/api/state");
            request.Method = "GET";
            request.Timeout = timeoutMs;
            request.ReadWriteTimeout = timeoutMs;
            request.Proxy = null;              // 别让 IE 代理设置拖慢本机回环
            request.KeepAlive = false;
            using (WebResponse response = request.GetResponse())
            {
                using (Stream stream = response.GetResponseStream())
                {
                    byte[] buffer = new byte[262144];
                    int total = 0;
                    int read;
                    while (total < buffer.Length
                           && (read = stream.Read(buffer, total, buffer.Length - total)) > 0)
                    {
                        total += read;
                    }
                    string body = Encoding.UTF8.GetString(buffer, 0, total);
                    return body.IndexOf("\"sessions\"", StringComparison.Ordinal) >= 0
                           && body.IndexOf("\"config\"", StringComparison.Ordinal) >= 0;
                }
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>起服务 + 起悬浮工具栏 + 等端口就绪。失败时自己弹错误框并返回 false。</summary>
    static bool StartServerAndWait()
    {
        try
        {
            OpenLog();
        }
        catch (Exception)
        {
            // 日志写不了不是致命问题，继续
        }
        LogLine("===== " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture)
                + " 启动 Study7Agent（端口 " + port + "）=====");

        try
        {
            // --no-open 不让服务端自己开浏览器标签页（界面由本程序打开）。
            // --no-float 不让服务端起悬浮工具栏（本程序自己起，退出时才收得干净）。
            // --base-dir：服务器默认按 webui\ 的上级当根目录，单文件版那是解包目录，
            //   必须显式指到 exe 旁边，否则 config\ 与 data\ 会落在会被覆盖的地方。
            // --data-dir 只在单文件版传：目录版保持 1.0 的老行为（会话在 webui\data），
            //   老用户换新版本时不会突然"看不到以前的会话"。
            // --exe-path：把**正在跑的这份** exe 的完整路径交给服务端。单文件版的文件名
            //   带着版本号（study7agent-1.1.0-beta11.exe），服务端要是死找 Study7Agent.exe
            //   就永远找不到，开机自启那条路等于从没生效过。
            string extra = " --no-open --no-float --base-dir \"" + baseDir + "\""
                + " --exe-path \"" + Application.ExecutablePath + "\"";
            if (!string.Equals(codeDir, baseDir, StringComparison.OrdinalIgnoreCase))
            {
                extra += " --data-dir \"" + Path.Combine(baseDir, "data") + "\"";
            }
            serverProcess = StartHidden(
                Path.Combine(codeDir, Path.Combine("python", "python.exe")),
                // 脚本路径必须在最前面：python 会把 --no-open 当成自己的选项直接报 unknown option
                "\"" + Path.Combine(codeDir, Path.Combine("webui", "server.py")) + "\"" + extra,
                true);
        }
        catch (Exception ex)
        {
            LogLine("python 启动失败：" + ex.Message);
            ShowStartupErrorOrLog("python 没能启动：" + ex.Message, true);
            return false;
        }

        WatchServerExit();

        // 开机自启时不弹「正在启动…」小窗：开机那一刻突然冒出个窗口很吓人，
        // 而且老师本来就没在等它，悬浮栏出来就够了。
        if (!autostartMode)
        {
            ShowSplash();
        }
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(ReadyTimeoutMs);
        bool ready = false;
        bool died = false;
        while (DateTime.UtcNow < deadline)
        {
            if (ServerAnswers(port, 1500))
            {
                ready = true;
                break;
            }
            try
            {
                if (serverProcess.HasExited)
                {
                    died = true;
                    break;
                }
            }
            catch (Exception)
            {
                // HasExited 偶尔抛异常：当作还活着，继续等
            }
            Application.DoEvents();
            Thread.Sleep(PollIntervalMs);
        }
        CloseSplash();

        if (!ready)
        {
            // 5. 超时（或服务刚起就死了）：把 logs\exe.log 的尾巴摆给老师看，再给一个「退出」。
            //    自己起的进程自己收掉，别留下一个没人管的 python 在后台占着端口。
            string what = died ? "服务刚启动就退出了。" : "等了 40 秒，服务还是没有响应。";
            KillTree(serverProcess);
            serverProcess = null;
            ShowStartupErrorOrLog(what, died);
            return false;
        }

        // 服务已经起来了：这时候它该生成的访问令牌已经落盘，重新取一次地址
        uiUrl = UiUrl(port, JsonString("access_token"));
        LogLine("服务已就绪：" + uiUrl);

        // 4. 悬浮工具栏（自己起、自己收；参数与 server.py 的 float_toolbar_command 一致）
        try
        {
            floatProcess = StartFloatToolbar();
        }
        catch (Exception ex)
        {
            LogLine("悬浮工具栏没能启动（不影响使用）：" + ex.Message);
        }
        return true;
    }

    /// <summary>无窗口地起一个子进程；redirect 为真时把它的 stdout/stderr 追加进 logs\exe.log。</summary>
    static Process StartHidden(string fileName, string arguments, bool redirect)
    {
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = fileName;
        info.Arguments = arguments;
        info.WorkingDirectory = baseDir;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;                     // ★ 不许弹黑窗口
        if (redirect)
        {
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        }
        Process process = Process.Start(info);
        if (redirect)
        {
            Pump(process.StandardOutput.BaseStream);
            Pump(process.StandardError.BaseStream);
        }
        return process;
    }

    static Process StartFloatToolbar()
    {
        string script = Path.Combine(codeDir, Path.Combine("scripts", "study7agent-float.ps1"));
        if (!File.Exists(script))
        {
            LogLine("没有 scripts\\study7agent-float.ps1，跳过悬浮工具栏。");
            return null;
        }
        string powershell = Path.Combine(
            Path.Combine(Environment.SystemDirectory, "WindowsPowerShell"),
            Path.Combine("v1.0", "powershell.exe"));
        if (!File.Exists(powershell))
        {
            powershell = "powershell.exe";
        }
        StringBuilder arguments = new StringBuilder();
        arguments.Append("-NoProfile -STA -ExecutionPolicy Bypass -File \"");
        arguments.Append(script);
        arguments.Append("\" -Api http://127.0.0.1:");
        arguments.Append(port.ToString(CultureInfo.InvariantCulture));
        arguments.Append(" -Url \"").Append(uiUrl).Append("\"");
        // PowerShell 的 -File 绑定器不接受空参数值（-Token "" 直接报错），没令牌就整个省掉
        string token = JsonString("access_token");
        if (!string.IsNullOrEmpty(token))
        {
            arguments.Append(" -Token \"").Append(token.Replace("\"", "")).Append("\"");
        }
        // 本 exe 的全路径：悬浮窗的「显示界面」在界面窗口被关掉之后靠它重新启动，
        // 第二次启动会经单实例信号把界面叫出来（老师关窗 = 收进托盘，这条是回来的路）。
        arguments.Append(" -Exe \"").Append(Application.ExecutablePath).Append("\"");
        LogLine("启动悬浮工具栏：powershell " + arguments.ToString());
        return StartHidden(powershell, arguments.ToString(), true);
    }

    /// <summary>服务自己退出（老师从工具栏/网页退出）时，本程序也收摊，不留一个空托盘图标。</summary>
    static void WatchServerExit()
    {
        Process watched = serverProcess;
        watched.EnableRaisingEvents = true;
        watched.Exited += delegate
        {
            if (stopping || restarting)
            {
                return;
            }
            Invoke(delegate
            {
                if (stopping || restarting)
                {
                    return;
                }
                stopping = true;
                LogLine("服务已退出，Study7Agent 一起关闭。");
                CleanupChildren();
                Application.Exit();
            });
        };
    }

    // ------------------------------------------------------------- 日志

    static void OpenLog()
    {
        if (logStream != null)
        {
            return;
        }
        string dir = Path.Combine(baseDir, "logs");
        if (!Directory.Exists(dir))
        {
            Directory.CreateDirectory(dir);
        }
        logStream = new FileStream(Path.Combine(dir, "exe.log"), FileMode.Append,
                                   FileAccess.Write, FileShare.ReadWrite);
    }

    static void CloseLog()
    {
        lock (logLock)
        {
            if (logStream != null)
            {
                try { logStream.Close(); }
                catch (Exception) { }
                logStream = null;
            }
        }
    }

    static void LogLine(string text)
    {
        WriteLog(Encoding.UTF8.GetBytes(text + "\r\n"));
    }

    static void WriteLog(byte[] bytes)
    {
        lock (logLock)
        {
            if (logStream == null)
            {
                return;
            }
            try
            {
                logStream.Write(bytes, 0, bytes.Length);
                logStream.Flush();
            }
            catch (Exception)
            {
                // 磁盘满了 / 文件被独占：日志不是关键路径
            }
        }
    }

    /// <summary>把子进程的一路输出原样搬进日志（python 那边已经设成 UTF-8）。</summary>
    static void Pump(Stream source)
    {
        Thread thread = new Thread(delegate ()
        {
            byte[] buffer = new byte[4096];
            try
            {
                while (true)
                {
                    int read = source.Read(buffer, 0, buffer.Length);
                    if (read <= 0)
                    {
                        break;
                    }
                    byte[] chunk = new byte[read];
                    Array.Copy(buffer, chunk, read);
                    WriteLog(chunk);
                }
            }
            catch (Exception)
            {
                // 进程被收掉时管道会断，正常
            }
        });
        thread.IsBackground = true;
        thread.Start();
    }

    /// <summary>日志尾巴：给「起不来」的错误框用。</summary>
    static string LogTail(int maxChars)
    {
        string path = Path.Combine(baseDir, Path.Combine("logs", "exe.log"));
        try
        {
            if (!File.Exists(path))
            {
                return "(还没有日志文件)";
            }
            using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                if (stream.Length == 0)
                {
                    return "(日志是空的)";
                }
                int size = (int)Math.Min(stream.Length, maxChars * 4);   // 中文一个字最多 3 字节
                stream.Seek(-size, SeekOrigin.End);
                byte[] buffer = new byte[size];
                int read = stream.Read(buffer, 0, size);
                string text = Encoding.UTF8.GetString(buffer, 0, read);
                int newline = text.IndexOf('\n');                        // 别从半个字中间开始
                if (newline >= 0 && newline + 1 < text.Length)
                {
                    text = text.Substring(newline + 1);
                }
                if (text.Length > maxChars)
                {
                    text = text.Substring(text.Length - maxChars);
                }
                return text.Trim().Length == 0 ? "(日志是空的)" : text.Trim();
            }
        }
        catch (Exception ex)
        {
            return "(读日志失败：" + ex.Message + ")";
        }
    }

    // ------------------------------------------------------- 界面 / 托盘

    static Form splash;

    static void ShowSplash()
    {
        splash = new Form();
        splash.FormBorderStyle = FormBorderStyle.None;
        splash.StartPosition = FormStartPosition.CenterScreen;
        splash.ShowInTaskbar = false;
        splash.TopMost = true;
        splash.BackColor = Color.FromArgb(32, 34, 40);
        splash.Size = new Size(420, 120);
        Label label = new Label();
        label.Text = "正在启动 Study7Agent…";
        label.ForeColor = Color.White;
        label.TextAlign = ContentAlignment.MiddleCenter;
        label.Dock = DockStyle.Fill;
        try
        {
            label.Font = new Font(SystemFonts.DefaultFont.FontFamily, 11F);
        }
        catch (Exception)
        {
            // 用默认字体
        }
        splash.Controls.Add(label);
        splash.Show();
        splash.Refresh();
        Application.DoEvents();
    }

    static void CloseSplash()
    {
        if (splash == null)
        {
            return;
        }
        try
        {
            splash.Close();
            splash.Dispose();
        }
        catch (Exception)
        {
            // 关不掉也无所谓
        }
        splash = null;
    }

    static void ShowStartupError(string what, bool died)
    {
        StringBuilder message = new StringBuilder();
        message.Append(what);
        message.Append("\r\n\r\n");
        if (died)
        {
            message.Append("（日志是空的话，通常是随包的 python 起不来：Win7 上一般缺 Microsoft Visual C++ "
                           + "2015-2022 运行库 / KB2999226，装上再双击一次即可。）\r\n\r\n");
        }
        message.Append("可以用下面的内容排查，或者把 logs\\exe.log 整个发给管理员。");

        Form form = new Form();
        form.Text = "Study7Agent 启动失败";
        form.StartPosition = FormStartPosition.CenterScreen;
        form.Size = new Size(660, 440);
        form.MinimizeBox = false;
        form.MaximizeBox = false;
        TextBox box = new TextBox();
        box.Multiline = true;
        box.ReadOnly = true;
        box.ScrollBars = ScrollBars.Both;
        box.WordWrap = false;
        box.Dock = DockStyle.Fill;
        box.Text = message.ToString() + "\r\n\r\n---- logs\\exe.log 末尾 ----\r\n" + LogTail(4000);
        Button quit = new Button();
        quit.Text = "退出";
        quit.Dock = DockStyle.Bottom;
        quit.Height = 40;
        quit.DialogResult = DialogResult.OK;
        form.Controls.Add(box);
        form.Controls.Add(quit);
        form.AcceptButton = quit;
        form.CancelButton = quit;
        form.ShowDialog();
        form.Dispose();
    }

    /// <summary>开机自启时**绝不弹模态框**：开机那一刻老师根本不在电脑前，弹窗只会把登录界面顶掉。
    /// 结论 + 日志尾巴写进 logs\exe.log，设置里的「开机自启动诊断」会把最近一条读出来。</summary>
    static void ShowStartupErrorOrLog(string what, bool died)
    {
        if (!autostartMode)
        {
            ShowStartupError(what, died);
            return;
        }
        LogLine("开机自启失败：" + what);
        if (died)
        {
            LogLine("（服务刚起来就死了：Win7 上多半是缺 Microsoft Visual C++ 2015-2022 运行库 / KB2999226，"
                    + "或者随包 python 被还原卡 / 杀毒软件拦了。）");
        }
        LogLine("---- logs\\exe.log 末尾（发给管理员排查用）----");
        LogLine(LogTail(4000));
    }

    /// <summary>开机自启时发现已经有一个实例在跑：记一笔就走，绝不把界面弹到前台。</summary>
    static void LogAutostartSkip()
    {
        try
        {
            if (string.IsNullOrEmpty(baseDir))
            {
                baseDir = Path.GetDirectoryName(Application.ExecutablePath);
            }
            if (string.IsNullOrEmpty(baseDir))
            {
                return;
            }
            OpenLog();
            LogLine("开机自启：检测到已经有一个 Study7Agent 在跑了，这次只记一笔就走"
                    + "（不打开界面窗口、不抢前台）。");
            CloseLog();
        }
        catch (Exception)
        {
            // 日志写不了也不影响：这次本来什么都不用做
        }
    }

    /// <summary>桌面好了没有：explorer 在跑，而且任务栏窗口（Shell_TrayWnd）已经建出来。</summary>
    static bool DesktopReady()
    {
        try
        {
            if (Process.GetProcessesByName("explorer").Length == 0)
            {
                return false;
            }
        }
        catch (Exception)
        {
            // 拿不到进程列表就当它好了，别为了这个把开机自启卡死
        }
        bool found = false;
        EnumWindows(delegate(IntPtr handle, IntPtr param)
        {
            if (found)
            {
                return false;
            }
            StringBuilder name = new StringBuilder(64);
            GetClassName(handle, name, name.Capacity);
            if (string.Equals(name.ToString(), "Shell_TrayWnd", StringComparison.OrdinalIgnoreCase))
            {
                found = true;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    /// <summary>等桌面就绪（最多 AutostartDesktopTimeoutMs）。等不到也继续起服务，只留一行日志。</summary>
    static void WaitForDesktopReady()
    {
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(AutostartDesktopTimeoutMs);
        int waited = 0;
        int nextNote = 10000;
        while (DateTime.UtcNow < deadline)
        {
            if (DesktopReady())
            {
                LogLine("开机自启：桌面已就绪（等了 " + waited + " 毫秒），开始起服务。");
                return;
            }
            if (waited >= nextNote)
            {
                LogLine("开机自启：还在等桌面（explorer / 任务栏）就绪，已等 "
                        + (waited / 1000) + " 秒…");
                nextNote = nextNote + 10000;
            }
            Application.DoEvents();
            Thread.Sleep(500);
            waited = waited + 500;
        }
        LogLine("开机自启：等了 " + (AutostartDesktopTimeoutMs / 1000)
                + " 秒桌面还没报「就绪」，先继续起服务（可能只是 explorer 起得慢，"
                + "或者这台机器用希沃管家之类的壳接管了桌面）。");
    }

    static void CreateTray()
    {
        tray = new NotifyIcon();
        tray.Icon = LoadAgentIcon();
        tray.Text = "Study7Agent（双击打开界面）";
        ContextMenuStrip menu = new ContextMenuStrip();
        menu.Items.Add("打开界面", null, delegate { OpenAppWindow(uiUrl, true); });
        menu.Items.Add("打开工作区", null, delegate { OpenWorkspace(); });
        menu.Items.Add("重启服务", null, delegate { RestartServer(); });
        menu.Items.Add("查看日志", null, delegate { OpenLogFile(); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("退出（连服务一起关）", null, delegate { ExitApplication(); });
        tray.ContextMenuStrip = menu;
        tray.DoubleClick += delegate { OpenAppWindow(uiUrl, true); };
        tray.Visible = true;
    }

    static Icon LoadAgentIcon()
    {
        try
        {
            string path = Path.Combine(baseDir, "agent.ico");
            if (File.Exists(path))
            {
                return new Icon(path, SystemInformation.SmallIconSize);
            }
        }
        catch (Exception)
        {
            // 图标坏了也不能不让程序起来
        }
        return SystemIcons.Application;
    }

    /// <summary>托盘菜单里的「打开工作区」：config\webui.json 里的 workspace_root。</summary>
    static void OpenWorkspace()
    {
        string path = JsonString("workspace_root");
        if (string.IsNullOrEmpty(path) || !Directory.Exists(path))
        {
            path = Path.Combine(baseDir, "workspace");
        }
        try
        {
            if (!Directory.Exists(path))
            {
                Directory.CreateDirectory(path);
            }
        }
        catch (Exception)
        {
            path = baseDir;
        }
        try
        {
            Process.Start("explorer.exe", "\"" + path + "\"");
        }
        catch (Exception ex)
        {
            MessageBox.Show("打不开工作区文件夹：\r\n\r\n" + path + "\r\n\r\n" + ex.Message,
                            "Study7Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    static void OpenLogFile()
    {
        string path = Path.Combine(baseDir, Path.Combine("logs", "exe.log"));
        if (!File.Exists(path))
        {
            MessageBox.Show("还没有日志文件：\r\n\r\n" + path, "Study7Agent",
                            MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        try
        {
            Process.Start(path);
        }
        catch (Exception)
        {
            try
            {
                Process.Start("notepad.exe", "\"" + path + "\"");
            }
            catch (Exception ex)
            {
                MessageBox.Show("打不开日志：" + ex.Message, "Study7Agent",
                                MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }
    }

    static void RestartServer()
    {
        if (serverProcess == null)
        {
            MessageBox.Show("当前这个服务不是 Study7Agent.exe 启动的（可能是 start-webui.cmd 或别的窗口起的），"
                            + "从这里没法重启。\r\n\r\n"
                            + "请先把原来的窗口关掉，再双击一次 Study7Agent.exe。",
                            "Study7Agent", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        restarting = true;
        LogLine("托盘菜单：重启服务");
        KillTree(serverProcess);
        KillTree(floatProcess);
        serverProcess = null;
        floatProcess = null;
        bool ok = StartServerAndWait();
        restarting = false;
        if (ok)
        {
            // 老师刚从托盘点了「重启服务」：界面得给他叫回来（force）
            OpenAppWindow(uiUrl, true);
        }
    }

    // ------------------------------------------------------------ 浏览器

    /// <summary>6. 应用窗口：先找已经在跑的界面窗口，没有再用浏览器开一个独立的「应用窗口」。
    ///
    /// force = 老师**明确要"把界面叫出来"**（再双击一次 exe、点托盘「打开界面」、
    /// 重启服务之后）。这时候绝不能再按"首次运行先等它"那条规则一直等：
    /// 界面窗口已经没了（被关掉 / 浏览器崩了）还等，老师那边就是"双击了没反应、
    /// 网页打不开"——真机反馈过。
    ///
    /// 这条路也是「关掉窗口 != 退出程序」的另一半：窗口关掉之后服务与托盘都还在，
    /// 老师从托盘或悬浮窗再点一次，就靠这里把界面重新开出来（**同一个 profile**，
    /// 所以还是那个应用窗口，不是新标签页）。</summary>
    static void OpenAppWindow(string url, bool force)
    {
        MarkWindowOpen();
        IntPtr existing = FindAppWindow();
        if (existing != IntPtr.Zero)
        {
            LogLine("界面窗口已经在了，切到前台。");
            ShowWindow(existing, SW_RESTORE);
            BringWindowToTop(existing);
            SetForegroundWindow(existing);
            return;
        }
        // 1.1 起：先用**随包的内置内核**（Chromium 109，Win7 上最后一个能用的版本）。
        // 找不到它（半个包、被删了）才退回老师在用的浏览器 —— 那条老路一个字没改。
        string kernel = FindKernel();
        string browser = kernel != null ? kernel : FindBrowser();
        if (browser != null)
        {
            // 第一次运行时浏览器要先建一份自己的 profile（本机实测要十几秒），
            // 这期间界面窗口还不存在。老师等不及会连点几次 —— 不能每次都再开一个窗口，
            // 否则一口气叠出三个界面。自己那个浏览器进程还活着就先等它。
            Process launched = browserProcess;
            double since = launched == null ? 999.0
                                            : (DateTime.UtcNow - browserStartedAt).TotalSeconds;
            bool warm = launched != null && !HasExited(launched);
            if (warm && since < (force ? 15.0 : 120.0))
            {
                LogLine("界面窗口还没出来（首次运行要先建 browser-profile，会慢一些），先等它，不再重复开。");
                return;
            }
            // 内置内核用自己的 profile 目录：老师的机器上以前可能用 360/Chrome 跑过 1.0，
            // 那些浏览器写的 profile 版本比 109 新，109 打开会报"配置来自更新的版本"。
            string profile = Path.Combine(baseDir, kernel != null ? "kernel-profile" : "browser-profile");
            try
            {
                if (!Directory.Exists(profile))
                {
                    Directory.CreateDirectory(profile);
                }
            }
            catch (Exception ex)
            {
                LogLine("建 browser-profile 目录失败（继续用默认位置）：" + ex.Message);
            }
            // 独立的浏览器配置目录：这个窗口完全属于本程序，
            //   * 不动老师自己的浏览器会话 / 历史 / 登录状态，关掉它也不影响老师其它网页
            //   * 是独立的一项任务栏窗口，不会变成老师浏览器里的一个标签页
            //   * --app = 没有标签栏、没有地址栏的应用窗口；万一某个 360 版本不认 --app，
            //     后面的 --new-window 至少保证它是一个独立窗口，而不是别人窗口里的标签页
            //   * --disable-renderer-backgrounding / --disable-backgrounding-occluded-windows：
            //     网页被遮住或切到后台时浏览器会冻结页面定时器，而网页 2 秒一次的心跳一停，
            //     悬浮工具栏就以为"网页关了"，把服务和它自己一起收掉（老师报过的"只剩空网页"）。
            // 走到这里说明**没有任何可见的界面窗口**（上面 FindAppWindow 已经找过）。
            // 但内核进程可能还在后台赖着（窗口关了进程没退），它占着同一个 profile ——
            // 这时直接再启一个，新的这次会被交接给那个僵尸进程，老师看到的就是一个**黑屏窗口**。
            // 所以先把没窗口的内核进程收掉，再干净地开。
            KillWindowlessKernel();
            string arguments = "--app=" + url
                + " --new-window"
                + " --user-data-dir=\"" + profile + "\""
                + " --no-first-run --no-default-browser-check"
                + " --disable-features=Translate"
                + " --disable-renderer-backgrounding --disable-backgrounding-occluded-windows";
            if (kernel != null)
            {
                // 教室机器常年断网：别让内核在后台偷偷连（组件更新、同步、后台网络）
                arguments += " --start-maximized --noerrdialogs"
                    + " --disable-background-networking --disable-component-update"
                    + " --disable-sync --disable-default-apps";
                if (gpuDisabled)
                {
                    // 老 Intel 驱动上内核可能起来就崩；compat.ini 里 gpu=0 或者自动重试都会走到这
                    arguments += " --disable-gpu";
                }
                if (kernelRetry >= 2)
                {
                    // 最后一档。实测在用户目录下跑内核时，GPU 进程会 STATUS_DLL_INIT_FAILED
                    // 连崩三次然后整个浏览器 FATAL；--disable-gpu / --in-process-gpu /
                    // --disable-gpu-sandbox 都救不回来，只有它能跑。安全上是让步（渲染进程
                    // 不再沙箱化），所以只在前面几档都失败后才自动升到这一档，并且每次都记日志；
                    // 界面只加载本机 127.0.0.1 上的自家页面，不加载外部内容。
                    arguments += " --no-sandbox";
                    LogLine("警告：内核已升到 --no-sandbox（关闭渲染进程沙箱）才能启动");
                }
                if (!string.IsNullOrEmpty(debugPort))
                {
                    arguments += " --remote-debugging-port=" + debugPort;
                }
            }
            try
            {
                ProcessStartInfo info = new ProcessStartInfo(browser, arguments);
                info.UseShellExecute = true;          // 用 shell 起，起完就走，不等它退出
                // 只记第一个还活着的实例：同一个 profile 的第二次启动会"交接"给已经在跑的那个
                // 然后自己退出，记下它等于把真正那个浏览器弄丢（退出时就关不掉窗口了）。
                Process current = browserProcess;
                Process started = Process.Start(info);
                if (current == null || HasExited(current))
                {
                    browserProcess = started;
                    browserStartedAt = DateTime.UtcNow;
                    if (kernel != null)
                    {
                        ArmKernelRetry(started, url);
                    }
                    // 老师自己点右上角 X 关窗时这个进程也会退出：那**不是**崩溃，是"收进托盘"
                    WatchAppWindowExit(started);
                }
                LogLine("打开界面：" + browser + " " + arguments);
                return;
            }
            catch (Exception ex)
            {
                LogLine("用 --app 打开界面失败（" + browser + "）：" + ex.Message);
            }
        }
        else
        {
            LogLine("没找到 Chromium 内核的浏览器，退回默认浏览器（你的机器上是 360 之类的默认浏览器）。"
                    + "要让它用随包内核，请确认解压出来的目录里有 chromium-109（或者直接在 exe 那一层运行）。");
        }
        try
        {
            Process.Start(url);               // 兜底：默认浏览器（这时就是一个普通标签页了）
        }
        catch (Exception ex)
        {
            MessageBox.Show("打不开界面，请手动访问：\r\n\r\n" + url + "\r\n\r\n" + ex.Message,
                            "Study7Agent", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    /// <summary>随包的内置内核（Chromium 109）。找不到返回 null，调用方会退回老师的浏览器。</summary>
    static string FindKernel()
    {
        string[] roots = new string[]
        {
            Path.Combine(codeDir, "chromium-109"),
            Path.Combine(codeDir, "chromium"),
            Path.Combine(codeDir, Path.Combine("vendor", "chromium-109"))
        };
        foreach (string root in roots)
        {
            string found = FindKernelIn(root, 0);
            if (found != null)
            {
                LogLine("使用内置内核：" + found);
                return found;
            }
        }
        // 找不到要说清楚找过哪儿：老师那台如果显示"退回了默认浏览器"，看这行就知道少什么
        LogLine("没找到内置内核，找过这些目录：" + string.Join("；", roots));
        return null;
    }

    static string FindKernelIn(string folder, int depth)
    {
        if (depth > 3 || !Directory.Exists(folder))
        {
            return null;
        }
        string[] names = new string[] { "chrome.exe", "chromium.exe" };
        foreach (string name in names)
        {
            string direct = Path.Combine(folder, name);
            if (File.Exists(direct))
            {
                return direct;
            }
        }
        foreach (string child in Directory.GetDirectories(folder))
        {
            string found = FindKernelIn(child, depth + 1);
            if (found != null)
            {
                return found;
            }
        }
        return null;
    }

    /// <summary>360 极速 → 360 安全 → Chrome → Edge → 注册表 App Paths → 目录里扫一遍。</summary>
    static string FindBrowser()
    {
        string[] relative = new string[]
        {
            @"360Chrome\Chrome\Application\360chrome.exe",
            @"360se6\Application\360se.exe",
            @"Google\Chrome\Application\chrome.exe",
            @"Microsoft\Edge\Application\msedge.exe"
        };
        string[] roots = new string[]
        {
            // SpecialFolder.ProgramFilesX86 是 .NET 4.0 才有的枚举值，3.5 上只能用环境变量
            Environment.GetEnvironmentVariable("ProgramFiles(x86)"),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            // ★ 学校那台机器的 360se 装在 **Roaming** 下
            //   （C:\Users\<用户>\AppData\Roaming\360se6\Application\360se.exe），
            //   漏了这条就会找不到 360，退化成"默认浏览器开个标签页"。真机实测踩到。
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData)
        };
        foreach (string name in relative)
        {
            foreach (string root in roots)
            {
                if (string.IsNullOrEmpty(root))
                {
                    continue;
                }
                string path = Path.Combine(root, name);
                if (File.Exists(path))
                {
                    return path;
                }
            }
        }
        // 装在别处也能找到：注册表里各浏览器自己登记的 App Paths
        foreach (string name in new string[] { "360chrome.exe", "360se.exe", "chrome.exe", "msedge.exe" })
        {
            string path = AppPath(name);
            if (path != null)
            {
                return path;
            }
        }
        // 最后：在 LOCALAPPDATA / Program Files (x86) 的第一层目录里找一份 Chromium
        foreach (string root in roots)
        {
            if (string.IsNullOrEmpty(root) || !Directory.Exists(root))
            {
                continue;
            }
            string[] found;
            try
            {
                found = Directory.GetDirectories(root);
            }
            catch (Exception)
            {
                continue;
            }
            foreach (string dir in found)
            {
                foreach (string candidate in new string[]
                {
                    Path.Combine(dir, @"Chrome\Application\chrome.exe"),
                    Path.Combine(dir, @"Application\chrome.exe"),
                    Path.Combine(dir, @"Application\msedge.exe"),
                    Path.Combine(dir, @"Application\360chrome.exe"),
                    Path.Combine(dir, @"Application\360se.exe")
                })
                {
                    if (File.Exists(candidate))
                    {
                        return candidate;
                    }
                }
            }
        }
        return null;
    }

    static string AppPath(string exeName)
    {
        string[] keys = new string[]
        {
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\" + exeName,
            @"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\" + exeName
        };
        foreach (string key in keys)
        {
            try
            {
                using (RegistryKey item = Registry.LocalMachine.OpenSubKey(key))
                {
                    if (item == null)
                    {
                        continue;
                    }
                    string path = item.GetValue("") as string;
                    if (!string.IsNullOrEmpty(path) && File.Exists(path))
                    {
                        return path;
                    }
                }
            }
            catch (Exception)
            {
                // 没权限读注册表就跳过
            }
        }
        return null;
    }

    // ------------------------------------------------------------- 退出

    static void ExitApplication()
    {
        stopping = true;
        LogLine("托盘菜单：退出（连服务一起关）");
        CleanupChildren();
        Application.Exit();
    }

    /// <summary>8. 只收自己拉起来的进程树；老师自己的浏览器、资源管理器一律不碰。</summary>
    static void CleanupChildren()
    {
        if (tray != null)
        {
            tray.Visible = false;
        }
        // 界面窗口是我们自己用独立 profile 开的浏览器实例，可以放心关；
        // 老师的浏览器是别的进程，这里绝不会碰（browserProcess 为空就什么都不做）。
        KillTree(browserProcess);
        browserProcess = null;
        KillTree(floatProcess);
        KillTree(serverProcess);
        floatProcess = null;
        serverProcess = null;
    }

    /// <summary>进程退出没有（HasExited 偶尔抛异常，宁可当作"还活着"）</summary>
    static bool HasExited(Process process)
    {
        if (process == null)
        {
            return true;
        }
        try
        {
            return process.HasExited;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>.NET 3.5 没有 Process.Kill(true)，整棵进程树只能交给 taskkill /T。</summary>
    static void KillTree(Process process)
    {
        if (process == null)
        {
            return;
        }
        if (HasExited(process))
        {
            return;
        }
        int pid;
        try
        {
            pid = process.Id;
        }
        catch (Exception)
        {
            return;
        }
        try
        {
            string taskkill = Path.Combine(Environment.SystemDirectory, "taskkill.exe");
            ProcessStartInfo info = new ProcessStartInfo(taskkill,
                "/PID " + pid.ToString(CultureInfo.InvariantCulture) + " /T /F");
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            Process killer = Process.Start(info);
            string ignore = killer.StandardOutput.ReadToEnd() + killer.StandardError.ReadToEnd();
            killer.WaitForExit(5000);
            killer.Dispose();
            if (ignore.Length > 0)
            {
                LogLine("taskkill /PID " + pid + "：" + ignore.Trim());
            }
        }
        catch (Exception ex)
        {
            LogLine("收进程 " + pid + " 失败：" + ex.Message);
        }
    }

    // ------------------------------------------------------- Win32（找窗口）
    const int SW_RESTORE = 9;

    delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int maxCount);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    static extern int GetWindowTextLength(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    static extern int GetClassName(IntPtr hWnd, StringBuilder name, int maxCount);

    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);

    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr hWnd, int command);

    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    static extern bool BringWindowToTop(IntPtr hWnd);

    /// <summary>界面窗口 = 标题里有 Study7Agent 的 Chromium 窗口（Chrome / Edge / 360 极速同一套类名）。
    /// 有多个时优先选**我们自己起的那个浏览器实例**的窗口，免得认错老师浏览器里的标签页。</summary>
    /// <summary>进程名（"chrome"/"msedge"/…）；拿不到返回空串。不碰 MainModule，跨位数也读得到。</summary>
    static string ProcessNameOf(uint pid)
    {
        try
        {
            return Process.GetProcessById((int)pid).ProcessName ?? "";
        }
        catch (Exception)
        {
            return "";
        }
    }

    /// <summary>这个窗口是不是**随包内核**的：exe 路径带 chromium-109，或进程名就是 chrome。</summary>
    static bool IsKernelWindow(string path, string processName)
    {
        // 先把"肯定不是我们的"挡掉：老师自己的 Edge / 360 / 火狐 / IE 打开同一个页面时，
        // 窗口类与标题都和我们一样，只有进程名不同。不挡的话就会把他的浏览器切到前台。
        string[] foreign = { "msedge", "360se", "360chrome", "360se6", "iexplore",
                             "firefox", "sogouexplorer", "chrome_proxy" };
        for (int index = 0; index < foreign.Length; index++)
        {
            if (string.Equals(processName, foreign[index], StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
        }
        if (!string.IsNullOrEmpty(path) &&
            path.IndexOf("chromium-109", StringComparison.OrdinalIgnoreCase) >= 0)
        {
            return true;
        }
        return string.Equals(processName, "chrome", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// 把"没有任何可见窗口、却还占着 profile 的内核进程"收掉。
    ///
    /// 为什么必须收：窗口关掉之后 Chromium 进程有时会在后台赖一会儿（还有后台任务/子进程），
    /// 这时再启动一次内核，新的这次会被**交接给那个僵尸进程** —— 老师看到的就是
    /// "打开界面出来一个黑屏的 chrome 窗口"。所以在**确认没有任何可见内核窗口**之后
    /// （调用方已经找过了），把这些没窗口的内核进程先结束掉，再干净地开一个。
    /// 只杀 exe 路径里带 chromium-109 的进程，绝不碰老师自己的浏览器。
    /// </summary>
    static void KillWindowlessKernel()
    {
        int killed = 0;
        try
        {
            foreach (Process process in Process.GetProcessesByName("chrome"))
            {
                try
                {
                    string path = process.MainModule == null ? "" : (process.MainModule.FileName ?? "");
                    if (path.IndexOf("chromium-109", StringComparison.OrdinalIgnoreCase) < 0)
                    {
                        continue;
                    }
                    if (process.MainWindowHandle != IntPtr.Zero && IsWindowVisible(process.MainWindowHandle))
                    {
                        continue;             // 还有可见窗口：别动它
                    }
                    process.Kill();
                    killed++;
                }
                catch (Exception)
                {
                    // 单个进程读不到/杀不掉都不是致命问题，继续下一个
                }
            }
        }
        catch (Exception)
        {
            return;
        }
        if (killed > 0)
        {
            LogLine("清掉了 " + killed + " 个没有窗口的内核进程（它们占着 profile，会让新窗口黑屏）");
        }
    }

    /// <summary>
    /// 界面窗口的进程 exe 路径（拿不到就返回空串）。判"这个窗口是不是我们的内核"用。
    /// </summary>
    static string ProcessPathOf(uint pid)
    {
        try
        {
            Process process = Process.GetProcessById((int)pid);
            return process.MainModule == null ? "" : (process.MainModule.FileName ?? "");
        }
        catch (Exception)
        {
            return "";                        // 权限/32-64 位差异都可能抛：拿不到就当"不是我们的"
        }
    }

    /// <summary>
    /// 找我们的界面窗口：**只认随包内核**（进程 exe 路径里带 chromium-109）。
    ///
    /// 以前是"窗口类像 Chrome + 标题里含 Study7Agent"就算 —— 老师自己的 Edge/Chrome/360
    /// 打开同一个页面时标题一模一样，于是被当成界面窗口：点「显示界面」/「打开界面」
    /// 把他的浏览器切到前台（老师反馈"打开前台已经开着的浏览器"），托盘那条路也一样；
    /// 而真窗口认不出来的时候又会再去开一个内核窗口（老师看到的"莫名其妙的黑屏内核窗口"）。
    /// 现在：内核窗口最优先；其次是我们自己起的那个浏览器进程；两个都没有时，
    /// **只有在我们没有随包内核**（目录版用老师的浏览器）才接受"标题像"的窗口。
    /// </summary>
    static IntPtr FindAppWindow()
    {
        int ownPid = 0;
        Process browser = browserProcess;
        if (browser != null && !HasExited(browser))
        {
            try
            {
                ownPid = browser.Id;
            }
            catch (Exception)
            {
                ownPid = 0;
            }
        }
        IntPtr mine = IntPtr.Zero;
        IntPtr other = IntPtr.Zero;
        IntPtr kernelWindow = IntPtr.Zero;
        EnumWindows(delegate (IntPtr hWnd, IntPtr lParam)
        {
            if (!IsWindowVisible(hWnd))
            {
                return true;
            }
            int length = GetWindowTextLength(hWnd);
            if (length <= 0 || length > 512)
            {
                return true;
            }
            StringBuilder title = new StringBuilder(length + 2);
            GetWindowText(hWnd, title, title.Capacity);
            if (title.ToString().IndexOf("Study7Agent", StringComparison.OrdinalIgnoreCase) < 0)
            {
                return true;
            }
            StringBuilder className = new StringBuilder(256);
            GetClassName(hWnd, className, className.Capacity);
            if (!className.ToString().StartsWith("Chrome_WidgetWin", StringComparison.Ordinal))
            {
                return true;
            }
            uint pid;
            GetWindowThreadProcessId(hWnd, out pid);
            string path = ProcessPathOf(pid);
            // 认内核看两样：exe 路径里带 chromium-109（最准），或者进程名就是 chrome。
            // 为什么要退到进程名：读别的进程的 exe 路径在某些情况下会失败（权限、位数差异），
            // 那时 path 是空串 —— 只按路径判就会认不出自己的内核窗口，于是又去开一个新的黑屏窗口。
            // 老师的机器上浏览器是 Edge（进程名 msedge），所以"进程名 chrome"不会误认他的浏览器。
            if (IsKernelWindow(path, ProcessNameOf(pid)))
            {
                kernelWindow = hWnd;          // 随包内核的窗口：就是它，别再看别的
                return false;
            }
            if (ownPid != 0 && (int)pid == ownPid)
            {
                mine = hWnd;
                return true;
            }
            if (other == IntPtr.Zero)
            {
                other = hWnd;                 // 不是自己的：先记下来，实在没有再用
            }
            return true;
        }, IntPtr.Zero);
        if (kernelWindow != IntPtr.Zero)
        {
            return kernelWindow;
        }
        if (mine != IntPtr.Zero)
        {
            return mine;
        }
        // 没有随包内核时（目录版走老师的浏览器）才认"标题像"的那个窗口
        return FindKernel() == null ? other : IntPtr.Zero;
    }
}

/// <summary>
/// 单文件版首次启动时的解包进度窗。只在解包那几十秒里出现，解完就关。
/// 用普通窗体（可激活、在任务栏里露脸）：老师双击之后必须马上看到"它在干活"，
/// 否则会以为没反应，接着连点好几次。
/// </summary>
sealed class ProgressForm : Form
{
    readonly ProgressBar bar = new ProgressBar();
    readonly Label caption = new Label();

    public ProgressForm(string stamp)
    {
        Text = "Study7Agent 首次启动";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        StartPosition = FormStartPosition.CenterScreen;
        MaximizeBox = false;
        MinimizeBox = false;
        ClientSize = new Size(430, 116);
        caption.Text = "正在准备运行环境（" + stamp + "），第一次启动要等一会儿……";
        caption.SetBounds(16, 18, 400, 36);
        bar.SetBounds(16, 60, 398, 20);
        bar.Minimum = 0;
        bar.Maximum = 1000;
        Controls.Add(caption);
        Controls.Add(bar);
    }

    public void Report(long current, long total)
    {
        if (!IsHandleCreated || IsDisposed)
        {
            return;
        }
        int value = total > 0 ? (int)Math.Min(1000, current * 1000 / total) : 0;
        try
        {
            BeginInvoke((MethodInvoker)delegate { bar.Value = value; });
        }
        catch (Exception)
        {
            // 窗体已经关了：进度丢了不影响解包
        }
    }
}

/// <summary>
/// 只用来跑消息循环、顺便当跨线程调度的宿主（界面本体是浏览器的 --app 窗口）。
/// WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW：它永远不会被激活、也不会出现在 Alt+Tab 里——
/// 否则老师最小化界面窗口时，前台会莫名其妙落到这个 1×1 的隐形窗口上。
/// </summary>
sealed class PumpForm : Form
{
    protected override CreateParams CreateParams
    {
        get
        {
            CreateParams parameters = base.CreateParams;
            parameters.ExStyle |= 0x08000000;      // WS_EX_NOACTIVATE
            parameters.ExStyle |= 0x00000080;      // WS_EX_TOOLWINDOW
            return parameters;
        }
    }
}
