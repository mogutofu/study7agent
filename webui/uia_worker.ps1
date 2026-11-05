# Resident UI Automation worker for Study7Agent.
#
# WHY A RESIDENT PROCESS
#   The old desktop layer started a fresh powershell.exe for every call: ~0.5 s of
#   pure process+compile overhead before any UIA work began, and every element
#   property was a separate cross-process COM call. Measured on the dev machine:
#   new process per call = 544 ms, resident round trip = 0.13 ms.
#
# PROTOCOL (one line in, one line out; the line separator is LF)
#   request : <seq> US <command> US <arg> US <arg> ...
#   reply   : <seq> US ok  US <meta> US <rows>
#             <seq> US err US <message>
#   US = 0x1f (unit separator). Meta is "k=v" pairs joined by US; rows are joined
#   by RS (0x1e) and their fields by US. Anything that would break the line
#   (CR/LF/TAB/US/RS) is replaced by a space inside text fields.
#
# The C# below is compiled once per worker start (Add-Type), which is the whole
# point: after that, every command is a pipe write plus the UIA work itself.
#
# Keep this file ASCII-only: PowerShell 2.0 decodes BOM-less .ps1 files as ANSI,
# so non-ASCII comments would come out mangled on the target machine.

$ErrorActionPreference = 'Continue'

try {
  [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
  [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch { }

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase

$W7A_CS = @'
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Windows;
using System.Windows.Automation;

namespace W7A
{
    public class Uia
    {
        const char US = '\u001f';
        const char RS = '\u001e';

        // Control types that can be acted on. Programmatic names, so they are the
        // same on every Windows language (LocalizedControlType is not).
        static readonly string[] INTERACTIVE = new string[] {
            "Button", "ListItem", "MenuItem", "Edit", "CheckBox", "RadioButton",
            "ComboBox", "Hyperlink", "SplitButton", "TabItem", "TreeItem",
            "DataItem", "HeaderItem", "Spinner", "Slider", "ScrollBar", "Document"
        };
        // Named containers worth showing as context.
        static readonly string[] STRUCTURAL = new string[] {
            "Pane", "Group", "Custom", "ToolBar", "Tab", "MenuBar"
        };
        // Read-only text-ish nodes.
        static readonly string[] INFORMATIVE = new string[] {
            "Text", "Image", "StatusBar"
        };
        // Control types whose ValuePattern carries the useful text.
        static readonly string[] VALUETYPES = new string[] {
            "Edit", "Document", "ComboBox", "Spinner", "Slider", "Text"
        };
        static readonly string[] TOGGLETYPES = new string[] {
            "CheckBox", "RadioButton", "MenuItem", "Button"
        };
        static readonly string[] EXPANDTYPES = new string[] {
            "ComboBox", "TreeItem", "MenuItem", "SplitButton", "Button"
        };
        // Containers we bother asking for a ScrollPattern.
        static readonly string[] SCROLLTYPES = new string[] {
            "Pane", "Group", "Custom", "Document", "List", "Tree", "DataGrid",
            "Table", "Window", "Menu", "Edit"
        };

        // Document TextPattern is the one call we cannot bound from outside, and
        // Chromium's provider is pathologically slow at it (measured: >10 s for a
        // 2000-char request on a busy page). Ask for a little, and stop asking once
        // the read's budget is gone.
        const int DOC_TEXT_MAX = 600;
        const int DOC_BUDGET_MS = 6000;

        // ------------------------------------------------------------------ helpers

        public static string Arg(string[] parts, int index)
        {
            if (parts == null || index < 0 || index >= parts.Length) return "";
            string value = parts[index];
            return value == null ? "" : value;
        }

        static int Int(string text, int fallback)
        {
            if (text == null) return fallback;
            int value;
            if (int.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out value)) return value;
            return fallback;
        }

        static int ToInt(object raw)
        {
            if (raw == null) return 0;
            try { return Convert.ToInt32(raw, CultureInfo.InvariantCulture); }
            catch { return 0; }
        }

        static double Dbl(string text, double fallback)
        {
            if (text == null) return fallback;
            double value;
            if (double.TryParse(text.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out value)) return value;
            return fallback;
        }

        static bool Bool(string text, bool fallback)
        {
            if (text == null) return fallback;
            string value = text.Trim().ToLowerInvariant();
            if (value == "" ) return fallback;
            if (value == "1" || value == "true" || value == "yes") return true;
            if (value == "0" || value == "false" || value == "no") return false;
            return fallback;
        }

        static bool Has(string[] set, string value)
        {
            if (value == null) return false;
            for (int i = 0; i < set.Length; i++)
            {
                if (string.Equals(set[i], value, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }

        static string S(int value)
        {
            return value.ToString(CultureInfo.InvariantCulture);
        }

        static string S(double value)
        {
            return Math.Round(value, 2).ToString(CultureInfo.InvariantCulture);
        }

        // Text that goes on the wire: no line breaks, no separators, no private-use
        // glyphs, trimmed and capped.
        static string Clean(object raw, int limit)
        {
            if (raw == null) return "";
            string text = raw as string;
            if (text == null)
            {
                // UIA sometimes hands back a COM object (Chromium AcceleratorKey);
                // Convert.ToString would yield "System.__ComObject" noise. Keep it empty.
                Type rawType = raw.GetType();
                if (!rawType.IsPrimitive && !(raw is decimal) && !(raw is DateTime)) return "";
                try { text = Convert.ToString(raw, CultureInfo.InvariantCulture); }
                catch { return ""; }
            }
            if (text == null) return "";
            StringBuilder sb = new StringBuilder(text.Length);
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '\r' || c == '\n' || c == '\t' || c == US || c == RS || c == '|')
                {
                    sb.Append(' ');
                    continue;
                }
                if (c >= '\uE000' && c <= '\uF8FF') continue;
                if (char.IsSurrogate(c))
                {
                    // Keep well-formed pairs, drop lone surrogates.
                    if (char.IsHighSurrogate(c) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
                    {
                        sb.Append(c);
                        sb.Append(text[i + 1]);
                        i++;
                    }
                    continue;
                }
                if (char.IsControl(c)) { sb.Append(' '); continue; }
                sb.Append(c);
            }
            text = sb.ToString().Trim();
            while (text.IndexOf("  ") >= 0) text = text.Replace("  ", " ");
            if (limit > 0 && text.Length > limit) text = text.Substring(0, limit);
            return text;
        }

        static AutomationElementCollection Kids(AutomationElement element, CacheRequest request)
        {
            // One cross-process round trip returns every child WITH its properties
            // (and the patterns named in the request) already cached.
            CacheRequest clone = request.Clone();
            clone.TreeScope = TreeScope.Children;
            AutomationElement updated = element.GetUpdatedCache(clone);
            if (updated == null) return null;
            return updated.CachedChildren;
        }

        static object Prop(AutomationElement element, AutomationProperty property)
        {
            try { return element.GetCachedPropertyValue(property, true); }
            catch { return null; }
        }

        static object LiveProp(AutomationElement element, AutomationProperty property)
        {
            try { return element.GetCurrentPropertyValue(property, true); }
            catch { return null; }
        }

        static T Pattern<T>(AutomationElement element, AutomationPattern pattern, bool live) where T : class
        {
            try
            {
                object obj = null;
                if (live)
                {
                    if (!element.TryGetCurrentPattern(pattern, out obj)) return null;
                }
                else
                {
                    obj = element.GetCachedPattern(pattern);
                }
                return obj as T;
            }
            catch { return null; }
        }

        static string Canon(object raw)
        {
            // ControlTypeProperty is NOT a string in the managed API: it comes back
            // as a ControlType object whose ProgrammaticName is "ControlType.Button".
            ControlType controlType = raw as ControlType;
            string text = controlType != null ? controlType.ProgrammaticName : raw as string;
            if (text == null) return "";
            if (text.StartsWith("ControlType.", StringComparison.Ordinal))
            {
                text = text.Substring("ControlType.".Length);
            }
            return text;
        }

        static bool RectOf(AutomationElement element, out int left, out int top, out int width, out int height)
        {
            left = 0; top = 0; width = 0; height = 0;
            object raw = Prop(element, AutomationElement.BoundingRectangleProperty);
            if (raw == null) return false;
            if (!(raw is Rect)) return false;
            Rect rect = (Rect)raw;
            if (rect.IsEmpty) return false;
            double x = rect.X, y = rect.Y, w = rect.Width, h = rect.Height;
            if (double.IsNaN(x) || double.IsNaN(y) || double.IsInfinity(x) || double.IsInfinity(y)) return false;
            if (double.IsNaN(w) || double.IsNaN(h) || double.IsInfinity(w) || double.IsInfinity(h)) return false;
            left = (int)Math.Round(x);
            top = (int)Math.Round(y);
            width = (int)Math.Round(w);
            height = (int)Math.Round(h);
            return true;
        }

        static bool LiveRectOf(AutomationElement element, out int left, out int top, out int width, out int height)
        {
            left = 0; top = 0; width = 0; height = 0;
            object raw = LiveProp(element, AutomationElement.BoundingRectangleProperty);
            if (raw == null || !(raw is Rect)) return false;
            Rect rect = (Rect)raw;
            if (rect.IsEmpty) return false;
            double x = rect.X, y = rect.Y, w = rect.Width, h = rect.Height;
            if (double.IsNaN(x) || double.IsNaN(y) || double.IsInfinity(x) || double.IsInfinity(y)) return false;
            left = (int)Math.Round(x);
            top = (int)Math.Round(y);
            width = (int)Math.Round(w);
            height = (int)Math.Round(h);
            return true;
        }

        static string RidKey(AutomationElement element)
        {
            object raw = Prop(element, AutomationElement.RuntimeIdProperty);
            int[] rid = raw as int[];
            if (rid == null || rid.Length == 0) return null;
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < rid.Length; i++)
            {
                if (i > 0) sb.Append('.');
                sb.Append(rid[i].ToString(CultureInfo.InvariantCulture));
            }
            return sb.ToString();
        }

        static CacheRequest MakeRequest()
        {
            CacheRequest request = new CacheRequest();
            request.TreeScope = TreeScope.Element | TreeScope.Children;
            request.Add(AutomationElement.NameProperty);
            request.Add(AutomationElement.ControlTypeProperty);
            request.Add(AutomationElement.LocalizedControlTypeProperty);
            request.Add(AutomationElement.AutomationIdProperty);
            request.Add(AutomationElement.ClassNameProperty);
            request.Add(AutomationElement.BoundingRectangleProperty);
            request.Add(AutomationElement.IsEnabledProperty);
            request.Add(AutomationElement.IsOffscreenProperty);
            request.Add(AutomationElement.IsControlElementProperty);
            request.Add(AutomationElement.IsKeyboardFocusableProperty);
            request.Add(AutomationElement.HasKeyboardFocusProperty);
            request.Add(AutomationElement.IsPasswordProperty);
            request.Add(AutomationElement.NativeWindowHandleProperty);
            request.Add(AutomationElement.ProcessIdProperty);
            request.Add(AutomationElement.RuntimeIdProperty);
            request.Add(AutomationElement.HelpTextProperty);
            request.Add(AutomationElement.AcceleratorKeyProperty);
            // Patterns: cached once per parent batch instead of per node.
            request.Add(ValuePattern.Pattern);
            request.Add(RangeValuePattern.Pattern);
            request.Add(TogglePattern.Pattern);
            request.Add(ExpandCollapsePattern.Pattern);
            request.Add(ScrollPattern.Pattern);
            request.Add(SelectionItemPattern.Pattern);
            request.Add(TextPattern.Pattern);
            return request;
        }

        static void AddRow(StringBuilder body, string row)
        {
            if (body.Length > 0) body.Append(RS);
            body.Append(row);
        }

        static string Row(params string[] fields)
        {
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < fields.Length; i++)
            {
                if (i > 0) sb.Append(US);
                sb.Append(fields[i] == null ? "" : fields[i]);
            }
            return sb.ToString();
        }

        static bool MatchWindow(string selector, string title, int hwnd, int pid)
        {
            if (selector == null || selector.Length == 0) return true;
            if (selector == "desktop") return true;
            if (selector.StartsWith("hwnd:", StringComparison.OrdinalIgnoreCase))
            {
                return S(hwnd) == selector.Substring(5).Trim();
            }
            if (selector.StartsWith("pid:", StringComparison.OrdinalIgnoreCase))
            {
                return S(pid) == selector.Substring(4).Trim();
            }
            if (selector.StartsWith("name:", StringComparison.OrdinalIgnoreCase))
            {
                return title.ToLowerInvariant().IndexOf(selector.Substring(5).Trim().ToLowerInvariant()) >= 0;
            }
            return title.ToLowerInvariant().IndexOf(selector.Trim().ToLowerInvariant()) >= 0;
        }

        // ------------------------------------------------------------------ tree

        public static string Ping()
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("pid=").Append(S(System.Diagnostics.Process.GetCurrentProcess().Id));
            sb.Append(US).Append("dotnet=").Append(Clean(Environment.Version.ToString(), 40));
            sb.Append(US).Append("os=").Append(Clean(Environment.OSVersion.VersionString, 80));
            sb.Append(US).Append("uia=1");
            return sb.ToString() + RS;
        }

        public static string Tree(string windowSelector, string maxElementsText, string maxDepthText,
                                  string wantScrollText, string nameLimitText)
        {
            int maxElements = Int(maxElementsText, 400);
            int maxDepth = Int(maxDepthText, 14);
            bool wantScroll = Bool(wantScrollText, true);
            int nameLimit = Int(nameLimitText, 120);
            DateTime started = DateTime.UtcNow;

            StringBuilder body = new StringBuilder();
            CacheRequest request = MakeRequest();
            int visited = 0;
            int emitted = 0;
            int windows = 0;
            bool truncated = false;

            AutomationElementCollection tops = null;
            try { tops = Kids(AutomationElement.RootElement, request); }
            catch { tops = null; }

            if (tops != null)
            {
                for (int i = 0; i < tops.Count; i++)
                {
                    AutomationElement win = null;
                    try { win = tops[i]; } catch { continue; }
                    if (win == null) continue;
                    string title = Clean(Prop(win, AutomationElement.NameProperty), nameLimit);
                    int hwnd = Int(Clean(Prop(win, AutomationElement.NativeWindowHandleProperty), 20), 0);
                    int pid = Int(Clean(Prop(win, AutomationElement.ProcessIdProperty), 20), 0);
                    int left, top, width, height;
                    if (!RectOf(win, out left, out top, out width, out height)) continue;
                    if (width <= 0 || height <= 0) continue;
                    if (!MatchWindow(windowSelector, title, hwnd, pid)) continue;
                    bool offscreen = Bool(Clean(Prop(win, AutomationElement.IsOffscreenProperty), 4), false);
                    windows++;
                    AddRow(body, Row("1", "0", "w", "Window", title, "", S(left), S(top), S(width), S(height),
                                     "1", offscreen ? "1" : "0", "0", "0", "0", "", "", "", "", "", "", "", "", "",
                                     S(hwnd), S(pid), ""));
                    if (maxDepth > 1 && !offscreen)
                    {
                        Visit(win, 1, "0", title, hwnd, left, top, left + width, top + height, request, body,
                              ref visited, maxElements, maxDepth, wantScroll, nameLimit, new List<string>(),
                              ref truncated, ref emitted);
                    }
                }
            }

            string meta = "visited=" + S(visited) + US + "emitted=" + S(emitted) + US + "windows=" + S(windows)
                        + US + "truncated=" + (truncated ? "1" : "0")
                        + US + "elapsed_ms=" + S((int)(DateTime.UtcNow - started).TotalMilliseconds);
            return meta + RS + body.ToString();
        }

        static void Visit(AutomationElement element, int depth, string path, string winName, int winHwnd,
                          int winLeft, int winTop, int winRight, int winBottom, CacheRequest request,
                          StringBuilder body, ref int visited, int maxElements, int maxDepth, bool wantScroll,
                          int nameLimit, List<string> ancestors, ref bool truncated, ref int emitted)
        {
            if (element == null) return;
            if (visited >= maxElements) { truncated = true; return; }
            if (depth >= maxDepth) return;

            // A provider can hand back a child whose RuntimeId repeats an ancestor
            // (seen with expanded browser select elements): without this guard the
            // walk runs until the stack dies.
            string rid = RidKey(element);
            List<string> chain = ancestors;
            if (rid != null)
            {
                if (ancestors.Contains(rid)) return;
                chain = new List<string>(ancestors);
                chain.Add(rid);
            }

            AutomationElementCollection kids = null;
            try { kids = Kids(element, request); }
            catch { kids = null; }
            if (kids == null) return;

            for (int i = 0; i < kids.Count; i++)
            {
                if (visited >= maxElements) { truncated = true; return; }
                AutomationElement child = null;
                try { child = kids[i]; } catch { continue; }
                if (child == null) continue;
                string childPath = path + "." + S(i);
                visited++;
                Describe(child, depth + 1, childPath, winName, winHwnd, winLeft, winTop, winRight, winBottom,
                         body, wantScroll, nameLimit, ref emitted);
                Visit(child, depth + 1, childPath, winName, winHwnd, winLeft, winTop, winRight, winBottom, request,
                      body, ref visited, maxElements, maxDepth, wantScroll, nameLimit, chain, ref truncated, ref emitted);
            }
        }

        static void Describe(AutomationElement element, int depth, string path, string winName, int winHwnd,
                             int winLeft, int winTop, int winRight, int winBottom, StringBuilder body,
                             bool wantScroll, int nameLimit, ref int emitted)
        {
            string ctrl = Canon(Prop(element, AutomationElement.ControlTypeProperty));
            if (ctrl.Length == 0) return;
            string name = Clean(Prop(element, AutomationElement.NameProperty), nameLimit);
            string aid = Clean(Prop(element, AutomationElement.AutomationIdProperty), 60);
            string cls = Clean(Prop(element, AutomationElement.ClassNameProperty), 60);
            bool offscreen = Bool(Clean(Prop(element, AutomationElement.IsOffscreenProperty), 4), false);
            bool enabled = Bool(Clean(Prop(element, AutomationElement.IsEnabledProperty), 4), true);
            bool isControl = Bool(Clean(Prop(element, AutomationElement.IsControlElementProperty), 4), true);
            bool focusable = Bool(Clean(Prop(element, AutomationElement.IsKeyboardFocusableProperty), 4), false);
            bool focused = Bool(Clean(Prop(element, AutomationElement.HasKeyboardFocusProperty), 4), false);
            bool password = Bool(Clean(Prop(element, AutomationElement.IsPasswordProperty), 4), false);

            if (!isControl) return;
            int left, top, width, height;
            if (!RectOf(element, out left, out top, out width, out height)) return;

            // Intersect with the window so a stale/oversized box cannot claim screen space.
            int boxLeft = Math.Max(left, winLeft);
            int boxTop = Math.Max(top, winTop);
            int boxRight = Math.Min(left + width, winRight);
            int boxBottom = Math.Min(top + height, winBottom);
            int boxWidth = boxRight - boxLeft;
            int boxHeight = boxBottom - boxTop;
            if (boxWidth < 0) boxWidth = 0;
            if (boxHeight < 0) boxHeight = 0;
            int area = boxWidth * boxHeight;

            bool visible = area > 0 && (!offscreen || ctrl == "Edit" || ctrl == "ListItem");
            if (!visible) return;

            bool interactive = enabled && Has(INTERACTIVE, ctrl) && (name.Length > 0 || aid.Length > 0);
            bool scrollable = false;
            bool scrollV = false, scrollH = false;
            double scrollVPct = 0, scrollHPct = 0;
            if (wantScroll && !interactive && !Has(INFORMATIVE, ctrl) && !offscreen && Has(SCROLLTYPES, ctrl))
            {
                ScrollPattern scroll = Pattern<ScrollPattern>(element, ScrollPattern.Pattern, false);
                if (scroll != null)
                {
                    try
                    {
                        scrollV = scroll.Cached.VerticallyScrollable;
                        scrollH = scroll.Cached.HorizontallyScrollable;
                        if (scrollV) scrollVPct = scroll.Cached.VerticalScrollPercent;
                        if (scrollH) scrollHPct = scroll.Cached.HorizontalScrollPercent;
                    }
                    catch { scrollV = false; scrollH = false; }
                    scrollable = scrollV;
                }
            }
            bool structural = !interactive && !scrollable && Has(STRUCTURAL, ctrl) && name.Length > 0;
            bool informative = !interactive && !scrollable && !structural && Has(INFORMATIVE, ctrl) && name.Length > 0;
            if (!interactive && !scrollable && !structural && !informative) return;

            string value = "";
            string vmin = "", vmax = "";
            string toggle = "";
            string expand = "";
            string shortcut = "";
            if (interactive)
            {
                if (!password && Has(VALUETYPES, ctrl))
                {
                    ValuePattern vp = Pattern<ValuePattern>(element, ValuePattern.Pattern, false);
                    if (vp != null)
                    {
                        try { value = Clean(vp.Cached.Value, nameLimit); }
                        catch { value = ""; }
                    }
                }
                if (ctrl == "Slider" || ctrl == "Spinner")
                {
                    RangeValuePattern rv = Pattern<RangeValuePattern>(element, RangeValuePattern.Pattern, false);
                    if (rv != null)
                    {
                        try
                        {
                            value = S(rv.Cached.Value);
                            vmin = S(rv.Cached.Minimum);
                            vmax = S(rv.Cached.Maximum);
                        }
                        catch { }
                    }
                }
                if (Has(TOGGLETYPES, ctrl))
                {
                    TogglePattern tp = Pattern<TogglePattern>(element, TogglePattern.Pattern, false);
                    if (tp != null)
                    {
                        try { toggle = tp.Cached.ToggleState.ToString(); }
                        catch { toggle = ""; }
                    }
                }
                if (Has(EXPANDTYPES, ctrl))
                {
                    ExpandCollapsePattern ep = Pattern<ExpandCollapsePattern>(element, ExpandCollapsePattern.Pattern, false);
                    if (ep != null)
                    {
                        try { expand = ep.Cached.ExpandCollapseState.ToString(); }
                        catch { expand = ""; }
                    }
                }
                shortcut = Clean(Prop(element, AutomationElement.AcceleratorKeyProperty), 60);
            }

            string kind = interactive ? "i" : (scrollable ? "s" : (structural ? "c" : "t"));
            emitted++;
            AddRow(body, Row(S(depth), path, kind, ctrl, name, aid, S(boxLeft), S(boxTop), S(boxWidth), S(boxHeight),
                             enabled ? "1" : "0", offscreen ? "1" : "0", focusable ? "1" : "0", focused ? "1" : "0",
                             password ? "1" : "0", value, vmin, vmax, toggle, expand,
                             scrollV ? "1" : "0", scrollH ? "1" : "0", S(scrollVPct), S(scrollHPct),
                             S(winHwnd), "", cls, shortcut, winName));
        }

        // ------------------------------------------------------------------ find

        // ------------------------------------------------------------------ diag
        //
        // Debugging aid: does the cached read path actually return values? Prints,
        // for the first window's first few children, the cached and the live value
        // of ControlType / Name / BoundingRectangle side by side.
        public static string Diag(string windowSelector, string limitText)
        {
            int limit = Int(limitText, 6);
            CacheRequest request = MakeRequest();
            StringBuilder sb = new StringBuilder();
            AutomationElement root = AutomationElement.RootElement;
            AutomationElementCollection windows = null;
            try { windows = Kids(root, request); }
            catch (Exception error) { return "err=" + Clean(error.Message, 200) + RS; }
            if (windows == null) return "err=no windows" + RS;
            int shown = 0;
            for (int w = 0; w < windows.Count && shown < limit; w++)
            {
                AutomationElement win = null;
                try { win = windows[w]; } catch { continue; }
                if (win == null) continue;
                if (!MatchWindow(windowSelector, Clean(Prop(win, AutomationElement.NameProperty), 120),
                                 ToInt(Prop(win, AutomationElement.NativeWindowHandleProperty)),
                                 ToInt(Prop(win, AutomationElement.ProcessIdProperty)))) continue;
                AutomationElementCollection kids = null;
                try { kids = Kids(win, request); }
                catch (Exception error) { sb.Append(Row("window", "kids-error=" + Clean(error.Message, 120))).Append(RS); continue; }
                sb.Append(Row("window", Clean(Prop(win, AutomationElement.NameProperty), 60),
                              S(kids == null ? 0 : kids.Count))).Append(RS);
                if (kids == null) continue;
                for (int i = 0; i < kids.Count && shown < limit; i++)
                {
                    AutomationElement child = null;
                    try { child = kids[i]; } catch { continue; }
                    if (child == null) continue;
                    int l1, t1, w1, h1, l2, t2, w2, h2;
                    bool cachedRect = RectOf(child, out l1, out t1, out w1, out h1);
                    bool liveRect = LiveRectOf(child, out l2, out t2, out w2, out h2);
                    sb.Append(Row(S(i),
                                  "ctrlC=" + Canon(Prop(child, AutomationElement.ControlTypeProperty)),
                                  "nameC=" + Clean(Prop(child, AutomationElement.NameProperty), 30),
                                  "isCtlgC=" + S(ToInt(Prop(child, AutomationElement.IsControlElementProperty))),
                                  "rectC=" + (cachedRect ? S(l1) + "," + S(t1) + "," + S(w1) + "x" + S(h1) : "no"),
                                  "ctrlL=" + Canon(LiveProp(child, AutomationElement.ControlTypeProperty)),
                                  "nameL=" + Clean(LiveProp(child, AutomationElement.NameProperty), 30),
                                  "isCtrlL=" + S(ToInt(LiveProp(child, AutomationElement.IsControlElementProperty))),
                                  "rectL=" + (liveRect ? S(l2) + "," + S(t2) + "," + S(w2) + "x" + S(h2) : "no"))).Append(RS);
                    shown++;
                }
            }
            return sb.ToString();
        }

        public static string Find(string needle, string exactText, string windowSelector,
                                  string maxHitsText, string maxScanText)
        {
            bool exact = Bool(exactText, false);
            int maxHits = Int(maxHitsText, 40);
            int maxScan = Int(maxScanText, 4000);
            DateTime started = DateTime.UtcNow;
            string want = (needle == null ? "" : needle).ToLowerInvariant();
            if (want.Length == 0) return "hits=0" + US + "elapsed_ms=0" + RS;

            StringBuilder body = new StringBuilder();
            CacheRequest request = MakeRequest();
            int scanned = 0;
            int hits = 0;
            bool truncated = false;

            AutomationElementCollection tops = null;
            try { tops = Kids(AutomationElement.RootElement, request); }
            catch { tops = null; }

            if (tops != null)
            {
                for (int i = 0; i < tops.Count && hits < maxHits; i++)
                {
                    AutomationElement win = null;
                    try { win = tops[i]; } catch { continue; }
                    if (win == null) continue;
                    string title = Clean(Prop(win, AutomationElement.NameProperty), 120);
                    int hwnd = Int(Clean(Prop(win, AutomationElement.NativeWindowHandleProperty), 20), 0);
                    int pid = Int(Clean(Prop(win, AutomationElement.ProcessIdProperty), 20), 0);
                    int left, top, width, height;
                    if (!RectOf(win, out left, out top, out width, out height)) continue;
                    if (width <= 0 || height <= 0) continue;
                    if (!MatchWindow(windowSelector, title, hwnd, pid)) continue;
                    FindVisit(win, 1, "0", title, hwnd, want, exact, request, body, ref scanned, maxScan,
                              ref hits, maxHits, new List<string>(), ref truncated);
                }
            }

            string meta = "hits=" + S(hits) + US + "scanned=" + S(scanned) + US + "truncated=" + (truncated ? "1" : "0")
                        + US + "elapsed_ms=" + S((int)(DateTime.UtcNow - started).TotalMilliseconds);
            return meta + RS + body.ToString();
        }

        static void FindVisit(AutomationElement element, int depth, string path, string winName, int winHwnd,
                              string needle, bool exact, CacheRequest request, StringBuilder body, ref int scanned,
                              int maxScan, ref int hits, int maxHits, List<string> ancestors, ref bool truncated)
        {
            if (element == null) return;
            if (scanned >= maxScan || hits >= maxHits) { truncated = true; return; }

            string rid = RidKey(element);
            List<string> chain = ancestors;
            if (rid != null)
            {
                if (ancestors.Contains(rid)) return;
                chain = new List<string>(ancestors);
                chain.Add(rid);
            }

            AutomationElementCollection kids = null;
            try { kids = Kids(element, request); }
            catch { kids = null; }
            if (kids == null) return;

            for (int i = 0; i < kids.Count; i++)
            {
                if (scanned >= maxScan || hits >= maxHits) { truncated = true; return; }
                AutomationElement child = null;
                try { child = kids[i]; } catch { continue; }
                if (child == null) continue;
                scanned++;
                string childPath = path + "." + S(i);
                string ctrl = Canon(Prop(child, AutomationElement.ControlTypeProperty));
                string name = Clean(Prop(child, AutomationElement.NameProperty), 120);
                string aid = Clean(Prop(child, AutomationElement.AutomationIdProperty), 120);
                string help = Clean(Prop(child, AutomationElement.HelpTextProperty), 120);
                string value = "";
                if (Has(VALUETYPES, ctrl))
                {
                    ValuePattern vp = Pattern<ValuePattern>(child, ValuePattern.Pattern, false);
                    if (vp != null)
                    {
                        try { value = Clean(vp.Cached.Value, 120); }
                        catch { value = ""; }
                    }
                }
                string field = null;
                string hitText = null;
                if (Hit(name, needle, exact)) { field = "Name"; hitText = name; }
                else if (Hit(aid, needle, exact)) { field = "AutomationId"; hitText = aid; }
                else if (Hit(help, needle, exact)) { field = "HelpText"; hitText = help; }
                else if (Hit(value, needle, exact)) { field = "Value"; hitText = value; }

                if (field != null)
                {
                    int left, top, width, height;
                    if (RectOf(child, out left, out top, out width, out height))
                    {
                        bool offscreen = Bool(Clean(Prop(child, AutomationElement.IsOffscreenProperty), 4), false);
                        bool enabled = Bool(Clean(Prop(child, AutomationElement.IsEnabledProperty), 4), true);
                        bool focusable = Bool(Clean(Prop(child, AutomationElement.IsKeyboardFocusableProperty), 4), false);
                        hits++;
                        AddRow(body, Row(S(depth), childPath, ctrl, name, aid,
                                         S(left), S(top), S(width), S(height), enabled ? "1" : "0",
                                         offscreen ? "1" : "0", focusable ? "1" : "0", field, hitText,
                                         S(winHwnd), winName));
                    }
                }
                FindVisit(child, depth + 1, childPath, winName, winHwnd, needle, exact, request, body, ref scanned,
                          maxScan, ref hits, maxHits, chain, ref truncated);
            }
        }

        static bool Hit(string text, string needle, bool exact)
        {
            if (text == null || text.Length == 0) return false;
            string lower = text.ToLowerInvariant();
            if (exact) return lower == needle;
            return lower.IndexOf(needle) >= 0;
        }

        // ------------------------------------------------------------------ read

        public static string Read(string windowSelector, string maxCharsText, string maxScanText, string nameLimitText,
                                  string docTextText)
        {
            int maxChars = Int(maxCharsText, 20000);
            int maxScan = Int(maxScanText, 4000);
            int nameLimit = Int(nameLimitText, 200);
            bool docText = Bool(docTextText, false);
            DateTime started = DateTime.UtcNow;
            DateTime deadline = started.AddMilliseconds(DOC_BUDGET_MS);
            StringBuilder body = new StringBuilder();
            CacheRequest request = MakeRequest();
            int scanned = 0;
            int chars = 0;
            bool truncated = false;

            AutomationElementCollection tops = null;
            try { tops = Kids(AutomationElement.RootElement, request); }
            catch { tops = null; }

            if (tops != null)
            {
                for (int i = 0; i < tops.Count; i++)
                {
                    AutomationElement win = null;
                    try { win = tops[i]; } catch { continue; }
                    if (win == null) continue;
                    string title = Clean(Prop(win, AutomationElement.NameProperty), 120);
                    int hwnd = Int(Clean(Prop(win, AutomationElement.NativeWindowHandleProperty), 20), 0);
                    int pid = Int(Clean(Prop(win, AutomationElement.ProcessIdProperty), 20), 0);
                    int left, top, width, height;
                    if (!RectOf(win, out left, out top, out width, out height)) continue;
                    if (width <= 0 || height <= 0) continue;
                    if (!MatchWindow(windowSelector, title, hwnd, pid)) continue;
                    PushText(body, "== " + title + " ==", maxChars, ref chars, ref truncated);
                    ReadVisit(win, 1, request, body, ref scanned, maxScan, maxChars, nameLimit, ref chars,
                              new List<string>(), ref truncated, docText, deadline);
                }
            }

            string meta = "chars=" + S(chars) + US + "scanned=" + S(scanned) + US + "truncated=" + (truncated ? "1" : "0")
                        + US + "elapsed_ms=" + S((int)(DateTime.UtcNow - started).TotalMilliseconds);
            return meta + RS + body.ToString();
        }

        static void PushText(StringBuilder body, string text, int maxChars, ref int chars, ref bool truncated)
        {
            if (text == null || text.Length == 0) return;
            if (chars >= maxChars) { truncated = true; return; }
            chars += text.Length + 1;
            AddRow(body, text);
        }

        static void ReadVisit(AutomationElement element, int depth, CacheRequest request, StringBuilder body,
                              ref int scanned, int maxScan, int maxChars, int nameLimit, ref int chars,
                              List<string> ancestors, ref bool truncated, bool docText, DateTime deadline)
        {
            if (element == null) return;
            if (scanned >= maxScan || chars >= maxChars) { truncated = true; return; }
            if (DateTime.UtcNow > deadline) { truncated = true; return; }
            scanned++;

            string rid = RidKey(element);
            List<string> chain = ancestors;
            if (rid != null)
            {
                if (ancestors.Contains(rid)) return;
                chain = new List<string>(ancestors);
                chain.Add(rid);
            }

            string ctrl = Canon(Prop(element, AutomationElement.ControlTypeProperty));
            bool offscreen = Bool(Clean(Prop(element, AutomationElement.IsOffscreenProperty), 4), false);
            bool password = Bool(Clean(Prop(element, AutomationElement.IsPasswordProperty), 4), false);
            if (!offscreen)
            {
                PushText(body, Clean(Prop(element, AutomationElement.NameProperty), nameLimit), maxChars, ref chars, ref truncated);
                if (!password && (ctrl == "Edit" || ctrl == "ComboBox" || ctrl == "Spinner" || ctrl == "Slider"))
                {
                    ValuePattern vp = Pattern<ValuePattern>(element, ValuePattern.Pattern, false);
                    if (vp != null)
                    {
                        try { PushText(body, Clean(vp.Cached.Value, nameLimit), maxChars, ref chars, ref truncated); }
                        catch { }
                    }
                }
                if (docText && ctrl == "Document")
                {
                    // Document text comes from TextPattern; windows/panes also
                    // advertise it but return the whole subtree, which would repeat
                    // everything the walk below already collects.
                    //
                    // Chromium documents are the reason for the cap and the deadline:
                    // its provider materialises text by walking the whole accessibility
                    // tree, so GetText(2000) on the agent's own WebUI window took over
                    // 10 seconds and would have killed the whole call. Ask for little,
                    // and give up on the rest if the budget is gone.
                    if (DateTime.UtcNow <= deadline)
                    {
                        TextPattern tp = Pattern<TextPattern>(element, TextPattern.Pattern, false);
                        if (tp != null)
                        {
                            try
                            {
                                int want = nameLimit * 10;
                                if (want > DOC_TEXT_MAX) want = DOC_TEXT_MAX;
                                string doc = Clean(tp.DocumentRange.GetText(want), DOC_TEXT_MAX);
                                string[] lines = doc.Split('\n');
                                for (int i = 0; i < lines.Length; i++)
                                {
                                    PushText(body, Clean(lines[i], nameLimit), maxChars, ref chars, ref truncated);
                                }
                            }
                            catch { }
                        }
                    }
                }
            }

            if (chars >= maxChars) { truncated = true; return; }
            AutomationElementCollection kids = null;
            try { kids = Kids(element, request); }
            catch { kids = null; }
            if (kids == null) return;
            for (int i = 0; i < kids.Count; i++)
            {
                if (scanned >= maxScan || chars >= maxChars) { truncated = true; return; }
                AutomationElement child = null;
                try { child = kids[i]; } catch { continue; }
                if (child == null) continue;
                ReadVisit(child, depth + 1, request, body, ref scanned, maxScan, maxChars, nameLimit, ref chars,
                          chain, ref truncated, docText, deadline);
            }
        }

        // ------------------------------------------------------------------ locate / act

        // Live element by raw child index path. FindAll(TreeScope.Children, TrueCondition)
        // returns the same child sequence the cached walk recorded (Raw view), but as
        // live elements that patterns can actually be invoked on.
        static AutomationElement LocateLive(string hwndText, string path)
        {
            int hwnd = Int(hwndText, 0);
            if (hwnd == 0) return null;
            AutomationElement element = null;
            try { element = AutomationElement.FromHandle(new IntPtr(hwnd)); }
            catch { return null; }
            if (element == null) return null;
            if (path == null || path.Length == 0) return element;
            string[] steps = path.Split('.');
            for (int i = 1; i < steps.Length; i++)
            {
                int index = Int(steps[i], -1);
                if (index < 0) return null;
                AutomationElementCollection kids = null;
                try { kids = element.FindAll(TreeScope.Children, Condition.TrueCondition); }
                catch { return null; }
                if (kids == null || index >= kids.Count) return null;
                element = kids[index];
                if (element == null) return null;
            }
            return element;
        }

        public static string Resolve(string hwndText, string path)
        {
            AutomationElement element = LocateLive(hwndText, path);
            if (element == null) return "ok=0" + US + "note=" + Clean("element is gone", 120) + RS;
            string name = Clean(LiveProp(element, AutomationElement.NameProperty), 160);
            string ctrl = Canon(LiveProp(element, AutomationElement.ControlTypeProperty));
            string aid = Clean(LiveProp(element, AutomationElement.AutomationIdProperty), 60);
            int left, top, width, height;
            bool hasRect = LiveRectOf(element, out left, out top, out width, out height);
            bool enabled = Bool(Clean(LiveProp(element, AutomationElement.IsEnabledProperty), 4), true);
            bool offscreen = Bool(Clean(LiveProp(element, AutomationElement.IsOffscreenProperty), 4), false);
            string value = "";
            ValuePattern vp = Pattern<ValuePattern>(element, ValuePattern.Pattern, true);
            if (vp != null)
            {
                try { value = Clean(vp.Current.Value, 160); }
                catch { value = ""; }
            }
            StringBuilder patterns = new StringBuilder();
            string[] names = new string[] { "invoke", "value", "toggle", "select", "expand", "scroll", "scrollitem", "range", "focus" };
            AutomationPattern[] list = new AutomationPattern[] {
                InvokePattern.Pattern, ValuePattern.Pattern, TogglePattern.Pattern, SelectionItemPattern.Pattern,
                ExpandCollapsePattern.Pattern, ScrollPattern.Pattern, ScrollItemPattern.Pattern, RangeValuePattern.Pattern
            };
            for (int i = 0; i < list.Length; i++)
            {
                try
                {
                    object obj = null;
                    if (element.TryGetCurrentPattern(list[i], out obj) && obj != null)
                    {
                        if (patterns.Length > 0) patterns.Append(',');
                        patterns.Append(names[i]);
                    }
                }
                catch { }
            }
            try
            {
                if (element.Current.IsKeyboardFocusable)
                {
                    if (patterns.Length > 0) patterns.Append(',');
                    patterns.Append("focus");
                }
            }
            catch { }

            StringBuilder sb = new StringBuilder();
            sb.Append("ok=1");
            sb.Append(US).Append("name=").Append(name);
            sb.Append(US).Append("ctrl=").Append(ctrl);
            sb.Append(US).Append("aid=").Append(aid);
            sb.Append(US).Append("value=").Append(value);
            sb.Append(US).Append("enabled=").Append(enabled ? "1" : "0");
            sb.Append(US).Append("offscreen=").Append(offscreen ? "1" : "0");
            sb.Append(US).Append("patterns=").Append(patterns.ToString());
            if (hasRect)
            {
                sb.Append(US).Append("x=").Append(S(left));
                sb.Append(US).Append("y=").Append(S(top));
                sb.Append(US).Append("w=").Append(S(width));
                sb.Append(US).Append("h=").Append(S(height));
                sb.Append(US).Append("cx=").Append(S(left + width / 2));
                sb.Append(US).Append("cy=").Append(S(top + height / 2));
            }
            return sb.ToString() + RS;
        }

        public static string Act(string hwndText, string path, string action, string text, string amountText)
        {
            AutomationElement element = LocateLive(hwndText, path);
            if (element == null) return "ok=0" + US + "note=" + Clean("element is gone", 120) + RS;
            string did = "";
            string note = "";
            try
            {
                switch (action)
                {
                    case "invoke":
                    {
                        InvokePattern pattern = Pattern<InvokePattern>(element, InvokePattern.Pattern, true);
                        if (pattern == null) note = "no invoke pattern";
                        else { pattern.Invoke(); did = "invoke"; }
                        break;
                    }
                    case "value":
                    {
                        ValuePattern pattern = Pattern<ValuePattern>(element, ValuePattern.Pattern, true);
                        if (pattern == null) note = "no value pattern";
                        else { pattern.SetValue(text == null ? "" : text); did = "value"; }
                        break;
                    }
                    case "toggle":
                    {
                        TogglePattern pattern = Pattern<TogglePattern>(element, TogglePattern.Pattern, true);
                        if (pattern == null) note = "no toggle pattern";
                        else { pattern.Toggle(); did = "toggle"; }
                        break;
                    }
                    case "select":
                    {
                        SelectionItemPattern pattern = Pattern<SelectionItemPattern>(element, SelectionItemPattern.Pattern, true);
                        if (pattern == null) note = "no selection pattern";
                        else { pattern.Select(); did = "select"; }
                        break;
                    }
                    case "expand":
                    {
                        ExpandCollapsePattern pattern = Pattern<ExpandCollapsePattern>(element, ExpandCollapsePattern.Pattern, true);
                        if (pattern == null) note = "no expand pattern";
                        else { pattern.Expand(); did = "expand"; }
                        break;
                    }
                    case "collapse":
                    {
                        ExpandCollapsePattern pattern = Pattern<ExpandCollapsePattern>(element, ExpandCollapsePattern.Pattern, true);
                        if (pattern == null) note = "no expand pattern";
                        else { pattern.Collapse(); did = "collapse"; }
                        break;
                    }
                    case "range":
                    {
                        RangeValuePattern pattern = Pattern<RangeValuePattern>(element, RangeValuePattern.Pattern, true);
                        if (pattern == null) note = "no range pattern";
                        else { pattern.SetValue(Dbl(amountText, 0)); did = "range"; }
                        break;
                    }
                    case "scrollitem":
                    {
                        ScrollItemPattern pattern = Pattern<ScrollItemPattern>(element, ScrollItemPattern.Pattern, true);
                        if (pattern == null) note = "no scroll item pattern";
                        else { pattern.ScrollIntoView(); did = "scrollitem"; }
                        break;
                    }
                    case "focus":
                    {
                        element.SetFocus();
                        did = "focus";
                        break;
                    }
                    case "scroll":
                    {
                        ScrollPattern pattern = Pattern<ScrollPattern>(element, ScrollPattern.Pattern, true);
                        if (pattern == null) note = "no scroll pattern";
                        else
                        {
                            // text carries "<axis>:<sign>", e.g. "vertical:forward"
                            // ("forward" = show later content, i.e. wheel-down).
                            int times = Int(amountText, 1);
                            if (times < 1) times = 1;
                            bool horizontal = text != null && text.StartsWith("horizontal", StringComparison.OrdinalIgnoreCase);
                            bool backward = text != null && text.IndexOf("backward", StringComparison.OrdinalIgnoreCase) >= 0;
                            ScrollAmount step = backward ? ScrollAmount.SmallDecrement : ScrollAmount.SmallIncrement;
                            for (int i = 0; i < times; i++)
                            {
                                if (horizontal) pattern.Scroll(step, ScrollAmount.NoAmount);
                                else pattern.Scroll(ScrollAmount.NoAmount, step);
                            }
                            did = "scroll";
                        }
                        break;
                    }
                    default:
                        note = "unknown action: " + Clean(action, 40);
                        break;
                }
            }
            catch (Exception ex)
            {
                note = Clean(ex.Message, 200);
            }

            string name = Clean(LiveProp(element, AutomationElement.NameProperty), 160);
            string ctrl = Canon(LiveProp(element, AutomationElement.ControlTypeProperty));
            string value = "";
            ValuePattern vp = Pattern<ValuePattern>(element, ValuePattern.Pattern, true);
            if (vp != null)
            {
                try { value = Clean(vp.Current.Value, 160); }
                catch { value = ""; }
            }
            string toggle = "";
            TogglePattern tgl = Pattern<TogglePattern>(element, TogglePattern.Pattern, true);
            if (tgl != null)
            {
                try { toggle = tgl.Current.ToggleState.ToString(); }
                catch { toggle = ""; }
            }
            StringBuilder sb = new StringBuilder();
            sb.Append(did.Length > 0 ? "ok=1" : "ok=0");
            sb.Append(US).Append("did=").Append(did);
            sb.Append(US).Append("name=").Append(name);
            sb.Append(US).Append("ctrl=").Append(ctrl);
            sb.Append(US).Append("value=").Append(value);
            if (toggle.Length > 0) sb.Append(US).Append("toggle=").Append(toggle);
            if (note.Length > 0) sb.Append(US).Append("note=").Append(note);
            return sb.ToString() + RS;
        }
    }
}
'@

try {
  Add-Type -TypeDefinition $W7A_CS -ReferencedAssemblies UIAutomationClient, UIAutomationTypes, WindowsBase -ErrorAction Stop
} catch {
  $message = [string]$_.Exception.Message
  $message = $message -replace '[\r\n]+', ' '
  [Console]::Out.WriteLine('FATAL' + [char]31 + $message)
  [Console]::Out.Flush()
  exit 1
}

$US = [char]31
[Console]::Out.WriteLine('READY' + $US + '1' + $US + 'pid=' + $PID)
[Console]::Out.Flush()

while ($true) {
  $line = $null
  try { $line = [Console]::In.ReadLine() } catch { break }
  if ($line -eq $null) { break }
  if ($line -eq '') { continue }

  $parts = $line.Split($US)
  $seq = ''
  if ($parts.Count -gt 0) { $seq = [string]$parts[0] }
  $command = ''
  if ($parts.Count -gt 1) { $command = [string]$parts[1] }
  if ($command -eq 'quit') { break }

  $status = 'ok'
  $payload = ''
  try {
    switch ($command) {
      'ping' { $payload = [W7A.Uia]::Ping() }
      'tree' {
        $payload = [W7A.Uia]::Tree([W7A.Uia]::Arg($parts, 2), [W7A.Uia]::Arg($parts, 3),
                                   [W7A.Uia]::Arg($parts, 4), [W7A.Uia]::Arg($parts, 5),
                                   [W7A.Uia]::Arg($parts, 6))
      }
      'diag' {
        $payload = [W7A.Uia]::Diag([W7A.Uia]::Arg($parts, 2), [W7A.Uia]::Arg($parts, 3))
      }
      'find' {
        $payload = [W7A.Uia]::Find([W7A.Uia]::Arg($parts, 2), [W7A.Uia]::Arg($parts, 3),
                                   [W7A.Uia]::Arg($parts, 4), [W7A.Uia]::Arg($parts, 5),
                                   [W7A.Uia]::Arg($parts, 6))
      }
      'read' {
        $payload = [W7A.Uia]::Read([W7A.Uia]::Arg($parts, 2), [W7A.Uia]::Arg($parts, 3),
                                   [W7A.Uia]::Arg($parts, 4), [W7A.Uia]::Arg($parts, 5),
                                   [W7A.Uia]::Arg($parts, 6))
      }
      'resolve' {
        $payload = [W7A.Uia]::Resolve([W7A.Uia]::Arg($parts, 2), [W7A.Uia]::Arg($parts, 3))
      }
      'act' {
        $payload = [W7A.Uia]::Act([W7A.Uia]::Arg($parts, 2), [W7A.Uia]::Arg($parts, 3),
                                  [W7A.Uia]::Arg($parts, 4), [W7A.Uia]::Arg($parts, 5),
                                  [W7A.Uia]::Arg($parts, 6))
      }
      default { $status = 'err'; $payload = 'unknown command: ' + $command }
    }
  } catch {
    $status = 'err'
    $payload = [string]$_.Exception.Message
  }

  $safe = ([string]$payload) -replace '[\r\n]+', ' '
  [Console]::Out.WriteLine($seq + $US + $status + $US + $safe)
  [Console]::Out.Flush()
}
