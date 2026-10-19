# -*- coding: utf-8 -*-
"""小四的桌面聊天窗（独立进程）：文字输入 → 本地 remote API → 大脑。
独立 Tk 窗口避开主程序无边框窗口的焦点坑；回复仍由主窗口朗读+字幕。"""
import json
import os
import sys
import threading
import tkinter as tk

# 证书库：certifi + Windows 系统信任（含 Watt 加速器根证书），import requests 前设置
os.environ.setdefault("REQUESTS_CA_BUNDLE",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "ca_bundle.pem"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "libs"))  # 用小四自带的库（系统 python 裸装没 requests）
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as f:
        CFG = json.load(f)
except (OSError, ValueError):
    CFG = {}
URL = "http://127.0.0.1:%d/api/chat?token=%s" % (
    int(CFG.get("remote_port", 8765)), CFG.get("remote_token", ""))

root = tk.Tk()
root.title("和小四说话")
root.configure(bg="#171A20")
root.geometry("340x430+420+260")
root.attributes("-topmost", True)

hist = tk.Text(root, bg="#171A20", fg="#EEF1F5", wrap="word", relief="flat",
               padx=10, pady=8, width=34, height=18,
               font=("Microsoft YaHei UI", 11), state="disabled")
hist.pack(side="top", fill="both", expand=True)
hist.tag_configure("me", foreground="#8FD3FF")
hist.tag_configure("maid", foreground="#F5B8CB")
hist.tag_configure("dim", foreground="#8A93A0")

row = tk.Frame(root, bg="#171A20")
row.pack(side="bottom", fill="x")
ent = tk.Entry(row, bg="#23272F", fg="#EEF1F5", insertbackground="#EEF1F5",
               relief="flat", font=("Microsoft YaHei UI", 11))
ent.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=8, ipady=4)
btn = tk.Button(row, text="发送", bg="#E8A0BF", fg="#171A20", relief="flat",
                cursor="hand2", font=("Microsoft YaHei UI", 10),
                command=lambda: send())
btn.pack(side="right", padx=(0, 10), pady=8, ipadx=6, ipady=2)


def log(role, text, tag=None):
    hist.configure(state="normal")
    hist.insert("end", "%s：%s\n" % (role, text),
                tag or ("me" if role == "我" else "maid"))
    hist.see("end")
    hist.configure(state="disabled")


def send(_e=None):
    t = ent.get().strip()
    if not t:
        return
    ent.delete(0, "end")
    log("我", t)

    def work():
        try:
            r = requests.post(URL, data=json.dumps({"text": t}).encode("utf-8"),
                              headers={"Content-Type": "application/json; charset=utf-8"},
                              timeout=180)
            resp = (r.json() or {}).get("resp") or {}
            zhr = resp.get("zh")
            if isinstance(zhr, list):  # 剧本式台词数组：聊天窗拼成整句展示
                zhr = " ".join(str(s) for s in zhr if str(s).strip())
            zh = str(zhr or "").strip() or "（小四好像走神了，再试一次？）"
        except Exception as e:
            zh = "联系不上小四（%s），确认她正在运行。" % type(e).__name__
        root.after(0, lambda: log("小四", zh))

    threading.Thread(target=work, daemon=True).start()
    log("小四", "……", "dim")


ent.bind("<Return>", send)
log("小四", "在的呢，直接打字吩咐吧。（回复会在桌面字幕朗读）")
ent.focus_set()
root.mainloop()
