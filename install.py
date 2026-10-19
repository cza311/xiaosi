# -*- coding: utf-8 -*-
"""小四一键安装：Python 依赖 + vosk 模型 + GPT-SoVITS 引导 + config 生成 + 开机自启
用法: py install.py"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
PYPI = "https://pypi.tuna.tsinghua.edu.cn/simple"
VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-cn-0.22.zip"
VOSK_DIR = os.path.join(BASE, "vosk-model-cn-0.22")
GSV_BAT_NAME = "GPT-SoVITS-v2pro-20250604"  # bat 里默认的整合包目录名


def run(cmd):
    subprocess.run(cmd, shell=True, cwd=BASE, check=False)


def download(url, dst):
    """支持断点续传的下载（.part 暂存）"""
    part = dst + ".part"
    done = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(url)
    if done:
        req.add_header("Range", "bytes=%d-" % done)
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
        total = int(r.headers.get("Content-Length", 0)) + done
        got = done
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                sys.stdout.write("\r  %.0f/%.0f MB (%.0f%%)"
                                 % (got / 2**20, total / 2**20, got * 100 / total))
                sys.stdout.flush()
    shutil.move(part, dst)
    print()


def main():
    if sys.version_info < (3, 10):
        print("需要 Python 3.10+，当前 %s" % sys.version)
        return 1
    print("== 1/5 Python 依赖 ==")
    run('pip install -r requirements.txt -i "%s"' % PYPI)

    print("== 2/5 vosk 中文语音模型（1.9GB，已存在则跳过）==")
    if os.path.isdir(VOSK_DIR):
        print("  已存在，跳过")
    else:
        z = os.path.join(BASE, "vosk-model-cn-0.22.zip")
        if not os.path.exists(z):
            download(VOSK_URL, z)
        with zipfile.ZipFile(z) as f:
            f.extractall(BASE)
        os.remove(z)
        print("  解压完成")

    print("== 3/5 GPT-SoVITS 整合包（约 20GB，官方渠道）==")
    gs_root = os.path.join(BASE, "gptsovits")
    found = None
    if os.path.isdir(gs_root):
        for n in os.listdir(gs_root):
            if n.startswith("GPT-SoVITS") and os.path.exists(
                    os.path.join(gs_root, n, "api_v2.py")):
                found = n
                break
    if found:
        print("  已检测到整合包: %s" % found)
        if found != GSV_BAT_NAME:
            for bat in ["启动女仆酱.bat"]:
                p = os.path.join(BASE, bat)
                if os.path.exists(p):
                    t = open(p, encoding="utf-8", errors="replace").read()
                    open(p, "w", encoding="utf-8", errors="replace").write(
                        t.replace(GSV_BAT_NAME, found))
                    print("  已把 %s 的目录名改写为 %s" % (bat, found))
    else:
        print("  未检测到。请手动下载官方整合包：")
        print("    https://github.com/RVC-Boss/GPT-SoVITS/releases")
        print("  解压到  gptsovits\\  目录下（保留 GPT-SoVITS-* 目录名），")
        print("  并把 启动女仆酱.bat 里的目录名改成实际目录名。")
        print("  （不装也能跑：config.json 里 tts_engine 改为 edge 用免费声线）")

    print("== 4/5 生成 config.json ==")
    cfg_path = os.path.join(BASE, "config.json")
    if os.path.exists(cfg_path):
        print("  已存在，跳过")
    else:
        cfg = json.load(open(os.path.join(BASE, "config.example.json"),
                             encoding="utf-8"))
        cfg["api_key"] = input("  粘贴智谱 API Key（回车跳过，稍后手填）: ").strip() \
            or cfg["api_key"]
        mn = input("  怎么称呼你？[主人]: ").strip()
        if mn:
            cfg["master_name"] = mn
        ct = input("  你在哪个城市（天气用，回车跳过）: ").strip()
        if ct:
            cfg["city"] = ct
        json.dump(cfg, open(cfg_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("  已写入 config.json")

    if not os.path.exists(os.path.join(BASE, "avatar.png")):
        print("  提示: 还没有立绘。放一张你的角色 png 为 avatar.png 即可出门。")

    print("== 5/5 开机自启（可选）==")
    if input("  注册开机自启？[y/N]: ").strip().lower() == "y":
        run("开机自启.bat")

    print("\n安装完成！双击 启动女仆酱.bat 出门吧。")
    print("唱翻声线等进阶配置见 README.md。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
