# -*- coding: utf-8 -*-
r"""小四翻唱流水线：一首歌 -> UVR5 分离人声 -> RVC 真白声线 -> 混音 -> 成品
用法: runtime\python.exe sing_pipeline.py 歌曲.wav [成品输出路径] [音调半移数]
输出默认: D:\maid\song\output\<歌名>_mashiro.wav（真白声线人声+原曲伴奏）"""
import os
import sys
import glob
import time

S = os.path.dirname(os.path.abspath(__file__))
RVC = os.path.join(S, "RVC2023")
OUT = os.path.join(os.path.dirname(os.path.dirname(S)),
                   "小四", "作品", "翻唱歌曲")  # 成品直接进小四的仓库
os.makedirs(OUT, exist_ok=True)

ARGS = sys.argv[1:]
if len(ARGS) < 1:
    print("usage: sing_pipeline.py song_file [output] [pitch]")
    raise SystemExit(2)
song = os.path.abspath(ARGS[0])
pitch = int(ARGS[2]) if len(ARGS) > 2 else 0
name = os.path.splitext(os.path.basename(song))[0]
final = os.path.abspath(ARGS[1]) if len(ARGS) > 1 else \
    os.path.join(OUT, "%s_mashiro.wav" % name)

RUNTIME = r"D:\maid\gptsovits\GPT-SoVITS-v2pro-20250604\runtime\python.exe"
t0 = time.time()


def run(cmd):
    import subprocess
    print(">>", " ".join(cmd), flush=True)
    r = subprocess.run(cmd).returncode
    if r != 0:
        raise RuntimeError("step failed: %s" % cmd)


# 1) UVR5 分离人声/伴奏（agg 0.65：比 0.5 更干净地去伴奏残留）
# 已有产物直接复用：重学/重唱同一首时免跑 3-6 分钟分离（人声伴奏不会变）
work = os.path.join(S, "work", name)
os.makedirs(os.path.join(work, "vocal"), exist_ok=True)
os.makedirs(os.path.join(work, "ins"), exist_ok=True)
vocals = glob.glob(os.path.join(work, "vocal", "*.wav"))
inss = glob.glob(os.path.join(work, "ins", "*.wav"))
if not (vocals and inss):
    uvr5_test = os.path.join(os.environ.get("TEMP", "."), "sing_uvr5.py")
    with open(uvr5_test, "w", encoding="utf-8") as f:
        f.write("""
import os, sys
os.chdir(r"%(RVC)s"); sys.path.insert(0, os.getcwd())
from infer_uvr5 import _audio_pre_
pre = _audio_pre_(0.65, "uvr5_weights/HP2_all_vocals.pth", "cuda:0", False)
pre._path_audio_(r"%(SONG)s", r"%(INS)s", r"%(VOC)s", format="wav")
print("UVR5 OK")
""" % {"RVC": RVC, "SONG": song,
       "INS": os.path.join(work, "ins"), "VOC": os.path.join(work, "vocal")})
    run([RUNTIME, uvr5_test])
    vocals = glob.glob(os.path.join(work, "vocal", "*.wav"))
    inss = glob.glob(os.path.join(work, "ins", "*.wav"))
else:
    print("UVR5 reuse existing work outputs", flush=True)

if not vocals or not inss:
    raise RuntimeError("UVR5 output missing")
vocal, ins = vocals[0], inss[0]

# 1.5) 自动移调：真白是女声，检测原曲人声音域，男声整体升 12 半音
import numpy as np
import librosa
if pitch == 0:
    try:
        y, _ = librosa.load(vocal, sr=16000, mono=True, duration=120)
        f0v, _, _ = librosa.pyin(y, fmin=65, fmax=600, sr=16000)
        f0v = f0v[~np.isnan(f0v)]
        med = float(np.median(f0v)) if len(f0v) else 0.0
        if med and med < 160.0:
            pitch = 12
            print("AUTO-PITCH: median f0=%.0f Hz -> +12 semitones" % med,
                  flush=True)
        else:
            print("AUTO-PITCH: median f0=%.0f Hz -> keep 0" % med, flush=True)
    except Exception as e:
        print("AUTO-PITCH detect failed (%r), keep 0" % e, flush=True)

# 2) RVC 真白声线转换（人声；f0 用 crepe，音准比 pm 稳，电音感显著降低）
vocal_conv = os.path.join(work, "vocal_mashiro.wav")
run([RUNTIME, os.path.join(RVC, "rvc_infer.py"), vocal, vocal_conv,
     str(pitch), "crepe"])

# 3) 混音：转换后的人声 + 伴奏
mix = os.path.join(os.environ.get("TEMP", "."), "sing_mix.py")
with open(mix, "w", encoding="utf-8") as f:
    f.write("""
import numpy as np, soundfile as sf, librosa
v, _ = librosa.load(r"%(VOC)s", sr=44100, mono=True)
a, _ = librosa.load(r"%(INS)s", sr=44100, mono=True)
n = min(len(v), len(a))
v, a = v[:n], a[:n]
pv = np.abs(v).max() / 0.9
if pv > 1: v /= pv
mix = 0.95 * v + 0.75 * a
peak = np.abs(mix).max()
if peak > 0.99: mix /= peak
sf.write(r"%(OUT)s", mix, 44100)
print(f"MIX OK {n/44100:.1f} s")
""" % {"VOC": vocal_conv, "INS": ins, "OUT": final})
run([RUNTIME, mix])

print("DONE %s in %.0f s" % (final, time.time() - t0), flush=True)
