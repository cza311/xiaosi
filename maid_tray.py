# -*- coding: utf-8 -*-
"""女仆酱托盘菜单"""
import os
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))


def start_tray(avatar_png, on_quit, avatar=None, listener_holder=None, on_settings=None):
    """listener_holder: dict {"listener": Listener或None}，语音开关时读取"""
    import pystray
    from PIL import Image

    img = Image.open(avatar_png).resize((64, 64))

    def _listener():
        return (listener_holder or {}).get("listener")

    def settings_click(icon, item):
        if on_settings:
            on_settings()
        else:
            subprocess.Popen(["pyw", os.path.join(BASE, "settings.py")], cwd=BASE)

    def toggle_voice(icon, item):
        l = _listener()
        if l is not None:
            l.enabled = not l.enabled
            if not l.enabled:
                l.sleep()
            if avatar:
                avatar.set_voice_state(l.enabled)

    def voice_checked(icon, item=None):
        l = _listener()
        return bool(l and l.enabled)

    def toggle_avatar(icon, item):
        if avatar:
            avatar.toggle()

    def quit_click(icon, item):
        icon.stop()
        on_quit()

    menu = pystray.Menu(
        pystray.MenuItem("设置AI密钥…", settings_click, default=True),
        pystray.MenuItem("语音监听", toggle_voice, checked=voice_checked),
        pystray.MenuItem("显示/隐藏 形象", toggle_avatar),
        pystray.MenuItem("退出", quit_click),
    )
    icon = pystray.Icon("maidchan", img, "小四（右键设置）", menu)
    icon.run_detached()
    return icon
