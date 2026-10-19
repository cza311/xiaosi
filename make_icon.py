# -*- coding: utf-8 -*-
"""生成小四桌面图标：立绘转 ico + 桌面快捷方式指向启动脚本"""
import os
import sys

from PIL import Image

AVA = r"D:\maid\avatar.png"
ICO = r"D:\maid\maid_icon.ico"
BAT = r"D:\maid\启动女仆酱.bat"
DESK = os.path.join(os.path.expanduser("~"), "Desktop", "小四.lnk")

im = Image.open(AVA).convert("RGBA")
im.save(ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ico ok:", ICO)

import pythoncom
from win32com.client import Dispatch

pythoncom.CoInitialize()
sh = Dispatch("WScript.Shell")
sc = sh.CreateShortCut(DESK)
sc.TargetPath = BAT
sc.WorkingDirectory = r"D:\maid"
sc.IconLocation = ICO
sc.Description = "启动电子女仆小四"
sc.save()
print("shortcut ok:", DESK)
