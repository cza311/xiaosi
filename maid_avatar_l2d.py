# -*- coding: utf-8 -*-
"""小四的 Live2D 形象引擎：pywebview(WebView2) 透明无边框置顶窗口 + Live2D 模型。

公开 API 与 maid_avatar.MaidAvatar 全对齐（同名同参），可按 config.json 的
avatar_engine 配置无缝替换：
    say(text, thinking=False)  底部字幕气泡 + 一小段口型 burst
    set_anim(on)               内部标记（Live2D 自带待机动作，暂不消费）
    toggle()                   显示/隐藏窗口
    set_voice_state(on)        内部标记（voice_on）
    set_talking(on)            持续口型开合循环开/关（Speaker 播完回调关闭）
    pose(key, ms=1600)         映射 Live2D motion 组（见 MOTION_MAP，缺组 no-op）
    spin()                     映射 motion
    touch()                    映射 motion + 刷新 last_touch
    show_heard(text)           顶部「你说的」气泡（6 秒消失）
    set_status(status, detail) 顶部状态胶囊（thinking/working 常驻，其余 8 秒收起）
    quit()                     销毁窗口（幂等）
    run()                      webview.start 阻塞主循环
属性 on_click / on_touch_event / menu_handler 保留。Live2D v1 暂不触发
点击/触摸交互回调；右键菜单经页面 contextmenu → js_api.menu → menu_handler。

窗口/通信关键写法沿用 l2d/poc_transparent.py 的已验证结论：
    create_window(url, transparent=True, frameless=True, on_top=True)
    webview.start(fn, private_mode=False, storage_path=可写目录)
Python→页面通信走 window.evaluate_js（页面就绪前的事件先缓冲，就绪后回放）；
本地静态服务复用 l2d/server.py（端口 8799 已有服务则直接复用）。
"""
import ctypes
import json
import os
import socket
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
L2D_DIR = os.path.join(BASE, "l2d")
HOST, PORT = "127.0.0.1", 8799
URL = "http://%s:%d/" % (HOST, PORT)

# 姿势键 → Live2D motion 组映射（顶部集中定义；Hiyori 只有 Idle/TapBody，
# 页面端 window.__motion 对缺失组自动 no-op，None 表示不配动作）
MOTION_MAP = {
    "front": None, "back": None,
    "side": "TapBody", "wave": "TapBody", "heart": "TapBody",
    "angry": "TapBody", "tea": "TapBody", "bow": "TapBody",
    "dozy": "TapBody",
    "talka": None, "talkb": None,
    "walka": None, "walkb": None,
    "spin": "TapBody", "touch": "TapBody",
}


def _js_str(s):
    return json.dumps(str(s if s is not None else ""), ensure_ascii=False)


def _js_bool(b):
    return "true" if b else "false"


class _JsApi:
    """暴露给页面的 pywebview js_api：右键菜单回调入口（js→Python 唯一通路）"""

    def __init__(self, owner):
        self._owner = owner

    def menu(self, label="main"):
        handler = self._owner.menu_handler
        if handler:
            try:
                handler(str(label or "main"))
            except Exception:
                pass


class L2dAvatar:
    # W2：状态胶囊圆点配色与文案（与 MaidAvatar 一致）
    STATUS_COLORS = {"idle": "#7BC96F", "thinking": "#F5D76E",
                     "working": "#E8A0BF", "waiting": "#F0932B",
                     "error": "#FF6B6B", "done": "#7BC96F",
                     "singing": "#C56CF0"}
    STATUS_TEXT = {"idle": "空闲", "thinking": "思考中", "working": "干活中",
                   "waiting": "等主人确认", "error": "出错", "done": "完成",
                   "singing": "唱歌中"}

    def __init__(self, img_path=None, sub_font=None, fullscreen_hide=True,
                 width=600, height=800):
        """img_path/sub_font 仅兼容 MaidAvatar 签名（Live2D 不消费）。
        模型固定用 l2d\\model\\Hiyori，找不到即抛错（main.py 回退 PNG 立绘）。"""
        import webview  # 延迟导入：缺 pywebview 时让调用方走 PNG 回退

        model3 = os.path.join(L2D_DIR, "model", "Hiyori", "Hiyori.model3.json")
        if not os.path.isfile(model3):
            raise FileNotFoundError("Live2D model not found: %s" % model3)

        self.on_click = None        # 单击形象回调（v1 暂不触发，保留接口）
        self.menu_handler = None    # 右键菜单回调，接收标签字符串
        self.on_touch_event = None  # 交互事件回调（v1 暂不触发，保留接口）
        self.voice_on = True
        self.last_touch = time.time()
        self._anim = False
        self._talking = False
        self._shown = True
        self._closed = False

        self._lock = threading.Lock()
        self._ready = False         # 页面就绪后置 True，缓冲事件回放完毕
        self._pending = []          # 页面就绪前缓冲的 js 片段

        self._ensure_server()
        self._webview = webview
        self._storage = os.path.join(
            os.environ.get("TEMP", os.path.join(BASE, "tmp")),
            "pywebview_l2d_avatar")
        os.makedirs(self._storage, exist_ok=True)

        sw, sh = self._screen_size()
        w = min(width, max(320, sw - 40))
        h = min(height, max(400, sh - 40))
        x = max(0, sw - w - 16)     # 右下角落位（与 PNG 立绘习惯一致）
        y = max(0, sh - h - 90)
        # private_mode=False + storage_path 是 WebView2 透明窗口的前提（见 PoC）
        self._window = webview.create_window(
            "小四", URL, width=w, height=h, x=x, y=y,
            transparent=True, frameless=True, on_top=True,
            js_api=_JsApi(self))

    # ---------- 线程安全公开 API ----------
    def say(self, text, thinking=False):
        self._push("window.__say(%s, %s)" % (_js_str(text), _js_bool(thinking)))

    def set_anim(self, on):
        self._anim = bool(on)

    def toggle(self):
        self._shown = not self._shown
        if self._shown:
            self._call("show")
        else:
            self._call("hide")

    def set_voice_state(self, on):
        self.voice_on = bool(on)

    def set_talking(self, on):
        self._talking = bool(on)
        self._push("window.__talk(%s)" % _js_bool(on))

    def pose(self, key, ms=1600):
        """姿势键映射 Live2D motion（ms 不消费：motion 自带时长与淡出）"""
        group = MOTION_MAP.get(key)
        if group:
            self._push("window.__motion(%s)" % _js_str(group))

    def spin(self):
        self.pose("spin")

    def touch(self):
        self.last_touch = time.time()
        self.pose("touch")

    def show_heard(self, text):
        self._push("window.__heard(%s)" % _js_str(str(text)))

    def set_status(self, status, detail=""):
        self._push("window.__status(%s, %s)"
                   % (_js_str(str(status)), _js_str(str(detail))))

    def quit(self):
        self._closed = True
        self._call("destroy")

    def run(self):
        self._webview.start(self._runner, private_mode=False,
                            storage_path=self._storage)

    # ---------- 内部 ----------
    def _screen_size(self):
        try:
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            return 1920, 1080

    def _call(self, method):
        """窗口方法（show/hide/destroy），跨线程 try 包住，幂等"""
        try:
            getattr(self._window, method)()
        except Exception:
            pass

    def _push(self, js):
        """页面就绪前缓冲，就绪后直接 evaluate_js（全部 try 包住）"""
        with self._lock:
            if self._closed:
                return
            if not self._ready:
                self._pending.append(js)
                return
        self._fire(js)

    def _fire(self, js):
        try:
            self._window.evaluate_js(js)
        except Exception:
            pass

    def _runner(self):
        """webview.start 的后台任务：等页面加载 → 等模型就绪 → 回放缓冲事件"""
        try:
            self._window.events.loaded.wait(timeout=20)
        except Exception:
            pass
        probe = "JSON.stringify(!!window._l2dModel)"
        for _ in range(30):  # 最多等 15 秒模型加载
            if self._closed:
                return
            try:
                raw = self._window.evaluate_js(probe)
                if raw and json.loads(raw):
                    break
            except Exception:
                pass
            time.sleep(0.5)
        with self._lock:
            self._ready = True
            pending, self._pending = self._pending, []
        for js in pending:
            if self._closed:
                break
            self._fire(js)

    def _ensure_server(self):
        """复用/启动 l2d 本地静态服务（参考 poc_transparent.py）"""
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return  # 端口已有服务（demo 或上次残留），直接复用
        except OSError:
            pass
        if L2D_DIR not in sys.path:
            sys.path.insert(0, L2D_DIR)
        try:
            import server as _l2d_server
            httpd = _l2d_server.make_server()
        except Exception:
            return  # 端口被占且非我们的服务：不阻塞主流程，页面仍可能可用
        threading.Thread(target=httpd.serve_forever, daemon=True,
                         name="l2d-http").start()
