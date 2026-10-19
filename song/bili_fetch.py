# -*- coding: utf-8 -*-
"""小四的 B 站自动找歌：
按歌名搜索 B 站 -> 选最像的完整歌曲音频 -> 下载转 mp3 -> 入歌库
用法: python bili_fetch.py <歌名>
输出: DONE <mp3路径>  或  FAIL <原因>"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

PY = sys.executable
FFMPEG = (r"D:\maid\gptsovits\GPT-SoVITS-v2pro-20250604"
          r"\runtime\ffmpeg.exe")
LIB = r"D:\maid\song\library"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
YT = [PY, "-m", "yt_dlp",
      "--user-agent", UA,
      "--add-headers", "Referer: https://www.bilibili.com/"]


def get_cookie_file():
    """匿名 cookie（过 412 风控）写成 Netscape 临时文件；1 小时内复用缓存"""
    p = os.path.join(os.environ.get("TEMP", "."), "bili_cookies.txt")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 3600:
        return p
    try:
        req = urllib.request.Request("https://www.bilibili.com/",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            setc = r.headers.get_all("Set-Cookie") or []
    except Exception:
        return p if os.path.exists(p) else None
    lines = ["# Netscape HTTP Cookie File"]
    for c in setc:
        kv = c.split(";")[0]
        if "=" not in kv:
            continue
        name, val = kv.split("=", 1)
        if name not in ("buvid3", "buvid4", "b_nut"):
            continue
        lines.append(".bilibili.com\tTRUE\t/\tFALSE\t0\t%s\t%s"
                     % (name, val))
    if len(lines) <= 1:
        return p if os.path.exists(p) else None
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return p


COOKIE_FILE = get_cookie_file()
if COOKIE_FILE:
    YT += ["--cookies", COOKIE_FILE]

query = sys.argv[1] if len(sys.argv) > 1 else ""
if not query.strip():
    print("FAIL empty query")
    raise SystemExit(2)
os.makedirs(LIB, exist_ok=True)


def run_json(args):
    r = subprocess.run(YT + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not out:
        print("DBG entries=0 rc=%s stderr=%s"
              % (r.returncode, (r.stderr or "")[-300:]), flush=True)
    return out


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip()[:80]


# 1) 搜索（完整元数据：标题/时长/BV号）；失败重试一次
def entry_url(e):
    vid = e.get("id") or ""
    if e.get("webpage_url"):
        return e["webpage_url"]
    m = re.match(r"(BV[0-9A-Za-z]+)(?:_p(\d+))?$", vid)
    if m:
        u = "https://www.bilibili.com/video/%s" % m.group(1)
        if m.group(2):
            u += "?p=%s" % m.group(2)
        return u
    if re.fullmatch(r"\d+", vid):
        return "https://www.bilibili.com/video/av%s" % vid
    return ""


def search_once():
    entries = run_json(["--no-warnings", "--skip-download", "--dump-json",
                        "bilisearch8:%s" % query])
    cands = []
    for e in entries:
        dur = e.get("duration") or 0
        title = (e.get("title") or "").strip()
        url = entry_url(e)
        if not url or not title:
            continue
        score = 0
        if 120 <= dur <= 600:
            score += 10
        elif 60 <= dur <= 900:
            score += 4
        q = re.sub(r"\s+", "", query)
        t = re.sub(r"\s+", "", title)
        if q and q in t:
            score += 8
        cands.append((score, dur, url, title))
    return cands


cands = search_once()
if not cands:
    time.sleep(4)
    try:
        os.remove(os.path.join(os.environ.get("TEMP", "."),
                               "bili_cookies.txt"))
    except OSError:
        pass
    global_cookie = get_cookie_file()  # 刷新匿名 cookie
    cands = search_once()

if not cands:
    print("FAIL no search results")
    raise SystemExit(1)
cands.sort(reverse=True)
score, dur, url, title = cands[0]
print("PICK: %s (%ds, score=%d)" % (title[:60], dur, score), flush=True)

# 2) 下载最佳音频流到临时目录
tmp_tpl = os.path.join(os.environ.get("TEMP", "."), "bili_dl_%(ext)s")
r = subprocess.run(YT + ["-f", "ba/b", "--no-playlist", "-o", tmp_tpl,
                         url], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=600)
src = None
for f in os.listdir(os.environ.get("TEMP", ".")):
    if f.startswith("bili_dl_"):
        src = os.path.join(os.environ["TEMP"], f)
if not src or not os.path.exists(src):
    print("FAIL download: %s" % (r.stderr or "")[-200:])
    raise SystemExit(1)

# 3) 转 mp3 入库
name = safe_name(title)
dst = os.path.join(LIB, name + ".mp3")
conv = subprocess.run([FFMPEG, "-y", "-i", src, "-vn", "-q:a", "2", dst],
                      capture_output=True, timeout=300)
try:
    os.remove(src)
except OSError:
    pass
if conv.returncode != 0 or not os.path.exists(dst):
    print("FAIL convert")
    raise SystemExit(1)
print("DONE %s" % dst)
