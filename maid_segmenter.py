# -*- coding: utf-8 -*-
"""麦克风切句器 —— 逐行移植自 Coopanion cortico-world-desktop-pet/src/asr/segmenter.ts + result.ts
（AGPL-3.0，小四同为开源项目，协议兼容）。

原来小四的收音链路是：声卡 250ms 一块 → 队列 → vosk 自己端点切句。
vosk 端点对能量一视同仁：电视声、键盘声、鼠标点击全都照单全收，
识别出什么就 heard 什么——这是"背景音误应答"的根源。

移植后的链路：声卡小块 → **Segmenter**（能量门限 + 起说时长 + 收尾静音 状态机，
纯逻辑不碰声卡）→ 整句 PCM → vosk 只做转写 → **Packer** 把连续短句攒成一条再投递。

两级静音门限，分开的是两件不同的事：
- dispatchSilenceMs（短）：停这么久就把手上这段**送去转写**——转写跑在静音窗里，
  而不是排在它后面；
- silenceMs（长）：停这么久才算**这句真的说完了**——Packer 据此决定发不发车。
  人在窗口里接着说，后半段照旧接上，两半并成一条。
  所以短门限不会把句子切碎：它只把转写提前，不把发车提前。
"""
import re
import time

try:
    import numpy as _np
except Exception:  # pragma: no cover - numpy 理论上必在（libs），纯逻辑兜底
    _np = None


# ---------------------------------------------------------------------------
# Segmenter：能量门限 + 起说时长 + 收尾静音 的状态机
# ---------------------------------------------------------------------------

class SegmentConfig:
    """判据全是可调参数，参数对不对只有拿录音的形状去验才说得清。默认值 = SEG_DEFAULTS"""

    def __init__(self,
                 threshold_db=-42.0,      # 判为"有人在说"的电平门槛(dBFS)，安静房间底噪常在 -60 上下
                 min_speech_ms=180.0,     # 连续超门槛多久才算开口：咳嗽、键盘、鼠标点击挡在这一关
                 dispatch_silence_ms=250.0,  # 静音多久就把手上这段送去转写（短门限，交得早）
                 silence_ms=500.0,        # 说完之后静音多久算一句结束（长门限，发车判据）
                 max_utterance_ms=15000.0,  # 一句最长多久强切：她不能等一个人讲完五分钟才听见第一个字
                 pre_roll_ms=320.0,       # 触发点往前多带一段：人开口的第一个字总先于能量越线
                 min_utterance_ms=350.0):  # 短于这个的片段直接丢：门限抖动出来的碎片不值得送识别
        self.threshold_db = float(threshold_db)
        self.min_speech_ms = float(min_speech_ms)
        self.dispatch_silence_ms = float(dispatch_silence_ms)
        self.silence_ms = float(silence_ms)
        self.max_utterance_ms = float(max_utterance_ms)
        self.pre_roll_ms = float(pre_roll_ms)
        self.min_utterance_ms = float(min_utterance_ms)


def rms_db(frame):
    """一帧的均方根电平(dBFS)；全零帧记 -100 而不是 -Infinity。
    frame: 16bit 单声道，numpy 数组或 bytes。"""
    if _np is not None:
        a = _np.frombuffer(frame, dtype=_np.int16) if isinstance(frame, (bytes, bytearray)) else frame
        n = a.size
        if n == 0:
            return -100.0
        v = a.astype(_np.float32) / 32768.0
        rms = float(_np.sqrt((v * v).mean()))
    else:
        if isinstance(frame, (bytes, bytearray)):
            import struct
            vals = struct.unpack("<%dh" % (len(frame) // 2), frame)
        else:
            vals = frame
        n = len(vals)
        if n == 0:
            return -100.0
        s = 0.0
        for x in vals:
            v = x / 32768.0
            s += v * v
        rms = (s / n) ** 0.5
    return rms <= 1e-5 and -100.0 or max(-100.0, 20.0 * _log10(rms))


def _log10(x):
    import math
    return math.log10(x)


class Segmenter:
    def __init__(self, cfg=None):
        self.cfg = cfg or SegmentConfig()
        self._pre_roll = []      # 门限之前的那几帧，长度按 preRollMs 截
        self._collected = []
        self._speaking = False
        self._above_ms = 0.0
        self._below_ms = 0.0
        self._elapsed_ms = 0.0
        self._start_ms = 0.0
        self.last_db = -100.0

    def configure(self, cfg):
        self.cfg = cfg

    @property
    def level(self):
        """最近一帧的电平(dBFS)"""
        return self.last_db

    @property
    def active(self):
        """此刻是否正在收一句"""
        return self._speaking

    @property
    def settle_remaining_ms(self):
        """距"这一段真的说完了"还差多少毫秒；0 = 已过收尾静音，或无话可等。

        短门限把音频交出去之后，人还有 silenceMs - dispatchSilenceMs 的窗口可以接着说。
        打包层拿这个数决定继续持有还是发车——转写提前，发车不提前。
        起说判定中（能量已越线、还不够 minSpeechMs）同样算"没完"：那 180ms 里既不
        active 也没在攒静音，不报出来就会在这个缝里把前半句先送走。
        """
        if self._speaking:
            return 0.0
        if self._above_ms > 0:
            return max(0.0, self.cfg.min_speech_ms - self._above_ms)
        if self._below_ms == 0:
            return 0.0
        return max(0.0, self.cfg.silence_ms - self._below_ms)

    def reset(self):
        """清空手上的一切（她自己在说话时调，防自家声音占着收音状态）"""
        self._pre_roll = []
        self._collected = []
        self._speaking = False
        self._above_ms = 0.0
        self._below_ms = 0.0
        self._start_ms = 0.0

    def push(self, frame):
        """喂一帧（16bit 单声道 PCM），拿回这一帧结束时切出来的句子列表（通常为空）。
        每句 = dict(pcm=bytes, start_ms, duration_ms, forced)"""
        ms = len(frame) / 32.0  # 16k 采样 × 2 字节 → 每 32 字节 1ms
        self.last_db = db = rms_db(frame)
        self._elapsed_ms += ms
        loud = db >= self.cfg.threshold_db
        out = []

        if not self._speaking:
            self._pre_roll.append(frame)
            keep = max(1, int(-(-self.cfg.pre_roll_ms // (ms or 1))))  # ceil
            while len(self._pre_roll) > keep:
                self._pre_roll.pop(0)
            self._above_ms = self._above_ms + ms if loud else 0.0
            # 交出去之后这条静音接着数：长门限没到之前，打包层还得替这一段留着位子。
            # 封顶在长门限上，免得安静一整天把这个数攒成天文数字。
            self._below_ms = 0.0 if loud else min(self._below_ms + ms, self.cfg.silence_ms)
            if self._above_ms >= self.cfg.min_speech_ms:
                self._speaking = True
                self._below_ms = 0.0
                self._collected = list(self._pre_roll)
                self._start_ms = self._elapsed_ms - self._frame_ms(self._collected)
                self._pre_roll = []
            return out

        self._collected.append(frame)
        self._below_ms = 0.0 if loud else self._below_ms + ms
        held_ms = self._frame_ms(self._collected)
        # 短门限交货；长门限那一半留给 settle_remaining_ms，不在这里挡。
        # 给反了（短的比长的还长）就退回单级，以长的为准。
        dispatch_ms = min(self.cfg.dispatch_silence_ms, self.cfg.silence_ms)
        if self._below_ms >= dispatch_ms:
            # belowMs 不清零：这条静音还要接着数到 silenceMs
            done = self._finish(False)
            if done:
                out.append(done)
        elif held_ms >= self.cfg.max_utterance_ms:
            # 强切之后照旧在"说话中"：人还没停，下一段接着收
            done = self._finish(True)
            if done:
                out.append(done)
            self._speaking = True
            self._collected = []
            self._start_ms = self._elapsed_ms
            self._below_ms = 0.0
        return out

    def flush(self):
        """停止采集时把手上这半句交出来（够长的话）"""
        tail = self._finish(False) if self._speaking else None
        # 不收音了就没有"还没说完"这回事：留着计数会让打包层永远等一个不再来的帧
        self._above_ms = 0.0
        self._below_ms = 0.0
        return tail

    def _frame_ms(self, frames):
        return sum(len(f) for f in frames) / 32.0

    def _finish(self, forced):
        frames = self._collected
        self._collected = []
        self._speaking = False
        self._above_ms = 0.0
        duration_ms = self._frame_ms(frames)
        if duration_ms < self.cfg.min_utterance_ms:
            return None
        pcm = frames[0] if len(frames) == 1 else b"".join(
            f if isinstance(f, (bytes, bytearray)) else f.tobytes() for f in frames)
        return {"pcm": bytes(pcm), "start_ms": self._start_ms,
                "duration_ms": duration_ms, "forced": forced}


# ---------------------------------------------------------------------------
# Packer：识别结果攒批。add 收句子，due 到点交货——时钟由调用方给。
# ---------------------------------------------------------------------------

class PackConfig:
    def __init__(self,
                 join_gap_ms=0.0,   # 转写落地且收尾静音结束后的额外等待；正值直接增加响应延迟
                 max_hold_ms=8000.0,  # 一条最多攒多久：再连着说也要发车
                 min_chars=2):      # 少于这么多字的识别结果丢：噪声与语气词识别出来常是一两个字
        self.join_gap_ms = float(join_gap_ms)
        self.max_hold_ms = float(max_hold_ms)
        self.min_chars = int(min_chars)


# 写法不带空格的（中日文）：并句时两侧任一是汉字/假名就不加空格
_UNSPACED = re.compile(u"[\u4e00-\u9fff\u3040-\u30ff]")


def join_speech(pieces):
    """连续短句并句：拉丁字母/数字边缘加空格，中日文边缘直接接。"""
    out = ""
    for p in pieces:
        if not p:
            continue
        spaced = (out and _UNSPACED.search(out[-1]) is None
                  and _UNSPACED.search(p[0]) is None)
        out += (u" " + p) if spaced else p
    return out


# vosk 在纯噪声上会幻觉出训练语料尾噪（字幕组内容），按内容挡不住真话，
# 但这些模式是真音频里几乎不会整句出现的——移植自 result.ts HALLUCINATION_PATTERNS
_HALLUCINATION = [
    re.compile(u"^[\\s。.,、!?!?…~\\-]*$"),                 # 纯标点
    re.compile(u"字幕|谢谢观看|请不吝点赞|订阅|转发|打赏|明镜与点点栏目"),
    re.compile(r"^(thank you|thanks for watching|subtitles by|you)[\s.!]*$", re.I),
    re.compile(r"^\s*(sous-titr|untertitel|subt[ií]tulos|legendas|sottotitoli|субтитр)", re.I),
    re.compile(u"^\\s*[\\[(（【*♪]"),                      # 以括号/星号/音符开头（声音标注）
]


def looks_hallucinated(text):
    t = (text or "").strip()
    if not t:
        return True
    return any(re.search(p, t) for p in _HALLUCINATION)


class Packer:
    def __init__(self, cfg=None):
        self.cfg = cfg or PackConfig()
        self._pieces = []
        self._first_at = 0.0
        self._last_at = 0.0

    @property
    def pending(self):
        return len(self._pieces) > 0

    def add(self, text, at_ms):
        """太短的直接丢，返回 False 表示这句没被收下"""
        t = (text or "").strip()
        if len(t) < self.cfg.min_chars:
            return False
        if not self._pieces:
            self._first_at = at_ms
        self._pieces.append(t)
        self._last_at = at_ms
        return True

    def due(self, now_ms, hold=False):
        """到点就把攒着的并成一条交出来；上游仍在收音或转写时继续持有。"""
        if not self._pieces:
            return None
        if hold:
            return None
        quiet = now_ms - self._last_at >= self.cfg.join_gap_ms
        held = now_ms - self._first_at >= self.cfg.max_hold_ms
        if not quiet and not held:
            return None
        return self.take()

    def deadline(self):
        if not self._pieces:
            return None
        return min(self._last_at + self.cfg.join_gap_ms,
                   self._first_at + self.cfg.max_hold_ms)

    def take(self):
        """不管到没到点，把攒着的交出来（停止采集、要求立刻投递时用）"""
        if not self._pieces:
            return None
        text = join_speech(self._pieces)
        self._pieces = []
        return text


def now_ms():
    return time.monotonic() * 1000.0
