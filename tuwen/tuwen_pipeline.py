# -*- coding: utf-8 -*-
"""小四的小说推文流水线：
小说片段.txt -> LLM切分+配图提示词 -> 文生图(竖屏) -> 真白TTS配音 ->
PIL烤字幕 -> ffmpeg(Ken Burns+拼接) -> 竖屏推文视频

用法: python tuwen_pipeline.py <小说片段.txt> [标题]
输出: D:\\maid\\tuwen\\output\\<标题>.mp4
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
MAID = r"D:\maid"
OUT_DIR = os.path.join(BASE, "output")
PROJ_DIR = os.path.join(BASE, "projects")
FFMPEG = os.path.join(MAID, "gptsovits", "GPT-SoVITS-v2pro-20250604",
                      "runtime", "ffmpeg.exe")
CONFIG = json.load(open(os.path.join(MAID, "config.json"), encoding="utf-8"))
TTS_URL = CONFIG.get("gptsovits_url", "http://127.0.0.1:9880").rstrip("/")
TTS_REF = CONFIG.get("gptsovits_ref", "")
TTS_PROMPT = CONFIG.get("gptsovits_prompt", "")
LLM_BASE = CONFIG.get("api_base", "")
LLM_KEY = CONFIG.get("api_key", "")
LLM_MODEL = CONFIG.get("model_free") or CONFIG.get("model", "glm-4.5-flash")
T2I = "https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image"

W, H, FPS = 1080, 1920, 30
STYLE = ("anime illustration, soft cinematic lighting, detailed background, "
         "vertical composition, high quality, no text, no watermark")
FONT = r"C:\Windows\Fonts\msyh.ttc"
log = lambda *a: print(time.strftime("%H:%M:%S"), *a, flush=True)

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, body, timeout=60):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + LLM_KEY},
        method="POST")
    with DIRECT.open(req, timeout=timeout) as r:
        return json.load(r)


# ============================================================
# 1. LLM 切分 + 配图提示词
# ============================================================
def plan_segments(text):
    sys_p = ("你是短视频编导。把小说片段改编成口播推文脚本。"
             "narration=口播文本(保留原文风味,去掉引号/书名号/不适合读的符号,每段15~45字)；"
             "image_prompt=中文画面描述(该段的人物/场景/情绪/光线,40字以内,供AI绘图用)。"
             "只输出JSON数组,如[{\"narration\":\"...\",\"image_prompt\":\"...\"}]")
    r = http_json(LLM_BASE.rstrip("/") + "/chat/completions", {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": sys_p},
                     {"role": "user", "content": "小说片段：\n" + text}],
        "temperature": 0.8, "max_tokens": 3000}, timeout=120)
    raw = r["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", raw, re.S)
    segs = json.loads(m.group())
    out = []
    for s in segs[:18]:
        n = str(s.get("narration", "")).strip()
        p = str(s.get("image_prompt", "")).strip()
        if n:
            out.append({"narration": n, "image_prompt": p or "quiet scene"})
    return out


# ============================================================
# 2. TTS（真白声线，交叉语言读中文）
# ============================================================
def tts(text, wav_path):
    from urllib.parse import quote
    q = ("/tts?text=%s&text_lang=zh&ref_audio_path=%s&prompt_text=%s&"
         "prompt_lang=ja&text_split_method=cut5&media_type=wav&streaming=false"
         % (quote(text, safe=""), quote(TTS_REF, safe=""),
            quote(TTS_PROMPT, safe="")))
    with DIRECT.open(TTS_URL + q, timeout=150) as r:
        data = r.read()
    if len(data) < 1000:
        raise RuntimeError("TTS 返回异常 (%d bytes)" % len(data))
    with open(wav_path, "wb") as f:
        f.write(data)
    with wave.open(wav_path, "rb") as w:
        return w.getnframes() / w.getframerate()


def tts_wav_len(path):
    with wave.open(path, "rb") as w:
        return w.getnframes(), w.getframerate(), w.getsampwidth(), w.getnchannels()


# ============================================================
# 3. 文生图（智谱 CogView，与大脑同一把钥匙，国内直连）
# ============================================================
STYLE_CN = "，动漫插画风格，竖屏构图，电影感光影，高清细节，画面中不要出现任何文字"


def gen_image(prompt, jpg_path, tries=6):
    for i in range(tries):
        try:
            body = {"model": "cogview-3-flash",
                    "prompt": str(prompt)[:900] + STYLE_CN,
                    "size": "768x1344"}
            req = urllib.request.Request(
                LLM_BASE.rstrip("/") + "/images/generations",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + LLM_KEY},
                method="POST")
            with DIRECT.open(req, timeout=180) as r:
                resp = json.load(r)
            url = resp["data"][0]["url"]
            with DIRECT.open(url, timeout=120) as r:
                data = r.read()
            if len(data) > 20000:
                with open(jpg_path, "wb") as f:
                    f.write(data)
                return True
        except Exception as e:
            s = str(e)
            wait = 16 if "429" in s else 4
            log("  img retry %d (%s)..." % (i, s[:40]))
            time.sleep(wait)
    return False


# ============================================================
# 4. 烤字幕
# ============================================================
def bake_subtitle(jpg, text, out_jpg, lines_max=3):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    im = Image.open(jpg).convert("RGB")
    # 先裁掉底部 7%（去掉"AI生成"角标），再 cover 裁剪
    im = im.crop((0, 0, im.width, int(im.height * 0.93)))
    r = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * r), round(im.height * r)))
    x0 = (im.width - W) // 2
    y0 = (im.height - H) // 2
    im = im.crop((x0, y0, x0 + W, y0 + H))
    # 底部渐变压暗
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        grad.putpixel((0, y), int(min(1, max(0, (y - H * 0.62) / (H * 0.38))) * 160))
    grad = grad.resize((W, H))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    im = Image.composite(black, im, grad)

    draw = ImageDraw.Draw(im)
    size = 58
    try:
        font = ImageFont.truetype(FONT, size)
    except Exception:
        font = ImageFont.load_default()
    maxw = W - 160
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > maxw or ch == "\n":
            lines.append(cur)
            cur = "" if ch == "\n" else ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    lines = lines[:lines_max]
    if not lines:
        im.save(out_jpg, quality=90)
        return
    lh = size + 14
    box_h = lh * len(lines) + 48
    box_y = int(H * 0.80) - box_h // 2
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(layer)
    d2.rounded_rectangle([80, box_y, W - 80, box_y + box_h],
                         radius=28, fill=(0, 0, 0, 150))
    layer = layer.filter(ImageFilter.GaussianBlur(2))
    im = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")
    draw = ImageDraw.Draw(im)
    y = box_y + 24
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        draw.text(((W - tw) / 2, y), ln, font=font, fill=(255, 255, 255))
        y += lh
    im.save(out_jpg, quality=90)


# ============================================================
# 5. 合成
# ============================================================
def pick_encoder():
    """实测选择 h264 编码器：编 4 帧测试帧，谁成功用谁"""
    from PIL import Image
    test_img = os.path.join(PROJ_DIR, "_enc_test.jpg")
    os.makedirs(PROJ_DIR, exist_ok=True)
    Image.new("RGB", (W, H), (10, 10, 10)).save(test_img)
    for name in ("h264_nvenc", "libx264", "h264_mf", "h264_qsv", "h264_amf"):
        p = subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error",
             "-i", test_img, "-frames:v", "4", "-c:v", name,
             "-b:v", "3M", os.path.join(PROJ_DIR, "_enc_test.mp4")],
            capture_output=True)
        if p.returncode == 0 and os.path.getsize(
                os.path.join(PROJ_DIR, "_enc_test.mp4")) > 500:
            try:
                os.remove(test_img)
                os.remove(os.path.join(PROJ_DIR, "_enc_test.mp4"))
            except OSError:
                pass
            return name
    raise RuntimeError("没有可用的 h264 编码器")
ENCODER = None


def run_ff(args, **kw):
    p = subprocess.run([FFMPEG, "-y", "-loglevel", "error"] + args,
                       capture_output=True, **kw)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: " + p.stderr.decode("utf-8", "ignore")[-400:])


def concat_wavs(wavs, out, pad=0.28):
    n0, fr, sw, ch = tts_wav_len(wavs[0])
    pad_frames = int(fr * pad)
    with wave.open(out, "wb") as o:
        o.setnchannels(ch)
        o.setsampwidth(sw)
        o.setframerate(fr)
        for i, wpath in enumerate(wavs):
            n, f2, s2, c2 = tts_wav_len(wpath)
            with wave.open(wpath, "rb") as w:
                o.writeframes(w.readframes(n))
            if i < len(wavs) - 1:
                o.writeframes(b"\x00" * (pad_frames * sw * ch))


def make_video(proj, title):
    global ENCODER
    if ENCODER is None:
        ENCODER = pick_encoder()
        log("encoder: %s" % ENCODER)
    segs = json.load(open(os.path.join(proj, "script.json"), encoding="utf-8"))
    n = len(segs)
    dur = []
    log("TTS %d segments..." % n)
    for i, s in enumerate(segs):
        wav = os.path.join(proj, "seg_%02d.wav" % i)
        d = tts(s["narration"], wav)
        dur.append(d)
        log("  seg%02d %.1fs %s" % (i, d, s["narration"][:18]))
    if not dur:
        raise RuntimeError("TTS 全部失败：请确认 GPT-SoVITS api_v2 (9880) 在运行")

    log("images %d (sequential)..." % n)
    ok = []
    for i in range(n):
        good = gen_image(segs[i]["image_prompt"],
                         os.path.join(proj, "img_%02d.jpg" % i))
        ok.append(good)
        log("  img%02d %s" % (i, "ok" if good else "FAIL"))
        if good:
            time.sleep(2)  # 避免 CogView 限流
    for i in range(n):
        if not ok[i]:  # 用相邻成功图兜底
            src = next((ok[j] and os.path.join(proj, "img_%02d.jpg" % j)
                        for j in range(n) if ok[j]), None)
            if src:
                import shutil
                shutil.copy2(src, os.path.join(proj, "img_%02d.jpg" % i))
                ok[i] = True
    if not any(ok):
        raise RuntimeError("图片全部生成失败")

    log("subtitles...")
    for i in range(n):
        if not ok[i]:
            continue
        bake_subtitle(os.path.join(proj, "img_%02d.jpg" % i),
                      segs[i]["narration"],
                      os.path.join(proj, "sub_%02d.jpg" % i))

    log("encode segments...")
    parts = []
    for i in range(n):
        if not ok[i]:
            continue
        frames = max(2, round(dur[i] * FPS))
        seg = os.path.join(proj, "vid_%02d.mp4" % i)
        run_ff(["-i", os.path.join(proj, "sub_%02d.jpg" % i), "-vf",
                "zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':d=%d:s=%dx%d:fps=%d,format=yuv420p"
                % (frames, W, H, FPS),
                "-frames:v", str(frames),
                "-c:v", ENCODER, "-b:v", "3M", seg], cwd=proj)
        parts.append(seg)

    log("concat...")
    lst = os.path.join(proj, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '%s'\n" % os.path.basename(p))
    allv = os.path.join(proj, "video_all.mp4")
    run_ff(["-f", "concat", "-safe", "0", "-i", "list.txt",
            "-c", "copy", "video_all.mp4"], cwd=proj)

    wavs = [os.path.join(proj, "seg_%02d.wav" % i)
            for i in range(n) if ok[i]]
    alla = os.path.join(proj, "audio_all.wav")
    concat_wavs(wavs, alla)

    os.makedirs(OUT_DIR, exist_ok=True)
    final = os.path.join(OUT_DIR, title + ".mp4")
    run_ff(["-i", "video_all.mp4", "-i", "audio_all.wav",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-shortest", final], cwd=proj)
    return final


def main():
    src = sys.argv[1]
    title = (sys.argv[2] if len(sys.argv) > 2
             else os.path.splitext(os.path.basename(src))[0])
    title = re.sub(r'[\\/:*?"<>|]', "_", title)[:40]
    text = open(src, encoding="utf-8", errors="ignore").read().strip()
    if len(text) < 30:
        raise RuntimeError("文本太短")
    proj = os.path.join(PROJ_DIR, title)
    os.makedirs(proj, exist_ok=True)

    log("LLM 分段+提示词 (%d 字)..." % len(text))
    segs = plan_segments(text)
    json.dump(segs, open(os.path.join(proj, "script.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
    log("got %d segments" % len(segs))

    final = make_video(proj, title)
    log("DONE %s (%.1f MB)" % (final, os.path.getsize(final) / 1048576))


if __name__ == "__main__":
    main()
