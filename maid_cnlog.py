# -*- coding: utf-8 -*-
"""小四的中文运行日志：把「主人让小四做了什么」用人话记进仓库。
写入 D:\\小四\\日志\\运行日志_中文_YYYY-MM-DD.txt
每次写入时自动检查：把非今天的旧日志压缩成 zip 放进 日志\\归档\\，原 txt 删除。
"""
import os
import time
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "小四", "日志")
ARCH = os.path.join(ROOT, "归档")
PREFIX = "运行日志_中文_"


def _archive_old(today_compact):
    """把非今天的中文日志压缩归档"""
    try:
        os.makedirs(ARCH, exist_ok=True)
        for fn in os.listdir(ROOT):
            if fn.startswith(PREFIX) and fn.endswith(".txt"):
                date = fn[len(PREFIX):-4].replace("-", "")
                if date and date != today_compact:
                    src = os.path.join(ROOT, fn)
                    dst = os.path.join(ARCH, fn[:-4] + ".zip")
                    if not os.path.exists(dst):
                        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
                            z.write(src, fn)
                    os.remove(src)
    except Exception:
        pass


def cnlog(what):
    """追加一条人话日志。what 例：『动作[open] → 已为主人打开。』"""
    try:
        os.makedirs(ROOT, exist_ok=True)
        today = time.strftime("%Y-%m-%d")
        _archive_old(today.replace("-", ""))
        path = os.path.join(ROOT, PREFIX + today + ".txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), what))
    except Exception:
        pass
