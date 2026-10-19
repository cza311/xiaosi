# -*- coding: utf-8 -*-
"""看门狗：main.py 每 10 秒写 maid_alive.txt 心跳；心跳超过 45 秒没更新视为崩溃。
重启前弹窗征求主人同意（选「是」才拉起），不再自作主张自动重启。
正常退出时 main 会删除心跳文件，看门狗随之休眠（不弹窗不误拉）。
开机自启不受影响：开机由启动脚本直接拉起主程序，不经过本确认。
单实例：系统级互斥锁，重复启动的看门狗自动退出（防止累积多份）。"""
import ctypes
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
HB = os.path.join(BASE, "maid_alive.txt")
BAT = os.path.join(BASE, "启动女仆酱.bat")

MB_YESNO = 0x04
MB_ICONQUESTION = 0x20
MB_TOPMOST = 0x40000
MB_SETFOREGROUND = 0x10000
IDYES = 6


def ask_restart():
    """弹窗询问是否重启小四；120 秒无操作视为否"""
    return ctypes.windll.user32.MessageBoxW(
        0, "小四好像出问题退出了，要现在重新启动她吗？",
        "小四 · 看门狗", MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND) == IDYES


def main():
    # 单实例锁：已有看门狗在跑就直接退出
    ctypes.windll.kernel32.CreateMutexW(None, True, "MaidWatchdogMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    armed = True  # 只有见过心跳后才允许询问，避免开机误弹
    while True:
        time.sleep(10)
        try:
            if not os.path.exists(HB):
                armed = False
                continue
            age = time.time() - os.path.getmtime(HB)
            if age < 45:
                armed = True
                continue
            if armed and ask_restart():
                print("owner agreed, restarting maid", flush=True)
                # start /min：bat 窗口最小化，不再蹦控制台打扰主人
                subprocess.Popen(["cmd", "/c", "start", "/min", "", BAT],
                                 cwd=BASE, creationflags=0x08000000)
            armed = False  # 无论答什么，本轮之后都需心跳恢复再武装
        except Exception as e:
            print("watchdog error:", e, flush=True)


if __name__ == "__main__":
    main()
