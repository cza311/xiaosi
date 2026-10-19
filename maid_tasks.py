# -*- coding: utf-8 -*-
"""女仆酱的事务能力：待办/日程存储、到点提醒、实用工具（天气/打开应用/报时/整理桌面）"""
import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser
import requests
import winotify

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maid_data.json")

# 桌面整理分类表
DESKTOP_CATS = {
    "图片": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "heic", "ico"},
    "文档": {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "pdf", "txt",
             "md", "csv", "epub"},
    "压缩包": {"zip", "rar", "7z", "tar", "gz"},
    "安装包": {"exe", "msi", "apk"},
    "视频": {"mp4", "mkv", "avi", "mov", "flv", "wmv"},
    "音频": {"mp3", "wav", "flac", "m4a", "aac", "ogg"},
    "代码": {"py", "js", "ts", "html", "css", "java", "c", "cpp", "h", "ipynb"},
}

# 纯网站别名（网页优先；有桌面客户端的应用不走这里）
WEB_ALIASES = {
    "b站": "https://www.bilibili.com", "哔哩哔哩": "https://www.bilibili.com",
    "bilibili": "https://www.bilibili.com", "bili": "https://www.bilibili.com",
    "百度": "https://www.baidu.com", "知乎": "https://www.zhihu.com",
    "github": "https://github.com", "淘宝": "https://www.taobao.com",
    "京东": "https://www.jd.com", "邮箱": "https://mail.qq.com",
    "微博": "https://weibo.com", "小红书": "https://www.xiaohongshu.com",
}
# 站内搜索：search 动作带 site 参数时用对应站内搜索页
SITE_SEARCH = {
    "b站": "https://search.bilibili.com/all?keyword=",
    "bilibili": "https://search.bilibili.com/all?keyword=",
    "哔哩哔哩": "https://search.bilibili.com/all?keyword=",
    "youtube": "https://www.youtube.com/results?search_query=",
    "油管": "https://www.youtube.com/results?search_query=",
    "抖音": "https://www.douyin.com/search/",
    "知乎": "https://www.zhihu.com/search?type=content&q=",
    "网易云音乐": "https://music.163.com/#/search/m/?s=",
    "qq音乐": "https://y.qq.com/n/ryqq/search?keyword=",
    "github": "https://github.com/search?q=",
}
# 有桌面客户端、可在客户端搜索框里直接输入的站点（进程名 + 本地应用名）
CLIENT_SEARCH = {
    "b站": ("哔哩哔哩", "bilibili"),
    "哔哩哔哩": ("哔哩哔哩", "bilibili"),
    "bilibili": ("哔哩哔哩", "bilibili"),
    "qq音乐": ("qqmusic", "qqmusicadmin"),
    "网易云音乐": ("cloudmusic",),
}
CLIENT_APP = {
    "b站": "哔哩哔哩", "哔哩哔哩": "哔哩哔哩", "bilibili": "哔哩哔哩",
    "qq音乐": "QQ音乐", "网易云音乐": "网易云音乐",
}
# 系统应用直接映射
SYSTEM_APPS = {
    "记事本": "notepad.exe", "计算器": "calc.exe", "画图": "mspaint.exe",
    "资源管理器": "explorer.exe", "任务管理器": "taskmgr.exe",
    "cmd": "cmd.exe", "命令行": "cmd.exe", "命令提示符": "cmd.exe",
    "控制面板": "control.exe", "设置": "ms-settings:",
}
# 本地应用同义词（语音说A，开始菜单里叫B）；含常见英文应用的中文音译
LOCAL_SYNONYMS = {
    "b站": "哔哩哔哩", "bilibili": "哔哩哔哩", "bili": "哔哩哔哩",
    "斯迪姆": "steam", "斯基姆": "steam", "斯特姆": "steam", "斯提姆": "steam",
    "steam平台": "steam",
    "迪斯科": "discord", "迪斯克": "discord",
    "油管": "youtube",
}

# PowerShell 危险命令隔离表：命中后必须主人确认才执行
RISKY_TOKENS = {"del", "erase", "rd", "rmdir", "rm", "ri", "format", "format.com",
                "shutdown", "taskkill", "logoff", "diskpart", "bcdedit", "cipher",
                "takeown", "icacls", "dism", "vssadmin", "wbadmin"}
RISKY_SUBSTR = ["remove-item", "remove-localuser", "stop-process", "stop-service",
                "stop-computer", "restart-computer", "clear-content", "clear-item",
                "reg delete", "reg add", "reg import", "net user", "net localgroup",
                "invoke-expression", "encodedcommand", "start-process powershell",
                "start-process pwsh", "schtasks /delete", "sc delete", "sc config",
                "set-itemproperty", "set-executionpolicy", "set-service",
                "new-service", "iex ", "iex("]


def toast(title, msg):
    """Windows 通知"""
    try:
        n = winotify.Notification(app_id="小四", title=title, msg=msg)
        n.set_audio(winotify.audio.Default, loop=False)
        n.show()
    except Exception:
        pass


class MaidTasks:
    def __init__(self, on_remind=None, get_config=None):
        self.lock = threading.Lock()
        self.on_remind = on_remind      # 提醒触发回调 (text)
        self.get_config = get_config    # 读取配置（城市等）
        self.data = {"todos": [], "schedules": []}
        self.pending = None              # 被隔离拦截、等待主人确认的操作 (kind, payload)
        self._load()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ---------- 存取 ----------
    def _load(self):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            self.data = {"todos": [], "schedules": []}

    def _save(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ---------- 待办 ----------
    def add_todo(self, text):
        with self.lock:
            self.data["todos"].append({
                "id": self._next_id(), "text": text, "done": False,
                "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            self._save()

    def list_todos(self):
        with self.lock:
            undone = [t for t in self.data["todos"] if not t["done"]]
            return list(undone)

    def done_todo(self, tid):
        with self.lock:
            for t in self.data["todos"]:
                if t["id"] == tid and not t["done"]:
                    t["done"] = True
                    self._save()
                    return t
        return None

    def del_todo(self, tid):
        with self.lock:
            before = len(self.data["todos"])
            self.data["todos"] = [t for t in self.data["todos"] if t["id"] != tid]
            changed = len(self.data["todos"]) != before
            if changed:
                self._save()
            return changed

    def _next_id(self):
        ids = [t["id"] for t in self.data["todos"]] + [s["id"] for s in self.data["schedules"]]
        return (max(ids) + 1) if ids else 1

    # ---------- 日程 ----------
    def add_schedule(self, time_str, text, repeat="once"):
        """time_str 支持 'YYYY-MM-DD HH:MM' 或 'HH:MM'（今天，已过则明天）"""
        time_str = time_str.strip()
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d%H:%M", "%m-%d %H:%M", "%H:%M"):
            try:
                dt = datetime.datetime.strptime(time_str, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None, "时间格式没看懂，请用 YYYY-MM-DD HH:MM 或 HH:MM"
        if "%Y" not in fmt:
            now = datetime.datetime.now()
            dt = dt.replace(year=now.year)
            if "%m" not in fmt:
                dt = dt.replace(month=now.month, day=now.day)
            if dt <= now:
                dt += datetime.timedelta(days=1)
        with self.lock:
            sch = {
                "id": self._next_id(), "time": dt.strftime("%Y-%m-%d %H:%M"),
                "text": text, "repeat": repeat if repeat in ("once", "daily") else "once",
                "reminded": False,
            }
            self.data["schedules"].append(sch)
            self._save()
        return sch, None

    def list_schedules(self):
        with self.lock:
            return list(self.data["schedules"])

    # ---------- 提醒循环 ----------
    def _loop(self):
        while not self._stop.wait(20):
            now = datetime.datetime.now()
            fired = []
            with self.lock:
                for s in self.data["schedules"]:
                    try:
                        t = datetime.datetime.strptime(s["time"], "%Y-%m-%d %H:%M")
                    except ValueError:
                        continue
                    if t <= now and not s.get("reminded"):
                        s["reminded"] = True
                        fired.append(s)
                self._save()
            for s in fired:
                text = "日程提醒：{}".format(s["text"])
                toast("小四 · 到点了", text)
                if self.on_remind:
                    try:
                        self.on_remind(text)
                    except Exception:
                        pass
                if s.get("repeat") == "daily":
                    with self.lock:
                        nt = t + datetime.timedelta(days=1)
                        s["time"] = nt.strftime("%Y-%m-%d %H:%M")
                        s["reminded"] = False
                        self._save()
                else:
                    with self.lock:
                        self.data["schedules"] = [
                            x for x in self.data["schedules"] if x["id"] != s["id"]]
                        self._save()

    def stop(self):
        self._stop.set()

    # ---------- 实用工具 ----------
    def weather(self, city=None):
        city = (city or (self.get_config() or {}).get("city") or "上海").strip()
        try:
            r = requests.get("https://wttr.in/{}?format=3&lang=zh".format(city), timeout=10,
                             headers={"User-Agent": "curl/8.0"})
            r.encoding = "utf-8"
            return r.text.strip()
        except Exception:
            return None

    def _app_index(self):
        """收集开始菜单+桌面的所有快捷方式 (小写名, 路径)，进程内缓存"""
        idx = getattr(self, "_appidx", None)
        if idx is not None:
            return idx
        entries = []
        dirs = [
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                         "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(os.environ.get("APPDATA", ""),
                         "Microsoft", "Windows", "Start Menu", "Programs"),
            os.path.join(os.path.expanduser("~"), "Desktop"),
        ]
        for d in dirs:
            if os.path.isdir(d):
                for root, _, files in os.walk(d):
                    for f in files:
                        if f.lower().endswith(".lnk"):
                            entries.append((os.path.splitext(f)[0].lower(),
                                            os.path.join(root, f)))
        self._appidx = entries
        return entries

    def _find_local_app(self, name):
        """按 本地同义词/系统应用/精确/子串/模糊 依次在快捷方式里找应用"""
        name = name.strip().lower()
        if not name:
            return None
        name = LOCAL_SYNONYMS.get(name, name)
        if name in SYSTEM_APPS:
            return SYSTEM_APPS[name]
        entries = self._app_index()
        bad_keys = ("卸载", "uninstall", "update", "修复", "readme", "帮助")

        def is_bad(stem):
            return any(k in stem for k in bad_keys)

        for stem, path in entries:
            if stem == name and not is_bad(stem):
                return path
        best = None
        best_bad = None
        for stem, path in entries:
            if name in stem or stem in name:
                if is_bad(stem):
                    best_bad = best_bad or path
                else:
                    best = best or path
        if best:
            return best
        import difflib
        pool = [s for s, _ in entries if not is_bad(s)] or [s for s, _ in entries]
        close = difflib.get_close_matches(name, pool, n=1, cutoff=0.55)
        if close:
            for stem, path in entries:
                if stem == close[0]:
                    return path
        return best_bad

    def _open_url(self, url):
        """用 Edge 打开网页（主人的指定浏览器），Edge 不在则回退系统默认"""
        for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
            if os.path.exists(p):
                try:
                    subprocess.Popen([p, url])
                    return
                except Exception:
                    break
        webbrowser.open(url)

    def open_target(self, target):
        t = target.strip()
        low = t.lower()
        if t.startswith("http://") or t.startswith("https://"):
            self._open_url(t)
            return True
        if re_url_like(t):
            self._open_url("https://" + t)
            return True
        # 本地应用优先：有桌面客户端的一律启动客户端
        local = self._find_local_app(t)
        if local:
            try:
                os.startfile(local)
                return True
            except Exception:
                pass
        if low in WEB_ALIASES:
            self._open_url(WEB_ALIASES[low])
            return True
        # 文件搜索：按名字找主人重命名过的文件/文件夹
        hits = self.search_file(t)
        for h in hits:
            try:
                os.startfile(h)
                return True
            except Exception:
                continue
        try:
            os.startfile(t)  # 系统应用或文件
            return True
        except Exception:
            pass
        try:
            subprocess.Popen(t)  # PATH 里的程序
            return True
        except Exception:
            return False

    # ---------- 桌面客户端内搜索 ----------
    PS_APP_SEARCH = r"""
$found = $null
foreach ($n in @(__PROCS__)) {
  $found = Get-Process -Name $n -ErrorAction SilentlyContinue |
           Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
  if ($found) { break }
}
if (-not $found) { Write-Output 'NOWIN'; exit }
if (-not ([System.Management.Automation.PSTypeName]'UW').Type) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class UW {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, UIntPtr e);
}
"@
}
[UW]::ShowWindow($found.MainWindowHandle, 9) | Out-Null
[UW]::SetForegroundWindow($found.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 800
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $found.Id)
$win = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $win) { Write-Output 'NOUIA'; exit }
$editCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Edit)
function Find-Edit {
  $edits = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCond)
  $t = $null
  foreach ($e in $edits) {
    $c = $e.Current
    if ($c.IsEnabled -and -not $c.IsOffscreen) {
      if ($c.Name -match '搜索|search') { return $e }
      if (-not $t) { $t = $e }
    }
  }
  return $t
}
function Send-Kw($kw) {
  $esc = ''
  foreach ($ch in $kw.ToCharArray()) {
    if ('+^%~(){}[]'.IndexOf($ch) -ge 0) { $esc += ('{' + $ch + '}') } else { $esc += $ch }
  }
  [System.Windows.Forms.SendKeys]::SendWait($esc)
}
$kw = '__KW__'
$target = Find-Edit
if (-not $target) {
  Start-Sleep -Seconds 3   # Chromium 类应用第一次查询后才渐进开启无障碍树
  $target = Find-Edit
}
if ($target) {
  try {
    $vp = $target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    $vp.SetValue($kw)
  } catch {
    $target.SetFocus()
    Start-Sleep -Milliseconds 500
    Send-Kw $kw
  }
} else {
  # 无障碍树拿不到：点窗口顶部搜索框的大概位置（宽42%、高45px），再键盘输入
  $r = $win.Current.BoundingRectangle
  $x = [int]($r.X + $r.Width * 0.42)
  $y = [int]($r.Y + 45)
  [UW]::SetCursorPos($x, $y) | Out-Null
  Start-Sleep -Milliseconds 250
  [UW]::mouse_event(0x02, 0, 0, 0, [UIntPtr]::Zero)
  [UW]::mouse_event(0x04, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 900
  Send-Kw $kw
}
Start-Sleep -Milliseconds 600
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Write-Output 'OK'
"""

    def _uia_search(self, procs, keyword):
        """UIA 在桌面客户端的搜索框输入关键词并回车，返回是否成功"""
        procs_s = ",".join('"%s"' % p for p in procs)
        kw = str(keyword).replace("'", "''")
        cmd = (self.PS_APP_SEARCH.replace("__PROCS__", procs_s)
               .replace("__KW__", kw))
        try:
            out = self.run_ps(cmd, timeout=45)
        except Exception:
            return False
        return "OK" in str(out)

    def client_search(self, site, keyword):
        """优先在已开的桌面客户端里搜索；客户端没开则先启动再搜。失败返回 False"""
        procs = CLIENT_SEARCH.get(site)
        if not procs:
            return False
        if self._uia_search(procs, keyword):
            return True
        app = CLIENT_APP.get(site)
        local = self._find_local_app(app) if app else None
        if not local:
            return False
        try:
            os.startfile(local)
        except Exception:
            return False
        time.sleep(8)  # 等客户端窗口起来
        return self._uia_search(procs, keyword)

    def search_file(self, name):
        """按名字在 桌面/下载/文档 里搜文件和文件夹（找主人重命名过的东西）"""
        kw = name.replace("'", "''").replace("*", "")
        if not kw:
            return []
        ps = ("Get-ChildItem \"$env:USERPROFILE\\Desktop\","
              "\"$env:USERPROFILE\\Downloads\",\"$env:USERPROFILE\\Documents\" "
              "-Filter '*%s*' -Recurse -Depth 2 -ErrorAction SilentlyContinue | "
              "Select-Object -First 5 -ExpandProperty FullName" % kw)
        out = self.run_ps(ps, timeout=15, confirmed=True)
        return [l.strip() for l in out.splitlines()
                if l.strip() and os.path.exists(l.strip())]

    def _ps_mouse_click(self, x, y):
        ps = ("Add-Type @'\n"
              "using System;using System.Runtime.InteropServices;\n"
              "public class M{[DllImport(\"user32.dll\")]public static extern bool "
              "SetCursorPos(int x,int y);[DllImport(\"user32.dll\")]public static "
              "extern void mouse_event(uint f,uint dx,uint dy,uint d,int e);}\n"
              "'@\n"
              "[M]::SetCursorPos(%d,%d);Start-Sleep -Milliseconds 150;"
              "[M]::mouse_event(2,0,0,0,0);Start-Sleep -Milliseconds 60;"
              "[M]::mouse_event(4,0,0,0,0)" % (int(x), int(y)))
        return self.run_ps(ps, timeout=10, confirmed=True)

    def _ps_type(self, text):
        esc = text.replace("'", "''")
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "[System.Windows.Forms.SendKeys]::SendWait('%s')" % esc)
        return self.run_ps(ps, timeout=10, confirmed=True)

    def close_app(self, name):
        """优雅关闭本地应用（等效点窗口×），不杀进程、不丢数据"""
        name = name.strip()
        if not name:
            return "要帮主人关掉哪个应用呢？"
        low = name.lower()
        key = LOCAL_SYNONYMS.get(low, low)
        proc_key = {"qq音乐": "qqmusic", "网易云音乐": "cloudmusic",
                    "网易云": "cloudmusic", "微信": "wechat", "weixin": "weixin",
                    "哔哩哔哩": "bilibili", "b站": "bilibili",
                    "酷狗音乐": "kugou", "百度网盘": "baidunetdisk",
                    "谷歌浏览器": "chrome", "edge浏览器": "msedge",
                    "记事本": "notepad", "计算器": "calculatorapp",
                    }.get(key, key.replace(" ", ""))
        proc_key = proc_key.replace("*", "").replace("?", "")
        title = name.replace("'", "''").replace("*", "")
        ps = (
            "$targets = Get-Process | Where-Object { "
            "($_.ProcessName -like '*%s*' -or $_.MainWindowTitle -like '*%s*') "
            "-and $_.Id -ne $PID -and $_.ProcessName -ne 'powershell' }; "
            "$closed = @(); "
            "foreach ($p in $targets) { try { if ($p.CloseMainWindow()) { $closed += $p.ProcessName } } catch {} }; "
            "if ($closed) { '已优雅关闭: ' + ($closed -join ', ') } "
            "elseif ($targets) { '找到了进程但窗口无法优雅关闭: ' + (($targets | Select-Object -First 3 -ExpandProperty ProcessName) -join ', ') } "
            "else { '没有找到正在运行的 %s' }"
        ) % (proc_key, title, name)
        return self.run_ps(ps, timeout=20, confirmed=True)

    def now(self):
        return datetime.datetime.now().strftime("现在是 %Y年%m月%d日 %H:%M，%A")

    # ---------- PowerShell 执行 ----------
    @staticmethod
    def _decode(b):
        for enc in ("utf-8", "gbk"):
            try:
                return b.decode(enc)
            except UnicodeDecodeError:
                continue
        return b.decode("utf-8", "replace")

    @staticmethod
    def _is_risky(cmd):
        """危险命令判定：删除/格式化/结束进程/关机/改注册表等"""
        low = cmd.lower()
        if any(s in low for s in RISKY_SUBSTR):
            return True
        for tok in re.split(r"[\s|;,&(){}]+", low):
            if tok.strip("`'\"") in RISKY_TOKENS:
                return True
        return False

    def run_ps(self, command, timeout=30, confirmed=False):
        """执行 PowerShell 命令，返回输出文本（供智能体观察结果）。
        危险命令先隔离：暂存并要求主人确认。"""
        if not confirmed and self._is_risky(command):
            self.pending = ("ps", command)
            return ("【隔离拦截】该命令可能改变系统状态（删除/结束进程/关机等），"
                    "已暂存、暂不执行。请告知主人：确认执行请回复「确认」，"
                    "放弃请回复「取消」。命令内容：%s" % command)
        full = ("$OutputEncoding=[Console]::OutputEncoding="
                "[Text.Encoding]::UTF8;" + command)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", full],
                capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "命令执行超时（%d秒），已被强制终止。" % timeout
        except FileNotFoundError:
            return "这台电脑找不到 powershell。"
        out = self._decode(r.stdout or b"").strip()
        err = self._decode(r.stderr or b"").strip()
        if r.returncode != 0:
            text = (out + "\n" if out else "") + "[执行出错] " + (err or ("退出码 %d" % r.returncode))
        else:
            text = out if out else "命令执行成功，没有输出。"
        if len(text) > 1500:
            text = text[:1500] + "\n…（输出过长已截断）"
        return text

    # ---------- 整理桌面 ----------
    @staticmethod
    def _desktop_dir():
        """真实桌面目录（含 OneDrive 重定向的情况）"""
        try:
            CSIDL_DESKTOPDIRECTORY = 0x10
            buf = ctypes.create_unicode_buffer(300)
            if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY,
                                                      None, 0, buf) == 0 and buf.value:
                return buf.value
        except Exception:
            pass
        return os.path.join(os.path.expanduser("~"), "Desktop")

    def clean_desktop(self, dry_run=False):
        """把桌面散落的文件按类型归档进「桌面整理\分类」文件夹，返回报告文本。
        快捷方式(.lnk)和文件夹不动；正在使用的文件跳过。"""
        desk = self._desktop_dir()
        moved, skipped = {}, 0
        for name in os.listdir(desk):
            p = os.path.join(desk, name)
            if not os.path.isfile(p) or name.lower().endswith(".lnk"):
                continue
            ext = os.path.splitext(name)[1].lower().lstrip(".")
            cat = next((c for c, exts in DESKTOP_CATS.items() if ext in exts), "其他")
            moved[cat] = moved.get(cat, 0) + 1
            if dry_run:
                continue
            dest_dir = os.path.join(desk, "桌面整理", cat)
            os.makedirs(dest_dir, exist_ok=True)
            stem, suffix = os.path.splitext(name)
            dest = os.path.join(dest_dir, name)
            i = 1
            while os.path.exists(dest):
                dest = os.path.join(dest_dir, "%s(%d)%s" % (stem, i, suffix))
                i += 1
            try:
                shutil.move(p, dest)
            except (PermissionError, OSError):
                moved[cat] -= 1
                skipped += 1
        total = sum(moved.values())
        if total == 0:
            return ("主人的桌面很干净，不需要整理呢。" if not skipped
                    else "有%d个文件正在使用中，暂时动不了。" % skipped)
        parts = ["%s%d个" % (c, n) for c, n in moved.items() if n]
        msg = "桌面已整理：%s，共%d个文件归入「桌面整理」文件夹。" % ("、".join(parts), total)
        if skipped:
            msg += "另有%d个文件正在使用，先留着。" % skipped
        return msg

    # ---------- 动作分发（AI 的 @@action 与本地指令共用） ----------
    def execute(self, action):
        """执行动作，返回给主人看的确认文本"""
        name = action.get("action")
        try:
            if name == "add_todo":
                text = str(action.get("text", "")).strip()
                if not text:
                    return "主人，待办内容是空的哦。"
                self.add_todo(text)
                return "已记下待办：{}".format(text)
            if name == "list_todo":
                todos = self.list_todos()
                if not todos:
                    return "主人目前没有待办事项，很清闲呢。"
                lines = ["{}. [ ] {}".format(t["id"], t["text"]) for t in todos]
                return "主人的待办清单：\n" + "\n".join(lines)
            if name == "done_todo":
                t = self.done_todo(int(action.get("id", 0)))
                return "已完成：{}".format(t["text"]) if t else "没有找到这条待办。"
            if name == "del_todo":
                ok = self.del_todo(int(action.get("id", 0)))
                return "已删除该待办。" if ok else "没有找到这条待办。"
            if name == "add_schedule":
                sch, err = self.add_schedule(
                    str(action.get("time", "")), str(action.get("text", "")),
                    action.get("repeat", "once"))
                if err:
                    return err
                return "日程已登记：{}，内容：{}（{}）".format(
                    sch["time"], sch["text"], "每日重复" if sch["repeat"] == "daily" else "单次")
            if name == "weather":
                w = self.weather(action.get("city"))
                return w if w else "天气服务暂时联系不上。"
            if name == "open":
                ok = self.open_target(str(action.get("target", "")))
                return "已为主人打开。" if ok else "打不开这个东西，主人确认一下名称？"
            if name == "time":
                return self.now()
            if name == "clean_desktop":
                return self.clean_desktop()
            if name == "run_ps":
                return self.run_ps(str(action.get("command", "")))
            if name == "close_app":
                nm = str(action.get("name") or action.get("target") or "").strip()
                return self.close_app(nm)  # name 回退 target：模型常与 open 混淆
            if name == "run_confirm":
                p = self.pending
                self.pending = None
                if not p:
                    return "现在没有待确认的操作。"
                kind, payload = p
                if kind == "ps":
                    return "主人已确认。" + self.run_ps(payload, confirmed=True)
                if kind == "mouse":
                    return "主人已确认。" + self._ps_mouse_click(*payload)
                if kind == "type":
                    return "主人已确认。" + self._ps_type(payload)
                if kind == "file":
                    path, content = payload
                    code_root = os.path.realpath(
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "code"))
                    real = os.path.realpath(path)
                    if not real.lower().startswith(code_root.lower()):
                        return "路径不在代码沙盒目录（%s）内，拒绝写入。" % code_root
                    try:
                        os.makedirs(os.path.dirname(real), exist_ok=True)
                        with open(real, "w", encoding="utf-8") as f:
                            f.write(content)
                        return "主人已确认。已写入 %s。" % real
                    except Exception as e:
                        return "写文件失败（%s）。" % type(e).__name__
                return "未知待确认操作。"
            if name == "run_cancel":
                self.pending = None
                return "主人已取消，待执行操作已作废。"
            if name == "mouse_click":
                x, y = int(action.get("x", 0)), int(action.get("y", 0))
                return self._ps_mouse_click(x, y)  # 主人已授权，直接执行
            if name == "type_text":
                txt = str(action.get("text", ""))
                if not txt:
                    return "要键入什么内容呢？"
                return self._ps_type(txt)  # 主人已授权，直接执行
            if name == "write_file":
                path = str(action.get("path", "")).strip()
                content = action.get("content", "")
                content = str(content) if not isinstance(content, str) else content
                if not path or not content:
                    return "写文件需要 path 和 content。"
                code_root = os.path.realpath(
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "code"))
                real = os.path.realpath(path)
                if not real.lower().startswith(code_root.lower()):
                    return ("写代码请放在沙盒目录 %s 里（主人的安全设定），"
                            "例如 D:\\maid\\code\\hello.py" % code_root)
                try:
                    os.makedirs(os.path.dirname(real), exist_ok=True)
                    with open(real, "w", encoding="utf-8") as f:
                        f.write(content)
                    return "已写入 %s（%d 字符）。" % (real, len(content))
                except Exception as e:
                    return "写文件失败（%s）。" % type(e).__name__
            if name == "search":
                q = str(action.get("query", "")).strip()
                site = str(action.get("site", "")).strip().lower()
                if not q:
                    return "主人，搜索词是空的哦。"
                # 兜底：主人原话里带平台名但AI没填 site，自动识别
                if not site:
                    low = q.lower()
                    for k in ("bilibili", "b站", "哔哩哔哩", "youtube", "油管",
                              "知乎", "抖音", "github", "网易云音乐", "qq音乐"):
                        if k in low:
                            site = k
                            break
                if site:
                    for k, pre in SITE_SEARCH.items():
                        if site == k or k in site:
                            # 从关键词里剔掉平台名和搜索动词，留纯关键词
                            kw = q
                            for w in ("bilibili", "b站", "哔哩哔哩", "youtube",
                                      "油管", "知乎", "抖音", "github",
                                      "网易云音乐", "qq音乐"):
                                kw = re.sub(re.escape(w), "", kw, flags=re.I)
                            kw = (kw.replace("搜索", " ").replace("搜一下", " ")
                                    .replace("搜", " ").replace("找一下", " ")
                                    .replace("查一下", " ").replace("找", " ")
                                    .replace("查", " "))
                            kw = kw.strip(" 在用去上里中的看个帮我帮，。,.．?？") or q
                            # 有桌面客户端的优先客户端内搜索
                            if k in CLIENT_SEARCH and self.client_search(k, kw):
                                return "已在%s客户端里为主人搜索：%s" % (k, kw)
                            self._open_url(pre + urllib.parse.quote(kw))
                            return "已在%s里为主人搜索：%s" % (k, kw)
                self._open_url("https://www.bing.com/search?q=" + urllib.parse.quote(q))
                return "已用必应为主人搜索：" + q
            if name == "write_note":
                title = re.sub(r'[\\/:*?"<>|]', "_",
                               str(action.get("title", "便签")).strip()) or "便签"
                content = str(action.get("content", "")).strip()
                if not content:
                    return "便签内容是空的哦。"
                desk = self._desktop_dir()
                path = os.path.join(desk, title + ".txt")
                i = 1
                while os.path.exists(path):
                    path = os.path.join(desk, "%s(%d).txt" % (title, i))
                    i += 1
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content + "\n")
                return "便签「%s」已经放到主人桌面上了。" % title
        except Exception as e:
            return "执行动作时出错了（{}）。".format(type(e).__name__)
        return "这个指令小四还没学会。"


def re_url_like(s):
    import re
    return bool(re.match(r"^[\w-]+(\.[\w-]+)+(/\S*)?$", s)) and " " not in s
