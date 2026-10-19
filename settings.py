# -*- coding: utf-8 -*-
"""女仆酱设置窗口：填API密钥等，保存到 config.json（即时生效）"""
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(BASE, "config.json")


def main():
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}

    root = tk.Tk()
    root.title("小四 · 设置")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="API密钥（智谱 open.bigmodel.cn）").grid(row=0, column=0, sticky="w")
    key_var = tk.StringVar(value=cfg.get("api_key", ""))
    ttk.Entry(frm, textvariable=key_var, width=52, show="•").grid(
        row=1, column=0, sticky="we", pady=(2, 10))

    ttk.Label(frm, text="城市（天气用）").grid(row=2, column=0, sticky="w")
    city_var = tk.StringVar(value=cfg.get("city", "上海"))
    ttk.Entry(frm, textvariable=city_var, width=20).grid(row=3, column=0, sticky="w", pady=(2, 10))

    ttk.Label(frm, text="对主人的称呼").grid(row=4, column=0, sticky="w")
    name_var = tk.StringVar(value=cfg.get("master_name", "主人"))
    ttk.Entry(frm, textvariable=name_var, width=20).grid(row=5, column=0, sticky="w", pady=(2, 10))

    tip = ttk.Label(frm, foreground="#666", wraplength=430, justify="left", text=(
        "注意：密钥必须复制完整的一长串（约32位），\n"
        "控制台里显示的「xxxx...xxxx」是打码预览，直接复制它是没用的。\n\n"
        "获取流程：打开 https://open.bigmodel.cn 并登录 → 控制台 →\n"
        "「API 密钥」页面 → 点密钥旁边的「复制」按钮 → 粘贴到上面 → 保存。\n"
        "GLM-4-Flash 免费，保存后立即生效，无需重启。"))
    tip.grid(row=6, column=0, sticky="we", pady=(6, 12))

    def save():
        cfg["api_key"] = key_var.get().strip()
        cfg["city"] = city_var.get().strip() or "上海"
        cfg["master_name"] = name_var.get().strip() or "主人"
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        messagebox.showinfo("小四", "已保存，设置即时生效。", parent=root)
        root.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=7, column=0, sticky="e")
    ttk.Button(btns, text="保存", command=save).pack(side="left", padx=4)
    ttk.Button(btns, text="取消", command=root.destroy).pack(side="left")

    root.eval("tk::PlaceWindow . center")
    root.mainloop()


if __name__ == "__main__":
    main()
