# -*- coding: utf-8 -*-
"""小四的桌面形象：无边框置顶透明窗口 + 全彩立绘（多帧动画）+ 字幕条。
拖动为纯 Tk 实现（geometry 跟随），带待机浮动、随机散步、转身与挥手。
单击唤醒、右键菜单。所有方法线程安全。"""
import ctypes
import math
import os
import queue
import random
import time
import tkinter as tk
import tkinter.font as tkfont

WHITE = "#ffffff"


class _RECT(ctypes.Structure):
    """W4：GetWindowRect 用的小矩形结构（纯定义，跨平台安全）"""
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MaidAvatar:
    # W2：灵动岛状态胶囊的圆点配色与文案
    STATUS_COLORS = {"idle": "#7BC96F", "thinking": "#F5D76E",
                     "working": "#E8A0BF", "waiting": "#F0932B",
                     "error": "#FF6B6B", "done": "#7BC96F",
                     "singing": "#C56CF0"}
    STATUS_TEXT = {"idle": "空闲", "thinking": "思考中", "working": "干活中",
                   "waiting": "等主人确认", "error": "出错", "done": "完成",
                   "singing": "唱歌中"}

    def __init__(self, img_path, sub_font=("Microsoft YaHei UI", 15),
                 fullscreen_hide=True):
        self._q = queue.Queue()
        self._anim = False
        self._phase = 0.0
        self._idle_phase = 0.0
        self._walking = False
        self._bob = 0.0            # 走路步伐起伏（与迈步帧同步）
        self._stroll_after = None
        self._base_pos = None
        self._standing_by = False   # 躲在屏幕右边缘待命
        self._home_x = None
        self._talking = False       # 说话中：嘴巴开合循环
        self._talk_phase = 0
        self.last_touch = time.time()
        self.voice_on = True
        self._press_pos = None
        self._dragged = False
        self.on_click = None      # 单击形象回调（手动唤醒）
        self.menu_handler = None  # 右键菜单回调，接收标签字符串
        self.on_touch_event = None  # 交互事件回调（戳/摸头/抛掷），可选接入对话记忆

        # ---- 交互：摸头 / 戳 / 抛掷（学 Coopanion）----
        self._pat_mode = False
        self._pat_dist = 0.0
        self._pat_turns = 0
        self._pat_last_dx = 0.0
        self._pat_cool = 0.0
        self._pokes = []           # 最近单击时间戳（2 秒内 ≥3 次=被戳烦）
        self._vel = (0.0, 0.0, 0.0)  # 拖动末速度采样 (vx, vy, t)
        self._toss = None          # 抛掷飞行状态 dict(vx,vy,x,y,bounces)

        self.root = tk.Tk()
        self.root.title("小四")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", WHITE)
        self.root.configure(bg=WHITE)

        self.photo = tk.PhotoImage(file=img_path)
        w, h = self.photo.width(), self.photo.height()

        # 多帧立绘（存在才启用）：front/back/wave/side
        self.frames = {"front": self.photo}
        self._frame_key = "front"
        self._applied_frame = None
        self._pose_after = None
        _base = os.path.dirname(os.path.abspath(img_path))
        for k in ("back", "wave", "side", "angry", "tea", "walka", "walkb",
                  "bow", "dozy", "heart", "talka", "talkb"):
            p = os.path.join(_base, "avatar_%s.png" % k)
            if os.path.exists(p):
                try:
                    self.frames[k] = tk.PhotoImage(file=p)
                except Exception:
                    pass
        w = max([f.width() for f in self.frames.values()] + [w])
        h = max([f.height() for f in self.frames.values()] + [h])
        self._canvas_w = w

        # 字幕条：深色 + 樱花粉左侧装饰条；待机时收起，说话时才出现
        self.subrow = tk.Frame(self.root, bg="#171A20")
        self.sub = tk.Label(self.subrow, text="", fg="#EEF1F5", bg="#171A20",
                            font=sub_font, wraplength=w - 24, justify="left",
                            padx=14, pady=9, anchor="nw")
        self._accent = tk.Frame(self.subrow, bg="#E8A0BF", width=4)
        self._accent.pack(side="left", fill="y")
        self.sub.pack(side="left", fill="x", expand=True)
        self._sub_after = None  # 12s 无新台词自动收起
        self.canvas = tk.Canvas(self.root, width=w, height=h, bg=WHITE,
                                highlightthickness=0)
        self.canvas.pack(side="top")
        self.img_id = self.canvas.create_image((w - self.photo.width()) // 2, 0,
                                               anchor="nw", image=self.photo)

        # 右下角定位（底部锚定：字幕变化时保持底边不动）
        self.root.update_idletasks()
        self._screen_h = self.root.winfo_screenheight()
        self._margin = 36
        self._x = max(0, self.root.winfo_screenwidth() - w - 16)
        self._y = max(0, self._screen_h - self.root.winfo_reqheight() - self._margin)
        self.root.geometry("+%d+%d" % (self._x, self._y))
        self._home_x = self._x

        # 「你说的」贴身气泡：立在绘头旁，≤3 条渐隐（越旧越暗+各自 6s 到期）
        self.heard_win = tk.Toplevel(self.root)
        self.heard_win.overrideredirect(True)
        self.heard_win.attributes("-topmost", True)
        self.heard_win.attributes("-transparentcolor", WHITE)
        self.heard_win.configure(bg=WHITE)
        self.heard_win.withdraw()
        self.heard = tk.Canvas(self.heard_win, bg=WHITE, highlightthickness=0)
        self.heard.pack()
        self._heard_font = tkfont.Font(family="Microsoft YaHei UI Semibold", size=13)
        self._heard_msgs = []   # [(text, expire_ts)] 按时间序，新的在尾部
        self._heard_tick = None

        # W2 灵动岛状态胶囊：屏幕顶端居中小条「● 状态文字」，默认隐藏零侵入
        self.status_win = tk.Toplevel(self.root)
        self.status_win.overrideredirect(True)
        self.status_win.attributes("-topmost", True)
        self.status_win.attributes("-transparentcolor", WHITE)
        self.status_win.configure(bg=WHITE)
        self.status_win.withdraw()
        self.status_cv = tk.Canvas(self.status_win, bg=WHITE, highlightthickness=0)
        self.status_cv.pack()
        self._status_font = tkfont.Font(family="Microsoft YaHei UI", size=12)
        self._status_after = None  # 8 秒无变化自动收起

        # W4 全屏退让状态：前台窗口铺满全屏时把立绘滑出右缘，退出后滑回原位
        self._fs_hidden = False
        self._fs_saved_x = None
        self._fs_dead = False      # ctypes 出错即永久停用轮询
        self._glide_gen = 0        # 滑动代号：新一轮滑动让旧的 after 循环作废

        # 首次散步定时
        self.root.after(random.randint(20000, 45000), self._maybe_stroll)

        # W4：全屏退让轮询（fullscreen_hide=False 时不启动）
        if fullscreen_hide:
            self.root.after(2000, self._check_fullscreen)

        # 拖动/单击/右键（立绘和字幕条都生效）
        for wdg in (self.canvas, self.sub, getattr(self, "_accent", None)):
            if wdg is None:
                continue
            wdg.bind("<ButtonPress-1>", self._press)
            wdg.bind("<B1-Motion>", self._motion)
            wdg.bind("<ButtonRelease-1>", self._release)
            wdg.bind("<Button-3>", self._popup_menu)

        self._poll()

    # ---------- 线程安全 API ----------
    def say(self, text, thinking=False):
        self._q.put(("sub", (text, thinking)))

    def set_anim(self, on):
        self._q.put(("anim", on))

    def toggle(self):
        self._q.put(("toggle", None))

    def set_voice_state(self, on):
        self._q.put(("voice_state", bool(on)))

    def set_talking(self, on):
        """说话状态：讲话时嘴巴开合循环（由 Speaker 播完回调关闭）"""
        self._q.put(("talking", bool(on)))

    def pose(self, key, ms=1600):
        """切换姿势帧（如 wave），ms 毫秒后回到正面"""
        self._q.put(("pose", (key, ms)))

    def spin(self):
        """原地转一圈"""
        self._q.put(("spin", None))

    def touch(self):
        """主人有动静：刷新待机计时；若躲在边缘则滑出来"""
        self._q.put(("touch", None))

    def show_heard(self, text):
        """显示主人说的话（屏幕顶端居中），6 秒后自动消失"""
        self._q.put(("heard", str(text)))

    def set_status(self, status, detail=""):
        """W2 灵动岛状态胶囊：状态变化弹出，8 秒无变化自动收起（线程安全）"""
        self._q.put(("status", (str(status), str(detail))))

    def quit(self):
        self._q.put(("quit", None))

    def run(self):
        self.root.mainloop()

    # ---------- 内部 ----------
    def _show_sub(self):
        """显示字幕条；收起时长按文本长度动态估算（长句音频长，字幕不能先走）"""
        if not self.subrow.winfo_ismapped():
            self.subrow.pack(side="top", fill="x", before=self.canvas)
        self.root.update_idletasks()
        self._y = max(0, self._screen_h - self.root.winfo_reqheight() - self._margin)
        self.root.geometry("+%d+%d" % (self._x, self._y))
        if self._sub_after:
            try:
                self.root.after_cancel(self._sub_after)
            except Exception:
                pass
        try:
            n = len(str(self.sub.cget("text")))
        except Exception:
            n = 20
        est = max(12, int(n * 0.25) + 6)  # 秒：按语速约 4 字/秒估算并留余量
        self._sub_after = self.root.after(est * 1000, self._hide_sub)

    def _pick_expression(self, text):
        """按台词语气切换神情帧（2.2 秒后回正面继续嘴部循环）"""
        try:
            if "！" in text or "!" in text:
                key = random.choice(["angry", "heart", "wave"]) \
                    if random.random() < 0.85 else None
            elif "？" in text or "?" in text:
                key = "side" if random.random() < 0.85 else None
            else:
                key = random.choice(["wave", "bow", "heart", "tea"]) \
                    if random.random() < 0.45 else None
            if key and key in self.frames:
                self.pose(key, 2200)
        except Exception:
            pass

    def _hide_sub(self):
        self._sub_after = None
        self.subrow.pack_forget()
        self.root.update_idletasks()
        self._y = max(0, self._screen_h - self.root.winfo_reqheight() - self._margin)
        self.root.geometry("+%d+%d" % (self._x, self._y))

    def _poll(self):
        try:
            while True:
                ev, val = self._q.get_nowait()
                if ev == "sub":
                    text, thinking = val
                    self.sub.configure(text=("…" if thinking else text))
                    self._show_sub()
                    if not thinking and text:
                        self._pick_expression(text)  # 按语气配神情
                elif ev == "talking":
                    self._talking = bool(val)
                    if not val and self._frame_key in ("talka", "talkb"):
                        self._frame_key = "front"
                elif ev == "anim":
                    self._anim = bool(val)
                    if not self._anim:
                        self.canvas.coords(self.img_id, 0, 0)
                elif ev == "toggle":
                    if self.root.state() == "withdrawn":
                        self.root.deiconify()
                    else:
                        self.root.withdraw()
                        self.heard_win.withdraw()
                        try:  # W2：形象隐藏时状态胶囊一并收起
                            self.status_win.withdraw()
                        except Exception:
                            pass
                elif ev == "heard":
                    self._show_heard(val)
                elif ev == "status":
                    self._show_status(val)
                elif ev == "pose":
                    key, ms = val
                    if key in self.frames:
                        self._frame_key = key
                        if self._pose_after:
                            try:
                                self.root.after_cancel(self._pose_after)
                            except Exception:
                                pass
                        self._pose_after = self.root.after(
                            ms, lambda: setattr(self, "_frame_key", "front"))
                elif ev == "spin":
                    self._play_spin()
                elif ev == "touch":
                    self.last_touch = time.time()
                    if self._standing_by:
                        self._standing_by = False
                        self._glide_to(self._home_x if self._home_x is not None
                                       else self._x)
                elif ev == "voice_state":
                    self.voice_on = bool(val)
                elif ev == "menu":
                    if self.menu_handler:
                        try:
                            self.menu_handler(val)
                        except Exception:
                            pass
                elif ev == "quit":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        try:
            self._tick()
            if self.root.winfo_exists():
                self.root.after(40, self._poll)
        except tk.TclError:
            pass

    def _tick(self):
        self._idle_phase += 0.12
        if self._talking and not self._pose_after and "talka" in self.frames:
            # 说话口型：约 160ms 一变，张-合-闭交替（pose 神情期间让位）
            self._talk_phase += 1
            if self._talk_phase % 4 == 0:
                seq = ("talka", "talkb", "front", "talkb")
                self._frame_key = seq[(self._talk_phase // 4) % len(seq)]
        img = self.frames.get(self._frame_key, self.photo)
        if img is not self._applied_frame:
            self._applied_frame = img
            self.canvas.itemconfigure(self.img_id, image=img)
        ox = (self._canvas_w - img.width()) // 2
        if self._anim:
            self._phase += 0.22
            dy = -abs(math.sin(self._phase)) * 7
            dx = ox + math.sin(self._phase / 2.2) * 4
            self.canvas.coords(self.img_id, dx, dy)
        else:
            # 待机呼吸浮动；走动时叠加步伐起伏（脚不离地的轻微重心移动）
            self.canvas.coords(self.img_id, ox,
                               math.sin(self._idle_phase) * 4 - self._bob)
        # 6 分钟没动静：滑到屏幕右边缘躲起来待命（W4：全屏退让期间不抢）
        if (not self._standing_by and not self._walking and not self._anim
                and not self._press_pos and not self._fs_hidden
                and time.time() - self.last_touch > 360):
            self._standing_by = True
            self._glide_to(self.root.winfo_screenwidth() - 26)

    # ---------- 自由活动 ----------
    def _maybe_stroll(self):
        """待机时沿屏幕底部随机散步一小段"""
        if self._walking or self._press_pos or self._anim or self._fs_hidden:
            # W4：全屏退让隐藏期间不散步，防止把立绘走回屏幕里
            self.root.after(15000, self._maybe_stroll)
            return
        try:
            if self.root.state() == "withdrawn":
                self.root.after(15000, self._maybe_stroll)
                return
        except Exception:
            return
        w = self.photo.width()
        sw = self.root.winfo_screenwidth()
        tx = self._x + random.choice([-1, 1]) * random.randint(150, 450)
        tx = max(10, min(tx, sw - w - 10))
        if abs(tx - self._x) < 60:
            self.root.after(15000, self._maybe_stroll)
            return
        self._stroll_to(tx)

    def _stroll_to(self, tx):
        self._walking = True
        x0, total = self._x, tx - self._x
        # 帧数按距离定：峰值约 160px/s，smoothstep 自带起步加速与临停减速
        T = max(20, int(abs(total) / 5.5))
        prog = [0]
        phase = [0.0]

        def step():
            if not self._walking or self._press_pos:
                self._walking = False
                self._bob = 0.0
                self._frame_key = "front"
                self.root.after(15000, self._maybe_stroll)
                return
            i = prog[0]
            prog[0] += 1
            if i >= T:
                self._x = tx
                self._bob = 0.0
                self._frame_key = "front"
                self.root.geometry("+%d+%d" % (self._x, self._y))
                r = random.random()
                if r < 0.4:
                    self._play_spin()
                elif r < 0.75:
                    extra = [k for k in self.frames if k not in ("front", "side", "walka", "walkb")]
                    if extra:
                        self.pose(random.choice(extra), 2600)
                self.root.after(random.randint(30000, 90000), self._maybe_stroll)
                return
            t = i / float(T)
            e = t * t * (3.0 - 2.0 * t)  # smoothstep：起步缓加速、到达前缓减速
            self._x = int(round(x0 + total * e))
            phase[0] += 0.8
            s = math.sin(phase[0])
            ramp = min(1.0, i / 5.0, (T - i) / 5.0)  # 首尾淡入淡出，消除顿挫
            self._bob = abs(s) * 4.5 * ramp
            if "walka" in self.frames:
                # 迈步帧与起伏同相位：半个步周期换一帧，像真的在走
                self._frame_key = "walkb" if s < 0 else "walka"
            self.root.geometry("+%d+%d" % (self._x, self._y))
            self.root.after(33, step)

        step()

    def _play_spin(self):
        """转身一圈：正面 -> 侧面 -> 背面 -> 侧面 -> 正面"""
        seq = ["side", "back", "side", "front"]

        def step(i):
            if i >= len(seq):
                self._frame_key = "front"
                return
            self._frame_key = seq[i]
            self.root.after(170, lambda: step(i + 1))

        step(0)

    def _glide_to(self, tx):
        """平滑滑动到指定 x（躲边/回归共用）：ease-out，到位轻缓停下"""
        x0 = self._x
        total = tx - x0
        if abs(total) <= 12:
            return
        T = max(15, int(abs(total) / 11.0))
        prog = [0]
        self._glide_gen += 1  # W4：新滑动开始，旧滑动循环自动作废（防并发打架）
        gen = self._glide_gen

        def step():
            if gen != self._glide_gen:
                return
            i = prog[0]
            prog[0] += 1
            if i >= T:
                self._x = tx
                self.root.geometry("+%d+%d" % (self._x, self._y))
                return
            t = i / float(T)
            e = 1.0 - (1.0 - t) * (1.0 - t)  # ease-out：起步顺滑、收尾轻盈
            self._x = int(round(x0 + total * e))
            self.root.geometry("+%d+%d" % (self._x, self._y))
            self.root.after(16, step)

        step()

    def _show_heard(self, text):
        """主人说的话变成贴身气泡（立绘头旁），最多留 3 条，各 6 秒渐隐"""
        if not text:
            self._heard_msgs = []
            self._heard_refresh()
            return
        self._heard_msgs.append((text, time.time() + 6.0))
        self._heard_msgs = self._heard_msgs[-3:]  # 只留最近 3 条
        self._heard_refresh()
        if not self._heard_tick:  # 过期清理循环只在有气泡时跑
            self._heard_tick = self.root.after(400, self._heard_loop)

    def _heard_loop(self):
        self._heard_tick = None
        now = time.time()
        self._heard_msgs = [m for m in self._heard_msgs if m[1] > now]
        self._heard_refresh()
        if self._heard_msgs:
            self._heard_tick = self.root.after(400, self._heard_loop)

    def _heard_refresh(self):
        """重绘全部气泡：越旧越暗（渐隐感），跟随立绘当前位置"""
        c = self.heard
        if not self._heard_msgs:
            try:
                if self._heard_tick:
                    self.root.after_cancel(self._heard_tick)
            except Exception:
                pass
            self._heard_tick = None
            self.heard_win.withdraw()
            return
        f = self._heard_font
        px, py, bar, gap, sp = 20, 11, 4, 12, 8
        cards = []  # (full_text, text_w, card_w, card_h)
        for text, _exp in self._heard_msgs:
            full = "你：" + text
            tw = min(f.measure(full), 420)  # 超宽自动折行
            tmp = c.create_text(0, 0, text=full, font=f, anchor="nw", width=tw)
            bb = c.bbox(tmp)
            c.delete(tmp)
            th = (bb[3] - bb[1]) if bb else f.metrics("linespace")
            cards.append((full, tw, px * 2 + bar + gap + tw, th + py * 2))
        W = max(cw for _, _, cw, _ in cards)
        H = sum(ch for _, _, _, ch in cards) + sp * (len(cards) - 1)
        c.configure(width=W, height=H)
        c.delete("all")
        n = len(cards)
        y0 = 0
        for i, (full, tw, cw, ch) in enumerate(cards):
            k = (i + 1) / float(n)  # 0=最旧 → 1=最新
            self._round_card(c, 0, y0, cw, ch,
                             self._mix("#232830", "#171A20", k),
                             self._mix("#3A424E", "#333A45", k))
            c.create_rectangle(px, y0 + py + 2, px + bar, y0 + ch - py - 2,
                               fill="#E8A0BF", width=0)
            c.create_text(px + bar + gap, y0 + ch // 2, text=full, font=f,
                          fill=self._mix("#8C96A2", "#EEF1F5", k),
                          anchor="w", width=tw, justify="left")
            y0 += ch + sp
        # 贴身定位：立绘头部一侧；左边放不下就换到右侧
        if self._x - W - 12 >= 0:
            x = self._x - W - 12
        else:
            x = self._x + self.root.winfo_width() + 12
        self.heard_win.geometry("+%d+%d" % (x, self._y + 4))
        self.heard_win.deiconify()

    @staticmethod
    def _mix(c1, c2, k):
        """颜色插值：k=0 返回 c1，k=1 返回 c2"""
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02X%02X%02X" % tuple(
            int(a[j] + (b[j] - a[j]) * k) for j in range(3))

    @staticmethod
    def _round_card(c, x, y, w, h, fill, outline):
        r = 12
        pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r,
               x + w, y + h - r, x + w, y + h, x + w - r, y + h,
               x + r, y + h, x, y + h, x, y + h - r,
               x, y + r, x, y, x + r, y]
        c.create_polygon(pts, smooth=True, fill=fill,
                         outline=outline, width=1)

    # ---------- W2 灵动岛状态胶囊 ----------
    def _hide_status(self):
        self._status_after = None
        try:
            self.status_win.withdraw()
        except Exception:
            pass

    def _show_status(self, val):
        """深色圆角小条「● 状态文字」，居中弹出；thinking/working 期间常驻，
        其余状态 8 秒无变化自动收起。"""
        status, detail = val
        if self._status_after:  # 状态又变了：取消上一次的收起定时
            try:
                self.root.after_cancel(self._status_after)
            except Exception:
                pass
            self._status_after = None
        color = self.STATUS_COLORS.get(status, "#7BC96F")
        label = self.STATUS_TEXT.get(status, status)
        if detail:
            label = "%s·%s" % (label, detail[:16])  # detail 过长截断
        c = self.status_cv
        f = self._status_font
        tw = f.measure(label)
        dot, px, py, gap = 8, 16, 7, 8
        W = px * 2 + dot + gap + tw
        H = f.metrics("linespace") + py * 2
        c.configure(width=W, height=H)
        c.delete("all")
        r = H // 2  # 胶囊圆角
        pts = [r, 0, W - r, 0, W, 0, W, r, W, H - r, W, H,
               W - r, H, r, H, 0, H, 0, H - r, 0, r, 0, 0]
        c.create_polygon(pts, smooth=True, fill="#171A20",
                         outline="#333A45", width=1)
        cy = H // 2
        c.create_oval(px, cy - dot // 2, px + dot, cy + dot // 2 - 1,
                      fill=color, outline=color)
        c.create_text(px + dot + gap, cy, text=label, font=f,
                      fill="#EEF1F5", anchor="w")
        sw = c.winfo_screenwidth()
        x = max(0, (sw - W) // 2)
        self.status_win.geometry("+%d+%d" % (x, 12))  # 气泡已贴身，顶部不再重叠
        self.status_win.deiconify()
        if status not in ("thinking", "working"):  # 干活/思考期间保持显示
            self._status_after = self.root.after(8000, self._hide_status)

    # ---------- W4 全屏退让 ----------
    def _check_fullscreen(self):
        """每 2 秒检查前台窗口是否铺满整屏：是则把立绘滑出右缘让位，
        退出全屏后滑回原位。ctypes 一旦出错永久停用，绝不影响主流程。"""
        if self._fs_dead:
            return
        try:
            user32 = ctypes.windll.user32
            fg = user32.GetForegroundWindow()
            sw = self.root.winfo_screenwidth()
            full = False
            if fg:
                rect = _RECT()
                if user32.GetWindowRect(fg, ctypes.byref(rect)):
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    if w >= sw and h >= self._screen_h:
                        mine = self.root.winfo_id()
                        if fg != mine and fg != user32.GetParent(mine):
                            full = True  # 前台全屏且不是自己的窗口
            if full and not self._fs_hidden:
                self._fs_hidden = True
                self._walking = False    # 打断散步，防止散步把立绘走回来
                self._standing_by = False
                self._fs_saved_x = self._x
                self._glide_to(sw + 60)  # 滑出右缘外藏起来（不 withdraw）
            elif not full and self._fs_hidden:
                self._fs_hidden = False
                back = self._fs_saved_x if self._fs_saved_x is not None \
                    else (self._home_x if self._home_x is not None else self._x)
                self._fs_saved_x = None
                self._glide_to(back)
        except Exception:
            self._fs_dead = True  # ctypes 不可用：永久停用轮询
            return
        self.root.after(2000, self._check_fullscreen)

    # ---------- 拖动 / 摸头 / 戳 / 抛掷 ----------
    def _head_zone(self, e):
        """按下点是否在头部区域（窗口顶部 32%）"""
        try:
            top = self.root.winfo_rooty()
            return (e.y_root - top) < self.photo.height() * 0.32
        except Exception:
            return False

    def _touch_react(self, kind, extra=""):
        """俏皮台词 + 可选回调给主程序（进对话上下文）。
        台词来源：maid_talk 大词库+模板组合+洗牌抽样（手指一动就要出声，
        不等大模型；近12句去重，短期绝不重复）； maid_talk 缺席时回退原句。"""
        lines = {
            "pat": ["嘿嘿…再摸就蹭你了", "唔…好舒服，继续呀", "毛茸茸认证通过～",
                    "主人的手，暖洋洋的"],
            "poke": ["戳戳戳，要戳坏啦！", "再戳我真的要生气了哦（才怪）",
                     "喂！我在工作呢！", "戳一下要收钱的哦"],
            "throw": ["呜哇——我会飞！！", "你把我扔出去了呜呜", "轻一点啦！！",
                      "自由落体ing，好刺激"],
            "landed": ["呜…坐晕了…", "哼，不理你了（蹭蹭）", "下次不许扔这么远"],
        }
        self.pose({"pat": "heart", "poke": "angry", "throw": "dozy",
                   "landed": "tea"}.get(kind, "wave"), 2200)
        try:
            import maid_talk
            line = maid_talk.touch_line(kind) or random.choice(lines[kind])
        except Exception:
            line = random.choice(lines[kind])
        self.say(line)
        if self.on_touch_event:
            try:
                self.on_touch_event(kind, extra)
            except Exception:
                pass

    def _press(self, e):
        if self._toss:                      # 飞行中一抓就接住
            self._toss = None
            self._frame_key = "front"
        self._press_pos = (e.x_root, e.y_root)
        self._base_pos = (self._x, self._y)
        self._dragged = False
        self._pat_mode = False
        self._pat_dist = 0.0
        self._pat_turns = 0
        self._pat_last_dx = 0.0
        self._vel = (0.0, 0.0, time.time())
        self._head = self._head_zone(e)

    def _motion(self, e):
        if not self._press_pos:
            return
        dx = e.x_root - self._press_pos[0]
        dy = e.y_root - self._press_pos[1]
        if not self._dragged and not self._pat_mode:
            if abs(dx) + abs(dy) < 6:
                return
            # 头部按下且是水平往复 → 摸头模式；垂直拉太远则转为拎起
            if self._head and abs(dy) < 18:
                self._pat_mode = True
            else:
                self._dragged = True
        if self._pat_mode:
            step = float(e.x_root - (self._press_pos[0] + self._pat_last_dx))
            self._pat_last_dx = float(e.x_root - self._press_pos[0])
            self._pat_dist += abs(step)
            if (self._pat_dist > 130 and time.time() - self._pat_cool > 4
                    and not self._dragged):
                self._pat_cool = time.time()
                self._pat_dist = 0.0
                self.last_touch = time.time()
                self._touch_react("pat")
            return
        if not self._dragged:
            return
        # 拖动中采样末速度（px/s）
        now = time.time()
        px = self._base_pos[0] + dx
        py = max(0, self._base_pos[1] + dy)
        vx0, vy0, t0 = self._vel
        dt = now - t0
        if dt > 0.02:
            self._vel = ((px - self._x) / dt, (py - self._y) / dt, now)
        self._x = int(px)
        self._y = int(py)
        self.root.geometry("+%d+%d" % (self._x, self._y))

    def _release(self, e):
        pos = self._press_pos
        was_drag = self._dragged
        self._press_pos = None
        self._base_pos = None
        self._head = False
        self._pat_mode = False
        if was_drag:
            self._home_x = self._x
            vx, vy, t0 = self._vel
            age = time.time() - t0
            if age < 0.25 and math.hypot(vx, vy) > 900:   # 甩出去了 → 抛掷
                self._start_toss(vx, vy)
                return
        if pos and not was_drag and self.on_click:
            if abs(e.x_root - pos[0]) + abs(e.y_root - pos[1]) < 8:
                now = time.time()
                self._pokes = [t for t in self._pokes if now - t < 2.0] + [now]
                self.last_touch = time.time()
                if len(self._pokes) >= 3:                 # 连戳 3 下 → 戳烦了
                    self._pokes = []
                    self._touch_react("poke")
                try:
                    self.on_click()
                except Exception:
                    pass

    # ---------- 抛掷物理（学 Coopanion 的拎起甩飞）----------
    def _start_toss(self, vx, vy):
        sw = self.root.winfo_screenwidth()
        self._toss = {"vx": max(-2400, min(2400, vx)), "vy": max(-2400, min(0, vy)) - 200,
                      "bounces": 0, "sw": sw}
        self.last_touch = time.time()
        self._touch_react("throw")
        self._toss_tick()

    def _toss_tick(self):
        if not self._toss:
            return
        t = self._toss
        dt = 0.04
        t["vy"] += 2600 * dt                     # 重力
        self._x = int(max(0, min(self._x + t["vx"] * dt, t["sw"] - self.photo.width() - 4)))
        self._y = int(self._y + t["vy"] * dt)
        floor = self._screen_h - self.root.winfo_reqheight() - self._margin
        if self._y >= floor:                     # 落地反弹
            self._y = floor
            t["vy"] = -abs(t["vy"]) * 0.45
            t["vx"] *= 0.72
            t["bounces"] += 1
        if self._x <= 0 or self._x >= t["sw"] - self.photo.width() - 4:
            t["vx"] *= -0.6                      # 撞墙反弹
        self.root.geometry("+%d+%d" % (self._x, self._y))
        self._frame_key = random.choice(["dozy", "angry", "side"]) if t["bounces"] else "dozy"
        if (t["bounces"] >= 2 and abs(t["vy"]) < 260 and abs(t["vx"]) < 60) \
                or t["bounces"] >= 5:
            self._toss = None
            self._home_x = self._x
            self._frame_key = "front"
            self._touch_react("landed")
            return
        self.root.after(40, self._toss_tick)

    # ---------- 右键菜单 ----------
    def _popup_menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="聊天窗口（打字）",
                      command=lambda: self._q.put(("menu", "chat")))
        m.add_command(label="打开小四仓库",
                      command=lambda: self._q.put(("menu", "warehouse")))
        m.add_command(label="设置AI密钥…",
                      command=lambda: self._q.put(("menu", "settings")))
        m.add_command(label="语音监听：%s" % ("开" if self.voice_on else "关"),
                      command=lambda: self._q.put(("menu", "voice")))
        m.add_command(label="隐藏形象",
                      command=lambda: self._q.put(("menu", "hide")))
        m.add_separator()
        m.add_command(label="退出", command=lambda: self._q.put(("menu", "quit")))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
