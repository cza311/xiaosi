# -*- coding: utf-8 -*-
"""小四的声音：日语TTS(edge-tts+MCI播放，带缓存与打断) + 离线语音识别(vosk) + 唤醒词 + 全局按键唤醒"""
import asyncio
import ctypes
import hashlib
import io
import json
import os
import queue
import re
import threading
import time
import wave

from maid_segmenter import (Segmenter, SegmentConfig, Packer, PackConfig,
                            looks_hallucinated, now_ms)

BASE = os.path.dirname(os.path.abspath(__file__))
TTS_FILE = os.path.join(BASE, "tts_cache.mp3")

# ---------- 多情绪参考音频（学习 Shinsekai 的机制，只学思想） ----------
# 约定：voice/refs/ 下放 <情绪>.wav，refs.json 登记 {"wav": 文件名, "text": 提示文本}；
# 缺音频的情绪条目写 {"wav": null}。用户以后放音频+改 refs.json 即生效，零代码改动。
REFS_DIR = os.path.join(BASE, "voice", "refs")


def load_refs():
    """读参考音频映射表 voice/refs/refs.json；缺失或损坏返回 None"""
    try:
        with open(os.path.join(REFS_DIR, "refs.json"), "r",
                  encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def pick_ref(emotion=""):
    """按情绪挑参考音频：refs.json 里对应条目的 wav 文件真实存在才采用；
    情绪为空/未知/条目缺失/文件不存在一律回退「默认」；连默认都没有就返回
    wav=None，由调用方回退 config 里的全局参考音频（行为与现状完全一致）。
    返回 {"wav": 绝对路径或None, "text": 参考音频提示文本}"""
    refs = load_refs() or {}

    def _entry(name):
        e = refs.get(name)
        if not isinstance(e, dict):
            return None
        wav = str(e.get("wav") or "").strip()
        if not wav:
            return None
        p = os.path.join(REFS_DIR, wav)
        try:
            if not os.path.isfile(p) or os.path.getsize(p) <= 0:
                return None
        except OSError:
            return None
        return {"wav": p, "text": str(e.get("text") or "")}

    e = _entry(str(emotion).strip()) if str(emotion or "").strip() else None
    if e is None:
        e = _entry("默认")
    return e or {"wav": None, "text": ""}


# 句末标点（切句边界）；逗号只在超长句时拿来细分
_SENT_END = re.compile(r"[。！？!?；;\n…]+|\.{3,}")
_PUNCT_ONLY = "。！？!?；;，,、….\n\r\t "  # 纯标点/空白片段视为空句丢弃
_MAX_LEN = 15  # 单句超过这个字数就在逗号处细分


def _hard_split(s, n):
    """硬切：尾巴上的标点挂回最后一块，避免切出裸标点句"""
    tail = ""
    while s and s[-1] in _PUNCT_ONLY:
        tail = s[-1] + tail
        s = s[:-1]
    if not s:
        return [tail] if tail.strip(_PUNCT_ONLY) else []
    chunks = [s[i:i + n] for i in range(0, len(s), n)]
    chunks[-1] += tail
    return chunks


def split_sentences(text):
    """流式分句：先按句末标点（。！？；!?\n 省略号）切，保序去空去纯标点；
    单句超过 15 字再在逗号处细分，仍超长（无逗号可断）就按 15 字硬切。"""
    if not text:
        return []
    t = str(text).replace("\r\n", "\n").replace("\r", "\n")
    parts, last = [], 0
    for m in _SENT_END.finditer(t):
        seg = t[last:m.end()]
        last = m.end()
        if seg.strip(_PUNCT_ONLY):
            parts.append(seg)
    if last < len(t) and t[last:].strip(_PUNCT_ONLY):
        parts.append(t[last:])
    out = []
    for seg in parts:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) <= _MAX_LEN:
            out.append(seg)
            continue
        # 超长：在逗号处细分（标点保留在前段，利于 TTS 断句语调）
        subs, buf = [], ""
        for ch in seg:
            buf += ch
            if ch in "，,":
                subs.append(buf)
                buf = ""
        if buf.strip():
            subs.append(buf)
        if len(subs) <= 1:
            out.extend(_hard_split(seg, _MAX_LEN))
            continue
        for sub in subs:
            sub = sub.strip()
            if not sub or not sub.strip(_PUNCT_ONLY):
                continue
            out.extend(_hard_split(sub, _MAX_LEN)
                       if len(sub) > _MAX_LEN else [sub])
    return out


# 括号旁白：（转过身来）（凑近）（小声）这类动作神态，只在字幕里展示，
# 绝不能被念出来——早前正是它们被合成成一串乱音（用户听到的「达拉一ki达那ko」）
_ACTION_RE = re.compile(r"[（(【\[｛{][^）)】\]｝}]{0,40}[）)】\]｝}]")


def clean_tts_text(text):
    """送 TTS 前的清洗：剥离括号旁白与装饰符号，返回真正要读的台词。
    剥空则返回空串，调用方需跳过该段（不合成空音频）。"""
    if not text:
        return ""
    t = str(text)
    t = _ACTION_RE.sub("", t)
    t = re.sub(r"[♪♫♬♩～~]", "", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip(" \t，,、;；:")


class Speaker:
    """日语音色朗读器：edge-tts 合成 mp3，MCI 播放。say() 排队阻塞式朗读。
    朗读期间 mic_muted=True，供识别线程丢弃回声。"""

    def __init__(self, voice="ja-JP-NanamiNeural", rate="+0%", volume="+0%", pitch="+0Hz",
                 on_idle=None):
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        self.on_idle = on_idle  # 整段朗读结束（合成播放队列全空）时回调
        # MCI 播放音量（0-1000）：两个引擎统一在此调音量
        try:
            pct = float(str(volume).strip().replace("%", ""))
            self._mci_vol = max(0, min(1000, int((100 + pct) / 100.0 * 1000)))
        except Exception:
            self._mci_vol = 1000
        self.mic_muted = False
        self._stream_busy = 0   # 正在流式播报的代数（0=空闲；期间 _player 让出 on_idle/静音控制权）
        self._stream_lock = threading.Lock()  # 流式/整段播放互斥（MCI 播放别名唯一）
        self._q = queue.Queue()      # 待合成队列
        self._play_q = queue.Queue()  # 已合成待播放队列（流水线：边合成边播）
        self._stop = threading.Event()
        self._gen = 0                      # 打断代数：+1 后旧播放作废
        self._lock = threading.Lock()
        self._cache_dir = os.path.join(BASE, "tts_cache")
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
        except OSError:
            self._cache_dir = None
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._player, daemon=True).start()
        # 预热：本地引擎首次推理要加载模型（十几秒），启动时先空跑一次
        threading.Thread(target=self._prewarm, daemon=True).start()

    def _prewarm(self):
        try:
            time.sleep(6)
            with open(os.path.join(BASE, "config.json"), "r",
                      encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("tts_engine") == "gptsovits":
                self._gen_gptsovits("あ、こんにちは。", cfg)
                self.log_prewarm()
        except Exception:
            pass

    def log_prewarm(self):
        try:
            with open(os.path.join(BASE, "maid_log.txt"), "a",
                      encoding="utf-8") as f:
                f.write("[%s] tts engine prewarmed\n"
                        % time.strftime("%H:%M:%S"))
        except OSError:
            pass

    def _cache_path(self, text):
        if not self._cache_dir:
            return None
        k = hashlib.md5(("%s|%s|%s|%s|%s" % (self.voice, self.rate, self.volume,
                                             self.pitch, text)).encode("utf-8")).hexdigest()
        return os.path.join(self._cache_dir, k + ".mp3")

    def interrupt(self):
        """主人插话：立刻停止当前朗读并清空待播队列"""
        with self._lock:
            self._gen += 1
        self.clear()
        try:
            while True:
                self._play_q.get_nowait()
        except queue.Empty:
            pass
        try:
            ctypes.windll.winmm.mciSendStringW("stop maidtts", None, 0, 0)
        except Exception:
            pass

    def say(self, text, alt=None):
        """按句拆分入队：第一句先合成先播，后续句子边播边合成。
        alt：同内容的中文文本。真白声线（gptsovits）不可用而回退到
        中文声线时读 alt——中文声线硬读日语会走音。"""
        text = clean_tts_text(text)
        alt = clean_tts_text(alt) if alt else None
        if not text and alt:
            text, alt = alt, None
        if not text:
            return
        ja_list = [s.strip() for s in re.split(r"[。！？!?；;\n]+", str(text))
                   if len(s.strip()) >= 2]
        if not ja_list:
            ja_list = [str(text).strip()]
        zh_list = ([s.strip() for s in re.split(r"[。！？!?；;\n]+", str(alt or ""))
                    if s.strip()] if alt else [])
        for i, seg in enumerate(ja_list):
            alt_seg = zh_list[i] if i < len(zh_list) else (
                zh_list[-1] if zh_list else "")
            self._q.put((seg, alt_seg))

    def clear(self):
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _worker(self):
        """合成线程：只负责把句子合成成音频文件，放入播放队列（流水线）"""
        while not self._stop.is_set():
            try:
                text, alt = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.mic_muted = True
                with self._lock:
                    gen = self._gen
                cp = self._cache_path(text)
                audio = cp
                if not (audio and os.path.exists(audio) and os.path.getsize(audio) > 500):
                    engine = "edge"
                    gs_cfg = None
                    try:
                        with open(os.path.join(BASE, "config.json"), "r",
                                  encoding="utf-8") as f:
                            cfg = json.load(f)
                        engine = cfg.get("tts_engine", "edge")
                        if engine == "gptsovits":
                            gs_cfg = cfg
                    except Exception:
                        pass
                    if gs_cfg is not None:
                        audio = self._gen_gptsovits(text, gs_cfg)
                    if not (audio and os.path.exists(audio)
                            and os.path.getsize(audio) > 500):
                        # 真白声线不可用或未启用：回退中文声线，改读中文内容
                        async def gen_audio():
                            import edge_tts
                            t = alt if (alt and len(alt) >= 2) else text
                            c = edge_tts.Communicate(t, self.voice, rate=self.rate,
                                                     volume=self.volume, pitch=self.pitch)
                            await c.save(TTS_FILE)
                        asyncio.run(gen_audio())
                        audio = TTS_FILE
                    # 回退产物(TTS_FILE)不入缓存：真白声线恢复后同一句话重走本地合成
                    if cp and audio and os.path.exists(audio) and audio != cp \
                            and audio != TTS_FILE:
                        try:
                            os.replace(audio, cp)
                            audio = cp
                        except OSError:
                            pass
                self._play_q.put((gen, audio))
            except Exception:
                time.sleep(0.5)

    def _player(self):
        """播放线程：从流水线取已合成音频播放；整段播完回调 on_idle"""
        while not self._stop.is_set():
            try:
                gen, audio = self._play_q.get(timeout=0.5)
            except queue.Empty:
                if self.on_idle and self._q.empty() and not self.mic_muted:
                    pass  # 队列已空且不在说话，无需动作
                continue
            try:
                with self._lock:
                    stale = gen != self._gen
                if stale:
                    continue  # 已被新命令打断，弃播
                if audio and os.path.exists(audio) and os.path.getsize(audio) > 500:
                    with self._stream_lock:  # 与流式播报互斥（MCI 播放别名唯一）
                        with self._lock:
                            stale = gen != self._gen  # 等锁期间可能已被打断
                        if not stale:
                            self._play(audio)
                    time.sleep(0.25)  # 留出回声尾音
            except Exception:
                time.sleep(0.5)
            finally:
                if self._q.empty() and self._play_q.empty() \
                        and not getattr(self, "_stream_busy", 0):
                    self.mic_muted = False
                    if self.on_idle:
                        try:
                            self.on_idle()
                        except Exception:
                            pass

    _gs_session = None  # gptsovits 连接复用（类级）

    def _gen_gptsovits(self, text, cfg, ref_wav=None, ref_text=None,
                       out_path=None):
        """本地 GPT-SoVITS 推理（api_v2 /tts），返回 wav 路径；失败返回 None。
        断路器：服务不可达时 60 秒内直接放弃，不拖慢回退。
        ref_wav/ref_text：情绪参考音频覆盖（None=沿用 config 全局参考）；
        out_path：结果落盘路径（None=gs_last.wav；流式逐句合成传独立文件防互踩）。"""
        if time.time() < getattr(self, "_gs_dead_until", 0):
            return None
        try:
            import requests
            if Speaker._gs_session is None:
                Speaker._gs_session = requests.Session()
                Speaker._gs_session.trust_env = False  # 本地 TTS 引擎：不走系统代理
            r = Speaker._gs_session.get(
                    cfg.get("gptsovits_url", "http://127.0.0.1:9880").rstrip("/") + "/tts",
                    params={
                        "text": text,
                        # 合成文本是中文、参考音频提示是日语：两个语言必须分开
                        # （旧版共用 gptsovits_lang 一键，中文被当日语读→开头固定杂音+音准差）
                        "text_lang": cfg.get("gptsovits_text_lang", "zh"),
                        "ref_audio_path": ref_wav or cfg.get("gptsovits_ref", ""),
                        "prompt_text": (ref_text if ref_text is not None
                                        else cfg.get("gptsovits_prompt", "")) or "",
                        "prompt_lang": cfg.get("gptsovits_prompt_lang", "ja"),
                        "text_split_method": "cut5",
                        "media_type": "wav",
                        "streaming": "false",
                    }, timeout=(3, 90))
            if r.status_code != 200 or len(r.content) < 1000:
                self._gs_dead_until = time.time() + 30
                return None
            self._gs_dead_until = 0
            out = out_path or os.path.join(BASE, "tts_cache", "gs_last.wav")
            with open(out, "wb") as f:
                f.write(r.content)
            return out
        except Exception:
            self._gs_dead_until = time.time() + 60
            return None

    def _play(self, path):
        mci = ctypes.windll.winmm.mciSendStringW
        # mpegvideo(MP3) 设备可能系统级失效：统一转 WAV 走 waveaudio
        if path.lower().endswith(".mp3"):
            wav = path[:-4] + ".wav"
            if not os.path.exists(wav):
                try:
                    import subprocess
                    ff = os.path.join(BASE, "gptsovits",
                                      "GPT-SoVITS-v2pro-20250604",
                                      "runtime", "ffmpeg.exe")
                    if not os.path.exists(ff):
                        ff = "ffmpeg"
                    subprocess.run([ff, "-y", "-i", path, wav],
                                   capture_output=True, timeout=60)
                except Exception:
                    pass
            if os.path.exists(wav) and os.path.getsize(wav) > 500:
                path = wav
        ftype = "waveaudio" if path.lower().endswith(".wav") else "mpegvideo"
        mci('open "%s" type %s alias maidtts' % (path, ftype), None, 0, 0)
        try:
            mci("set maidtts volume to %d" % self._mci_vol, None, 0, 0)
        except Exception:
            pass
        mci("play maidtts wait", None, 0, 0)
        mci("close maidtts", None, 0, 0)

    # ---------- 流式播报（升级B：分句边合成边播，仅 gptsovits 引擎走此路径） ----------

    def _synth_stream_sentence(self, text, ref, cfg):
        """流式路径的单句合成：带缓存（参考音频+文本做 key，唤醒应答等高频句秒回）。
        ref 为 pick_ref 的结果；wav 为空则走 config 全局参考（行为同现状）。
        返回 wav 路径；失败 None。"""
        ref_wav = (ref or {}).get("wav")
        ref_text = (ref or {}).get("text", "")
        if not ref_wav:
            return self._gen_gptsovits(text, cfg)
        cp = None
        if self._cache_dir:
            # 参考音频指纹（大小+修改时间）进 key：换参考音后旧缓存自动失效，
            # 否则换了音频内容但路径没变，旧的乱音缓存会被一直复用
            try:
                _st = os.stat(ref_wav)
                _sig = "%d_%d" % (int(_st.st_mtime), _st.st_size)
            except OSError:
                _sig = "0"
            k = hashlib.md5(("gs-stream|%s|%s|%s" % (ref_wav, _sig, text))
                            .encode("utf-8")).hexdigest()
            cp = os.path.join(self._cache_dir, k + ".wav")
            if os.path.exists(cp) and os.path.getsize(cp) > 500:
                return cp
        out = cp or os.path.join(self._cache_dir or BASE,
                                 "gs_stream_%d.wav" % threading.get_ident())
        w = self._gen_gptsovits(text, cfg, ref_wav=ref_wav,
                                ref_text=ref_text, out_path=out)
        if w and cp and os.path.abspath(w) != os.path.abspath(cp):
            try:
                os.replace(w, cp)
                w = cp
            except OSError:
                pass
        return w

    def speak_stream(self, text, emotion="", alt=None):
        """流式播报：分句后第一句合成完立刻开播，后台线程预合成后续句子，
        播完一句无缝接一句。全程 try 包裹：任何失败整体回退整段 say()，永不哑巴。
        仅 gptsovits 引擎走流式；其他引擎/空文本原样转 say()（edge 路径不动）。"""
        text = clean_tts_text(text)
        alt = clean_tts_text(alt) if alt else None
        if not text and alt:
            text, alt = alt, None
        if not text:
            return
        try:
            engine = "edge"
            try:
                with open(os.path.join(BASE, "config.json"), "r",
                          encoding="utf-8") as f:
                    engine = json.load(f).get("tts_engine", "edge")
            except Exception:
                pass
            sents = split_sentences(text)
            if engine != "gptsovits" or not sents:
                self.say(text, alt=alt)  # 非 gptsovits：原整段路径，一行不改
                return
            threading.Thread(target=self._stream_talk, daemon=True,
                             args=(sents, emotion, alt, text)).start()
        except Exception:
            try:
                self.say(text, alt=alt)  # 保守回退
            except Exception:
                pass

    def speak_segments(self, segments, emotion="", alt=None):
        """剧本式播报（学七海）：大脑输出的台词数组逐段进流式队列。
        每段本身就是短句，个别超长段再细切；首段合成完立刻开播。
        非 gptsovits 引擎/异常回退整段 say()，永不哑巴。"""
        segs = []
        try:
            segs = [clean_tts_text(s) for s in (segments or [])]
            segs = [s for s in segs if s]  # 纯旁白段（如「（转身）」）剥空后跳过
        except Exception:
            segs = []
        if not segs:
            return self.say(alt or "")
        try:
            engine = "edge"
            try:
                with open(os.path.join(BASE, "config.json"), "r",
                          encoding="utf-8") as f:
                    engine = json.load(f).get("tts_engine", "edge")
            except Exception:
                pass
            if engine != "gptsovits":
                self.say(" ".join(segs), alt=alt)
                return
            joined = "".join(segs)
            if len(joined) <= 36:
                # 短回复整段合成：一次请求出一个连续文件、一次播放，
                # 消除逐段合成的拼接卡顿（长回复仍走逐段流式保首响速度）
                threading.Thread(target=self._stream_talk, daemon=True,
                                 args=([joined], emotion, alt, joined)).start()
                return
            sents = []
            for s in segs:  # 超过一句长度的段仍细切，保口语节奏
                sents.extend(split_sentences(s) or [s])
            threading.Thread(target=self._stream_talk, daemon=True,
                             args=(sents, emotion, alt, " ".join(segs))).start()
        except Exception:
            try:
                self.say(" ".join(segs), alt=alt)  # 保守回退
            except Exception:
                pass

    def _stream_talk(self, sents, emotion, alt, full_text):
        """流式播报线程入口：同一时刻只允许一个流式在播（MCI 别名唯一），
        抢不到锁就转整段队列排队；拿到锁后交给 _stream_run 执行。"""
        if not self._stream_lock.acquire(blocking=False):
            self.say(full_text, alt=alt)
            return
        try:
            self._stream_run(sents, emotion, alt, full_text)
        finally:
            self._stream_lock.release()

    def _stream_run(self, sents, emotion, alt, full_text):
        """流式主体：首句合成→开播，同时预合成第 2..N 句按序无缝续播。
        被打断（主人插话 interrupt）静默退出；中途失败把未播部分交整段路径兜底。"""
        zh_sents = split_sentences(alt) if alt else []
        ref = pick_ref(emotion)
        with self._lock:
            gen = self._gen
        cfg = {}
        try:
            with open(os.path.join(BASE, "config.json"), "r",
                      encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        try:
            first = self._synth_stream_sentence(sents[0], ref, cfg)
            if not (first and os.path.exists(first)
                    and os.path.getsize(first) > 500):
                raise RuntimeError("首句合成失败")
        except Exception:
            self.say(full_text, alt=alt)  # 保守回退：整段合成播放，保证不哑
            return
        with self._lock:
            self._stream_busy = gen
        self.mic_muted = True
        synth_q = queue.Queue()

        def _presynth():
            """后台预合成第 2..N 句：按序入队；被打断就停止浪费合成"""
            for i, s in enumerate(sents[1:], start=2):
                try:
                    with self._lock:
                        if gen != self._gen:
                            break
                    w = self._synth_stream_sentence(s, ref, cfg)
                except Exception:
                    w = None
                synth_q.put((i, w))

        threading.Thread(target=_presynth, daemon=True).start()
        played = 0
        failed = False
        interrupted = False
        try:
            audio, idx = first, 1
            while True:
                with self._lock:
                    if gen != self._gen:
                        interrupted = True  # 主人插话：静默退出，不回退不重复
                        break
                if not (audio and os.path.exists(audio)
                        and os.path.getsize(audio) > 500):
                    failed = True  # 本句合成失败/文件损坏
                    break
                self._play(audio)
                played = idx
                if idx >= len(sents):
                    break
                try:
                    idx, audio = synth_q.get(timeout=180)
                except queue.Empty:
                    with self._lock:
                        if gen != self._gen:
                            interrupted = True
                    failed = True
                    break
        except Exception:
            failed = True
        finally:
            with self._lock:
                if self._stream_busy == gen:  # 仍是自己这代才收尾（防与新一轮流式互踩）
                    self._stream_busy = 0
                    self.mic_muted = False
                    idle_cb = self.on_idle
                else:
                    idle_cb = None
            if idle_cb:
                try:
                    idle_cb()
                except Exception:
                    pass
        if failed and not interrupted and played < len(sents):
            # 中途失败：剩余未播部分交整段路径兜底（不重复已播内容，永不哑巴）
            try:
                remain_ja = "".join(sents[played:])
                remain_zh = ("".join(zh_sents[played:])
                             if played < len(zh_sents) else None)
                self.say(remain_ja, alt=remain_zh)
            except Exception:
                pass

    def open_stream(self):
        """流式台词会话（学 Shinsekai 的 tts_queue 喂法）：大脑边生成边喂段，
        后台边合成边播——首段台词一闭合就开口，不等整条回复写完。
        返回会话对象（feed/finish/abandon）；引擎非 gptsovits 或已有流式
        在播时返回 None，上层自动回退整段 speak，行为与旧版一致。"""
        engine = "edge"
        try:
            with open(os.path.join(BASE, "config.json"), "r",
                      encoding="utf-8") as f:
                engine = json.load(f).get("tts_engine", "edge")
        except Exception:
            pass
        if engine != "gptsovits":
            return None
        if not self._stream_lock.acquire(blocking=False):
            return None  # 上一条流式还没播完：本条走整段路径排队
        return _StreamSession(self)

    def _release_stream(self, gen):
        """流式收尾：清占用、恢复监听、通知空闲（与 _stream_run 同款语义）"""
        with self._lock:
            if self._stream_busy == gen:
                self._stream_busy = 0
                self.mic_muted = False
                idle_cb = self.on_idle
            else:
                idle_cb = None
        if idle_cb:
            try:
                idle_cb()
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        self.clear()
        try:
            ctypes.windll.winmm.mciSendStringW("close maidtts", None, 0, 0)
        except Exception:
            pass


# 待机状态下，含这些关键词的话视为直接命令（不必先喊唤醒词）
COMMAND_HINTS = ("帮我", "打开", "搜索", "搜一下", "查一下", "查查", "查询",
                 "提醒我", "添加", "写个", "写份", "整理", "看看", "几点",
                 "天气", "便签", "待办", "你好", "您好", "在吗", "在不在",
                 "说话", "叫声", "测试", "讲个", "来一个", "简单介绍",
                 "唱歌", "唱个", "来首", "唱一首", "跳舞", "放歌",
                 "去学", "学习一下", "怎么赚钱", "副业")

# 常见误听纠正：识别结果先映射修正，再走唤醒/命令（提高准确性）
CORRECTIONS = {
    "吸盘": "C盘", "西盘": "C盘", "系盘": "C盘",
    "比站": "B站", "比例比": "哔哩哔哩", "逼逼": "哔哩哔哩",
    "必理": "哔哩哔哩", "比例比例": "哔哩哔哩",
    "妹抖": "女仆", "梅酱": "小四", "小司": "小四", "梅德": "小四",
    "小斯": "小四", "小思": "小四", "小希": "小四", "小西": "小四",
    "小师": "小四", "小诗": "小四", "小十": "小四", "小四酱": "小四",
}


class _StreamSession:
    """一次"边生成边播"的台词会话（学 Shinsekai tts_queue）：
    feed 喂段、finish 收尾、abandon 弃单，幂等可重复调用。
    播放线程复用 Speaker 的合成缓存与打断代数（_gen）语义：
    主人插话（interrupt 后 _gen 变化）会话静默作废，不回退不重复。"""

    def __init__(self, sp):
        self._sp = sp
        self._q = queue.Queue()
        self._done = False
        self.ok = False      # 首段成功出声（上层据此决定跳过整段 speak）
        self._closed = False  # 流锁已释放
        with sp._lock:
            self._gen = sp._gen
        self.segs = []       # 已喂入的台词段（供上层对账）

    def feed(self, seg, emo=""):
        """喂入一段台词；纯旁白剥空后自动跳过"""
        if self._done:
            return
        try:
            seg = clean_tts_text(seg)
        except Exception:
            pass
        if seg:
            self.segs.append(str(seg))
            self._q.put((str(seg), str(emo or "")))

    def finish(self):
        """正常收尾：喂完即调，队列播完自动释放"""
        if self._done:
            return
        self._done = True
        self._q.put(None)

    def abandon(self):
        """弃单：清掉未播段落立即释放（已播出去的收不回）"""
        self._done = True
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._q.put(None)

    def _work(self):
        sp = self._sp
        cfg = {}
        try:
            with open(os.path.join(BASE, "config.json"), "r",
                      encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        with sp._lock:
            if sp._gen != self._gen:  # 建会话前刚被打断：直接作废
                self._release_stream(self._gen)
                return
            sp.mic_muted = True
        try:
            while True:
                item = self._q.get(timeout=180)  # 大脑还在生成：等下一段
                if item is None:
                    break
                seg, emo = item
                with sp._lock:
                    if sp._gen != self._gen:
                        break  # 主人插话：静默退出
                    ref = pick_ref(emo)  # 每段按情绪选参考音
                w = sp._synth_stream_sentence(seg, ref, cfg)
                if not (w and os.path.exists(w) and os.path.getsize(w) > 500):
                    continue  # 单段合成失败跳过，别整段哑火
                sp._play(w)
                self.ok = True
                with sp._lock:
                    sp._stream_busy = self._gen  # 已出声：标记占用（打断时清理）
        except queue.Empty:
            pass  # 大脑迟迟不喂：放弃本会话
        except Exception:
            pass
        finally:
            self._release_stream(self._gen)
            if not self._closed:  # 归还 open_stream 持有的流锁（恰好一次）
                self._closed = True
                try:
                    sp._stream_lock.release()
                except Exception:
                    pass


class Listener:
    """离线唤醒+识别线程。事件通过 on_event(kind, text) 回调：
    kind="wake"  检测到唤醒词
    kind="utter" 唤醒状态下的一句完整话"""

    def __init__(self, model_dir, wake_words, on_event, speaker=None, log_fn=None,
                 mic_keyword="", on_heard=None, corrections=None, remote_url=None,
                 mic_cfg=None):
        import sounddevice as sd

        self.wake_words = wake_words
        self.on_event = on_event
        self.on_heard = on_heard  # 每句识别结果（含未唤醒时），用于字幕显示
        self.speaker = speaker
        self.log_fn = log_fn
        self.remote_url = (remote_url.rstrip("/") if remote_url else None)
        self.corrections = dict(CORRECTIONS)  # 基础误听纠正表，可被 config 覆盖扩展
        if corrections:
            self.corrections.update(corrections)
        self.enabled = True
        self.awake = False
        self.last_seen = time.time()
        # 切句器 + 攒批器（移植 Coopanion segmenter）：能量门限挡环境音，
        # vosk 退化为纯转写器——不再让它自己端点切句听什么收什么
        c = dict(mic_cfg or {})
        self._seg = Segmenter(SegmentConfig(
            threshold_db=float(c.get("threshold_db", -42.0)),
            min_speech_ms=float(c.get("min_speech_ms", 180)),
            dispatch_silence_ms=float(c.get("dispatch_silence_ms", 250)),
            silence_ms=float(c.get("silence_ms", 500)),
            max_utterance_ms=float(c.get("max_utterance_ms", 15000)),
            pre_roll_ms=float(c.get("pre_roll_ms", 320)),
            min_utterance_ms=float(c.get("min_utterance_ms", 350))))
        self._pack = Packer(PackConfig(
            join_gap_ms=float(c.get("join_gap_ms", 0)),
            max_hold_ms=float(c.get("max_hold_ms", 8000)),
            min_chars=int(c.get("min_chars", 2))))
        self._was_muted = False
        if self.remote_url:
            self.model = None  # 识别在 worker 进程：主进程不加载 vosk（省 3GB+）
        else:
            import vosk
            self.model = vosk.Model(model_dir)
        self._q = queue.Queue()
        self._running = True
        self.error = None
        self._mic_keyword = mic_keyword
        self._blocks = 0          # 收到的音频块计数（判断流是否存活）
        self._last_blocks = 0
        self._rms_ema = 0.0       # 音量滑动均值

        device = None
        if mic_keyword:
            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0 and \
                        mic_keyword.lower() in str(d["name"]).lower():
                    device = i
                    break
        try:
            mic_name = str(sd.query_devices(device, kind="input")["name"]) \
                if device is not None else \
                str(sd.query_devices(kind="input")["name"])
        except Exception:
            mic_name = "?"
        self._log("listener init, mic: %s (keyword=%r)" % (mic_name, mic_keyword))
        threading.Thread(target=self._recog, daemon=True).start()
        threading.Thread(target=self._pack_loop, daemon=True).start()
        self.stream = None
        try:
            self.stream = sd.InputStream(
                samplerate=16000, channels=1, dtype="int16", blocksize=800,
                callback=self._cb, device=device)
            self.stream.start()
        except Exception as e:
            if device is not None:
                # 绑定的设备可能已失效（拔插耳机后设备表会变），回退到系统默认麦
                self._log("mic %r failed (%s), fallback to default" % (mic_name, e))
                try:
                    self.stream = sd.InputStream(
                        samplerate=16000, channels=1, dtype="int16",
                        blocksize=800, callback=self._cb, device=None)
                    self.stream.start()
                    self._device = None
                    self._log("fallback mic: %s"
                              % str(sd.query_devices(kind="input")["name"]))
                except Exception as e2:
                    self.error = str(e2)
                    raise
            else:
                self.error = str(e)
                raise
        self._device = device
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _find_device(self, sd):
        """按关键字重新查找输入设备，找不到返回 None（用系统默认）"""
        if not self._mic_keyword:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and \
                    self._mic_keyword.lower() in str(d["name"]).lower():
                return i
        return None

    def _watchdog(self):
        """每 8 秒报告音量；发现音频流失效（拔插耳机）自动重连"""
        import sounddevice as sd
        while self._running:
            time.sleep(8)
            try:
                got = self._blocks - self._last_blocks
                self._last_blocks = self._blocks
                self._log("mic stat: +%d blocks/2s, level=%d"
                          % (got, int(self._rms_ema)))
                if got == 0 and not getattr(self, "_no_reopen", False):
                    idx = self._find_device(sd)
                    try:
                        old = self.stream
                        self.stream = sd.InputStream(
                            samplerate=16000, channels=1, dtype="int16",
                            blocksize=800, callback=self._cb, device=idx)
                        self.stream.start()
                        self._device = idx
                        try:
                            old.stop(); old.close()
                        except Exception:
                            pass
                        nm = str(sd.query_devices(idx, kind="input")["name"]) \
                            if idx is not None else \
                            str(sd.query_devices(kind="input")["name"])
                        self._blocks = 0
                        self._last_blocks = 0
                        self._log("mic reconnected: %s" % nm)
                    except Exception as e:
                        self._log("mic reconnect failed: %s" % e)
            except Exception:
                pass

    def _cb(self, indata, frames, t, status):
        self._blocks += 1
        try:
            import numpy as np
            a = indata[:, 0].astype(np.float32)
            rms = float(np.sqrt((a * a).mean()))
            self._rms_ema = self._rms_ema * 0.6 + rms * 0.4
        except Exception:
            pass
        try:
            if self.speaker is not None and self.speaker.mic_muted:
                # 她自己在说话：清掉切句器状态，自家声音不进识别链路
                self._seg.reset()
                return
            for utt in self._seg.push(indata):
                self._q.put(utt["pcm"])  # 整句 PCM 入队，识别线程逐句转写
        except Exception as e:
            self._log("seg push failed: %s" % e)

    def _pack_loop(self):
        """攒批发车循环：上游（切句器）还在收音/等收尾静音时按住不动，
        静音窗走完才把攒着的识别结果并成一条投递——转写提前，发车不提前。"""
        while self._running:
            time.sleep(0.05)
            try:
                muted = self.speaker is not None and self.speaker.mic_muted
                if muted and not self._was_muted:
                    self._pack.take()   # 她开口前的残留碎句直接丢弃
                    self._seg.reset()
                self._was_muted = muted
                if muted:
                    continue
                hold = self._seg.active or self._seg.settle_remaining_ms > 0
                text = self._pack.due(now_ms(), hold)
                if text:
                    self._on_final(text)
            except Exception:
                pass

    def _recog(self):
        """识别跑在本进程：队列里拿的是切句器交出的整句 PCM，逐句转写"""
        if self.remote_url:
            return self._recog_remote()
        import vosk
        rec = vosk.KaldiRecognizer(self.model, 16000)
        rec.SetWords(False)
        while self._running:
            try:
                pcm = self._q.get(timeout=1)
            except queue.Empty:
                continue
            try:
                text = ""
                if rec.AcceptWaveform(pcm):
                    text = json.loads(rec.Result()).get("text", "")
                tail = json.loads(rec.FinalResult()).get("text", "")
                text = ("%s %s" % (text, tail)).strip()
            except Exception:
                continue
            if text.strip():
                self._on_final(text)

    def _recog_remote(self):
        """识别跑在 voice_worker 进程：整句 PCM 包成 WAV POST /rec 逐句转写
        （主进程零 vosk 内存；/rec 每句新建识别器，天然无跨句状态污染）"""
        import requests
        sess = requests.Session()
        sess.trust_env = False  # 本机内部通信：绝不走系统代理（Watt 等会劫持 127.0.0.1）
        url = self.remote_url + "/rec"
        fails = 0
        while self._running:
            try:
                pcm = self._q.get(timeout=1)
            except queue.Empty:
                continue
            buf = io.BytesIO()
            try:
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(pcm)
            except Exception:
                continue
            try:
                r = sess.post(url, data=buf.getvalue(), timeout=(3, 30))
                if r.status_code != 200:
                    raise RuntimeError("http %s" % r.status_code)
                fails = 0
                text = (r.json().get("text") or "").strip()
            except Exception:
                fails += 1
                if fails >= 40:
                    self._log("voice worker unreachable, retry in 10s")
                    time.sleep(10)  # 不放弃：等 worker 恢复后自动续上
                    fails = 0
                continue
            if text:
                self._on_final(text)

    def _on_final(self, text):
        if not text or not self.enabled:
            return
        self._log("heard: %s (awake=%s)" % (text, self.awake))
        if self.speaker is not None and self.speaker.mic_muted:
            return  # 她自己在说话，忽略
        text = text.replace(" ", "")  # vosk 中文按字分隔，去掉空格
        if looks_hallucinated(text):
            self._log("drop hallucination: %s" % text)
            return  # 识别器在纯噪声上的训练语料尾噪（字幕/谢谢观看之类），不是人话
        fixed = self._apply_corrections(text)
        if fixed != text:
            self._log("corrected: %s -> %s" % (text, fixed))
            text = fixed
        if not self.awake:
            if self._is_wake(text):
                if self.on_heard:
                    try:
                        self.on_heard(text)
                    except Exception:
                        pass
                self.awake = True
                self.last_seen = time.time()
                self._fire("wake", text)
                # 唤醒词后面跟着的内容，直接作为命令执行
                rest = text
                for w in self.wake_words:
                    if w in rest:
                        rest = rest.replace(w, "", 1)
                rest = rest.strip()
                if len(rest) >= 2:
                    self._fire("utter", text)
            elif len(text) >= 4 and any(h in text for h in COMMAND_HINTS):
                # 待机时听到明确指令，直接当命令处理（免唤醒）
                if self.on_heard:
                    try:
                        self.on_heard(text)
                    except Exception:
                        pass
                self.awake = True
                self.last_seen = time.time()
                self._fire("wake", text)
                self._fire("utter", text)
        else:
            if self.on_heard:
                try:
                    self.on_heard(text)
                except Exception:
                    pass
            self.last_seen = time.time()
            self._fire("utter", text)

    def _is_wake(self, text):
        return any(w in text for w in self.wake_words)

    def _apply_corrections(self, text):
        """按纠正表替换误听词（长词优先替换，避免被短词部分覆盖）"""
        for k in sorted(self.corrections, key=len, reverse=True):
            if k in text:
                text = text.replace(k, self.corrections[k])
        return text

    def _log(self, *args):
        if self.log_fn:
            try:
                self.log_fn(*args)
            except Exception:
                pass

    def _fire(self, kind, text):
        try:
            self.on_event(kind, text)
        except Exception:
            pass

    def sleep(self):
        self.awake = False

    def wake(self):
        self.awake = True
        self.last_seen = time.time()

    def watch(self, timeout, on_sleep):
        """对话超时自动回待机"""
        def loop():
            while self._running:
                time.sleep(3)
                if self.awake and time.time() - self.last_seen > timeout:
                    self.awake = False
                    self._fire("sleep", "")
        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self._running = False
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


class KeyWake:
    """全局键盘唤醒：按右 Alt 键触发回调。
    使用 Windows 低级键盘钩子，全系统任意界面下都有效。"""

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    VK_ALT = (0xA5,)  # 仅右 Alt（左 Alt 留给 Alt+Tab / Alt+F4 等组合键）

    def __init__(self, on_wake, window=0.7, log_fn=None):
        from ctypes import wintypes
        self.on_wake = on_wake
        self.window = window
        self.log_fn = log_fn
        self._c_time = 0.0
        self._ct = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)]
        self._KBDLL = KBDLLHOOKSTRUCT
        self._proc = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        c, wt = self._ct, self._wintypes
        # wintypes 没有 LRESULT，它与 LPARAM 同为 LONG_PTR
        HOOKPROC = c.WINFUNCTYPE(wt.LPARAM, c.c_int, wt.WPARAM, wt.LPARAM)
        self._proc = HOOKPROC(self._hookproc)
        # 64 位下必须显式声明句柄类型，否则模块句柄被截断导致钩子安装失败
        self._kernel32.GetModuleHandleW.restype = wt.HMODULE
        self._kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        self._user32.SetWindowsHookExW.restype = wt.HHOOK
        self._user32.SetWindowsHookExW.argtypes = [c.c_int, HOOKPROC, wt.HMODULE, wt.DWORD]
        self._user32.CallNextHookEx.restype = wt.LPARAM
        self._user32.CallNextHookEx.argtypes = [wt.HHOOK, c.c_int, wt.WPARAM, wt.LPARAM]
        self._hook = self._user32.SetWindowsHookExW(
            self.WH_KEYBOARD_LL, self._proc,
            self._kernel32.GetModuleHandleW(None), 0)
        if self.log_fn:
            try:
                if self._hook:
                    self.log_fn("keyboard hook installed")
                else:
                    self.log_fn("keyboard hook FAILED, WinError", self._kernel32.GetLastError())
            except Exception:
                pass
        msg = wt.MSG()
        while self._user32.GetMessageW(c.byref(msg), None, 0, 0) > 0:
            pass

    def _hookproc(self, ncode, wparam, lparam):
        try:
            if wparam == self.WM_KEYDOWN:
                s = self._ct.cast(lparam, self._ct.POINTER(self._KBDLL)).contents
                if s.vkCode in self.VK_ALT:
                    try:
                        self.on_wake()
                    except Exception:
                        pass
        except Exception:
            pass
        return self._user32.CallNextHookEx(None, ncode, wparam, lparam)


class MouseWake:
    """全局鼠标唤醒：按下中键（滚轮键）触发回调。
    低级鼠标钩子只监听不拦截——中键在浏览器关标签、IDE 里的
    原有功能完全不受影响。全系统任意界面下都有效。"""

    WH_MOUSE_LL = 14
    WM_MBUTTONDOWN = 0x0207

    def __init__(self, on_wake, log_fn=None):
        from ctypes import wintypes
        self.on_wake = on_wake
        self.log_fn = log_fn
        self._ct = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]
        self._MSLL = MSLLHOOKSTRUCT
        self._proc = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        c, wt = self._ct, self._wintypes
        HOOKPROC = c.WINFUNCTYPE(wt.LPARAM, c.c_int, wt.WPARAM, wt.LPARAM)
        self._proc = HOOKPROC(self._hookproc)
        self._kernel32.GetModuleHandleW.restype = wt.HMODULE
        self._kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        self._user32.SetWindowsHookExW.restype = wt.HHOOK
        self._user32.SetWindowsHookExW.argtypes = [c.c_int, HOOKPROC, wt.HMODULE, wt.DWORD]
        self._user32.CallNextHookEx.restype = wt.LPARAM
        self._user32.CallNextHookEx.argtypes = [wt.HHOOK, c.c_int, wt.WPARAM, wt.LPARAM]
        self._hook = self._user32.SetWindowsHookExW(
            self.WH_MOUSE_LL, self._proc,
            self._kernel32.GetModuleHandleW(None), 0)
        if self.log_fn:
            try:
                if self._hook:
                    self.log_fn("mouse hook installed (middle button)")
                else:
                    self.log_fn("mouse hook FAILED, WinError", self._kernel32.GetLastError())
            except Exception:
                pass
        msg = wt.MSG()
        while self._user32.GetMessageW(c.byref(msg), None, 0, 0) > 0:
            pass

    def _hookproc(self, ncode, wparam, lparam):
        try:
            if wparam == self.WM_MBUTTONDOWN:
                self._ct.cast(lparam, self._ct.POINTER(self._MSLL))  # 结构校验
                try:
                    self.on_wake()
                except Exception:
                    pass
        except Exception:
            pass
        return self._user32.CallNextHookEx(None, ncode, wparam, lparam)
